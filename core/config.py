"""配置换头、校验、备份与生效。

流程：新配置 → 去头(保留正文) → 拼接 base.yaml 头 → mihomo -t 校验
    → 备份当前生效配置 → 原子写入 → systemctl reload mihomo → 探活。
"""
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from core.paths import DATA_DIR

BASE_FILE = DATA_DIR / "base.yaml"
BACKUP_DIR = DATA_DIR / "backups"
KEEP_BACKUPS = 5

# /etc/mihomo/config.yaml 是指向真实配置的软链接；
# 必须写入解析后的真实路径，否则 os.replace 会把软链接整个替换掉。
LIVE_LINK = Path("/etc/mihomo/config.yaml")

# 属于"正文"的顶层键：新文件从第一个命中的键开始保留到文件尾
BODY_KEYS = ("proxies", "proxy-groups", "proxy-providers", "rule-providers", "rules", "sub-rules")


class ConfigError(Exception):
    """配置处理失败。"""


def _run(args: list[str], timeout: float = 120) -> tuple[int, str]:
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode, out


def live_file() -> Path:
    """返回生效配置的真实路径。"""
    if not LIVE_LINK.exists():
        raise ConfigError(f"未找到生效配置: {LIVE_LINK}")
    return LIVE_LINK.resolve()


def strip_header(text: str) -> str:
    """去掉新配置文件的头部，返回从第一个正文键起的原文（保留注释）。"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 解析失败: {e}") from e
    if not isinstance(data, dict) or not data:
        raise ConfigError("配置文件不是有效的 YAML 映射")

    start = next((k for k in data if k in BODY_KEYS), None)
    if start is None:
        raise ConfigError(f"未在新配置中找到正文键（{'/'.join(BODY_KEYS)}），无法识别正文")

    pat = re.compile(rf"^{re.escape(start)}\s*:")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if pat.match(line):
            return "\n".join(lines[i:]) + "\n"
    raise ConfigError(f"未在文件中定位到顶层键 {start}:")


def build_candidate(new_text: str) -> str:
    """base.yaml 头 + 新文件正文 = 候选配置。"""
    base = BASE_FILE.read_text(encoding="utf-8").rstrip("\n")
    body = strip_header(new_text).strip("\n")
    return f"{base}\n\n{body}\n"


def validate(text: str) -> tuple[bool, str]:
    """mihomo -t 校验候选配置，返回 (是否通过, 输出尾部)。"""
    fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix="clashaux-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        rc, out = _run(["mihomo", "-t", "-d", "/etc/mihomo", "-f", tmp], timeout=120)
    finally:
        os.unlink(tmp)
    tail = "\n".join(out.splitlines()[-30:])
    return rc == 0 and "test is successful" in out, tail


def stats(text: str) -> dict:
    """统计候选配置内容。"""
    data = yaml.safe_load(text) or {}
    count = lambda k: len(data.get(k) or [])  # noqa: E731
    return {
        "proxies": count("proxies"),
        "groups": count("proxy-groups"),
        "providers": count("proxy-providers"),
        "rules": count("rules"),
        "rule_providers": count("rule-providers"),
    }


def live_info() -> dict:
    """当前生效配置概况（只读，无需 root）。"""
    try:
        path = live_file()
    except ConfigError as e:
        return {"exists": False, "error": str(e)}
    info: dict = {"exists": True, "path": str(path), "link": str(LIVE_LINK)}
    st = path.stat()
    info["mtime"] = int(st.st_mtime)
    info["size"] = st.st_size
    try:
        info.update(stats(path.read_text(encoding="utf-8")))
    except Exception:
        pass
    return info


def backup_current() -> str:
    """备份当前生效配置到 backups/，超出保留数时清理旧备份。"""
    path = live_file()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = f"config-{datetime.now():%Y%m%d-%H%M%S}.yaml"
    shutil.copy2(path, BACKUP_DIR / name)
    olds = sorted(BACKUP_DIR.glob("config-*.yaml"))
    for p in olds[:-KEEP_BACKUPS]:
        p.unlink()
    return name


def list_backups() -> list[dict]:
    items = []
    if BACKUP_DIR.exists():
        for p in sorted(BACKUP_DIR.glob("config-*.yaml"), reverse=True):
            st = p.stat()
            items.append({
                "name": p.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
    return items


def rollback(name: str) -> tuple[bool, str]:
    """回滚到指定备份：校验通过后覆盖当前配置（先再备份一次）。"""
    if not re.fullmatch(r"config-[\w.-]+\.yaml", name):
        return False, "非法备份名"
    path = BACKUP_DIR / name
    if not path.exists():
        return False, "备份不存在"
    text = path.read_text(encoding="utf-8")
    ok, out = validate(text)
    if not ok:
        return False, f"备份文件校验未通过: {out}"
    if LIVE_LINK.exists():
        backup_current()
    fd, tmp = tempfile.mkstemp(suffix=".yaml", dir=str(live_file().parent))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, live_file())
    return True, f"已回滚到 {name}"


def apply(new_text: str) -> dict:
    """完整生效流程：校验 → 备份 → 写入。重载由调用方执行。"""
    candidate = build_candidate(new_text)
    ok, out = validate(candidate)
    if not ok:
        return {"ok": False, "stage": "validate", "error": "mihomo -t 校验未通过", "output": out}
    backup = backup_current()
    path = live_file()
    fd, tmp = tempfile.mkstemp(suffix=".yaml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(candidate)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except PermissionError as e:
        raise ConfigError(f"写入 {path} 失败，需要 root 权限: {e}") from e
    return {"ok": True, "backup": backup, "stats": stats(candidate)}
