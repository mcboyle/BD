"""Path 4 — cockpit capture existing-options parity.

Pins that the cockpit Capture form's numeric knobs flow into capture_session argv
as their EXISTING backend flags, defaults are preserved when blank, invalid values
are rejected, and the exposed set maps only to real capture_session options (no
invented backend behavior). Backend-argv checks via cockpit_core; form presence via
source-inspection of cockpit_console.py (same style as test_cockpit_capture_form_fields).

Zero-arg functions for the custom runner.
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from tools import cockpit_core as cc

_URL = "https://example.com/members/clip"
_CONSOLE = (_ROOT / "tools" / "cockpit_console.py").read_text(encoding="utf-8")
_CAPTURE_SRC = (_ROOT / "tools" / "capture_session.py").read_text(encoding="utf-8")


def _argv(params):
    base = {"url": _URL, "label": "clip"}
    base.update(params)
    out = cc.captures_root() / "task_parity"
    out.mkdir(parents=True, exist_ok=True)
    return cc._argv_for_capture("capture_session", base, out)


# ── defaults preserved when blank ────────────────────────────────────────────
def test_blank_numerics_preserve_default_command():
    argv = _argv({"body_cap_mib": "", "chunk_events": "", "max_seconds": ""})
    for flag in ("--body-cap-mib", "--chunk-events", "--max-seconds"):
        assert flag not in argv, f"{flag} should be absent when blank"


def test_missing_numerics_preserve_default_command():
    argv = _argv({})
    for flag in ("--body-cap-mib", "--chunk-events", "--max-seconds"):
        assert flag not in argv


# ── valid values pass through as the existing flags ──────────────────────────
def test_body_cap_passes_through():
    argv = _argv({"body_cap_mib": "8"})
    assert argv[argv.index("--body-cap-mib") + 1] == "8"


def test_chunk_events_passes_through():
    argv = _argv({"chunk_events": "50000"})
    assert argv[argv.index("--chunk-events") + 1] == "50000"


def test_max_seconds_passes_through():
    argv = _argv({"max_seconds": "1200"})
    assert argv[argv.index("--max-seconds") + 1] == "1200"


# ── invalid values rejected safely ───────────────────────────────────────────
def test_body_cap_out_of_range_rejected():
    for bad in ("0", "999"):
        try:
            _argv({"body_cap_mib": bad}); raise AssertionError(f"{bad} should reject")
        except cc.ValidationError:
            pass


def test_chunk_events_non_integer_rejected():
    try:
        _argv({"chunk_events": "abc"}); raise AssertionError("non-int should reject")
    except cc.ValidationError:
        pass


def test_max_seconds_must_stay_below_runner_kill():
    # The cockpit runner kills the subprocess at 1800s (start_task timeout=1800);
    # max_seconds must be capped below that so the capture auto-saves gracefully.
    argv = _argv({"max_seconds": "1700"})  # boundary ok
    assert argv[argv.index("--max-seconds") + 1] == "1700"
    for bad in ("1800", "5000", "-1", "abc"):
        try:
            _argv({"max_seconds": bad}); raise AssertionError(f"{bad} should reject")
        except cc.ValidationError:
            pass


def test_all_three_together():
    argv = _argv({"body_cap_mib": "4", "chunk_events": "20000", "max_seconds": "900"})
    assert "--body-cap-mib" in argv and "--chunk-events" in argv and "--max-seconds" in argv


# ── form renders the new fields and forwards them ────────────────────────────
def test_form_renders_numeric_fields():
    for fid in ('id="cs_bodycap"', 'id="cs_chunks"', 'id="cs_maxsec"'):
        assert fid in _CONSOLE, f"capture form missing {fid}"


def test_submit_forwards_numeric_params():
    for kv in ("body_cap_mib:$('#cs_bodycap').value",
               "chunk_events:$('#cs_chunks').value",
               "max_seconds:$('#cs_maxsec').value"):
        assert kv in _CONSOLE, f"capture submit no longer forwards {kv}"


# ── parity proof: every exposed option is a REAL capture_session flag ────────
def test_exposed_options_map_to_real_backend_flags():
    # The cockpit only exposes flags capture_session actually accepts (no invention).
    exposed_flags = ("--profile-dir", "--autofill", "--body-cap-mib",
                     "--chunk-events", "--max-seconds", "--url", "--title",
                     "--out", "--no-hud")
    for flag in exposed_flags:
        assert f'"{flag}"' in _CAPTURE_SRC, \
            f"{flag} is not an actual capture_session.py argument"


def test_no_invented_toggles_in_form():
    # Guard against exposing options the backend does not support.
    for invented in ('id="cs_rrweb"', 'id="cs_snapdom"', 'id="cs_domcap"',
                     'id="cs_backend"', 'id="cs_novnc"'):
        assert invented not in _CONSOLE, f"unexpected unsupported field {invented}"


# ── HUD overlay toggle (v3.66.230): default-ON, off via --no-hud ─────────────
def test_hud_checkbox_present_and_default_checked():
    # The capture form exposes a HUD checkbox that is CHECKED by default
    # (HUD on by default), backed by the real --no-hud backend flag.
    assert 'id="cs_hud"' in _CONSOLE, "capture form missing HUD checkbox"
    # checked-by-default: the checkbox carries the `checked` attribute.
    import re as _re
    m = _re.search(r'id="cs_hud"[^>]*', _CONSOLE)
    assert m and "checked" in m.group(0), "HUD checkbox must be checked by default"


def test_hud_value_forwarded_on_submit():
    assert "hud:$('#cs_hud').checked" in _CONSOLE, \
        "capture submit no longer forwards the HUD checkbox"


def test_hud_default_on_emits_no_flag():
    # Default (checkbox checked -> hud True, or omitted) keeps HUD on and adds
    # no flag, preserving the prior command exactly.
    assert "--no-hud" not in _argv({})
    assert "--no-hud" not in _argv({"hud": True})
    assert "--no-hud" not in _argv({"hud": ""})  # blank treated as default-on


def test_hud_unchecked_emits_no_hud_flag():
    # Operator unticks the box -> hud False -> --no-hud forwarded.
    assert "--no-hud" in _argv({"hud": False})
