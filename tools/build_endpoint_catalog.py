#!/usr/bin/env python3
"""build_endpoint_catalog.py — KB_PLAN_v2 B.2.

Generates ENDPOINT_CATALOG.md from app.url_map after importing
bulk_downloader.app with BD_DISABLE_KEEPALIVE=1. The catalog is a
flat, sorted, greppable index of every registered route the running
server exposes — including routes registered dynamically by the
VPN / widgets / captcha / live-recorder API blueprints, since those
fire at module import time.

Output format (one row per method+path combination):

    GET    /api/status                       CSRF: no    — site state snapshot
    POST   /api/queue/v2/cancel              CSRF: yes   — single job cancel
    ...

CSRF column reflects what `_check_csrf` (the `@app.before_request`
hook in bulk_downloader.app) actually enforces. The hook fires iff:

  - HTTP method is one of POST/PUT/PATCH/DELETE
  - request.path starts with `/api/`
  - request.path is NOT `/api/pair/redeem` (the cookie-bootstrap
    exception)
  - request has a `bd_session` cookie and no Bearer token (those are
    auth-mode-dependent and not route-static, so the catalog reports
    the *route-static* answer — i.e. "would CSRF fire if a cookie-
    based browser session called this method?")

So `CSRF: yes` means "this method+path requires a valid X-CSRF-Token
header from a browser session." `CSRF: no` means it does not.

Description column is the view function's docstring first line,
truncated to 100 chars. Routes whose view function has no docstring
get an empty description.

Usage:

    # Regenerate the catalog from current source. Writes
    # ENDPOINT_CATALOG.md at repo root. Exits 0 on success.
    python tools/build_endpoint_catalog.py

    # Check-only mode: regenerate in-memory, diff against on-disk,
    # exit 0 if identical, 1 if drift detected, 2 on hard error.
    # Used by the drift test and build_release.py's gate.
    python tools/build_endpoint_catalog.py --check

    # Print to stdout instead of writing the file. Useful for piping.
    python tools/build_endpoint_catalog.py --stdout

Implementation notes:

  - `BD_DISABLE_KEEPALIVE=1` is set in this script's environment
    BEFORE the `from bulk_downloader.app import app` import, so the
    background subsystems (session keeper, scheduler, VPN watchdog
    etc.) never start. We get a fully-registered url_map without the
    side effects.

  - Routes are grouped into two sections — `/api/*` first, then
    everything else (frontends `/m`, `/m2`; assets `/static`,
    `/icon.svg`, etc.; the operational endpoint `/metrics`). Within
    each section, sort is purely by path then method.

  - Flask auto-adds OPTIONS and HEAD methods to every route. The
    catalog skips them — they're noise, not user-facing endpoints.
"""
import argparse
import difflib
import importlib
import os
import sys
from pathlib import Path
from typing import Iterable


# These methods are auto-added by Flask and not user-facing. Skip
# them so the catalog doesn't double or triple every row.
_BORING_METHODS = frozenset({"HEAD", "OPTIONS"})

# v3.66.748 (audit R11-13) -- THE CSRF POLICY IS IMPORTED, NEVER RE-TYPED.
#
# These used to be local copies of the rule in `_check_csrf`, and one of them
# drifted: the app brought "/cockpit/api/" under the guard (PHC-1) and this
# module never learned, so 28 protected cockpit write endpoints were reported
# `csrf: false` by ROUTE_INDEX, ENDPOINT_CATALOG and -- the one that ships --
# the OpenAPI spec at /api/openapi.json, which therefore omitted the required
# X-CSRF-Token header and the 403 response for them.
#
# Worse than the wrong column: because the artifact already said `false`,
# DROPPING the guard from the hook would have changed no artifact and tripped no
# gate. A mirror wrong in the safe direction cannot detect a move in the
# dangerous one.
#
# They are now the app's own objects -- bound LAZILY, because importing
# bulk_downloader.app at module scope would beat `_import_app`'s
# BD_DISABLE_KEEPALIVE=1 and spawn the background subsystems this tool exists to
# avoid starting. `_bind_csrf_policy()` is called by `_import_app()` (and by
# `_csrf_fires_for` on first use, so a direct caller cannot get a stale None).
# test_v3_66_748 asserts identity (not equality) against the app's objects and
# cross-validates the predicate against the REAL hook for every mutating route.
_CSRF_TRIPPING_METHODS = None
_CSRF_GUARDED_PREFIXES = None
_CSRF_EXEMPT_PATHS = None


