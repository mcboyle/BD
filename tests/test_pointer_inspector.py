"""Row 946: overlapping z-index / pointer-events overlay inspector.

Zero-arg tests per run_tests.py conventions; repo root via __file__.
The pure half (classify_pointer_probe) is exercised on probe dicts shaped
exactly like the in-page _INSPECT_JS return value; the live half
(inspect_pointer_target) is proven against a stub locator. No browser, no
site, no login (Fleet Rule 21).
"""
import os
import pytest
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BD_GATE_SCOPE = "module"

os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_ptr_"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")


def _load():
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from bulk_downloader import pointer_inspector as mod
    return mod


def _probe(**kw):
    base = {
        "in_viewport": True,
        "pointer_events": "auto",
        "hit_is_target": True,
        "hit_tag": "button",
        "hit_id": "",
        "hit_class": "",
        "hit_z_index": "auto",
        "hit_opacity": "1",
    }
    base.update(kw)
    return base


class _Locator:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.calls = []

    def evaluate(self, expression):
        self.calls.append(expression)
        if self.exc is not None:
            raise self.exc
        return self.result


# ── (1) obscured beneath an overlay ─────────────────────────────────────────

def test_obscured_by_transparent_overlay_is_flagged():
    m = _load()
    v = m.classify_pointer_probe(_probe(
        hit_is_target=False, hit_tag="div", hit_id="cookie-shield",
        hit_class="modal-backdrop", hit_z_index="9999", hit_opacity="0"))
    assert v.interactive is False
    assert v.reason == m.REASON_OBSCURED
    assert v.blocker == "div#cookie-shield.modal-backdrop"
    assert "z=9999" in v.detail and "opacity=0" in v.detail


def test_obscured_by_untagged_overlay_still_names_the_hit():
    m = _load()
    v = m.classify_pointer_probe(_probe(hit_is_target=False, hit_tag="span"))
    assert v.reason == m.REASON_OBSCURED
    assert v.blocker == "span"


# ── (2) pointer-events validation ───────────────────────────────────────────

def test_pointer_events_none_is_not_interactive():
    m = _load()
    v = m.classify_pointer_probe(_probe(pointer_events="none"))
    assert v.interactive is False
    assert v.reason == m.REASON_POINTER_EVENTS_NONE


def test_pointer_events_none_outranks_obscured():
    # A p-e:none target can never receive the click, whatever is on top of it.
    m = _load()
    v = m.classify_pointer_probe(_probe(pointer_events="none",
                                        hit_is_target=False, hit_tag="div"))
    assert v.reason == m.REASON_POINTER_EVENTS_NONE


def test_off_viewport_is_reported_before_hit_test():
    # elementFromPoint returns null off-viewport; must not be mistaken for an overlay.
    m = _load()
    v = m.classify_pointer_probe(_probe(in_viewport=False, hit_is_target=False,
                                        hit_tag=""))
    assert v.interactive is False
    assert v.reason == m.REASON_OFF_VIEWPORT


# ── (3) clean pass-through ──────────────────────────────────────────────────

def test_unobstructed_target_passes_through():
    m = _load()
    v = m.classify_pointer_probe(_probe())
    assert v.interactive is True
    assert v.reason == ""
    assert v.blocker == ""


def test_hit_on_descendant_counts_as_target():
    # elementFromPoint on <button><span>Go</span></button> returns the span.
    m = _load()
    v = m.classify_pointer_probe(_probe(hit_is_target=True, hit_tag="span"))
    assert v.interactive is True


# ── live half: stub locator ────────────────────────────────────────────────

def test_inspect_pointer_target_uses_locator_evaluate_with_element_from_point():
    m = _load()
    loc = _Locator(result=_probe(hit_is_target=False, hit_tag="div", hit_id="ov"))
    v = m.inspect_pointer_target(loc)
    assert len(loc.calls) == 1
    assert "elementFromPoint" in loc.calls[0]
    assert "pointerEvents" in loc.calls[0]
    assert v.reason == m.REASON_OBSCURED and v.blocker == "div#ov"


def test_inspect_pointer_target_fails_open_as_unknown():
    m = _load()
    v = m.inspect_pointer_target(_Locator(exc=RuntimeError("page closed")))
    assert v.interactive is False
    assert v.reason == m.REASON_UNKNOWN
    assert "page closed" in v.detail


def test_inspect_pointer_target_rejects_non_dict_probe():
    m = _load()
    v = m.inspect_pointer_target(_Locator(result="garbage"))
    assert v.reason == m.REASON_UNKNOWN


def test_verdict_as_dict_round_trips_json():
    import json
    m = _load()
    v = m.classify_pointer_probe(_probe(hit_is_target=False, hit_tag="div"))
    d = json.loads(json.dumps(v.as_dict()))
    assert d["interactive"] is False and d["reason"] == m.REASON_OBSCURED


# ── fixer (O928): correctness REFUTE E1/E2 on real Chromium ──────────────────

_REAL_PAGE = """
<!doctype html><html><body style="margin:0">
<div style="height:3000px">spacer</div>
<button id="below" style="width:120px;height:40px">below the fold</button>
<div style="height:600px"></div>
<div style="position:relative;width:200px;height:60px">
  <button id="shielded" style="width:120px;height:40px">shielded</button>
  <div id="shield" style="position:absolute;inset:0;background:rgba(0,0,0,0.01);z-index:5"></div>
</div>
<div style="height:600px"></div>
<button id="pen" style="pointer-events:none;width:120px;height:40px">pe none</button>
<div style="height:600px"></div>
<my-el id="host"></my-el>
<script>
  class MyEl extends HTMLElement {
    constructor() { super(); const sr = this.attachShadow({mode: 'open'});
      sr.innerHTML = '<div style="padding:20px"><button id="inner" style="width:120px;height:40px">inner</button></div>'; }
  }
  customElements.define('my-el', MyEl);
</script>
</body></html>
"""


def test_real_chromium_below_the_fold_and_shadow_root_controls_are_interactive():
    """E1/E2: on real Chromium the verdict is a function of the element, not
    of the scroll position or of a shadow boundary. A control 3000px down
    and a control inside an open shadow root are interactive (Playwright's
    trial click agrees); the shield and pointer-events:none negatives keep
    their verdicts on the same page."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright
    m = _load()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(_REAL_PAGE)
            page.wait_for_selector("#host")
            page.evaluate("window.scrollTo(0, 0)")

            below = m.inspect_pointer_target(page.locator("#below"))
            assert below.interactive is True, below
            page.locator("#below").click(trial=True)             # Playwright agrees it is clickable

            inner = m.inspect_pointer_target(page.locator("#host >> #inner"))
            assert inner.interactive is True, inner
            page.locator("#host >> #inner").click(trial=True)

            shielded = m.inspect_pointer_target(page.locator("#shielded"))
            assert shielded.interactive is False and shielded.reason == m.REASON_OBSCURED, shielded
            assert "#shield" in shielded.blocker

            pen = m.inspect_pointer_target(page.locator("#pen"))
            assert pen.interactive is False and pen.reason == m.REASON_POINTER_EVENTS_NONE, pen

            # negative control for E1: the old probe's verdict was scroll-dependent --
            # reset the scroll and re-inspect: still interactive
            page.evaluate("window.scrollTo(0, 0)")
            assert m.inspect_pointer_target(page.locator("#below")).interactive is True
        finally:
            browser.close()
