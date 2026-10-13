"""mihomo 版本检测、一键更新与卸载（程序目录安装），metacubexd 内置面板释放。

安装布局：二进制 <程序目录>/mihomo，数据目录 -d <程序目录>，
配置/面板/缓存全部落在程序目录内。
"""
import gzip
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from core import net
from core import basic as basicm
from core.paths import BUNDLE_DIR, DATA_DIR, MIHOMO_BIN, MIHOMO_HOME
from core.service import require_root, service_action

logger = logging.getLogger("clashaux")

GITHUB_API = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
MIHOMO_SERVICE = "/etc/systemd/system/mihomo.service"
BAK_DIR = DATA_DIR / "backups"

# 卸载时清理的 mihomo 运行文件（保留 backups/、metacubexd/ 与面板自身数据）
_UNINSTALL_FILES = ("config.yaml", "cache.db")
_UNINSTALL_DIRS = ("ruleset",)

# 内置 metacubexd 面板（打包进可执行文件，启动时释放到可执行文件同目录）
EMBEDDED_DASH_TGZ = BUNDLE_DIR / "metacubexd.tgz"


class UpgradeError(Exception):
    """升级流程错误。"""


def is_installed() -> bool:
    """mihomo 是否已安装。"""
    return Path(MIHOMO_BIN).exists()


