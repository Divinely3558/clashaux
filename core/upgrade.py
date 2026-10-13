"""mihomo 版本检测与一键更新（deb 安装路径）。"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from core.paths import DATA_DIR
from core.service import service_action

GITHUB_API = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
LOCAL_PROXY = "http://127.0.0.1:7890"
MIHOMO_BIN = "/usr/bin/mihomo"
BAK_DIR = DATA_DIR / "backups"

HEADERS = {"User-Agent": "clashaux/0.1"}


class UpgradeError(Exception):
    """升级流程错误。"""


def current_version() -> str:
    """从 mihomo -v 解析当前版本号，如 1.19.32。"""
    try:
        out = subprocess.run(
            [MIHOMO_BIN, "-v"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"[vV](\d+(?:\.\d+)+)", out)
    return m.group(1) if m else ""


def goamd64_level() -> str:
    """根据 CPU 指令集判断可用的 GOAMD64 档位。"""
    try:
        flags = set(
            re.findall(
                r"\b(avx2|avx|bmi2|sse4_2|popcnt)\b",
                Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore"),
            )
        )
    except OSError:
        return "compatible"
    if {"avx2", "bmi2"} <= flags:
        return "v3"
    if {"sse4_2", "popcnt"} <= flags:
        return "v2"
    return "compatible"


def _latest_release(use_proxy: bool) -> dict:
    """查询 GitHub 最新 release，返回 {tag, deb_url}。"""
    try:
        resp = httpx.get(
            GITHUB_API,
            timeout=30,
            follow_redirects=True,
            proxy=LOCAL_PROXY if use_proxy else None,
            headers=HEADERS,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise UpgradeError(f"查询 GitHub 失败: {e}") from e
    tag = str(data.get("tag_name", "")).lstrip("v")
    if not re.fullmatch(r"\d+(?:\.\d+)+", tag):
        raise UpgradeError(f"无法识别的版本号: {data.get('tag_name')}")
    level = goamd64_level()
    name = f"mihomo-linux-amd64-{level}-v{tag}.deb"
    assets = {a["name"]: a for a in data.get("assets", [])}
    if name not in assets:
        # 目标档位缺失时降级：v2→默认(v1)→compatible
        fallbacks = {
            "v3": [f"mihomo-linux-amd64-v{tag}.deb"],
            "v2": [f"mihomo-linux-amd64-v{tag}.deb", f"mihomo-linux-amd64-compatible-v{tag}.deb"],
            "compatible": [],
        }
        for fb in fallbacks[level]:
            if fb in assets:
                name = fb
                break
        else:
            raise UpgradeError(f"release {tag} 中没有适用于 {level} 的 deb 包")
    return {"tag": tag, "deb_name": name, "deb_url": assets[name]["browser_download_url"]}


def _version_gt(a: str, b: str) -> bool:
    """a 是否大于 b（点分数字比较，位数不足补 0）。"""
    pa = [int(x) for x in a.split(".")]
    pb = [int(x) for x in b.split(".")]
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb


def check(use_proxy: bool = False) -> dict:
    """检测更新：当前版本 vs GitHub 最新版。"""
    cur = current_version()
    if not cur:
        return {"ok": False, "error": "无法获取当前 mihomo 版本"}
    rel = _latest_release(use_proxy)
    return {
        "ok": True,
        "current": cur,
        "latest": rel["tag"],
        "has_update": _version_gt(rel["tag"], cur),
        "level": goamd64_level(),
        "deb_name": rel["deb_name"],
    }


def _download_deb(url: str, use_proxy: bool) -> Path:
    """流式下载 deb 到临时文件。"""
    fd, tmp = tempfile.mkstemp(suffix=".deb", prefix="mihomo-")
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=httpx.Timeout(600, connect=30),
            follow_redirects=True,
            proxy=LOCAL_PROXY if use_proxy else None,
            headers=HEADERS,
        ) as resp:
            resp.raise_for_status()
            with os.fdopen(fd, "wb") as f:
                for chunk in resp.iter_bytes(65536):
                    f.write(chunk)
    except httpx.HTTPError as e:
        Path(tmp).unlink(missing_ok=True)
        raise UpgradeError(f"下载失败: {e}") from e
    return Path(tmp)


def _backup_binary(version: str) -> Path | None:
    """升级前备份当前二进制，用于回滚。"""
    if not Path(MIHOMO_BIN).exists():
        return None
    BAK_DIR.mkdir(parents=True, exist_ok=True)
    dest = BAK_DIR / f"mihomo-{version}.bin"
    if not dest.exists():  # 已有同名备份不覆盖
        shutil.copy2(MIHOMO_BIN, dest)
    return dest


def apply(use_proxy: bool = False) -> dict:
    """一键更新：下载 → 备份 → dpkg 安装 → 重启 → 验证。"""
    cur = current_version()
    if not cur:
        return {"ok": False, "error": "无法获取当前 mihomo 版本"}
    rel = _latest_release(use_proxy)
    if not _version_gt(rel["tag"], cur):
        return {"ok": True, "no_update": True,
                "message": f"已是最新版本 v{cur}"}

    deb = _download_deb(rel["deb_url"], use_proxy)
    try:
        _backup_binary(cur)
        # dpkg 安装（面板以 root 运行）
        proc = subprocess.run(
            ["dpkg", "-i", str(deb)], capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            raise UpgradeError(
                "dpkg 安装失败: " + (proc.stderr or proc.stdout).strip()[-500:]
            )
    finally:
        deb.unlink(missing_ok=True)

    new_ver = current_version()
    if new_ver != rel["tag"]:
        return {"ok": False, "error": f"安装后版本异常: 期望 {rel['tag']}，实际 {new_ver or '未知'}"}

    rok, rmsg = service_action("restart")
    if not rok:
        return {"ok": False, "stage": "restart",
                "error": f"已安装 v{new_ver} 但服务重启失败: {rmsg}（旧版备份在 backups/mihomo-{cur}.bin）"}

    return {"ok": True, "message": f"已从 v{cur} 升级到 v{new_ver}，服务重启成功",
            "backup": f"mihomo-{cur}.bin"}
