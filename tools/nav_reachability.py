#!/usr/bin/env python3
"""nav_reachability — the gate that keeps every page reachable by clicks.

Born from the v3.66.199 orphaned-pages audit (FINDING_orphaned_pages_v3_66_199,
docs/NAV_CONSOLIDATION.md): from `/`, exactly one page was reachable — `/`
itself — and 13 of 26 D3 SPA routes had no inbound link. The parity gate
measures endpoint *wiring* (`spa_wired`), not page *reachability*, so the gap
accumulated silently across three frontends. This tool measures reachability.

Two checks:

1. SERVER — boot the real app, BFS-crawl rendered ``<a href>`` links from
   the sanctioned root ``/`` (the D3 SPA, root-flipped at v3.66.203). The
   legacy shell and its /legacy crawl root were removed in Phase 4
   (v3.66.334); a handful of legacy-era cockpit/admin pages that were only
   reachable from that shell are now typed-URL-only (see
   ``_is_typed_url_only``).
   Every GET page rule in ``url_map`` must be crawl-reachable. Parameterized
   rules pass via an inbound href whose path starts with the rule's static
   prefix (on any reachable page), falling back to a source-level reference
   scan. ``/m``, ``/m/ops`` and ``/m2`` are documented redirect shims
   (302 -> /). ``/`` may answer 503 when ``frontend/dist`` is absent
   (sandbox/CI without a built SPA).

2. SPA — statically parse ``frontend/src/App.tsx`` routes, then scan
   ``frontend/src`` for inbound nav targets (``to=``, ``navigate(``, ``go(``,
   ``href=``, ``<Link``, ``Navigate``). Every route needs >=1 inbound
   reference outside its own route file and App.tsx. ``/`` and the ``*``
   catch-all are exempt; ``:param`` routes match on their distinctive tail.

Exit 1 from ``--check`` on any orphan. Lives as a test
(tests/test_nav_reachability.py) so it runs in the cut band and the on-stash
suite — deliberately NOT wired into build_release.py (guard file).
"""
from __future__ import annotations

import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent.parent

# Imported by absolute path AND as ``tools.nav_reachability`` (see
# tools/code_intelligence/reachability_service.py), so its own directory has to
# be on sys.path before the sibling import can resolve either way.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import spa_population  # noqa: E402  (needs the sys.path insert above)

# ── shared page-classification filters (v3.66.766: canonical HERE -- NOT a mirror.
# These filters are defined only in this tool; there is no separate "audit" source
# to keep in sync with. Earlier wording claimed a mirror that does not exist.) ─────

_NON_PAGE_SEGMENTS = (
    "/api/", "/static", "/screenshots", "/thumb", "/stream", "/download",
    "/file", "/raw", "/export", "/sse", "/ws", "/events", "/blob", "/media",
    "/preview_image",
)
_INFRA_EXACT = {"/metrics", "/sw.js", "/apple-touch-icon.png", "/icon.svg"}

# Redirect shims: entry conveniences, 302 -> / (Phase 1 root flip).
# Documented in docs/NAV_CONSOLIDATION.md; intentionally unlinked.
_REDIRECT_SHIMS = {"/m", "/m/", "/m/ops", "/m/ops/", "/m2", "/m2/"}

# Sanctioned crawl root (Phase 1 root flip, v3.66.203): "/" is the SPA.
# Phase 4 (v3.66.334) removed the /legacy shell that used to be the second
# crawl root, so "/" is the only seed now.
_CRAWL_ROOTS = {"/"}

# Phase 4 (v3.66.334): the now-deleted legacy shell was the only page that
# carried *static* <a href> nav into the server-rendered surfaces. Two such
# surfaces are no longer statically crawl-reachable as a result:
#   1. the cockpit dev console (/cockpit/*) — its own nav is API-driven
#      (/api/cockpit/nav, rendered client-side), reached by opening the
#      console, not by a static crawl; cockpit_console.py is deploy-excluded.
#   2. the two top-level legacy-admin pages /fleet + /framework — typed-URL
#      only now (same disposition as the nav-consolidation pages in
#      docs/NAV_CONSOLIDATION.md).
# All are valid GET routes; they are exempt from the static-crawl orphan gate.
_TYPED_URL_ONLY_EXACT = {"/fleet", "/framework", "/cockpit"}
_TYPED_URL_ONLY_PREFIXES = ("/cockpit/",)


def _is_typed_url_only(rule: str) -> bool:
    n = _norm(rule)
    if n in _TYPED_URL_ONLY_EXACT:
        return True
    return any(n.startswith(p) for p in _TYPED_URL_ONLY_PREFIXES)

_ASSET_EXT = re.compile(r"\.(png|jpe?g|gif|svg|ico|css|js|json|map|webmanifest|txt)$", re.I)


def _norm(path: str) -> str:
    path = urlsplit(path).path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _is_page_rule(rule: str) -> bool:
    if any(seg in rule for seg in _NON_PAGE_SEGMENTS):
        return False
    if _norm(rule) in _INFRA_EXACT:
        return False
    if _ASSET_EXT.search(rule):
        return False
    return True


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for k, v in attrs:
            if k == "href" and v and not v.startswith(("#", "mailto:", "javascript:")):
                self.hrefs.append(v)


