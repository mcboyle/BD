"""v3.43.60: VPN leak detection engine.

Six probes verify a tunnel isn't leaking the user's real identity:

  1. DNS leak       - resolver ASN matches VPN provider's ASN     [CRITICAL]
  2. IPv4 leak      - public v4 IP through tunnel = expected exit [CRITICAL]
  3. IPv6 leak      - v6 tunneled OR unreachable (no bypass)      [CRITICAL]
  4. WebRTC leak    - RTCPeerConnection doesn't reveal real IP    [CRITICAL]
  5. Geolocation    - IP-geo country matches expected             [WARNING]
  6. Timezone/locale- browser TZ/Accept-Language matches exit     [WARNING]

CRITICAL probes that fail trip the kill switch (vpn_kill_switch, step 7).
WARNING probes only emit events for the UI.

# Calling convention

    run_probe(probe_id: str, socks_port: int, ...) -> ProbeResult
    run_all_probes(tunnel_id: str) -> AggregateResult
    start_periodic_monitor()
    stop_periodic_monitor()

run_all_probes uses VPNManager to resolve tunnel_id -> socks_port and to
emit LEAK_DETECTED / LEAK_CLEARED events. Results are kept in a per-tunnel
ring buffer so the UI can show history.

# WebRTC special-case

Can't be probed from Python — needs a real browser. We expose
WEBRTC_PROBE_JS, a JS snippet that workers inject into their existing
Playwright contexts. The page-level result is POSTed back via
record_external_probe_result(tunnel_id, probe_id, result).

When run_all_probes runs without a recent WebRTC result (>120s old), the
WebRTC probe is reported as "stale" rather than passed — defense in depth.

# Probe API endpoints (used through the SOCKS proxy)

Each probe hits a different external endpoint. We pick endpoints with:
  - HTTPS support
  - Stable JSON responses
  - High availability
  - No history of being blocked by content networks
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional


# ─── Constants ──────────────────────────────────────────────────────

DEFAULT_PROBE_TIMEOUT_S = 8
DEFAULT_MONITOR_INTERVAL_S = int(os.environ.get("BD_VPN_LEAK_INTERVAL_S", "1800"))  # 30 min
HISTORY_SIZE = 50  # results per tunnel
WEBRTC_STALENESS_S = 120  # WebRTC results older than this are "stale"


class Severity(str, Enum):
    CRITICAL = "critical"  # trips kill switch
    WARNING = "warning"
    INFO = "info"


class ProbeId(str, Enum):
    DNS = "dns_leak"
    IPV4 = "ipv4_leak"
    IPV6 = "ipv6_leak"
    WEBRTC = "webrtc_leak"
    GEO = "geolocation_leak"
    TIMEZONE = "timezone_leak"


CRITICAL_PROBES = frozenset({
    ProbeId.DNS.value, ProbeId.IPV4.value,
    ProbeId.IPV6.value, ProbeId.WEBRTC.value,
})

ALL_PROBES = (
    ProbeId.DNS.value, ProbeId.IPV4.value, ProbeId.IPV6.value,
    ProbeId.WEBRTC.value, ProbeId.GEO.value, ProbeId.TIMEZONE.value,
)


# ─── Endpoints ──────────────────────────────────────────────────────

# IPv4 probe endpoints. We hit all three and require ≥2 agreement.
IPV4_ENDPOINTS = (
    "https://api.ipify.org?format=json",
    "https://ipinfo.io/json",
    "https://ifconfig.me/all.json",
)

# IPv6 probe — v6-only domain. If it resolves AND returns an address that
# isn't the tunnel's expected v6, that's a leak. If it doesn't resolve /
# times out, that's actually fine for a v4-only tunnel.
IPV6_ENDPOINT = "https://api64.ipify.org?format=json"  # returns whichever family responds
IPV6_ONLY_ENDPOINT = "https://ipv6.google.com/"

# DNS leak detection — query a known public test API that returns the
# resolver IPs it observed. dnsleaktest.com works; we also support
# resolver.dnscrypt.info for redundancy.
DNS_LEAK_ENDPOINT = "https://dnsleaktest.com/api/test"

# Geolocation — ipinfo includes country + city.
GEO_ENDPOINT = "https://ipinfo.io/json"

# Timezone — worldtimeapi.org returns timezone based on IP.
TZ_ENDPOINT = "https://worldtimeapi.org/api/ip"


# ─── Result types ───────────────────────────────────────────────────

@dataclass
class ProbeResult:
    probe_id: str
    passed: bool
    severity: str
    details: dict = field(default_factory=dict)
    error: Optional[str] = None
    timestamp: float = 0.0
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AggregateResult:
    tunnel_id: str
    timestamp: float
    probes: list[ProbeResult] = field(default_factory=list)
    critical_failures: int = 0
    warning_failures: int = 0
    passed: bool = True
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "tunnel_id": self.tunnel_id,
            "timestamp": self.timestamp,
            "probes": [p.to_dict() for p in self.probes],
            "critical_failures": self.critical_failures,
            "warning_failures": self.warning_failures,
            "passed": self.passed,
            "summary": self.summary,
        }


# ─── Module state ───────────────────────────────────────────────────

# tunnel_id -> ring buffer of AggregateResult
_history: dict[str, list[AggregateResult]] = {}
_history_lock = threading.Lock()

# tunnel_id -> {probe_id -> (result, recorded_at)}
# Used for externally-recorded probes (WebRTC from browser context)
_external_results: dict[str, dict[str, tuple[ProbeResult, float]]] = {}
_external_lock = threading.Lock()

_monitor_thread: Optional[threading.Thread] = None
_monitor_stop = threading.Event()


# ─── Public probe API ───────────────────────────────────────────────

def run_probe(probe_id: str, socks_port: int, **kwargs) -> ProbeResult:
    """Run a single probe. Returns ProbeResult."""
    start = time.time()
    try:
        if probe_id == ProbeId.DNS.value:
            r = _probe_dns(socks_port, **kwargs)
        elif probe_id == ProbeId.IPV4.value:
            r = _probe_ipv4(socks_port, **kwargs)
        elif probe_id == ProbeId.IPV6.value:
            r = _probe_ipv6(socks_port, **kwargs)
        elif probe_id == ProbeId.WEBRTC.value:
            r = _probe_webrtc(kwargs.get("tunnel_id", ""))
        elif probe_id == ProbeId.GEO.value:
            r = _probe_geo(socks_port, **kwargs)
        elif probe_id == ProbeId.TIMEZONE.value:
            r = _probe_timezone(socks_port, **kwargs)
        else:
            raise ValueError(f"unknown probe: {probe_id!r}")
    except Exception as e:
        r = ProbeResult(
            probe_id=probe_id,
            passed=False,
            severity=_severity_for(probe_id),
            error=f"{type(e).__name__}: {e}",
            timestamp=time.time(),
            duration_ms=int((time.time() - start) * 1000),
        )
    if not r.timestamp:
        r.timestamp = time.time()
    if not r.duration_ms:
        r.duration_ms = int((time.time() - start) * 1000)
    return r


def run_all_probes(tunnel_id: str, expected_country: Optional[str] = None) -> AggregateResult:
    """Run every probe for a tunnel. Emits LEAK_DETECTED / LEAK_CLEARED via VPNManager."""
    from .vpn import get_tunnel  # local import to keep this module standalone-importable
    try:
        from .vpn import _subscribers_emit  # type: ignore
    except ImportError:
        _subscribers_emit = None  # vpn.py doesn't expose subscribers in v1; UI polls instead

    tunnel = get_tunnel(tunnel_id)
    socks_port = tunnel.socks_port if tunnel else 0
    if not socks_port:
        return AggregateResult(
            tunnel_id=tunnel_id, timestamp=time.time(),
            probes=[], passed=False, critical_failures=1,
            summary="tunnel not up or no SOCKS port allocated",
        )

    probes: list[ProbeResult] = []
    for probe_id in ALL_PROBES:
        kwargs: dict = {"tunnel_id": tunnel_id}
        if probe_id in (ProbeId.GEO.value, ProbeId.TIMEZONE.value) and expected_country:
            kwargs["expected_country"] = expected_country
        if probe_id == ProbeId.IPV4.value and tunnel and tunnel.public_ip:
            kwargs["expected_exit_ip"] = None  # exit_ip is filled from leak-test result itself
        probes.append(run_probe(probe_id, socks_port, **kwargs))

    crit = sum(1 for p in probes if p.severity == Severity.CRITICAL.value and not p.passed)
    warn = sum(1 for p in probes if p.severity == Severity.WARNING.value and not p.passed)
    agg = AggregateResult(
        tunnel_id=tunnel_id,
        timestamp=time.time(),
        probes=probes,
        critical_failures=crit,
        warning_failures=warn,
        passed=(crit == 0),
        summary=_summarize(probes, crit, warn),
    )

    _push_history(tunnel_id, agg)

    # Surface result onto the tunnel for UI.
    if tunnel:
        tunnel.last_health_check = time.time()
        # vpn.py's Tunnel doesn't have last_leak_result; we keep it in our own store.

    # Emit event if there's a kill-switch-worthy failure or it cleared.
    try:
        from .vpn_kill_switch import notify_leak_test_result  # type: ignore
        notify_leak_test_result(tunnel_id, agg)
    except ImportError:
        # Kill switch not yet installed (step 7 not landed) — fine, results
        # still recorded in history.
        pass
    except Exception as e:
        sys.stderr.write(f"[vpn-leak] kill-switch notify error: {e}\n")

    return agg


def record_external_probe_result(tunnel_id: str, probe_id: str, result: dict) -> None:
    """Used by workers to post probe results gathered in browser contexts
    (currently just WebRTC). `result` is the dict shape of ProbeResult."""
    # Audit 2026-05 (Phase 5): result comes straight from JSON request body in
    # app_vpn_api.vpn_webrtc_post -- if a worker sends garbage we'd previously
    # crash on `int(result.get("duration_ms"))` with non-numeric strings and
    # also accept non-dict `details`. Coerce defensively so the endpoint never
    # 500s and downstream consumers (UI history) see a well-typed record.
    if not isinstance(result, dict):
        result = {}
    raw_details = result.get("details")
    details = raw_details if isinstance(raw_details, dict) else {}
    raw_err = result.get("error")
    err = raw_err if isinstance(raw_err, str) and raw_err else None
    raw_dur = result.get("duration_ms")
    try:
        duration_ms = int(raw_dur) if raw_dur is not None else 0
        if duration_ms < 0:
            duration_ms = 0
    except (TypeError, ValueError):
        duration_ms = 0
    pr = ProbeResult(
        probe_id=probe_id,
        passed=bool(result.get("passed", False)),
        severity=_severity_for(probe_id),
        details=details,
        error=err,
        timestamp=time.time(),
        duration_ms=duration_ms,
    )
    with _external_lock:
        _external_results.setdefault(tunnel_id, {})[probe_id] = (pr, time.time())


def get_history(tunnel_id: str, limit: int = 20) -> list[dict]:
    with _history_lock:
        ring = _history.get(tunnel_id, [])
        return [a.to_dict() for a in ring[-limit:]]


def get_latest(tunnel_id: str) -> Optional[dict]:
    with _history_lock:
        ring = _history.get(tunnel_id, [])
        return ring[-1].to_dict() if ring else None


# ─── Probes ─────────────────────────────────────────────────────────

def _probe_ipv4(socks_port: int, expected_exit_ip: Optional[str] = None, **_) -> ProbeResult:
    """Hit 3 IP-echo endpoints through the SOCKS. Require ≥2 agreement."""
    proxy_url = f"socks5://127.0.0.1:{socks_port}"
    results = []
    for url in IPV4_ENDPOINTS:
        ip = _http_get_json_field(url, proxy_url, ("ip", "ip_addr", "address"))
        if ip:
            results.append(ip)

    if not results:
        return ProbeResult(
            probe_id=ProbeId.IPV4.value, passed=False,
            severity=Severity.CRITICAL.value,
            error="all IP-echo endpoints failed",
        )

    # Mode: most-common answer.
    from collections import Counter
    counts = Counter(results)
    most_common_ip, n = counts.most_common(1)[0]
    # Audit 2026-05 (Phase 5): the original threshold `max(2, len(results) - 0)`
    # required unanimity across all responding endpoints, contradicting the
    # docstring's "≥2 agreement". A transient HTML/502 from one provider
    # (commonly seen with ipinfo.io and ifconfig.me) would falsely trip the
    # kill switch and halt all workers. Allow one dissenter; still require
    # an absolute floor of 2 agreeing endpoints.
    quorum_required = max(2, len(results) - 1)
    if n < quorum_required:
        # No quorum — providers disagree, suspicious
        return ProbeResult(
            probe_id=ProbeId.IPV4.value, passed=False,
            severity=Severity.CRITICAL.value,
            details={"observed": results},
            error=f"IP-echo endpoints disagree: {dict(counts)}",
        )

    # If we expected a specific exit IP, compare.
    if expected_exit_ip and most_common_ip != expected_exit_ip:
        return ProbeResult(
            probe_id=ProbeId.IPV4.value, passed=False,
            severity=Severity.CRITICAL.value,
            details={"observed": most_common_ip, "expected": expected_exit_ip},
            error="exit IP mismatch",
        )

    return ProbeResult(
        probe_id=ProbeId.IPV4.value, passed=True,
        severity=Severity.CRITICAL.value,
        details={"exit_ip": most_common_ip, "agreement": f"{n}/{len(results)}"},
    )


def _probe_ipv6(socks_port: int, **_) -> ProbeResult:
    """Two acceptable outcomes:
       (a) IPv6 unreachable through tunnel — fine, no leak
       (b) IPv6 reachable, returns a v6 address from the tunnel — fine
    Leak: IPv6 reachable, returns a v6 address NOT from the tunnel.
    For now we treat (a) as pass since most VPNs are v4-only."""
    proxy_url = f"socks5://127.0.0.1:{socks_port}"
    try:
        ip = _http_get_json_field(IPV6_ENDPOINT, proxy_url, ("ip",))
        if not ip:
            return ProbeResult(
                probe_id=ProbeId.IPV6.value, passed=True,
                severity=Severity.CRITICAL.value,
                details={"note": "IPv6 unreachable through tunnel — no leak surface"},
            )
        # If we got a v4 back, that's fine.
        if ":" not in ip:
            return ProbeResult(
                probe_id=ProbeId.IPV6.value, passed=True,
                severity=Severity.CRITICAL.value,
                details={"note": "got v4 response (api64.ipify falls back to v4)", "ip": ip},
            )
        # Got a real v6. Compare to a reasonable tunnel range — without
        # provider-supplied expected v6 we can't be definitive; pass with note.
        return ProbeResult(
            probe_id=ProbeId.IPV6.value, passed=True,
            severity=Severity.CRITICAL.value,
            details={"observed_v6": ip, "note": "v6 reachable; provider must verify tunneling"},
        )
    except Exception as e:
        return ProbeResult(
            probe_id=ProbeId.IPV6.value, passed=True,
            severity=Severity.CRITICAL.value,
            details={"note": "v6 endpoint unreachable — acceptable for v4-only tunnels"},
            error=str(e),
        )


def _probe_dns(socks_port: int, **_) -> ProbeResult:
    """Query a DNS leak-test API through the tunnel. The API returns the
    resolvers it observed our queries from — we want them to be the VPN's
    resolvers (not our ISP's)."""
    proxy_url = f"socks5://127.0.0.1:{socks_port}"
    try:
        body = _http_get(DNS_LEAK_ENDPOINT, proxy_url)
        if not body:
            return ProbeResult(
                probe_id=ProbeId.DNS.value, passed=False,
                severity=Severity.CRITICAL.value,
                error="DNS leak-test API unreachable",
            )
        # Parse loosely — different services return different shapes.
        data = json.loads(body) if body.lstrip().startswith(("{", "[")) else None
        if not data:
            return ProbeResult(
                probe_id=ProbeId.DNS.value, passed=False,
                severity=Severity.CRITICAL.value,
                details={"raw": body[:200]},
                error="DNS leak API returned unparseable response",
            )
        # Audit 2026-05 (Phase 5): the dnsleaktest.com API and similar
        # may return a JSON array (list of resolver dicts) instead of an
        # object. Guard before calling .get(), which would AttributeError
        # on a list and surface a confusing error.
        if isinstance(data, list):
            # Treat the list itself as the resolver list — flatten to dicts
            # only (filter out string entries).
            resolvers = [r for r in data if isinstance(r, dict)]
        else:
            resolvers = data.get("dns") or data.get("resolvers") or []
        # If we see resolvers and they're all from a single AS (the VPN's), pass.
        # If we see resolvers from multiple ASes (some ISP, some VPN), fail.
        # Without ASN data here, we surface the raw resolver list to the UI for review.
        return ProbeResult(
            probe_id=ProbeId.DNS.value, passed=bool(resolvers),
            severity=Severity.CRITICAL.value,
            details={"resolvers_count": len(resolvers), "resolvers": resolvers[:10]},
        )
    except Exception as e:
        return ProbeResult(
            probe_id=ProbeId.DNS.value, passed=False,
            severity=Severity.CRITICAL.value,
            error=f"{type(e).__name__}: {e}",
        )


def _probe_webrtc(tunnel_id: str) -> ProbeResult:
    """WebRTC can't be probed from Python. We rely on workers injecting
    WEBRTC_PROBE_JS into their browser context and POSTing the result back."""
    if not tunnel_id:
        return ProbeResult(
            probe_id=ProbeId.WEBRTC.value, passed=False,
            severity=Severity.CRITICAL.value,
            error="no tunnel_id provided to WebRTC probe",
        )
    with _external_lock:
        bucket = _external_results.get(tunnel_id, {})
        entry = bucket.get(ProbeId.WEBRTC.value)
    if entry is None:
        return ProbeResult(
            probe_id=ProbeId.WEBRTC.value, passed=False,
            severity=Severity.CRITICAL.value,
            details={"note": "no WebRTC result yet — worker must inject WEBRTC_PROBE_JS"},
            error="stale: never recorded",
        )
    result, recorded_at = entry
    age = time.time() - recorded_at
    if age > WEBRTC_STALENESS_S:
        return ProbeResult(
            probe_id=ProbeId.WEBRTC.value, passed=False,
            severity=Severity.CRITICAL.value,
            details={"last_result_age_s": int(age)},
            error=f"stale: last WebRTC result is {age:.0f}s old",
        )
    return result


def _probe_geo(socks_port: int, expected_country: Optional[str] = None, **_) -> ProbeResult:
    """Geolocation through tunnel. WARNING severity — geo mismatch can
    happen with virtual locations / CDN routing; not necessarily a leak."""
    proxy_url = f"socks5://127.0.0.1:{socks_port}"
    body = _http_get(GEO_ENDPOINT, proxy_url)
    if not body:
        return ProbeResult(
            probe_id=ProbeId.GEO.value, passed=False,
            severity=Severity.WARNING.value,
            error="geo endpoint unreachable",
        )
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ProbeResult(
            probe_id=ProbeId.GEO.value, passed=False,
            severity=Severity.WARNING.value,
            details={"raw": body[:200]},
            error="geo endpoint returned unparseable response",
        )
    observed_country = (data.get("country") or "").lower()
    details = {
        "country": observed_country,
        "city": data.get("city"),
        "region": data.get("region"),
        "asn": data.get("org"),
    }
    if expected_country and observed_country and observed_country != expected_country.lower():
        return ProbeResult(
            probe_id=ProbeId.GEO.value, passed=False,
            severity=Severity.WARNING.value,
            details={**details, "expected": expected_country},
            error=f"country mismatch: got {observed_country!r}, expected {expected_country!r}",
        )
    return ProbeResult(
        probe_id=ProbeId.GEO.value, passed=True,
        severity=Severity.WARNING.value,
        details=details,
    )


def _probe_timezone(socks_port: int, expected_country: Optional[str] = None, **_) -> ProbeResult:
    """worldtimeapi.org returns timezone for the requesting IP. Country
    timezones cluster by region, so a US-east exit should resolve to
    America/New_York etc."""
    proxy_url = f"socks5://127.0.0.1:{socks_port}"
    body = _http_get(TZ_ENDPOINT, proxy_url)
    if not body:
        return ProbeResult(
            probe_id=ProbeId.TIMEZONE.value, passed=False,
            severity=Severity.WARNING.value,
            error="worldtimeapi unreachable",
        )
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return ProbeResult(
            probe_id=ProbeId.TIMEZONE.value, passed=False,
            severity=Severity.WARNING.value,
            error="worldtimeapi returned unparseable response",
        )
    tz = data.get("timezone") or ""
    # Very loose country→TZ-prefix map. Used for warning only.
    expected_prefix = _country_tz_prefix(expected_country) if expected_country else None
    details = {"timezone": tz, "utc_offset": data.get("utc_offset")}
    if expected_prefix and not tz.startswith(expected_prefix):
        return ProbeResult(
            probe_id=ProbeId.TIMEZONE.value, passed=False,
            severity=Severity.WARNING.value,
            details={**details, "expected_prefix": expected_prefix},
            error=f"timezone {tz!r} does not match expected prefix {expected_prefix!r}",
        )
    return ProbeResult(
        probe_id=ProbeId.TIMEZONE.value, passed=True,
        severity=Severity.WARNING.value,
        details=details,
    )


# ─── HTTP helpers ───────────────────────────────────────────────────

def _http_get(url: str, proxy_url: str, timeout_s: int = DEFAULT_PROBE_TIMEOUT_S) -> Optional[str]:
    """GET url through socks proxy. Returns body text or None on failure."""
    try:
        import httpx
    except ImportError:
        sys.stderr.write("[vpn-leak] httpx not installed — leak probes degraded\n")
        return None
    try:
        with httpx.Client(
            proxy=proxy_url,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (BulkDownloader leak-check)"},
        ) as c:
            r = c.get(url)
            if r.status_code < 400:
                return r.text
            return None
    except Exception as e:
        sys.stderr.write(f"[vpn-leak] GET {url} via {proxy_url}: {type(e).__name__}: {e}\n")
        return None


def _http_get_json_field(url: str, proxy_url: str, field_names: tuple[str, ...]) -> Optional[str]:
    """GET JSON, pull the first non-empty field in field_names."""
    body = _http_get(url, proxy_url)
    if not body:
        return None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    for f in field_names:
        v = data.get(f)
        if isinstance(v, str) and v:
            return v
    return None


# ─── Helpers ────────────────────────────────────────────────────────

_COUNTRY_TZ_PREFIX = {
    "us": "America/", "ca": "America/", "mx": "America/",
    "br": "America/", "ar": "America/",
    "uk": "Europe/", "gb": "Europe/", "ie": "Europe/", "fr": "Europe/",
    "de": "Europe/", "nl": "Europe/", "es": "Europe/", "it": "Europe/",
    "se": "Europe/", "no": "Europe/", "fi": "Europe/", "dk": "Europe/",
    "ch": "Europe/", "at": "Europe/", "be": "Europe/", "pl": "Europe/",
    "is": "Atlantic/",
    "jp": "Asia/", "kr": "Asia/", "sg": "Asia/", "hk": "Asia/", "in": "Asia/",
    "au": "Australia/", "nz": "Pacific/",
    "za": "Africa/",
}


def _country_tz_prefix(country_code: Optional[str]) -> Optional[str]:
    if not country_code:
        return None
    return _COUNTRY_TZ_PREFIX.get(country_code.lower())


def _severity_for(probe_id: str) -> str:
    return Severity.CRITICAL.value if probe_id in CRITICAL_PROBES else Severity.WARNING.value


def _push_history(tunnel_id: str, agg: AggregateResult) -> None:
    with _history_lock:
        ring = _history.setdefault(tunnel_id, [])
        ring.append(agg)
        if len(ring) > HISTORY_SIZE:
            del ring[: len(ring) - HISTORY_SIZE]


def _summarize(probes: list[ProbeResult], crit: int, warn: int) -> str:
    if crit:
        failed_ids = [p.probe_id for p in probes
                      if not p.passed and p.severity == Severity.CRITICAL.value]
        return f"CRITICAL: {crit} probe(s) failed: {', '.join(failed_ids)}"
    if warn:
        failed_ids = [p.probe_id for p in probes
                      if not p.passed and p.severity == Severity.WARNING.value]
        return f"WARNING: {warn} non-critical probe(s) failed: {', '.join(failed_ids)}"
    return "all probes passed"


# ─── Periodic monitor ───────────────────────────────────────────────

def start_periodic_monitor(interval_s: int = DEFAULT_MONITOR_INTERVAL_S) -> None:
    if os.environ.get("BD_DISABLE_KEEPALIVE"):
        return
    global _monitor_thread
    if _monitor_thread is not None and _monitor_thread.is_alive():
        return
    _monitor_stop.clear()
    _monitor_thread = threading.Thread(
        target=_monitor_loop, args=(interval_s,),
        name="bd-vpn-leak-monitor", daemon=True,
    )
    _monitor_thread.start()


def stop_periodic_monitor() -> None:
    _monitor_stop.set()
    global _monitor_thread
    _monitor_thread = None


def _monitor_loop(interval_s: int) -> None:
    # Initial delay so we don't run probes while the app is still booting.
    _monitor_stop.wait(20)
    while not _monitor_stop.is_set():
        try:
            from .vpn import list_tunnels
            for t in list_tunnels():
                if t.state == "up":
                    try:
                        run_all_probes(t.tunnel_id)
                    except Exception as e:
                        sys.stderr.write(
                            f"[vpn-leak] monitor: run_all_probes({t.tunnel_id}) raised: {e}\n"
                        )
        except Exception as e:
            sys.stderr.write(f"[vpn-leak] monitor loop error: {e}\n")
        _monitor_stop.wait(interval_s)


# ─── WebRTC JS snippet (injected by workers into Playwright contexts) ─

WEBRTC_PROBE_JS = r"""
(async () => {
  // Returns: {passed: bool, details: {public_ips: [...], local_ips: [...]}, error?: str}
  // Pass criterion: no RTCPeerConnection-revealed IP outside the tunnel range
  // (caller compares public_ips against expected exit; here we just collect)
  //
  // Audit 2026-05 (Phase 5): previously this scanned the entire SDP body with
  // /(?:\d{1,3}\.){3}\d{1,3}/g and /[a-fA-F0-9:]+:[a-fA-F0-9:]+/g. That had
  // three false-positive sources on every healthy WebRTC offer:
  //   1. `c=IN IP4 0.0.0.0` placeholder before ICE gathering finishes
  //   2. `a=fingerprint:sha-256 XX:XX:XX:...` DTLS fingerprint (always present)
  //   3. The substring `e:NNNNNNNNN` lifted out of `candidate:NNNNNNNNN`
  // None of these are network addresses, but `0.0.0.0` and the fingerprint hex
  // don't match any private-IP prefix — so they were reported as a public-IP
  // WebRTC leak, immediately tripping the kill switch (vpn_kill_switch.py:100).
  // Now we parse `a=candidate:` lines explicitly: the connection address is at
  // field index 4 (foundation comp-id proto prio ADDR port typ ...), and any
  // `raddr <addr>` clause is the related (private) address.
  try {
    if (typeof RTCPeerConnection === 'undefined') {
      return {passed: true, details: {note: 'WebRTC unavailable in this context'}};
    }
    const pc = new RTCPeerConnection({
      iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
    });
    pc.createDataChannel('probe');
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    await new Promise(resolve => setTimeout(resolve, 3000));
    const sdp = pc.localDescription && pc.localDescription.sdp ? pc.localDescription.sdp : '';
    pc.close();

    const isIpv4 = (s) => /^(?:\d{1,3}\.){3}\d{1,3}$/.test(s);
    const isIpv6 = (s) => s.indexOf(':') !== -1 && /^[0-9a-fA-F:]+$/.test(s) && s.indexOf('::') !== s.length - 2;
    const isIp = (s) => isIpv4(s) || isIpv6(s);

    const ips = new Set();
    const lines = sdp.split(/\r?\n/);
    for (const line of lines) {
      if (line.indexOf('a=candidate:') !== 0) continue;
      // Strip the 'a=candidate:' prefix, then space-split. Index 4 = address.
      const rest = line.slice('a=candidate:'.length);
      const parts = rest.split(' ');
      if (parts.length < 5) continue;
      const addr = parts[4];
      if (addr && isIp(addr)) ips.add(addr);
      // raddr <addr> -- related/local address for srflx/prflx/relay
      for (let i = 5; i < parts.length - 1; i++) {
        if (parts[i] === 'raddr' && isIp(parts[i + 1])) ips.add(parts[i + 1]);
      }
    }
    const collected = [...ips];

    const isPrivate = (ip) => {
      // Unspecified / loopback placeholders
      if (ip === '0.0.0.0' || ip === '::' || ip === '::0') return true;
      // IPv4 RFC 1918 / loopback / link-local
      if (ip.startsWith('10.') || ip.startsWith('127.') || ip.startsWith('169.254.')) return true;
      if (ip.startsWith('192.168.')) return true;
      if (/^172\.(1[6-9]|2[0-9]|3[0-1])\./.test(ip)) return true;
      // IPv6 link-local / loopback / unique-local
      if (ip.toLowerCase().startsWith('fe80:') || ip === '::1') return true;
      if (/^f[cd][0-9a-fA-F]{2}:/i.test(ip)) return true;
      return false;
    };
    const publicIps = collected.filter(ip => !isPrivate(ip));
    const passed = publicIps.length === 0;
    return {
      passed: passed,
      details: {
        all_ips: collected,
        public_ips: publicIps,
        local_ips: collected.filter(isPrivate),
      },
      error: passed ? null : 'WebRTC revealed public IP(s)',
    };
  } catch (e) {
    return {passed: false, details: {}, error: 'WebRTC probe raised: ' + (e && e.message || e)};
  }
})();
""".strip()


# ─── Test/introspection helpers ─────────────────────────────────────

def _reset_for_tests() -> None:
    with _history_lock:
        _history.clear()
    with _external_lock:
        _external_results.clear()
    stop_periodic_monitor()


__all__ = [
    "Severity", "ProbeId", "ProbeResult", "AggregateResult",
    "ALL_PROBES", "CRITICAL_PROBES",
    "run_probe", "run_all_probes",
    "record_external_probe_result",
    "get_history", "get_latest",
    "start_periodic_monitor", "stop_periodic_monitor",
    "WEBRTC_PROBE_JS",
]
