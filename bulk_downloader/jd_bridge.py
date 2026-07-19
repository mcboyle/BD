"""v3.43.21: JDownloader 2 bridge.

For sites where JD has working hoster plugins (brazzers, bangbros,
adulttime, xnxx, xempire, evilangel, filthykings, etc.), we forward
the URL to a local JD instance and let JD's protocol-level plugin
handle the actual download. JD's plugins are faster than our
Playwright/teach flow (no browser, ~3-5 HTTP calls per URL) and more
robust against CSS changes (they talk to the site's API, not its DOM).

JD's known weakness: stateless plugins. The plugin authenticates once
per call and doesn't refresh the session mid-job, so long-running
downloads timeout when the site's session expires. BulkDownloader
fixes this — the session keeper (v3.43.16) holds a persistent
Chromium per site/account, refreshes cookies every ~5 min, and the
bridge submits the freshest cookies inline with each JD job.

If JD is unreachable or the plugin returns an auth-class error, the
runner falls through to the teach-based flow without losing the URL.
The system degrades gracefully to current-day behavior when JD is
absent — no hard dependency.

## JD Remote API setup

This bridge uses JD2's local Remote API ("Deprecated API" /
"Direct Connection mode"). In JD2:

    Settings → Advanced Settings → search "Remote API" →
    enable "Deprecated API" → set port to 3128 (default)

After enabling, JD listens at http://127.0.0.1:3128/ for unauthenticated
requests from the same machine. If JD isn't reachable, the bridge's
`diagnose()` method returns a structured error so the UI can show the
user exactly which port was tried and what to enable.

## API surface

JDClient(host, port).
  is_reachable() -> bool                  fast connectivity check
  diagnose() -> dict                      structured status for UI
  submit(url, cookies, dest_dir,
         headers=None, timeout=300)       -> link_id (raises on failure)
  poll(link_id, timeout=10) -> dict       {status, bytes_done, bytes_total,
                                           filename, error}
  cancel(link_id) -> bool

Module-level helpers:
  cookies_playwright_to_jd(cookies_list)  Playwright list → "k=v; k=v" string
  classify_jd_error(error_msg) -> str     "auth" | "plugin_broken" | "network"
                                           | "unknown" — drives the
                                           fallback decision
"""
from __future__ import annotations

import socket
from typing import Iterable

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

# Default JD2 Remote API port. Configurable per-site via cfg["jd_port"].
DEFAULT_PORT = 3128
DEFAULT_HOST = "127.0.0.1"

# JD-3 (v3.66.694): host-coverage probe path. JD2's DEPRECATED Remote API has
# no single cleanly-documented "supported hosts" endpoint -- the surface varies
# across JD builds -- so this is a DOCUMENTED ASSUMPTION, overridable per-site
# via the declared cfg field `jd_supported_hosts_path` (JD-3 @702; a first-class
# GUI-editable per-site field) and VERIFIED LIVE
# on-stash. supported_hosts() degrades to [] (available=False + hint) on a JD
# that lacks it, so a wrong default is never a crash. The namespace targets
# JD's link-crawler plugin-host listing (deprecated-API namespaces observed:
# accounts/content/linkcrawler/plugins/...).
DEFAULT_SUPPORTED_HOSTS_PATH = "/linkcrawler/getSupportedHosts"

# Connection timeouts. JD's plugin chain can be slow on first call
# (some plugins do 3-4 sequential HTTP requests to extract the download
# URL) so the submit timeout is generous. Poll timeout is tight because
# we want UI feedback quickly.
SUBMIT_TIMEOUT_S = 300.0
POLL_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 2.0


class JDError(Exception):
    """Raised for any JD bridge failure. The `kind` attribute classifies
    the failure so the runner can decide whether to refresh cookies,
    fall back to teach, or surface a setup hint to the user.

    Values:
      - "unreachable": JD process not running or wrong port
      - "auth": JD returned 401/403/login-redirect — cookies stale
      - "plugin_broken": JD parsed the URL but its plugin failed
      - "network": transient HTTP error (5xx, timeout)
      - "unknown": JD response didn't match expected shape
    """
    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}