def current_version() -> str:
    """从 mihomo -v 解析当前版本号，如 1.19.32。"""
    if not is_installed():
        return ""
    try:
        out = subprocess.run(
            [MIHOMO_BIN, "-v"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"[vV](\d+(?:\.\d+)+)", out)
    return m.group(1) if m else ""


def goamd64_level() -> str:
    """根据 CPU 指令集判断可用的最高 GOAMD64 档位。"""
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


def detected_level() -> str:
    """系统检测的推荐档位（compatible 归一化为 v1）。"""
    return goamd64_level()


def dashboard_dir() -> Path:
    """metacubexd 面板释放目录（可执行文件同目录）。"""
    return DATA_DIR / "metacubexd"


def ensure_dashboard(force: bool = False) -> dict:
    """释放内置面板到可执行文件同目录（已存在且不强制时跳过）。"""
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
    return {"ok": True, "extracted": True, "dir": str(dest), "message": f"已释放到 {dest}"}


def redeploy_dashboard() -> dict:
    """强制重新释放内置面板（覆盖现有目录）。"""
    r = ensure_dashboard(force=True)
    return {"ok": r["ok"], "message": r.get("message") or r.get("error", ""), "dir": r.get("dir", "")}


# 内置开机自启安装脚本（打包进可执行文件，启动时释放到可执行文件同目录）
EMBEDDED_SERVICE_SH = BUNDLE_DIR / "install-service.sh"


def ensure_service_script() -> dict:
    """释放内置 install-service.sh 到可执行文件同目录（每次启动覆盖，保持与程序同步）。"""
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


def _latest_release(use_proxy: bool) -> dict:
    """查询 GitHub 最新 release，返回 {tag, assets: {文件名: 下载地址}}。"""
    try:
        resp = net.http_get(GITHUB_API, timeout=30, use_proxy=use_proxy,
                            headers=dict(net.HEADERS))
        data = resp.json()
    except net.NetError as e:
        raise UpgradeError(f"查询 GitHub 失败: {e}") from e
    tag = str(data.get("tag_name", "")).lstrip("v")
    if not re.fullmatch(r"\d+(?:\.\d+)+", tag):
        raise UpgradeError(f"无法识别的版本号: {data.get('tag_name')}")
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    return {"tag": tag, "assets": assets}


def _pick_asset(tag: str, assets: dict, level: str) -> str:
    """按档位挑选 gz 二进制包名，缺失时抛错。"""
    candidates = {
        "v1": [f"mihomo-linux-amd64-v{tag}.gz",
               f"mihomo-linux-amd64-compatible-v{tag}.gz"],
        "v2": [f"mihomo-linux-amd64-v2-v{tag}.gz"],
        "v3": [f"mihomo-linux-amd64-v3-v{tag}.gz"],
    }[level]
    for name in candidates:
        if name in assets:
            return name
    raise UpgradeError(f"release v{tag} 中没有 {level} 档位的 gz 包")


def _level_assets(tag: str, assets: dict) -> dict:
    """各档位可用的 gz 包名（缺失为 null）。"""
    out = {}
    for lv in ("v1", "v2", "v3"):
        try:
            out[lv] = _pick_asset(tag, assets, lv)
        except UpgradeError:
            out[lv] = None
    return out


def _version_gt(a: str, b: str) -> bool:
    """a 是否大于 b（点分数字比较，位数不足补 0）。"""
    pa = [int(x) for x in a.split(".")]
    pb = [int(x) for x in b.split(".")]
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb


def check(use_proxy: bool = False) -> dict:
    """检测更新：当前版本 vs GitHub 最新版，返回档位信息供用户选择。"""
    cur = current_version()
    if not cur:
        return {"ok": False, "error": "无法获取当前 mihomo 版本"}
    rel = _latest_release(use_proxy)
    auto = detected_level()
    return {
        "ok": True,
        "current": cur,
        "latest": rel["tag"],
        "has_update": _version_gt(rel["tag"], cur),
        "detected": auto,
        "levels": _level_assets(rel["tag"], rel["assets"]),
        "asset_name": _pick_asset(rel["tag"], rel["assets"], auto),
    }


def _download_gz(url: str, use_proxy: bool) -> Path:
    """下载 gz 二进制包到临时文件（线路回退 + 自动重试）。"""
    fd, tmp = tempfile.mkstemp(suffix=".gz", prefix="mihomo-")
    os.close(fd)
    try:
        net.stream_download(url, Path(tmp), use_proxy=use_proxy)
    except net.NetError as e:
        Path(tmp).unlink(missing_ok=True)
        raise UpgradeError(str(e)) from e
    return Path(tmp)


def _backup_binary(version: str) -> Path | None:
    """升级前备份当前二进制，用于兜底。"""
    if not Path(MIHOMO_BIN).exists():
        return None
    BAK_DIR.mkdir(parents=True, exist_ok=True)
    dest = BAK_DIR / f"mihomo-{version}.bin"
    if not dest.exists():  # 已有同名备份不覆盖
        shutil.copy2(MIHOMO_BIN, dest)
    return dest


def _place_binary(src: Path) -> None:
    """把二进制原子替换到程序目录并赋予执行权限。"""
    dest = Path(MIHOMO_BIN)
    fd, tmp = tempfile.mkstemp(prefix="mihomo-", dir=str(dest.parent))
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
            shutil.copyfileobj(f, out)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
    except OSError as e:
        Path(tmp).unlink(missing_ok=True)
        raise UpgradeError(f"写入 {dest} 失败: {e}") from e


def _install_gz(gz: Path) -> None:
    """解压 gz 二进制并安装到程序目录。"""
    with tempfile.TemporaryDirectory(prefix="mihomo-unzip-") as td:
        raw = Path(td) / "mihomo"
        try:
            with gzip.open(gz, "rb") as f, open(raw, "wb") as out:
                shutil.copyfileobj(f, out)
        except (gzip.BadGzipFile, OSError) as e:
            raise UpgradeError(f"解压安装包失败: {e}") from e
        _place_binary(raw)


def _extract_deb(deb: Path) -> Path:
    """从 deb 包提取 mihomo 二进制（dpkg-deb 解包，返回二进制路径）。"""
    if not shutil.which("dpkg-deb"):
        raise UpgradeError("系统缺少 dpkg-deb，无法解包 deb，请改上传 .gz 二进制包")
    with tempfile.TemporaryDirectory(prefix="mihomo-deb-") as td:
        proc = subprocess.run(
            ["dpkg-deb", "-x", str(deb), td], capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            raise UpgradeError("deb 解包失败: " + (proc.stderr or proc.stdout).strip()[-500:])
        hits = sorted(Path(td).rglob("mihomo"))
        if not hits:
            raise UpgradeError("deb 包中未找到 mihomo 二进制")
        # 复制到临时目录外层（TemporaryDirectory 退出后源文件失效）
        out = deb.parent / "mihomo.extracted"
        shutil.copy2(hits[0], out)
        return out


def _level_hint() -> str:
    return "若因 CPU 档位不匹配无法运行，请在上方选择正确档位后一键更新重装（或上传 .deb/.gz 安装包手动安装）"


def apply(use_proxy: bool = False, level: str = "") -> dict:
    """一键更新/重装：下载 → 备份 → 解压安装到程序目录 → 重启 → 验证。

    level 为空 = 按系统推荐档位正常升级（有新版才装）；
    level 显式指定且与推荐不同 = 更换档位重装（覆盖旧版二进制，不限版本）。
    """
    cur = current_version()
    if not cur:
        return {"ok": False, "error": "无法获取当前 mihomo 版本"}
    rel = _latest_release(use_proxy)
    auto = detected_level()
    lvl = level if level in ("v1", "v2", "v3") else auto
    force = bool(level) and level != auto
    if not force and not _version_gt(rel["tag"], cur):
        msg = f"已是最新版本 v{cur}"
        if level and level == auto:
            msg += f"，所选档位 {lvl} 与系统推荐一致，无需重装"
        return {"ok": True, "no_update": True, "message": msg}

    name = _pick_asset(rel["tag"], rel["assets"], lvl)
    gz = _download_gz(rel["assets"][name], use_proxy)
    try:
        _backup_binary(cur)
        _install_gz(gz)
    finally:
        gz.unlink(missing_ok=True)

    new_ver = current_version()
    if new_ver != rel["tag"]:
        return {"ok": False, "error": f"安装后版本异常: 期望 {rel['tag']}，实际 {new_ver or '未知'}"}

    rok, rmsg = service_action("restart")
    if not rok:
        return {"ok": False, "stage": "restart",
                "error": (f"已安装 v{new_ver}（档位 {lvl}）但服务重启失败: {rmsg}。"
                          f"{_level_hint()}（旧版备份 backups/mihomo-{cur}.bin）")}

    verb = "重装" if force else "升级"
    return {"ok": True, "version": new_ver,
            "message": f"已从 v{cur} {verb}到 v{new_ver}（GOAMD64 {lvl}），服务重启成功",
            "backup": f"mihomo-{cur}.bin"}


def install_from_upload(upload) -> dict:
    """网页上传 .gz / .deb 安装包安装 mihomo（GitHub 不可用时的手动通道）。"""
    name = (upload.filename or "").strip()
    lower = name.lower()
    if not (lower.endswith(".gz") or lower.endswith(".deb")):
        return {"ok": False, "error": "请上传 .gz 二进制包或 .deb 安装包"}
    cur = current_version()
    fd, tmp = tempfile.mkstemp(suffix=Path(name).suffix or ".pkg", prefix="upload-")
    os.close(fd)
    try:
        with upload.file as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst)
        _backup_binary(cur)
        if lower.endswith(".deb"):
            extracted = _extract_deb(Path(tmp))
            try:
                _place_binary(extracted)
            finally:
                extracted.unlink(missing_ok=True)
        else:
            _install_gz(Path(tmp))
    finally:
        Path(tmp).unlink(missing_ok=True)

    if not is_installed():
        return {"ok": False, "error": "安装后未找到 mihomo 二进制"}
    new_ver = current_version()
    rok, rmsg = service_action("restart")
    if not rok:
        return {"ok": False, "stage": "restart",
                "error": (f"已安装 v{new_ver} 但服务重启失败: {rmsg}。"
                          f"{_level_hint()}（旧版备份 backups/mihomo-{cur}.bin）")}
    return {"ok": True, "version": new_ver,
            "message": f"已安装 v{new_ver}（{name}），服务重启成功"}


def _create_service() -> None:
    """创建 systemd service 文件。"""
    Path(MIHOMO_SERVICE).write_text(
        MIHOMO_SERVICE_UNIT.format(bin=MIHOMO_BIN, home=MIHOMO_HOME), encoding="utf-8"
    )
    subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=30)


