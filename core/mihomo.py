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
    adv = package_advice(installed)
    result = {
        "installed": installed,
        "version": ver,
        "bin": bin_path,
        "home": str(home()),
        "level": adv["level"],
        "advice": adv,
    }
    _detect_cache = (now, result)
    return result


def invalidate_detect_cache() -> None:
    """安装/卸载 mihomo 后调用，避免 detect() 返回过期结果。"""
    global _detect_cache
    _detect_cache = None


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


# ---------- 面板升级（仅官方 compressed-dist.tgz，版本取自包内 appVersion） ----------
PANEL_PKG_NAME = "compressed-dist.tgz"
_PANEL_APP_VER_RE = re.compile(r'appVersion\s*:\s*"([0-9]+(?:\.[0-9]+)*)"')


def _html_app_version(text: str) -> str:
    """从面板 index.html 的 __NUXT__.config.public 中读取 appVersion。"""
    m = _PANEL_APP_VER_RE.search(text)
    return m.group(1) if m else ""


def _index_app_version(path: Path) -> str:
    try:
        return _html_app_version(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return ""


def _tgz_app_version(tgz: Path) -> str:
    """读取面板包内 index.html 记录的 appVersion（读不出返回空串）。"""
    try:
        with tarfile.open(tgz, "r:gz") as tf:
            for m in tf.getmembers():
                if not m.isfile() or m.name.rsplit("/", 1)[-1] != "index.html":
                    continue
                f = tf.extractfile(m)
                if f:
                    return _html_app_version(f.read(300_000).decode("utf-8", "ignore"))
    except (tarfile.TarError, OSError, EOFError):
        return ""
    return ""


def dashboard_version() -> str:
    """当前已释放面板的版本（未释放或读不出返回空串）。"""
    return _index_app_version(dashboard_dir() / "index.html")


def dashboard_info() -> dict:
    """面板版本与本机适配建议（升级面板弹窗用）。

    面板是纯静态前端，与 CPU 架构、GOAMD64 档位无关；未释放面板时以
    内置包的版本作为当前版本。
    """
    dest = dashboard_dir()
    embedded = _tgz_app_version(EMBEDDED_DASH_TGZ) if EMBEDDED_DASH_TGZ.exists() else ""
    installed_ver = dashboard_version()
    return {
        "ok": True,
        "version": installed_ver or embedded,
        "deployed": (dest / "index.html").is_file(),
        "dir": str(dest),
        "embedded_version": embedded,
        "arch": host_arch(),
        "pattern": PANEL_PKG_NAME,
    }


def install_dashboard_from_upload(path: Path, orig_name: str = "") -> dict:
    """用上传的官方 compressed-dist.tgz 升级网页面板。

    先解压到 mihomo 数据目录下的临时目录校验（必须是含 index.html 的面板资源、
    版本必须高于当前面板版本），通过后再原子替换 metacubexd/，替换失败自动还原。
    """
    name = (orig_name or path.name).lower()
    if not (name.endswith(".tgz") or name.endswith(".tar.gz")):
        return {"ok": False,
                "error": f"仅支持官方 {PANEL_PKG_NAME}（.tgz）；deb / rpm / AppImage / "
                         f"exe / dmg 是桌面客户端安装包，不是网页面板资源"}
    dest = dashboard_dir()
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=dest.parent, prefix=".metacubexd-new-"))
    except OSError as e:
        return {"ok": False, "error": f"无法在 {dest.parent} 创建临时目录: {e}"}
    old: Path | None = None
    try:
        try:
            with tarfile.open(path, "r:gz") as tf:
                tf.extractall(staging, filter="data")
        except (tarfile.TarError, OSError, EOFError) as e:
            return {"ok": False, "error": f"解压失败，请确认上传的是官方 {PANEL_PKG_NAME}: {e}"}
        if not (staging / "index.html").is_file():
            return {"ok": False,
                    "error": f"包内没有 index.html，不是网页面板资源，请下载官方 {PANEL_PKG_NAME}"}
        new_ver = _index_app_version(staging / "index.html")
        if not new_ver:
            return {"ok": False,
                    "error": "包内 index.html 未记录 appVersion，无法判断版本，已拒绝升级"}
        cur_ver = dashboard_version()
        if cur_ver and not version_newer(new_ver, cur_ver):
            return {"ok": False,
                    "error": f"新面板 v{new_ver} 不高于当前面板 v{cur_ver}，无需升级"}
        try:
            if dest.exists():
                old = dest.with_name(f".metacubexd-old-{os.getpid()}")
                if old.exists():
                    shutil.rmtree(old, ignore_errors=True)
                dest.rename(old)
            staging.rename(dest)
        except OSError as e:
            if old is not None and old.exists() and not dest.exists():
                try:
                    old.rename(dest)
                except OSError:
                    pass
            return {"ok": False, "error": f"替换面板目录失败: {e}"}
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
        return {"ok": True, "message": f"面板已升级到 v{new_ver}",
                "version": new_ver, "dir": str(dest)}
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


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

