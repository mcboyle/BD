"""v3.66.292 — UI-DAL: dead in-app hash-href gate (class-wide).

Field bug: the "Open DOM analyzer / workbench →" control in
`CaptureWorkflow.tsx` rendered as `<a href="#/dom-analyzer">`. The SPA is a
PATH-based router (`main.tsx`: `<BrowserRouter basename="/">`), so a `#/…`
href is only an in-page hash fragment — clicking it appends `#/dom-analyzer`
to the current path (`/capture#/dom-analyzer`) and never navigates. The
`/dom-analyzer` route itself is fine and is reached correctly elsewhere via
`<Link to="/dom-analyzer">` (Settings.tsx) and `go("/dom-analyzer")`
(CommandPalette.tsx); only this one shortcut was a dead link. `nav_reachability`
did not catch it because the route HAS inbound links — this link is broken, not
missing.

This is a CLASS gate, not a one-link assertion: on a path-based BrowserRouter,
ANY `href="#/…"` (an internal app path behind a hash) is structurally dead.
Scanning the whole SPA, exactly one such href exists today (the bug). The gate
fails RED on it and, once fixed, prevents the entire class from recurring.

Scope note: this targets only INTERNAL hash-path hrefs (`href="#/..."`). It does
NOT touch a bare `href="#"` (a deliberate no-op placeholder) or real fragment
anchors like `href="#section-id"`; the `#/` shape specifically means "a route
path stuck behind a hash", which only an in-app navigation would intend.

RED on pristine v3.66.291 (proven before implementing):
  * `test_no_dead_hash_path_hrefs` fails — CaptureWorkflow.tsx carries
    `href="#/dom-analyzer"`.
  * `test_dom_analyzer_link_is_router_link` fails — that control is an `<a>`,
    not a `<Link to="/dom-analyzer">`.
GREEN after the `<a href="#/dom-analyzer">` → `<Link to="/dom-analyzer">` fix
(plus the `react-router-dom` Link import).

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

SPA_SRC = REPO / "frontend" / "src"
CAPTURE = SPA_SRC / "routes" / "CaptureWorkflow.tsx"

# An internal app path stuck behind a hash, e.g. href="#/dom-analyzer".
# Matches single- or double-quoted. Deliberately does NOT match a bare
# href="#" placeholder or a real fragment anchor href="#section".
_DEAD_HASH_HREF = re.compile(r'href\s*=\s*["\']#/')


def _tsx_files():
    return sorted(SPA_SRC.rglob("*.tsx"))


def test_spa_src_present():
    """Sanity: the SPA source tree and the workflow file exist, so a future
    move can't silently turn this gate into a vacuous pass."""
    files = _tsx_files()
    assert files, f"no .tsx files found under {SPA_SRC}"
    assert CAPTURE.exists(), f"missing {CAPTURE}"


def test_no_dead_hash_path_hrefs():
    """No .tsx in the SPA may use href="#/…". On a path-based BrowserRouter
    that is a dead link (in-page hash fragment, no navigation). RED on 291:
    CaptureWorkflow.tsx has href="#/dom-analyzer"."""
    offenders = []
    for f in _tsx_files():
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if _DEAD_HASH_HREF.search(line):
                rel = f.relative_to(REPO)
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, (
        "dead in-app hash-path hrefs (href=\"#/…\") on a path-based router — "
        "use <Link to=\"/route\"> instead:\n  " + "\n  ".join(offenders)
    )


def test_dom_analyzer_link_is_router_link():
    """The DOM-analyzer shortcut in CaptureWorkflow.tsx must be a React-Router
    <Link to="/dom-analyzer"> (the Settings.tsx / LogDiff.tsx pattern), not an
    <a href="#/dom-analyzer">. RED on 291."""
    src = CAPTURE.read_text(encoding="utf-8")
    assert 'href="#/dom-analyzer"' not in src, (
        'CaptureWorkflow.tsx still uses the dead href="#/dom-analyzer"'
    )
    assert re.search(r'<Link\b[^>]*\bto="/dom-analyzer"', src), (
        'CaptureWorkflow.tsx should navigate to the DOM analyzer via '
        '<Link to="/dom-analyzer">'
    )
    # And the Link import must be present so the component compiles.
    assert re.search(
        r'import\s*\{[^}]*\bLink\b[^}]*\}\s*from\s*["\']react-router-dom["\']',
        src,
    ), "CaptureWorkflow.tsx must import { Link } from 'react-router-dom'"
