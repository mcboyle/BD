"""Behavior tests for the deterministic fuzz replay frontend."""

from __future__ import annotations

import errno
import argparse
import hashlib
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tools.code_intelligence.adapters import (
    AdapterBudget,
    AdapterCase,
    AdapterContext,
    register_adapter,
)
from tools.code_intelligence.fuzz_service import (
    effective_case_hash,
    load_corpus,
    run_fuzz_adapter,
    run_hypothesis_adapter,
)
from tools.code_intelligence.results import CheckResult, ResultState, exit_code
import tools.fuzz_harness as fuzz_harness
from tools.code_intelligence.fuzz_adapters import (
    CommandFuzzAdapter,
    register_builtin_fuzzers,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FixtureFuzzer:
    name: str = "fixture"
    kind: str = "fuzz"

    def cases(self, context: AdapterContext):
        return load_corpus(context.corpus_dir / "corpus.json", max_cases=context.budget.max_cases)

    def run(self, case: AdapterCase, context: AdapterContext) -> CheckResult:
        if case.case_id == "crash":
            raise RuntimeError("fixture crash")
        if case.case_id == "sleep":
            time.sleep(1.0)
        return CheckResult(self.name, ResultState.PASS, case.case_id, {"case_id": case.case_id})


@dataclass(frozen=True)
class DescendantFuzzer:
    name: str = "descendant"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        return (AdapterCase("fork", {}),)

    def run(self, _case: AdapterCase, context: AdapterContext) -> CheckResult:
        pid_path = context.corpus_dir / "descendant.pid"
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid_path.write_text(str(child.pid), encoding="utf-8")
        time.sleep(30.0)
        return CheckResult(self.name, ResultState.PASS, "fork", {})


@dataclass(frozen=True)
class SlowCasesFuzzer:
    name: str = "slow-cases"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        time.sleep(1.0)
        return (AdapterCase("late", {}),)

    def run(self, _case: AdapterCase, _context: AdapterContext) -> CheckResult:
        return CheckResult(self.name, ResultState.PASS, "late", {})


@dataclass(frozen=True)
class AdvisoryFuzzer:
    name: str = "advisory"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        return (AdapterCase("review", {"value": 1}),)

    def run(self, _case: AdapterCase, _context: AdapterContext) -> CheckResult:
        return CheckResult(self.name, ResultState.ADVISORY, "review required", {})


@dataclass(frozen=True)
class GeneratedFuzzer:
    name: str = "generated"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        return ()

    def hypothesis_strategy(self, _context: AdapterContext):
        return _FixtureStrategy(({"value": 3}, {"value": 5}))

    def run(self, case: AdapterCase, _context: AdapterContext) -> CheckResult:
        return CheckResult(self.name, ResultState.PASS, str(case.payload["value"]), {})


@dataclass(frozen=True)
class _FixtureStrategy:
    values: tuple[object, ...]


@dataclass(frozen=True)
class StaticFuzzer:
    name: str
    rows: tuple[tuple[str, object], ...]
    state: ResultState = ResultState.PASS
    summary: str = "fixture result"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        return tuple(AdapterCase(case_id, payload) for case_id, payload in self.rows)

    def run(self, _case: AdapterCase, _context: AdapterContext) -> CheckResult:
        return CheckResult(self.name, self.state, self.summary, {})


@dataclass(frozen=True)
class GeneratedMarkerFuzzer:
    values: tuple[int, ...]
    name: str = "generated-marker"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        return (AdapterCase("normal", {"value": 0}),)

    def hypothesis_strategy(self, _context: AdapterContext):
        return _FixtureStrategy(tuple({"value": value} for value in self.values))

    def run(self, case: AdapterCase, context: AdapterContext) -> CheckResult:
        value = case.payload["value"]
        (context.corpus_dir / f"run-{value}.json").write_text(
            json.dumps({"case_id": case.case_id, "payload": case.payload}, sort_keys=True),
            encoding="utf-8",
        )
        return CheckResult(self.name, ResultState.PASS, "generated case passed", {})


@dataclass(frozen=True)
class OversizedFuzzer:
    mode: str
    marker: str
    name: str = "oversized"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        payload = {"value": self.marker * 300} if self.mode == "cases" else {}
        return (AdapterCase("large", payload),)

    def run(self, _case: AdapterCase, _context: AdapterContext) -> CheckResult:
        if self.mode == "result":
            summary = self.marker * 10
        elif self.mode == "invalid-summary":
            summary = self.marker * 300
        else:
            summary = "passed"
        return CheckResult(self.name, ResultState.PASS, summary, {})


@dataclass(frozen=True)
class FindingThenOutputErrorFuzzer:
    mode: str
    marker: str
    name: str = "finding-then-output-error"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        return (
            AdapterCase("01-finding", {}),
            AdapterCase("02-output-error", {}),
        )

    def run(self, case: AdapterCase, _context: AdapterContext) -> CheckResult:
        if case.case_id == "01-finding":
            return CheckResult(
                self.name,
                ResultState.FAIL,
                "controlled finding",
                {},
            )
        summary = (
            self.marker * 10
            if self.mode == "transport"
            else f"invalid\n{self.marker}"
        )
        return CheckResult(self.name, ResultState.PASS, summary, {})


@dataclass(frozen=True)
class ContainmentProbeFuzzer:
    marker: Path
    name: str = "containment-probe"
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext):
        self.marker.write_text("cases launched", encoding="utf-8")
        return (AdapterCase("probe", {}),)

    def run(self, _case: AdapterCase, _context: AdapterContext) -> CheckResult:
        self.marker.write_text("run launched", encoding="utf-8")
        return CheckResult(self.name, ResultState.PASS, "probe passed", {})


def _spawn_descendant(context: AdapterContext, label: str) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (context.corpus_dir / f"{label}.pid").write_text(str(child.pid), encoding="utf-8")


@dataclass(frozen=True)
class DescendantLifecycleFuzzer:
    mode: str
    name: str = "descendant-lifecycle"
    kind: str = "fuzz"

    def cases(self, context: AdapterContext):
        if self.mode == "cases":
            _spawn_descendant(context, self.mode)
        return (AdapterCase("lifecycle", {}),)

    def run(self, _case: AdapterCase, context: AdapterContext) -> CheckResult:
        if self.mode != "cases":
            _spawn_descendant(context, self.mode)
        if self.mode == "exception":
            raise RuntimeError("controlled failure")
        return CheckResult(self.name, ResultState.PASS, "controlled return", {})


def _context(
    tmp_path: Path,
    timeout: float = 1.0,
    seed: int = 42,
    *,
    max_cases: int = 10,
    max_output_bytes: int = 4096,
    repo_root: Path = PROJECT_ROOT,
) -> AdapterContext:
    return AdapterContext(
        repo_root,
        tmp_path / "artifacts",
        tmp_path,
        seed,
        AdapterBudget(timeout, max_cases, max_output_bytes),
    )