def classify_jd_error(text) -> str:
    """Best-effort classifier for JD error strings. Used by the runner
    to decide between cookie-refresh-and-retry and teach-fallback.

    Non-string input returns 'unknown' — v3.43.27 audit fix; same gap
    as qb_bridge.classify_qb_error."""
    if not isinstance(text, str) or not text:
        return "unknown"
    t = text.lower()
    # Auth-class: stale cookies, expired session, login required.
    # JD plugins return varied wording across sites; cover the common
    # patterns rather than building per-host knowledge here.
    if any(p in t for p in ("401", "403", "login", "auth", "unauthorized",
                              "session", "premium", "account")):
        return "auth"
    # Plugin-class: hoster recognized but the plugin's parsing chain
    # broke. Site changed an HTML format or API response.
    if any(p in t for p in ("plugin", "host", "unsupported", "captcha",
                              "regex", "parse")):
        return "plugin_broken"
    # Network-class: timeouts, 5xx, DNS. Transient — could retry.
    if any(p in t for p in ("timeout", "connection", "dns", "5xx",
                              "503", "504", "reset", "refused")):
        return "network"
    return "unknown"


def cookies_playwright_to_jd(cookies: Iterable[dict]) -> str:
    """Convert Playwright-format cookie list to the flat
    `name=value; name=value` string JD's Click&Load and linkgrabber
    endpoints accept.

    Playwright cookies look like:
        [{"name": "session", "value": "abc", "domain": ".example.com",
          "path": "/", "expires": 1234567890, ...}, ...]

    JD wants just the name=value pairs joined by `; `. Domain/path/
    expiry are ignored because JD applies them to the URL's host
    automatically — the same cookie format you'd paste into a browser's
    Cookie HTTP header.
    """
    if not cookies:
        return ""
    parts = []
    for c in cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name") or ""
        value = c.get("value") or ""
        if not name:
            continue
        # JD's cookie parser doesn't handle URL-encoding internally —
        # values are passed through verbatim. Strip whitespace and
        # newlines that would break the header format.
        value = str(value).replace("\r", "").replace("\n", "")
        parts.append(f"{name}={value}")
    return "; ".join(parts)


