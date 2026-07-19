"""v3.43.37: deep Jellyfin integration.

Mirrors v3.43.29 plex_deep.py but for Jellyfin. Same opt-in pattern,
same fail-open philosophy.

Builds on the v3.20-era basic `hooks.jellyfin_refresh` (full library
refresh) with the workflows that move the needle:

  1. Per-item refresh — refresh just the new file, not the whole
     library. Faster on large libraries.
  2. Match confirmation — after refresh, poll to confirm Jellyfin
     identified the new file. Surface unmatched items so user can
     fix in Jellyfin's metadata identification UI.
  3. Collection routing — auto-add new items to a per-site
     collection. Optional auto-create.

## Jellyfin API surface

JSON-based (much nicer than Plex's XML). Auth via either:
  - `X-Emby-Token` header (preferred — key stays out of URLs/logs)
  - `?api_key=...` query parameter (works on every endpoint)

We use the `X-Emby-Token` header form so the key never lands in
access logs, the Referer header, or a proxy's request line. This
matches the basic refresh code in hooks.py.

Endpoints used:
  GET  /System/Info                       — version + identity probe
  GET  /Library/MediaFolders              — list libraries
  POST /Items/<id>/Refresh                — per-item refresh
  POST /Library/Refresh                   — full library refresh
  GET  /Users/<userId>/Items?path=...     — find item by file path
  GET  /Users/<userId>/Items?... &SearchTerm=...
  GET  /Collections                       — list collections
  POST /Collections                       — create collection
  POST /Collections/<id>/Items?ids=...    — add items

## Configuration

Builds on basic `jellyfin_url` / `jellyfin_api_key`. Adds:

  jellyfin_deep_enabled (bool)
  jellyfin_user_id (str)              — required for some lookups
  jellyfin_match_confirm (bool)
  jellyfin_collection (str)
  jellyfin_auto_create_collection (bool)

The user_id is required because Jellyfin's "find by path" requires
a user context (sees what THAT user can see). The basic refresh
trigger doesn't need it; deep features do.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


# Jellyfin is fast on LAN (similar to Plex). Match polling takes a bit
# longer because the scan + metadata fetch happen back-to-back.
QUERY_TIMEOUT_S = 15.0
MATCH_POLL_TIMEOUT_S = 30.0


class JellyfinError(Exception):
    """Mirrors PlexError / StashError / QBError / JDError: structured
    kind for caller routing (auth → re-prompt, network → retry,
    unknown → fall through silently)."""
    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}


class JellyfinClient:
    """HTTP/JSON client for Jellyfin's API.

    All read methods return None on failure. All write methods return
    True on success, False on failure (and never raise). Same
    convention as plex_deep.PlexClient + stash_deep.StashClient."""

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""

    @property
    def configured(self) -> bool:
        """Jellyfin requires BOTH URL and API key. Without the key,
        every endpoint returns 401."""
        return bool(self.base_url and self.api_key)

    def _url(self, path: str, params: dict | None = None) -> str:
        """Compose the request URL. The api_key is sent via the
        ``X-Emby-Token`` header in _request (not the query string), so it
        never leaks into access logs, the Referer header, or proxy
        request lines. This matches hooks.py's basic refresh."""
        if not self.base_url:
            return ""
        params = dict(params or {})
        if not params:
            return f"{self.base_url}{path}"
        query = urllib.parse.urlencode(params)
        sep = "&" if "?" in path else "?"
        return f"{self.base_url}{path}{sep}{query}"

    def _request(self, method: str, path: str,
                  params: dict | None = None,
                  body: bytes | None = None,
                  timeout: float = QUERY_TIMEOUT_S,
                  parse_json: bool = True) -> Any:
        """Execute one HTTP request. Returns parsed JSON dict (or raw
        bytes when parse_json=False). Raises JellyfinError on failure."""
        if not self.configured:
            raise JellyfinError("config",
                "Jellyfin URL or API key not set")
        url = self._url(path, params)
        try:
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header("Accept", "application/json")
            req.add_header("X-Emby-Token", self.api_key)
            if body is not None:
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise JellyfinError("auth",
                    f"Jellyfin rejected API key (HTTP {e.code})",
                    {"hint": "Get a fresh API key from Jellyfin → "
                              "Dashboard → API Keys, then update the "
                              "site's jellyfin_api_key."})
            raise JellyfinError("network", f"HTTP {e.code}")
        except urllib.error.URLError as e:
            raise JellyfinError("network",
                f"connection failed: {e.reason}",
                {"hint": "Verify Jellyfin is running and the URL is "
                          "reachable from this server."})
        except Exception as e:
            raise JellyfinError("unknown",
                f"{type(e).__name__}: {e}")
        if not parse_json:
            return raw
        # Jellyfin returns empty body on some POST endpoints (refresh,
        # add-to-collection); treat as success.
        if not raw or not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise JellyfinError("unknown",
                f"non-JSON response: {e}",
                {"raw": raw[:300].decode("utf-8", "replace")})

    # ── Connectivity ──────────────────────────────────────────────────

    def diagnose(self) -> dict:
        """Connectivity + auth probe. Same response shape as
        PlexClient.diagnose() so the UI can render uniformly."""
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
                result["error"] = "jellyfin_url not configured"
                result["hint"] = "Set jellyfin_url in the site config."
            else:
                result["error"] = "jellyfin_api_key not configured"
                result["hint"] = "Set jellyfin_api_key in the site config."
            return result
        try:
            data = self._request("GET", "/System/Info", timeout=5.0)
            result["reachable"] = True
            result["ok"] = True
            result["version"] = data.get("Version")
        except JellyfinError as e:
            if e.kind == "network":
                result["error"] = e.message
                result["hint"] = e.detail.get("hint") or (
                    "Jellyfin not reachable. Verify the URL and that the "
                    "server is running."
                )
            elif e.kind == "auth":
                result["reachable"] = True
                result["error"] = e.message
                result["hint"] = e.detail.get("hint")
            else:
                result["error"] = e.message
        return result

    # ── Library / section discovery ───────────────────────────────────

    def list_libraries(self) -> list[dict]:
        """Enumerate the user's libraries (Movies, Shows, etc.).
        Returns [{id, name, collection_type, locations}, ...] used by
        the UI to populate a library-id dropdown."""
        try:
            data = self._request("GET", "/Library/MediaFolders")
        except JellyfinError:
            return []
        out = []
        for item in (data.get("Items") or []):
            out.append({
                "id": item.get("Id"),
                "name": item.get("Name"),
                "collection_type": item.get("CollectionType"),
                "locations": item.get("Locations", []),
            })
        return out

    # ── Per-item refresh ──────────────────────────────────────────────

    def refresh_item(self, item_id: str) -> bool:
        """Refresh just one item's metadata. Used after we've located
        a freshly-imported file via find_item_by_path — confirms its
        metadata is fully populated before we try collection routing.
        """
        if not item_id:
            return False
        try:
            self._request("POST", f"/Items/{item_id}/Refresh",
                           params={"Recursive": "false",
                                   "MetadataRefreshMode": "FullRefresh"})
            return True
        except JellyfinError:
            return False

    def refresh_library(self) -> bool:
        """Full library refresh. The basic v3.20 hook already does this,
        but exposing it here means deep_enabled sites don't need both
        the basic hook AND deep — one path covers both."""
        try:
            self._request("POST", "/Library/Refresh")
            return True
        except JellyfinError:
            return False

    # ── Item lookup ───────────────────────────────────────────────────

    def find_item_by_path(self, user_id: str, file_path: str,
                            timeout_s: float = MATCH_POLL_TIMEOUT_S) -> dict | None:
        """Locate the Jellyfin item for a given file path. Polls
        because the scan + metadata fetch is async; gives up after
        timeout_s.

        Jellyfin lets us filter items by Path which is direct unlike
        Plex's need to list-and-grep.

        Returns the item dict (with Id, Name, Path, ProductionYear,
        etc.) or None."""
        if not user_id or not file_path:
            return None
        deadline = time.time() + timeout_s
        # Jellyfin stores paths as the server sees them — same volume
        # mount as our downloader since we're on the same host.
        from pathlib import Path
        normalized = str(Path(file_path)).replace("\\", "/")
        while time.time() < deadline:
            try:
                # Search recently-added first; faster than full
                # library walk
                data = self._request("GET",
                    f"/Users/{user_id}/Items",
                    params={
                        "Recursive": "true",
                        "Fields": "Path,ProductionYear",
                        "SortBy": "DateCreated",
                        "SortOrder": "Descending",
                        "Limit": "50",
                    })
                for item in (data.get("Items") or []):
                    item_path = (item.get("Path") or "").replace("\\", "/")
                    if item_path == normalized or item_path.endswith(normalized):
                        return {
                            "id": item.get("Id"),
                            "name": item.get("Name"),
                            "path": item_path,
                            "year": item.get("ProductionYear"),
                            "type": item.get("Type"),
                            "unmatched": not bool(item.get("ProductionYear")),
                        }
            except JellyfinError:
                pass
            time.sleep(2.0)
        return None

    # ── Collection management ────────────────────────────────────────

    def find_collection_by_name(self, user_id: str,
                                   name: str) -> dict | None:
        """Look up a collection by exact name. Jellyfin's collection
        listing requires a user context (collections are per-user)."""
        if not user_id or not name:
            return None
        try:
            data = self._request("GET", f"/Users/{user_id}/Items",
                                  params={
                                      "Recursive": "true",
                                      "IncludeItemTypes": "BoxSet",
                                      "SearchTerm": name,
                                      "Limit": "20",
                                  })
        except JellyfinError:
            return None
        # Exact match — Jellyfin's SearchTerm is substring
        for item in (data.get("Items") or []):
            if (item.get("Name") or "").strip() == name.strip():
                return {"id": item.get("Id"), "name": item.get("Name")}
        return None

    def create_collection(self, name: str,
                            item_id: str = "") -> dict | None:
        """Create a new collection. Jellyfin (unlike Plex) doesn't
        require a seed item, but we pass one anyway for the common
        case of "first item being added kicks off the collection."
        """
        if not name:
            return None
        params = {"Name": name}
        if item_id:
            params["Ids"] = item_id
        try:
            data = self._request("POST", "/Collections", params=params)
        except JellyfinError:
            return None
        coll_id = data.get("Id")
        if not coll_id:
            return None
        return {"id": coll_id, "name": name}

    def add_to_collection(self, collection_id: str,
                            item_id: str) -> bool:
        """Add an item to an existing collection. Idempotent — Jellyfin
        no-ops if the item is already in the collection."""
        if not collection_id or not item_id:
            return False
        try:
            self._request("POST",
                f"/Collections/{collection_id}/Items",
                params={"Ids": item_id})
            return True
        except JellyfinError:
            return False


