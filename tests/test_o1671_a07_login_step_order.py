"""O1671 AUDIT-07 (HIGH CORRECTNESS): login_assist.infer_step must not skip
identifier entry on a unified single-page form that shows BOTH the identifier
and the password field -- the identifier is entered first."""

import pytest

from bulk_downloader import login_assist

BD_GATE_SCOPE = "module"


def test_unified_form_next_step_is_identifier_entry():
    out = login_assist.infer_step({"fields": ["email", "password"]})
    assert out["next_step"] == "enter_email", (
        f"O1671-A07 unified form skipped identifier entry: {out['next_step']!r}")
    assert "identifier" in out["summary"].lower()
    assert out["requires_review"] is False


@pytest.mark.parametrize("fields", [["username", "passwd"], ["Login", "Password"]])
def test_unified_form_variants_enter_identifier_first(fields):
    assert login_assist.infer_step({"fields": fields})["next_step"] == "enter_email"


@pytest.mark.parametrize("fields", [["password"], ["login_password"], ["user_password"]])
def test_password_only_form_keeps_enter_password(fields):
    # A field whose name carries an identifier word AND a password word is a
    # password field; it must not be read as an identifier field.
    out = login_assist.infer_step({"fields": fields})
    assert out["next_step"] == "enter_password", (fields, out["next_step"])


def test_email_only_form_enters_identifier():
    assert login_assist.infer_step({"fields": ["email"]})["next_step"] == "enter_email"


def test_challenge_and_sso_still_take_precedence_over_unified_form():
    both = ["email", "password"]
    assert login_assist.infer_step(
        {"fields": both, "challenge": True})["next_step"] == "manual_handoff"
    assert login_assist.infer_step(
        {"fields": both, "buttons": ["Sign in with Google"]})["next_step"] == "sso_review"
    assert login_assist.infer_step(
        {"fields": both, "cross_origin": True})["next_step"] == "sso_review"
