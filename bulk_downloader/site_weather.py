"""Site weather (Phase 154, Block Q).

Tracks "is the target site up?" indicators that complement BD's own
job-success metrics. Useful when failure rate spikes — operator wants
to know if it's the site (everyone's affected) or BD's setup.

Sources:
  • HTTP probe of the homepage — does TCP+TLS+HTTP/200 succeed?
  • DNS resolution — does the hostname resolve?
  • CDN check — does the configured CDN endpoint respond?
  • Recent BD success rate — historical baseline

Schedule: BD's bg_scheduler can run probes every N minutes per site.
Results land in `site_weather_log`. Operator UI shows red/yellow/green
indicators on the dashboard.

Cheap — each probe is a single HTTP HEAD + DNS lookup.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from typing import Optional
from urllib.parse import urlparse, urljoin


def _ensure_table():
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""CREATE TABLE IF NOT EXISTS site_weather_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                site_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                ok INTEGER NOT NULL,
                latency_ms INTEGER,
                status_code INTEGER,
                error TEXT
            )""")
            cx.execute("""CREATE INDEX IF NOT EXISTS idx_weather_site
                          ON site_weather_log(site_id, ts)""")
    except Exception as e:
        sys.stderr.write(f"[site_weather] schema init: {e}\n")


def _record(site_id: str, kind: str, ok: bool,
           latency_ms: Optional[int] = None,
           status_code: Optional[int] = None,
           error: str = ""):
    _ensure_table()
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            cx.execute("""INSERT INTO site_weather_log(
                ts, site_id, kind, ok, latency_ms, status_code, error
            ) VALUES (?,?,?,?,?,?,?)""",
                (time.time(), site_id, kind, 1 if ok else 0,
                 latency_ms, status_code, (error or "")[:300]))
    except Exception:
        pass


# ─── Individual probes ────────────────────────────────────────────────

# sl-dns r2 (cx178 F1): a lookup abandoned at a probe's deadline keeps its daemon thread until the resolver
# returns, so a stalled resolver must not get a fresh thread on every probe. One outstanding lookup per hostname
# (a later probe for that host waits on it) and at most _DNS_MAX_OUTSTANDING hosts at once; past the cap a probe
# fails at once as "dns resolver busy" instead of starting another thread.
_DNS_MAX_OUTSTANDING = 4
# sl-dns r3 (cx206 F1): "busy" is this process's capacity, not the site's DNS. It is stored under its own kind,
# left out of probe_one's overall_ok and of site_status, so a healthy site does not read down while slots are held.
_DNS_BUSY_KIND = "dns_busy"
_dns_lock = threading.Lock()
_dns_inflight: dict = {}  # hostname -> (thread, box) while its lookup runs


class DNSResolverBusy(RuntimeError):
    """Every lookup slot is held by an outstanding lookup: the probed host was not asked."""


def _dns_lookup(hostname: str, box: dict) -> None:
    try:
        box["ip"] = socket.gethostbyname(hostname)
    except Exception as e:  # handed to the caller(s) waiting on box
        box["error"] = e
    finally:
        with _dns_lock:
            entry = _dns_inflight.get(hostname)
            if entry is not None and entry[0] is threading.current_thread():
                del _dns_inflight[hostname]


def _resolve_bounded(hostname: str, timeout: float) -> str:
    """socket.gethostbyname(hostname), bounded by `timeout` seconds.

    sl-dns (O1789 sites-lifecycle row 4): the lookup runs in a daemon thread and the caller waits at most
    `timeout`. The process-global socket.setdefaulttimeout is never touched -- setting it for the lookup gave
    every socket another thread created meanwhile (downloads, runners) the probe's timeout, a finally could
    clobber a concurrent caller's value, and gethostbyname does not honour it anyway. A lookup still running
    at the deadline is reported as a timeout and left to finish; its thread is reused, not repeated (above)."""
    with _dns_lock:
        entry = _dns_inflight.get(hostname)
        if entry is None:
            if len(_dns_inflight) >= _DNS_MAX_OUTSTANDING:
                raise DNSResolverBusy(
                    f"dns resolver busy: {len(_dns_inflight)} lookups still outstanding")
            box: dict = {}
            t = threading.Thread(target=_dns_lookup, args=(hostname, box),
                                 name=f"bd-dns-probe-{hostname}"[:60], daemon=True)
            entry = _dns_inflight[hostname] = (t, box)
            t.start()
    t, box = entry
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"dns lookup timed out after {timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["ip"]


