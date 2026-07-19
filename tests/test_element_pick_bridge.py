"""Phase 2 of the live element-pick bridge: the ACTIVE one-shot pick.

Distinct from the existing OBSERVATIONAL picker (``dom_overlay.picker_script``,
``{passive:true}``, records every click as workflow descriptors). The active
pick is what the mockup's "Pick from live page" does:

  * one-shot: arm -> the operator clicks ONE element -> a finished selector is
    grabbed into the armed draft field -> mode returns to interact;
  * preventDefault: the grab MUST NOT let the click through, or picking the
    download row would fire a real download / navigate the live page;
  * cross-process: the capture runs as a separate process holding the page;
    Flask arms it and reads the result via filesystem sentinels in the capture
    out_dir (same pattern as FINISH/CANCEL). The capture's per-second on_tick
    (``_pump_dom``) injects the listener while armed and drains the result.

Sandbox boundary (honest): everything here is proven with Playwright headless
by standing in a real ``page.click`` for the operator's noVNC-forwarded click.
Once the click reaches the page the two are identical; only the human finger on
the noVNC canvas is un-testable in-sandbox.
"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

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

_FIX = """
<!doctype html><html><body>
  <div class="content_download">
    <a class="ct_dl_button" data-framerate="60" data-res="1080"
       href="#went">1080p 60fps</a>
    <a class="ct_dl_button" data-framerate="30" data-res="720"
       href="#went30">720p 30fps</a>
  </div>
</body></html>
"""


@contextmanager
def _pw_page(html=_FIX):
    """One Playwright context per test, opened+closed in scope (never a
    process-wide singleton), so two sync contexts never overlap when the full
    suite runs this alongside other Playwright tests."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = _launch(p)
        try:
            pg = br.new_page(viewport={"width": 1000, "height": 760})
            pg.set_content(html, wait_until="load")
            yield pg
        finally:
            br.close()


# ── active-pick in-page behavior ────────────────────────────────────────────

def test_armed_click_prevents_default_and_grabs_finished_selector():
    from bulk_downloader import element_pick as ep
    with _pw_page() as pg:
        ep.inject_active_pick(pg)            # arm the live document
        pg.click('a.ct_dl_button[data-framerate="60"]')                   # stand-in for the noVNC click
        # preventDefault worked => the href="#went" navigation did NOT happen
        assert "#went" not in pg.url, pg.url
        r = ep.read_active_pick(pg)
        assert r is not None, "no pick captured"
        assert 'data-framerate="60"' in r["selector"], r
        assert r["unique"] is True and r["visible"] is True, r
        # one-shot: a second click after the grab produces nothing new
        assert ep.read_active_pick(pg) is None


def test_unarmed_click_is_not_captured():
    from bulk_downloader import element_pick as ep
    with _pw_page() as pg:
        pg.click('a.ct_dl_button[data-framerate="60"]')                   # no inject -> no active listener
        assert ep.read_active_pick(pg) is None


# ── cross-process sentinel protocol (out_dir files like FINISH/CANCEL) ──────

def test_arm_collect_consume_roundtrip():
    from bulk_downloader import element_pick as ep
    out = Path(tempfile.mkdtemp())
    with _pw_page() as pg:
        # Flask side: arm.
        ep.arm(out)
        assert ep.is_armed(out) is True
        assert ep.consume_result(out) is None  # nothing yet

        # Capture on_tick BEFORE the click: injects, finds no result, arm stays.
        assert ep.maybe_arm_and_collect([pg], out) is None
        assert ep.is_armed(out) is True

        # Operator clicks.
        pg.click('a.ct_dl_button[data-framerate="60"]')

        # Capture on_tick AFTER the click: drains the in-page result, writes
        # PICK_RESULT.json, clears the arm sentinel.
        got = ep.maybe_arm_and_collect([pg], out)
        assert got is not None and 'data-framerate="60"' in got["selector"], got
        assert ep.is_armed(out) is False
        assert ep.result_path(out).exists()

        # Flask side: consume (read + delete).
        consumed = ep.consume_result(out)
        assert consumed == got
        assert not ep.result_path(out).exists()
        assert ep.consume_result(out) is None


def test_collect_is_noop_when_not_armed():
    from bulk_downloader import element_pick as ep
    out = Path(tempfile.mkdtemp())
    with _pw_page() as pg:
        # No arm sentinel -> no injection, no result, no file written.
        assert ep.maybe_arm_and_collect([pg], out) is None
        pg.click('a.ct_dl_button[data-framerate="60"]')
        assert ep.read_active_pick(pg) is None     # listener was never installed
        assert not ep.result_path(out).exists()



if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
    print("ALL PASS")