def _init_config() -> None:
    """创建初始配置文件 config.yaml（基础设置头 + base.yaml）。"""
    cfg_dir = Path(MIHOMO_HOME)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    if not cfg_file.exists():
        src = BUNDLE_DIR / "base.yaml"
        base_text = src.read_text(encoding="utf-8") if src.exists() else "mode: rule\n"
        text = basicm.render(basicm.load()) + "\n" + basicm.strip_keys(base_text)
        cfg_file.write_text(text, encoding="utf-8")
    # 确保 ruleset 目录存在
    (cfg_dir / "ruleset").mkdir(exist_ok=True)


MIHOMO_SERVICE_UNIT = """[Unit]
Description=mihomo
After=network.target

[Service]
Type=simple
ExecStart={bin} -d {home}
Restart=on-failure
RestartSec=5
LimitNPROC=500
LimitNOFILE=1000000
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE CAP_SYS_TIME CAP_SYS_PTRACE CAP_DAC_READ_SEARCH CAP_DAC_OVERRIDE
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW CAP_NET_BIND_SERVICE CAP_SYS_TIME CAP_SYS_PTRACE CAP_DAC_READ_SEARCH CAP_DAC_OVERRIDE

[Install]
WantedBy=multi-user.target
"""


def uninstall() -> dict:
    """卸载 mihomo：停止/禁用服务 → 删单元 → 删二进制 → 清理运行文件。

    保留 backups/ 配置备份、metacubexd/ 面板与面板自身数据，卸载后可用一键安装重装。
    """
    if err := require_root():
        return {"ok": False, "error": err}
    # 1. 停止并禁用服务
    subprocess.run(["systemctl", "stop", "mihomo"], capture_output=True, timeout=30)
    subprocess.run(["systemctl", "disable", "mihomo"], capture_output=True, timeout=30)
    # 2. 删除 systemd 单元
    unit = Path(MIHOMO_SERVICE)
    unit_removed = False
    try:
        if unit.exists():
            unit.unlink()
            unit_removed = True
            subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=30)
    except OSError as e:
        return {"ok": False, "error": f"删除 systemd 单元失败（需要 root）: {e}"}
    # 3. 删除二进制
    removed = []
    try:
        if Path(MIHOMO_BIN).exists():
            Path(MIHOMO_BIN).unlink()
            removed.append("mihomo 二进制")
    except OSError as e:
        return {"ok": False, "error": f"删除 mihomo 二进制失败（需要 root）: {e}"}
    # 4. 清理运行文件（config.yaml 若为软链接仅删除链接本身，不碰目标文件）
    for name in _UNINSTALL_FILES:
        p = Path(MIHOMO_HOME) / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
                removed.append(name)
        except OSError as e:
            return {"ok": False, "error": f"删除 {p} 失败: {e}"}
    for name in _UNINSTALL_DIRS:
        shutil.rmtree(Path(MIHOMO_HOME) / name, ignore_errors=True)
    if unit_removed:
        removed.append("systemd 服务单元")
    logger.info("mihomo 已卸载，清理: %s", "、".join(removed) or "无")
    return {"ok": True,
            "message": f"mihomo 已卸载（{'、'.join(removed) or '本就未安装'}）。"
                       "backups/ 备份与面板数据已保留，可随时一键安装重装"}


