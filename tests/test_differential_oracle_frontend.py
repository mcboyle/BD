from __future__ import annotations

import json
import multiprocessing
import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from tools.code_intelligence.adapters import (
    AdapterBudget,
    AdapterCase,
    AdapterContext,
    clear_adapters_for_test,
    get_adapter,
)
from tools.code_intelligence.artifacts import canonical_bytes
from tools.code_intelligence.oracle_adapters import register_builtin_oracles
from tools.code_intelligence.oracle_adapters import (
    BUILTIN_ORACLE_COMMANDS,
    CommandOracleAdapter,
)
import tools.code_intelligence.oracle_adapters as oracle_adapters
import tools.code_intelligence.oracle_service as oracle_service
from tools.code_intelligence.oracle_service import run_oracle_adapter
from tools.code_intelligence.results import CheckResult, ResultState
from tools.differential_oracle import main


@dataclass(frozen=True)
class PairAdapter:
    name: str = "pairs"
    kind: str = "oracle"

    def cases(self, context):
        return (
            AdapterCase(
                "same",
                {"left": "A", "right": "a", "allow": False},
            ),
            AdapterCase(
                "allowed",
                {"left": 1, "right": 2, "allow": True},
            ),
            AdapterCase(
                "forbidden",
                {"left": 1, "right": 3, "allow": False},
            ),
        )

    def run(self, case, context):
        payload = case.payload
        normalize = (
            lambda value: value.lower()
            if isinstance(value, str)
            else value
        )
        equal = normalize(payload["left"]) == normalize(payload["right"])
        return CheckResult(
            self.name,
            (
                ResultState.PASS
                if equal or payload["allow"]
                else ResultState.FAIL
            ),
            case.case_id,
            {
                "left": payload["left"],
                "right": payload["right"],
                "normalized_left": normalize(payload["left"]),
                "normalized_right": normalize(payload["right"]),
                "equal": equal,
                "allowed": payload["allow"],
                "reason": "fixture policy",
            },
        )


def _brief_child_exit() -> None:
    time.sleep(0.05)


def test_valid_payload_gets_bounded_worker_exit_grace() -> None:
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_brief_child_exit)
    process.start()

    assert oracle_service._join_after_payload(process)
    assert process.exitcode == 0


def test_completed_wrapper_gets_bounded_reader_drain_grace() -> None:
    reader = threading.Thread(target=time.sleep, args=(0.05,))
    reader.start()

    assert oracle_adapters._readers_finished_after_grace((reader,))
    assert not reader.is_alive()


def _context(tmp_path, timeout=1.0):
    return AdapterContext(
        tmp_path,
        tmp_path / "artifacts",
        tmp_path / "corpus",
        17,
        AdapterBudget(timeout, 10, 4096),
    )


def test_forbidden_divergence_fails_but_allowed_divergence_does_not(
    tmp_path,
):
    result, rows = run_oracle_adapter(PairAdapter(), _context(tmp_path))

    assert result.state is ResultState.FAIL
    assert [row.case_id for row in rows] == [
        "same",
        "allowed",
        "forbidden",
    ]
    assert next(
        row for row in rows if row.case_id == "allowed"
    ).allowed is True
    assert next(
        row for row in rows if row.case_id == "forbidden"
    ).allowed is False


@dataclass(frozen=True)
class SlowAdapter:
    name: str = "slow"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("sleep", {}),)

    def run(self, case, context):
        time.sleep(1.0)
        return CheckResult(
            self.name,
            ResultState.PASS,
            case.case_id,
            {},
        )


def test_timeout_is_not_a_pass(tmp_path):
    result, rows = run_oracle_adapter(
        SlowAdapter(),
        _context(tmp_path, timeout=0.05),
    )

    assert result.state is ResultState.TIMEOUT
    assert rows == ()


@dataclass(frozen=True)
class TooManyAdapter:
    name: str = "too-many"
    kind: str = "oracle"

    def cases(self, context):
        return tuple(
            AdapterCase(f"case-{index}", {})
            for index in range(context.budget.max_cases + 1)
        )

    def run(self, case, context):
        raise AssertionError("over-budget cases must never run")


def test_case_count_is_bounded_before_adapter_execution(tmp_path):
    result, rows = run_oracle_adapter(
        TooManyAdapter(),
        _context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "adapter exceeded max_cases"
    assert result.evidence == {"cases": 11}
    assert rows == ()


@dataclass(frozen=True)
class CrashAdapter:
    name: str = "crash"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("crash", {}),)

    def run(self, case, context):
        os._exit(7)


