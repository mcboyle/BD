"""v3.43.29: deep Plex integration.

Builds on the v3.20-era `hooks.plex_refresh` (which just triggers a
library refresh and returns) with workflows that move the needle for
users with a populated Plex library:

  1. Path-scoped refresh: refresh only the directory containing the
     new file, not the whole library. Order-of-magnitude faster on
     a library with 10k+ items.

  2. Match confirmation: after the refresh, verify Plex actually
     matched the new file to metadata vs leaving it "unmatched".
     Surface unmatched files in the BulkDownloader UI so the user
     can fix matching in Plex without having to spelunk for it.

  3. Recently-added boost: optionally re-stamp the file's addedAt
     timestamp to "now" so Plex's Recently Added widget shows the
     freshly-downloaded item at the top, not buried by scan order.

  4. Collection routing: auto-add the new item to a per-site Plex
     collection. The user can then browse all wowgirls or all
     ultrafilms downloads as a single library view.

## Plex API surface

Plex uses an HTTP API with XML responses (not JSON). Auth via
X-Plex-Token, sent as URL parameter (works everywhere) or header.

Endpoints we use:
  GET  /                                      — server identity (version)
  GET  /library/sections                      — list libraries
  GET  /library/sections/<id>/refresh?path=…  — refresh one path
  GET  /library/sections/<id>/all             — items in a library
  GET  /library/metadata/<ratingKey>          — one item's metadata
  PUT  /library/metadata/<ratingKey>          — update item fields
  GET  /library/collections                   — list collections
  POST /library/collections                   — create collection
  PUT  /library/collections/<id>/items        — add items to collection

## Why XML, not JSON

Plex has an `Accept: application/json` mode that returns JSON, but
it's spotty: some endpoints honor it (library/metadata) and some
don't (library/sections still returns XML). We use XML throughout
for consistency — Python's xml.etree.ElementTree handles it fine.

## Configuration

Same as v3.20 (plex_url, plex_token, plex_section_id) but adds:

  plex_deep_enabled (bool)         — turn deep features on/off
  plex_match_confirm (bool)        — check unmatched status after refresh
  plex_recently_added_boost (bool) — restamp addedAt to bump in UI
  plex_collection (str, optional)  — collection name to add new items to
  plex_auto_create_collection (bool) — create collection if missing

The basic refresh trigger is unaffected by these flags.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any


# Plex's HTTP API is fast on LAN (<100ms typical) but the scan-and-match
# pipeline takes seconds. Match confirmation polls for up to 30s.
QUERY_TIMEOUT_S = 15.0
MATCH_POLL_TIMEOUT_S = 30.0


class PlexError(Exception):
    """Mirrors StashError/QBError/JDError: structured kind so callers
    can route the response uniformly (auth → re-prompt, network →
    retry, unknown → fall through silently)."""
    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}


class PlexClient:
    """HTTP client for Plex's API. Keeps token in URL parameter form
    so the same query string works from any context (curl, browser).

    All read methods return None on failure. All write methods raise
    PlexError so the caller can surface the reason. Same convention
    as stash_deep.StashClient."""

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""

    @property
    def configured(self) -> bool:
        """True when we have enough info to make a call. Plex requires
        BOTH a URL and a token (unlike Stash, where API key is optional
        for some endpoints)."""
        return bool(self.base_url and self.token)

    def _url(self, path: str, params: dict | None = None) -> str:
        """Compose a Plex URL with auth token appended. Plex accepts
        the token as a query param on every endpoint, which sidesteps
        the X-Plex-Token header handling on proxies that strip
        unknown headers."""
        if not self.base_url:
            return ""
        params = dict(params or {})
        params["X-Plex-Token"] = self.token
        query = urllib.parse.urlencode(params)
        sep = "&" if "?" in path else "?"
        return f"{self.base_url}{path}{sep}{query}"

    def _request(self, method: str, path: str,
                  params: dict | None = None,
                  body: bytes | None = None,
                  timeout: float = QUERY_TIMEOUT_S,
                  parse_xml: bool = True) -> Any:
        """Execute a Plex API request. Returns parsed XML root (or raw
        body when parse_xml=False). Raises PlexError on any failure
        (HTTP, network, malformed XML)."""
        if not self.configured:
            raise PlexError("config", "Plex URL or token not set")
        url = self._url(path, params)
        try:
            req = urllib.request.Request(url, data=body, method=method)
            # Plex returns XML by default. Setting Accept just to be
            # explicit (some reverse proxies sniff content negotiation).
            req.add_header("Accept", "application/xml")
            if body is not None:
                req.add_header("Content-Type",
                                "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise PlexError("auth",
                    f"Plex rejected token (HTTP {e.code})",
                    {"hint": "Verify the Plex Token. Get a fresh one from "
                              "an authenticated browser session via "
                              "<https://www.plexopedia.com/plex-media-server/general/plex-token/>"})
            raise PlexError("network", f"HTTP {e.code}")
        except urllib.error.URLError as e:
            raise PlexError("network",
                f"connection failed: {e.reason}",
                {"hint": "Verify Plex is running and the URL is reachable."})
        except Exception as e:
            raise PlexError("unknown", f"{type(e).__name__}: {e}")
        if not parse_xml:
            return raw
        try:
            return ET.fromstring(raw)
        except ET.ParseError:
            # Some endpoints (refresh, PUT) return empty body on success.
            # An empty body that we tried to parse is a soft success;
            # return an empty element so callers don't crash.
            return ET.Element("MediaContainer")

    # ── Connectivity ──────────────────────────────────────────────────

    def diagnose(self) -> dict:
        """Connectivity + auth probe. Returns the same shape as
        StashClient.diagnose() so the UI can render with the same
        component."""
        result = {
            "ok": False,
            "reachable": False,
            "version": None,
            "url": self.base_url or "(not configured)",
            "error": None,
            "hint": None,
        }
        if not self.configured:
            if not self.base_url:
                result["error"] = "plex_url not configured"
                result["hint"] = "Set plex_url in the site config."
            else:
                result["error"] = "plex_token not configured"
                result["hint"] = "Set plex_token in the site config."
            return result
        try:
            root = self._request("GET", "/identity", timeout=5.0)
            # /identity returns <MediaContainer machineIdentifier=… version=… />
            result["reachable"] = True
            result["ok"] = True
            result["version"] = root.get("version")
        except PlexError as e:
            if e.kind == "network":
                result["error"] = e.message
                result["hint"] = e.detail.get("hint") or (
                    "Plex not reachable. Check the URL and that Plex "
                    "Media Server is running."
                )
            elif e.kind == "auth":
                result["reachable"] = True  # got HTTP back
                result["error"] = e.message
                result["hint"] = e.detail.get("hint")
            else:
                result["error"] = e.message
        return result

    # ── Library / section discovery ───────────────────────────────────

    def list_sections(self) -> list[dict]:
        """Enumerate the user's libraries. Returns a list of
        {key, title, type, locations} dicts. Used by the UI to
        let the user pick a section ID from a dropdown."""
        try:
            root = self._request("GET", "/library/sections")
        except PlexError:
            return []
        out = []
        for d in root.findall("Directory"):
            locs = [loc.get("path") for loc in d.findall("Location")
                    if loc.get("path")]
            out.append({
                "key": d.get("key"),
                "title": d.get("title"),
                "type": d.get("type"),  # movie, show, music, photo
                "locations": locs,
            })
        return out

    # ── Path-scoped refresh ───────────────────────────────────────────

    def refresh_path(self, section_id: str, file_path: str) -> bool:
        """Refresh just the directory containing `file_path`, not the
        whole library. Plex's `path` parameter accepts a directory and
        scans only that subtree — orders of magnitude faster than the
        default full library refresh on large libraries.

        Returns True if Plex accepted the request (HTTP 200/204).
        Doesn't wait for completion — caller polls find_by_path() for
        actual results."""
        if not section_id or not file_path:
            return False
        # Plex wants a directory path, not the file itself
        from pathlib import Path
        dir_path = str(Path(file_path).parent)
        try:
            self._request("GET", f"/library/sections/{section_id}/refresh",
                           params={"path": dir_path})
            return True
        except PlexError:
            return False

    # ── Match confirmation ────────────────────────────────────────────

    def find_by_path(self, section_id: str, file_path: str,
                      timeout_s: float = MATCH_POLL_TIMEOUT_S) -> dict | None:
        """Locate the metadata item Plex created for `file_path`.
        Polls for up to `timeout_s` because the refresh is async.

        Returns the item dict (with ratingKey, title, addedAt, etc.)
        or None if not found in time.

        Plex doesn't have a direct "find by file path" query like
        Stash's find_scene_by_path. We list the section's items and
        filter client-side. This is slow on huge libraries (10k items
        = ~2s LAN), but it's only called once per download."""
        if not section_id or not file_path:
            return None
        deadline = time.time() + timeout_s
        from pathlib import Path
        target = str(Path(file_path)).replace("\\", "/")
        while time.time() < deadline:
            try:
                # Pull recently-added first — much faster than the
                # full /all listing, and the file we want WAS just
                # added (or refreshed).
                root = self._request("GET",
                    f"/library/sections/{section_id}/recentlyAdded",
                    params={"X-Plex-Container-Start": "0",
                             "X-Plex-Container-Size": "50"})
                for v in root.findall(".//Video"):
                    for media in v.findall("Media"):
                        for part in media.findall("Part"):
                            part_file = (part.get("file") or "").replace("\\", "/")
                            if part_file == target or part_file.endswith(target):
                                return {
                                    "ratingKey": v.get("ratingKey"),
                                    "title": v.get("title"),
                                    "summary": v.get("summary"),
                                    "addedAt": v.get("addedAt"),
                                    "unmatched": (v.get("title") ==
                                                   Path(file_path).stem),
                                    "type": v.get("type"),
                                }
            except PlexError:
                pass
            time.sleep(2.0)
        return None

    # ── Recently-added boost ──────────────────────────────────────────

    def update_added_at(self, rating_key: str,
                         section_id: str = "") -> bool:
        """Bump the item's addedAt to NOW so Plex's "Recently Added"
        widget displays it at the top. Useful when downloading a
        backlog of older content — without this boost, the items
        would land in chronological position based on the original
        upload date and never surface in Recently Added.

        Plex's API requires the section ID to update item metadata.
        Returns True on success."""
        if not rating_key:
            return False
        now_unix = int(time.time())
        params = {
            "id": rating_key,
            "addedAt.value": str(now_unix),
            "addedAt.locked": "1",  # prevent Plex re-deriving from file mtime
            "type": "1",  # movie type (works for adult content too)
        }
        if section_id:
            try:
                self._request("PUT",
                    f"/library/sections/{section_id}/all",
                    params=params)
                return True
            except PlexError:
                return False
        # Without section_id, the per-item update endpoint:
        try:
            self._request("PUT",
                f"/library/metadata/{rating_key}",
                params=params)
            return True
        except PlexError:
            return False

    # ── Collection management ────────────────────────────────────────

    def find_collection_by_title(self, section_id: str,
                                    title: str) -> dict | None:
        """Look up a collection by exact title within a section."""
        if not section_id or not title:
            return None
        try:
            root = self._request("GET", "/library/collections",
                params={"sectionId": section_id})
        except PlexError:
            return None
        for d in root.findall("Directory"):
            if d.get("title") == title:
                return {"ratingKey": d.get("ratingKey"),
                         "title": d.get("title")}
        return None

    def create_collection(self, section_id: str, title: str,
                           item_rating_key: str = "") -> dict | None:
        """Create a new collection. Plex requires at least one item
        on creation — usually we pass the freshly-imported scene.
        Returns the collection dict or None on failure.

        If `item_rating_key` is empty, the create attempt is skipped
        (Plex would 400)."""
        if not section_id or not title or not item_rating_key:
            return None
        params = {
            "type": "1",
            "title": title,
            "smart": "0",
            "sectionId": section_id,
            "uri": f"server://{section_id}/library/metadata/{item_rating_key}",
        }
        try:
            root = self._request("POST", "/library/collections",
                                  params=params)
            # Response is <MediaContainer><Directory .../></MediaContainer>
            d = root.find("Directory")
            if d is not None:
                return {"ratingKey": d.get("ratingKey"),
                         "title": d.get("title")}
        except PlexError:
            return None
        return None

    def add_to_collection(self, collection_rating_key: str,
                            section_id: str,
                            item_rating_key: str) -> bool:
        """Add an item to an existing collection. Idempotent — Plex
        silently no-ops if the item is already in the collection."""
        if not all((collection_rating_key, section_id, item_rating_key)):
            return False
        params = {
            "uri": f"server://{section_id}/library/metadata/{item_rating_key}",
        }
        try:
            self._request("PUT",
                f"/library/collections/{collection_rating_key}/items",
                params=params)
            return True
        except PlexError:
            return False


