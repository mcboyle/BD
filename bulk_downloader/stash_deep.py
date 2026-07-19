"""v3.43.28: deep Stash integration.

Builds on the v3.20-era `hooks.stash_trigger_scan` (which is just a
fire-and-forget scan trigger) by adding the workflows that actually
move the needle for the user:

  1. Pre-download dedup: query Stash to see if the URL is already
     in the library. If so, mark the queue row "duplicate in Stash"
     and skip the download. Saves bandwidth + disk on the 8K files.

  2. Pre-download scrape preview: ask Stash's scraper plugin to look
     at the URL and return what metadata (performers, studio, tags)
     it WOULD extract. The user can see this in the queue before
     committing the bandwidth.

  3. Post-download enrichment: after the scan completes, find the
     newly-created scene and push BulkDownloader-known tags (from
     URL patterns) into it. Set the site's configured Stash studio
     ID. Confirm Stash found a match — if it didn't ("unmatched"),
     surface that in the BulkDownloader UI so the user can fix in
     Stash.

  4. Open-in-Stash link generation for completed queue rows.

## GraphQL queries used

  findScene(id)                    — get a scene by ID
  findScenes(scene_filter, ...)    — search/filter; we use url match
  scrapeSceneURL(url, scraper_id)  — preview what scraping would yield
  sceneUpdate(input)               — push tags/studio/details
  findStudios / studioCreate       — for auto-creating per-site studios
  allTags                          — for resolving tag names to IDs

## Configuration

Same as v3.20 (`stash_url`, `stash_api_key`) but adds:

  stash_deep_enabled (bool)        — turn deep integration on/off
                                     for this site
  stash_dedup_check (bool)         — query before download
  stash_scrape_preview (bool)      — pull metadata preview
  stash_studio_id (str, optional)  — auto-assign this Stash studio
                                     to scenes from this site

The basic scan trigger is unaffected by these flags; the deep
features are strictly additive.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request


# Connection timeouts. GraphQL queries against a typical Stash instance
# return in <500ms; the longer timeouts are for the scraper plugin
# which can take 5-15s when it has to hit the source site.
QUERY_TIMEOUT_S = 15.0
SCRAPE_TIMEOUT_S = 60.0


class StashError(Exception):
    """Raised for any Stash GraphQL failure with a structured kind so
    callers can route the response (auth-class → re-test, network →
    retry, unknown → fall through silently)."""
    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.detail = detail or {}


class StashClient:
    """GraphQL client for the v3.43.28 deep features. Reuses the
    URL-validation discipline from hooks.py — empty URLs are silently
    no-op'd, host validation matches the basic scan trigger.

    Doesn't cache connections; each call is its own urllib request.
    Stash GraphQL is fast (<500ms) over LAN; the connection-setup
    overhead is negligible compared to the work done per-URL."""

    def __init__(self, base_url: str, api_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self._graphql_url = None
        if self.base_url:
            if not self.base_url.endswith("/graphql"):
                self._graphql_url = self.base_url + "/graphql"
            else:
                self._graphql_url = self.base_url

    @property
    def configured(self) -> bool:
        """True when we have enough info to make a call. Callers use
        this to short-circuit when Stash isn't set up on the site."""
        return bool(self._graphql_url)

    def _graphql(self, query: str, variables: dict | None = None,
                  timeout: float = QUERY_TIMEOUT_S) -> dict:
        """Execute a GraphQL query. Returns the `data` field on success;
        raises StashError on any failure."""
        if not self._graphql_url:
            raise StashError("config", "Stash URL not configured")
        payload = {"query": query, "variables": variables or {}}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        try:
            req = urllib.request.Request(
                self._graphql_url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise StashError("auth",
                    f"Stash rejected request (HTTP {e.code})",
                    {"hint": "Verify the API key in Stash → Settings → "
                              "Security → API Key, then update the site's "
                              "stash_api_key field."})
            raise StashError("network", f"HTTP {e.code}")
        except urllib.error.URLError as e:
            raise StashError("network", f"connection failed: {e.reason}",
                {"hint": "Verify Stash is running and the URL is reachable "
                          "from this server."})
        except Exception as e:
            raise StashError("unknown",
                f"{type(e).__name__}: {e}")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise StashError("unknown", "non-JSON response",
                {"body": body[:300]})
        if "errors" in data and data["errors"]:
            # Surface the first GraphQL error verbatim — they're
            # usually descriptive (missing field, type mismatch).
            msg = data["errors"][0].get("message", "unknown GraphQL error")
            raise StashError("graphql", msg, {"errors": data["errors"]})
        return data.get("data") or {}

    # ── Connectivity ──────────────────────────────────────────────────

    def diagnose(self) -> dict:
        """Connectivity + auth probe. Returns the structured shape the
        UI expects: {ok, reachable, version, error, hint}."""
        result = {
            "ok": False,
            "reachable": False,
            "version": None,
            "url": self._graphql_url or "(not configured)",
            "error": None,
            "hint": None,
        }
        if not self.configured:
            result["error"] = "Stash URL not configured"
            result["hint"] = "Set stash_url in the site config."
            return result
        try:
            # `version` query is one of the lightest reads available;
            # works pre- and post-auth (returns Stash version string).
            data = self._graphql("query { version { version } }",
                                  timeout=5.0)
            v = (data.get("version") or {}).get("version")
            result["reachable"] = True
            result["ok"] = True
            result["version"] = v
        except StashError as e:
            if e.kind == "network":
                result["error"] = e.message
                result["hint"] = e.detail.get("hint") or (
                    "Stash not reachable. Check the URL and that the "
                    "Stash process is running."
                )
            elif e.kind == "auth":
                result["reachable"] = True  # got HTTP back, just unauth
                result["error"] = e.message
                result["hint"] = e.detail.get("hint")
            else:
                result["error"] = e.message
        return result

    # ── Pre-download dedup ────────────────────────────────────────────

    def find_scene_by_url(self, url: str) -> dict | None:
        """Search Stash for a scene whose URL matches `url`. Returns
        the scene dict (with id, title, path, performers, studio) or
        None if not found. Used by the runner BEFORE downloading to
        detect duplicates.

        Stash scenes can have multiple URLs; the filter matches if ANY
        of them equals the query. We use MODIFIER=EQUALS for exact
        match — partial-match would cause false positives (one episode
        URL matching another from the same series page)."""
        if not url:
            return None
        query = """
        query FindSceneByURL($url: String!) {
          findScenes(
            scene_filter: { url: { value: $url, modifier: EQUALS } }
            filter: { per_page: 1 }
          ) {
            scenes {
              id
              title
              files { path size duration }
              performers { id name }
              studio { id name }
              tags { id name }
            }
          }
        }
        """
        try:
            data = self._graphql(query, {"url": url})
        except StashError:
            return None
        scenes = ((data.get("findScenes") or {}).get("scenes") or [])
        return scenes[0] if scenes else None

    # ── Scraper preview ───────────────────────────────────────────────

    def scrape_url(self, url: str) -> dict | None:
        """Ask Stash's scrape system what metadata it would extract
        from `url`. Returns the scrape result (title, performers,
        studio, tags, details, image) or None on failure.

        This is a READ ONLY query — it doesn't create or modify
        anything in Stash. Returns None if no scraper supports the
        URL's host."""
        if not url:
            return None
        # scrapeURL works without a specific scraper_id — Stash picks
        # the best match by host. If no scraper matches, returns null.
        query = """
        query ScrapeSceneURL($url: String!) {
          scrapeSceneURL(url: $url) {
            title
            details
            url
            date
            image
            studio { name }
            performers { name }
            tags { name }
          }
        }
        """
        try:
            data = self._graphql(query, {"url": url}, timeout=SCRAPE_TIMEOUT_S)
        except StashError:
            return None
        return data.get("scrapeSceneURL")

    # ── Studio resolution ─────────────────────────────────────────────

    def find_studio_by_name(self, name: str) -> dict | None:
        """Look up a studio by exact name. Returns {id, name, url}
        or None. Used by the per-site auto-studio path: we look up
        the site name in Stash and either reuse the existing studio
        or create one."""
        if not name:
            return None
        query = """
        query FindStudios($name: String!) {
          findStudios(
            studio_filter: { name: { value: $name, modifier: EQUALS } }
            filter: { per_page: 1 }
          ) {
            studios { id name url }
          }
        }
        """
        try:
            data = self._graphql(query, {"name": name})
        except StashError:
            return None
        studios = ((data.get("findStudios") or {}).get("studios") or [])
        return studios[0] if studios else None

    def create_studio(self, name: str, url: str = "") -> dict | None:
        """Create a new studio. Used when find_studio_by_name returns
        None and the site wants auto-create. Returns the new studio
        dict or None on failure (e.g. duplicate name race condition).
        """
        if not name:
            return None
        mutation = """
        mutation CreateStudio($input: StudioCreateInput!) {
          studioCreate(input: $input) { id name url }
        }
        """
        input_obj = {"name": name}
        if url:
            input_obj["url"] = url
        try:
            data = self._graphql(mutation, {"input": input_obj})
        except StashError:
            return None
        return data.get("studioCreate")

    # ── Tag resolution ────────────────────────────────────────────────

    def find_or_create_tags(self, tag_names: list[str]) -> list[str]:
        """Given a list of tag NAMES, return the corresponding list of
        Stash tag IDs. Tags that don't exist are created. Used by the
        post-download enrichment path to push BulkDownloader's URL-
        pattern tags into Stash.

        Returns a list of string IDs; failures get filtered out
        silently — losing one tag is better than failing the whole
        enrichment."""
        if not tag_names:
            return []
        out_ids = []
        for name in tag_names:
            if not name or not isinstance(name, str):
                continue
            # Search first
            find_q = """
            query FindTags($name: String!) {
              findTags(
                tag_filter: { name: { value: $name, modifier: EQUALS } }
                filter: { per_page: 1 }
              ) {
                tags { id name }
              }
            }
            """
            try:
                data = self._graphql(find_q, {"name": name})
                tags = ((data.get("findTags") or {}).get("tags") or [])
                if tags:
                    out_ids.append(tags[0]["id"])
                    continue
            except StashError:
                continue
            # Not found — create
            create_q = """
            mutation CreateTag($input: TagCreateInput!) {
              tagCreate(input: $input) { id name }
            }
            """
            try:
                data = self._graphql(create_q, {"input": {"name": name}})
                created = data.get("tagCreate")
                if created and created.get("id"):
                    out_ids.append(created["id"])
            except StashError:
                continue
        return out_ids

    # ── Post-download enrichment ──────────────────────────────────────

    def update_scene(self, scene_id: str,
                      studio_id: str | None = None,
                      tag_ids: list[str] | None = None,
                      url: str | None = None,
                      title: str | None = None) -> dict | None:
        """Patch an existing scene's metadata. None-valued args are
        omitted from the mutation so this method can selectively
        update just one field without clobbering others.

        Returns the updated scene dict or None on failure."""
        if not scene_id:
            return None
        input_obj = {"id": scene_id}
        if studio_id is not None:
            input_obj["studio_id"] = studio_id
        if tag_ids is not None:
            input_obj["tag_ids"] = list(tag_ids)
        if url is not None:
            input_obj["url"] = url
        if title is not None:
            input_obj["title"] = title
        # If only id is set, nothing to update — skip the call
        if len(input_obj) == 1:
            return None
        mutation = """
        mutation UpdateScene($input: SceneUpdateInput!) {
          sceneUpdate(input: $input) { id title }
        }
        """
        try:
            data = self._graphql(mutation, {"input": input_obj})
        except StashError:
            return None
        return data.get("sceneUpdate")

    # ── Find newly-imported scene by path ─────────────────────────────

    def find_scene_by_path(self, file_path: str) -> dict | None:
        """After a scan completes, find the scene Stash created for a
        given file path. Used by the post-download enrichment path
        to push BulkDownloader's tags onto a freshly-scanned scene.

        Returns the scene dict or None. The scan can take a few
        seconds; the caller usually retries this for ~10s before
        giving up."""
        if not file_path:
            return None
        # findScenes supports a path-substring filter; we use that
        # since exact-match on the path requires it to match Stash's
        # internal normalization (trailing slashes, case, etc.)
        query = """
        query FindByPath($path: String!) {
          findScenes(
            scene_filter: { path: { value: $path, modifier: INCLUDES } }
            filter: { per_page: 5 }
          ) {
            scenes {
              id title
              files { path }
              studio { id name }
              tags { id name }
            }
          }
        }
        """
        try:
            data = self._graphql(query, {"path": file_path})
        except StashError:
            return None
        scenes = ((data.get("findScenes") or {}).get("scenes") or [])
        # Filter: exact path match against any file
        for s in scenes:
            for f in (s.get("files") or []):
                if f.get("path") == file_path:
                    return s
        # No exact match — return the first INCLUDES result if any
        return scenes[0] if scenes else None


# ── Module helpers ────────────────────────────────────────────────────

def get_client_for_site(cfg: dict) -> StashClient:
    """Construct a StashClient from a site config. Returns an
    unconfigured client (configured=False) when stash_url is empty —
    callers branch on that to skip Stash work entirely for sites that
    don't use it."""
    return StashClient(
        base_url=cfg.get("stash_url") or "",
        api_key=cfg.get("stash_api_key") or "",
    )


def deep_enabled(cfg: dict) -> bool:
    """True when the site has opted into deep Stash features. Both
    URL must be set AND the explicit flag must be true. This means
    sites with just the basic scan trigger (v3.20) are unaffected."""
    if not cfg.get("stash_url"):
        return False
    return bool(cfg.get("stash_deep_enabled"))


def build_open_in_stash_url(stash_base_url: str, scene_id: str) -> str:
    """Construct a deep-link URL that opens a scene in Stash's web UI.
    Used by the queue context menu's "Open in Stash" action."""
    if not stash_base_url or not scene_id:
        return ""
    base = stash_base_url.rstrip("/")
    # Strip /graphql if the user put the GraphQL URL in stash_url —
    # the human UI lives at /scenes/<id>, not /graphql/scenes/<id>
    if base.endswith("/graphql"):
        base = base[:-len("/graphql")]
    return f"{base}/scenes/{scene_id}"


def summarize_scrape(scrape_result: dict | None) -> str:
    """Render a scrape result into a one-line summary for the queue
    row tooltip. Returns empty string if nothing useful."""
    if not isinstance(scrape_result, dict):
        return ""
    parts = []
    title = scrape_result.get("title")
    if title:
        parts.append(title[:60])
    studio = (scrape_result.get("studio") or {}).get("name")
    if studio:
        parts.append(f"[{studio}]")
    performers = scrape_result.get("performers") or []
    if performers:
        names = [p.get("name", "") for p in performers if isinstance(p, dict)]
        names = [n for n in names if n]
        if names:
            parts.append("· " + ", ".join(names[:3])
                          + (" +more" if len(names) > 3 else ""))
    return " ".join(parts)