class JDClient:
    """JDownloader 2 Remote API client.

    All methods are synchronous (httpx blocking client). The runner
    already runs each URL on its own worker thread, so blocking calls
    here don't starve other workers. Async would only complicate the
    threading model for no actual concurrency win at our scale (max ~10
    concurrent JD jobs in practice).
    """
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host or DEFAULT_HOST
        self.port = int(port or DEFAULT_PORT)
        self._base = f"http://{self.host}:{self.port}"
        # Per-instance HTTP client, reused across calls. JD's Remote API
        # accepts keep-alive so this saves the TCP handshake on repeat
        # submits — usually 50-200ms per call on Windows loopback.
        self._client = None
        if _HAS_HTTPX:
            self._client = httpx.Client(
                base_url=self._base,
                timeout=httpx.Timeout(POLL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ── Connectivity ──────────────────────────────────────────────────

    def is_reachable(self) -> bool:
        """Fast TCP-level check. Returns True if a socket can connect to
        the configured host:port. Does NOT verify the Remote API is
        actually enabled — only that something is listening. Use
        diagnose() for the full status."""
        try:
            with socket.create_connection((self.host, self.port),
                                            timeout=CONNECT_TIMEOUT_S):
                return True
        except (OSError, socket.timeout):
            return False

    def diagnose(self) -> dict:
        """Structured connectivity status for the UI. Returns a dict
        with `reachable`, `api_enabled`, `version`, `error`, and a
        human-readable `hint` for the UI to display when things are
        broken.

        Network ordering matters here: TCP probe first (catches "JD not
        running"), then HTTP probe (catches "JD running but Remote API
        disabled"). The hint tells the user exactly what to fix."""
        result = {
            "reachable": False,
            "api_enabled": False,
            "version": None,
            "host": self.host,
            "port": self.port,
            "error": None,
            "hint": None,
        }
        if not _HAS_HTTPX:
            result["error"] = "httpx not installed"
            result["hint"] = "pip install httpx"
            return result
        if not self.is_reachable():
            result["error"] = f"No TCP connection to {self.host}:{self.port}"
            result["hint"] = ("Make sure JDownloader 2 is running. "
                              "If JD is on a different machine or port, "
                              "update jd_host / jd_port in the site config.")
            return result
        result["reachable"] = True
        # Probe the API. The /jd/version endpoint is the canonical
        # "is the Remote API enabled" check — returns the JD version
        # string when on, 404 or empty body when the API itself is off.
        try:
            r = self._client.get("/jd/version", timeout=CONNECT_TIMEOUT_S)
            if r.status_code == 200:
                result["api_enabled"] = True
                # Response format varies by JD version; usually a JSON
                # number or string. Best-effort parse.
                try:
                    result["version"] = r.json()
                except Exception:
                    result["version"] = (r.text or "").strip()[:32]
            else:
                result["error"] = f"Remote API returned HTTP {r.status_code}"
                result["hint"] = (
                    "JDownloader is running but the Remote API is not "
                    "enabled. In JD: Settings → Advanced Settings → "
                    "search 'Remote API' → enable 'Deprecated API' → "
                    f"set port to {self.port}."
                )
        except Exception as e:
            result["error"] = f"API probe failed: {type(e).__name__}: {e}"
            result["hint"] = (
                "JD is running on this port but rejected the API probe. "
                "Verify the Remote API ('Deprecated API') is enabled in "
                "JD's Advanced Settings."
            )
        return result

    def supported_hosts(self, path: str = None) -> list:
        """JD-3 (v3.66.694): return JD's supported-host list (the hosts JD has a
        hoster plugin for), normalized to a sorted list of lowercased host
        strings. ``path`` overrides ``DEFAULT_SUPPORTED_HOSTS_PATH`` (a
        DOCUMENTED ASSUMPTION -- see the constant). Any error / unreachable /
        non-200 / unparseable body -> ``[]`` (never raises): a JD without this
        endpoint reads as 'coverage unavailable', not a failure. Live-verified
        on-stash."""
        if self._client is None:
            return []
        p = (path or DEFAULT_SUPPORTED_HOSTS_PATH)
        try:
            r = self._client.get(p, timeout=CONNECT_TIMEOUT_S)
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            payload = r.json()
        except Exception:
            return []
        return parse_supported_hosts(payload)

    # ── Submission ────────────────────────────────────────────────────

    def submit(self, url: str, cookies: str = "", dest_dir: str = "",
               headers: dict | None = None,
               timeout: float = SUBMIT_TIMEOUT_S) -> str:
        """Submit a single URL to JD's linkgrabber with the supplied
        cookies. Returns the JD link UUID for subsequent polling.

        JD's linkgrabber API accepts a JSON payload with an `links`
        string containing one or more URLs. Cookies are passed via the
        `properties` field which the plugin reads when authenticating.

        Raises JDError on any failure. The runner uses `error.kind` to
        decide between retry (auth → refresh cookies; network → retry
        same submit) and fallback (plugin_broken/unknown → teach mode).
        """
        if not _HAS_HTTPX:
            raise JDError("unreachable", "httpx not installed",
                          {"hint": "pip install httpx"})
        if not url:
            raise JDError("unknown", "Empty URL")

        # JD's linkgrabber addLinks endpoint. Payload shape per JD's
        # Remote API docs: query.string-of-urls + optional package
        # name, destination folder, autostart flag.
        payload = {
            "links": url,
            "autostart": True,
            "destinationFolder": dest_dir or "",
            "overwritePackagizerEnabled": False,
            "packageName": "BulkDownloader",
            "extractPassword": "",
            "downloadPassword": "",
            "priority": "DEFAULT",
        }
        # Cookies travel as a "properties" entry on the link — the JD
        # plugin reads these BEFORE its first HTTP call, so the
        # authenticated session is used end-to-end.
        if cookies:
            payload["properties"] = {"cookies": cookies}
        if headers:
            payload.setdefault("properties", {})["headers"] = headers

        try:
            r = self._client.post(
                "/linkgrabberv2/addLinks",
                json=payload,
                timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_S),
            )
        except httpx.ConnectError as e:
            raise JDError("unreachable", str(e),
                          {"host": self.host, "port": self.port})
        except httpx.TimeoutException as e:
            raise JDError("network", f"submit timeout: {e}")
        except Exception as e:
            raise JDError("unknown",
                          f"submit failed: {type(e).__name__}: {e}")

        if r.status_code == 401 or r.status_code == 403:
            raise JDError("auth",
                          f"JD rejected request (HTTP {r.status_code})",
                          {"body": (r.text or "")[:500]})
        if r.status_code >= 500:
            raise JDError("network", f"JD HTTP {r.status_code}",
                          {"body": (r.text or "")[:500]})
        if r.status_code != 200:
            raise JDError("unknown",
                          f"unexpected status {r.status_code}",
                          {"body": (r.text or "")[:500]})

        try:
            data = r.json()
        except Exception:
            raise JDError("unknown", "JD returned non-JSON",
                          {"body": (r.text or "")[:200]})

        # JD's response for addLinks is a job-id (numeric) — the link
        # itself gets a UUID once the linkgrabber finishes parsing.
        # The job-id is what we poll on.
        job_id = data.get("data") if isinstance(data, dict) else data
        if job_id is None:
            raise JDError("unknown", "no job id in response",
                          {"body": str(data)[:200]})
        return str(job_id)

    # ── Polling ───────────────────────────────────────────────────────

    def poll(self, link_id: str, timeout: float = POLL_TIMEOUT_S) -> dict:
        """Query the status of a previously submitted link. Returns a
        normalized status dict:

            {
              "status": "running" | "done" | "failed" | "pending",
              "bytes_done": int,
              "bytes_total": int,
              "filename": str,
              "speed": int (bytes/sec),
              "error": str,
            }

        Falsy fields are filled with empty strings or zeros — never
        None — so callers can format unconditionally.
        """
        if not _HAS_HTTPX:
            raise JDError("unreachable", "httpx not installed")
        if not link_id:
            raise JDError("unknown", "empty link_id")

        # The /downloadsV2/queryLinks endpoint gives per-link state.
        # `linkIds` filters to just the one we care about; flags enable
        # the fields we want (bytes done, status, filename).
        payload = {
            "linkIds": [int(link_id)] if str(link_id).isdigit() else [],
            "bytesTotal": True,
            "bytesLoaded": True,
            "speed": True,
            "status": True,
            "running": True,
            "finished": True,
            "name": True,
            "comment": True,
        }
        try:
            r = self._client.post(
                "/downloadsV2/queryLinks",
                json=payload,
                timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_S),
            )
        except httpx.ConnectError as e:
            raise JDError("unreachable", str(e))
        except httpx.TimeoutException:
            raise JDError("network", "poll timeout")
        except Exception as e:
            raise JDError("unknown",
                          f"poll failed: {type(e).__name__}: {e}")

        if r.status_code != 200:
            raise JDError("network", f"JD HTTP {r.status_code}",
                          {"body": (r.text or "")[:200]})

        try:
            data = r.json()
        except Exception:
            raise JDError("unknown", "non-JSON poll response")

        # Response shape: {"data": [{"uuid": ..., "name": ..., ...}, ...]}
        rows = data.get("data") if isinstance(data, dict) else None
        if not rows:
            # Link no longer in JD — could be finished + cleared, or
            # never indexed. Treat as pending; caller will time out
            # eventually if it stays this way.
            return {
                "status": "pending",
                "bytes_done": 0, "bytes_total": 0,
                "filename": "", "speed": 0, "error": "",
            }

        row = rows[0] if isinstance(rows, list) else rows
        # JD's status enum is verbose ("FINISHED", "RUNNING",
        # "OFFLINE", "PLUGIN_DEFECT", etc.). Normalize to our 4-state.
        raw_status = (row.get("status") or "").upper()
        finished = bool(row.get("finished"))
        running = bool(row.get("running"))
        if finished:
            norm = "done"
        elif running or raw_status in ("RUNNING", "DOWNLOADING"):
            norm = "running"
        elif raw_status in ("FAILED", "OFFLINE", "PLUGIN_DEFECT",
                              "FATAL_ERROR", "ABORTED", "ERROR"):
            norm = "failed"
        else:
            norm = "pending"

        # JD sometimes reports a `comment` field with the error reason
        # (e.g. "Plugin defect", "File not found"). Surface that as
        # `error` so the classifier can route the fallback decision.
        error_msg = ""
        if norm == "failed":
            error_msg = (row.get("comment") or row.get("status")
                          or raw_status or "JD reported failure")

        return {
            "status": norm,
            "bytes_done": int(row.get("bytesLoaded") or 0),
            "bytes_total": int(row.get("bytesTotal") or 0),
            "filename": row.get("name") or "",
            "speed": int(row.get("speed") or 0),
            "error": str(error_msg)[:500],
        }

    # ── Cancellation ──────────────────────────────────────────────────

    def cancel(self, link_id: str) -> bool:
        """Remove a link from JD. Best-effort; failures are swallowed
        because cancellation is typically called from cleanup paths
        where there's nothing useful to do with an error."""
        if not _HAS_HTTPX or not link_id:
            return False
        try:
            self._client.post(
                "/downloadsV2/removeLinks",
                json={"linkIds": [int(link_id)] if str(link_id).isdigit() else []},
                timeout=CONNECT_TIMEOUT_S,
            )
            return True
        except Exception:
            return False


