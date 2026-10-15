from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
_METADATA_IP = ipaddress.ip_address("169.254.169.254")

class SSRFBlocked(ValueError):
    pass

def _classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allowed_subnets: list[str] | None=None) -> tuple[bool, str]:
    if ip.is_loopback:
        return (False, f"loopback address {ip} is blocked")
    if ip == _METADATA_IP:
        return (False, f"cloud metadata address {ip} is blocked")
    if ip.is_link_local:
        return (False, f"link-local address {ip} is blocked")
    if ip.is_multicast:
        return (False, f"multicast address {ip} is blocked")
    if ip.is_unspecified:
        return (False, f"unspecified address {ip} is blocked")
    if ip.is_reserved:
        return (False, f"reserved address {ip} is blocked")
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return (False, f"carrier-grade NAT address {ip} is blocked")
    if ip.is_private:
        if allowed_subnets:
            for subnet_str in allowed_subnets:
                try:
                    net = ipaddress.ip_network(subnet_str, strict=False)
                    if ip in net:
                        return (True, f"permitted by declared subnet {subnet_str}")
                except ValueError:
                    continue
        return (False, f"private subnet address {ip} is blocked")
    return (True, "public IP")

def is_safe_url(url: str, allowed_subnets: list[str] | None=None) -> tuple[bool, str]:
    if not url or not isinstance(url, str):
        return (False, "empty or non-string URL")
    try:
        parsed = urlparse(url.strip())
    except (ValueError, TypeError) as exc:
        return (False, f"unparseable URL: {exc}")
    if not parsed.scheme or parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return (False, f"unsupported scheme {parsed.scheme!r}; only http and https allowed")
    hostname = parsed.hostname
    if not hostname:
        return (False, "missing hostname")
    host_clean = hostname.strip().rstrip(".").lower()
    if host_clean in {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}:
        return (False, "localhost domain is blocked")
    try:
        ip_str = host_clean.strip("[]")
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            allowed, reason = _classify_ip(ip_obj, allowed_subnets)
            if not allowed:
                return (False, reason)
            return (True, "")
        except ValueError:
            pass
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addr_infos = socket.getaddrinfo(host_clean, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not addr_infos:
            return (False, f"could not resolve host {host_clean}")
        for addr_info in addr_infos:
            sockaddr = addr_info[4]
            raw_ip = sockaddr[0]
            ip_obj = ipaddress.ip_address(raw_ip)
            allowed, reason = _classify_ip(ip_obj, allowed_subnets)
            if not allowed:
                return (False, f"host {host_clean} resolves to {reason}")
        return (True, "")
    except socket.gaierror as exc:
        return (False, f"DNS resolution failed for {host_clean}: {exc}")
    except (OSError, ValueError) as exc:
        return (False, f"URL validation error: {exc}")

def validate_url(url: str, allowed_subnets: list[str] | None=None) -> str:
    safe, reason = is_safe_url(url, allowed_subnets=allowed_subnets)
    if not safe:
        raise SSRFBlocked(f"SSRF policy refusal: {reason}")
    return url.strip()

def sanitize_url(url: str, allowed_subnets: list[str] | None=None) -> str:
    clean_url = validate_url(url, allowed_subnets=allowed_subnets)
    parsed = urlparse(clean_url)
    if parsed.username or parsed.password:
        port_suffix = f":{parsed.port}" if parsed.port else ""
        safe_netloc = f"{parsed.hostname}{port_suffix}"
        clean_url = urlunparse((parsed.scheme, safe_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    return clean_url

def resolve_and_pin_host(url: str, allowed_subnets: list[str] | None=None) -> tuple[str, str, int]:
    validated = validate_url(url, allowed_subnets=allowed_subnets)
    parsed = urlparse(validated)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        ip_obj = ipaddress.ip_address(host.strip("[]"))
        return (str(ip_obj), host, port)
    except ValueError:
        pass
    addr_infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    if not addr_infos:
        raise SSRFBlocked(f"No address returned for {host}")
    first_ip = addr_infos[0][4][0]
    allowed, reason = _classify_ip(ipaddress.ip_address(first_ip), allowed_subnets)
    if not allowed:
        raise SSRFBlocked(f"Resolved IP {first_ip} blocked: {reason}")
    return (str(first_ip), host, port)