def install(use_proxy: bool = False) -> dict:
    """一键安装 mihomo：下载 → 解压安装 → 创建 service → 初始化配置 → 启动。"""
    if is_installed():
        return {"ok": False, "error": "mihomo 已安装，无需重复安装"}

    rel = _latest_release(use_proxy)
    level = detected_level()
    name = _pick_asset(rel["tag"], rel["assets"], level)
    gz = _download_gz(rel["assets"][name], use_proxy)
    try:
        _install_gz(gz)
    finally:
        gz.unlink(missing_ok=True)

    if not is_installed():
        return {"ok": False, "error": "安装后未找到 mihomo 二进制"}

    _create_service()
    _init_config()

    # 启用并启动服务
    subprocess.run(["systemctl", "enable", "mihomo"], check=False, timeout=30)
    rc = subprocess.run(
        ["systemctl", "start", "mihomo"], capture_output=True, text=True, timeout=30
    ).returncode

    new_ver = current_version()
    if rc != 0:
        return {"ok": False, "stage": "start",
                "error": f"已安装 v{new_ver} 但服务启动失败。{_level_hint()}"}

    return {"ok": True, "message": f"mihomo v{new_ver}（GOAMD64 {level}）安装成功，服务已启动"}


def install_stream(use_proxy: bool = False):
    """一键安装流程的 SSE 生成器，逐步 yield 进度事件。"""
    if is_installed():
        yield {"step": "error", "message": "mihomo 已安装，无需重复安装"}
        return

    level = detected_level()

    # 1. 查询版本
    yield {"step": "query", "status": "running", "message": "查询 GitHub 最新版本…"}
    try:
        rel = _latest_release(use_proxy)
        name = _pick_asset(rel["tag"], rel["assets"], level)
    except UpgradeError as e:
        yield {"step": "query", "status": "error", "message": str(e),
               "manual": _mihomo_manual_install(level)}
        return
    yield {"step": "query", "status": "done",
           "message": f"最新版本 v{rel['tag']}（GOAMD64 {level}，文件 {name}）"}

    # 2. 下载二进制包
    yield {"step": "download", "status": "running", "message": "下载 mihomo 二进制包（失败自动重试）…"}
    try:
        gz = _download_gz(rel["assets"][name], use_proxy)
    except UpgradeError as e:
        yield {"step": "download", "status": "error", "message": str(e),
               "manual": _mihomo_manual_install(level)}
        return
    yield {"step": "download", "status": "done", "message": "下载完成"}

    # 3. 解压安装到程序目录
    yield {"step": "install", "status": "running", "message": "安装 mihomo 到程序目录…"}
    try:
        _install_gz(gz)
    except UpgradeError as e:
        gz.unlink(missing_ok=True)
        yield {"step": "install", "status": "error", "message": str(e),
               "manual": _mihomo_manual_install(level)}
        return
    gz.unlink(missing_ok=True)
    if not is_installed():
        yield {"step": "install", "status": "error", "message": "安装后未找到 mihomo 二进制",
               "manual": _mihomo_manual_install(level)}
        return
    yield {"step": "install", "status": "done", "message": "安装完成"}

    # 4. 创建服务
    yield {"step": "service", "status": "running", "message": "创建 systemd 服务…"}
    _create_service()
    yield {"step": "service", "status": "done", "message": "服务已创建并设为开机自启"}

    # 5. 初始化配置
    yield {"step": "config", "status": "running", "message": "初始化配置目录…"}
    _init_config()
    yield {"step": "config", "status": "done", "message": "配置目录已就绪"}

    # 6. 启动服务
    yield {"step": "start", "status": "running", "message": "启动 mihomo 服务…"}
    subprocess.run(["systemctl", "enable", "mihomo"], check=False, timeout=30)
    proc = subprocess.run(
        ["systemctl", "start", "mihomo"], capture_output=True, text=True, timeout=30
    )
    new_ver = current_version()
    if proc.returncode != 0:
        yield {"step": "start", "status": "error",
               "message": f"已安装 v{new_ver} 但服务启动失败。{_level_hint()}",
               "manual": "sudo systemctl start mihomo\nsudo journalctl -u mihomo -n 50"}
        return
    yield {"step": "start", "status": "done", "message": f"mihomo v{new_ver} 服务已启动"}

    # 7. 释放内置面板
    yield {"step": "dash", "status": "running", "message": "释放内置 metacubexd 面板…"}
    r = ensure_dashboard()
    if not r.get("ok"):
        yield {"step": "dash", "status": "error", "message": r.get("error", "释放失败"),
               "manual": _dashboard_manual_install()}
        return
    yield {"step": "dash", "status": "done",
           "message": f"metacubexd 已释放到 {r['dir']}"}

    yield {"step": "done", "message": "全部安装完成！"}


