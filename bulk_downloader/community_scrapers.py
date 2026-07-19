"""v3.43.75: CommunityScrapers auto-install for Stash.

# What this module is

Stash has a community-maintained collection of 800+ scrapers at
https://github.com/stashapp/CommunityScrapers. Each scraper is a YAML
file (with optional companion .py files for JS-heavy sites) that
teaches Stash how to extract metadata for a specific site.

Manually managing them is painful:

  - User has to know which scraper exists for which site
  - Scrapers update frequently (sites change HTML, contributors fix)
  - Stash itself doesn't auto-update its scrapers

This module gives BD the ability to:

  1. Fetch the latest CommunityScrapers index (file listing)
  2. List what's available, what's installed, what's outdated
  3. Install/update/remove specific scrapers
  4. Bulk-install scrapers matching a brand list (handy for Matt:
     he has Aylo/Vixen/dl8/Badoink sites; install all matching
     scrapers in one click)

# Strategy

  - Use the GitHub Contents API to enumerate `scrapers/*.yml` (no git
    clone needed; one HTTPS GET per directory)
  - For each scraper of interest, fetch the raw .yml file (and any
    companion .py / .js file referenced via the YAML's `scraper.script`
    or similar field)
  - Copy into Stash's `scrapers/` directory (configurable path)
  - Track installed scrapers + their commit SHAs in a local manifest

# Fail-open

  - No network → fetch_index returns empty list with error string
  - GitHub API rate-limited (60 req/hr for anon, 5000 with token) →
    cache index locally for 1 hour; the install_one calls then use
    the cache instead of refetching the index
  - Disk write failures during install → return False, leave Stash
    state unchanged (no half-installed scraper)
  - Stash dir not configured → all install ops return early with
    "stash_scrapers_dir_not_configured"

# Configuration

Per-deployment (not per-site) — stored in a `community_scrapers.json`
in BD's working dir:

  - `stash_scrapers_dir` — absolute path to Stash's scrapers/ dir
  - `auto_update_on_startup` — refresh index + update installed
    scrapers when BD boots (default False)
  - `installed` — manifest dict: {scraper_name: {sha, installed_at, files}}

# API surface (used by Flask routes)

  - `fetch_index(force_refresh=False)` — list available scrapers
  - `list_installed()` — read local manifest
  - `install_one(scraper_name)` — fetch + copy + manifest update
  - `remove_one(scraper_name)` — delete files + manifest entry
  - `update_one(scraper_name)` — re-fetch + replace if SHA changed
  - `bulk_install(brand_list)` — install matching scrapers
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# Configuration constants
GITHUB_API_BASE = "https://api.github.com/repos/stashapp/CommunityScrapers"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/stashapp/CommunityScrapers/master"
SCRAPERS_API_PATH = "/contents/scrapers"
DEFAULT_BRANCH = "master"

# Cache
_INDEX_CACHE_FILENAME = "community_scrapers_index.json"
_MANIFEST_FILENAME = "community_scrapers_manifest.json"
_INDEX_CACHE_TTL_S = 3600  # 1 hour
_USER_AGENT = "BulkDownloader/3.43.75"


# In-process state lock — serializes manifest writes
_state_lock = threading.RLock()


# ─── Availability ──────────────────────────────────────────────────


def _httpx_available() -> bool:
    try:
        import httpx  # noqa: F401
        return True
    except ImportError:
        return False


# ─── Result types ──────────────────────────────────────────────────


@dataclass
class ScraperEntry:
    """One scraper available in CommunityScrapers."""
    name: str                # filename without extension (matches Stash convention)
    yml_path: str            # path relative to repo root
    sha: str = ""            # GitHub SHA for this file
    size: int = 0
    companion_files: list = field(default_factory=list)  # other .py/.js paths


@dataclass
class InstallResult:
    ok: bool = False
    scraper_name: str = ""
    files_written: list = field(default_factory=list)
    error: str = ""


# ─── Cache + manifest IO ───────────────────────────────────────────


def _cache_dir() -> Path:
    """Where to store the index cache + manifest. Best-effort:
    BD's working dir unless overridden."""
    p = Path(".") / "community_scrapers_cache"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _index_cache_path() -> Path:
    return _cache_dir() / _INDEX_CACHE_FILENAME


