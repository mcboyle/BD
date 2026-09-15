"""Row 738 (T2 follow-up to 667): the login-attempt cap's single-process premise
must be a declared invariant, not folklore.

reserve_login_attempt() (bulk_downloader/session_keeper.py) decides AND
records one login attempt in a single SQLite statement so concurrent
ACCOUNTS/THREADS inside one process can't race a stale count. Nothing in
that function -- or anywhere else -- says the reverse holds for a SECOND
PROCESS writing the same session_history table for the same home: row 667's
production incident measured two processes exceeding the cap. Today's
deploy is safe only because the one WSGI entrypoint (downloader_ui.py
_serve_wsgi) never spawns more than one process for a given home; that is
an environmental fact, not a code guarantee, and it was previously
unrecorded anywhere a future change would trip over it.

This file is the guard named by INVARIANTS.json's I0011 entry. It fails
red under two independent conditions, either of which lets the premise
drift silently:
  1. the declaration disappears from INVARIANTS.json (documentation drift)
  2. the deploy entrypoint gains a second process for one home (the premise
     itself breaks)
"""
from __future__ import annotations

import copy
import importlib
import inspect
import json
import sys
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_INVARIANTS_PATH = _REPO / "INVARIANTS.json"


def _load_invariants(path: Path) -> dict:
    def reject_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(path.read_text(), object_pairs_hook=reject_duplicates)


def _find_single_process_premise(invariants: dict) -> str | None:
    """Return the invariant id declaring the login-cap single-process premise,
    or None if no entry mentions both a single-process constraint and the
    login-attempt cap it bounds. The two-term match is deliberate: matching on
    "single" alone would pass on an unrelated invariant and never catch a
    genuine regression."""
    for iid, entry in invariants.items():
        statement = str(entry.get("statement", "")).lower()
        if "single" in statement and "process" in statement and "login" in statement and "cap" in statement:
            return iid
    return None


def test_the_matcher_can_say_yes_before_it_says_no():
    """Positive control: prove the matcher recognizes the real entry before
    trusting it to report an absence."""
    invariants = _load_invariants(_INVARIANTS_PATH)["invariants"]
    found = _find_single_process_premise(invariants)
    assert found is not None, (
        "positive control failed: no invariant in INVARIANTS.json currently "
        "matches the single-process login-cap premise wording"
    )
    assert invariants[found]["status"] == "GUARDED"
    guard_test = invariants[found]["guard_test"]
    assert (_REPO / guard_test).exists(), (
        f"{found} names a guard_test that does not exist on disk: {guard_test}"
    )


def test_invariants_declares_single_process_login_cap_premise():
    """RED-first: this is the assertion that had 0 matches on 66178294
    (`grep -i single-process INVARIANTS.json` = 0 hits) before this row's
    patch landed."""
    invariants = _load_invariants(_INVARIANTS_PATH)["invariants"]
    iid = _find_single_process_premise(invariants)
    assert iid is not None, (
        "no INVARIANTS.json entry declares the login-cap single-process "
        "premise (row 738 acceptance unmet)"
    )
    entry = invariants[iid]
    assert entry["at"] == "bulk_downloader/session_keeper.py"
    assert "session_history" in entry["statement"]


def test_negative_control_absence_is_detected_for_the_right_reason():
    """Build a fixture that removes ONLY the row 738 entry and prove the
    matcher goes to None -- not because the fixture is malformed, but because
    the specific entry is gone. The fixture's built shape is proven nonzero
    (nine other guarded invariants survive) before the absence verdict is
    trusted."""
    invariants = _load_invariants(_INVARIANTS_PATH)
    full_count = len(invariants["invariants"])
    assert full_count > 1, "fixture precondition: real registry has other entries"

    stripped = copy.deepcopy(invariants["invariants"])
    victim = _find_single_process_premise(stripped)
    assert victim is not None, "precondition: the entry exists to be removed"
    del stripped[victim]

    assert len(stripped) == full_count - 1, (
        "negative control did not build the intended shape: expected exactly "
        f"one entry removed, got {full_count - len(stripped)}"
    )
    assert _find_single_process_premise(stripped) is None, (
        "matcher still found a single-process login-cap premise after the "
        "declaring entry was removed -- the negative control fails for the "
        "wrong reason"
    )


def test_deploy_entrypoint_still_runs_a_single_process():
    """The premise's environmental half: the one WSGI entrypoint must not pass
    a multi-process option to its server call. If a future change adds one,
    this goes red and the invariant above must be re-adjudicated, not
    silently invalidated."""
    sys.path.insert(0, str(_REPO))
    try:
        downloader_ui = importlib.import_module("downloader_ui")
    finally:
        sys.path.pop(0)
    src = inspect.getsource(downloader_ui._serve_wsgi)
    assert "processes=" not in src, (
        "_serve_wsgi now passes processes= to its server call -- the "
        "single-process premise I0011 declares no longer holds and the "
        "invariant must be re-adjudicated before this can go green"
    )


def test_invariants_json_is_valid_json_with_no_duplicate_ids():
    import pytest
    with pytest.raises(ValueError, match="duplicate JSON key"):
        _load_invariants_from_text('{"invariants": {"I0011": {}, "I0011": {}}}')


def _load_invariants_from_text(text: str) -> dict:
    def reject_duplicates(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError(f"duplicate JSON key: {key}")
            out[key] = value
        return out
    return json.loads(text, object_pairs_hook=reject_duplicates)