def _bind_csrf_policy():
    """Bind this module's CSRF policy to the app's -- the same objects, not
    copies. Safe to call repeatedly; sets BD_DISABLE_KEEPALIVE first."""
    global _CSRF_TRIPPING_METHODS, _CSRF_GUARDED_PREFIXES, _CSRF_EXEMPT_PATHS
    if _CSRF_GUARDED_PREFIXES is not None:
        return
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import bulk_downloader.app as _A
    _CSRF_TRIPPING_METHODS = _A.CSRF_TRIPPING_METHODS
    _CSRF_GUARDED_PREFIXES = _A.CSRF_GUARDED_PREFIXES
    _CSRF_EXEMPT_PATHS = _A.CSRF_EXEMPT_PATHS


# Max description length (docstring first line). 100 chars matches
# the catalog format example in KB_PLAN_v2 B.2 — keeps each row on
# one terminal line at 132-col widths.
_DESC_MAX = 100


def _repo_root() -> Path:
    """This script lives at <root>/tools/build_endpoint_catalog.py."""
    return Path(__file__).resolve().parent.parent


def _import_app():
    """Import bulk_downloader.app with keepalive disabled.

    `BD_DISABLE_KEEPALIVE=1` is set in os.environ before importing so
    the background subsystems (session_keeper, scheduler, VPN
    watchdog, etc.) never start. The app.url_map is fully populated
    by import-time blueprint registration, so we get every route
    without spawning any threads.
    """
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    # Make repo root importable when invoked as `python tools/...`.
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    # Re-import path: invalidate caches so we pick up source changes
    # even if a prior import happened in the same process.
    importlib.invalidate_caches()
    from bulk_downloader.app import app  # noqa: E402 — env must be set first
    return app


def _csrf_fires_for(method: str, path: str) -> bool:
    """Static (route-only) answer to: would `_check_csrf` 403 a
    cookie-based browser session calling this method+path?

    v3.66.748: DELEGATES to the app's `csrf_fires_for` -- the same predicate the
    hook itself applies. This function used to re-implement the rule and drifted
    (see _bind_csrf_policy above). It is kept as the module's public name so
    every caller (ROUTE_INDEX, ENDPOINT_CATALOG, build_openapi) keeps working.
    """
    _bind_csrf_policy()
    import bulk_downloader.app as _A
    return _A.csrf_fires_for(method, path)


def _doc_first_line(view_fn) -> str:
    """Return the first non-empty stripped line of a view function's
    docstring, truncated to _DESC_MAX chars. Empty string if none."""
    doc = getattr(view_fn, "__doc__", None)
    if not doc:
        return ""
    for raw in doc.splitlines():
        line = raw.strip()
        if line:
            if len(line) > _DESC_MAX:
                line = line[: _DESC_MAX - 1] + "…"
            return line
    return ""


def _rows_for_app(app) -> list[tuple[str, str, bool, str]]:
    """Walk app.url_map and yield (method, path, csrf_fires, desc)
    tuples for every (method, path) pair worth showing.

    Returns a sorted list — sort key is (path, method) so multi-
    method routes stay grouped under one path.
    """
    rows: list[tuple[str, str, bool, str]] = []
    for rule in app.url_map.iter_rules():
        path = rule.rule
        view_fn = app.view_functions.get(rule.endpoint)
        desc = _doc_first_line(view_fn) if view_fn is not None else ""
        methods = sorted(m for m in (rule.methods or set())
                         if m not in _BORING_METHODS)
        if not methods:
            # Defensive — Flask should always populate at least GET,
            # but if a custom rule somehow has no concrete methods
            # we still want a row so the route is visible.
            methods = ["?"]
        for m in methods:
            rows.append((m, path, _csrf_fires_for(m, path), desc))
    # Sort by path first (so multi-method routes cluster) then method.
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows


def _partition(rows: Iterable[tuple[str, str, bool, str]]):
    """Split rows into (api_rows, other_rows). Both lists preserve
    incoming order, which is already (path, method) sorted."""
    api, other = [], []
    for r in rows:
        if r[1].startswith("/api/"):
            api.append(r)
        else:
            other.append(r)
    return api, other


def _format_table(rows: list[tuple[str, str, bool, str]]) -> str:
    """Render rows to the catalog's fixed-width column format.

    Column widths are computed from the data so a future route with
    a long path doesn't break alignment.
    """
    if not rows:
        return "(no routes in this section)\n"
    method_w = max(6, max(len(r[0]) for r in rows))
    path_w = max(20, max(len(r[1]) for r in rows))
    lines = []
    for method, path, csrf, desc in rows:
        csrf_s = "CSRF: yes" if csrf else "CSRF: no "
        prefix = f"{method:<{method_w}}  {path:<{path_w}}  {csrf_s}"
        if desc:
            lines.append(f"{prefix}  — {desc}")
        else:
            lines.append(prefix.rstrip())
    return "\n".join(lines) + "\n"


