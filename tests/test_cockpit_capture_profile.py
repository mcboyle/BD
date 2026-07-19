"""cockpit capture form → _argv_for_capture: profile_dir + autofill wiring.

Pins that the cockpit Capture form's fields flow into the capture_session argv:
- profile_dir (the persistent-session field added for one-click authenticated capture)
  becomes --profile-dir, confined under the captures root;
- a blank profile_dir yields a fresh (no --profile-dir) session;
- autofill on/off toggles --autofill;
- an escaping profile_dir is refused.

Backend-only (the form itself is HTML/JS). Zero-arg functions for the custom runner.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from tools import cockpit_core as cc

_URL = "https://example.com/members/clip"


def _task_out():
    out = cc.captures_root() / "task_test"
    out.mkdir(parents=True, exist_ok=True)
    return out


def test_profile_dir_becomes_profile_flag_confined():
    argv = cc._argv_for_capture(
        "capture_session",
        {"url": _URL, "label": "clip", "autofill": "false", "profile_dir": "profiles/reptyle"},
        _task_out(),
    )
    assert "--profile-dir" in argv
    i = argv.index("--profile-dir")
    # the resolved path is confined under the captures root
    resolved = Path(argv[i + 1]).resolve()
    assert str(resolved).startswith(str(cc.captures_root().resolve()))
    assert resolved.name == "reptyle"


def test_blank_profile_dir_is_fresh_session():
    argv = cc._argv_for_capture(
        "capture_session",
        {"url": _URL, "label": "clip", "autofill": "false", "profile_dir": ""},
        _task_out(),
    )
    assert "--profile-dir" not in argv


def test_autofill_toggles_flag():
    on = cc._argv_for_capture(
        "capture_session",
        {"url": _URL, "label": "clip", "autofill": "true"},
        _task_out(),
    )
    off = cc._argv_for_capture(
        "capture_session",
        {"url": _URL, "label": "clip", "autofill": "false"},
        _task_out(),
    )
    assert "--autofill" in on
    assert "--autofill" not in off


def test_escaping_profile_dir_refused():
    try:
        cc._argv_for_capture(
            "capture_session",
            {"url": _URL, "label": "clip", "profile_dir": "../../etc"},
            _task_out(),
        )
    except cc.ValidationError:
        return  # expected
    raise AssertionError("escaping profile_dir should be refused")


def test_url_and_label_still_present():
    argv = cc._argv_for_capture(
        "capture_session",
        {"url": _URL, "label": "myclip", "profile_dir": ""},
        _task_out(),
    )
    assert "--url" in argv and _URL in argv
    assert "--title" in argv and "myclip" in argv
    assert "--out" in argv
