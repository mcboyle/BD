"""GCW-2 — pick-overlay port into element_pick.py's ACTIVE_PICK_JS.

Ports the legacy ``learn.py`` TEACH_OVERLAY_JS capabilities (hover-highlight,
colored click-flash, shift-click pass-through, persistent — non-self-removing —
capture-phase interceptor) into the live capture picker. ``element_pick.py`` is
NOT one of the 7 release-guard files, so no sha-declaration is required; the
existing bridge contract (``tests/test_element_pick_bridge.py``) stays green.

Two layers, both sandbox-runnable:

  1. SOURCE-SCAN of ACTIVE_PICK_JS — same idiom as test_v3_43_22_teach_shift_click
     and test_v3_66_270_promote_suffix: the overlay JS is a client-side string
     embedded in a Python module, so we pin the load-bearing markers (hover
     mousemove/outline, the shift branch with NO preventDefault + cyan, the pink
     recorded-only flash, the armed-flag gate, and the absence of the old
     self-removing one-shot listener). The real in-browser behaviour is proven by
     the existing Playwright bridge test + on stash.

  2. EXECUTABLE bridge logic via a FakePage stub (no browser needed) — pins the
     Python redesign: ``maybe_arm_and_collect`` installs the persistent overlay
     once per page (one ``add_init_script`` across ticks), mirrors the PICK_ARM
     sentinel into the in-page ``__bd_pick_armed`` flag, drains a result, writes
     PICK_RESULT.json and clears the arm. ``inject_active_pick`` installs AND arms
     the current document (direct-call path used by the existing bridge test).

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins; restore module
globals in try/finally.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EP_PY = REPO / "bulk_downloader" / "element_pick.py"


def _read(p: Path) -> str:
    assert p.exists(), f"missing source file: {p}"
    return p.read_text(encoding="utf-8")


def _active_pick_js() -> str:
    """Return the ACTIVE_PICK_JS string literal block (from the assignment to
    the next top-level def/assignment) so a marker elsewhere in the module can't
    mask the assertion."""
    src = _read(EP_PY)
    start = src.find("ACTIVE_PICK_JS = (")
    assert start >= 0, "ACTIVE_PICK_JS assignment not found in element_pick.py"
    # scope to the next read-and-clear constant / def
    end = src.find("_READ_AND_CLEAR_JS", start + 10)
    if end < 0:
        end = src.find("\ndef ", start)
    return src[start:end] if end > start else src[start:start + 4000]


# ── Layer 1: source-scan of the overlay JS ──────────────────────────────────

def test_overlay_has_hover_highlight():
    """A follow-mouse hover layer (mousemove + an outline box) must exist — the
    old picker had NO hover layer, so the operator picked blind."""
    block = _active_pick_js()
    assert "mousemove" in block, "hover highlight needs a mousemove listener"
    assert "mouseout" in block, "hover must clear on mouseout"
    # an outline/box element follows the target
    assert "getBoundingClientRect" in block, (
        "hover box must position itself from the target's bounding rect"
    )
    assert "__bd_pick_hover" in block, (
        "hover overlay element id (__bd_pick_hover) must be present"
    )


def test_overlay_shift_branch_passes_click_through_with_cyan():
    """Shift-click records the selector AND lets the click through (so a click
    that opens a modal still opens it), flashing cyan — mirrors the legacy
    shift-through. The shift arm must NOT preventDefault or the modal never
    opens and the feature is a no-op."""
    block = _active_pick_js()
    assert "shiftKey" in block, "click handler must branch on shiftKey"
    assert "#06b6d4" in block, "shift-through must flash cyan (#06b6d4)"
    assert "#ff4d8f" in block, "default pick must flash pink (#ff4d8f) recorded-only"
    # locate the shift-true arm and assert it does NOT preventDefault
    si = block.find("shiftKey")
    # read a window around the branch; the shift (pass-through) arm precedes the
    # else (default, preventDefault) arm.
    shift_arm = block[si:block.find("else", si)] if block.find("else", si) > si else block[si:si + 400]
    assert "preventDefault" not in shift_arm, (
        "shift-through arm must NOT preventDefault — else the modal won't open"
    )


def test_overlay_listener_is_persistent_not_self_removing():
    """The capture-phase click listener must be persistent (gated on an in-page
    armed flag), NOT the old self-removing one-shot. The fix kills the arm-lag
    race + the 'doesn't stop real clicks' flakiness."""
    block = _active_pick_js()
    # gated on the armed flag rather than installing/removing the listener
    assert "__bd_pick_armed" in block, (
        "the click interceptor must gate on window.__bd_pick_armed"
    )
    # the old self-removal markers must be gone from the overlay
    assert "__bd_active_pick_listening" not in block, (
        "the old self-removing one-shot guard must be removed"
    )
    assert "removeEventListener('click'" not in block, (
        "the persistent listener must NOT remove itself after one click"
    )
    # idempotent install guard so re-evaluation each on_tick is a no-op
    assert "__bd_pick_overlay_installed" in block, (
        "overlay install must be idempotent (per-document guard)"
    )


