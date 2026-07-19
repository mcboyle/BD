"""Phase 1 of the live-capture element-pick bridge (noVNC workflow).

The operator clicks an element on the held-open live page (a real click
forwarded through the noVNC canvas into the remote Chromium). The page itself
identifies what was clicked via ``document.elementFromPoint`` and derives a
robust CSS selector IN-PAGE -- no canvas->viewport coordinate mapping. This
suite proves the in-page selector deriver (``bulk_downloader.element_pick``
``PICK_SELECTOR_JS``).

A *picked* selector must look like a *built* one (mirror
``tools/build_template_from_wacz.py::_html_selectors``):
  * prefer a STABLE ``tag#id`` (``input#username``, ``input#user-password``),
  * else ``tag.class[data-attr]...`` combos (``a.ct_dl_button[data-framerate="60"]``),
    minimal-but-unique,
  * never lean on a volatile/hashed id,
  * report visibility (FIX-1: decoys are hidden) + uniqueness so the surface
    can warn before the selector lands in the draft.

Sandbox: runs in Playwright headless against DOM fixtures (no network, no
fetch -- ``set_content`` is fine here because the deriver is pure in-page).
The *real* noVNC-forwarded click only proves on stash; this proves the logic.
"""

import os
from contextlib import contextmanager

CHROME = os.environ.get(
    "BD_PW_CHROME",
    "/home/claude/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
)


def _launch(p):
    """Portable chromium launch: explicit path first (so this RUNS in the
    sandbox, where Playwright's default launch can't find chrome-headless-shell),
    then Playwright's default install (so it RUNS on stash), else SKIP. Mirrors
    tests/test_dom_recorder_asi._launch -- a missing browser must SKIP, never
    FAIL (these test selector LOGIC, not browser availability)."""
    import os as _os
    _args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and _os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=_args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=_args)
    except Exception as e:
        import pytest
        pytest.skip(f"chromium not launchable here: {e}")


@contextmanager
def _pw_page(html=None):
    """One Playwright context per call, opened+closed in scope. Per-test (not a
    process-wide singleton) so two sync contexts never overlap -- which would
    trip Playwright's asyncio-loop guard when the full suite runs this file
    alongside other Playwright tests (e.g. test_e2e_smoke)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = _launch(p)
        try:
            pg = br.new_page(viewport={"width": 1000, "height": 760})
            if html is not None:
                pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


def _derive(html, test_sel):
    """Render ``html``, pick the element matched by ``test_sel`` (a test-only
    ``[data-test=...]`` hook that is stripped before derivation), and return the
    deriver's result dict for that element."""
    from bulk_downloader.element_pick import PICK_SELECTOR_JS
    with _pw_page(html) as pg:
        return pg.evaluate(
            """(ts) => {
                %s
                const el = document.querySelector(ts);
                if (!el) throw new Error('test hook not found: ' + ts);
                el.removeAttribute('data-test');   // never let the test hook leak into output
                return bdPickSelector(el);
            }""" % PICK_SELECTOR_JS,
            test_sel,
        )


# ── fixtures: the wowgirls direct-link archetype + bangbros login ──────────

# Three inline download rows sharing a class; two share framerate=30 (one of
# those is also res=720) -> exercises minimal-but-unique multi-attr selection.
# Plus a hidden decoy anchor (FIX-1) and a login form (#username + #user-password
# -- bangbros uses a hyphenated password id, which must still be grabbed).
_WG = """
<!doctype html><html><body>
  <div class="content_download">
    <a class="ct_dl_button" data-framerate="60" data-res="1080" data-test="row60">1080p 60fps</a>
    <a class="ct_dl_button" data-framerate="30" data-res="1080" data-test="row30a">1080p 30fps</a>
    <a class="ct_dl_button" data-framerate="30" data-res="720"  data-test="row720">720p 30fps</a>
  </div>
  <a download class="thumb-grab" style="display:none" data-test="decoy">hidden grab</a>
  <form>
    <input id="username" type="text" data-test="user">
    <input id="user-password" type="password" data-test="pw">
    <button id="btn_a1b2c3d4e5f6a7b8" data-test="vol">Sign in</button>
  </form>
  <section><article><div><p>x</p><p data-test="plain">deep</p></div></article></section>
</body></html>
"""


def _matches(html, sel):
    """How many elements ``sel`` selects in ``html`` (independent re-render)."""
    with _pw_page(html) as pg:
        return pg.eval_on_selector_all(sel, "els => els.length")


# ── tests ──────────────────────────────────────────────────────────────────

def test_visible_download_row_uses_class_plus_distinguishing_dataattr():
    r = _derive(_WG, "[data-test='row60']")
    sel = r["selector"]
    assert r["unique"] is True and r["count"] == 1, r
    assert r["visible"] is True, r
    # built-like: tag + the meaningful class + the distinguishing data-attr
    assert sel.startswith("a.ct_dl_button"), sel
    assert 'data-framerate="60"' in sel, sel
    # and it really is minimal-but-unique against the live DOM
    assert _matches(_WG, sel) == 1, sel


def test_ambiguous_row_adds_attrs_until_unique():
    # 720p/30 shares both class AND framerate=30 with the 1080p/30 row, so the
    # deriver must add res=720 to disambiguate (the mockup's exact target).
    r = _derive(_WG, "[data-test='row720']")
    sel = r["selector"]
    assert r["unique"] is True and r["count"] == 1, r
    assert 'data-framerate="30"' in sel and 'data-res="720"' in sel, sel
    assert _matches(_WG, sel) == 1, sel


def test_hidden_decoy_is_flagged_not_visible():
    r = _derive(_WG, "[data-test='decoy']")
    # The deriver still returns a selector, but visible=False lets the surface
    # warn before a decoy lands in the draft (FIX-1 is a runtime skip; the pick
    # surface should not silently grab an invisible row).
    assert r["visible"] is False, r


def test_stable_id_login_field_is_tag_hash_id():
    r = _derive(_WG, "[data-test='user']")
    assert r["selector"] == "input#username", r
    assert r["unique"] is True, r


def test_hyphenated_password_id_is_grabbed():
    # bangbros: #user-password (NOT #password). A hyphenated stable id is fine.
    r = _derive(_WG, "[data-test='pw']")
    assert r["selector"] == "input#user-password", r


def test_volatile_id_is_rejected_falls_back():
    # id="btn_a1b2c3d4e5f6a7b8" looks hashed -> must NOT appear in the selector.
    r = _derive(_WG, "[data-test='vol']")
    assert "a1b2c3d4e5f6a7b8" not in r["selector"], r
    assert r["unique"] is True and r["count"] == 1, r
    assert _matches(_WG, r["selector"]) == 1, r


def test_plain_element_gets_scoped_unique_path():
    # No id, no class -> scoped nth-of-type path, still uniquely selecting it.
    r = _derive(_WG, "[data-test='plain']")
    assert r["unique"] is True and r["count"] == 1, r
    assert _matches(_WG, r["selector"]) == 1, r


if __name__ == "__main__":  # allow direct run for quick iteration
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
    print("ALL PASS")
