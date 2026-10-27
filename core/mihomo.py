"""mihomo 安装检测与内置 metacubexd 面板释放。

mihomo 由用户自行安装（deb 包 / gz 包均可），面板不负责安装与升级：
1. 检测 mihomo 二进制位置、版本与数据目录（systemd 单元 ExecStart 的 -d 参数）；
2. 将内置 metacubexd 面板释放到 mihomo 数据目录下的 metacubexd/。
"""
import gzip
import json
import logging
import os
import platform
import re
import shlex
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

from core.paths import BUNDLE_DIR, DATA_DIR, MIHOMO_UNIT as UNIT
from core.utils import atomic_write, run_cmd

logger = logging.getLogger("clashaux")

# 内置 metacubexd 面板（打包进可执行文件，检测到 mihomo 后释放到其数据目录）
EMBEDDED_DASH_TGZ = BUNDLE_DIR / "assets" / "metacubexd.tgz"

# 内置开机自启安装脚本（面板自身的 systemd 服务，与 mihomo 安装无关）
EMBEDDED_SERVICE_SH = BUNDLE_DIR / "install-service.sh"

# geo 数据文件名（geoip.dat / geosite.dat / country.mmdb，检测到 mihomo 后释放到其数据目录）
GEO_NAMES = ("geoip.dat", "geosite.dat", "country.mmdb")


def geo_sources() -> tuple[Path, ...]:
    """geo 数据源解析：优先可执行文件同目录 assets/（外部覆盖，免重新打包更新），单个文件缺失时回退内置。"""
    return tuple(
        (DATA_DIR / "assets" / name)
        if (DATA_DIR / "assets" / name).is_file()
        else (BUNDLE_DIR / "assets" / name)
        for name in GEO_NAMES
    )


def _systemd_argv() -> list[str] | None:
    """从 systemctl show 解析 mihomo 服务的 ExecStart argv（未安装服务返回 None）。"""
    _, out = run_cmd(["systemctl", "show", UNIT, "-p", "ExecStart", "--no-pager"], silent=True)
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
    _, out = run_cmd([bin_path, "-v"], timeout=10, silent=True)
    m = re.search(r"[vV](\d+(?:\.\d+)+)", out)
    return m.group(1) if m else ""


_detect_cache: tuple[float, dict] | None = None
_DETECT_TTL = 10.0


def detect() -> dict:
    """检测当前 mihomo 安装状态（结果缓存 10 秒）。"""
    global _detect_cache
    now = time.monotonic()
    if _detect_cache and now - _detect_cache[0] < _DETECT_TTL:
        return _detect_cache[1]
    bin_path = detect_bin()
    ver = _version(bin_path)
    installed = bool(bin_path) and bool(ver)
    result = {
        "installed": installed,
        "version": ver,
        "bin": bin_path,
        "home": str(home()),
        "level": goamd64_level(),
    }
    _detect_cache = (now, result)
    return result


def invalidate_detect_cache() -> None:
    """安装/卸载 mihomo 后调用，避免 detect() 返回过期结果。"""
    global _detect_cache
    _detect_cache = None


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
    """释放 geoip.dat / geosite.dat / country.mmdb 到 mihomo 数据目录（mihomo 从 -d 目录读取）。

    来源优先级：可执行文件同目录 assets/（外部覆盖）> 打包内置；已存在且大小一致时跳过，大小变化自动覆盖。
    返回 released 表示本次实际写入的文件名列表。
    """
    sources = geo_sources()
    missing = [p.name for p in sources if not p.exists()]
    if missing:
        return {"ok": False, "error": f"geo 数据缺失（{', '.join(missing)} 既无外部覆盖文件也未打包进程序）"}
    dest_dir = home()
    released: list[str] = []
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in sources:
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

# ---------- 安装包元数据（记录最近一次安装的 架构/GOAMD64 档位，供升级校验用） ----------
INSTALL_META_FILE = DATA_DIR / "mihomo-install.json"

_ARCH_MAP = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def normalize_arch(machine: str) -> str:
    """归一化架构名：x86_64→amd64、aarch64→arm64。"""
    m = (machine or "").lower()
    return _ARCH_MAP.get(m, m)


def host_arch() -> str:
    """当前机器架构（归一化为 amd64 / arm64 命名）。"""
    return normalize_arch(platform.machine())


