"""mihomo 安装检测与内置 metacubexd 面板释放。

mihomo 由用户自行安装（deb 包 / gz 包均可），面板不负责安装与升级：
1. 检测 mihomo 二进制位置、版本与数据目录（systemd 单元 ExecStart 的 -d 参数）；
2. 将内置 metacubexd 面板释放到 mihomo 数据目录下的 metacubexd/。
"""
import gzip
import logging
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from core.paths import BUNDLE_DIR, DATA_DIR

logger = logging.getLogger("clashaux")

UNIT = "mihomo"

# 内置 metacubexd 面板（打包进可执行文件，检测到 mihomo 后释放到其数据目录）
EMBEDDED_DASH_TGZ = BUNDLE_DIR / "assets" / "metacubexd.tgz"

# 内置开机自启安装脚本（面板自身的 systemd 服务，与 mihomo 安装无关）
EMBEDDED_SERVICE_SH = BUNDLE_DIR / "install-service.sh"

# 内置 geo 数据（geoip.dat / geosite.dat / country.mmdb，打包进可执行文件，检测到 mihomo 后释放到其数据目录）
EMBEDDED_GEO = (
    BUNDLE_DIR / "assets" / "geoip.dat",
    BUNDLE_DIR / "assets" / "geosite.dat",
    BUNDLE_DIR / "assets" / "country.mmdb",
)


def _run(args: list[str], timeout: float = 15) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def _systemd_argv() -> list[str] | None:
    """从 systemctl show 解析 mihomo 服务的 ExecStart argv（未安装服务返回 None）。"""
    _, out = _run(["systemctl", "show", UNIT, "-p", "ExecStart", "--no-pager"])
    m = re.search(r"argv\[\]=(.*?)\s*;", out)
    if not m:
        return None
    try:
        return shlex.split(m.group(1))
    except ValueError:
        return None


def _home_from_argv(argv: list[str] | None) -> Path | None:
    if not argv:
        return None
    for i, a in enumerate(argv):
        if a == "-d" and i + 1 < len(argv):
            return Path(argv[i + 1])
        if a.startswith("-d") and len(a) > 2:
            return Path(a[2:])
    return None


def detect_bin() -> str:
    """定位 mihomo 二进制：优先 systemd 单元，其次 PATH。"""
    argv = _systemd_argv()
    if argv:
        cand = Path(argv[0])
        if cand.exists():
            return str(cand)
    found = shutil.which("mihomo")
    return found or ""


def home() -> Path:
    """mihomo 数据目录（-d 目录）：systemd 单元 → /etc/mihomo → 二进制所在目录。"""
    h = _home_from_argv(_systemd_argv())
    if h and h.exists():
        return h
    etc = Path("/etc/mihomo")
    if etc.exists():
        return etc
    bin_path = detect_bin()
    return Path(bin_path).parent if bin_path else etc


def _version(bin_path: str) -> str:
    """从 mihomo -v 解析版本号，如 1.19.32。"""
    if not bin_path:
        return ""
    _, out = _run([bin_path, "-v"], timeout=10)
    m = re.search(r"[vV](\d+(?:\.\d+)+)", out)
    return m.group(1) if m else ""


def detect() -> dict:
    """检测当前 mihomo 安装状态。"""
    bin_path = detect_bin()
    ver = _version(bin_path)
    installed = bool(bin_path) and bool(ver)
    return {
        "installed": installed,
        "version": ver,
        "bin": bin_path,
        "home": str(home()),
        "level": goamd64_level(),
    }


def goamd64_level() -> str:
    """根据 CPU 指令集判断可用的最高 GOAMD64 档位（供用户自行下载安装包时参考）。"""
    try:
        flags = set(
            re.findall(
                r"\b(avx2|avx|bmi2|sse4_2|popcnt)\b",
                Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore"),
            )
        )
    except OSError:
        return "v1"
    if {"avx2", "bmi2"} <= flags:
        return "v3"
    if {"sse4_2", "popcnt"} <= flags:
        return "v2"
    return "v1"


def dashboard_dir() -> Path:
    """metacubexd 面板释放目录（mihomo 数据目录下的 metacubexd/）。"""
    return home() / "metacubexd"


def ensure_dashboard(force: bool = False) -> dict:
    """释放内置面板到 mihomo 数据目录（已存在且不强制时跳过）。"""
    dest = dashboard_dir()
    if (dest / "index.html").exists() and not force:
        return {"ok": True, "extracted": False, "dir": str(dest), "message": "面板已就绪"}
    if not EMBEDDED_DASH_TGZ.exists():
        return {"ok": False, "error": "内置面板包缺失（metacubexd.tgz 未打包进程序）"}
    try:
        if force and dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(EMBEDDED_DASH_TGZ, "r:gz") as tf:
            tf.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as e:
        return {"ok": False, "error": f"释放面板失败: {e}"}
    return {"ok": True, "extracted": True, "dir": str(dest),
            "message": f"已释放到 {dest}"}


def redeploy_dashboard() -> dict:
    """强制重新释放内置面板（覆盖现有目录）。"""
    r = ensure_dashboard(force=True)
    return {"ok": r["ok"], "message": r.get("message") or r.get("error", ""),
            "dir": r.get("dir", "")}


