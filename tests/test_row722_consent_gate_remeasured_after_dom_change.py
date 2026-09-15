"""Row 722 live (bangbros / Aylo, 2026-09-15): the login page carries a
React-rendered consent banner. The generic pass snapshots its "Accept all
cookies" control, then re-checks the live DOM immediately before clicking --
and Aylo's banner re-renders once in between, so the live read no longer
matches what was snapshotted. The product (correctly) refuses to click a
control it can no longer prove is the one it measured -- "a control that
changed is not the control you measured" -- but it then gave up entirely
after that ONE race:

    "login: measurement_unknown for consent gate via '' -- generic consent
    matched control changed after snapshot; control not used"

and the login form was never submitted.

The fix (bulk_downloader/interstitial.py, the "changed after snapshot"
branch): on a live-label mismatch, re-snapshot and re-match the SAME
tier/label up to twice more, ~300ms apart. If the control settles back to
the ORIGINAL snapshot's labels (same label, same tier admission already
proven by that match, still visible), it is used -- a single re-render is
not "the control changed", it is "the control was read mid-render". If it
keeps changing, the original ``measurement_unknown`` refusal is unchanged.

RED at BASE (before this fix): the consent control is refused as
``measurement_unknown`` and never clicked, on the very first mismatch --
no re-measurement is attempted at all.
"""
from __future__ import annotations

from bulk_downloader import interstitial
from tests.test_row721_age_gate_enter_affordance import _GatePage

BD_GATE_SCOPE = "module"

CONSENT_LABEL = "Accept all cookies"


class _FlickerControl:
    """A generic control whose measured label follows a fixed sequence.

    ``_Control`` (test_row721) has one fixed label; this one changes per
    READ, modelling the same live DOM object being re-rendered by React
    between the generic pass's snapshot and its pre-click live re-check.
    Once the sequence is exhausted the last label repeats.
    """

    def __init__(self, labels, *, next_text=None, destination=""):
        self._labels = list(labels)
        self.reads = 0
        self.next_text = next_text
        self.destination = destination

    @property
    def label(self):
        index = min(self.reads, len(self._labels) - 1)
        self.reads += 1
        return self._labels[index]


def _consent_page(control):
    return _GatePage(
        "https://site-ma.bangbros.example/login",
        "Members login",
        [control],
    )


def _run(page):
    sleep_calls = []
    actions = interstitial.dismiss_gates(
        page, "", destination_url=page.url, settle_s=0.0,
        sleep=sleep_calls.append,
    )
    return actions, sleep_calls


def _outcomes(actions, outcome):
    return [a for a in actions if a.get("outcome") == outcome]


def _remeasure_sleeps(sleep_calls):
    return [s for s in sleep_calls if s == interstitial.GENERIC_REMEASURE_INTERVAL_S]


def test_control_that_rerenders_once_is_clicked_on_the_remeasure():
    """THE ROW: one re-render, re-measured, clicked, outcome cleared."""
    control = _FlickerControl(
        [CONSENT_LABEL, "Accept", CONSENT_LABEL],
        next_text="Members area",
    )
    page = _consent_page(control)
    actions, sleep_calls = _run(page)

    assert page.clicked == [CONSENT_LABEL], (
        "the re-rendered consent control was never clicked: clicked=%r"
        % (page.clicked,)
    )
    remeasured = _outcomes(actions, "re_measured")
    assert len(remeasured) == 1, "expected one re_measured log line, got %r" % (
        [a.get("outcome") for a in actions],
    )
    assert "re-measured" in remeasured[0]["reason"]
    assert CONSENT_LABEL in remeasured[0]["reason"]
    assert "(attempt 2/3)" in remeasured[0]["reason"], remeasured[0]["reason"]
    cleared = _outcomes(actions, "cleared")
    assert len(cleared) == 1, "expected exactly one cleared action, got %r" % (
        [a.get("outcome") for a in actions],
    )
    assert cleared[0]["tier"] == "consent"
    assert cleared[0]["label"] == CONSENT_LABEL
    assert _outcomes(actions, "measurement_unknown") == []
    # Bounded: exactly one re-measurement sleep was spent, not the full two.
    assert len(_remeasure_sleeps(sleep_calls)) == 1


def test_control_that_keeps_changing_stays_measurement_unknown():
    """Negative (a): never stabilises -> the ORIGINAL refusal, verbatim."""
    control = _FlickerControl(
        [CONSENT_LABEL, "Accept X", "Accept Y", "Accept Z"],
    )
    page = _consent_page(control)
    actions, sleep_calls = _run(page)

    assert page.clicked == [], (
        "a control that never stabilised was clicked: %r" % (page.clicked,)
    )
    unknown = _outcomes(actions, "measurement_unknown")
    assert len(unknown) == 1, "expected one UNKNOWN, got %r" % (
        [a.get("outcome") for a in actions],
    )
    assert unknown[0]["reason"] == (
        "generic consent matched control changed after snapshot; "
        "control not used"
    ), unknown[0]["reason"]
    assert _outcomes(actions, "re_measured") == [], (
        "a control that never stabilised must not log a re_measured line"
    )
    # Bounded: exactly the two allowed re-measurement sleeps, never more --
    # this is what catches a "bound dropped" mutant instead of hanging.
    assert len(_remeasure_sleeps(sleep_calls)) == 2


def test_denylisted_control_that_stabilises_is_still_refused():
    """Negative (b): the FIRST live read is already denylisted -- refused
    before re-measurement is even considered, whatever it later reads as."""
    control = _FlickerControl(
        [CONSENT_LABEL, "Cancel", CONSENT_LABEL, CONSENT_LABEL],
    )
    page = _consent_page(control)
    actions, _sleep_calls = _run(page)

    assert page.clicked == [], (
        "a denylisted control was clicked: %r" % (page.clicked,)
    )
    refused = _outcomes(actions, "refused")
    assert len(refused) == 1, "expected one refusal, got %r" % (
        [a.get("outcome") for a in actions],
    )
    assert "denylisted label matched 'cancel'" in refused[0]["reason"]
    assert _outcomes(actions, "re_measured") == []
    assert _outcomes(actions, "cleared") == []


def test_stable_control_is_unchanged_single_pass_no_remeasure():
    """Negative (c): a control that never changes needs no re-measurement."""
    control = _FlickerControl([CONSENT_LABEL])
    page = _consent_page(control)
    actions, sleep_calls = _run(page)

    assert page.clicked == [CONSENT_LABEL]
    cleared = _outcomes(actions, "cleared")
    assert len(cleared) == 1
    assert _outcomes(actions, "re_measured") == [], (
        "a stable control must not produce a re-measure log line"
    )
    assert _remeasure_sleeps(sleep_calls) == []
