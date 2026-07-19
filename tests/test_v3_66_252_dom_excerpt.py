"""F2.7c -- live DOM-excerpt for the capture-console AI assist.

The /capture console's "AI suggestion" previously sent ``dom_excerpt: ""`` and
crammed everything into ``context_hint``. F2.7c feeds it a REAL excerpt of the
live page, pulled over the same held-open capture session as the element pick --
a new ``dom`` / ``dom_poll`` action on ``/cockpit/api/captures/pick`` driven by
the SAME cross-process sentinel pattern as arm/poll (DOM_REQUEST -> the capture's
``_pump_dom`` reads ``outerHTML`` -> DOM_RESULT.json -> Flask reads-and-clears).

No new ROUTE (a new action on the existing pick POST) -> the cockpit count-pins
and test_v3_66_98's POST-SET are untouched.

F2 posture: the excerpt is the operator's own authenticated session DOM going to
the operator's own configured assistant, but credential VALUES (JWTs, token=/
Signature= query params, bearer) are scrubbed from the excerpt before it leaves
the capture process -- structure (and therefore selectors) is preserved.

RED on 251 (the action + element_pick.maybe_collect_dom + the scrub do not exist).
"""

import os
import tempfile
from pathlib import Path

CHROME = os.environ.get(
    "BD_PW_CHROME",
    "/home/claude/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome",
)


def _launch(p):
    """Portable chromium launch (explicit sandbox path -> default -> skip),
    mirroring test_element_pick_bridge._launch. Never FAIL on a missing browser."""
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(headless=True, timeout=20000, args=args,
                                     executable_path=CHROME)
        except Exception:
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:
        import pytest
        pytest.skip(f"chromium not launchable here: {e}")


_FIX = """
<!doctype html><html><body>
  <div class="content_download">
    <a class="ct_dl_button" data-token="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"
       href="https://cdn.example.com/v.mp4?Signature=ABCDEF123&Expires=99">DL</a>
  </div>
</body></html>
"""


# ── element_pick: the cross-process DOM sentinel protocol ───────────────────

def test_dom_request_collect_consume_roundtrip():
    from bulk_downloader import element_pick as ep
    from playwright.sync_api import sync_playwright
    out = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        br = _launch(p)
        try:
            pg = br.new_page()
            pg.set_content(_FIX, wait_until="load")

            # Flask side: request a DOM excerpt.
            assert ep.request_dom(out) is True
            assert ep.dom_requested(out) is True
            assert ep.consume_dom_result(out) is None  # nothing serviced yet

            # Capture on_tick: reads outerHTML, writes DOM_RESULT, clears request.
            got = ep.maybe_collect_dom([pg], out)
            assert got is not None
            assert "content_download" in got["html"]       # structure preserved
            assert "ct_dl_button" in got["html"]
            assert ep.dom_requested(out) is False
            assert ep.dom_result_path(out).exists()

            # Flask side: read-and-clear.
            consumed = ep.consume_dom_result(out)
            assert consumed is not None and "content_download" in consumed["html"]
            assert not ep.dom_result_path(out).exists()
            assert ep.consume_dom_result(out) is None
        finally:
            br.close()


def test_dom_collect_is_noop_when_not_requested():
    from bulk_downloader import element_pick as ep
    from playwright.sync_api import sync_playwright
    out = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        br = _launch(p)
        try:
            pg = br.new_page()
            pg.set_content(_FIX, wait_until="load")
            assert ep.maybe_collect_dom([pg], out) is None
            assert not ep.dom_result_path(out).exists()
        finally:
            br.close()


def test_dom_excerpt_scrubs_credential_values_but_keeps_structure():
    """Hard creds (JWT, signed-query Signature=) are stripped from the excerpt;
    the tag/class structure the AI reasons over is intact."""
    from bulk_downloader import element_pick as ep
    from playwright.sync_api import sync_playwright
    out = Path(tempfile.mkdtemp())
    with sync_playwright() as p:
        br = _launch(p)
        try:
            pg = br.new_page()
            pg.set_content(_FIX, wait_until="load")
            ep.request_dom(out)
            got = ep.maybe_collect_dom([pg], out)
            html = got["html"]
            # structure preserved
            assert "ct_dl_button" in html and "data-token" in html
            # credential VALUES gone
            assert "eyJhbGciOiJIUzI1NiJ9" not in html          # JWT head
            assert "Signature=ABCDEF123" not in html           # signed query
        finally:
            br.close()


# ── cockpit_core: the dom / dom_poll action on the existing pick route ──────

def test_pick_capture_dom_action_requests_and_polls():
    from tools import cockpit_core as cc
    out = Path(tempfile.mkdtemp())
    _orig = cc.get_task
    cc.get_task = lambda tid: {"category": "capture", "out_dir": str(out)}
    try:
        r = cc.pick_capture("t1", action="dom")
        assert r["action"] == "dom" and r["requested"] is True
        # the request sentinel is now present (capture services it next tick)
        from bulk_downloader import element_pick as ep
        assert ep.dom_requested(out) is True

        # nothing serviced yet -> poll returns no result
        r2 = cc.pick_capture("t1", action="dom_poll")
        assert r2["action"] == "dom_poll" and r2["result"] is None

        # simulate the capture servicing it
        ep.dom_result_path(out).write_text(
            '{"html": "<div class=\\"x\\"></div>", "url": "https://s/"}',
            encoding="utf-8")
        r3 = cc.pick_capture("t1", action="dom_poll")
        assert r3["result"] is not None and "class=\"x\"" in r3["result"]["html"]
    finally:
        cc.get_task = _orig


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
    print("ALL PASS")