def _mihomo_manual_install(level: str) -> str:
    """返回 mihomo 手动安装的 shell 命令（安装到程序目录布局）。"""
    dest = Path(MIHOMO_BIN).parent
    return (
        f"# 1. 下载最新版 mihomo（CPU 档位: {level}）\n"
        f"VER=$(curl -s https://api.github.com/repos/MetaCubeX/mihomo/releases/latest | grep tag_name | cut -d'\"' -f4 | tr -d v)\n"
        f"wget \"https://github.com/MetaCubeX/mihomo/releases/download/v${{VER}}/mihomo-linux-amd64-{level}-v${{VER}}.gz\" -O /tmp/mihomo.gz\n\n"
        f"# 2. 解压安装到程序目录\n"
        f"sudo gzip -d /tmp/mihomo.gz\n"
        f"sudo install -m 755 /tmp/mihomo {MIHOMO_BIN}\n\n"
        f"# 3. 创建 systemd 服务\n"
        f"sudo tee /etc/systemd/system/mihomo.service << 'EOF'\n"
        f"[Unit]\nDescription=mihomo\nAfter=network.target\n\n"
        f"[Service]\nType=simple\nExecStart={MIHOMO_BIN} -d {dest}\nRestart=on-failure\nRestartSec=5\n\n"
        f"[Install]\nWantedBy=multi-user.target\nEOF\n\n"
        f"# 4. 初始化配置\n"
        f"sudo mkdir -p {dest}/ruleset\n\n"
        f"# 5. 启动服务\n"
        f"sudo systemctl daemon-reload\n"
        f"sudo systemctl enable mihomo\n"
        f"sudo systemctl start mihomo\n\n"
        f"# 也可在面板「mihomo 更新」卡片直接上传下载好的 .gz 包安装"
    )


def _dashboard_manual_install() -> str:
    """返回 metacubexd 手动安装的 shell 命令（内置包不可用时的兜底）。"""
    dest = dashboard_dir()
    return (
        "# 手动安装 metacubexd 面板\n"
        f"sudo mkdir -p {dest}\n"
        "cd /tmp\n"
        "curl -L \"https://github.com/MetaCubeX/metacubexd/releases/latest/download/compressed-dist.tgz\" -o metacubexd.tgz\n"
        f"sudo tar xzf metacubexd.tgz -C {dest}\n"
        "rm metacubexd.tgz\n\n"
        "# 安装完成后在面板点击「重载配置」"
    )
