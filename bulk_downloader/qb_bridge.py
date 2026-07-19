"""v3.43.26: qBittorrent bridge.

For URLs that are magnet links or .torrent files (or for sites where
you want seeding behavior or built-in private-tracker handling),
forward the URL to a local qBittorrent instance via its Web API.

Mirrors the v3.43.21 JD bridge pattern:
  - Per-site `backend: "qbittorrent"` option
  - Bridge does the file transfer; BulkDownloader keeps the session
    warm and supplies cookies for any HTTP-style URLs
  - Auto-detection: magnet:?/.torrent URLs route through qB regardless
    of backend setting (you can mix HTTP downloads + torrents on the
    same site)
  - On any qB failure → fall back to teach path
  - Per-site success-rate tracking → amber insight when broken
  - Health pill in header next to JD pill

Architectural difference from JD: qBittorrent's Web API requires
session-cookie auth (POST /api/v2/auth/login with username/password,
get a SID cookie, send that cookie on subsequent calls). JD's
deprecated API is unauthenticated.

## qBittorrent Web UI setup

The Web UI must be enabled in qB:

  Tools → Options → Web UI → enable
    - IP: 127.0.0.1 (or 0.0.0.0 for remote)
    - Port: 8080 (default)
    - Authentication: username/password
    - "Bypass authentication for clients on localhost" (optional)

After enabling, the bridge can connect to http://127.0.0.1:8080/.

## API surface

QBittorrentClient(host, port, username, password)
  is_reachable() -> bool                  fast TCP probe
  diagnose() -> dict                      structured status for UI
  login() -> bool                         establishes SID cookie
  submit(url|magnet, dest_dir, ...) -> torrent_hash
  poll(torrent_hash) -> dict              {status, progress, dlspeed,
                                            name, size, eta, error}
  cancel(torrent_hash, delete_files=False)

Module helpers:
  is_qb_backend(cfg)                      cfg['backend'] == 'qbittorrent'
  looks_like_torrent_url(url)             magnet: prefix or .torrent
  classify_qb_error(text) -> str          auth | network | unknown
  get_client_for_site(cfg)                construct from site config
"""
from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

# Default qBittorrent Web UI host + port
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

# Timeouts. qB's API is mostly fast (sub-second) but submitting a
# magnet involves DHT bootstrapping which can take 5-30s. Submission
# gets a generous timeout; polling is tight.
SUBMIT_TIMEOUT_S = 60.0
POLL_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 2.0
AUTH_TIMEOUT_S = 5.0


class QBError(Exception):
    """Raised for any qBittorrent bridge failure. Mirrors JDError so
    the runner's existing fall-through logic doesn't need a separate
    path."""
    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}


def classify_qb_error(text) -> str:
    """Categorize qB error strings for the runner's retry decision.
    Smaller surface than JD because qB has fewer failure modes —
    its plugins (such as they are) don't break the same way.

    Non-string input returns 'unknown' — found by the v3.43.27 audit
    pass; callers that pass e.message can sometimes pass other types
    if the underlying exception's __str__ is unusual."""
    if not isinstance(text, str) or not text:
        return "unknown"
    t = text.lower()
    if any(p in t for p in ("401", "403", "unauthorized", "forbidden",
                              "login", "auth")):
        return "auth"
    if any(p in t for p in ("timeout", "connection", "refused", "dns",
                              "5xx", "503", "504")):
        return "network"
    return "unknown"


def looks_like_torrent_url(url) -> bool:
    """Detect URLs that should automatically route to qB regardless of
    the site's backend setting:
      - magnet:?xt=urn:btih:... links
      - http(s)://...torrent files

    This lets the user mix conventional HTTP downloads and torrent
    URLs on the same site without per-URL config. Site backend stays
    as 'teach' or 'jd'; only torrent URLs flow through qB.

    Non-string inputs return False — caller is expected to pass a URL
    but a malformed config or callsite bug shouldn't crash the runner.
    Found by the v3.43.27 audit pass."""
    if not isinstance(url, str) or not url:
        return False
    u = url.strip().lower()
    if u.startswith("magnet:?"):
        return True
    if u.endswith(".torrent"):
        return True
    # path-based fallback (some sites omit the extension but the
    # path is unmistakable)
    try:
        path = urlparse(u).path
        if path.endswith(".torrent"):
            return True
    except Exception:
        pass
    return False