def _install_fake_hypothesis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str | None = "9.9.9",
    distribution_version: str | None = None,
) -> Path:
    package_root = tmp_path / "fake-packages"
    package = package_root / "hypothesis"
    package.mkdir(parents=True)
    version_line = f"__version__ = {version!r}\n" if version is not None else ""
    (package / "__init__.py").write_text(
        version_line
        + "import json\n"
        + "import os\n"
        + "_settings = {}\n"
        + "_seed_value = 0\n"
        + "def _write(name, value):\n"
        + "    root = os.environ.get('FAKE_HYPOTHESIS_TRACE')\n"
        + "    if root:\n"
        + "        with open(os.path.join(root, name), 'w', encoding='utf-8') as target:\n"
        + "            json.dump(value, target, sort_keys=True)\n"
        + "def seed(value):\n"
        + "    global _seed_value\n"
        + "    _seed_value = value\n"
        + "    _write('hypothesis-seed.json', value)\n"
        + "    return lambda fn: fn\n"
        + "def settings(**kwargs):\n"
        + "    _settings.update(kwargs)\n"
        + "    _write('hypothesis-settings.json', kwargs)\n"
        + "    return lambda fn: fn\n"
        + "def given(strategy):\n"
        + "    def decorate(fn):\n"
        + "        def run():\n"
        + "            offset = _seed_value % len(strategy.values) if strategy.values else 0\n"
        + "            ordered = strategy.values[offset:] + strategy.values[:offset]\n"
        + "            for value in ordered[:_settings['max_examples']]:\n"
        + "                fn(value)\n"
        + "        return run\n"
        + "    return decorate\n",
        encoding="utf-8",
    )
    if distribution_version is not None:
        metadata_dir = package_root / f"hypothesis-{distribution_version}.dist-info"
        metadata_dir.mkdir()
        (metadata_dir / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: hypothesis\nVersion: {distribution_version}\n",
            encoding="utf-8",
        )
    monkeypatch.syspath_prepend(str(package_root))
    monkeypatch.setenv("FAKE_HYPOTHESIS_TRACE", str(tmp_path))
    sys.modules.pop("hypothesis", None)
    return package_root


def _cli_args(
    tmp_path: Path,
    adapter_name: str,
    *,
    out: Path,
    check: Path | None = None,
    gate: bool = False,
    generator: str = "none",
    json_output: bool = False,
    root: Path = PROJECT_ROOT,
) -> argparse.Namespace:
    return argparse.Namespace(
        root=root,
        adapters=[adapter_name],
        corpus=None,
        seed=23,
        timeout=2.0,
        max_cases=4,
        max_output_bytes=8192,
        generator=generator,
        reproducer_dir=tmp_path / "repro",
        out=out,
        check=check,
        gate=gate,
        json=json_output,
    )


def _valid_fuzz_artifact() -> dict[str, object]:
    return {
        "schema_name": "bd.fuzz-results",
        "schema_version": 1,
        "source_sha": "a" * 64,
        "tool_version": "1",
        "input_hashes": {"adapter.fixture.cases": "b" * 64},
        "generated_at": "2026-07-25T12:00:00Z",
        "seed": 1,
        "results": [
            {
                "name": "fixture",
                "state": "pass",
                "summary": "fixture passed",
                "evidence": {"case_corpus_sha256": "b" * 64},
            }
        ],
        "findings": [
            {
                "adapter": "fixture",
                "case_id": "case",
                "state": "fail",
                "fingerprint": (
                    "56f425a4b1f3ee713af6e8c1d4457b77181d06d63c5653c2"
                    "edd25d15b9b2a947"
                ),
                "summary": "controlled finding",
                "reproducer": (
                    "regression_corpus/reproducers/"
                    "fixture--case--56f425a4b1f3.json"
                ),
            }
        ],
    }


def _wait_until_dead(process_id: int) -> bool:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except OSError as error:
            if error.errno == errno.ESRCH:
                return True
            raise
        time.sleep(0.05)
    return False