_ARCH_MAP = {
    "x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64",
    "i386": "386", "i486": "386", "i586": "386", "i686": "386", "386": "386",
    "armv5tel": "armv5", "armv5": "armv5", "armv6l": "armv6", "armv6": "armv6",
    "armv7l": "armv7", "armv7": "armv7", "loongarch64": "loong64", "loong64": "loong64",
    "mips64": "mips64", "mips": "mips", "ppc64le": "ppc64le", "riscv64": "riscv64",
    "s390x": "s390x",
}

# 官方发布资源后缀（长的在前，确保 .pkg.tar.zst 不被 .zst 之类截断）
_ASSET_EXTS = (".pkg.tar.zst", ".tar.gz", ".tgz", ".deb", ".rpm", ".zip", ".gz")
# 文件名中的 GOOS 段（mihomo-<os>-<arch>-…），面板只管理 Linux 内核
_ASSET_OS = ("linux", "darwin", "windows", "freebsd", "android", "ios")
# 文件名中的 GOARCH 段（长的在前，避免 mips64le 被 mips 抢先匹配）
_ASSET_ARCH_RE = re.compile(
    r"-(x86_64|amd64|aarch64|arm64|armv5|armv6|armv7|386|loong64"
    r"|mips64le|mips64|mipsle|mips|ppc64le|riscv64|s390x)(?:-|$)"
)


def normalize_arch(machine: str) -> str:
    """归一化架构名：x86_64→amd64、aarch64→arm64、i686→386、loongarch64→loong64 等。"""
    m = (machine or "").lower()
    return _ARCH_MAP.get(m, m)


def host_arch() -> str:
    """当前机器架构（归一化为 amd64 / arm64 命名）。"""
    return normalize_arch(platform.machine())


# GOAMD64 各档位在 v1 基础上额外要求的 x86-64 指令集（与 Go 官方档位定义一致）
_GOAMD64_V2_FLAGS = ("sse4_2", "popcnt", "cx16", "movbe")
_GOAMD64_V3_FLAGS = ("avx", "avx2", "bmi1", "bmi2", "fma", "osxsave")
_LEVELS = ("v1", "v2", "v3")


def cpu_flags() -> set[str]:
    """本机 CPU 指令集标志（/proc/cpuinfo 的 flags/features 行，读不到返回空集）。"""
    try:
        text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    flags: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() in ("flags", "features"):
            flags.update(value.split())
    return flags


def goamd64_level() -> str:
    """本机 CPU 可运行的最高 GOAMD64 档位（v1/v2/v3）；非 amd64 或读不到指令集返回空串。"""
    if host_arch() != "amd64":
        return ""
    flags = cpu_flags()
    if not flags:
        return ""
    if set(_GOAMD64_V2_FLAGS) <= flags and set(_GOAMD64_V3_FLAGS) <= flags:
        return "v3"
    if set(_GOAMD64_V2_FLAGS) <= flags:
        return "v2"
    return "v1"


def level_rank(level: str) -> int:
    """档位高低排序值（未知档位按最低的 v1 处理）。"""
    return _LEVELS.index(level) if level in _LEVELS else 0