# ── Module helpers ────────────────────────────────────────────────────

def get_client_for_site(cfg: dict) -> JellyfinClient:
    """Construct a JellyfinClient from a site config. Returns an
    unconfigured client when fields are missing."""
    return JellyfinClient(
        base_url=cfg.get("jellyfin_url") or "",
        api_key=cfg.get("jellyfin_api_key") or "",
    )


def deep_enabled(cfg: dict) -> bool:
    """True when the site has opted into deep Jellyfin features.
    Requires URL+key (basic hook needs both anyway) AND the explicit
    deep flag."""
    if not (cfg.get("jellyfin_url") and cfg.get("jellyfin_api_key")):
        return False
    return bool(cfg.get("jellyfin_deep_enabled"))


def build_open_in_jellyfin_url(jellyfin_base_url: str,
                                  item_id: str) -> str:
    """Construct a deep-link URL that opens an item in Jellyfin's web
    UI. Works in any browser logged in to that Jellyfin instance."""
    if not jellyfin_base_url or not item_id:
        return ""
    base = jellyfin_base_url.rstrip("/")
    return f"{base}/web/index.html#!/details?id={item_id}"


def summarize_item(item: dict | None) -> str:
    """Render a Jellyfin item dict into a one-line UI summary."""
    if not isinstance(item, dict):
        return ""
    parts = []
    name = item.get("name")
    if name:
        parts.append(name[:60])
    year = item.get("year")
    if year:
        parts.append(f"({year})")
    if item.get("unmatched"):
        parts.append("[unmatched]")
    return " ".join(parts)