# Header banner — kept stable so the drift test can diff content
# without churning on text every release. If you edit this, raise
# CATALOG_SCHEMA_VERSION below by one and update the matching
# constant in test_endpoint_catalog_in_sync.py.
CATALOG_SCHEMA_VERSION = 1

_HEADER = """\
# ENDPOINT_CATALOG

Auto-generated by `tools/build_endpoint_catalog.py` (KB_PLAN_v2 B.2).
DO NOT EDIT BY HAND — `tests/test_endpoint_catalog_in_sync.py` will
fail the build. To regenerate after adding or removing a route:

    python tools/build_endpoint_catalog.py

`CSRF: yes` means a cookie-based browser session calling that
method+path must send a valid `X-CSRF-Token` header. `CSRF: no`
means the `_check_csrf` `@before_request` hook does not 403 the
request based on route (it may still reject on Bearer / session
absence, but those are auth-mode-dependent — see the script
docstring for the full rule).

The description column is the view function's docstring first line.
Routes with no docstring have an empty description — fixing that
is a documentation chore, not a catalog bug.

Schema version: {schema}
"""


def render_catalog(app) -> str:
    """Build the full ENDPOINT_CATALOG.md content for the given app.

    Returns the rendered Markdown as a single string with a trailing
    newline. Pure function once `app` is in hand — used by both the
    write path and the drift test.
    """
    rows = _rows_for_app(app)
    api, other = _partition(rows)
    out = []
    out.append(_HEADER.format(schema=CATALOG_SCHEMA_VERSION))
    out.append("")
    out.append(f"## /api/* routes ({len(api)})")
    out.append("")
    out.append("```")
    out.append(_format_table(api).rstrip("\n"))
    out.append("```")
    out.append("")
    out.append(f"## Non-API routes ({len(other)})")
    out.append("")
    out.append("```")
    out.append(_format_table(other).rstrip("\n"))
    out.append("```")
    out.append("")
    return "\n".join(out)


def _catalog_path() -> Path:
    """The canonical on-disk location for ENDPOINT_CATALOG.md."""
    return _repo_root() / "ENDPOINT_CATALOG.md"


def _write_if_changed(content: str, path: Path) -> bool:
    """Write `content` to `path` only if the file is missing or its
    current content differs. Returns True if a write happened.

    Atomic-write (`.tmp` sibling + `os.replace`) to keep the on-disk
    file from going through a half-written state if interrupted.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing == content:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _diff_text(have: str, want: str, label: str) -> str:
    """Unified diff of `have` vs `want` for human-readable error
    output. Capped at 60 lines so a totally-stale catalog doesn't
    flood the build log."""
    diff = list(difflib.unified_diff(
        have.splitlines(keepends=True),
        want.splitlines(keepends=True),
        fromfile=f"{label} (on disk)",
        tofile=f"{label} (regenerated)",
        n=2,
    ))
    if len(diff) > 60:
        diff = diff[:60] + [f"... (+{len(diff) - 60} more lines)\n"]
    return "".join(diff)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="diff regenerated content against the on-disk "
                         "ENDPOINT_CATALOG.md and exit 1 on drift; do "
                         "not write")
    ap.add_argument("--stdout", action="store_true",
                    help="print regenerated content to stdout instead "
                         "of writing the file (mutually exclusive with "
                         "--check)")
    args = ap.parse_args(argv)

    if args.check and args.stdout:
        print("FAIL: --check and --stdout are mutually exclusive",
              file=sys.stderr)
        return 2

    try:
        app = _import_app()
    except Exception as e:
        print(f"FAIL: could not import bulk_downloader.app: {e!r}",
              file=sys.stderr)
        return 2

    content = render_catalog(app)
    path = _catalog_path()

    if args.stdout:
        sys.stdout.write(content)
        return 0

    if args.check:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing == content:
            print(f"OK: {path.name} is in sync "
                  f"({len(content.splitlines())} lines)")
            return 0
        print(f"FAIL: {path.name} drift detected.", file=sys.stderr)
        print(_diff_text(existing, content, path.name), file=sys.stderr)
        print(f"Run `python tools/build_endpoint_catalog.py` to fix.",
              file=sys.stderr)
        return 1

    wrote = _write_if_changed(content, path)
    if wrote:
        print(f"WROTE: {path} "
              f"({len(content.splitlines())} lines)")
    else:
        print(f"OK: {path.name} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