def test_child_crash_is_an_error_with_content_free_evidence(tmp_path):
    result, rows = run_oracle_adapter(CrashAdapter(), _context(tmp_path))

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker failed"
    assert result.evidence == {"exitcode": 7}
    assert rows == ()


@dataclass(frozen=True)
class InvalidResultAdapter:
    name: str = "invalid-result"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("invalid", {}),)

    def run(self, case, context):
        return {"state": "pass"}


def test_malformed_worker_result_is_not_a_pass(tmp_path):
    result, rows = run_oracle_adapter(
        InvalidResultAdapter(),
        _context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker payload invalid"
    assert rows == ()


@dataclass(frozen=True)
class SecretResultAdapter:
    name: str = "secret-result"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("secret", {}),)

    def run(self, case, context):
        return CheckResult(
            self.name,
            ResultState.PASS,
            "Bearer do-not-copy",
            {
                "left": "Bearer do-not-copy",
                "right": None,
                "normalized_left": None,
                "normalized_right": None,
                "equal": True,
                "allowed": False,
                "reason": "do-not-copy",
            },
        )


def test_secret_worker_evidence_is_rejected_without_echo(tmp_path):
    result, rows = run_oracle_adapter(
        SecretResultAdapter(),
        _context(tmp_path),
    )

    rendered = json.dumps(
        {
            "summary": result.summary,
            "evidence": result.evidence,
        },
        sort_keys=True,
    )
    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker payload invalid"
    assert "do-not-copy" not in rendered
    assert rows == ()


@dataclass(frozen=True)
class OversizedResultAdapter:
    name: str = "oversized-result"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("large", {}),)

    def run(self, case, context):
        return CheckResult(
            self.name,
            ResultState.PASS,
            case.case_id,
            {
                "left": "x" * 5000,
                "right": None,
                "normalized_left": "x" * 5000,
                "normalized_right": None,
                "equal": True,
                "allowed": False,
                "reason": "oversized comparison fixture",
            },
        )


def test_worker_ipc_bytes_are_bounded(tmp_path):
    context = AdapterContext(
        tmp_path,
        tmp_path / "artifacts",
        tmp_path / "corpus",
        17,
        AdapterBudget(1.0, 10, 128),
    )

    result, rows = run_oracle_adapter(OversizedResultAdapter(), context)

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker output exceeded budget"
    assert rows == ()


@dataclass(frozen=True)
class LargeValidResultAdapter:
    name: str = "large-valid-result"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("large-valid", {}),)

    def run(self, case, context):
        return CheckResult(
            self.name,
            ResultState.PASS,
            case.case_id,
            {
                "left": "x" * 100_000,
                "right": None,
                "normalized_left": "x" * 100_000,
                "normalized_right": None,
                "equal": True,
                "allowed": False,
                "reason": "large comparison fixture",
            },
        )


def test_sub_budget_worker_payload_is_drained_without_pipe_deadlock(
    tmp_path,
):
    context = AdapterContext(
        tmp_path,
        tmp_path / "artifacts",
        tmp_path / "corpus",
        17,
        AdapterBudget(2.0, 10, 250_000),
    )

    result, rows = run_oracle_adapter(
        LargeValidResultAdapter(),
        context,
    )

    assert result.state is ResultState.PASS
    assert len(rows) == 1
    assert len(rows[0].left) == 100_000


class UnpicklableAdapter:
    name = "unpicklable"
    kind = "oracle"

    def __init__(self):
        self.callback = lambda: None

    def cases(self, context):
        return ()

    def run(self, case, context):
        raise AssertionError("unpicklable adapter cannot run")


def test_spawn_serialization_failure_is_a_content_free_error(tmp_path):
    result, rows = run_oracle_adapter(
        UnpicklableAdapter(),
        _context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker failed"
    assert result.evidence == {"stage": "start"}
    assert rows == ()


@dataclass(frozen=True)
class UnknownAdapter:
    name: str = "unknown"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("optional", {}),)

    def run(self, case, context):
        return CheckResult(
            self.name,
            ResultState.UNKNOWN,
            case.case_id,
            {
                "left": None,
                "right": None,
                "normalized_left": None,
                "normalized_right": None,
                "equal": False,
                "allowed": True,
                "reason": "optional wrapper unavailable",
            },
        )


def test_unknown_adapter_result_remains_unknown(tmp_path):
    result, rows = run_oracle_adapter(UnknownAdapter(), _context(tmp_path))

    assert result.state is ResultState.UNKNOWN
    assert len(rows) == 1
    assert rows[0].reason == "optional wrapper unavailable"