def test_overlay_default_pick_still_prevents_default():
    """The default (non-shift) armed pick MUST cancel the click — picking a
    download row must not fire the download / navigate the live page. (Preserves
    the existing one-shot bridge contract.)"""
    block = _active_pick_js()
    assert "preventDefault" in block and "stopPropagation" in block, (
        "default pick must preventDefault + stopPropagation"
    )
    # one-shot per arm: disarm in-page after a pick so an immediate second click
    # is inert until the next arm (the arm/poll/consume protocol is one-per-arm).
    assert "window.__bd_pick_armed = false" in block, (
        "after a pick the overlay must disarm in-page (one pick per arm)"
    )


# ── Layer 2: executable bridge logic (FakePage, no browser) ──────────────────

class _FakePage:
    """Minimal stand-in for a Playwright page: records add_init_script calls and
    runs a tiny model of the overlay's window state in response to evaluate()."""
    def __init__(self):
        self.init_scripts = []          # add_init_script payloads (count matters)
        self.armed = None               # last __bd_pick_armed value set
        self.overlay_installed = False  # set when ACTIVE_PICK_JS is evaluated
        self._active_pick = None        # window.__bd_active_pick

    def add_init_script(self, script):
        self.init_scripts.append(script)

    def evaluate(self, script, *args):
        # install overlay
        if "__bd_pick_overlay_installed" in script and "addEventListener" in script:
            self.overlay_installed = True
            return None
        # set armed flag: "(a)=>{ window.__bd_pick_armed = !!a; }"
        if "__bd_pick_armed" in script and args:
            self.armed = bool(args[0])
            return None
        # read-and-clear the in-page pick result
        if "__bd_active_pick" in script and "return r" in script:
            r = self._active_pick
            self._active_pick = None
            return r
        return None

    # test helper: simulate the operator clicking a picked element while armed
    def _operator_picks(self, selector):
        if self.armed:
            self._active_pick = {"selector": selector, "unique": True,
                                 "visible": True, "ts": 1, "shift": False}
            self.armed = False  # in-page one-shot disarm


def test_maybe_arm_and_collect_installs_overlay_once_across_ticks():
    """The persistent overlay's document-start init script must be registered
    exactly once per page even though maybe_arm_and_collect runs every on_tick."""
    from bulk_downloader import element_pick as ep
    out = Path(tempfile.mkdtemp())
    pg = _FakePage()
    # three unarmed ticks
    for _ in range(3):
        assert ep.maybe_arm_and_collect([pg], out) is None
    assert pg.overlay_installed is True, "overlay must be installed even unarmed"
    assert len(pg.init_scripts) == 1, (
        f"add_init_script must be called once per page, got {len(pg.init_scripts)}"
    )


def test_maybe_arm_and_collect_mirrors_arm_sentinel_into_inpage_flag():
    """PICK_ARM present => in-page __bd_pick_armed True; absent => False."""
    from bulk_downloader import element_pick as ep
    out = Path(tempfile.mkdtemp())
    pg = _FakePage()
    # unarmed
    ep.maybe_arm_and_collect([pg], out)
    assert pg.armed is False
    # arm
    ep.arm(out)
    ep.maybe_arm_and_collect([pg], out)
    assert pg.armed is True


def test_maybe_arm_and_collect_drains_result_and_clears_arm():
    """Full sentinel roundtrip over the FakePage: arm -> tick (no result) ->
    operator picks -> tick drains, writes PICK_RESULT.json, clears PICK_ARM."""
    from bulk_downloader import element_pick as ep
    out = Path(tempfile.mkdtemp())
    pg = _FakePage()
    ep.arm(out)
    assert ep.maybe_arm_and_collect([pg], out) is None  # armed, no click yet
    assert ep.is_armed(out) is True
    pg._operator_picks('a.ct_dl_button[data-res="1080"]')
    got = ep.maybe_arm_and_collect([pg], out)
    assert got is not None and "1080" in got["selector"], got
    assert ep.is_armed(out) is False
    assert ep.result_path(out).exists()
    consumed = ep.consume_result(out)
    assert consumed == got


def test_inject_active_pick_installs_and_arms_current_document():
    """The direct-call path (used by the existing Playwright bridge test) must
    install the overlay AND arm THIS document in one call."""
    from bulk_downloader import element_pick as ep
    pg = _FakePage()
    ep.inject_active_pick(pg)
    assert pg.overlay_installed is True
    assert pg.armed is True


if __name__ == "__main__":
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            _f()
            print("PASS", _n)
    print("ALL PASS")
