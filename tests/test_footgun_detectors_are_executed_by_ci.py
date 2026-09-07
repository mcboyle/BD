"""FOOTGUNS.json and the CI shard denominator, linked in ONE direction.

A footgun may declare itself `blocking` and point its detector at a real test
file, and nothing anywhere requires CI to run that file. CI executes EXPLICIT
FILE LISTS -- ci.yml names every suite it runs, it never runs `tests/` -- so a
file in no shard list is never executed. The gate that stops suites falling out
of the matrix (test_v3_66_939_ci_gate_shards_cover_every_gate) only ever sees
files that carry BD_GATE_SCOPE or sit in its pinned legacy remainder, and a file
carrying NEITHER is invisible to it. FOOTGUNS.json is a separate registry, and
at v3.66.1513 it pointed five blocking detectors at exactly such files.

This gate links them in the direction that cannot create a third list: naming a
test in FOOTGUNS.json is enough to REQUIRE that test in a CI shard. Both
populations are derived -- one from the JSON structure, one from the parsed
workflow -- so neither can be kept in step by hand, and neither can go stale
without this failing.

THE EXTRACTION RULE, stated here because two readers extracted two different
denominators from the same file on the same night (8 and 9) and the difference
was never written down:

    A FOOTGUN TEST SUBJECT is any string matching `tests/<...>.py` appearing in
    ANY string value, at ANY depth, of a footgun whose `status` is "active".

That is the WIDEST structure-derived rule, and it is chosen deliberately over
the narrower `detector.test` reading. `detector.test` gives 8; the wide rule
gives 9, the extra being a file named under `protects`. Narrowing the rule to
the key one happens to care about is the shrink-the-denominator move that
FOOTGUNS.json's own FG-ADVISORY-MEANS-VISIBLE-NOT-DROPPED entry names, and a
footgun that names a test at all is claiming that test is load-bearing. The
cost of the wide rule is measured, not assumed: it admits exactly one extra
file today and that file is already in a shard.

INACTIVE footguns are excluded because every consumer in the tree filters
`status == "active"`; an inactive entry enforces nothing, so requiring lane time
for it would be this gate inventing policy rather than reading it.

UNKNOWN IS A FAILING THIRD STATE (A2/A7). A subject this gate cannot resolve --
a path naming a file that is not in the tree -- is neither covered nor a clean
miss; it is a registry that has drifted from the filesystem, and it FAILS here
rather than being skipped, because a skipped subject reads as a covered one in
every summary anyone will look at.
"""
BD_GATE_SCOPE = "repo-wide"

import json
import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_FOOTGUNS = _REPO / "FOOTGUNS.json"
_CI = _REPO / ".github" / "workflows" / "ci.yml"
_TEST_PATH = re.compile(r"tests/[A-Za-z0-9_./-]+\.py")

# ── the one exemption, and everything that stops it spreading ────────────────
#
# An exemption is a hole. It is here rather than absent because the honest
# alternative -- sharding a suite whose two runners disagree about it -- makes a
# BLOCKING lane flaky, and a flaky blocking gate gets disabled by the third
# person to hit it. The tests below pin the hole to exactly this shape: one
# entry, a reason, the file must still exist, and the moment the file IS in a
# shard the exemption must be deleted. It cannot outlive its cause quietly.
_EXEMPT: dict[str, str] = {
    "tests/test_v3_66_726_body_contract.py": (
        "The two runners disagree about this file: on clean a8267e5d pytest "
        "reports 12 passed and run_tests.py reports 10/12. Every other subject "
        "in this registry agrees under both. Adopting a suite that SPLITS by "
        "runner would make a blocking lane flaky. The exemption is the runner "
        "question staying open, not the footgun being waived -- FG-726 is still "
        "blocking and is still evaluated by hand until the split is settled."
    ),
}


def _footguns() -> list[dict]:
    return json.loads(_FOOTGUNS.read_text("utf-8"))["footguns"]


def _paths_in(node) -> set[str]:
    """Every `tests/<...>.py` string at any depth of one JSON value.

    Walks the STRUCTURE rather than the file text, so a path is found wherever
    it is written -- a detector, a prose `provenance`, a `protects` list -- and
    a path that appears only inside a JSON key, or only in a comment-shaped
    string outside the footgun, is never invented.
    """
    if isinstance(node, dict):
        return set().union(*(_paths_in(v) for v in node.values())) if node else set()
    if isinstance(node, list):
        return set().union(*(_paths_in(v) for v in node)) if node else set()
    if isinstance(node, str):
        return set(_TEST_PATH.findall(node))
    return set()