@dataclass(frozen=True)
class MismatchedIdentityAdapter:
    name: str = "expected-name"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("identity", {}),)

    def run(self, case, context):
        return CheckResult(
            "wrong-name",
            ResultState.PASS,
            case.case_id,
            {
                "left": 0,
                "right": 0,
                "normalized_left": 0,
                "normalized_right": 0,
                "equal": True,
                "allowed": False,
                "reason": "identity fixture",
            },
        )


def test_worker_result_name_must_match_adapter_identity(tmp_path):
    result, rows = run_oracle_adapter(
        MismatchedIdentityAdapter(),
        _context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker payload invalid"
    assert rows == ()


@dataclass(frozen=True)
class ControlReasonAdapter:
    name: str = "control-reason"
    kind: str = "oracle"

    def cases(self, context):
        return (AdapterCase("control", {}),)

    def run(self, case, context):
        return CheckResult(
            self.name,
            ResultState.PASS,
            case.case_id,
            {
                "left": 0,
                "right": 0,
                "normalized_left": 0,
                "normalized_right": 0,
                "equal": True,
                "allowed": False,
                "reason": "line one\nline two",
            },
        )


def test_worker_reason_must_be_bounded_printable_text(tmp_path):
    result, rows = run_oracle_adapter(
        ControlReasonAdapter(),
        _context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker payload invalid"
    assert rows == ()


def _fake_wrapper(
    root: Path,
    name: str,
    stdout: str,
    returncode: int,
) -> CommandOracleAdapter:
    relative, flag = BUILTIN_ORACLE_COMMANDS[name]
    script = root / relative
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"raise SystemExit({returncode})\n",
        encoding="utf-8",
    )
    return CommandOracleAdapter(name, relative, flag)


def _fake_context(root: Path) -> AdapterContext:
    return AdapterContext(
        root,
        root / "artifacts",
        root,
        17,
        AdapterBudget(2.0, 20, 65536),
    )


def _diff_case(root: Path) -> AdapterCase:
    (root / "old.json").write_text('{"status":"old"}', encoding="utf-8")
    (root / "new.json").write_text('{"status":"new"}', encoding="utf-8")
    return AdapterCase(
        "diff",
        {"old": "old.json", "new": "new.json"},
    )


def test_symlinked_wrapper_is_error_not_optional_unknown(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("raise SystemExit(0)\n", encoding="utf-8")
    root = tmp_path / "repo"
    relative, flag = BUILTIN_ORACLE_COMMANDS["template-diff"]
    script = root / relative
    script.parent.mkdir(parents=True)
    try:
        script.symlink_to(outside)
    except (OSError, NotImplementedError):
        import pytest

        pytest.skip("symlinks unavailable")
    adapter = CommandOracleAdapter("template-diff", relative, flag)

    result = adapter.run(_diff_case(root), _fake_context(root))

    assert result.state is ResultState.ERROR
    assert result.summary == "wrapper path invalid"


def test_wrapper_json_rejects_duplicate_and_extra_fields(tmp_path):
    duplicate = (
        '{"added_keys":[],"removed_keys":[],"changed":[],'
        '"summary":{"added":0,"removed":0,"changed":0},'
        '"summary":{"added":0,"removed":0,"changed":0}}'
    )
    extra = (
        '{"added_keys":[],"removed_keys":[],"changed":[],'
        '"summary":{"added":0,"removed":0,"changed":0},"extra":0}'
    )
    for index, raw in enumerate((duplicate, extra)):
        root = tmp_path / f"repo-{index}"
        adapter = _fake_wrapper(root, "template-diff", raw, 0)

        result = adapter.run(_diff_case(root), _fake_context(root))

        assert result.state is ResultState.ERROR
        assert result.summary == "wrapped command JSON invalid"


def test_schema_wrapper_cannot_certify_tree_with_malformed_json(tmp_path):
    clean = (
        '{"schemas":0,"invalid":[],"conflicts":[],'
        '"summary":{"schemas":0,"invalid":0,"conflicts":0}}'
    )
    adapter = _fake_wrapper(tmp_path, "schema-oracle", clean, 0)
    work = tmp_path / "work"
    work.mkdir()
    (work / "broken.json").write_text("{", encoding="utf-8")

    result = adapter.run(
        AdapterCase("schema", {"work": "work"}),
        _fake_context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "adapter input invalid"


def test_builtin_registration_is_sorted_and_missing_wrapper_is_unknown(
    tmp_path,
):
    clear_adapters_for_test()
    names = register_builtin_oracles()

    assert names == (
        "consumer-agreement",
        "plugin-diff",
        "plugin-permission-diff",
        "rollback-oracle",
        "schema-oracle",
        "template-diff",
        "url-classifier-truth",
    )
    adapter = get_adapter("schema-oracle")
    case = AdapterCase("missing", {"work": "fixtures"})
    result = adapter.run(case, _context(tmp_path))
    assert result.state is ResultState.UNKNOWN


def test_consumer_wrapper_with_unbindable_hard_coded_root_is_unknown(
    tmp_path,
):
    clear_adapters_for_test()
    register_builtin_oracles()
    root = Path(__file__).resolve().parents[1]
    context = AdapterContext(
        root,
        tmp_path / "artifacts",
        root,
        17,
        AdapterBudget(1.0, 10, 4096),
    )

    result = get_adapter("consumer-agreement").run(
        AdapterCase("default", {}),
        context,
    )

    assert result.state is ResultState.UNKNOWN
    assert result.summary == "wrapper lacks explicit root"


def test_rollback_wrapper_preserves_informational_decision(tmp_path):
    clear_adapters_for_test()
    register_builtin_oracles()
    root = Path(__file__).resolve().parents[1]
    context = AdapterContext(
        root,
        tmp_path / "artifacts",
        root,
        17,
        AdapterBudget(5.0, 10, 65536),
    )

    result = get_adapter("rollback-oracle").run(
        AdapterCase(
            "guard",
            {"failure": "guard", "touched": 2, "tier": "low"},
        ),
        context,
    )

    assert result.state is ResultState.ADVISORY
    assert result.summary.startswith(
        "toolchain/bin/bd-rollback-oracle: "
        "rollback recommendation produced; exit 0"
    )


def test_cli_writes_deterministic_strict_source_bound_artifact(tmp_path):
    root = Path(__file__).resolve().parents[1]
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    arguments = [
        "--root",
        str(root),
        "--adapter",
        "url-classifier-truth",
        "--timeout",
        "5",
        "--max-cases",
        "10",
        "--max-output-bytes",
        "65536",
    ]

    assert main([*arguments, "--out", str(first)]) == 0
    assert main([*arguments, "--out", str(second)]) == 0
    first_value = json.loads(first.read_text(encoding="utf-8"))
    second_value = json.loads(second.read_text(encoding="utf-8"))

    assert first_value["schema_name"] == "bd.differential-oracle"
    assert first_value["schema_version"] == 1
    assert first_value["source_sha"] != "0" * 64
    assert first_value["input_hashes"]["tracked_tree"] == (
        first_value["source_sha"]
    )
    assert canonical_bytes(first_value) == canonical_bytes(second_value)

    invalid = dict(first_value)
    invalid["unexpected"] = True
    check = tmp_path / "invalid.json"
    check.write_text(json.dumps(invalid), encoding="utf-8")
    preserved = tmp_path / "preserved.json"
    preserved.write_text("unchanged", encoding="utf-8")

    assert main(
        [
            *arguments,
            "--out",
            str(preserved),
            "--check",
            str(check),
        ]
    ) == 1
    assert preserved.read_text(encoding="utf-8") == "unchanged"


def test_cli_durably_records_timeout_as_blocking_state(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "timeout.json"

    status = main(
        [
            "--root",
            str(root),
            "--adapter",
            "url-classifier-truth",
            "--timeout",
            "0.000001",
            "--max-cases",
            "10",
            "--max-output-bytes",
            "65536",
            "--out",
            str(output),
        ]
    )

    assert status == 1
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["results"][0]["state"] == "timeout"
    assert artifact["results"][0]["evidence"] == {
        "comparisons": 0,
        "forbidden": 0,
    }


@dataclass(frozen=True)
class EmptyAdapter:
    name: str = "empty"
    kind: str = "oracle"

    def cases(self, context):
        return ()

    def run(self, case, context):
        raise AssertionError("an empty adapter has no runnable case")


def test_zero_case_adapter_is_not_pass(tmp_path):
    result, rows = run_oracle_adapter(EmptyAdapter(), _context(tmp_path))

    assert result.state is ResultState.ERROR
    assert result.summary == "oracle worker payload invalid"
    assert rows == ()


def test_empty_manifest_cannot_bypass_missing_wrapper(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "schema-oracle.json").write_text(
        '{"cases":[]}',
        encoding="utf-8",
    )
    relative, flag = BUILTIN_ORACLE_COMMANDS["schema-oracle"]
    adapter = CommandOracleAdapter("schema-oracle", relative, flag)

    result, rows = run_oracle_adapter(adapter, _fake_context(root))

    assert result.state is ResultState.UNKNOWN
    assert len(rows) == 1
    assert rows[0].reason == "optional wrapper unavailable"


def test_invalid_manifest_cannot_become_url_classifier_pass(tmp_path):
    clean = (
        '{"dangerous":[],"overblock":[],'
        '"summary":{"tested":26,"bypass":0,"overblock":0}}'
    )
    adapter = _fake_wrapper(
        tmp_path,
        "url-classifier-truth",
        clean,
        0,
    )
    (tmp_path / "url-classifier-truth.json").write_text(
        '{"cases":"invalid"}',
        encoding="utf-8",
    )

    result, rows = run_oracle_adapter(adapter, _fake_context(tmp_path))

    assert result.state is ResultState.ERROR
    assert len(rows) == 1
    assert rows[0].reason == "adapter input invalid"


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    status = Path(f"/proc/{pid}/stat")
    if status.is_file():
        try:
            after_name = status.read_text(encoding="utf-8").rpartition(")")[2]
            if after_name.split()[0] == "Z":
                return False
        except (IndexError, OSError):
            pass
    return True


def _wait_for_pid_exit(pid: int, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_is_running(pid):
            return True
        time.sleep(0.01)
    return not _pid_is_running(pid)


def test_unsupported_descendant_containment_never_launches_wrapper(
    tmp_path,
    monkeypatch,
):
    relative, flag = BUILTIN_ORACLE_COMMANDS["url-classifier-truth"]
    script = tmp_path / relative
    script.parent.mkdir(parents=True)
    marker = tmp_path / "wrapper-launched"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "Path('wrapper-launched').write_text('yes', encoding='utf-8')\n"
        "print(json.dumps({\n"
        "    'dangerous': [],\n"
        "    'overblock': [],\n"
        "    'summary': {'tested': 26, 'bypass': 0, 'overblock': 0},\n"
        "}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tools.code_intelligence.oracle_adapters.sys.platform",
        "darwin",
    )
    adapter = CommandOracleAdapter(
        "url-classifier-truth",
        relative,
        flag,
    )

    result = adapter.run(
        AdapterCase("unsupported-platform", {}),
        _fake_context(tmp_path),
    )

    assert result.state is ResultState.ERROR
    assert result.evidence["left"] is None
    assert result.evidence["right"] is None
    assert marker.exists() is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group regression")
@pytest.mark.parametrize(
    ("mode", "expected_state", "timeout", "max_output"),
    [
        ("normal", ResultState.PASS, 2.0, 65536),
        ("overflow", ResultState.ERROR, 2.0, 128),
        ("timeout", ResultState.TIMEOUT, 0.2, 65536),
        ("crash", ResultState.ERROR, 2.0, 65536),
    ],
)
def test_wrapper_background_descendant_is_reaped_on_every_completion_path(
    tmp_path,
    mode,
    expected_state,
    timeout,
    max_output,
):
    relative, flag = BUILTIN_ORACLE_COMMANDS["url-classifier-truth"]
    script = tmp_path / relative
    script.parent.mkdir(parents=True)
    script.write_text(
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "child = subprocess.Popen([\n"
        "    sys.executable,\n"
        "    '-c',\n"
        "    'import time; time.sleep(60)',\n"
        "],\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL,\n"
        ")\n"
        "Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
        f"mode = {mode!r}\n"
        "if mode == 'normal':\n"
        "    print(json.dumps({\n"
        "        'dangerous': [],\n"
        "        'overblock': [],\n"
        "        'summary': {'tested': 26, 'bypass': 0, 'overblock': 0},\n"
        "    }))\n"
        "elif mode == 'overflow':\n"
        "    sys.stdout.write('x' * 10000)\n"
        "elif mode == 'timeout':\n"
        "    time.sleep(60)\n"
        "else:\n"
        "    os._exit(7)\n",
        encoding="utf-8",
    )
    adapter = CommandOracleAdapter(
        "url-classifier-truth",
        relative,
        flag,
    )
    context = AdapterContext(
        tmp_path,
        tmp_path / "artifacts",
        tmp_path,
        17,
        AdapterBudget(timeout, 10, max_output),
    )
    child_pid: int | None = None
    leaked = False
    try:
        result = adapter.run(AdapterCase("background", {}), context)
        child_pid = int(
            (tmp_path / "child.pid").read_text(encoding="utf-8")
        )
        leaked = not _wait_for_pid_exit(child_pid)
    finally:
        if child_pid is not None and _pid_is_running(child_pid):
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _wait_for_pid_exit(child_pid)

    assert result.state is expected_state
    assert leaked is False
