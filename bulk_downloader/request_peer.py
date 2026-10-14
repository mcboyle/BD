"""Who is on the other end of a request: the loopback test the admission gates share.

A gate that trusts "the operator on the box itself" (GET /api/pair minting a pairing code) must not read a reverse
proxy on the box as that operator. The shipped conf/Caddyfile fronts the app with `reverse_proxy localhost:5555`, so
every request through it arrives from 127.0.0.1 with the real client in X-Forwarded-For. Here a loopback peer counts
as the box itself only when no forwarding header is present, or when the peer is a configured trusted proxy and the
forwarded client is itself loopback. 127.0.0.0/8, ::1 and IPv4-mapped loopback (::ffff:127.0.0.1) are all loopback.

Every loopback admission goes through client_is_loopback(request, trusted_proxies()): the /api/dev gate
(app._dev_request_authorized) and the /api/pair mint (app_pair._pair_mint_authorized). A raw
request.remote_addr loopback comparison anywhere else is refused by tests/test_pair_admission_proxy_is_not_loopback.py.
"""

import ipaddress

FORWARDING_HEADERS = ("Forwarded", "X-Forwarded-For", "X-Real-IP")


def parse_addr(value):
    """An ip_address from a peer / forwarded-hop string, IPv4-mapped IPv6 unwrapped; None if it is not one.
    Accepts "[v6]:port", "v4:port", a zone suffix, and the literal "localhost"."""
    v = str(value or "").strip().strip('"')
    if v.lower() == "localhost":
        v = "127.0.0.1"
    if v.startswith("["):
        v = v[1:v.find("]")] if "]" in v else ""
    elif v.count(":") == 1:
        v = v.split(":", 1)[0]
    v = v.split("%", 1)[0]
    try:
        addr = ipaddress.ip_address(v)
    except ValueError:
        return None
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr


def is_loopback_addr(value) -> bool:
    addr = parse_addr(value)
    return bool(addr is not None and addr.is_loopback)


def _networks(trusted):
    nets = []
    for entry in trusted or ():
        text = str(entry).strip()
        addr = parse_addr(text)
        try:
            nets.append(ipaddress.ip_network(addr if addr is not None else text, strict=False))
        except ValueError:
            continue
    return nets


def _trusted(addr, nets) -> bool:
    return addr is not None and any(addr.version == n.version and addr in n for n in nets)


def _hops(header, value):
    """Forwarded client hops, left (origin) to right (nearest proxy), as raw strings."""
    if header == "Forwarded":
        hops = []
        for element in value.split(","):
            for pair in element.split(";"):
                key, _, val = pair.strip().partition("=")
                if key.strip().lower() == "for":
                    hops.append(val.strip())
        return hops
    return [h.strip() for h in value.split(",")]


def client_is_loopback(request, trusted_proxies=()) -> bool:
    """Whether the client behind `request` is on this box.

    - Peer (remote_addr) not loopback: False.
    - No Forwarded / X-Forwarded-For / X-Real-IP header: the peer decides (True).
    - A forwarding header from a peer not in `trusted_proxies`: False. A local proxy is not the operator, and
      the header's content is client-written, so even "127.0.0.1" in it proves nothing.
    - Peer in `trusted_proxies`: in every forwarding header present, the right-most hop not in
      `trusted_proxies` must be loopback. An unparseable hop, or a header whose hops are all trusted
      (no client named), fails closed (False).
    """
    peer = parse_addr(request.remote_addr)
    if peer is None or not peer.is_loopback:
        return False
    present = [(h, request.headers.get(h)) for h in FORWARDING_HEADERS if request.headers.get(h) is not None]
    if not present:
        return True
    nets = _networks(trusted_proxies)
    if not _trusted(peer, nets):
        return False
    for header, value in present:
        client = None
        for hop in reversed(_hops(header, value)):
            addr = parse_addr(hop)
            if addr is None:
                return False
            if not _trusted(addr, nets):
                client = addr
                break
        if client is None or not client.is_loopback:
            return False
    return True


def trusted_proxies() -> list:
    """trusted_proxies from app_config.json: reverse proxies (IPs or CIDRs) whose forwarding headers are believed.
    Empty by default, so any forwarded request is remote. A list, or one comma/space separated string; unreadable
    -> []. global_config is imported here, at call time, so this module stays a leaf at import."""
    try:
        from bulk_downloader import global_config

        raw = global_config.get("trusted_proxies", []) or []
        if isinstance(raw, str):
            raw = raw.replace(",", " ").split()
        return [str(x).strip() for x in raw if str(x).strip()]
    except Exception:
        return []
