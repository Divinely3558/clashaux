"""订阅链接拉取与保存管理。"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.paths import DATA_DIR

SUBS_FILE = DATA_DIR / "subscriptions.json"
LOCAL_PROXY = "http://127.0.0.1:7890"


class SubscriptionError(Exception):
    """订阅拉取失败。"""


def fetch(url: str, use_proxy: bool = False) -> str:
    """拉取订阅内容，返回文本。"""
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise SubscriptionError("订阅链接必须以 http:// 或 https:// 开头")
    try:
        resp = httpx.get(
            url,
            timeout=30,
            follow_redirects=True,
            proxy=LOCAL_PROXY if use_proxy else None,
            headers={"User-Agent": "clashaux/0.1"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise SubscriptionError(f"拉取失败: {e}") from e
    if not resp.text.strip():
        raise SubscriptionError("订阅返回内容为空")
    return resp.text


def list_subs() -> list[dict]:
    """已保存的订阅列表。"""
    if not SUBS_FILE.exists():
        return []
    try:
        items = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return items if isinstance(items, list) else []


def save_sub(name: str, url: str, proxy: bool = False) -> tuple[bool, str]:
    url = url.strip()
    name = name.strip() or urlparse(url).netloc or "未命名"
    if not re.match(r"^https?://", url):
        return False, "链接必须以 http:// 或 https:// 开头"
    items = [s for s in list_subs() if s["name"] != name]
    items.append({"name": name, "url": url, "proxy": bool(proxy)})
    SUBS_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True, f"已保存订阅 {name}"


def rename_sub(old_name: str, new_name: str) -> tuple[bool, str]:
    """重命名已保存的订阅。"""
    old_name, new_name = old_name.strip(), new_name.strip()
    if not new_name:
        return False, "新名称不能为空"
    items = list_subs()
    target = next((s for s in items if s["name"] == old_name), None)
    if target is None:
        return False, "订阅不存在"
    if any(s["name"] == new_name for s in items):
        return False, f"订阅「{new_name}」已存在"
    target["name"] = new_name
    SUBS_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True, f"已重命名为 {new_name}"


def remove_sub(name: str) -> tuple[bool, str]:
    items = list_subs()
    rest = [s for s in items if s["name"] != name]
    if len(rest) == len(items):
        return False, "订阅不存在"
    SUBS_FILE.write_text(
        json.dumps(rest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True, f"已删除订阅 {name}"