def parse_asset_name(name: str) -> dict:
    """从 mihomo 发布包文件名解析 架构 / GOAMD64 档位 / 版本号。

    形如 mihomo-linux-amd64-v3-v1.19.32.deb、mihomo-linux-arm64-v1.19.32.gz；
    compatible 视为 v1；arm64 无档位概念（level 为空串）。解析不出的字段返回空串。
    """
    stem = Path(name).name
    for ext in (".deb", ".gz", ".tgz", ".zip"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    result = {"arch": "", "level": "", "version": ""}
    m = re.search(r"(?:^|-)(v\d+(?:\.\d+)+)$", stem)
    if m:
        result["version"] = m.group(1).lstrip("v")
        stem = stem[: m.start(1)].rstrip("-")
    m = re.search(r"-(amd64|x86_64|arm64|aarch64)(?:-|$)", stem)
    if m:
        result["arch"] = normalize_arch(m.group(1))
        m2 = re.match(r"(v[123]|compatible)(?:-|$)", stem[m.end():])
        if m2:
            result["level"] = "v1" if m2.group(1) == "compatible" else m2.group(1)
    return result


def install_meta() -> dict:
    """最近一次通过面板安装/升级记录的包信息（arch/goamd64/version/method）。"""
    try:
        meta = json.loads(INSTALL_META_FILE.read_text(encoding="utf-8"))
        return meta if isinstance(meta, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def record_install_meta(arch: str = "", level: str = "",
                        version: str = "", method: str = "") -> None:
    """安装/升级成功后记录包信息（记录失败不影响安装结果）。"""
    meta = install_meta()
    if arch:
        meta["arch"] = arch
    if level:
        meta["goamd64"] = level
    if version:
        meta["version"] = version
    if method:
        meta["method"] = method
    try:
        atomic_write(INSTALL_META_FILE,
                     json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    except OSError as e:
        logger.warning("记录 mihomo 安装信息失败: %s", e)


def probe_deb_version(deb: Path) -> str:
    """从 .deb 包读取版本号（dpkg-deb -f），失败返回空串。"""
    rc, out = run_cmd(["dpkg-deb", "-f", str(deb), "Version"], timeout=15, silent=True)
    m = re.search(r"\d+(?:\.\d+)+", out) if rc == 0 else None
    return m.group(0) if m else ""


def probe_gz_version(gz: Path) -> str:
    """解压 .gz 到临时文件运行 -v 探测版本号，失败返回空串。"""
    try:
        raw = gzip.decompress(gz.read_bytes())
    except (OSError, EOFError):
        return ""
    if not raw.startswith(b"\x7fELF"):
        return ""
    fd, tmp = tempfile.mkstemp(prefix=".mihomo-probe-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        os.chmod(tmp, 0o755)
        return _version(tmp)
    finally:
        os.unlink(tmp)


def probe_package_version(path: Path, orig_name: str = "") -> str:
    """探测上传包的版本号：.deb 用 dpkg-deb，.gz 解压运行 -v；失败回退文件名解析。"""
    suffix = path.suffix.lower()
    if suffix == ".deb":
        return probe_deb_version(path) or parse_asset_name(orig_name)["version"]
    if suffix == ".gz":
        return probe_gz_version(path) or parse_asset_name(orig_name)["version"]
    return ""


def version_newer(new: str, cur: str) -> bool:
    """语义化版本比较：new 是否严格高于 cur（逐段数值比较）。"""
    def nums(v: str) -> tuple[int, ...]:
        found = re.findall(r"\d+", v)
        return tuple(int(x) for x in found) if found else (0,)
    return nums(new) > nums(cur)


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


def install_deb(deb: Path, orig_name: str = "") -> dict:
    """dpkg 安装 .deb 包；缺依赖时 apt-get -f 修复后重试一次。"""
    rc, out = run_cmd(["dpkg", "-i", str(deb)], timeout=300, silent=True)
    if rc != 0:
        run_cmd(["apt-get", "install", "-y", "-f"], timeout=600, silent=True)
        rc, out = run_cmd(["dpkg", "-i", str(deb)], timeout=300, silent=True)
    if rc != 0:
        tail = out.strip()[-600:]
        return {"ok": False, "error": "dpkg 安装失败" + (f": {tail}" if tail else "")}
    invalidate_detect_cache()  # 清缓存，确保拿到安装后的真实状态
    d = detect()
    if not d["installed"]:
        return {"ok": False,
                "error": "dpkg 执行成功，但未检测到 mihomo，请确认上传的是 mihomo 的 deb 包"}
    info = parse_asset_name(orig_name)
    record_install_meta(arch=info["arch"], level=info["level"],
                        version=d["version"], method="deb")
    return {"ok": True, "message": f"mihomo v{d['version']} 安装成功",
            "version": d["version"]}


def install_gz(gz: Path, orig_name: str = "") -> dict:
    """解压 .gz 安装二进制到 /usr/bin/mihomo；无 systemd 单元时自动补一个。"""
    # 已用 deb 安装时拒绝 .gz，避免覆盖 dpkg 管理的文件
    rc, _ = run_cmd(["dpkg", "-s", "mihomo"], silent=True)
    if rc == 0:
        return {"ok": False,
                "error": "检测到 mihomo 已通过 deb 包安装，请改用 .deb 上传安装（或先 sudo apt remove mihomo）"}
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
    rc, _ = run_cmd(["systemctl", "cat", "mihomo.service", "--no-pager"], silent=True)
    if rc != 0 and not GZ_UNIT_FILE.exists():
        try:
            GZ_ETC_DIR.mkdir(parents=True, exist_ok=True)
            GZ_UNIT_FILE.parent.mkdir(parents=True, exist_ok=True)
            GZ_UNIT_FILE.write_text(
                _GZ_UNIT.format(bin=GZ_BIN_TARGET, etc=GZ_ETC_DIR))
            run_cmd(["systemctl", "daemon-reload"], silent=True)
            run_cmd(["systemctl", "enable", "mihomo.service"], silent=True)
        except OSError as e:
            return {"ok": False, "error": f"写入 systemd 单元失败: {e}"}
    invalidate_detect_cache()  # 清缓存，确保后续 detect() 拿到安装后的真实状态
    info = parse_asset_name(orig_name)
    record_install_meta(arch=info["arch"], level=info["level"],
                        version=ver, method="gz")
    return {"ok": True, "message": f"mihomo v{ver} 安装成功（{GZ_BIN_TARGET}）",
            "version": ver}


def install_from_upload(path: Path, orig_name: str = "") -> dict:
    """按后缀分发安装上传的 .deb / .gz 包（orig_name 用于解析架构/档位）。"""
    suffix = path.suffix.lower()
    if suffix == ".deb":
        return install_deb(path, orig_name)
    if suffix == ".gz":
        return install_gz(path, orig_name)
    return {"ok": False, "error": "仅支持 .deb 或 .gz 安装包"}