# ── 1. server-side crawl ─────────────────────────────────────────────────────

def check_server(verbose: bool = False) -> list[str]:
    """Return list of orphan descriptions (empty == pass)."""
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    if "BD_HOME" not in os.environ:
        import tempfile
        os.environ["BD_HOME"] = tempfile.mkdtemp(prefix="bd_navreach_")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from bulk_downloader.app import app  # heavy import, by design

    exact_rules: set[str] = set()
    param_rules: list[str] = []
    for r in app.url_map.iter_rules():
        rule = str(r)
        if "GET" not in (r.methods or set()):
            continue
        if not _is_page_rule(rule):
            continue
        if "<" in rule:
            # /<path:subpath> is the SPA fallback of an exact rule we
            # already track ("/"); /m2/<path:subpath> is the redirect
            # shim's deep-link twin. Skip param twins whose prefix IS
            # an exact rule / shim.
            prefix = rule.split("<", 1)[0]
            if _norm(prefix) in {"/m2", "/"}:
                continue
            param_rules.append(rule)
        else:
            exact_rules.add(_norm(rule))

    client = app.test_client()
    dist_present = (ROOT / "frontend" / "dist" / "index.html").is_file()

    visited: set[str] = set()
    all_hrefs: set[str] = set()
    frontier = sorted(_CRAWL_ROOTS)
    while frontier:
        path = _norm(frontier.pop())
        if path in visited:
            continue
        visited.add(path)
        try:
            resp = client.get(path, follow_redirects=False)
        except Exception as e:  # noqa: BLE001 — a crashing page is a finding
            if verbose:
                print(f"  ! GET {path} raised {type(e).__name__}: {e}")
            continue
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location", "")
            if loc:
                tgt = _norm(urljoin(path, loc))
                all_hrefs.add(tgt)
                if tgt in exact_rules:
                    frontier.append(tgt)
            continue
        ctype = (resp.headers.get("Content-Type") or "")
        if resp.status_code != 200 or "html" not in ctype:
            continue
        parser = _HrefParser()
        parser.feed(resp.get_data(as_text=True))
        for h in parser.hrefs:
            tgt = _norm(urljoin(path + "/", h))
            all_hrefs.add(tgt)
            if tgt in exact_rules and tgt not in visited:
                frontier.append(tgt)

    orphans: list[str] = []
    for rule in sorted(exact_rules):
        if rule in _CRAWL_ROOTS:
            continue
        if rule in _REDIRECT_SHIMS or _norm(rule) in _REDIRECT_SHIMS:
            continue
        if _is_typed_url_only(rule):
            continue
        if rule in visited:
            continue
        # discovered-but-not-200 (e.g. / -> 503 without dist) still counts
        # as linked; the link is what the gate protects.
        if rule in all_hrefs:
            if rule == "/" and not dist_present:
                continue
            # linked but the GET failed — surface it, that's a real finding
            orphans.append(f"SERVER linked-but-unrenderable: {rule}")
            continue
        orphans.append(f"SERVER orphan (no inbound link from a crawl root): {rule}")

    src_cache: dict[str, str] = {}

    def _source_has_prefix(prefix: str) -> bool:
        # POPULATION: PRODUCT-ONLY, and *.tsx-only. This is the FALLBACK
        # evidence that a parametrised server route is linked from somewhere,
        # so anything inside it can suppress an orphan report. The Python half
        # already excludes tests/ -- it walks only bulk_downloader/ and tools/
        # -- and the frontend half must agree: a route named by a Vitest spec
        # is not a route the product links to. The suffix set is NOT widened
        # to *.ts here on purpose; more voucher files would mean FEWER orphans
        # reported, which is a silent loosening. See tools/spa_population.py.
        for base in ("bulk_downloader", "tools", "frontend/src"):
            d = ROOT / base
            if not d.exists():
                continue
            if base == "frontend/src":
                candidates = spa_population.product_files(d, ("*.tsx",))
            else:
                candidates = sorted(d.rglob("*.py"))
            for p in candidates:
                key = str(p)
                if key not in src_cache:
                    try:
                        src_cache[key] = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        src_cache[key] = ""
                if prefix in src_cache[key]:
                    return True
        return False

    for rule in sorted(param_rules):
        prefix = _norm(rule.split("<", 1)[0])
        if prefix == "/":
            continue
        hit = any(h.startswith(prefix + "/") or h == prefix for h in all_hrefs)
        if hit:
            continue
        # fall back to a source-level reference (e.g. url_for-built links
        # that only render when data exists, like /framework/report/<name>)
        if not _source_has_prefix(prefix):
            orphans.append(f"SERVER param-rule orphan (no inbound prefix): {rule}")

    if verbose:
        print(f"  server: {len(exact_rules)} exact rules, {len(param_rules)} param rules, "
              f"{len(visited)} pages crawled, {len(orphans)} orphans")
    return orphans