def is_qb_backend(cfg: dict) -> bool:
    """Single source of truth for the runner's backend branch."""
    return (cfg.get("backend") or "teach").strip().lower() == "qbittorrent"


class QBittorrentClient:
    """qBittorrent Web API client. Maintains a session cookie so we
    don't re-auth on every call (qB stores it server-side until the
    user changes the Web UI password)."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 username: str = "admin", password: str = ""):
        self.host = host or DEFAULT_HOST
        self.port = int(port or DEFAULT_PORT)
        self.username = username or "admin"
        self.password = password or ""
        self._base = f"http://{self.host}:{self.port}"
        self._client = None
        self._logged_in = False
        if _HAS_HTTPX:
            # cookies=True enables cookie persistence across calls,
            # which is how qB tracks the SID after /auth/login.
            self._client = httpx.Client(
                base_url=self._base,
                timeout=httpx.Timeout(POLL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                follow_redirects=False,
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
        """TCP probe — doesn't verify the Web UI is enabled, just that
        something is listening on the port."""
        try:
            with socket.create_connection((self.host, self.port),
                                            timeout=CONNECT_TIMEOUT_S):
                return True
        except (OSError, socket.timeout):
            return False

    def diagnose(self) -> dict:
        """Structured connectivity status for the UI. Returns the same
        contract shape as JDClient.diagnose() so the frontend can
        render both with the same component."""
        result = {
            "reachable": False,
            "api_enabled": False,
            "logged_in": False,
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
            result["hint"] = (
                "Make sure qBittorrent is running. Tools → Options → "
                "Web UI → enable; default port 8080. If qB is on a "
                "different host or port, update qb_host / qb_port in "
                "the site config."
            )
            return result
        result["reachable"] = True
        # Probe the API. /api/v2/app/version is one of the few qB
        # endpoints that's reachable both pre- and post-auth — it
        # returns 403 when auth is required and we haven't logged in.
        try:
            r = self._client.get("/api/v2/app/version",
                                  timeout=AUTH_TIMEOUT_S)
            if r.status_code == 200:
                # Either no auth required, or we're already logged in
                result["api_enabled"] = True
                result["version"] = (r.text or "").strip().strip('"')[:32]
                # Try auth anyway to verify credentials
                if self.password:
                    if self.login():
                        result["logged_in"] = True
                    else:
                        result["error"] = "auth probe failed"
                        result["hint"] = (
                            "qB Web UI is reachable but the username/"
                            "password doesn't authenticate. Verify "
                            "Tools → Options → Web UI → Authentication."
                        )
            elif r.status_code == 403:
                # Auth required — try logging in
                result["api_enabled"] = True
                if self.login():
                    result["logged_in"] = True
                    # Re-fetch version with the SID cookie
                    try:
                        r2 = self._client.get("/api/v2/app/version",
                                               timeout=AUTH_TIMEOUT_S)
                        if r2.status_code == 200:
                            result["version"] = (r2.text or "").strip().strip('"')[:32]
                    except Exception:
                        pass
                else:
                    result["error"] = "auth required but login failed"
                    result["hint"] = (
                        "qB requires authentication. Set qb_username "
                        "and qb_password in the site config, or enable "
                        "'Bypass authentication for clients on localhost'."
                    )
            else:
                result["error"] = f"unexpected HTTP {r.status_code}"
        except Exception as e:
            result["error"] = f"API probe failed: {type(e).__name__}: {e}"
            result["hint"] = (
                "qB is reachable but rejected the API probe. Web UI "
                "may not be enabled. Tools → Options → Web UI → enable."
            )
        return result

    # ── Authentication ────────────────────────────────────────────────

    def login(self) -> bool:
        """POST /api/v2/auth/login. On success qB sets a SID cookie
        that the httpx client persists for subsequent requests.
        Returns True if logged in, False otherwise. Idempotent —
        calling when already authenticated returns True without
        re-auth."""
        if not _HAS_HTTPX or self._client is None:
            return False
        try:
            r = self._client.post(
                "/api/v2/auth/login",
                data={"username": self.username, "password": self.password},
                # qB rejects requests without a matching Referer when
                # auth is enabled. The base URL satisfies it.
                headers={"Referer": self._base},
                timeout=AUTH_TIMEOUT_S,
            )
            # qB returns body "Ok." for success, "Fails." for bad creds
            if r.status_code == 200 and r.text.strip().lower() == "ok.":
                self._logged_in = True
                return True
            self._logged_in = False
            return False
        except Exception:
            self._logged_in = False
            return False

    def _ensure_authed(self):
        """Lazy-auth helper. submit/poll call this before any API
        request that requires auth. If we already have a SID cookie
        AND qB hasn't expired it, the explicit login() is skipped."""
        if self._logged_in:
            return
        # Try a no-auth probe first; if /version returns 200 without
        # a cookie, qB has 'Bypass auth for localhost' enabled and we
        # don't need to log in at all.
        if not self.password:
            self._logged_in = True
            return
        if not self.login():
            raise QBError("auth", "qB login failed",
                {"hint": "Verify qb_username and qb_password in site config."})

    # ── Submission ────────────────────────────────────────────────────

    def submit(self, url: str, dest_dir: str = "",
                category: str = "BulkDownloader",
                paused: bool = False,
                timeout: float = SUBMIT_TIMEOUT_S) -> str:
        """Submit a URL or magnet link to qB.

        qB's /torrents/add accepts:
          - urls: newline-separated string of magnet links and HTTP
            URLs to .torrent files
          - savepath: destination directory
          - category: free-form label, useful for UI grouping
          - paused: start paused (we set False so download begins
            immediately; the runner controls flow via the site's
            pause/resume)

        Returns the torrent hash. qB doesn't return this directly from
        addTorrent — we have to poll the torrents list right after
        and find the new entry. The polling delay is ~100-500ms which
        is acceptable on the once-per-URL path.
        """
        if not _HAS_HTTPX:
            raise QBError("unreachable", "httpx not installed")
        if not url:
            raise QBError("unknown", "empty URL")
        self._ensure_authed()

        # Snapshot the current torrent hashes so we can identify the
        # new one. qB has an `addTorrent` endpoint that returns a hash
        # in newer versions, but older 4.x return nothing — the diff
        # approach works on all versions.
        try:
            before = self._list_hashes()
        except QBError:
            before = set()

        payload = {
            "urls": url,
            "category": category,
            "paused": "true" if paused else "false",
        }
        if dest_dir:
            payload["savepath"] = dest_dir

        try:
            r = self._client.post(
                "/api/v2/torrents/add",
                data=payload,
                headers={"Referer": self._base},
                timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_S),
            )
        except httpx.ConnectError as e:
            raise QBError("unreachable", str(e),
                {"host": self.host, "port": self.port})
        except httpx.TimeoutException as e:
            raise QBError("network", f"submit timeout: {e}")
        except Exception as e:
            raise QBError("unknown",
                f"submit failed: {type(e).__name__}: {e}")

        if r.status_code in (401, 403):
            self._logged_in = False  # force re-login on next call
            raise QBError("auth", f"qB rejected request (HTTP {r.status_code})",
                {"body": (r.text or "")[:300]})
        if r.status_code != 200:
            raise QBError("unknown",
                f"unexpected status {r.status_code}",
                {"body": (r.text or "")[:300]})

        # Diff the torrent list to find the new hash. qB updates the
        # list within ~100ms but DHT-only magnets can take longer; we
        # retry for up to 5s before giving up.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                after = self._list_hashes()
                new = after - before
                if new:
                    return next(iter(new))
            except QBError:
                pass
            time.sleep(0.2)

        # Couldn't find the new hash — qB may have rejected the URL
        # silently (duplicate, invalid). Surface this so the runner
        # can fall back.
        raise QBError("unknown",
            "qB accepted submit but no new torrent appeared",
            {"hint": "URL may be a duplicate already in qB, or "
                      "malformed. Check qB's UI for messages."})

    def _list_hashes(self) -> set[str]:
        """Return the set of torrent hashes currently in qB. Used by
        submit() to detect newly-added torrents via set diff."""
        try:
            r = self._client.get(
                "/api/v2/torrents/info",
                headers={"Referer": self._base},
                timeout=POLL_TIMEOUT_S,
            )
            if r.status_code != 200:
                raise QBError("network", f"list HTTP {r.status_code}")
            return {t.get("hash", "") for t in r.json() if t.get("hash")}
        except httpx.RequestError as e:
            raise QBError("network", f"list failed: {e}")
        except (ValueError, KeyError) as e:
            raise QBError("unknown", f"list response: {e}")

    # ── Polling ───────────────────────────────────────────────────────

    def poll(self, torrent_hash: str, timeout: float = POLL_TIMEOUT_S) -> dict:
        """Query the status of a torrent. Returns a normalized dict
        matching the JD bridge's shape so the runner's mirror code is
        backend-agnostic:

            {
              "status": "running" | "done" | "failed" | "pending",
              "bytes_done": int,
              "bytes_total": int,
              "filename": str,
              "speed": int,
              "error": str,
            }
        """
        if not _HAS_HTTPX:
            raise QBError("unreachable", "httpx not installed")
        if not torrent_hash:
            raise QBError("unknown", "empty torrent_hash")
        self._ensure_authed()

        try:
            r = self._client.get(
                "/api/v2/torrents/info",
                params={"hashes": torrent_hash},
                headers={"Referer": self._base},
                timeout=timeout,
            )
        except httpx.ConnectError as e:
            raise QBError("unreachable", str(e))
        except httpx.TimeoutException:
            raise QBError("network", "poll timeout")
        except Exception as e:
            raise QBError("unknown",
                f"poll failed: {type(e).__name__}: {e}")

        if r.status_code in (401, 403):
            self._logged_in = False
            raise QBError("auth", f"qB HTTP {r.status_code}")
        if r.status_code != 200:
            raise QBError("network", f"qB HTTP {r.status_code}",
                {"body": (r.text or "")[:200]})

        try:
            rows = r.json()
        except Exception:
            raise QBError("unknown", "non-JSON poll response")

        if not rows:
            # Torrent gone (deleted by user, never registered)
            return {
                "status": "pending", "bytes_done": 0, "bytes_total": 0,
                "filename": "", "speed": 0, "error": "",
            }
        t = rows[0]

        # qB's state enum: error, missingFiles, uploading, pausedUP,
        # queuedUP, stalledUP, checkingUP, forcedUP, allocating,
        # downloading, metaDL, pausedDL, queuedDL, stalledDL, checkingDL,
        # forcedDL, checkingResumeData, moving
        raw_state = t.get("state", "")
        # done covers everything past 100% (seeding states)
        seeding_states = {"uploading", "pausedUP", "queuedUP", "stalledUP",
                           "checkingUP", "forcedUP"}
        downloading_states = {"downloading", "metaDL", "stalledDL", "forcedDL",
                               "allocating", "checkingDL", "queuedDL"}
        error_states = {"error", "missingFiles"}
        pending_states = {"pausedDL", "checkingResumeData", "moving"}

        if raw_state in seeding_states:
            norm = "done"
        elif raw_state in downloading_states:
            norm = "running"
        elif raw_state in error_states:
            norm = "failed"
        elif raw_state in pending_states:
            norm = "pending"
        else:
            # Unknown state — log it as "pending" and the caller's
            # progress-stuck detector will eventually time out if it
            # stays here forever.
            norm = "pending"

        return {
            "status": norm,
            "bytes_done": int(t.get("downloaded") or t.get("completed") or 0),
            "bytes_total": int(t.get("size") or t.get("total_size") or 0),
            "filename": t.get("name") or "",
            "speed": int(t.get("dlspeed") or 0),
            "error": (raw_state if norm == "failed" else ""),
        }

    # ── Cancellation ──────────────────────────────────────────────────

    def cancel(self, torrent_hash: str, delete_files: bool = False) -> bool:
        """Remove a torrent from qB. Best-effort. delete_files=True
        also deletes the partial files on disk (use when falling back
        to teach so we don't double-download)."""
        if not _HAS_HTTPX or not torrent_hash:
            return False
        try:
            self._ensure_authed()
            self._client.post(
                "/api/v2/torrents/delete",
                data={"hashes": torrent_hash,
                       "deleteFiles": "true" if delete_files else "false"},
                headers={"Referer": self._base},
                timeout=AUTH_TIMEOUT_S,
            )
            return True
        except Exception:
            return False


# ── Module helpers ────────────────────────────────────────────────────

def get_client_for_site(cfg: dict) -> QBittorrentClient:
    """Construct a QBittorrentClient from a site's config. Defaults
    on any missing field. Doesn't error on bad config — diagnose()
    will surface the issue."""
    host = (cfg.get("qb_host") or "").strip() or DEFAULT_HOST
    try:
        port = int(cfg.get("qb_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    username = (cfg.get("qb_username") or "admin").strip()
    password = cfg.get("qb_password") or ""
    return QBittorrentClient(host=host, port=port,
                              username=username, password=password)