def footgun_test_subjects(footguns=None) -> dict[str, list[str]]:
    """{test path: [footgun ids naming it]} for ACTIVE footguns. The rule is
    the module docstring's, and this function is the only place it lives."""
    out: dict[str, list[str]] = {}
    for fg in _footguns() if footguns is None else footguns:
        if fg.get("status") != "active":
            continue
        for path in sorted(_paths_in(fg)):
            out.setdefault(path, []).append(fg.get("id", "<unnamed footgun>"))
    return out


def ci_executed_paths(workflow=None) -> set[str]:
    """Every `tests/<...>.py` CI actually executes, from the PARSED workflow.

    Parsed, never grepped, and that is load-bearing rather than tidy: ci.yml's
    own commentary names suites (`test_gui_parity 30.6s` in the timing block),
    and a text scan would count a file mentioned in a comment as executed. YAML
    parsing drops comments, so a path survives here only if it is inside a value
    the runner sees. It takes every job, not only the gate-suites matrix, so a
    suite executed by some other job is correctly reported as covered.
    """
    if workflow is None:
        workflow = yaml.safe_load(_CI.read_text("utf-8"))
    return _paths_in(workflow)


def uncovered(subjects, executed) -> list[str]:
    """Subjects no CI job executes, exemptions removed. EXTRACTED so it can be
    driven with synthetic inputs: a comparison whose only assertions live inside
    the test that computes it is a detector with no detector (the escape a
    battery found in the 939 gate's own `_coverage_delta`)."""
    return sorted(set(subjects) - set(executed) - set(_EXEMPT))


def spent_exemptions(exempt, executed) -> list[str]:
    """Exemptions whose subject IS now executed by CI.

    EXTRACTED for the same reason `uncovered` is, and it was not extracted in
    the first draft: a mutation battery neutered the live assertion below with
    ` or True` and every test still passed, because the only thing asserting on
    this comparison was the assertion being mutated. A hole that cannot be
    detected once it is spent is a hole that stays open.
    """
    return sorted(set(exempt) & set(executed))


def unresolvable(subjects) -> list[str]:
    """Subjects naming a file that is not in the tree. UNKNOWN, which fails."""
    return sorted(p for p in subjects if not (_REPO / p).is_file())


# ── the live gate ────────────────────────────────────────────────────────────

def test_every_active_footgun_test_subject_is_executed_by_some_ci_job():
    subjects = footgun_test_subjects()
    executed = ci_executed_paths()

    assert subjects, (
        "no footgun names a test file, so this gate has an empty denominator "
        "and proves nothing")
    assert executed, "ci.yml names no test file, so the coverage side is empty"
    covered = sorted(set(subjects) & set(executed))
    assert covered, (
        "POSITIVE CONTROL: not one subject reads as covered, so this gate is "
        "measuring its own extraction rather than the tree")

    missing = uncovered(subjects, executed)
    assert not missing, (
        "FOOTGUNS.json points an active detector at a test no CI job runs, so "
        "the footgun can only ever be evaluated by hand:\n  "
        + "\n  ".join(f"{p} <- {', '.join(subjects[p])}" for p in missing))


def test_a_footgun_subject_that_names_no_file_in_the_tree_is_unknown_and_fails():
    """A registry drifted from the filesystem is UNKNOWN, never a pass."""
    assert unresolvable(footgun_test_subjects()) == []


def test_the_denominator_is_the_whole_registry_not_the_detector_key_alone():
    """The wide rule's cost, asserted rather than assumed.

    If this ever fails because the wide set grew, the question to answer is
    whether the new file belongs in a lane -- not whether to narrow the rule.
    """
    subjects = footgun_test_subjects()
    detector_only = {
        (fg.get("detector") or {}).get("test")
        for fg in _footguns() if fg.get("status") == "active"}
    wide_extra = sorted(set(subjects) - {d for d in detector_only if d})
    assert wide_extra == ["tests/test_gui_parity.py"], wide_extra
    assert "tests/test_gui_parity.py" in ci_executed_paths(), (
        "the one file the wide rule adds must already be executed, which is "
        "what makes the wide rule free today")