def _manifest_path() -> Path:
    return _cache_dir() / _MANIFEST_FILENAME


def _load_index_cache() -> Optional[dict]:
    """Load cached index. Returns None if cache is missing or stale."""
    p = _index_cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cached_at = float(data.get("cached_at", 0) or 0)
        if time.time() - cached_at > _INDEX_CACHE_TTL_S:
            return None
        return data
    except Exception:
        return None


def _save_index_cache(entries: list) -> bool:
    p = _index_cache_path()
    try:
        # v3.47.8 (#43): atomic write — earlier versions wrote directly to
        # the final path, which could leave a half-written cache file if
        # the process was killed mid-write. Subsequent loads would then
        # fail JSON-parse and bypass the cache (slower but not broken).
        # Use tmp+replace for proper atomicity.
        import os as _os
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps({
            "cached_at": time.time(),
            "entries": [
                {"name": e.name, "yml_path": e.yml_path,
                 "sha": e.sha, "size": e.size,
                 "companion_files": e.companion_files}
                for e in entries
            ],
        }, indent=2), encoding="utf-8")
        _os.replace(tmp, p)
        return True
    except Exception as e:
        log.info("community_scrapers: cache write failed: %s", e)
        return False


def load_manifest() -> dict:
    """Read the installed-scrapers manifest. Always returns a dict
    (empty when file missing)."""
    p = _manifest_path()
    if not p.exists():
        return {"installed": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"installed": {}}


def _save_manifest(manifest: dict) -> bool:
    p = _manifest_path()
    try:
        # v3.47.8 (#43): atomic write
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:
        log.info("community_scrapers: manifest write failed: %s", e)
        return False


# ─── Index fetch (GitHub Contents API) ─────────────────────────────


def fetch_index(
    *, force_refresh: bool = False,
    github_token: str = "",
    timeout_s: float = 30.0,
) -> tuple:
    """Fetch the list of available scrapers from GitHub.

    Returns (entries: list[ScraperEntry], error: str). On success
    error is "". On failure entries is [] and error explains why.

    Cache: uses _INDEX_CACHE_TTL_S local cache unless force_refresh.
    """
    # Try cache first
    if not force_refresh:
        cached = _load_index_cache()
        if cached and "entries" in cached:
            entries = [
                ScraperEntry(
                    name=e.get("name", ""),
                    yml_path=e.get("yml_path", ""),
                    sha=e.get("sha", ""),
                    size=int(e.get("size", 0) or 0),
                    companion_files=list(e.get("companion_files", [])),
                )
                for e in cached["entries"]
            ]
            return (entries, "")

    if not _httpx_available():
        return ([], "httpx_not_installed")

    headers = {"User-Agent": _USER_AGENT,
               "Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"

    try:
        import httpx
        url = GITHUB_API_BASE + SCRAPERS_API_PATH
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
        if r.status_code != 200:
            # Rate limit?
            if r.status_code == 403:
                return ([], f"github_rate_limited (status {r.status_code})")
            return ([], f"github_http_{r.status_code}")
        data = r.json()
    except Exception as e:
        return ([], f"fetch_failed:{type(e).__name__}:{str(e)[:120]}")

    if not isinstance(data, list):
        return ([], "unexpected_index_response")

    entries: list = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "")
        if not name.endswith(".yml"):
            continue
        base_name = name[:-4]  # strip .yml
        entries.append(ScraperEntry(
            name=base_name,
            yml_path=str(item.get("path", "") or ""),
            sha=str(item.get("sha", "") or ""),
            size=int(item.get("size", 0) or 0),
        ))

    _save_index_cache(entries)
    return (entries, "")


# ─── List installed ────────────────────────────────────────────────


def list_installed() -> dict:
    """Return the installed-scrapers manifest."""
    with _state_lock:
        return load_manifest()


# ─── Install / update / remove ─────────────────────────────────────


def _validate_stash_dir(stash_dir: str) -> tuple:
    """Validate that the Stash scrapers dir exists + is writable.

    Returns (ok, error_or_empty)."""
    if not stash_dir:
        return (False, "stash_scrapers_dir_not_configured")
    if not os.path.isdir(stash_dir):
        return (False, f"not_a_directory:{stash_dir}")
    # Try writing a probe file to test write access
    probe = os.path.join(stash_dir, ".bd_write_probe")
    try:
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
    except Exception as e:
        return (False, f"not_writable:{e}")
    return (True, "")


