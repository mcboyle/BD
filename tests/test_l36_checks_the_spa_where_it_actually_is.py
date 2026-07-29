"""L36 asserted a contract the frontend was deliberately re-rooted away from.

THE DEFECT, measured on the box 2026-07-29 on L36's first ever run:

    [FAIL]  L36  m2-spa-bundle-served
            /m2/ returned HTML but no referenced JS/CSS asset under /m2/
            -- index.html appears not to be a Vite-built bundle

That is not a stale bundle. It is L36 asking for something that no longer
exists by design.

At v3.66.203 the D3 SPA moved from /m2 to `/`. `serve_m2_spa` is now a pure
302 deep-link shim -- its own docstring says "Vite base + router basename are
both '/' now (frontend re-rooted in this same cut), so no /m2-prefixed asset
URLs are emitted anymore". frontend/vite.config.{js,ts} set `base: "/"` and
main.tsx sets `<BrowserRouter basename="/">`.

So urlopen follows the 302 to `/`, receives the real SPA index.html, and finds
its assets under /assets/ rather than /m2/assets/. L36's regexes look only for
/m2/-prefixed paths, find none, and FAIL. **It cannot pass on a correct
deployment**, and it fails identically on a perfectly built dist.

That is CLAUDE.md section 0's inverse rule: a gate that fires on identity. Over-
sensitivity is a soundness bug, not a safe default -- a check that cries wolf on
a healthy box gets switched off, and then the thing it was guarding is
unguarded.

HOW IT GOT INTO A CAPTURE. L36 was registered but never selected, so it had
never run anywhere. The cut that derived EXPECTED_LIVE_TESTS from the registry
added it to LIVE_IDS on the strength of an audit note describing its failure
branches as the stale-frontend/dist class. That description was inherited rather
than re-derived against the source. Verify-then-act exists for exactly this.

WHAT L36 SHOULD CHECK. Its subject is still real and still uncovered: whether
the SPA bundle is actually SERVED. CLAUDE.md section 7 names frontend/dist/ as
the one thing a git deploy silently does not deliver -- it holds zero tracked
files and is gitignored, so a missing or stale bundle is a silent 503, and
capture.sh's own on-disk index.html check looks at a file rather than at what
the service serves. The fix points L36 at `/`, where the SPA now lives, and
keeps a separate assertion that /m2/ still redirects, because the deep-link shim
is itself a contract worth holding.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKS = REPO_ROOT / "live_tests" / "checks.py"
APP = REPO_ROOT / "bulk_downloader" / "app.py"


@pytest.fixture(scope="module")
def l36_source() -> str:
    body = CHECKS.read_text(encoding="utf-8")
    start = body.find("def l36_")
    if start < 0:
        pytest.fail("L36 not found in live_tests/checks.py; this gate cannot "
                    "verify its subject")
    nxt = body.find("\n@live_test", start)
    return body[start:nxt if nxt > 0 else len(body)]


# ── denominator canaries ─────────────────────────────────────────────────────

def test_l36_still_exists(l36_source):
    assert l36_source.strip(), "L36's body is empty; nothing below is meaningful"


def test_the_spa_really_did_move_off_m2():
    """If /m2 ever becomes the SPA root again, this whole file is wrong.

    The premise is not an opinion about routing -- it is read from the source
    that decides it. Pinning it here means a future re-rooting fails this test
    loudly instead of leaving L36 quietly pointed at the wrong path again.
    """
    vite = ""
    for name in ("vite.config.ts", "vite.config.js"):
        path = REPO_ROOT / "frontend" / name
        if path.is_file():
            vite = path.read_text(encoding="utf-8")
            break
    assert vite, "no frontend/vite.config.{ts,js} found; premise unverifiable"
    assert re.search(r'base:\s*["\']/["\']', vite), (
        "vite base is no longer '/'. The SPA may have moved again, and L36's "
        "target must be re-derived rather than assumed."
    )
    app_src = APP.read_text(encoding="utf-8")
    start = app_src.find("def serve_m2_spa")
    assert start > 0, "serve_m2_spa not found"
    body = app_src[start:start + 1400]
    assert "redirect(" in body, (
        "serve_m2_spa no longer redirects. If /m2 serves the SPA again, L36 "
        "should target it again -- and this test should be deleted with it."
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_l36_does_not_require_m2_prefixed_assets(l36_source):
    """The shape the frontend deliberately stopped emitting."""
    offenders = re.findall(r"/m2/assets/|/m2/\[\^", l36_source)
    assert not offenders, (
        "L36 still searches index.html for /m2/-prefixed asset paths. Since "
        "v3.66.203 the Vite base and router basename are both '/', so those "
        "paths are never emitted and the check FAILs on a correctly built, "
        "correctly deployed bundle."
    )


def test_l36_targets_the_served_spa_root(l36_source):
    """The check must fetch the SPA where the SPA is.

    The first version of this asserted `'"/"' in l36_source`, which matches
    almost any line and passed with the fetch pointed straight back at /m2/ --
    proven by mutation, not noticed by reading. A predicate that cannot fail is
    the defect this whole file is about, so it is stated concretely: the check
    fetches the root, and does not fetch /m2/ as its subject.
    """
    fetches_root = ('ctx.get("/", timeout' in l36_source
                    or "ctx.get('/', timeout" in l36_source)
    assert fetches_root, (
        "L36 does not fetch the SPA root. Its subject -- whether the bundle is "
        "actually served -- lives at '/' since v3.66.203."
    )
    assert 'ctx.get("/m2/"' not in l36_source, (
        "L36 still fetches /m2/ as its subject. That path is a 302 shim now; "
        "the bundle it must verify is served at the root."
    )
    assert 'base_url + "/m2/"' not in l36_source, (
        "L36 still opens /m2/ directly as its subject rather than the root."
    )


def test_l36_still_spot_checks_a_hashed_asset(l36_source):
    """Serving index.html proves nothing about a half-built dist.

    The original check was right that a fresh index.html over a stale assets/
    directory is the interesting failure. Keep that; only the prefix changes.
    """
    assert "assets" in l36_source, (
        "L36 no longer spot-checks a referenced asset. index.html alone cannot "
        "distinguish a complete bundle from a half-built one whose hashed "
        "assets 404."
    )


def test_l36_still_treats_a_missing_build_as_environmental(l36_source):
    """An unbuilt dist is a WARN, not a FAIL -- it is not a code defect."""
    assert "not-built" in l36_source, (
        "L36 no longer recognises the not-built surface. A deploy that simply "
        "has not run `npm run build` would then FAIL the capture instead of "
        "warning, which is the same over-sensitivity this cut is removing."
    )


def test_l36_still_holds_the_m2_redirect_contract(l36_source):
    """The shim is a real contract: old bookmarks and PWA start_urls use it."""
    assert "/m2" in l36_source, (
        "L36 no longer asserts anything about /m2. The 302 deep-link shim is "
        "load-bearing -- old bookmarks, the legacy shell's 'New UI' link, and "
        "any installed-PWA start_url that captured /m2 all depend on it -- and "
        "nothing else checks it."
    )
