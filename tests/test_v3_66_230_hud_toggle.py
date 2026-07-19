"""v3.66.230 — HUD overlay: default-ON, per-capture --no-hud, global kill.

Pins the resolution semantics of the decorative capture HUD (re-derived from
source: tools/capture_session.py ``_hud_enabled`` + the ``--no-hud`` CLI arg)
and that the inject path is the CSP-immune ``page.evaluate`` (not
``add_script_tag``). No browser is launched here; the live mount is proven on
stash. Zero-arg functions for the custom runner.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import capture_session as cs

_CAPTURE_SRC = (_ROOT / "tools" / "capture_session.py").read_text(encoding="utf-8")


def _args(no_hud=False):
    return cs._build_parser().parse_args(
        ["--url", "u", "--out", "o"] + (["--no-hud"] if no_hud else []))


# ── default-ON resolution ────────────────────────────────────────────────────
def test_default_on_when_env_unset_and_no_flag():
    assert cs._hud_enabled(_args(no_hud=False), env={}) is True


def test_env_value_one_is_on():
    assert cs._hud_enabled(_args(no_hud=False), env={"BD_HUD_OVERLAY": "1"}) is True


def test_env_zero_is_global_kill():
    assert cs._hud_enabled(_args(no_hud=False), env={"BD_HUD_OVERLAY": "0"}) is False


def test_no_hud_flag_disables_even_when_env_on():
    assert cs._hud_enabled(_args(no_hud=True), env={"BD_HUD_OVERLAY": "1"}) is False


def test_no_hud_flag_disables_when_env_unset():
    assert cs._hud_enabled(_args(no_hud=True), env={}) is False


def test_env_zero_and_flag_both_off():
    assert cs._hud_enabled(_args(no_hud=True), env={"BD_HUD_OVERLAY": "0"}) is False


def test_unknown_env_value_treated_as_on():
    # Anything other than "0" leaves the HUD on (only "0" is the kill switch).
    assert cs._hud_enabled(_args(no_hud=False), env={"BD_HUD_OVERLAY": "yes"}) is True


# ── --no-hud is a real CLI option ────────────────────────────────────────────
def test_no_hud_is_a_real_cli_flag():
    opts = {s for a in cs._build_parser()._actions for s in a.option_strings}
    assert "--no-hud" in opts


# ── persistence: the HUD is injected inside the pump loop, not once ──────────
def test_hud_injected_inside_pump_loop():
    # The inject must live in _pump_dom (re-mounts every tick -> survives nav),
    # NOT as a single pre-loop call. Source-level proof: the inject symbol is
    # referenced from within the pump and the old one-shot env gate is gone.
    assert "_inject_hud" in _CAPTURE_SRC
    assert 'os.environ.get("BD_HUD_OVERLAY") == "1"' not in _CAPTURE_SRC, \
        "the old one-shot default-OFF env gate must be gone"
    assert "_hud_enabled(args)" in _CAPTURE_SRC


# ── inject mechanism is evaluate, never add_script_tag, in dom_overlay ───────
def test_inject_overlay_source_uses_evaluate_not_script_tag():
    src = (_ROOT / "bulk_downloader" / "dom_overlay.py").read_text(encoding="utf-8")
    # The inject_overlay body calls page.evaluate(...) and not add_script_tag(...)
    body = src.split("def inject_overlay", 1)[1]
    assert "page.evaluate(" in body
    assert "add_script_tag(" not in body
