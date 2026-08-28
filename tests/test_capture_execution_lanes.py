from __future__ import annotations

BD_GATE_SCOPE = "repo-wide"

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LANES_MODULE = REPO_ROOT / "tests" / "capture_lanes.py"

# These are MECHANICAL facts derived from the curated allowlist after row 324's
# three evidence-backed promotions. They do not claim a new whole-tree review:
# 1,253 memberships retain row 292's evidence and exactly three have the
# per-file mechanism and two-width measurements recorded beside the lane set.
# The digest canonicalisation is sorted non-comment membership with one UTF-8
# newline after every entry; an actual allowlist edit updates both facts and its
# review evidence in the same commit.
_MECHANICAL_PARALLEL_ALLOWLIST_COUNT = 1264
_MECHANICAL_PARALLEL_ALLOWLIST_SHA256 = (
    "f6f56f9ae338a4b6cf11cda5599605bc033728238f277807a47b83832178c13e"
)
_PARALLEL_RATCHET_MARGIN = 10
_PARALLEL_RATCHET_FLOOR = (
    _MECHANICAL_PARALLEL_ALLOWLIST_COUNT - _PARALLEL_RATCHET_MARGIN
)


def _load_lanes_module():
    assert LANES_MODULE.is_file(), "capture lane classifier is missing"
    spec = importlib.util.spec_from_file_location(
        "bd_capture_lanes_under_test", LANES_MODULE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tracked_test_files() -> frozenset[str]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--", "tests/test*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AssertionError(
            "tracked test denominator is UNKNOWN because git ls-files "
            f"could not run: {exc}"
        ) from exc
    assert listed.returncode == 0, (
        "tracked test denominator is UNKNOWN because git ls-files failed: "
        + listed.stderr
    )
    tracked = frozenset(
        Path(line).relative_to("tests").as_posix()
        for line in listed.stdout.splitlines()
        if line
    )
    assert tracked, "tracked test denominator is zero; lane verdict is UNKNOWN"
    return tracked


def _parallel_allowlist_digest(allowlist: frozenset[str]) -> str:
    canonical = "".join(f"{relative}\n" for relative in sorted(allowlist))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assert_parallel_allowlist_pin(allowlist: frozenset[str]) -> None:
    digest = _parallel_allowlist_digest(allowlist)
    assert digest == _MECHANICAL_PARALLEL_ALLOWLIST_SHA256, (
        "parallel allowlist is outside its mechanically pinned curated set: "
        "an entry was added, removed, or replaced without updating the "
        f"allowlist evidence (actual sha256={digest})"
    )


def _assert_parallel_ratchet(parallel: int) -> None:
    assert parallel >= _PARALLEL_RATCHET_FLOOR, (
        f"the parallel lane is down to {parallel} files. The mechanically "
        f"derived allowlist count was {_MECHANICAL_PARALLEL_ALLOWLIST_COUNT}; "
        f"the stated margin is {_PARALLEL_RATCHET_MARGIN} files and the floor "
        f"is {_PARALLEL_RATCHET_FLOOR}. If files were legitimately demoted, "
        "update the count, digest, and evidence in the same commit -- do not "
        "let the lane erode silently toward the 45-minute serial capture."
    )


def _collect(marker: str, test_path: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            marker,
            test_path,
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_classifier_routes_each_risky_category_to_serial() -> None:
    lanes = _load_lanes_module()

    # v3.66.923: every path here carries a _zzsynth_ marker so it CANNOT be a
    # real file. Three of these names were real, and once the allowlist covered
    # the tree they classified parallel and failed this test for a reason that
    # had nothing to do with the classifier -- the same stale-exemplar trap
    # test_classifier_defaults_unreviewed_files_to_serial hit at v3.66.921.
    # A synthetic case must be synthetic.
    cases = [
        ("tests/test_zzsynth_global_probe.py",
         "pytestmark = pytest.mark.bd_module_wipe"),
        ("tests/test_zzsynth_runner_contract.py", "RUNNER = 'run_tests.py'"),
        ("tests/test_zzsynth_browser_flow.py", ""),
        ("tests/test_zzsynth_service_install.py", ""),
        ("tests/test_zzsynth_artifact_workflow.py", ""),
        ("tests/test_zzsynth_network_probe.py", ""),
    ]
    for path, source in cases:
        assert lanes.classify_capture_file(path, source=source) == "serial", path

    assert (
        lanes.classify_capture_file(
            "tests/test_validators.py",
            source="def test_rejects_bad_path(): assert True",
        )
        == "parallel"
    )


def test_classifier_serializes_unscoped_state_and_external_io_signals() -> None:
    lanes = _load_lanes_module()

    # Synthetic for the same reason as above -- these assert the UNLISTED
    # default, so a real (and therefore possibly allowlisted) name would test
    # the allowlist instead of the heuristic.
    cases = [
        ("tests/test_zzsynth_state_probe.py", 'sys.modules["probe"] = object()'),
        ("tests/test_zzsynth_env_probe.py", 'os.environ["PROBE"] = "dirty"'),
        ("tests/test_zzsynth_cwd_probe.py", 'os.chdir("/tmp")'),
        ("tests/test_zzsynth_client_probe.py", 'import httpx\nhttpx.get("https://example")'),
        ("tests/test_zzsynth_transport_probe.py", "import socket\nsocket.create_connection(addr)"),
        ("tests/test_zzsynth_index_sync.py", 'Path("PIN_INDEX.json").read_text()'),
    ]
    for path, source in cases:
        assert lanes.classify_capture_file(path, source=source) == "serial", path


def test_allowlisted_file_cannot_bypass_dynamic_runner_import_risk() -> None:
    lanes = _load_lanes_module()
    allowlisted = "tests/test_validators.py"

    for source in (
        'import importlib\nimportlib.import_module("run_tests")',
        'from importlib import import_module as load\nload("run_tests_core")',
        '__import__("run_tests")',
        # v3.66.998: the demonstrated ESCAPE (SESSION_CARRY 15.79) -- a
        # file-loader with a path literal whose trailing ".py" defeats both
        # the quote-anchored literal regex and the "run_tests.py" substring.
        # Proven RED against the pre-fix classifier: it returned "parallel".
        'import importlib.util\n'
        'spec = importlib.util.spec_from_file_location('
        '"rtc", "run_tests_core.py")\n'
        'rtc = importlib.util.module_from_spec(spec)\n'
        'spec.loader.exec_module(rtc)\n',
        # ...and the nested form a real file used: the literal sits inside a
        # Path division, not as a bare argument.
        'from importlib.util import spec_from_file_location\n'
        'from pathlib import Path\n'
        'spec = spec_from_file_location('
        '"rtc", Path(".") / "run_tests_core.py")\n',
        # ...and the UNPARSEABLE form, which exercises the AST check's
        # fail-closed fallback -- the only runner reference here is one no
        # substring or quote-anchored check matches, so a fallback that
        # returned False would classify this parallel. bd-mutate proved
        # exactly that mutant ESCAPED before this case existed.
        'spec_from_file_location("rtc", "run_tests_core.py")\n'
        'def broken(:\n',
        # Load forms added when the rule was re-instrumented from substrings
        # to AST loads. The monkeypatch dotted path was INVISIBLE to the old
        # instrument -- pytest resolves the string by importing the module
        # into this interpreter, and the quote-anchored literal regex needed
        # the closing quote right after the module name -- so the precise
        # rule is wider here, not narrower.
        'import runpy\nrunpy.run_path("run_tests.py")\n',
        'def test_x(monkeypatch):\n'
        '    monkeypatch.setattr("run_tests_core._FILE_TIMEOUT_S", 1)\n',
        'from unittest import mock\n'
        'with mock.patch("run_tests_core.main"):\n    pass\n',
        'import pytest\npytest.importorskip("run_tests_core")\n',
        # The alias-assignment form: the callee name says nothing, the
        # binding does. A callee-name check without alias tracking misses it.
        'import importlib\nload = importlib.import_module\n'
        'NAME = "run_tests_core"\nload(NAME)\n',
        # The fail-closed indirection: a loader-capable call fed a VARIABLE,
        # in a file that also names the runner. Containment is statically
        # unprovable, so it pins -- review included.
        'import importlib\nNAME = "run_tests_core"\n'
        'importlib.import_module(NAME)\n',
    ):
        assert (
            lanes.classify_capture_file(allowlisted, source=source)
            == "serial"
        )


def test_a_contained_runner_literal_is_reviewable() -> None:
    """RED before the precise rule: the flip side of the test above.

    A runner literal in a subprocess argv or a heredoc driver string is
    executed -- if at all -- by a CHILD interpreter, which mutates its own
    state and exits. Measured at eb0c00b: 12 real files carried the name only
    in such positions and sat in the serial lane for it. A reviewed
    (allowlisted) file with those shapes and no load now classifies parallel;
    the unlisted default stays serial (pinned by the @990 suite's companion
    test, and by the fail-closed default itself)."""
    lanes = _load_lanes_module()
    source = (
        'import subprocess, sys\n'
        'def test_x(tmp_path):\n'
        '    subprocess.run([sys.executable, "run_tests.py", "--json"],\n'
        '                   timeout=120)\n'
    )
    assert (
        lanes.classify_capture_file("tests/test_validators.py", source=source)
        == "parallel"
    )
    # ...and the fail-closed indirection rule requires BOTH halves: a
    # loader-capable call on a variable in a file with NO runner literal is
    # loading something else, and pinning it would demote a large share of
    # the reviewed lane (dynamic import over module lists is a common test
    # shape). Only the conjunction with a runner-naming literal is
    # unprovable containment.
    source = (
        'import importlib\n'
        'def test_mods():\n'
        '    for name in ("json", "ast"):\n'
        '        importlib.import_module(name)\n'
    )
    assert (
        lanes.classify_capture_file("tests/test_validators.py", source=source)
        == "parallel"
    )


def _has_source_hazard(lanes, path) -> bool:
    """True when a file's SOURCE trips the one check the allowlist may not override.

    NARROWED at v3.66.923. It used to cover every source heuristic, which was
    right while those were absolute. They are not any more: whole-tree
    experimental evidence promoted them, and only the fallback-runner import --
    which rewires global interpreter state -- stayed absolute.

    Still reuses the classifier's own constants rather than restating them. A
    restatement drifts: while backfilling the allowlist at v3.66.921 a
    hand-picked subset of SERIAL_SOURCE_SNIPPETS omitted five entries, and
    seven files were promoted that the real predicate refuses. The instrument
    fixes the denominator; borrowing it fixes the predicate too.

    v3.66.992: it now borrows the classifier's `code_only` as well, for the same
    reason it borrows the constants. @990 made the absolute check read CODE
    rather than prose; this helper kept reading raw source, so the guard and the
    classifier held two different definitions of "hazard" and the guard failed
    on every file promoted for having only a docstring mention. Borrowing the
    constants but not the text they are applied to is half a borrow -- and this
    caught it on the first run, which is the argument for deriving the subject
    rather than restating it.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return True
    # v3.66.998: borrow the WHOLE predicate, not its parts. This helper used
    # to restate snippets + literal regex; when the classifier gained the
    # dynamic-loader check the restatement would have silently held a second,
    # narrower definition of "hazard" -- the exact drift @992 caught when it
    # borrowed the constants but not code_only.
    return lanes.runner_import_hazard(lanes.code_only(source))


def test_classifier_defaults_unreviewed_files_to_serial() -> None:
    lanes = _load_lanes_module()

    assert (
        lanes.classify_capture_file(
            "tests/test_unreviewed_probe.py",
            source="def test_pure_looking_but_unreviewed(): assert True",
        )
        == "serial"
    )
    assert (
        lanes.classify_capture_file(
            REPO_ROOT / "tests" / "test_validators.py",
        )
        == "parallel"
    )
    # v3.66.921: these two assertions used to NAME two real files as exemplars
    # of "risky". That pinned the allowlist's contents rather than the
    # classifier's behaviour, so the moment either file was reviewed and
    # promoted the test failed for a reason that had nothing to do with the
    # property it exists to protect. It fired exactly that way when the
    # allowlist was backfilled from 173 to 783.
    #
    # The property is now asserted over the whole tree and derives its own
    # subject: EVERY file carrying a source-level hazard must classify serial,
    # allowlisted or not. That cannot go stale as the allowlist grows, and it
    # is a strictly stronger claim than two filenames were.
    hazardous = [
        path
        for path in sorted((REPO_ROOT / "tests").rglob("test_*.py"))
        if _has_source_hazard(lanes, path)
    ]
    # THE FLOOR DROPPED FROM 100 TO 15 AT v3.66.992, AND FROM 15 TO 8 WHEN THE
    # RULE WAS RE-INSTRUMENTED FROM SUBSTRINGS TO AST LOADS. Same reason both
    # times: the PREDICATE got more precise, not the guard weaker. @990 made
    # the check read CODE (143 mentions -> 22 code matches); the successor cut
    # made it read LOADS, so a subprocess argv or heredoc literal no longer
    # counts. Measured at eb0c00b over 1279 tracked test files: 11 -- the 7
    # files that import, import_module or spec-load the runner in-process,
    # plus 4 whose loader-capable calls take variables in files that also name
    # the runner (containment statically unprovable, fail-closed).
    #
    # The assertion's job is unchanged -- prove the denominator has not
    # COLLAPSED, so the per-file loop below is asserting over something. 8
    # keeps that with headroom under the measured 11, and a drop past it means
    # the walk or the constants broke rather than that the tree got tidier.
    assert len(hazardous) > 8, (
        f"only {len(hazardous)} hazardous files found -- the denominator "
        f"collapsed and the assertion below would mean nothing"
    )
    for path in hazardous:
        assert lanes.classify_capture_file(path) == "serial", (
            f"{path.name} carries a source-level hazard but classified "
            f"parallel. Source checks are ABSOLUTE: an allowlist entry may "
            f"override a filename token, never a construct that leaks across "
            f"files inside an xdist worker."
        )


def test_parallel_manifest_is_explicit_complete_and_risk_free() -> None:
    lanes = _load_lanes_module()
    tests_root = REPO_ROOT / "tests"
    allowlist = lanes.parallel_allowlist()
    tracked = _tracked_test_files()

    assert allowlist
    _assert_parallel_allowlist_pin(allowlist)
    assert allowlist <= tracked, (
        "parallel allowlist contains untracked paths: "
        f"{sorted(allowlist - tracked)}"
    )
    for relative in sorted(allowlist):
        path = tests_root / relative
        assert path.is_file(), f"stale parallel allowlist entry: {relative}"
        assert lanes.classify_capture_file(path) == "parallel", relative

    parallel = set()
    for relative in sorted(tracked):
        path = tests_root / relative
        if lanes.classify_capture_file(path) == "parallel":
            assert relative in allowlist
            parallel.add(relative)

    assert 0 < len(parallel) <= len(tracked), (
        f"classified parallel denominator {len(parallel)} is invalid for "
        f"{len(tracked)} tracked files; lane verdict is UNKNOWN"
    )
    _assert_parallel_ratchet(len(parallel))


def test_a_new_unreviewed_file_stays_serial_without_invalidating_the_gate(
    monkeypatch,
) -> None:
    """Tree growth is safe because an unlisted file cannot enter xdist."""
    lanes = _load_lanes_module()
    tracked = _tracked_test_files()
    relative = "test_row292_new_unreviewed_probe.py"

    assert relative not in tracked
    assert relative not in lanes.parallel_allowlist()
    assert (
        lanes.classify_capture_file(
            REPO_ROOT / "tests" / relative,
            source="def test_pure_looking_but_unreviewed(): assert True",
        )
        == "serial"
    )

    grown = tracked | {relative}
    assert len(grown) == len(tracked) + 1
    with pytest.raises(AssertionError):
        # RED control for the retired design: exact equality rejects this safe
        # growth even though the classifier above kept it out of xdist.
        assert len(grown) == len(tracked)

    monkeypatch.setattr(
        sys.modules[__name__], "_load_lanes_module", lambda: lanes
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_tracked_test_files", lambda: grown
    )
    test_parallel_manifest_is_explicit_complete_and_risk_free()


def test_an_allowlist_addition_outside_the_pinned_digest_is_rejected(
    monkeypatch,
) -> None:
    """Allowlisting is the unsafe direction, so an addition needs evidence."""
    lanes = _load_lanes_module()
    tracked = _tracked_test_files()
    allowlist = lanes.parallel_allowlist()
    candidates = []
    for relative in sorted(tracked - allowlist):
        path = REPO_ROOT / "tests" / relative
        source = path.read_text(encoding="utf-8")
        if (
            path.name.lower() not in lanes.SERIAL_EXACT_BASENAMES
            and not lanes.runner_import_hazard(lanes.code_only(source))
        ):
            candidates.append(relative)

    assert candidates, "no safe-looking unlisted file reaches the negative control"
    relative = candidates[0]
    path = REPO_ROOT / "tests" / relative
    assert lanes.classify_capture_file(path) == "serial"

    augmented = allowlist | {relative}
    assert len(augmented) == len(allowlist) + 1
    monkeypatch.setattr(lanes, "parallel_allowlist", lambda: augmented)
    assert lanes.classify_capture_file(path) == "parallel"

    monkeypatch.setattr(
        sys.modules[__name__], "_load_lanes_module", lambda: lanes
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_tracked_test_files", lambda: tracked
    )
    with pytest.raises(
        AssertionError,
        match="outside its mechanically pinned curated set",
    ):
        test_parallel_manifest_is_explicit_complete_and_risk_free()


def test_a_failed_git_tracked_census_is_unknown(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["git", "ls-files"],
        returncode=2,
        stdout="",
        stderr="forced git failure",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(AssertionError, match="UNKNOWN because git ls-files failed"):
        _tracked_test_files()


def test_a_zero_tracked_census_is_unknown(monkeypatch) -> None:
    completed = subprocess.CompletedProcess(
        args=["git", "ls-files"],
        returncode=0,
        stdout="",
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(AssertionError, match="tracked test denominator is zero"):
        _tracked_test_files()


def test_real_pytest_collection_selects_safe_and_serial_lanes() -> None:
    parallel = _collect("capture_parallel", "tests/test_validators.py")
    assert parallel.returncode == 0, parallel.stdout + parallel.stderr
    assert "test_validators.py" in parallel.stdout

    serial = _collect(
        "capture_serial", "tests/test_v3_66_797_runner_isolate.py"
    )
    assert serial.returncode == 0, serial.stdout + serial.stderr
    assert "test_v3_66_797_runner_isolate.py" in serial.stdout


def test_capture_script_gives_workers_only_to_parallel_lane() -> None:
    source = (REPO_ROOT / "capture.sh").read_text(encoding="utf-8")

    assert "-m capture_parallel" in source
    assert '-n "$WORKERS"' in source
    assert '--junitxml="$OUT/02_pytest_parallel.xml"' in source

    assert "-m capture_serial" in source
    assert "-n 0" in source
    assert '--junitxml="$OUT/02_pytest_serial.xml"' in source

    assert source.count("--junit ") >= 2
    assert '"$OUT/02_pytest_parallel.xml"' in source
    assert '"$OUT/02_pytest_serial.xml"' in source


def test_an_allowlist_entry_overrides_a_filename_token() -> None:
    """v3.66.921: the capability the backfill needed, pinned.

    A filename is not a behaviour. `SERIAL_NAME_TOKENS` matches substrings like
    "capture" and "runner", and 88 files were serial for that reason alone with
    no risky construct in them. The tokens are a proxy for "nobody has reviewed
    this"; an allowlist entry IS a review, so it now wins.

    The tokens still bite for every UNLISTED file -- the assertion below
    re-checks that, because a change making them inert entirely would satisfy
    the first half and destroy the fail-closed default.
    """
    lanes = _load_lanes_module()
    tests_root = REPO_ROOT / "tests"
    allowlist = lanes.parallel_allowlist()

    promoted = [
        tests_root / rel
        for rel in sorted(allowlist)
        if any(tok in rel.lower() for tok in lanes.SERIAL_NAME_TOKENS)
    ]
    assert promoted, (
        "no allowlisted file carries a name token, so this test is asserting "
        "over an empty set and proves nothing"
    )
    for path in promoted:
        assert lanes.classify_capture_file(path) == "parallel", path.name

    # ...and the token still routes an UNREVIEWED file to serial.
    assert (
        lanes.classify_capture_file(
            "tests/test_capture_not_reviewed_probe.py",
            source="def test_pure(): assert True",
        )
        == "serial"
    )


def test_serial_exact_pins_match_row324_mechanism_measurements() -> None:
    """Only mechanisms still present in source remain exact serial pins.

    Row 324 re-derived all six names rather than treating a favourable race as
    evidence, and promoted three. ROW 327 RETURNED ONE OF THEM. The fleet
    capture of v3.66.1306 itself -- bd_capture-20260828T021337Z-0d53fd2c on
    test3 -- failed twice inside test_v3_66_729_body_contract_fixtures.py:
    test_unknown_only_ever_shrinks (UNKNOWN rose to 135 against a 134 baseline)
    and test_verdicts_are_order_independent_across_probe_runs, whose message is
    "state is leaking across probe runs (fixture isolation regression)". That
    is precisely the @754 app-singleton mechanism row 324 believed closed.

    THE LESSON IS ABOUT THE SHAPE OF THE MEASUREMENT, NOT THE CARE TAKEN. Six
    green runs at ``-n 2`` and ``-n 4`` cannot express a leak that needs many
    co-resident files to surface; the capture parallel lane is far wider. A
    promotion is only refuted at the width the lane actually runs.
    perf_lab and t14_vpn_probe_egress stay promoted: their mechanisms are
    named, fixed in source, and did not fail anywhere in that same capture.
    """
    lanes = _load_lanes_module()

    still_serial = {
        "test_dev_suite_tier1b.py",
        "test_v3_66_717_exec_bridge.py",
        "test_v3_66_797_runner_isolate.py",
        # Row 327: promoted at v3.66.1306, refuted by that release's own
        # capture, returned here. See the docstring.
        "test_v3_66_729_body_contract_fixtures.py",
    }
    promoted = {
        "test_perf_lab.py",
        "test_t14_vpn_probe_egress.py",
    }

    assert lanes.SERIAL_EXACT_BASENAMES == still_serial
    assert promoted <= lanes.parallel_allowlist()
    assert not (promoted & lanes.SERIAL_EXACT_BASENAMES)

    for name in still_serial:
        assert name in lanes.SERIAL_EXACT_BASENAMES, name
        assert (
            lanes.classify_capture_file(
                REPO_ROOT / "tests" / name,
                source="def test_pure_looking(): assert True",
            )
            == "serial"
        ), name

    for name in promoted:
        assert (
            lanes.classify_capture_file(REPO_ROOT / "tests" / name)
            == "parallel"
        ), name


def test_the_parallel_lane_did_not_collapse_back() -> None:
    """A ratchet on the backfill, because its value is entirely in its size.

    The lane split became fail-closed at 1ae076a with 173 files reviewed, and
    nothing ever backfilled it -- so as the suite grew to 1232 files, 86% of it
    drifted into the serial lane and capture went from ~10 minutes to ~45. That
    regression was silent precisely because a fail-closed default raises no
    error. This makes a repeat loud.
    """
    lanes = _load_lanes_module()
    allowlist = lanes.parallel_allowlist()
    _assert_parallel_allowlist_pin(allowlist)
    parallel = sum(
        1
        for relative in allowlist
        if lanes.classify_capture_file(REPO_ROOT / "tests" / relative)
        == "parallel"
    )
    assert 0 < parallel <= len(allowlist), (
        f"classified parallel denominator {parallel} is invalid for "
        f"{len(allowlist)} allowlisted files; lane verdict is UNKNOWN"
    )
    _assert_parallel_ratchet(parallel)


def test_parallel_lane_ratchet_negative_control_rejects_a_regression() -> None:
    assert _MECHANICAL_PARALLEL_ALLOWLIST_COUNT == 1264
    assert _PARALLEL_RATCHET_MARGIN == 10
    assert _PARALLEL_RATCHET_FLOOR == 1254
    with pytest.raises(
        AssertionError,
        match=r"down to 1253 files.*count was 1264.*margin is 10.*floor is 1254",
    ):
        _assert_parallel_ratchet(_PARALLEL_RATCHET_FLOOR - 1)


def test_transform_control_imports_lanes_without_judging_lane_population() -> None:
    """Mutation transform control: valid imports alone make no census verdict."""
    lanes = _load_lanes_module()
    assert lanes.__name__ == "bd_capture_lanes_under_test"


def test_tool_state_partition_has_five_parallel_loadfile_units() -> None:
    """The split is useful only when every resulting file reaches xdist.

    `--dist loadfile` hands one FILE to one worker, so splitting a 494.7s module
    buys nothing unless each piece is in the parallel allowlist -- four shards
    left in the serial lane would be the same critical path wearing new names.
    """
    lanes = _load_lanes_module()
    allowlist = lanes.parallel_allowlist()
    partition = {
        "test_v3_66_1046_gates_for_this_sessions_shapes.py",
        "test_v3_66_1046_tool_state_1040.py",
        "test_v3_66_1046_tool_state_1043.py",
        "test_v3_66_1046_tool_state_1044.py",
        "test_v3_66_1046_tool_state_1054.py",
    }
    assert len(partition) == 5
    missing = sorted(partition - allowlist)
    assert not missing, (
        "row 332 split the tool-state module so loadfile could spread it, but "
        f"these pieces are not in the parallel lane and would still serialise: {missing}"
    )
    for name in sorted(partition):
        assert (REPO_ROOT / "tests" / name).is_file(), name


def test_recognizer_corpus_partition_has_five_parallel_loadfile_units() -> None:
    """The corpus split is useful only when every piece reaches xdist.

    `--dist loadfile` gives one FILE to one worker, so splitting the corpus buys
    nothing unless each shard is in the parallel allowlist -- four shards left in
    the serial lane would be the same critical path wearing new names. The
    behavioural denominator stays 46; this asserts only where the work RUNS.
    """
    lanes = _load_lanes_module()
    allowlist = lanes.parallel_allowlist()
    partition = {
        "test_recognizer_corpus.py",
        "test_recognizer_corpus_shard_a.py",
        "test_recognizer_corpus_shard_b.py",
        "test_recognizer_corpus_shard_c.py",
        "test_recognizer_corpus_shard_d.py",
    }
    assert len(partition) == 5
    missing = sorted(partition - allowlist)
    assert not missing, (
        "row 333 split the recognizer corpus so loadfile could spread it, but "
        f"these pieces are not in the parallel lane and would still serialise: {missing}"
    )
    for name in sorted(partition):
        assert (REPO_ROOT / "tests" / name).is_file(), name