def probe_dns(site_id: str, hostname: str, *,
             timeout: float = 5.0) -> dict:
    """Try to resolve the hostname within `timeout` seconds (global socket default untouched)."""
    started = time.time()
    try:
        ip = _resolve_bounded(hostname, timeout)
        latency = int((time.time() - started) * 1000)
        _record(site_id, "dns", True, latency_ms=latency)
        return {"ok": True, "ip": ip, "latency_ms": latency}
    except DNSResolverBusy as e:  # local capacity: the site's DNS is unknown, not failed
        latency = int((time.time() - started) * 1000)
        _record(site_id, _DNS_BUSY_KIND, False, latency_ms=latency, error=str(e)[:200])
        return {"ok": False, "busy": True, "error": str(e)[:200], "latency_ms": latency}
    except Exception as e:
        latency = int((time.time() - started) * 1000)
        _record(site_id, "dns", False, latency_ms=latency,
                error=str(e)[:200])
        return {"ok": False, "error": str(e)[:200], "latency_ms": latency}


# v3.66.550 (F-COREBD17-02): the config-derived probe URL (weather_url/homepage_url/
# base_url) -- and every redirect hop -- is validated against the single canonical SSRF
# predicate before any outbound request, so a URL (or a 30x redirect from one) pointing
# at loopback/private/link-local/CGNAT space is refused, not fetched. The probe is blind
# (records only status/latency), so this is defense-in-depth; the main amplifier it
# closes is the redirect (an operator-set external URL can be redirected inward). Same
# predicate as runner._is_safe_public_host / tier_probe (fixed for RFC 6598 CGNAT @524).
_WEATHER_UA = "BulkDownloader-Weather/1.0"
_MAX_REDIRECTS = 5


def _host_is_public(url: str) -> tuple:
    """(ok, reason) for the target URL's host via the canonical predicate
    (provider_resolve_impl._common._is_safe_public_host, which rejects loopback/private/
    link-local/reserved/multicast + RFC 6598 CGNAT). Refuses non-http(s) schemes and
    fails closed on a resolution error (the predicate resolves + fails closed itself)."""
    from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False, f"scheme must be http or https (got {p.scheme or 'none'!r})"
    host = p.hostname or ""
    if not host:
        return False, "invalid host"
    return _is_safe_public_host(host)


def probe_http(site_id: str, url: str, *,
              timeout: float = 10.0) -> dict:
    """HEAD request to the URL. Note: many sites block HEAD; we
    fall back to GET if HEAD returns 405.

    The target host and every redirect hop are SSRF-validated before the request
    (F-COREBD17-02); a non-global target is refused with {"ok": False,
    "error": "blocked: ..."} and recorded as a failed http probe.
    """
    try:
        import requests
    except ImportError:
        return {"ok": False, "error": "requests not installed"}
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
    started = time.time()

    def _fetch(method: str):
        """Fetch `url` via `method`, following redirects manually (bounded) and
        re-validating the host at every hop. Raises SSRFBlocked on a non-global
        target or an over-long redirect chain."""
        cur, meth = url, method
        for _ in range(_MAX_REDIRECTS + 1):
            ok, reason = _host_is_public(cur)
            if not ok:
                raise SSRFBlocked(reason)
            kw = {"timeout": timeout, "allow_redirects": False,
                  "headers": {"User-Agent": _WEATHER_UA}}
            if meth == "get":
                kw["stream"] = True
            r = getattr(requests, meth)(cur, **kw)
            loc = r.headers.get("Location") if getattr(r, "headers", None) else None
            if r.status_code in (301, 302, 303, 307, 308) and loc:
                if meth == "get":
                    r.close()
                cur = urljoin(cur, loc)
                if r.status_code == 303:
                    meth = "get"
                continue
            return r
        raise SSRFBlocked("too many redirects")

    try:
        r = _fetch("head")
        if r.status_code == 405:
            r = _fetch("get")
            r.close()
        latency = int((time.time() - started) * 1000)
        ok = r.status_code < 500
        _record(site_id, "http", ok, latency_ms=latency,
                status_code=r.status_code,
                error=(f"status={r.status_code}" if not ok else ""))
        return {"ok": ok, "status_code": r.status_code,
                "latency_ms": latency}
    except SSRFBlocked as e:
        latency = int((time.time() - started) * 1000)
        _record(site_id, "http", False, latency_ms=latency,
                error=f"blocked: {e}"[:300])
        return {"ok": False, "error": f"blocked: {e}"[:200],
                "latency_ms": latency}
    except Exception as e:
        latency = int((time.time() - started) * 1000)
        _record(site_id, "http", False, latency_ms=latency,
                error=str(e)[:200])
        return {"ok": False, "error": str(e)[:200],
                "latency_ms": latency}


