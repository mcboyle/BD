"""BH-F050: bd-mcp gitea_ci(journal_job=...) must not let the job name escape
the remote shell command it is interpolated into (ssh RUNNER_HOST "<cmd>").

Executes the candidate's real gitea_ci/_refuse_forbidden source (extracted by
AST, decorators dropped) with _gitea and _run mocked: no network, no ssh.
Opt-in: BD_BH_F050_CANDIDATE=<absolute path to a bd-mcp server.py>.
"""

import ast
import os
import re
import shlex
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH_F050_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

# Real value seen in fleet transcripts: matrix job display names carry
# spaces and parentheses, so a bare slug regex would break the tool.
REAL_JOB = "gate-suites (measurement-isolation)"


@pytest.fixture
def harness() -> tuple[Any, Mock, Mock, str]:
    path = Path(CANDIDATE)
    assert path.is_absolute(), "candidate path must be absolute"
    assert path.is_file(), f"candidate missing: {path}"
    tree = ast.parse(path.read_text(), filename=str(path))
    selected: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {
            "gitea_ci",
            "_refuse_forbidden",
            # BH-DA001 helpers: gitea_ci now validates repo/run through these;
            # without them in the exec namespace every call is a NameError.
            "_valid_gitea_repo",
            "_valid_gitea_run",
        }:
            node.decorator_list = []
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"FORBIDDEN", "RUNNER_HOST"}
            for t in node.targets
        ):
            selected.append(node)
    assert {"gitea_ci", "_refuse_forbidden"} <= {
        n.name for n in selected if isinstance(n, ast.FunctionDef)
    }, "candidate lacks gitea_ci"
    jobs = [
        {
            "name": "lint",
            "status": "completed",
            "conclusion": "failure",
            "started_at": "2026-09-28T00:00:00Z",
            "completed_at": "2026-09-28T00:01:00Z",
        }
    ]
    api = Mock(side_effect=[{"workflow_runs": [{"id": 123}]}, {"jobs": jobs}])
    runner = Mock(
        return_value={"rc": 0, "out": "old\nError one\nFAILED two\n", "err": ""}
    )
    ns: dict[str, Any] = {"re": re, "shlex": shlex, "_gitea": api, "_run": runner}
    code = compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec")
    exec(code, ns)  # noqa: S102 -- only the opted-in candidate's functions
    return ns["gitea_ci"], runner, api, ns["RUNNER_HOST"]


@pytest.mark.parametrize(
    "job",
    [
        "lint'; printf injected; #",
        "x' ; id ; echo '",
        "lint'",
        "lint\nother",
        "lint\r",
        "lint\tother",
        "-lint",
        ".lint",
        "_lint",
        " lint",
        "(lint",
        "éclair",
        "lint/job",
        "lint;other",
        "lint|other",
        "lint&&other",
        "$(id)",
        "lint`id`",
        'lint"other',
        "lint\\other",
        "lint)$(id",
        "a" * 129,
    ],
)
def test_malformed_job_refused_before_runner(harness, job):
    tool, runner, api, _host = harness
    with pytest.raises(ValueError, match="^invalid journal_job$"):
        tool(journal_job=job)
    runner.assert_not_called()
    assert api.call_count == 2


@pytest.mark.parametrize(
    "job", ["lint", "Build_2.test-job", "0", "A-._9", REAL_JOB, "a" * 128]
)
def test_safe_job_keeps_the_legacy_command_byte_for_byte(harness, job):
    tool, runner, _api, host = harness
    result = tool(journal_job=job, journal_lines=2)
    legacy = (
        "sudo journalctl -u act_runner --since '6 hours ago' --no-pager "
        f"| grep -F '[CI/{job}]' "
        "| grep -vE 'DEBUG|::debug|Collecting|Downloading|━━|Requirement already' "
        "| grep -E 'FAILED|Error|error|❌|AssertionError|No module|E   |exitcode'"
        " | tail -n 2"
    )
    runner.assert_called_once_with(
        ["ssh", "-o", "ConnectTimeout=8", host, legacy], timeout=60
    )
    assert result["journal"] == {
        "job": job,
        "rc": 0,
        "lines": ["Error one", "FAILED two"],
        "err": "",
    }


def test_real_job_name_is_one_shell_token(harness):
    tool, runner, _api, _host = harness
    tool(journal_job=REAL_JOB)
    tokens = shlex.split(runner.call_args.args[0][-1])
    assert tokens[tokens.index("-F") + 1] == f"[CI/{REAL_JOB}]"
    assert tokens.count("|") == 4  # journalctl|grep|grep|grep|tail


def test_empty_job_skips_journal(harness):
    tool, runner, api, _host = harness
    result = tool(journal_job="")
    runner.assert_not_called()
    assert api.call_count == 2 and "journal" not in result