# Go 二进制内嵌的编译参数（debug/buildinfo）带有 GOAMD64=vN，比文件名更可信
_GOAMD64_RE = re.compile(rb"GOAMD64=(v\d+)")
_bin_level_cache: dict[str, tuple[str, str]] = {}


def binary_goamd64(data: bytes) -> str:
    """从 Go 二进制内嵌编译参数中读取 GOAMD64 档位（读不出返回空串）。"""
    m = _GOAMD64_RE.search(data)
    return m.group(1).decode() if m else ""


def file_goamd64(path: Path) -> str:
    """读取本机二进制的 GOAMD64，按 修改时间+大小 缓存（避免每次检测重复读几十 MB）。"""
    try:
        st = path.stat()
    except OSError:
        return ""
    key = f"{st.st_mtime_ns}|{st.st_size}"
    cached = _bin_level_cache.get(str(path))
    if cached and cached[0] == key:
        return cached[1]
    try:
        level = binary_goamd64(path.read_bytes())
    except OSError:
        level = ""
    _bin_level_cache[str(path)] = (key, level)
    return level


def installed_goamd64() -> str:
    """当前已安装内核编译时的 GOAMD64 档位（升级必须保持同档位）。"""
    bin_path = detect_bin()
    return file_goamd64(Path(bin_path)) if bin_path else ""


def deb_binary(deb: Path) -> bytes:
    """解出 .deb 内的 mihomo 二进制内容（取解包后的第一个 ELF），失败返回空字节。"""
    with tempfile.TemporaryDirectory(prefix="mihomo-deb-") as td:
        rc, _ = run_cmd(["dpkg-deb", "-x", str(deb), td], timeout=180, silent=True)
        if rc != 0:
            return b""
        for p in sorted(Path(td).rglob("*")):
            if not p.is_file():
                continue
            try:
                with p.open("rb") as f:
                    if f.read(4) != b"\x7fELF":
                        continue
                return p.read_bytes()
            except OSError:
                return b""
        return b""


def package_goamd64(path: Path, orig_name: str = "") -> str:
    """探测上传包内二进制的 GOAMD64：.gz 解压读字节，.deb 解包读二进制；读不出返回空串。"""
    suffix = path.suffix.lower()
    if suffix == ".gz":
        try:
            return binary_goamd64(gzip.decompress(path.read_bytes()))
        except (OSError, EOFError):
            return ""
    if suffix == ".deb":
        return binary_goamd64(deb_binary(path))
    return ""


def package_pattern(arch: str, level: str = "", ext: str = ".deb") -> str:
    """应下载的安装包文件名形式，如 mihomo-linux-amd64-v3-*.deb。

    arm64 等无档位架构不带动位段；amd64 档位无法判定时用 v1/v2/v3 占位。
    """
    hint = level or ("v1/v2/v3" if arch == "amd64" else "")
    return f"mihomo-linux-{arch}" + (f"-{hint}" if hint else "") + "-*" + ext


def package_advice(installed: bool) -> dict:
    """给出本机应选的安装包档位建议（首次安装与升级弹窗共用）。

    未安装时应选档位为本机 CPU 支持的上限；已安装时升级必须保持当前档位
    （档位只能靠卸载重装更换），当前档位以内核二进制内嵌的 GOAMD64 为准，
    读不出时依次回退安装记录、CPU 上限。
    """
    meta = install_meta()
    arch = host_arch()
    cpu_level = goamd64_level() if arch == "amd64" else ""
    cur_level = ""
    if installed and arch == "amd64":
        cur_level = installed_goamd64() or meta.get("goamd64") or cpu_level
    level = cur_level or cpu_level
    return {
        "arch": arch,
        "cpu_level": cpu_level,
        "cur_level": cur_level,
        "level": level,
        "pattern": package_pattern(arch, level),
        "locked": bool(cur_level),  # 已安装且档位已知：升级不得换档
    }


