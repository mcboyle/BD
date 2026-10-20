"""BH1-49 r2: a lost keystroke binding refuses the whole login in do_login.

_try_fill aborts a credential entry with the BOUND_FIELD_LOST reason when the
checked element is lost mid-typing.  do_login must take that through the
refusal path it already uses for the origin check: no staged Continue retry
(which re-resolves the password Locator), no re-submit password after a lost
username, and the abort reason (not "Couldn't find password field") is what
the caller gets.  Drives the real do_login through the o1698 harness; only
_try_fill's result for the named labels is replaced.
"""
import pytest

from bulk_downloader.login_impl import submit
from tests.test_o1698_login_prefill_origin_guard import _run

BD_GATE_SCOPE = "module"
BOUND_FIELD_LOST = "credential entry aborted: bound field lost"  # the abort reason prefix
LOGIN = "https://www.site.test/login"
LOST = f"{BOUND_FIELD_LOST} (isConnected: element not connected)"


def _lose(monkeypatch, *labels):
    real, tried = submit._try_fill, []

    def try_fill(page, candidates, value, label, refusal=None):
        tried.append(label)
        if label in labels:
            return False, LOST
        return real(page, candidates, value, label, refusal=refusal)

    monkeypatch.setattr(submit, "_try_fill", try_fill)
    return tried


@pytest.mark.parametrize("allow_manual", [False, True], ids=["auto", "manual-takeover"])
def test_lost_password_binding_is_not_retried_after_continue(monkeypatch, tmp_path, allow_manual):
    tried = _lose(monkeypatch, "password")
    result, calls = _run(monkeypatch, tmp_path, login_url=LOGIN, landed=LOGIN,
                         staged_to=f"{LOGIN}/step2", allow_manual=allow_manual)
    assert tried == ["username", "password"] and not calls["continued"], (
        f"BH1_49_R2_STAGED_RETRY: a lost password binding went on to the staged retry "
        f"(fills {tried}, continue clicked {calls['continued']})")
    assert not calls["submit"] and result[0] in (False, "MANUAL_PENDING"), (calls, result)
    assert result[1].startswith(BOUND_FIELD_LOST), f"BH1_49_R2_REASON: {result[1]!r}"


@pytest.mark.parametrize("allow_manual", [False, True], ids=["auto", "manual-takeover"])
def test_lost_binding_after_continue_reports_the_abort(monkeypatch, tmp_path, allow_manual):
    tried = _lose(monkeypatch, "password (after continue)")
    result, calls = _run(monkeypatch, tmp_path, login_url=LOGIN, landed=LOGIN,
                         staged_to=f"{LOGIN}/step2", allow_manual=allow_manual)
    assert tried == ["username", "password", "password (after continue)"], tried  # premise
    assert not calls["submit"] and result[0] in (False, "MANUAL_PENDING"), (calls, result)
    assert result[1].startswith(BOUND_FIELD_LOST), (
        f"BH1_49_R2_STAGED_REASON: the staged abort surfaced as {result[1]!r}")


def test_lost_username_binding_on_re_submit_types_no_password(monkeypatch, tmp_path):
    tried = _lose(monkeypatch, "username (re-submit)")
    result, calls = _run(monkeypatch, tmp_path, login_url=LOGIN, landed=LOGIN,
                         challenge_to=f"{LOGIN}?retry=1")
    assert tried == ["username", "password", "username (re-submit)"] and calls["submit"] == 1, (
        f"BH1_49_R2_RESUBMIT: after the lost username binding do_login went on to "
        f"{tried[3:]} and submitted {calls['submit']}x")
    assert result[0] is False and result[1].startswith(BOUND_FIELD_LOST), result


def test_control_no_loss_fills_and_re_submits(monkeypatch, tmp_path):
    tried = _lose(monkeypatch)
    result, calls = _run(monkeypatch, tmp_path, login_url=LOGIN, landed=LOGIN,
                         challenge_to=f"{LOGIN}?retry=1")
    assert tried == ["username", "password", "username (re-submit)", "password (re-submit)"]
    assert calls["submit"] == 2, (calls, result[1])
