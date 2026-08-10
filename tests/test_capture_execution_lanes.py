from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LANES_MODULE = REPO_ROOT / "tests" / "capture_lanes.py"


def _load_lanes_module():
    assert LANES_MODULE.is_file(), "capture lane classifier is missing"
    spec = importlib.util.spec_from_file_location(
        "bd_capture_lanes_under_test", LANES_MODULE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    ):
        assert (
            lanes.classify_capture_file(allowlisted, source=source)
            == "serial"
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
    code = lanes.code_only(source)
    lowered = code.lower()
    if any(snippet in lowered for snippet in lanes.ABSOLUTE_SERIAL_SNIPPETS):
        return True
    return bool(lanes.RUNTESTS_LITERAL.search(code))


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
    # THE FLOOR DROPPED FROM 100 TO 15 AT v3.66.992, AND THE REASON MATTERS.
    # It is not the guard weakening: the PREDICATE got more precise. While the
    # absolute check read raw source it counted every file that merely MENTIONED
    # the runner in a comment or docstring -- 143 of them, of which 4 actually
    # imported it. @990 made the check read CODE, so the honest population is
    # the files that really carry the hazard. Measured at v3.66.992: 22.
    #
    # The assertion's job is unchanged -- prove the denominator has not
    # COLLAPSED, so the per-file loop below is asserting over something. 15
    # keeps that with headroom under the measured 22, and a drop past it means
    # the strip or the constants broke rather than that the tree got tidier.
    assert len(hazardous) > 15, (
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

    assert allowlist
    for relative in sorted(allowlist):
        path = tests_root / relative
        assert path.is_file(), f"stale parallel allowlist entry: {relative}"
        assert lanes.classify_capture_file(path) == "parallel", relative

    for path in tests_root.rglob("test*.py"):
        if lanes.classify_capture_file(path) == "parallel":
            assert path.relative_to(tests_root).as_posix() in allowlist


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


def test_files_the_experiment_refuted_stay_serial() -> None:
    """Named, not merely omitted, so a later green run cannot promote them.

    The whole serial lane was run in parallel on the box at v3.66.920: five
    files failed and all five passed on a serial retry. Three were already
    source-flagged or listed; these two were neither, and they failed the same
    way in an independent container run. Two machines agreeing is the evidence.

    Omission would not have held -- the backfill is generated, so anything not
    explicitly refused gets regenerated back in. This is why the refusal lives
    in SERIAL_EXACT_BASENAMES rather than in a comment.
    """
    lanes = _load_lanes_module()
    for name in ("test_dev_suite_tier1b.py", "test_v3_66_717_exec_bridge.py"):
        assert name in lanes.SERIAL_EXACT_BASENAMES, name
        assert (
            lanes.classify_capture_file(
                REPO_ROOT / "tests" / name,
                source="def test_pure_looking(): assert True",
            )
            == "serial"
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
    parallel = sum(
        1
        for path in (REPO_ROOT / "tests").rglob("test_*.py")
        if lanes.classify_capture_file(path) == "parallel"
    )
    assert parallel >= 1000, (
        f"the parallel lane is down to {parallel} files. It was 1079 at "
        f"v3.66.923. If files were legitimately demoted, lower this floor in "
        f"the same commit and say which and why -- do not let it erode "
        f"silently, which is how the 45-minute capture happened."
    )
