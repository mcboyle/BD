"""Cockpit capture form exposes profile_dir + autofill (presence + passthrough).

Path 3 verification: the cockpit Capture form must render the persistent-session
fields and pass them into the /api/run-capture params, which `cockpit_core.
_argv_for_capture` already turns into confined `--profile-dir` / gated `--autofill`
(see test_cockpit_capture_profile.py for the backend-argv contract).

These are source-presence checks on the cockpit console markup/handler — the same
style as test_autofill.py's checks against login.py — so a future edit can't quietly
drop the UI fields or their wiring. Backend behavior is pinned separately; this only
guards the form ↔ params surface. Zero-arg functions for the custom runner.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_CONSOLE = (_ROOT / "tools" / "cockpit_console.py").read_text(encoding="utf-8")


def test_profile_dir_field_in_capture_form():
    # the Profile dir input is rendered with the id the submit handler reads
    assert 'id="cs_profile"' in _CONSOLE, \
        "cockpit capture form missing the Profile dir input (#cs_profile)"


def test_autofill_field_in_capture_form():
    # the Autofill control is rendered with the id the submit handler reads
    assert 'id="cs_af"' in _CONSOLE, \
        "cockpit capture form missing the Autofill control (#cs_af)"


def test_capture_submit_passes_profile_dir_and_autofill():
    # the run-capture submit handler forwards both fields into params
    assert "profile_dir:$('#cs_profile').value" in _CONSOLE, \
        "capture submit no longer forwards profile_dir"
    assert "autofill:$('#cs_af').value" in _CONSOLE, \
        "capture submit no longer forwards autofill"


def test_capture_form_documents_blank_means_fresh():
    # the UI tells the operator that a blank profile dir = fresh session
    assert "blank = fresh" in _CONSOLE or "(blank" in _CONSOLE, \
        "capture form should document that a blank profile dir is a fresh session"
