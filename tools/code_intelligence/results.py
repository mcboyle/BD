"""Normalized result states and their command-line exit policy."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum


class ResultState(str, Enum):
    """The portable outcomes produced by code-intelligence checks."""

    PASS = "pass"
    FAIL = "fail"
    ADVISORY = "advisory"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class CheckResult:
    """One named check result with machine-readable supporting evidence."""

    name: str
    state: ResultState
    summary: str
    evidence: Mapping[str, object]


_ALWAYS_BLOCKING = frozenset({ResultState.FAIL, ResultState.TIMEOUT, ResultState.ERROR})


def exit_code(results: Iterable[CheckResult], gate: bool) -> int:
    """Return a nonzero code only for blocking states under the selected policy."""
    for result in results:
        if result.state in _ALWAYS_BLOCKING:
            return 1
        if gate and result.state is ResultState.UNKNOWN:
            return 1
    return 0