# ── Module helpers ────────────────────────────────────────────────────

def get_client_for_site(cfg: dict) -> PlexClient:
    """Construct a PlexClient from a site config. Returns an
    unconfigured client when fields are missing."""
    return PlexClient(
        base_url=cfg.get("plex_url") or "",
        token=cfg.get("plex_token") or "",
    )


def deep_enabled(cfg: dict) -> bool:
    """True when the site has opted into deep Plex features. Requires
    BOTH the URL+token AND the explicit flag — sites with just the
    basic v3.20 refresh trigger are unaffected."""
    if not (cfg.get("plex_url") and cfg.get("plex_token")):
        return False
    return bool(cfg.get("plex_deep_enabled"))


def build_open_in_plex_url(plex_base_url: str, rating_key: str,
                             server_machine_id: str = "") -> str:
    """Construct a deep-link URL that opens an item in Plex's web UI.
    Plex's web URL format requires the server's machineIdentifier in
    the hash fragment. When unknown, the URL still works if the user
    is logged in to Plex Web with this server as the default."""
    if not plex_base_url or not rating_key:
        return ""
    base = plex_base_url.rstrip("/")
    if server_machine_id:
        return (f"{base}/web/index.html#!/server/{server_machine_id}"
                 f"/details?key=%2Flibrary%2Fmetadata%2F{rating_key}")
    # Fallback: direct metadata URL (works in some Plex deployments)
    return f"{base}/web/index.html#!/details?key=%2Flibrary%2Fmetadata%2F{rating_key}"


def summarize_item(item: dict | None) -> str:
    """Render a Plex item dict into a one-line UI summary. Used by
    the queue row tooltip after a download confirms in Plex."""
    if not isinstance(item, dict):
        return ""
    parts = []
    title = item.get("title")
    if title:
        parts.append(title[:60])
    if item.get("unmatched"):
        parts.append("[unmatched]")
    return " ".join(parts)