def _fetch_raw_file(
    rel_path: str, *,
    timeout_s: float = 30.0,
    github_token: str = "",
) -> tuple:
    """Fetch a single file from CommunityScrapers raw. Returns
    (content_bytes, error_str)."""
    if not _httpx_available():
        return (b"", "httpx_not_installed")
    headers = {"User-Agent": _USER_AGENT}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    try:
        import httpx
        url = f"{GITHUB_RAW_BASE}/{rel_path}"
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
        if r.status_code != 200:
            return (b"", f"http_{r.status_code}")
        return (r.content, "")
    except Exception as e:
        return (b"", f"fetch_failed:{type(e).__name__}:{str(e)[:120]}")


_COMPANION_REF_RE = re.compile(
    rb"script:\s*\n\s*-\s*['\"]?([^'\"\n]+\.(?:py|js))['\"]?",
    re.IGNORECASE,
)


def _find_companion_files(yml_bytes: bytes) -> list:
    """Parse a scraper .yml and return the list of companion file
    paths it references. Conservative: only catches the most common
    `script: - file.py` pattern."""
    refs: list = []
    for m in _COMPANION_REF_RE.finditer(yml_bytes or b""):
        try:
            ref = m.group(1).decode("utf-8", errors="ignore").strip()
            if ref and ref not in refs:
                refs.append(ref)
        except Exception:
            continue
    return refs


def install_one(
    scraper_name: str,
    *,
    stash_dir: str,
    entries: Optional[list] = None,
    github_token: str = "",
    overwrite: bool = True,
) -> InstallResult:
    """Install one scraper into the Stash scrapers/ dir.

    `entries`: if supplied, use these (skip a fetch). Otherwise calls
    fetch_index() to get them.

    `overwrite`: if False AND a file already exists at the target
    path, abort and return ok=False with error="already_installed".
    """
    ok, err = _validate_stash_dir(stash_dir)
    if not ok:
        return InstallResult(ok=False, scraper_name=scraper_name, error=err)

    if entries is None:
        fetched, fetch_err = fetch_index(github_token=github_token)
        if fetch_err:
            return InstallResult(ok=False, scraper_name=scraper_name,
                                 error=f"index_fetch:{fetch_err}")
        entries = fetched

    target_entry = next((e for e in entries if e.name == scraper_name), None)
    if target_entry is None:
        return InstallResult(ok=False, scraper_name=scraper_name,
                             error="scraper_not_found")

    # Fetch the main .yml
    yml_bytes, ferr = _fetch_raw_file(target_entry.yml_path,
                                       github_token=github_token)
    if ferr:
        return InstallResult(ok=False, scraper_name=scraper_name,
                             error=f"yml_fetch:{ferr}")

    # Find companion files referenced
    companions = _find_companion_files(yml_bytes)

    # Write main .yml + companions atomically (best-effort: if any
    # companion fetch fails, we still proceed with the main one but
    # log the miss).
    files_written: list = []
    yml_target = os.path.join(stash_dir, f"{scraper_name}.yml")
    if not overwrite and os.path.exists(yml_target):
        return InstallResult(ok=False, scraper_name=scraper_name,
                             error="already_installed")
    try:
        with open(yml_target, "wb") as f:
            f.write(yml_bytes)
        files_written.append(yml_target)
    except Exception as e:
        return InstallResult(ok=False, scraper_name=scraper_name,
                             error=f"yml_write:{e}")

    companion_errors: list = []
    for ref in companions:
        # Reference may be a bare filename like "BrazzersByLink.py" or
        # a path like "scripts/foo.py". Try the scrapers/ subdir.
        rel = ref
        if not rel.startswith("scrapers/"):
            rel = f"scrapers/{rel}"
        comp_bytes, comp_err = _fetch_raw_file(rel,
                                                github_token=github_token)
        if comp_err:
            companion_errors.append(f"{ref}:{comp_err}")
            continue
        # Write next to the .yml using the bare filename
        bare = os.path.basename(ref)
        comp_target = os.path.join(stash_dir, bare)
        try:
            with open(comp_target, "wb") as f:
                f.write(comp_bytes)
            files_written.append(comp_target)
        except Exception as e:
            companion_errors.append(f"{ref}:write:{e}")

    # Update manifest
    with _state_lock:
        m = load_manifest()
        installed = m.setdefault("installed", {})
        installed[scraper_name] = {
            "sha": target_entry.sha,
            "installed_at": time.time(),
            "files": files_written,
            "companions_missed": companion_errors,
        }
        _save_manifest(m)

    return InstallResult(
        ok=True, scraper_name=scraper_name,
        files_written=files_written,
        error="" if not companion_errors else f"companions_missed:{len(companion_errors)}",
    )