# ── Module-level convenience for runner integration ────────────────────

def parse_supported_hosts(payload) -> list:
    """JD-3 (v3.66.694): normalize a JD supported-hosts response to a sorted
    list of lowercased host strings. The deprecated API wraps values as
    ``{"data": <value>}`` (e.g. ``/jd/getCoreRevision -> {"data": 43190}``), so
    unwrap ``data`` first. Accepts a bare list of host strings, a
    ``{"data": [...]}`` wrapper, or a list of dicts carrying a host-ish key
    (``host``/``hostname``/``domain``/``name``). Anything unparseable -> ``[]``.
    """
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if not isinstance(payload, list):
        return []
    hosts = set()
    for item in payload:
        h = ""
        if isinstance(item, str):
            h = item
        elif isinstance(item, dict):
            for k in ("host", "hostname", "domain", "name"):
                v = item.get(k)
                if isinstance(v, str) and v.strip():
                    h = v
                    break
        h = h.strip().lower()
        if h:
            hosts.add(h)
    return sorted(hosts)


def host_coverage(site_host: str, jd_hosts) -> dict:
    """JD-3 (v3.66.694): is ``site_host`` covered by JD's supported-hosts list?
    Matches on the registrable-ish domain: ``www.brazzers.com`` is covered when
    ``brazzers.com`` (or the full host) appears in ``jd_hosts`` (the normalized
    list from ``parse_supported_hosts``). Progressively strips leftmost labels,
    stopping before a bare TLD. Returns ``{host, covered, matched,
    jd_host_count}``; empty inputs are safe (covered=False)."""
    sh = (site_host or "").strip().lower().lstrip(".")
    jset = set(jd_hosts or [])
    covered = False
    matched = ""
    if sh:
        parts = sh.split(".")
        for i in range(len(parts) - 1):     # stop before a bare TLD
            cand = ".".join(parts[i:])
            if cand in jset:
                covered = True
                matched = cand
                break
    return {"host": sh, "covered": covered, "matched": matched,
            "jd_host_count": len(jset)}


def get_client_for_site(cfg: dict) -> JDClient:
    """Construct a JDClient using a site's config. Falls back to
    defaults when fields are missing or invalid (don't error on
    bad config — the diagnose() call will surface the issue cleanly
    instead)."""
    host = (cfg.get("jd_host") or "").strip() or DEFAULT_HOST
    try:
        port = int(cfg.get("jd_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return JDClient(host=host, port=port)


def is_jd_backend(cfg: dict) -> bool:
    """One-liner used at every branch in the runner. Returns True only
    when the site has explicitly opted into JD. Default is teach mode."""
    return (cfg.get("backend") or "teach").strip().lower() == "jd"
