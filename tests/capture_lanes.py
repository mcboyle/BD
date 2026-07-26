"""Deterministic pytest lane classification used by ``capture.sh``.

The parallel lane is the default only for files without a reviewed risk signal.
Risky files are selected into a separate serial pytest invocation, so they never
overlap the xdist workload or one another.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


SERIAL_EXACT_BASENAMES = frozenset(
    {
        "test_fixture_site.py",
        "test_fixture_site2.py",
        "test_session_keeper.py",
        "test_v3_66_13_phase2_p2_snapshot_replay.py",
        "test_v3_66_729_body_contract_fixtures.py",
        "test_v3_66_797_runner_isolate.py",
    }
)

# These describe the risk category, not a particular historical failure.
SERIAL_NAME_TOKENS = (
    "artifact",
    "browser",
    "capture",
    "chrome",
    "firefox",
    "fixture_site",
    "golden",
    "live",
    "network",
    "playwright",
    "runner",
    "server",
    "service",
    "shared",
    "socket",
    "systemd",
)

SERIAL_SOURCE_SNIPPETS = (
    "pytest.mark.bd_module_wipe",
    "import run_tests",
    "from run_tests",
    "run_tests.py",
    "playwright.",
    "from playwright",
    "import playwright",
    "selenium.",
    "socket.socket",
    "requests.get(",
    "requests.post(",
    "urllib.request",
    "systemctl",
    "regenerate_goldens",
    "pin_index.json",
    "function_index.md",
    "endpoint_catalog",
    "route_index",
)

SERIAL_SOURCE_PATTERNS = (
    re.compile(r"\bsys\.modules\b", re.IGNORECASE),
    re.compile(
        r"\bos\.environ\s*(?:"
        r"\[[^\]]+\]\s*=|"
        r"\.pop\s*\(|"
        r"\.clear\s*\(|"
        r"\.update\s*\(|"
        r"\.setdefault\s*\()",
        re.IGNORECASE,
    ),
    re.compile(r"\bos\.(?:chdir|putenv|unsetenv)\s*\(", re.IGNORECASE),
    re.compile(
        r"\b(?:requests|httpx)\."
        r"(?:request|get|post|put|patch|delete|stream)\s*\(",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:from\s+socket\s+import|import\s+socket\b)",
        re.IGNORECASE | re.MULTILINE,
    ),
)


def classify_capture_file(
    path: str | Path,
    *,
    source: str | None = None,
) -> str:
    """Return ``serial`` for reviewed risk signals, otherwise ``parallel``."""
    candidate = Path(path)
    basename = candidate.name.lower()
    if basename in SERIAL_EXACT_BASENAMES:
        return "serial"
    if any(token in basename for token in SERIAL_NAME_TOKENS):
        return "serial"

    if source is None:
        try:
            source = candidate.read_text(encoding="utf-8")
        except OSError:
            # A path pytest collected but we cannot inspect is not proven safe.
            return "serial"
    lowered = source.lower()
    if any(snippet in lowered for snippet in SERIAL_SOURCE_SNIPPETS):
        return "serial"
    if any(pattern.search(source) for pattern in SERIAL_SOURCE_PATTERNS):
        return "serial"
    return "parallel"


@lru_cache(maxsize=None)
def classify_capture_path(path: str) -> str:
    """Cached filesystem adapter for pytest's per-item collection hook."""
    return classify_capture_file(path)