def probe_one(site_id: str, site_cfg: dict) -> dict:
    """Run all configured probes for one site."""
    out: dict = {"site_id": site_id, "ts": time.time(), "probes": {}}
    # Find a representative URL
    probe_url = (site_cfg.get("weather_url") or
                 site_cfg.get("homepage_url") or
                 site_cfg.get("base_url") or "")
    if not probe_url:
        return {**out, "skipped": "no probe_url configured"}
    hostname = urlparse(probe_url).hostname or ""
    if hostname:
        out["probes"]["dns"] = probe_dns(site_id, hostname)
    out["probes"]["http"] = probe_http(site_id, probe_url)
    # Roll up
    out["overall_ok"] = all(p.get("ok") for p in out["probes"].values() if not p.get("busy"))
    return out


def probe_all(s_cfg: Optional[dict] = None) -> list:
    """Probe every site that has weather config."""
    if not s_cfg:
        return []
    results = []
    for sid, cfg in s_cfg.items():
        if not cfg or not (cfg.get("weather_url") or
                            cfg.get("homepage_url") or
                            cfg.get("base_url")):
            continue
        try:
            results.append(probe_one(sid, cfg))
        except Exception as e:
            results.append({"site_id": sid, "error": str(e)[:200]})
    return results


# ─── Reporting ────────────────────────────────────────────────────────

def site_status(site_id: str, *,
               lookback_minutes: int = 60) -> dict:
    """Roll up recent probe results for one site.

    Returns {site_id, color, probes_ok, probes_total,
             recent_status_codes, success_rate}."""
    _ensure_table()
    cutoff = time.time() - lookback_minutes * 60
    try:
        from . import db as _db
        with _db.db_conn() as cx:
            rows = cx.execute("""SELECT ok, status_code, latency_ms
                                  FROM site_weather_log
                                  WHERE site_id = ? AND ts >= ? AND kind != ?
                                  ORDER BY ts DESC""",
                              (site_id, cutoff, _DNS_BUSY_KIND)).fetchall()
    except Exception:
        return {"site_id": site_id, "color": "gray", "error": "DB"}
    if not rows:
        return {"site_id": site_id, "color": "gray",
                "probes_total": 0,
                "message": "no probes recorded yet"}
    total = len(rows)
    ok_count = sum(1 for r in rows if (r[0] if not hasattr(r, "keys") else r["ok"]))
    rate = 100 * ok_count / total
    if rate >= 95:
        color = "green"
    elif rate >= 50:
        color = "yellow"
    else:
        color = "red"
    return {
        "site_id": site_id,
        "color": color,
        "probes_ok": ok_count,
        "probes_total": total,
        "success_rate": round(rate, 1),
        "lookback_minutes": lookback_minutes,
    }


def all_statuses(s_cfg: Optional[dict] = None) -> list:
    """One status row per site."""
    if not s_cfg:
        return []
    return [site_status(sid) for sid in s_cfg.keys()]