def remove_one(scraper_name: str, *, stash_dir: str) -> InstallResult:
    """Remove the installed files for a scraper and drop the manifest
    entry. Doesn't error if files are already missing."""
    ok, err = _validate_stash_dir(stash_dir)
    if not ok:
        return InstallResult(ok=False, scraper_name=scraper_name, error=err)
    with _state_lock:
        m = load_manifest()
        installed = m.get("installed", {})
        entry = installed.get(scraper_name)
        if entry is None:
            return InstallResult(ok=False, scraper_name=scraper_name,
                                 error="not_in_manifest")
        files = entry.get("files") or []
        removed: list = []
        for f in files:
            try:
                if os.path.exists(f):
                    os.remove(f)
                    removed.append(f)
            except Exception as e:
                log.info("community_scrapers: remove %s failed: %s", f, e)
        installed.pop(scraper_name, None)
        _save_manifest(m)
        return InstallResult(ok=True, scraper_name=scraper_name,
                             files_written=removed)


def update_one(
    scraper_name: str, *,
    stash_dir: str,
    github_token: str = "",
) -> InstallResult:
    """Re-fetch + reinstall if the upstream SHA has changed."""
    fetched, fetch_err = fetch_index(force_refresh=True,
                                      github_token=github_token)
    if fetch_err:
        return InstallResult(ok=False, scraper_name=scraper_name,
                             error=f"index_fetch:{fetch_err}")
    target = next((e for e in fetched if e.name == scraper_name), None)
    if target is None:
        return InstallResult(ok=False, scraper_name=scraper_name,
                             error="scraper_not_found")
    with _state_lock:
        m = load_manifest()
        cur = (m.get("installed") or {}).get(scraper_name) or {}
        cur_sha = cur.get("sha", "")
    if cur_sha and cur_sha == target.sha:
        return InstallResult(ok=True, scraper_name=scraper_name,
                             error="already_up_to_date")
    return install_one(scraper_name, stash_dir=stash_dir,
                       entries=fetched, github_token=github_token,
                       overwrite=True)


def bulk_install(
    brand_keywords: list,
    *,
    stash_dir: str,
    github_token: str = "",
) -> dict:
    """Install all scrapers whose name matches any keyword (case-
    insensitive substring). Returns a summary dict."""
    fetched, fetch_err = fetch_index(github_token=github_token)
    if fetch_err:
        return {"ok": False, "error": f"index_fetch:{fetch_err}"}
    keywords_low = [str(k).lower() for k in (brand_keywords or [])]
    if not keywords_low:
        return {"ok": False, "error": "empty_keyword_list"}
    matches: list = []
    for e in fetched:
        nm = e.name.lower()
        if any(k in nm for k in keywords_low):
            matches.append(e)
    if not matches:
        return {"ok": True, "matched": 0, "installed": [], "failed": []}
    installed: list = []
    failed: list = []
    for e in matches:
        result = install_one(e.name, stash_dir=stash_dir,
                              entries=fetched, github_token=github_token)
        if result.ok:
            installed.append(e.name)
        else:
            failed.append({"name": e.name, "error": result.error})
    return {
        "ok": True,
        "matched": len(matches),
        "installed": installed,
        "failed": failed,
    }


__all__ = [
    "fetch_index",
    "list_installed",
    "install_one",
    "remove_one",
    "update_one",
    "bulk_install",
    "load_manifest",
    "ScraperEntry",
    "InstallResult",
    "_validate_stash_dir",
    "_find_companion_files",
]