def ensure_geo_files(force: bool = False) -> dict:
    """释放内置 geoip.dat / geosite.dat / country.mmdb 到 mihomo 数据目录（mihomo 从 -d 目录读取）。

    已存在且大小与内置文件一致时跳过；打包件更新后大小变化会自动覆盖。
    返回 released 表示本次实际写入的文件名列表。
    """
    missing = [p.name for p in EMBEDDED_GEO if not p.exists()]
    if missing:
        return {"ok": False, "error": f"内置 geo 数据缺失（{', '.join(missing)} 未打包进程序）"}
    dest_dir = home()
    released: list[str] = []
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in EMBEDDED_GEO:
            dest = dest_dir / src.name
            if not force and dest.exists() and dest.stat().st_size == src.stat().st_size:
                continue
            shutil.copy2(src, dest)
            released.append(src.name)
    except OSError as e:
        return {"ok": False, "error": f"释放 geo 数据失败: {e}"}
    return {"ok": True, "released": released, "dir": str(dest_dir)}


def ensure_service_script() -> dict:
    """释放内置 install-service.sh 到面板程序目录（每次启动覆盖，保持与程序同步）。"""
    dest = DATA_DIR / "install-service.sh"
    if not EMBEDDED_SERVICE_SH.exists():
        return {"ok": False, "error": "内置脚本缺失（install-service.sh 未打包进程序）"}
    if dest.resolve() == EMBEDDED_SERVICE_SH.resolve():
        return {"ok": True, "path": str(dest)}  # 开发模式：同目录，无需复制
    try:
        shutil.copy2(EMBEDDED_SERVICE_SH, dest)
        dest.chmod(0o755)
    except OSError as e:
        return {"ok": False, "error": f"释放 install-service.sh 失败: {e}"}
    return {"ok": True, "path": str(dest)}


# ---------- 上传安装（.deb / .gz，阻断弹窗内使用） ----------
# .gz 安装位置与 .deb 布局一致（以 deb 为准）：二进制 /usr/bin、单元 /usr/lib/systemd/system、数据目录 /etc/mihomo
GZ_BIN_TARGET = Path("/usr/bin/mihomo")
GZ_ETC_DIR = Path("/etc/mihomo")
GZ_UNIT_FILE = Path("/usr/lib/systemd/system/mihomo.service")

_GZ_UNIT = """\
[Unit]
Description=mihomo Daemon, another clash kernel.
After=network.target NetworkManager.service systemd-networkd.service

[Service]
Type=simple
LimitNPROC=500
LimitNOFILE=1000000
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE CAP_SYS_TIME CAP_SYS_PTRACE CAP_DAC_READ_SEARCH
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE CAP_SYS_TIME CAP_SYS_PTRACE CAP_DAC_READ_SEARCH
Restart=always
ExecStartPre=/usr/bin/sleep 1s
ExecStart={bin} -d {etc}
ExecReload=/bin/kill -HUP $MAINPID

[Install]
WantedBy=multi-user.target
"""


def install_deb(deb: Path) -> dict:
    """dpkg 安装 .deb 包；缺依赖时 apt-get -f 修复后重试一次。"""
    rc, out = _run(["dpkg", "-i", str(deb)], timeout=300)
    if rc != 0:
        _run(["apt-get", "install", "-y", "-f"], timeout=600)
        rc, out = _run(["dpkg", "-i", str(deb)], timeout=300)
    if rc != 0:
        tail = out.strip()[-600:]
        return {"ok": False, "error": "dpkg 安装失败" + (f": {tail}" if tail else "")}
    d = detect()
    if not d["installed"]:
        return {"ok": False,
                "error": "dpkg 执行成功，但未检测到 mihomo，请确认上传的是 mihomo 的 deb 包"}
    return {"ok": True, "message": f"mihomo v{d['version']} 安装成功",
            "version": d["version"]}


def install_gz(gz: Path) -> dict:
    """解压 .gz 安装二进制到 /usr/local/bin/mihomo；无 systemd 单元时自动补一个。"""
    try:
        raw = gzip.decompress(gz.read_bytes())
    except (OSError, EOFError) as e:
        return {"ok": False, "error": f"解压失败（请上传 mihomo-linux-amd64-*.gz）: {e}"}
    if not raw.startswith(b"\x7fELF"):
        return {"ok": False,
                "error": "解压后不是有效的 ELF 可执行文件，请上传 mihomo-linux-amd64-*.gz"}
    tmp = None
    try:
        GZ_BIN_TARGET.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=GZ_BIN_TARGET.parent,
                                         prefix=".mihomo-", delete=False) as tf:
            tf.write(raw)
            tmp = Path(tf.name)
        tmp.chmod(0o755)
        ver = _version(str(tmp))
        if not ver:
            return {"ok": False, "error": "二进制无法运行（CPU 架构不匹配或文件损坏）"}
        os.replace(tmp, GZ_BIN_TARGET)
    except OSError as e:
        return {"ok": False, "error": f"安装二进制失败: {e}"}
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    # deb 安装自带服务单元，通常不会走到这里
    rc, _ = _run(["systemctl", "cat", "mihomo.service", "--no-pager"])
    if rc != 0 and not GZ_UNIT_FILE.exists():
        try:
            GZ_ETC_DIR.mkdir(parents=True, exist_ok=True)
            GZ_UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            GZ_UNIT_FILE.write_text(
                _GZ_UNIT.format(bin=GZ_BIN_TARGET, etc=GZ_ETC_DIR))
            _run(["systemctl", "daemon-reload"])
            _run(["systemctl", "enable", "mihomo.service"])
        except OSError as e:
            return {"ok": False, "error": f"写入 systemd 单元失败: {e}"}
    return {"ok": True, "message": f"mihomo v{ver} 安装成功（{GZ_BIN_TARGET}）",
            "version": ver}


def install_from_upload(path: Path) -> dict:
    """按后缀分发安装上传的 .deb / .gz 包。"""
    suffix = path.suffix.lower()
    if suffix == ".deb":
        return install_deb(path)
    if suffix == ".gz":
        return install_gz(path)
    return {"ok": False, "error": "仅支持 .deb 或 .gz 安装包"}
