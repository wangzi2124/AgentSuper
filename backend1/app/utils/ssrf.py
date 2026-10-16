"""SSRF 防护：HTTP 工具/插件出站请求前的目标地址校验。

- 仅允许 http/https 协议
- 拒绝私有 / 环回 / 链路本地 / 保留地址及云元数据地址（169.254.169.254 等）
- 主机名先经 DNS 解析再逐 IP 校验，避免裸 IP / 指向内网的域名绕过
- 默认严格拦截；设置环境变量 SSRF_ALLOW_INTERNAL=true 可放行（用于本地调试场景）
"""

import ipaddress
import logging
import os
import socket
import urllib.parse

logger = logging.getLogger(__name__)

# 云元数据服务地址（AWS/GCP/Azure 169.254.169.254，阿里云 100.100.100.200 系列）
_METADATA_HOSTS = (
    "metadata",
    "metadata.google.internal",
    "169.254.169.254",
    "100.100.100.200",
    "100.100.100.132",
    "100.100.100.204",
)

_TRUE_VALUES = ("1", "true", "yes", "on")


def allow_internal() -> bool:
    """是否放行内网地址（默认不放行）。"""
    return os.environ.get("SSRF_ALLOW_INTERNAL", "").strip().lower() in _TRUE_VALUES


def is_internal_ip(ip: str) -> bool:
    """判断单个 IP 是否属于内网/环回/保留段。"""
    try:
        addr = ipaddress.ip_address(ip.strip().strip("[]"))
    except ValueError:
        return False
    if (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    ):
        return True
    # IPv4 映射的 IPv6（如 ::ffff:127.0.0.1）按内嵌 IPv4 判断
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return is_internal_ip(str(addr.ipv4_mapped))
    return False


def _host_is_internal(host: str) -> bool:
    """判断主机名是否指向内网地址。

    裸 IP 直接判断；域名则解析全部 A/AAAA 记录，任一命中内网即视为内网。
    """
    host = host.strip().strip("[]").lower().rstrip(".")
    if not host:
        return True
    if host in _METADATA_HOSTS:
        return True
    if host in ("localhost", "localhost.localdomain"):
        return True
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan"):
        return True

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return is_internal_ip(host)

    # 域名 → 解析全部记录
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError):
        logger.warning("SSRF check: host %r unresolvable", host)
        return False
    for info in infos:
        try:
            if is_internal_ip(info[4][0]):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def validate_http_url(url: str) -> str:
    """校验出站 HTTP 地址，返回错误文案（空串表示通过）。

    SSRF_ALLOW_INTERNAL=true 时跳过内网拦截（协议与格式校验仍生效）。
    """
    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return f"Error: invalid URL '{url}'"
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"Error: URL scheme '{scheme or 'unknown'}' not allowed (http/https only)"
    host = parsed.hostname or ""
    if not host:
        return f"Error: URL missing host: '{url}'"
    if not allow_internal() and _host_is_internal(host):
        return (
            f"Error: URL '{url}' targets an internal/private address "
            "and is blocked by SSRF protection (set SSRF_ALLOW_INTERNAL=true to allow)"
        )
    return ""


def check_url(url: str) -> None:
    """校验出站地址并抛出 ValueError（供同步工具直接 raise）。"""
    err = validate_http_url(url)
    if err:
        raise ValueError(err)