def test_corpus_order_is_seeded_and_repeatable(tmp_path: Path) -> None:
    corpus = {"schema": 1, "cases": [{"id": "b", "payload": 2}, {"id": "a", "payload": 1}]}
    (tmp_path / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")

    first = load_corpus(tmp_path / "corpus.json", max_cases=10)
    second = load_corpus(tmp_path / "corpus.json", max_cases=10)

    assert first == second
    assert [case.case_id for case in first] == ["a", "b"]


def test_corpus_rejects_secret_content_without_echoing_it(tmp_path: Path) -> None:
    secret = "do-not-disclose-fuzz-secret"
    (tmp_path / "corpus.json").write_text(
        json.dumps({"schema": 1, "cases": [{"id": "case", "payload": {"token": secret}}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        load_corpus(tmp_path / "corpus.json", max_cases=10)

    assert secret not in str(error.value)


def test_crash_writes_minimal_secret_safe_reproducer(tmp_path: Path) -> None:
    (tmp_path / "corpus.json").write_text(
        json.dumps({"schema": 1, "cases": [{"id": "crash", "payload": {"value": 1}}]}),
        encoding="utf-8",
    )

    result, findings = run_fuzz_adapter(
        FixtureFuzzer(), _context(tmp_path), reproducer_dir=tmp_path / "repro"
    )

    assert result.state is ResultState.FAIL
    assert findings[0].state == "error"
    repro = tmp_path / "repro" / findings[0].reproducer
    assert json.loads(repro.read_text(encoding="utf-8"))["case_id"] == "crash"
    assert "RuntimeError" not in repro.read_text(encoding="utf-8")


def test_timeout_is_reported_as_a_replay_finding(tmp_path: Path) -> None:
    (tmp_path / "corpus.json").write_text(
        json.dumps({"schema": 1, "cases": [{"id": "sleep", "payload": {}}]}),
        encoding="utf-8",
    )

    result, findings = run_fuzz_adapter(
        FixtureFuzzer(), _context(tmp_path, timeout=0.5), reproducer_dir=tmp_path / "repro"
    )

    assert result.state is ResultState.FAIL
    assert [(finding.case_id, finding.state) for finding in findings] == [("sleep", "timeout")]


def test_timeout_reaps_worker_descendants(tmp_path: Path) -> None:
    result, findings = run_fuzz_adapter(
        DescendantFuzzer(), _context(tmp_path, timeout=2.0), reproducer_dir=tmp_path / "repro"
    )

    child_pid = int((tmp_path / "descendant.pid").read_text(encoding="utf-8"))
    assert result.state is ResultState.FAIL
    assert findings[0].state == "timeout"
    assert _wait_until_dead(child_pid)


def test_case_enumeration_is_bounded_by_the_same_timeout(tmp_path: Path) -> None:
    started = time.monotonic()
    result, findings = run_fuzz_adapter(
        SlowCasesFuzzer(), _context(tmp_path, timeout=0.1), reproducer_dir=tmp_path / "repro"
    )

    assert result.state is ResultState.TIMEOUT
    assert findings == ()
    assert time.monotonic() - started < 0.8


def test_import_does_not_execute_fuzzing(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: called.append(args))

    importlib.reload(importlib.import_module("tools.fuzz_harness"))

    assert called == []


def test_direct_script_lists_builtin_adapters() -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "tools/fuzz_harness.py", "--list-adapters"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "import-parser",
        "path-guard",
        "plugin",
        "redaction",
        "url-guard",
    ]


def test_advisory_case_is_not_collapsed_to_pass(tmp_path: Path) -> None:
    result, findings = run_fuzz_adapter(
        AdvisoryFuzzer(), _context(tmp_path), reproducer_dir=tmp_path / "repro"
    )

    assert findings == ()
    assert result.state is ResultState.ADVISORY


def test_reproducer_is_a_versioned_provenance_envelope(tmp_path: Path) -> None:
    (tmp_path / "corpus.json").write_text(
        json.dumps({"schema": 1, "cases": [{"id": "crash", "payload": {"value": 1}}]}),
        encoding="utf-8",
    )
    context = AdapterContext(PROJECT_ROOT, tmp_path / "artifacts", tmp_path, 42, AdapterBudget(1.0, 10, 4096))
    _, findings = run_fuzz_adapter(FixtureFuzzer(), context, reproducer_dir=tmp_path / "repro")

    payload = json.loads((tmp_path / "repro" / findings[0].reproducer).read_text(encoding="utf-8"))
    assert payload["schema_name"] == "bd.fuzz-reproducer"
    assert payload["schema_version"] == 1
    assert len(payload["source_sha"]) == 64
    assert set(payload["input_hashes"]) == {"case_payload"}
    assert payload["case_id"] == "crash"


def test_cli_same_path_check_does_not_overwrite_stale_baseline(tmp_path: Path) -> None:
    register_builtin_fuzzers()
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"stale":true}\n', encoding="utf-8")
    args = argparse.Namespace(
        root=Path(__file__).resolve().parents[1], adapters=["url-guard"], corpus=None,
        seed=7, timeout=10.0, max_cases=10, max_output_bytes=4096,
        generator="none", reproducer_dir=tmp_path / "repro", out=baseline,
        check=baseline, gate=False, json=False,
    )

    assert fuzz_harness.run_fuzz_cli(args) == 1
    assert baseline.read_text(encoding="utf-8") == '{"stale":true}\n'


def test_hypothesis_generation_is_seeded_bounded_and_payload_sensitive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = tmp_path / "hypothesis"
    package.mkdir()
    (package / "__init__.py").write_text(
        "__version__ = '9.9.9'\n"
        "def seed(value):\n    return lambda fn: fn\n"
        "def settings(**kwargs):\n    return lambda fn: fn\n"
        "def given(strategy):\n    return lambda fn: (lambda: [fn(value) for value in strategy.values])\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    context = _context(tmp_path, seed=17)

    first, _ = run_hypothesis_adapter(GeneratedFuzzer(), context, reproducer_dir=tmp_path / "repro")
    second, _ = run_hypothesis_adapter(GeneratedFuzzer(), context, reproducer_dir=tmp_path / "repro")

    assert first.state is ResultState.PASS
    assert first.evidence["generator"] == "hypothesis"
    assert first.evidence["findings"] == 0
    assert first.evidence["seed"] == 17
    assert len(first.evidence["case_corpus_sha256"]) == 64
    assert second == first


@pytest.mark.parametrize(
    ("raw", "max_cases", "sentinel"),
    [
        (b"\xff", 1, ""),
        (b"{broken", 1, "broken"),
        (b'{"schema":1,"cases":[{"id":"a","payload":NaN}]}', 1, "NaN"),
        (b'{"schema":1,"cases":[{"id":"a","payload":1,"id":"b"}]}', 1, ""),
        (b'{"schema":1,"cases":[{"id":"same","payload":1},{"id":"same","payload":2}]}', 2, "same"),
        (b'{"schema":1,"cases":[{"id":"","payload":1}]}', 1, ""),
        (b'{"schema":1,"cases":[{"id":"a","payload":{"token":"DO-NOT-LEAK"}}]}', 1, "DO-NOT-LEAK"),
        (b'{"schema":1,"cases":[{"id":"a","payload":1},{"id":"LEAK-LIMIT","payload":2}]}', 1, "LEAK-LIMIT"),
    ],
)
def test_corpus_rejects_malformed_duplicate_invalid_and_over_limit_content(
    tmp_path: Path, raw: bytes, max_cases: int, sentinel: str
) -> None:
    path = tmp_path / "corpus.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="fuzz corpus invalid") as error:
        load_corpus(path, max_cases=max_cases)
    assert not sentinel or sentinel not in str(error.value)


@pytest.mark.parametrize("mode", ["cases", "result"])
def test_oversized_worker_ipc_fails_closed_without_content(
    tmp_path: Path, mode: str
) -> None:
    marker = "PRIVATE-IPC-CONTENT"
    if mode == "result":
        summary = marker * 10
        AdapterCase("transport-summary", {"summary": summary})
        serialized = json.dumps(
            {"kind": "result", "state": "pass", "summary": summary},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert summary.isprintable()
        assert len(summary) < 4096
        assert len(serialized) > 128
    result, findings = run_fuzz_adapter(
        OversizedFuzzer(mode, marker),
        _context(tmp_path, max_output_bytes=128),
        reproducer_dir=tmp_path / "repro",
    )

    assert result.state is ResultState.ERROR
    assert findings == ()
    assert marker not in repr((result, findings))


def test_semantically_oversized_summary_is_not_transport_overflow(
    tmp_path: Path,
) -> None:
    result, findings = run_fuzz_adapter(
        OversizedFuzzer("invalid-summary", "PRIVATE-INVALID-SUMMARY"),
        _context(tmp_path, max_output_bytes=8192),
        reproducer_dir=tmp_path / "repro",
    )

    assert result.state is ResultState.ERROR
    assert result.summary == "fuzz worker output invalid"
    assert findings == ()
    assert "PRIVATE-INVALID-SUMMARY" not in repr((result, findings))


@pytest.mark.parametrize(
    ("mode", "expected_summary"),
    [
        ("transport", "fuzz worker output exceeded budget"),
        ("invalid", "fuzz worker output invalid"),
    ],
)
def test_later_worker_output_error_leaves_no_orphaned_reproducer(
    tmp_path: Path,
    mode: str,
    expected_summary: str,
) -> None:
    marker = "PRIVATE-LATE-OUTPUT"
    if mode == "transport":
        summary = marker * 10
        serialized = json.dumps(
            {"kind": "result", "state": "pass", "summary": summary},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert summary.isprintable()
        assert len(summary) < 4096
        assert len(serialized) > 128
    reproducer_dir = tmp_path / "repro"

    result, findings = run_fuzz_adapter(
        FindingThenOutputErrorFuzzer(mode, marker),
        _context(tmp_path, max_output_bytes=128),
        reproducer_dir=reproducer_dir,
    )

    assert result.state is ResultState.ERROR
    assert result.summary == expected_summary
    assert findings == ()
    assert not reproducer_dir.exists()
    assert marker not in repr((result, findings))


def test_unsupported_containment_fails_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "launched"
    monkeypatch.setattr(
        "tools.code_intelligence.fuzz_service._supports_descendant_containment",
        lambda: False,
    )
    monkeypatch.setattr(
        "tools.code_intelligence.fuzz_service.multiprocessing.get_context",
        lambda *_args, **_kwargs: pytest.fail("process context requested"),
    )

    result, findings = run_fuzz_adapter(
        ContainmentProbeFuzzer(marker),
        _context(tmp_path),
        reproducer_dir=tmp_path / "repro",
    )

    assert result.state is ResultState.ERROR
    assert findings == ()
    assert not marker.exists()


@pytest.mark.parametrize(
    ("mode", "expected_state"),
    [
        ("return", ResultState.PASS),
        ("exception", ResultState.FAIL),
        ("cases", ResultState.PASS),
    ],
)
def test_completed_workers_reap_real_descendants(
    tmp_path: Path, mode: str, expected_state: ResultState
) -> None:
    result, _ = run_fuzz_adapter(
        DescendantLifecycleFuzzer(mode),
        _context(tmp_path),
        reproducer_dir=tmp_path / "repro",
    )

    child_pid = int((tmp_path / f"{mode}.pid").read_text(encoding="utf-8"))
    assert result.state is expected_state
    assert _wait_until_dead(child_pid)


def test_reproducer_filename_is_exact_and_replace_failure_is_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = StaticFuzzer(
        "atomic-fixture",
        (("bad-case", {"value": 1}),),
        ResultState.ERROR,
        "controlled error",
    )
    digest = hashlib.sha256(b"atomic-fixture\0bad-case\0error").hexdigest()
    filename = f"atomic-fixture--bad-case--{digest[:12]}.json"
    target = tmp_path / "repro" / filename
    target.parent.mkdir()
    target.write_text("old-target\n", encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("controlled replace failure")

    monkeypatch.setattr("tools.code_intelligence.artifacts.os.replace", fail_replace)
    with pytest.raises(OSError, match="controlled replace failure"):
        run_fuzz_adapter(adapter, _context(tmp_path), reproducer_dir=target.parent)

    assert target.read_text(encoding="utf-8") == "old-target\n"
    assert list(target.parent.glob(f".{filename}.*.tmp")) == []


@pytest.mark.parametrize(
    ("name", "expected_tail"),
    [
        ("redaction", ["--json", "--work"]),
        ("url-guard", ["--json"]),
    ],
)
def test_real_wrapper_argv_cwd_and_no_shell_expansion(
    tmp_path: Path, name: str, expected_tail: list[str]
) -> None:
    root = tmp_path / "repo;touch-SHELL_EXPANDED"
    root.mkdir()
    script = root / "wrapper.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path('invocation.json').write_text("
        "json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    adapter = CommandFuzzAdapter(name, "wrapper.py")

    result = adapter.run(AdapterCase("builtin-corpus", {}), _context(tmp_path, repo_root=root))

    invocation = json.loads((root / "invocation.json").read_text(encoding="utf-8"))
    expected = expected_tail + ([str(root)] if "--work" in expected_tail else [])
    assert result.state is ResultState.PASS
    assert invocation == {"argv": expected, "cwd": str(root)}
    assert not (tmp_path / "SHELL_EXPANDED").exists()


@pytest.mark.parametrize(
    ("body", "timeout", "max_output_bytes", "expected"),
    [
        ("raise SystemExit(0)\n", 1.0, 1024, ResultState.PASS),
        ("raise SystemExit(1)\n", 1.0, 1024, ResultState.FAIL),
        ("raise SystemExit(7)\n", 1.0, 1024, ResultState.ERROR),
        (
            "import sys\nsys.stdout.write('CONTROLLED-OUTPUT' * 10000)\nsys.stdout.flush()\n",
            1.0,
            128,
            ResultState.ERROR,
        ),
        ("import time\ntime.sleep(5)\n", 0.2, 1024, ResultState.TIMEOUT),
    ],
)
def test_real_wrapper_maps_exit_overflow_and_timeout(
    tmp_path: Path,
    body: str,
    timeout: float,
    max_output_bytes: int,
    expected: ResultState,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "wrapper.py").write_text(body, encoding="utf-8")
    adapter = CommandFuzzAdapter("url-guard", "wrapper.py")

    result = adapter.run(
        AdapterCase("builtin-corpus", {}),
        _context(
            tmp_path,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            repo_root=root,
        ),
    )

    assert result.state is expected
    assert "CONTROLLED-OUTPUT" not in result.summary


def test_hypothesis_worker_uses_seed_settings_bound_and_real_run_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_hypothesis(tmp_path, monkeypatch)
    adapter = GeneratedMarkerFuzzer((3, 5, 7))
    context = _context(tmp_path, seed=17, max_cases=2, max_output_bytes=8192)

    result, findings = run_hypothesis_adapter(
        adapter, context, reproducer_dir=tmp_path / "repro"
    )

    assert result.state is ResultState.PASS
    assert findings == ()
    assert json.loads((tmp_path / "hypothesis-seed.json").read_text(encoding="utf-8")) == 17
    assert json.loads((tmp_path / "hypothesis-settings.json").read_text(encoding="utf-8")) == {
        "database": None,
        "deadline": None,
        "max_examples": 2,
    }
    markers = sorted([
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(tmp_path.glob("run-*.json"))
    ], key=lambda marker: marker["case_id"])
    assert [marker["payload"] for marker in markers] == [{"value": 7}, {"value": 3}]
    payload_hashes = [
        hashlib.sha256(b'{"value":7}\n').hexdigest()[:16],
        hashlib.sha256(b'{"value":3}\n').hexdigest()[:16],
    ]
    expected_ids = [
        f"hypothesis-0000-{payload_hashes[0]}",
        f"hypothesis-0001-{payload_hashes[1]}",
    ]
    assert [marker["case_id"] for marker in markers] == expected_ids

    other_root = tmp_path / "seed-18"
    other_root.mkdir()
    monkeypatch.setenv("FAKE_HYPOTHESIS_TRACE", str(other_root))
    other, _ = run_hypothesis_adapter(
        adapter,
        _context(other_root, seed=18, max_cases=2, max_output_bytes=8192),
        reproducer_dir=other_root / "repro",
    )
    assert json.loads(
        (other_root / "hypothesis-seed.json").read_text(encoding="utf-8")
    ) == 18
    assert result.evidence["case_corpus_sha256"] != other.evidence["case_corpus_sha256"]


def test_effective_case_and_artifact_provenance_is_repeatable_and_sensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_hypothesis(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    context = _context(tmp_path, seed=31, max_cases=3, max_output_bytes=8192)
    first_adapter = GeneratedMarkerFuzzer((1, 2))
    changed_adapter = GeneratedMarkerFuzzer((1, 9))
    normal, _ = run_fuzz_adapter(first_adapter, context, reproducer_dir=tmp_path / "repro")
    first, _ = run_hypothesis_adapter(first_adapter, context, reproducer_dir=tmp_path / "repro")
    repeat, _ = run_hypothesis_adapter(first_adapter, context, reproducer_dir=tmp_path / "repro")
    changed, _ = run_hypothesis_adapter(changed_adapter, context, reproducer_dir=tmp_path / "repro")

    first_artifact = fuzz_harness._artifact(
        PROJECT_ROOT, 31, None, (first_adapter.name,), [normal, first], []
    )
    repeat_artifact = fuzz_harness._artifact(
        PROJECT_ROOT, 31, None, (first_adapter.name,), [normal, repeat], []
    )
    changed_artifact = fuzz_harness._artifact(
        PROJECT_ROOT, 31, None, (first_adapter.name,), [normal, changed], []
    )

    assert normal.evidence["case_corpus_sha256"] == effective_case_hash(
        (AdapterCase("normal", {"value": 0}),)
    )
    assert first.evidence["case_corpus_sha256"] == repeat.evidence["case_corpus_sha256"]
    assert first.evidence["case_corpus_sha256"] != changed.evidence["case_corpus_sha256"]
    assert first_artifact["input_hashes"] == repeat_artifact["input_hashes"]
    assert (
        first_artifact["input_hashes"]["generator.generated-marker.cases"]
        != changed_artifact["input_hashes"]["generator.generated-marker.cases"]
    )
    descriptor = b'{"engine":"hypothesis","version":"9.9.9"}\n'
    assert first_artifact["input_hashes"]["generator.hypothesis.runtime"] == hashlib.sha256(
        descriptor
    ).hexdigest()


def test_hypothesis_runtime_is_attested_by_the_exact_generating_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_hypothesis(
        tmp_path,
        monkeypatch,
        version="9.9.9",
        distribution_version="1.2.3",
    )
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    adapter = GeneratedMarkerFuzzer((1,))
    normal, _ = run_fuzz_adapter(
        adapter, _context(tmp_path), reproducer_dir=tmp_path / "repro"
    )
    generated, findings = run_hypothesis_adapter(
        adapter, _context(tmp_path), reproducer_dir=tmp_path / "repro"
    )
    descriptor = {"engine": "hypothesis", "version": "9.9.9"}
    descriptor_hash = hashlib.sha256(
        b'{"engine":"hypothesis","version":"9.9.9"}\n'
    ).hexdigest()

    artifact = fuzz_harness._artifact(
        PROJECT_ROOT,
        42,
        None,
        (adapter.name,),
        [normal, generated],
        list(findings),
    )

    assert generated.evidence["hypothesis_runtime"] == descriptor
    assert generated.evidence["hypothesis_runtime_sha256"] == descriptor_hash
    assert artifact["input_hashes"]["generator.hypothesis.runtime"] == descriptor_hash


def test_builtin_artifact_records_wrapper_and_effective_case_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    case_hash = "b" * 64
    result = CheckResult(
        "url-guard",
        ResultState.PASS,
        "passed",
        {"case_corpus_sha256": case_hash},
    )

    artifact = fuzz_harness._artifact(
        PROJECT_ROOT,
        1,
        None,
        ("url-guard",),
        [result],
        [],
    )

    wrapper_hash = hashlib.sha256(
        (PROJECT_ROOT / fuzz_harness.BUILTIN_FUZZ_COMMANDS["url-guard"]).read_bytes()
    ).hexdigest()
    assert artifact["input_hashes"]["wrapper.url-guard"] == wrapper_hash
    assert artifact["input_hashes"]["adapter.url-guard.cases"] == case_hash


@pytest.mark.parametrize(
    "evidence",
    [{}, {"case_corpus_sha256": "B" * 64}],
)
def test_builtin_artifact_requires_lowercase_effective_case_hash(
    evidence: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    result = CheckResult("url-guard", ResultState.PASS, "passed", evidence)

    with pytest.raises(ValueError, match="fuzz results invalid"):
        fuzz_harness._artifact(
            PROJECT_ROOT,
            1,
            None,
            ("url-guard",),
            [result],
            [],
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "result-extra",
        "result-name",
        "result-state",
        "result-summary-empty",
        "result-summary-control",
        "result-summary-long",
        "evidence-not-object",
        "evidence-nan",
        "evidence-secret",
        "nested-generated-at",
        "finding-extra",
        "finding-adapter",
        "finding-case",
        "finding-state",
        "finding-fingerprint",
        "finding-summary",
        "finding-reproducer",
        "input-hash-uppercase",
        "input-hashes-empty",
    ],
)
def test_artifact_validator_rejects_deep_invalid_records(mutation: str) -> None:
    artifact = _valid_fuzz_artifact()
    fuzz_harness._validate_artifact(artifact)
    result = artifact["results"][0]
    finding = artifact["findings"][0]

    if mutation == "result-extra":
        result["unexpected"] = True
    elif mutation == "result-name":
        result["name"] = ""
    elif mutation == "result-state":
        result["state"] = "skipped"
    elif mutation == "result-summary-empty":
        result["summary"] = ""
    elif mutation == "result-summary-control":
        result["summary"] = "line one\nline two"
    elif mutation == "result-summary-long":
        result["summary"] = "x" * 4097
    elif mutation == "evidence-not-object":
        result["evidence"] = []
    elif mutation == "evidence-nan":
        result["evidence"] = {"metric": float("nan")}
    elif mutation == "evidence-secret":
        result["evidence"] = {"api_token": "PRIVATE-EVIDENCE"}
    elif mutation == "nested-generated-at":
        result["evidence"] = {
            "nested": {"generated_at": "2000-01-01T00:00:00Z"}
        }
    elif mutation == "finding-extra":
        finding["unexpected"] = True
    elif mutation == "finding-adapter":
        finding["adapter"] = "../unsafe"
    elif mutation == "finding-case":
        finding["case_id"] = ""
    elif mutation == "finding-state":
        finding["state"] = "unknown"
    elif mutation == "finding-fingerprint":
        finding["fingerprint"] = "C" * 64
    elif mutation == "finding-summary":
        finding["summary"] = ""
    elif mutation == "finding-reproducer":
        finding["reproducer"] = "../fixture--case--56f425a4b1f3.json"
    elif mutation == "input-hash-uppercase":
        artifact["input_hashes"]["adapter.fixture.cases"] = "B" * 64
    elif mutation == "input-hashes-empty":
        artifact["input_hashes"] = {}

    with pytest.raises(ValueError, match="fuzz results invalid") as error:
        fuzz_harness._validate_artifact(artifact)
    if mutation == "evidence-secret":
        assert "PRIVATE-EVIDENCE" not in str(error.value)


def test_fuzz_comparison_ignores_only_top_level_generated_at() -> None:
    baseline = _valid_fuzz_artifact()
    current = json.loads(json.dumps(baseline))
    current["generated_at"] = "2026-07-25T13:00:00Z"

    assert fuzz_harness._comparison_bytes(baseline) == fuzz_harness._comparison_bytes(
        current
    )

    current["results"][0]["evidence"]["generated_at"] = (
        "2000-01-01T00:00:00Z"
    )
    assert fuzz_harness._comparison_bytes(baseline) != fuzz_harness._comparison_bytes(
        current
    )


def test_artifact_validator_accepts_adapter_derived_hash_key_at_name_limit() -> None:
    artifact = _valid_fuzz_artifact()
    adapter_name = "a" * 256
    artifact["input_hashes"] = {
        f"adapter.{adapter_name}.cases": "b" * 64,
    }
    artifact["results"][0]["name"] = adapter_name
    artifact["findings"] = []

    fuzz_harness._validate_artifact(artifact)


@pytest.mark.parametrize("adapter_name_length", [245, 246, 255, 256])
def test_hypothesis_result_name_is_bounded_at_registry_name_limit(
    tmp_path: Path,
    adapter_name_length: int,
) -> None:
    adapter_name = "a" * adapter_name_length

    result, findings = run_hypothesis_adapter(
        StaticFuzzer(adapter_name, ()),
        _context(tmp_path),
        reproducer_dir=tmp_path / "repro",
    )

    if adapter_name_length == 245:
        expected_name = f"{adapter_name}.hypothesis"
    else:
        digest = hashlib.sha256(adapter_name.encode("utf-8")).hexdigest()
        expected_name = f"{adapter_name[:180]}.{digest}.hypothesis"
    assert result.name == expected_name
    assert len(result.name) == 256
    assert findings == ()


def test_hypothesis_result_is_classified_relative_to_selected_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    adapter = StaticFuzzer("base.hypothesis", ())
    normal, _ = run_fuzz_adapter(
        adapter,
        _context(tmp_path),
        reproducer_dir=tmp_path / "repro",
    )
    generated, _ = run_hypothesis_adapter(
        adapter,
        _context(tmp_path),
        reproducer_dir=tmp_path / "repro",
    )

    artifact = fuzz_harness._artifact(
        PROJECT_ROOT,
        42,
        None,
        (adapter.name,),
        [normal, generated],
        [],
    )

    assert normal.name == "base.hypothesis"
    assert generated.name == "base.hypothesis.hypothesis"
    assert set(artifact["input_hashes"]) == {
        "adapter.base.hypothesis.cases",
        "generator.base.hypothesis.cases",
    }


def test_long_hypothesis_result_identity_is_collision_safe_and_associated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hypothesis(tmp_path, monkeypatch)
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    first_name = ("b" * 255) + "x"
    second_name = ("b" * 255) + "y"
    first_adapter = GeneratedMarkerFuzzer((1,), first_name)
    second_adapter = GeneratedMarkerFuzzer((2,), second_name)
    context = _context(tmp_path, max_output_bytes=8192)
    first_normal, _ = run_fuzz_adapter(
        first_adapter,
        context,
        reproducer_dir=tmp_path / "repro",
    )
    second_normal, _ = run_fuzz_adapter(
        second_adapter,
        context,
        reproducer_dir=tmp_path / "repro",
    )
    first_generated, _ = run_hypothesis_adapter(
        first_adapter,
        context,
        reproducer_dir=tmp_path / "repro",
    )
    first_repeat, _ = run_hypothesis_adapter(
        first_adapter,
        context,
        reproducer_dir=tmp_path / "repro",
    )
    second_generated, _ = run_hypothesis_adapter(
        second_adapter,
        context,
        reproducer_dir=tmp_path / "repro",
    )

    artifact = fuzz_harness._artifact(
        PROJECT_ROOT,
        context.seed,
        None,
        (first_name, second_name),
        [
            first_normal,
            second_normal,
            first_generated,
            second_generated,
        ],
        [],
    )

    first_digest = hashlib.sha256(first_name.encode("utf-8")).hexdigest()
    second_digest = hashlib.sha256(second_name.encode("utf-8")).hexdigest()
    assert first_generated.name == f"{first_name[:180]}.{first_digest}.hypothesis"
    assert second_generated.name == f"{second_name[:180]}.{second_digest}.hypothesis"
    assert first_repeat.name == first_generated.name
    assert first_generated.name != second_generated.name
    assert artifact["input_hashes"][f"generator.{first_name}.cases"] == (
        first_generated.evidence["case_corpus_sha256"]
    )
    assert artifact["input_hashes"][f"generator.{second_name}.cases"] == (
        second_generated.evidence["case_corpus_sha256"]
    )
    assert (
        artifact["input_hashes"][f"generator.{first_name}.cases"]
        != artifact["input_hashes"][f"generator.{second_name}.cases"]
    )
    fuzz_harness._validate_artifact(artifact)


def test_cli_writes_hypothesis_artifact_for_maximum_registry_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter_name = "c" * 256
    register_adapter(StaticFuzzer(adapter_name, ()))
    monkeypatch.setattr(
        fuzz_harness.importlib.util,
        "find_spec",
        lambda _name: object(),
    )
    output = tmp_path / "long-name.json"

    status = fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            adapter_name,
            out=output,
            generator="hypothesis",
        )
    )

    assert status == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    generated = payload["results"][1]
    digest = hashlib.sha256(adapter_name.encode("utf-8")).hexdigest()
    assert generated["name"] == f"{adapter_name[:180]}.{digest}.hypothesis"
    assert payload["input_hashes"][f"generator.{adapter_name}.cases"] == (
        generated["evidence"]["case_corpus_sha256"]
    )
    fuzz_harness._validate_artifact(payload)


def test_artifact_validator_rejects_arbitrary_overlength_result_name() -> None:
    artifact = _valid_fuzz_artifact()
    artifact["results"][0]["name"] = "a" * 257

    with pytest.raises(ValueError, match="fuzz results invalid"):
        fuzz_harness._validate_artifact(artifact)


def _round_five_normal_result(name: str, case_hash: str) -> CheckResult:
    return CheckResult(
        name,
        ResultState.PASS,
        "normal passed",
        {"case_corpus_sha256": case_hash},
    )


def _round_five_generated_result(name: str, case_hash: str) -> CheckResult:
    runtime = {"engine": "hypothesis", "version": "9.9.9"}
    return CheckResult(
        name,
        ResultState.PASS,
        "generated passed",
        {
            "generator": "hypothesis",
            "case_corpus_sha256": case_hash,
            "hypothesis_runtime": runtime,
            "hypothesis_runtime_sha256": hashlib.sha256(
                b'{"engine":"hypothesis","version":"9.9.9"}\n'
            ).hexdigest(),
        },
    )


@pytest.mark.parametrize(
    "scenario",
    ["unassociated", "marked-normal-collision", "duplicate"],
)
def test_artifact_rejects_hypothesis_results_without_unique_adapter_association(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    if scenario == "unassociated":
        selected = ("custom",)
        results = [
            _round_five_normal_result("custom", "b" * 64),
            _round_five_generated_result("other.hypothesis", "c" * 64),
        ]
    elif scenario == "marked-normal-collision":
        selected = ("base", "base.hypothesis")
        results = [
            _round_five_normal_result("base", "b" * 64),
            _round_five_generated_result("base.hypothesis", "c" * 64),
        ]
    else:
        selected = ("custom",)
        generated = _round_five_generated_result(
            "custom.hypothesis",
            "c" * 64,
        )
        results = [
            _round_five_normal_result("custom", "b" * 64),
            generated,
            generated,
        ]

    with pytest.raises(ValueError, match="fuzz results invalid"):
        fuzz_harness._artifact(
            PROJECT_ROOT,
            42,
            None,
            selected,
            results,
            [],
        )


@pytest.mark.parametrize(
    "scenario",
    ["selected-suffix", "bounded-long", "ordinary-normal"],
)
def test_artifact_preserves_unambiguous_hypothesis_and_normal_identities(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    if scenario == "selected-suffix":
        adapter_name = "base.hypothesis"
        generated_name = "base.hypothesis.hypothesis"
        selected = (adapter_name,)
        results = [
            _round_five_normal_result(adapter_name, "b" * 64),
            _round_five_generated_result(generated_name, "c" * 64),
        ]
    elif scenario == "bounded-long":
        adapter_name = "l" * 256
        digest = hashlib.sha256(adapter_name.encode("utf-8")).hexdigest()
        generated_name = f"{adapter_name[:180]}.{digest}.hypothesis"
        selected = (adapter_name,)
        results = [
            _round_five_normal_result(adapter_name, "b" * 64),
            _round_five_generated_result(generated_name, "c" * 64),
        ]
    else:
        selected = ("base", "base.hypothesis")
        generated_name = None
        results = [
            _round_five_normal_result("base", "b" * 64),
            _round_five_normal_result("base.hypothesis", "d" * 64),
        ]

    artifact = fuzz_harness._artifact(
        PROJECT_ROOT,
        42,
        None,
        selected,
        results,
        [],
    )

    assert [result["name"] for result in artifact["results"]] == [
        result.name for result in results
    ]
    if generated_name is None:
        assert not any(
            name.startswith("generator.")
            for name in artifact["input_hashes"]
        )
    else:
        assert len(generated_name) <= 256
        assert artifact["input_hashes"][f"generator.{adapter_name}.cases"] == (
            "c" * 64
        )
        assert "generator.hypothesis.runtime" in artifact["input_hashes"]


def test_artifact_fails_closed_on_missing_or_malformed_required_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    valid_hash = "a" * 64
    normal = CheckResult("custom", ResultState.PASS, "passed", {})
    malformed = CheckResult(
        "custom", ResultState.PASS, "passed", {"case_corpus_sha256": "A" * 64}
    )
    generated_missing = CheckResult(
        "custom.hypothesis",
        ResultState.PASS,
        "generated",
        {"generator": "hypothesis"},
    )
    generated_valid = CheckResult(
        "custom.hypothesis",
        ResultState.PASS,
        "generated",
        {
            "generator": "hypothesis",
            "case_corpus_sha256": valid_hash,
            "hypothesis_runtime": {
                "engine": "hypothesis",
                "version": "9.9.9",
            },
            "hypothesis_runtime_sha256": hashlib.sha256(
                b'{"engine":"hypothesis","version":"9.9.9"}\n'
            ).hexdigest(),
        },
    )
    generated_bad_runtime = CheckResult(
        "custom.hypothesis",
        ResultState.PASS,
        "generated",
        {
            **generated_valid.evidence,
            "hypothesis_runtime_sha256": "not-a-hash",
        },
    )
    normal_valid = CheckResult(
        "custom", ResultState.PASS, "passed", {"case_corpus_sha256": valid_hash}
    )

    for results in ([normal], [malformed], [normal_valid, generated_missing]):
        with pytest.raises(ValueError, match="fuzz results invalid"):
            fuzz_harness._artifact(PROJECT_ROOT, 1, None, ("custom",), list(results), [])
    monkeypatch.setattr(
        fuzz_harness,
        "_hypothesis_runtime_hash",
        lambda: valid_hash,
        raising=False,
    )
    with pytest.raises(ValueError, match="fuzz results invalid"):
        fuzz_harness._artifact(
            PROJECT_ROOT,
            1,
            None,
            ("custom",),
            [normal_valid, generated_bad_runtime],
            [],
        )


def test_hypothesis_runtime_without_installed_version_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_hypothesis(tmp_path, monkeypatch, version=None)

    result, findings = run_hypothesis_adapter(
        GeneratedFuzzer(), _context(tmp_path), reproducer_dir=tmp_path / "repro"
    )

    assert result.state is ResultState.ERROR
    assert findings == ()


@pytest.mark.parametrize(
    "version",
    [
        "9.9.9\nPRIVATE-UNPRINTABLE-RUNTIME",
        "Bearer PRIVATE-RUNTIME-VERSION",
    ],
)
def test_hypothesis_runtime_unsafe_version_fails_closed_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
) -> None:
    _install_fake_hypothesis(tmp_path, monkeypatch, version=version)

    result, findings = run_hypothesis_adapter(
        GeneratedFuzzer(),
        _context(tmp_path),
        reproducer_dir=tmp_path / "repro",
    )

    assert result.state is ResultState.ERROR
    assert findings == ()
    assert "PRIVATE-" not in repr((result, findings))


def test_hypothesis_runtime_attestation_rejects_mismatched_hash() -> None:
    descriptor = {"engine": "hypothesis", "version": "9.9.9"}

    with pytest.raises(ValueError, match="hypothesis runtime invalid"):
        fuzz_harness._validate_hypothesis_runtime(descriptor, "f" * 64)


@pytest.mark.parametrize(
    "adapter_name",
    ["hypothesis-unavailable-fixture", "hypothesis"],
)
def test_hypothesis_unavailable_gate_and_installed_noncapable_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    unavailable_adapter = StaticFuzzer(adapter_name, ())
    register_adapter(unavailable_adapter)
    monkeypatch.setattr(fuzz_harness.importlib.util, "find_spec", lambda _name: None)

    non_gate = fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            unavailable_adapter.name,
            out=tmp_path / "unavailable-non-gate.json",
            generator="hypothesis",
        )
    )
    gate = fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            unavailable_adapter.name,
            out=tmp_path / "unavailable-gate.json",
            gate=True,
            generator="hypothesis",
        )
    )
    assert non_gate == 0
    assert gate == 1
    unavailable_payload = json.loads(
        (tmp_path / "unavailable-non-gate.json").read_text(encoding="utf-8")
    )
    unavailable_result = unavailable_payload["results"][-1]
    assert unavailable_result["state"] == "unknown"
    assert unavailable_result["evidence"] == {
        "availability": "unavailable",
        "requested_generator": "hypothesis",
    }
    assert unavailable_result["name"] == (
        "hypothesis"
        if adapter_name != "hypothesis"
        else "hypothesis.unavailable.0"
    )
    assert unavailable_result["name"] not in {
        result["name"]
        for result in unavailable_payload["results"][:-1]
    }
    assert "generator.hypothesis.runtime" not in unavailable_payload["input_hashes"]

    _install_fake_hypothesis(tmp_path / "installed", monkeypatch)
    noncapable, findings = run_hypothesis_adapter(
        CommandFuzzAdapter("redaction", "unused.py"),
        _context(tmp_path / "installed"),
        reproducer_dir=tmp_path / "repro",
    )
    assert findings == ()
    assert noncapable.state is ResultState.ADVISORY
    assert exit_code([noncapable], gate=False) == 0
    assert exit_code([noncapable], gate=True) == 0


def test_cli_nonalias_check_gate_json_and_input_hashes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fuzz_harness,
        "build_snapshot",
        lambda _root: SimpleNamespace(source_sha="a" * 64),
    )
    adapter = StaticFuzzer("cli-fuzz-fixture", (("normal", {"value": 4}),))
    register_adapter(adapter)
    baseline = tmp_path / "baseline.json"
    assert fuzz_harness.run_fuzz_cli(
        _cli_args(tmp_path, adapter.name, out=baseline)
    ) == 0
    capsys.readouterr()
    baseline_payload = json.loads(baseline.read_text(encoding="utf-8"))
    baseline_payload["generated_at"] = "2000-01-01T00:00:00Z"
    baseline.write_text(
        json.dumps(baseline_payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    equal_output = tmp_path / "equal.json"
    assert fuzz_harness.run_fuzz_cli(
        _cli_args(tmp_path, adapter.name, out=equal_output, check=baseline)
    ) == 0
    capsys.readouterr()
    assert json.loads(equal_output.read_text(encoding="utf-8"))["input_hashes"][
        "adapter.cli-fuzz-fixture.cases"
    ] == effective_case_hash((AdapterCase("normal", {"value": 4}),))

    mismatch = json.loads(baseline.read_text(encoding="utf-8"))
    mismatch["input_hashes"]["adapter.cli-fuzz-fixture.cases"] = "f" * 64
    mismatch_path = tmp_path / "mismatch.json"
    mismatch_path.write_text(json.dumps(mismatch), encoding="utf-8")
    assert fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            adapter.name,
            out=tmp_path / "mismatch-output.json",
            check=mismatch_path,
        )
    ) == 1
    capsys.readouterr()

    json_output = tmp_path / "json-output.json"
    assert fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            adapter.name,
            out=json_output,
            json_output=True,
        )
    ) == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout) == json.loads(json_output.read_text(encoding="utf-8"))
    assert stdout == json_output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("state", "gate", "expected_exit"),
    [
        (ResultState.PASS, False, 0),
        (ResultState.PASS, True, 0),
        (ResultState.ADVISORY, False, 0),
        (ResultState.ADVISORY, True, 0),
        (ResultState.UNKNOWN, False, 0),
        (ResultState.UNKNOWN, True, 1),
        (ResultState.FAIL, False, 1),
        (ResultState.FAIL, True, 1),
        (ResultState.TIMEOUT, False, 1),
        (ResultState.TIMEOUT, True, 1),
        (ResultState.ERROR, False, 1),
        (ResultState.ERROR, True, 1),
    ],
)
def test_run_fuzz_cli_full_state_gate_matrix_writes_canonical_checked_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    state: ResultState,
    gate: bool,
    expected_exit: int,
) -> None:
    adapter = StaticFuzzer(
        f"cli-matrix-{state.value}-{'gate' if gate else 'nongate'}",
        (),
    )
    register_adapter(adapter)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "tracked.txt").write_text("matrix fixture\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "tracked.txt"],
        cwd=repository,
        check=True,
        capture_output=True,
    )

    def select_state(
        selected_adapter: object,
        _context: AdapterContext,
        *,
        reproducer_dir: Path,
    ) -> tuple[CheckResult, tuple[object, ...]]:
        del reproducer_dir
        return (
            CheckResult(
                selected_adapter.name,
                state,
                f"{state.value} selected",
                {"case_corpus_sha256": "d" * 64},
            ),
            (),
        )

    monkeypatch.setattr(fuzz_harness, "run_fuzz_adapter", select_state)
    baseline = tmp_path / "baseline.json"

    baseline_exit = fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            adapter.name,
            out=baseline,
            gate=gate,
            root=repository,
        )
    )
    capsys.readouterr()
    output = tmp_path / "current.json"
    current_exit = fuzz_harness.run_fuzz_cli(
        _cli_args(
            tmp_path,
            adapter.name,
            out=output,
            check=baseline,
            gate=gate,
            json_output=True,
            root=repository,
        )
    )
    stdout = capsys.readouterr().out
    raw_output = output.read_bytes()
    payload = json.loads(raw_output)
    independent_canonical = (
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")

    assert baseline_exit == expected_exit
    assert current_exit == expected_exit
    assert len(payload["results"]) == 1
    assert payload["results"][0]["name"] == adapter.name
    assert payload["results"][0]["state"] == state.value
    fuzz_harness._validate_artifact(payload)
    assert raw_output == independent_canonical
    assert stdout.encode("utf-8") == raw_output
