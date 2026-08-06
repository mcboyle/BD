"""capture.sh must probe /api/selftest, and must not be reassured by a 200.

THE GAP. capture.sh never called /api/selftest -- `grep -n selftest capture.sh`
returned nothing. So no capture bundle could confirm or refute ANY selftest
change, and every selftest check on the box sat permanently outside the
capture's denominator. SESSION_CARRY 15.19 records the bite: a session grepped
the 844 bundle for "stale lock", got silence, and reported the WARN as gone.
The claim was true and the evidence was worthless -- a denominator that
structurally cannot contain the subject, reporting clean.

WHY A BARE `curl -fsS` WOULD REPRODUCE THE DEFECT IT FIXES. capture.sh's own
header records step [7] probing `sse_smoke`, which is not a registered route: it
returned {"error":"endpoint not found"} on every run and nothing noticed,
because curl exits 0 on a 200 carrying an error body. A stage that only checks
curl's exit code would write a reassuring log and make the blind spot INVISIBLE
rather than absent. The load-bearing assertion is therefore the non-empty
denominator -- (ok + warn + fail) >= 1 -- not the HTTP status.

WARN DOES NOT FAIL THE CAPTURE, deliberately. tools/capture_verdict.py has no
warn tier for stage exits, and its own comments record why live WARNs were
ungated: gating them "reported FAIL on a healthy box that no code change could
ever turn green ... a gate that cries wolf gets switched off." So row 2 below
must stay exit 0, and a test asserts it -- over-sensitivity is a soundness bug
in the same way blindness is (CLAUDE.md section 0).

THE FAKE CURL CAN DISAGREE WITH ITSELF. CLAUDE.md section 6 records two shipped
harness defects of exactly this kind: a fake curl that answered every URL
identically, so the check under test could never observe the disagreement it
exists to detect, and a fake python returning 0 for every -c. Here the body and
the exit code are both parameterised, and the seven rows below span three
distinct verdicts.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shell_source import shell_code_only  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_SH = REPO_ROOT / "capture.sh"
VERDICT_TOOL = REPO_ROOT / "tools" / "selftest_verdict.py"
PYTHON = sys.executable

_HEALTHY = {"ok": True, "summary": {"ok": 15, "warn": 0, "fail": 0},
            "checks": [{"status": "ok", "name": f"c{i}"} for i in range(15)]}
_WARNING = {"ok": True, "summary": {"ok": 12, "warn": 3, "fail": 0},
            "checks": ([{"status": "ok", "name": f"c{i}"} for i in range(12)]
                       + [{"status": "warn", "name": f"w{i}"} for i in range(3)])}
_FAILING = {"ok": False, "summary": {"ok": 13, "warn": 0, "fail": 2},
            "checks": ([{"status": "ok", "name": f"c{i}"} for i in range(13)]
                       + [{"status": "fail", "name": f"f{i}"} for i in range(2)])}
_NOT_FOUND = {"error": "endpoint not found"}
_EMPTY = {"ok": True, "summary": {"ok": 0, "warn": 0, "fail": 0}, "checks": []}


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _grade(path: Path) -> int:
    """Run the grader, having first proved the grader EXISTS.

    Without this precondition every "must be nonzero" row below passes
    vacuously on a tree with no tool: python exits 2 when it cannot open the
    script, which is nonzero for a reason that has nothing to do with grading.
    Measured on pristine source -- six of seven such rows went green before this
    assertion was added, which is CLAUDE.md section 0 inside the RED battery
    written to close a section-0 defect.
    """
    assert VERDICT_TOOL.is_file(), (
        f"{VERDICT_TOOL} does not exist -- a nonzero exit here would grade "
        "nothing and prove nothing"
    )
    return subprocess.run(
        [PYTHON, str(VERDICT_TOOL), str(path)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    ).returncode


# --------------------------------------------------------------------------
# The grader. Rows 4-7 are the whole point of the tool existing.
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "label,payload,should_pass",
    [
        ("healthy", _HEALTHY, True),
        ("warnings-only", _WARNING, True),
        ("real-failures", _FAILING, False),
        ("error-body-behind-200", _NOT_FOUND, False),
        ("empty-denominator", _EMPTY, False),
    ],
)
def test_verdict_tool_grades_each_response_shape(
    tmp_path, label, payload, should_pass
) -> None:
    code = _grade(_write_json(tmp_path / f"{label}.json", payload))
    if should_pass:
        assert code == 0, f"{label} should be a clean stage, got exit {code}"
    else:
        assert code != 0, (
            f"{label} must not read as a clean stage -- that is the shape that "
            "makes a blind spot invisible instead of absent"
        )


def test_verdict_tool_rejects_malformed_json(tmp_path) -> None:
    broken = tmp_path / "truncated.json"
    broken.write_text('{"ok": true, "summary": {"ok": 15, "war', encoding="utf-8")
    assert _grade(broken) != 0, "a truncated body must not grade as a clean run"


def test_verdict_tool_rejects_a_missing_file(tmp_path) -> None:
    assert _grade(tmp_path / "never-written.json") != 0, (
        "an absent file means the probe never produced evidence; silence is not a pass"
    )


def test_verdict_tool_rejects_summary_inconsistent_with_checks(tmp_path) -> None:
    """A body whose counts do not match its own checks is not evidence."""
    forged = {"ok": True, "summary": {"ok": 15, "warn": 0, "fail": 0},
              "checks": [{"status": "ok", "name": "only-one"}]}
    assert _grade(_write_json(tmp_path / "forged.json", forged)) != 0


def test_verdict_tool_separates_cannot_evaluate_from_real_failure(tmp_path) -> None:
    """Both are nonzero, but they must not be the same nonzero.

    capture_verdict.py collapses every nonzero to FAIL, so this distinction is
    for the operator reading the log, not for the gate -- an unreadable body and
    a genuinely failing box are different problems with different fixes.
    """
    failing = _grade(_write_json(tmp_path / "f.json", _FAILING))
    unknown = _grade(_write_json(tmp_path / "u.json", _NOT_FOUND))
    assert failing != unknown, (
        f"real failures and cannot-evaluate both exited {failing}; the log "
        "cannot tell the operator which happened"
    )


# --------------------------------------------------------------------------
# The stage, extracted on STRUCTURE and executed with a fake curl.
# --------------------------------------------------------------------------
def _extract_stage() -> str:
    lines = CAPTURE_SH.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("#") and "[7b/9]" in ln]
    ends = [i for i, ln in enumerate(lines) if ln.startswith("#") and "[8/9]" in ln]
    assert len(starts) == 1, f"expected one [7b/9] banner, got {len(starts)}"
    assert len(ends) == 1, f"expected one [8/9] banner, got {len(ends)}"
    assert starts[0] < ends[0], "the selftest stage must sit before step [8]"
    return "\n".join(lines[starts[0]:ends[0]])


def _run_stage(tmp_path: Path, *, body: str, curl_exit: int = 0) -> tuple[int, str]:
    """Run the real extracted stage against a fake curl. Returns (SELFTEST_EXIT, log)."""
    fragment = _extract_stage()

    syntax = subprocess.run(["bash", "-n"], input=fragment,
                            capture_output=True, text=True, timeout=30)
    assert syntax.returncode == 0, (
        "the extracted fragment is not valid bash -- this is a HARNESS failure, "
        f"not a subject failure:\n{syntax.stderr}"
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    body_file = tmp_path / "body.txt"
    body_file.write_text(body, encoding="utf-8")
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "{curl_exit}" -ne 0 ]; then\n'
        f'  echo "curl: (7) Failed to connect to localhost port 5555" >&2\n'
        f'  exit {curl_exit}\n'
        "fi\n"
        f'cat "{body_file}"\n',
        encoding="utf-8",
    )
    curl.chmod(0o755)

    out_dir = tmp_path / "capture-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "stage.sh"
    script.write_text(
        f'set -u\nOUT="{out_dir}"\n' + fragment
        + '\nprintf "SELFTEST_EXIT=%s\\n" "$SELFTEST_EXIT" > "$OUT/../exit.txt"\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    subprocess.run(["bash", str(script)], cwd=str(REPO_ROOT), env=env,
                   capture_output=True, text=True, timeout=120)

    marker = (tmp_path / "exit.txt").read_text(encoding="utf-8").strip()
    code = int(marker.split("=", 1)[1])
    log = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(out_dir.iterdir()) if p.is_file()
    )
    return code, log


def test_stage_passes_on_a_healthy_battery(tmp_path) -> None:
    code, log = _run_stage(tmp_path, body=json.dumps(_HEALTHY))
    assert code == 0, f"a healthy battery must not fail the capture. Log:\n{log}"


def test_stage_does_not_fail_the_capture_on_warnings(tmp_path) -> None:
    """The over-sensitivity direction, and it is not optional.

    capture_verdict.py collapses any nonzero stage exit to FAIL, so gating WARNs
    would report FAIL on a healthy box that no code change could turn green --
    the precedent capture_verdict.py:138-148 already set for live WARNs.
    """
    code, log = _run_stage(tmp_path, body=json.dumps(_WARNING))
    assert code == 0, (
        f"WARNs must not fail the capture -- a gate that cries wolf gets "
        f"switched off. Log:\n{log}"
    )


def test_stage_fails_on_real_selftest_failures(tmp_path) -> None:
    code, _ = _run_stage(tmp_path, body=json.dumps(_FAILING))
    assert code != 0, "a battery reporting FAILs must fail the stage"


def test_stage_fails_on_an_error_body_behind_a_200(tmp_path) -> None:
    """capture.sh's own sse_smoke bug, reproduced as a test.

    curl exits 0 here. A stage keyed on curl's exit code alone would report a
    clean selftest for a route that does not exist.
    """
    code, log = _run_stage(tmp_path, body=json.dumps(_NOT_FOUND))
    assert code != 0, (
        f"an error body behind a 200 must not read as a clean battery -- this is "
        f"the recorded sse_smoke defect. Log:\n{log}"
    )


def test_stage_fails_on_an_empty_denominator(tmp_path) -> None:
    code, log = _run_stage(tmp_path, body=json.dumps(_EMPTY))
    assert code != 0, (
        f"zero checks executed is not a pass -- unknown is a third state. Log:\n{log}"
    )


def test_stage_fails_when_curl_cannot_connect(tmp_path) -> None:
    code, _ = _run_stage(tmp_path, body="", curl_exit=7)
    assert code != 0, "a refused connection must not read as a clean battery"


def test_stage_reports_the_transport_failure_rather_than_a_parse_error(
    tmp_path,
) -> None:
    """Closing a mutation escape, and the escape is worth reading.

    A mutant that ignored curl's exit code stayed GREEN: the fall-through fed an
    EMPTY body to the grader, which correctly returned CANNOT EVALUATE, so the
    stage was still nonzero and `assert code != 0` could not tell the two paths
    apart. The stage exit alone does not constrain this branch.

    What the branch actually buys is the DIAGNOSIS. An unreachable service and a
    service answering with a body we cannot parse are different problems with
    different fixes, and the log is where the operator learns which one happened
    -- so that is what is asserted here, along with curl's own stderr surviving
    into the bundle.
    """
    code, log = _run_stage(tmp_path, body="", curl_exit=7)

    assert code == 7, (
        f"the transport failure must carry curl's own exit code through, not be "
        f"relabelled as a grading outcome; got {code}"
    )
    assert "curl exit=7" in log, (
        f"the log never says the probe could not reach the service, so an "
        f"unreachable box reads like an unparseable answer. Log:\n{log}"
    )
    assert "Failed to connect" in log, (
        f"curl's stderr did not survive into the bundle. Log:\n{log}"
    )


def test_stage_writes_the_raw_body_for_the_operator(tmp_path) -> None:
    """The bundle must carry the evidence, not only the verdict."""
    _, log = _run_stage(tmp_path, body=json.dumps(_WARNING))
    assert "warn" in log, f"the raw selftest body never reached $OUT. Log:\n{log}"


# --------------------------------------------------------------------------
# Wiring into the release verdict.
# --------------------------------------------------------------------------
def test_selftest_exit_reaches_capture_verdict() -> None:
    code = shell_code_only(CAPTURE_SH)
    at = code.find("tools/capture_verdict.py")
    assert at != -1, "capture.sh no longer calls capture_verdict.py"
    stages = re.findall(r'--stage-exit\s+"([^"]+)"', code[at:])
    assert stages, "no --stage-exit pairs found; the denominator is empty"
    joined = " ".join(stages)
    assert "SELFTEST_EXIT" in joined, (
        f"the selftest stage never reaches the verdict, so a failing battery "
        f"cannot turn the capture red. Pairs found: {stages}"
    )


def test_capture_sh_has_no_surviving_comment_lines() -> None:
    """The sharp constraint: a `#` inside a multi-line quoted string survives
    stripping, because _strip_shell_comments carries quote state across lines.
    That is why the JSON parsing lives in tools/, not an inline `python -c`.
    """
    code = shell_code_only(CAPTURE_SH)
    survivors = [ln for ln in code.splitlines() if ln.lstrip().startswith("#")]
    assert not survivors, (
        f"comment lines survived stripping -- a quoted multi-line program is "
        f"the usual cause: {survivors[:5]}"
    )