# ── the exemption, pinned so it cannot spread or outlive its cause ───────────

def test_the_exemption_is_exactly_one_named_file_with_a_stated_reason():
    assert list(_EXEMPT) == ["tests/test_v3_66_726_body_contract.py"]
    reason = _EXEMPT["tests/test_v3_66_726_body_contract.py"]
    assert "run_tests" in reason and "10/12" in reason, (
        "the reason must name the measurement that justifies the hole")


def test_the_exempt_file_still_exists_and_is_still_uncovered():
    """The exemption dies with its cause, in either direction.

    If 726 is deleted, an exemption for a file nobody has is stale. If 726 IS
    put in a shard, the exemption is spent and must be removed rather than left
    to quietly cover the next file someone adds to the dict.
    """
    exempt = "tests/test_v3_66_726_body_contract.py"
    assert (_REPO / exempt).is_file()
    assert spent_exemptions(_EXEMPT, ci_executed_paths()) == [], (
        "726 is now in a CI shard, so its exemption is spent -- delete the "
        "_EXEMPT entry rather than leaving a hole with no subject")
    assert exempt in footgun_test_subjects(), (
        "726 is no longer named by any active footgun, so the exemption has no "
        "subject and must be deleted")


# ── negative controls: the gate must be able to SAY the thing it asserts ─────

def test_a_footgun_pointed_at_a_file_in_no_shard_is_reported():
    """THE RED. Point a synthetic active footgun at a file no shard names and
    the comparison must name it; a gate that cannot fail cannot pass."""
    synthetic = [{"id": "FG-SYNTHETIC", "status": "active",
                  "detector": {"test": "tests/test_no_shard_names_me.py"}}]
    subjects = footgun_test_subjects(synthetic)
    assert subjects == {"tests/test_no_shard_names_me.py": ["FG-SYNTHETIC"]}
    assert uncovered(subjects, ci_executed_paths()) == [
        "tests/test_no_shard_names_me.py"]


def test_an_inactive_footgun_contributes_no_subject():
    inactive = [{"id": "FG-RETIRED", "status": "retired",
                 "detector": {"test": "tests/test_no_shard_names_me.py"}}]
    assert footgun_test_subjects(inactive) == {}


def test_the_workflow_reader_ignores_a_suite_named_only_in_a_comment():
    """ci.yml's timing commentary names suites. A text scan would count them."""
    doc = ("jobs:\n  x:\n    steps:\n"
           "      # tests/test_only_in_a_comment.py 30.6s\n"
           "      - run: pytest tests/test_really_executed.py\n")
    executed = ci_executed_paths(yaml.safe_load(doc))
    assert executed == {"tests/test_really_executed.py"}


def test_the_uncovered_comparison_is_not_severed_from_either_input():
    """Drive both sides synthetically, so neither can be replaced by a constant."""
    assert uncovered({"tests/a.py": []}, {"tests/a.py"}) == []
    assert uncovered({"tests/a.py": []}, set()) == ["tests/a.py"]
    assert uncovered({}, {"tests/a.py"}) == []
    exempt = next(iter(_EXEMPT))
    assert uncovered({exempt: []}, set()) == [], (
        "the exemption must actually suppress its one entry")
    assert uncovered({"tests/test_v3_66_726_body_contract_v2.py": []}, set()) == [
        "tests/test_v3_66_726_body_contract_v2.py"], (
        "the exemption matches an exact path, never a prefix or a substring")


def test_a_spent_exemption_is_named_and_a_live_one_is_not():
    """Drive the spent-exemption comparison from both sides, so it cannot be
    severed from either input or replaced by a constant."""
    assert spent_exemptions({"tests/a.py": "reason"}, {"tests/a.py"}) == ["tests/a.py"]
    assert spent_exemptions({"tests/a.py": "reason"}, set()) == []
    assert spent_exemptions({}, {"tests/a.py"}) == []


def test_unresolvable_names_a_missing_file_and_not_a_present_one():
    assert unresolvable(["tests/test_no_such_file_exists_here.py"]) == [
        "tests/test_no_such_file_exists_here.py"]
    assert unresolvable([Path(__file__).relative_to(_REPO).as_posix()]) == []
