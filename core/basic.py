"""基础连接设置：external-controller / external-ui / secret / mixed-port。

单独存于 basic.json，生成配置时置于文件最头部；base.yaml 不再包含这些键
（启动时自动从旧版 base.yaml 中剥离）。
"""
import json
import re
import secrets
import string
from pathlib import Path

import yaml

from core import mihomo
from core.paths import DATA_DIR

BASIC_FILE = DATA_DIR / "basic.json"

# 渲染顺序（写入配置文件最头部的顺序）
BASIC_KEYS = ("external-controller", "external-ui", "secret", "mixed-port")

_CONTROLLER_RE = re.compile(r"^[\w.\-]+:\d{1,5}$")


def random_secret(n: int = 10) -> str:
    """随机 n 位大小写字母混合密钥。"""
    alphabet = string.ascii_letters
    return "".join(secrets.choice(alphabet) for _ in range(n))


def defaults() -> dict:
    """默认基础设置（external-ui 指向 mihomo 数据目录下的 metacubexd/）。"""
    return {
        "external-controller": "0.0.0.0:9090",
        "external-ui": str(mihomo.dashboard_dir()),
        "secret": random_secret(),
        "mixed-port": 7890,
    }


def load() -> dict:
    """读取基础设置，缺失的键用默认值补齐并写回。"""
    data: dict = {}
    if BASIC_FILE.exists():
        try:
            raw = json.loads(BASIC_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except (json.JSONDecodeError, OSError):
            data = {}
    changed = False
    for key in BASIC_KEYS:
        if key not in data:
            data[key] = defaults()[key]
            changed = True
    if changed or not BASIC_FILE.exists():
        save(data)
    return data


def save(basics: dict) -> None:
    """保存基础设置到 basic.json。"""
    BASIC_FILE.write_text(
        json.dumps(basics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def validate(basics: dict) -> tuple[bool, str]:
    """校验基础设置，非法时返回 (False, 错误信息)。"""
    if not _CONTROLLER_RE.match(str(basics.get("external-controller", ""))):
        return False, "external-controller 格式应为 主机:端口（如 0.0.0.0:9090）"
    ui = str(basics.get("external-ui", "")).strip()
    if not ui.startswith("/"):
        return False, "external-ui 必须是绝对路径（以 / 开头）"
    if len(str(basics.get("secret", ""))) > 128:
        return False, "secret 过长（上限 128 字符）"
    try:
        port = int(basics.get("mixed-port", 0))
    except (TypeError, ValueError):
        return False, "mixed-port 必须是数字"
    if not 1 <= port <= 65535:
        return False, "mixed-port 取值范围 1-65535"
    return True, ""


def render(basics: dict) -> str:
    """渲染为基础设置 YAML 头（固定顺序，置于配置文件最头部）。"""
    lines = []
    for key in BASIC_KEYS:
        val = basics[key]
        if key == "mixed-port":
            lines.append(f"mixed-port: {int(val)}")
        else:
            # safe_dump 处理引号/特殊字符转义
            lines.append(
                yaml.safe_dump({key: str(val)}, allow_unicode=True,
                               default_flow_style=False, width=8192).rstrip("\n")
            )
    return "\n".join(lines)


def strip_keys(text: str) -> str:
    """去掉文本中顶层的基础设置键行（保留注释与其他内容）。"""
    pats = [re.compile(rf"^{re.escape(k)}\s*:") for k in BASIC_KEYS]
    kept = [
        line for line in text.splitlines()
        if not any(p.match(line) for p in pats)
    ]
    return "\n".join(kept) + ("\n" if kept else "")


def strip_base_file(base_file: Path) -> bool:
    """从 base.yaml 中剥离基础设置键，有变更时写回。"""
    if not base_file.exists():
        return False
    text = base_file.read_text(encoding="utf-8")
    stripped = strip_keys(text)
    if stripped != text:
        base_file.write_text(stripped, encoding="utf-8")
        return True
    return False
