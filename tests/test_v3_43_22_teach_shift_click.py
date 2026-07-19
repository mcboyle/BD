"""v3.43.22 regression test — teach-mode shift-click pass-through.

The teach overlay's click handler in learn.py's TEACH_OVERLAY_JS was
binary: teach-only mode blocked every click (so two-step download
flows couldn't be taught because the modal never opened), or live
mode let every click through (dangerous; any click in the overlay
fires real actions). Shift-click gives per-click pass-through while
still recording the selector.

The handler is client-side JS embedded in a Python module, so these
tests check the source for the expected patterns. The actual JS
runs in the headed Chromium during teach mode.
"""
from __future__ import annotations

from pathlib import Path


_LEARN_PY = Path(__file__).resolve().parent.parent / "bulk_downloader" / "learn.py"


def _learn_impl_src():
    """Concatenated source of the decomposed learn_impl/ package. learn.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 5); RECORDER_JS/TEACH_OVERLAY_JS and the
    function bodies live in learn_impl/*.py, so source/structure tests read the package."""
    from pathlib import Path as _P
    import bulk_downloader.learn_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))


def test_shift_click_branch_exists_in_teach_handler():
    """The click handler must branch on shiftKey in teach-only mode."""
    src = _learn_impl_src()
    # Both the teach-only path (preventDefault) and the new shift path
    # (no preventDefault) must be present
    assert "state.teach_only && !e.shiftKey" in src, (
        "Teach-only block branch must explicitly exclude shift-click"
    )
    assert "state.teach_only && e.shiftKey" in src, (
        "Shift-click pass-through branch must exist in teach-only mode"
    )


def test_shift_click_does_not_prevent_default():
    """The shift-through branch must NOT call preventDefault on the
    click — otherwise the modal won't open and we've shipped a no-op."""
    src = _learn_impl_src()
    # Locate the shift-true block
    shift_block_start = src.find("state.teach_only && e.shiftKey")
    assert shift_block_start > 0
    # Read forward to the next branch boundary
    block = src[shift_block_start:shift_block_start + 600]
    # Cyan flash is the visual indicator (matches the panel hint)
    assert "#06b6d4" in block, (
        "Shift-through branch should flash cyan to visually distinguish "
        "from pink (recorded-only) and green (live mode)"
    )
    # CRITICAL: the shift-through block must NOT call preventDefault
    # within its own arm. The closing arm is the live-mode else.
    # Find the arm by reading until the next 'else' or end of the if/else.
    arm_end = block.find("} else {")
    if arm_end < 0:
        arm_end = block.find("}\n    } else")
    arm = block[:arm_end] if arm_end > 0 else block[:300]
    assert "preventDefault" not in arm, (
        "Shift-through arm must NOT preventDefault, else the modal "
        "won't open and the fix is a no-op"
    )


def test_shift_metadata_recorded():
    """The record stamps shift state so future improvements (auto-
    detecting trigger vs row click) have the data to work with."""
    src = _learn_impl_src()
    assert "rec.shift = !!e.shiftKey" in src, (
        "Click record must include shift flag for downstream consumers"
    )


def test_teach_panel_hint_mentions_shift_click():
    """The panel hint must mention shift-click so users discover the
    feature without reading the changelog."""
    src = _learn_impl_src()
    assert "shift-click pass-through" in src.lower(), (
        "Teach panel hint must mention shift-click pass-through "
        "so users find the feature"
    )


def test_default_teach_only_unchanged():
    """The default teach-only behavior must not change. Sites that
    work today (single-click downloads) must continue to work after
    the shift-click addition."""
    src = _learn_impl_src()
    # Default state initializer must still be teach_only: true
    assert "teach_only: true" in src
    # The default checkbox must still be checked
    assert 'id="pw_teach_only" checked' in src
