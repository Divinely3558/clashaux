"""HTTP 请求封装：直连失败时自动 DoH 解析回退 + 本机代理兜底。

尝试顺序（use_proxy=False 时）：
  1. 直连
  2. DoH 解析域名后按 IP 直连（SNI/Host 仍用原域名，证书按原域名校验）
  3. 本机代理 127.0.0.1:7890 兜底
use_proxy=True 时仅走本机代理（尊重用户显式选择）。
"""
import logging
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from core.paths import LOCAL_PROXY

logger = logging.getLogger("clashaux")

HEADERS = {"User-Agent": "clashaux/0.1"}

# 全部为纯 IP 的 DoH 服务，自身不依赖 DNS
_DOH_SERVERS = (
    "https://223.5.5.5/resolve",       # 阿里
    "https://120.53.53.53/dns-query",  # DNSPod
    "https://1.1.1.1/dns-query",       # Cloudflare
)
_DOH_CACHE: dict[str, tuple[float, str]] = {}
_DOH_CACHE_TTL = 600.0


class NetError(Exception):
    """网络请求最终失败（所有尝试均未成功）。"""


def doh_resolve(host: str) -> str | None:
    """通过 DoH 解析域名 A 记录，失败返回 None。"""
    cached = _DOH_CACHE.get(host)
    if cached and time.monotonic() - cached[0] < _DOH_CACHE_TTL:
        return cached[1]
    for server in _DOH_SERVERS:
        try:
            resp = httpx.get(
                server,
                params={"name": host, "type": "A"},
                headers={"accept": "application/dns-json"},
                timeout=5,
            )
            resp.raise_for_status()
            answers = [
                a["data"]
                for a in resp.json().get("Answer", [])
                if a.get("type") == 1
            ]
            if answers:
                ip = answers[0]
                _DOH_CACHE[host] = (time.monotonic(), ip)
                return ip
        except (httpx.HTTPError, ValueError):
            continue
    return None


def _with_host(url: str, ip: str) -> str:
    """把 URL 中的主机名替换为 IP（保留端口与路径）。"""
    s = urlsplit(url)
    netloc = ip if s.port is None else f"{ip}:{s.port}"
    return urlunsplit((s.scheme, netloc, s.path, s.query, s.fragment))


def _plan(url: str, use_proxy: bool, proxy_fallback: bool) -> list[dict]:
    """生成尝试顺序：[{url, sni, proxy, label}]。"""
    host = urlsplit(url).hostname or ""
    plans: list[dict] = []
    if use_proxy:
        plans.append({"url": url, "sni": None, "proxy": LOCAL_PROXY, "label": "本机代理"})
        return plans
    plans.append({"url": url, "sni": None, "proxy": None, "label": "直连"})
    ip = doh_resolve(host) if host else None
    if ip:
        plans.append({
            "url": _with_host(url, ip),
            "sni": host,
            "proxy": None,
            "label": f"DoH({ip})",
        })
    if proxy_fallback:
        plans.append({"url": url, "sni": None, "proxy": LOCAL_PROXY, "label": "本机代理回退"})
    return plans


def _short(e: BaseException) -> str:
    return str(e).strip()[:120] or type(e).__name__


def _client(proxy: str | None, headers: dict | None) -> httpx.Client:
    """按线路构造 Client（proxy 仅能在构造时指定）。"""
    kwargs: dict = {"follow_redirects": True, "headers": headers or HEADERS}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def http_get(
    url: str,
    *,
    timeout: float = 30,
    use_proxy: bool = False,
    proxy_fallback: bool = True,
    headers: dict | None = None,
) -> httpx.Response:
    """GET 请求，按 直连→DoH→代理 顺序自动回退。

    成功时把实际使用的线路写入 resp.extensions["via"]。
    全部失败抛 NetError。
    """
    errors: list[str] = []
    for p in _plan(url, use_proxy, proxy_fallback):
        try:
            with _client(p["proxy"], headers) as client:
                extensions = {"sni_hostname": p["sni"]} if p["sni"] else {}
                resp = client.get(p["url"], timeout=timeout, extensions=extensions)
            resp.raise_for_status()
            resp.extensions["via"] = p["label"]
            if p["label"] != "直连":
                logger.info("GET %s 成功（经 %s）", url, p["label"])
            return resp
        except httpx.TransportError as e:
            errors.append(f"{p['label']}: {_short(e)}")
            logger.warning("GET %s 经 %s 失败: %s", url, p["label"], _short(e))
        except httpx.HTTPStatusError as e:
            # 服务端有响应，不换线路重试
            raise NetError(f"{p['label']} 返回 {e.response.status_code}") from e
    raise NetError("；".join(errors) or "未知网络错误")