# ── 2. SPA static audit ──────────────────────────────────────────────────────

_NAV_CTX = re.compile(r"\bto=|\bto:\s*[\"']|navigate\(|\bgo\(|href=|<Link|Navigate")


# Server-rendered pages that are NOT React routes, so neither check_spa (parses
# App.tsx routes) nor check_server (a server-side HTML crawl of `/` can't see the
# client-rendered SPA nav) can govern their reachability. They are surfaced to the
# operator as EXTERNAL nav links in the SPA (rendered as <a href> targets, not
# react-router <NavLink>, so a click leaves the SPA and loads the server page).
# This check asserts those external entries exist in navGroups.ts so a click path
# to each server console survives a nav refactor. See GUI_CONFIG_BUCKETS_PLAN_DEEP
# (nav-orphan fix) — un-exempting these from check_server would be wrong (a client
# SPA link can't satisfy a server crawl); the static assertion is the correct gate.
_EXTERNAL_NAV_REQUIRED = ("/framework", "/fleet", "/cockpit")


def check_external_nav(verbose: bool = False) -> list[str]:
    """Assert navGroups.ts declares an external:true nav entry for each
    server-rendered console page. Returns a list of missing entries (empty = OK)."""
    nav_ts = ROOT / "frontend" / "src" / "lib" / "navGroups.ts"
    if not nav_ts.is_file():
        return [f"external-nav: missing {nav_ts}"]
    src = nav_ts.read_text(encoding="utf-8", errors="replace")
    missing: list[str] = []
    for path in _EXTERNAL_NAV_REQUIRED:
        # an object literal naming this `to:` AND carrying external:true. Tolerant
        # of key order / whitespace within the single item object {...}.
        item_re = re.compile(
            r"\{[^{}]*?\bto:\s*\"" + re.escape(path) + r"\"[^{}]*?\}", re.S)
        ext = False
        for m in item_re.finditer(src):
            if re.search(r"\bexternal:\s*true\b", m.group(0)):
                ext = True
                break
        if not ext:
            missing.append(
                f"external-nav orphan (no external:true navGroups entry): {path}")
    if verbose:
        print(f"  external-nav: {len(_EXTERNAL_NAV_REQUIRED)} required, "
              f"{len(missing)} missing")
    return missing


def check_spa(verbose: bool = False) -> list[str]:
    src_dir = ROOT / "frontend" / "src"
    app_tsx = src_dir / "App.tsx"
    if not app_tsx.is_file():
        return [f"SPA: missing {app_tsx}"]
    app_src = app_tsx.read_text(encoding="utf-8", errors="replace")

    routes: dict[str, str] = {}  # path -> component name
    for m in re.finditer(r'<Route\s+path="([^"]+)"\s+element=\{<(\w+)', app_src):
        routes[m.group(1)] = m.group(2)

    # POPULATION: PRODUCT-ONLY. The question is "can a USER reach this route by
    # clicking?", so a <Link to="/x"> inside a Vitest spec must not answer it --
    # a spec never ships and nobody clicks it. See tools/spa_population.py.
    files = {str(p): p.read_text(encoding="utf-8", errors="replace")
             for p in spa_population.product_files(src_dir, ("*.tsx", "*.ts"))}

    orphans: list[str] = []
    for route, comp in sorted(routes.items()):
        if route in ("/", "*"):
            continue
        own = f"routes/{comp}.tsx"
        if ":" in route:
            tail = route.split(":", 1)[1]
            tail = "/" + tail.split("/", 1)[1] if "/" in tail else None
            needle = tail  # e.g. '/inspect', '/actions'; None for bare :param
        else:
            needle = route
        found = False
        for fn, txt in files.items():
            if fn.endswith("App.tsx") or fn.endswith(own):
                continue
            for line in txt.splitlines():
                ls = line.strip()
                if ls.startswith("//") or ls.startswith("*") or "/api/" in ls:
                    continue
                if not _NAV_CTX.search(ls):
                    continue
                if needle is None:
                    if re.search(r"/sites/\$\{|`/sites/", ls):
                        found = True
                        break
                elif needle in ls and re.search(re.escape(needle) + r"(?![\w-])", ls):
                    found = True
                    break
            if found:
                break
        if not found:
            orphans.append(f"SPA orphan (no inbound nav link): {route} ({comp})")

    if verbose:
        print(f"  spa: {len(routes)} routes, {len(orphans)} orphans")
    return orphans


# ── entrypoint ───────────────────────────────────────────────────────────────

def run_check(verbose: bool = True) -> int:
    orphans = (check_spa(verbose=verbose) + check_server(verbose=verbose)
               + check_external_nav(verbose=verbose))
    if orphans:
        print("NAV REACHABILITY: FAIL")
        for o in orphans:
            print(f"  - {o}")
        return 1
    print("NAV REACHABILITY: PASS (every page reachable by clicks from a crawl root)")
    return 0


if __name__ == "__main__":
    sys.exit(run_check(verbose=("--check" in sys.argv or "-v" in sys.argv)))
