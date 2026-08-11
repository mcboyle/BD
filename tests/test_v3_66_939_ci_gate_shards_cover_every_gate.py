"""v3.66.939 -- the CI gate lane is sharded, and a shard can silently lose a file.

WHY THE LANE WAS SPLIT. `ci.yml`'s own comment set the rule on 2026-08-03:
"81 tests, 52s -- keep it under a minute; if it grows past that, SPLIT rather
than silently dropping files, because a truncated list here reads as coverage
it does not have." Re-measured 2026-08-07 at v3.66.938: 161 tests, 140s in CI.

Per-file timings on this container, which are what the shard boundaries are
drawn from -- the lane is not evenly distributed and a split by COUNT would
have missed that entirely:

    test_toolchain_534                 72.5s     <- 40% of the lane alone
    test_gui_parity                    30.6s
    test_import_graph_no_new_edges     16.6s
    test_v3_66_653_dep_freshness       11.2s
    test_route_index_in_sync           10.8s
    the remaining ten, combined        38.1s
                                      ------
                                      179.8s

So `test_toolchain_534` gets a shard to itself; no two-way split could have put
every lane under the budget while that file stayed whole, and profiling it
shows 59s of its 68s in four subprocess-heavy tests that walk the 240-tool
suite -- not a cheap win, and not safe to trim.

WHAT THIS FILE GUARDS, AND IT IS NOT THE TIMING. Sharding introduces exactly
one new failure mode, and it is the one the original comment named: a file that
falls out of every shard still leaves a GREEN tick. Nothing else in the tree
would notice -- the job passes, the check is green, and the gate that was
supposed to run simply did not. That is a denominator quietly shrinking, which
is the defect class CLAUDE.md section 0 is entirely about.

The assertions are therefore about COVERAGE, never about duration:

  * the union of the shards is exactly the declared set -- a drop fails, and so
    does an addition nobody declared;
  * no file appears in two shards, because a duplicate inflates the apparent
    coverage while the real one may still be missing;
  * every named path exists and is tracked, because `pytest tests/typo.py`
    exits non-zero but a path that merely MOVED would be a silent no-op if the
    runner were ever made lenient;
  * the declared set is non-empty, because every assertion above passes
    vacuously over an empty list.

DELIBERATELY NOT ASSERTED: how long any shard takes. A timing assertion here
would fail on a slow runner -- a gate firing on identity rather than content,
which CLAUDE.md section 0 counts as a soundness bug of equal weight. The budget
is a rule for humans reading the comment, not a test.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip(
    "yaml",
    reason="PyYAML is declared in requirements-test.txt; a missing import here "
           "means the test environment is unprovisioned, not that the workflow "
           "is correct")

_REPO = Path(__file__).resolve().parent.parent
_CI = _REPO / ".github" / "workflows" / "ci.yml"

# The gates that must run on every PR. Pinned HERE rather than derived from
# ci.yml, because deriving the expectation from the thing under test is how a
# dropped file passes: the union would simply shrink to match. Adding a
# repo-wide gate to CI is meant to be a two-file change.
_DECLARED = {
    "tests/test_all_sources_parse.py",
    "tests/test_pin_index_in_sync.py",
    "tests/test_route_index_in_sync.py",
    "tests/test_import_graph_no_new_edges.py",
    "tests/test_source_windows_do_not_shift.py",
    "tests/test_generated_artifacts_are_not_tracked.py",
    "tests/test_settings_center_slice4.py",
    "tests/test_versync_gate.py",
    "tests/test_release_hygiene_gates.py",
    "tests/test_scan_version_pins_fixture.py",
    "tests/test_gui_parity.py",
    "tests/test_pk_mirrors_stay_retired.py",
    "tests/test_toolchain_534.py",
    "tests/test_v3_66_799_audit_tool_selftests.py",
    "tests/test_v3_66_653_dep_freshness.py",
    # @1035. The isolation shard. These three are repo-wide despite not
    # looking it: 1034 enumerates git ls-files for the leaker ratchet, and
    # all three assert invariants about the SUITE rather than a module --
    # the plugins guard holding, the leaker population not growing, and no
    # live PyPI call from a dependency. Added in the SAME cut that created
    # them, because 944, 947, 1031 and 1034 were all added to the tree and
    # never to this list, and a gate CI does not run is a gate that does
    # not exist.
    "tests/test_v3_66_1034_guards_survive_a_module_wipe.py",
    "tests/test_v3_66_1031_socket_recorder_stages.py",
    "tests/test_no_test_writes_the_repo_plugins_dir.py",
}


def _workflow() -> dict:
    return yaml.safe_load(_CI.read_text("utf-8"))


def _shard_lists() -> dict[str, list[str]]:
    """{shard name: [test paths]} from the matrix include entries.

    Reads the matrix rather than grepping the run block: a grep would count a
    path named in a comment, and this file's own docstring names several.
    """
    wf = _workflow()
    for job_name, job in (wf.get("jobs") or {}).items():
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if not include:
            continue
        out = {}
        for entry in include:
            if "suites" not in entry:
                continue
            out[str(entry.get("name") or len(out))] = str(entry["suites"]).split()
        if out:
            return out
    return {}


def _tracked(rel: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel],
                          cwd=str(_REPO), capture_output=True).returncode == 0


# ── coverage ─────────────────────────────────────────────────────────────────

def test_the_shards_exist_at_all():
    """RED on pristine: there is no matrix, so there is nothing to cover with."""
    shards = _shard_lists()
    assert shards, (
        "no sharded gate job found in ci.yml -- expected a job whose "
        "strategy.matrix.include entries each carry a `suites` string. Without "
        "it every assertion below passes over an empty set.")
    assert len(shards) >= 2, (
        f"found {len(shards)} shard(s); a one-shard 'split' is the unsplit lane "
        f"wearing a matrix.")


def _coverage_delta(declared: set[str], got: set[str]) -> tuple[list[str], list[str]]:
    """(missing, extra) between the declared gate set and what the shards name.

    EXTRACTED SO IT CAN BE TESTED DIRECTLY. A mutation battery severed each of
    these two comparisons from its meaning -- `declared - got` became `got - got`,
    and `got - declared` became a constant -- and NO test noticed either, because
    the only assertions about them lived inside the test being mutated. A
    detector with no detector, which is the same escape @938 closed one cut ago
    and the reason it is worth extracting on sight rather than after a battery.
    """
    return sorted(declared - got), sorted(got - declared)


def test_the_coverage_comparison_actually_compares():
    """The positive control for the gate below.

    Synthetic sets whose answer is not in doubt: one declared-but-absent, one
    named-but-undeclared, one present in both. If either direction stops
    depending on its inputs, the gate underneath it is decoration.
    """
    missing, extra = _coverage_delta({"a", "shared"}, {"shared", "c"})
    assert missing == ["a"], (
        f"the declared-but-absent direction returned {missing!r}; a gate that "
        f"cannot see a dropped suite is the whole failure mode of sharding.")
    assert extra == ["c"], (
        f"the named-but-undeclared direction returned {extra!r}; the two lists "
        f"could drift with only one of them being read.")

    same = {"x", "y"}
    assert _coverage_delta(same, set(same)) == ([], []), (
        "identical sets reported a delta -- the gate would fire on every clean "
        "tree and be switched off, which CLAUDE.md section 0 counts as a "
        "soundness bug of equal weight to a false clean.")


def test_the_shard_union_is_exactly_the_declared_gate_set():
    """The assertion the whole file exists for.

    A dropped file leaves CI green while the gate does not run. Nothing else in
    the tree notices, which is why this is pinned against a set declared here
    rather than against ci.yml itself.
    """
    union: list[str] = []
    for names in _shard_lists().values():
        union.extend(names)
    got = set(union)

    missing, extra = _coverage_delta(_DECLARED, got)
    assert not missing, (
        f"repo-wide gate(s) declared but in NO shard, so they no longer run on "
        f"any PR while the check stays green: {missing}")
    assert not extra, (
        f"shard(s) name suite(s) that are not in the declared gate set: "
        f"{extra}. Add them to _DECLARED with a reason, or remove them -- an "
        f"undeclared entry means the two lists have drifted and only one of "
        f"them is being read.")


def test_no_suite_is_listed_in_two_shards():
    """A duplicate inflates apparent coverage and wastes the budget the split
    exists to respect."""
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for shard, names in _shard_lists().items():
        for n in names:
            if n in seen:
                dupes.append(f"{n} (in {seen[n]} and {shard})")
            seen[n] = shard
    assert not dupes, f"suite(s) listed in more than one shard: {dupes}"


def test_every_sharded_suite_exists_and_is_tracked():
    """A path that moved would run nothing. pytest exits non-zero on a missing
    file today, but that is the runner's behaviour and not a property this
    workflow states."""
    bad = []
    for shard, names in _shard_lists().items():
        for n in names:
            if not (_REPO / n).is_file():
                bad.append(f"{n} ({shard}): not on disk")
            elif not _tracked(n):
                bad.append(f"{n} ({shard}): untracked")
    assert not bad, "sharded suite path(s) that cannot run:\n  " + "\n  ".join(bad)


def test_the_declared_set_is_not_empty():
    """Every assertion above is vacuous over an empty declaration."""
    assert _DECLARED, "the declared gate set is empty; this file proves nothing"
    for rel in sorted(_DECLARED):
        assert (_REPO / rel).is_file(), (
            f"{rel} is declared as a repo-wide gate but is not in the "
            f"checkout -- fix the declaration rather than letting the union "
            f"assertion fail for the wrong reason.")


# ── the split must not have cost the setup the lane depends on ───────────────

def test_the_shard_job_checks_out_full_history():
    """bd-freshcheck (inside test_toolchain_534) resolves the register's close
    tip with `merge-base --is-ancestor`.

    Under a shallow checkout that commit is absent and the exit code is 128 --
    "I cannot see it", which is a different thing from 1, "not in this history".
    The gates job carries `fetch-depth: 0` for this reason; a shard job running
    the same suite needs it too, and losing it in the split would be a silent
    downgrade.
    """
    wf = _workflow()
    for job_name, job in (wf.get("jobs") or {}).items():
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if not any("suites" in e for e in include):
            continue
        checkouts = [s for s in (job.get("steps") or [])
                     if str(s.get("uses", "")).startswith("actions/checkout")]
        assert checkouts, f"{job_name} never checks out the repository"
        depths = [(s.get("with") or {}).get("fetch-depth") for s in checkouts]
        assert 0 in depths, (
            f"{job_name} does not set fetch-depth: 0. bd-freshcheck's "
            f"close-tip ancestry check needs full history; without it the "
            f"answer is UNKNOWN and the failure reads as a stale register.")
        return
    pytest.fail("no sharded gate job found to check")


def test_the_shard_job_installs_runtime_dependencies():
    """The suites import the product. A shard that skips the install fails for
    an environmental reason that reads as a real defect (CLAUDE.md section 5)."""
    ci = _CI.read_text("utf-8")
    wf = _workflow()
    for job_name, job in (wf.get("jobs") or {}).items():
        include = (((job.get("strategy") or {}).get("matrix") or {})
                   .get("include") or [])
        if not any("suites" in e for e in include):
            continue
        body = "\n".join(str(s.get("run", "")) for s in (job.get("steps") or []))
        assert "requirements.txt" in body, (
            f"{job_name} never installs requirements.txt")
        assert "requirements-test.txt" in body, (
            f"{job_name} never installs requirements-test.txt -- PyYAML and the "
            f"test-only dependencies live there, and this very file "
            f"importorskips on one of them, so the omission would present as a "
            f"SKIP rather than a failure.")
        return
    pytest.fail("no sharded gate job found to check")