def check_package_fit(path: Path, orig_name: str = "",
                      pkg_level: str | None = None) -> str:
    """校验上传包能否用于本机：平台需为 linux、架构需一致、GOAMD64 档位不得超出本机 CPU 上限。

    档位优先取包内二进制内嵌的编译参数（被改名的包同样有效），读不出时回退文件名；
    返回拒绝原因，空串表示通过，本机 CPU 档位无法判定时不拦截。
    """
    info = parse_asset_name(orig_name)
    arch = host_arch()
    if info["os"] and info["os"] != "linux":
        return (f"平台不符：安装包为 {info['os']}，本面板只管理 Linux 上的 mihomo，"
                f"请下载 {package_pattern(arch, goamd64_level())}")
    if info["arch"] and info["arch"] != arch:
        return (f"架构不符：安装包为 {info['arch']}，本机为 {arch}，"
                f"请下载 {package_pattern(arch, goamd64_level())}")
    cpu_level = goamd64_level()
    if not cpu_level:
        return ""
    if pkg_level is None:
        pkg_level = info["level"] or package_goamd64(path, orig_name)
    if level_rank(pkg_level) > level_rank(cpu_level):
        return (f"GOAMD64 档位超出本机 CPU 支持：安装包为 {pkg_level}，"
                f"本机最高 {cpu_level}，请下载 {package_pattern(arch, cpu_level)}")
    return ""


def parse_asset_name(name: str) -> dict:
    """从 mihomo 发布包文件名解析 平台 / 架构 / GOAMD64 档位 / 版本号。

    覆盖官方全部资源命名，如 mihomo-linux-amd64-v3-v1.19.34.deb、
    mihomo-linux-amd64-v2-go124-v1.19.34.gz（go1xx 为 Go 工具链变体，忽略）、
    mihomo-linux-amd64-compatible-v1.19.34.gz（compatible 视为 v1）、
    mihomo-linux-armv7-v1.19.34.deb、mihomo-linux-loong64-abi1-v1.19.34.rpm、
    mihomo-linux-mipsle-hardfloat-v1.19.34.gz、mihomo-darwin-arm64-v1.19.34.gz 等；
    非 amd64 架构无档位概念（level 为空串），解析不出的字段同样返回空串。
    """
    stem = Path(name).name
    for ext in _ASSET_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    result = {"os": "", "arch": "", "level": "", "version": ""}
    m = re.search(r"(?:^|-)(v\d+(?:\.\d+)+)$", stem)
    if m:
        result["version"] = m.group(1).lstrip("v")
        stem = stem[: m.start(1)].rstrip("-")
    m = re.match(r"^[a-z]+-([a-z]+)-", stem.lower())
    if m and m.group(1) in _ASSET_OS:
        result["os"] = m.group(1)
    m = re.search(_ASSET_ARCH_RE, stem)
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
    record_install_meta(arch=info["arch"] or host_arch(),
                        level=installed_goamd64() or info["level"],
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
    want = package_pattern(host_arch(), goamd64_level(), ".gz")
    try:
        raw = gzip.decompress(gz.read_bytes())
    except (OSError, EOFError) as e:
        return {"ok": False, "error": f"解压失败（请上传 {want}）: {e}"}
    if not raw.startswith(b"\x7fELF"):
        return {"ok": False, "error": f"解压后不是有效的 ELF 可执行文件，请上传 {want}"}
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
    record_install_meta(arch=info["arch"] or host_arch(),
                        level=binary_goamd64(raw) or info["level"],
                        version=ver, method="gz")
    return {"ok": True, "message": f"mihomo v{ver} 安装成功（{GZ_BIN_TARGET}）",
            "version": ver}


def install_from_upload(path: Path, orig_name: str = "",
                        pkg_level: str | None = None) -> dict:
    """按后缀分发安装上传的 .deb / .gz 包（orig_name 用于解析架构/档位）。"""
    err = check_package_fit(path, orig_name, pkg_level)
    if err:
        return {"ok": False, "error": err}
    suffix = path.suffix.lower()
    if suffix == ".deb":
        return install_deb(path, orig_name)
    if suffix == ".gz":
        return install_gz(path, orig_name)
    return {"ok": False, "error": "仅支持 .deb 或 .gz 安装包"}
