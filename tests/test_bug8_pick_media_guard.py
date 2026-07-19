"""BUG-8 -- the live element-pick overlay must not let a <video>/<audio>
element (or its native controls) consume the pick gesture. Native media
controls act on pointerdown/mousedown BEFORE 'click', so clicking a player to
pick it toggled play/pause and the pick landed on nothing.

These assert the injected ACTIVE_PICK_JS carries the capture-phase media guard
on the pointer/mouse events (gated on armed, shift-through preserved) and that
the whole injected blob stays syntactically valid JS.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import bulk_downloader.element_pick as EP  # noqa: E402

JS = EP.ACTIVE_PICK_JS


def test_media_guard_intercepts_pointer_events():
    # the earlier-than-click events native media controls use
    for evt in ("pointerdown", "mousedown", "pointerup", "mouseup"):
        assert evt in JS, f"pick JS does not intercept {evt} (media controls fire first)"
    assert "__bdMediaGuard" in JS, "media guard handler missing"
    assert "__bdInMedia" in JS, "media ancestor test missing"
    # media detection covers <video>/<audio>
    assert "'video'" in JS and "'audio'" in JS, "media guard must match video/audio"


def test_media_guard_is_gated_and_shift_through_preserved():
    # gated on the armed flag (inert outside a pick)
    assert "if (!window.__bd_pick_armed) { return; }" in JS
    # shift-through still passes to the click handler (not swallowed by the guard)
    assert "if (ev.shiftKey) { return; }" in JS, "shift-through not preserved in media guard"
    # the guard suppresses the UA default + control handlers
    assert "stopImmediatePropagation" in JS and "preventDefault" in JS


def test_click_handler_still_present():
    # the primary click interceptor (the actual selector derivation) is intact
    assert "__bdPickClick" in JS
    assert "document.addEventListener('click', __bdPickClick, { capture:true });" in JS


def test_injected_js_is_syntactically_valid():
    node = shutil.which("node")
    if not node:
        return  # node not available in this env; the string-content tests still guard
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "pick.js"
        # minimal shims so top-level references resolve during a syntax check
        f.write_text("var document={addEventListener:function(){},"
                     "getElementById:function(){return null;}};"
                     "var window={};\n" + JS)
        r = subprocess.run([node, "--check", str(f)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"injected pick JS is not valid JS:\n{r.stderr}"


if __name__ == "__main__":
    for k in [x for x in sorted(dict(globals())) if x.startswith("test_")]:
        try:
            globals()[k](); print(f"PASS  {k}")
        except AssertionError as e:
            print(f"FAIL  {k}: {e}")
        except Exception as e:
            print(f"ERROR {k}: {type(e).__name__}: {e}")
