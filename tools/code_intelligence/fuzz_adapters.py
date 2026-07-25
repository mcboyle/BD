"""Focused wrappers around the repository's existing fuzz commands."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from .adapters import AdapterCase, AdapterContext, get_adapter, register_adapter
from .oracle_adapters import _run_bounded
from .results import CheckResult, ResultState


BUILTIN_FUZZ_COMMANDS = {
    "redaction": "toolchain/bin/bd-fuzz-redaction",
    "url-guard": "toolchain/bin/bd-fuzz-urlguard",
    "path-guard": "toolchain/bin/bd-fuzz-pathguard",
    "import-parser": "toolchain/bin/bd-fuzz-import",
    "plugin": "toolchain/bin/bd-plugin-fuzz",
}

_WORK_ARGUMENT_ADAPTERS = frozenset({"redaction", "path-guard", "import-parser", "plugin"})


@dataclass(frozen=True)
class CommandFuzzAdapter:
    name: str
    script: str
    kind: str = "fuzz"

    def cases(self, _context: AdapterContext) -> tuple[AdapterCase, ...]:
        # These wrapper commands own their existing internal corpora.
        return (AdapterCase("builtin-corpus", {}),)

    def run(self, _case: AdapterCase, context: AdapterContext) -> CheckResult:
        script = (context.repo_root / self.script).resolve()
        try:
            script.relative_to(context.repo_root.resolve())
        except ValueError:
            return CheckResult(self.name, ResultState.ERROR, "fuzzer command unavailable", {})
        command = [sys.executable, str(script), "--json"]
        if self.name in _WORK_ARGUMENT_ADAPTERS:
            command.extend(("--work", str(context.repo_root)))
        try:
            output = _run_bounded(command, context)
        except (OSError, ValueError, RuntimeError):
            return CheckResult(self.name, ResultState.ERROR, "fuzzer command unavailable", {})
        if output.timed_out:
            return CheckResult(self.name, ResultState.TIMEOUT, "fuzzer exceeded budget", {})
        if output.overflowed:
            return CheckResult(self.name, ResultState.ERROR, "fuzzer output exceeded budget", {})
        if output.returncode == 0:
            return CheckResult(self.name, ResultState.PASS, "fuzzer passed", {})
        if output.returncode == 1:
            return CheckResult(self.name, ResultState.FAIL, "fuzzer reported finding", {})
        return CheckResult(self.name, ResultState.ERROR, "fuzzer command failed", {})


def register_builtin_fuzzers() -> tuple[str, ...]:
    """Register the stable built-in wrappers exactly once per process."""
    for name, script in BUILTIN_FUZZ_COMMANDS.items():
        try:
            get_adapter(name)
        except KeyError:
            register_adapter(CommandFuzzAdapter(name, script))
    return tuple(sorted(BUILTIN_FUZZ_COMMANDS))
