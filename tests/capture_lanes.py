"""Fail-closed pytest lane classification used by ``capture.sh``.

Only files in the checked-in, reviewed allowlist can enter the xdist lane.
Unlisted, unreadable, malformed, and risk-matching paths default to serial.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path, PurePosixPath


TESTS_ROOT = Path(__file__).resolve().parent
PARALLEL_ALLOWLIST_PATH = TESTS_ROOT / "capture_parallel_files.txt"


SERIAL_EXACT_BASENAMES = frozenset(
    {
        "test_fixture_site.py",
        "test_fixture_site2.py",
        "test_session_keeper.py",
        "test_v3_66_13_phase2_p2_snapshot_replay.py",
        "test_v3_66_729_body_contract_fixtures.py",
        "test_v3_66_797_runner_isolate.py",
        # v3.66.921 -- PROVEN FRAGILE BY EXPERIMENT, not by heuristic. The
        # whole serial lane (1059 files, 13,429 tests) was run under
        # `-n $(nproc) --dist loadfile` ON THE BOX; exactly five files failed
        # and every one passed on a serial retry. Three were already listed or
        # source-flagged; these two were not, and they are the only files in
        # the promotion set that the experiment refuted.
        #
        # Both also failed the same way in an independent 496-file xdist run in
        # a cloud container, so this is two machines agreeing, not one flake.
        # Do not promote them on a future green run: a race that resolves
        # favourably passes, which is the whole reason this list is by name.
        "test_dev_suite_tier1b.py",
        "test_v3_66_717_exec_bridge.py",
        # v3.66.922 -- REFUTED BY THE FIRST REAL CAPTURE after the @921
        # backfill, and it is the hazard @921 predicted rather than a new
        # one. app_widgets_api._collect_data reads `history` and `library`
        # DIRECTLY; the test patches db_stats and dashboard_widgets.snapshot,
        # which do not cover that path. Serially it passed because an EARLIER
        # FILE had created those tables, so the eta computed from real rows.
        # In the parallel lane it lands on a worker where nothing did, the
        # query fails with "no such table: history", and eta_clear_fmt falls
        # back to "now" instead of None/"30m".
        #
        # IT PASSED THE @921 PROMOTION EXPERIMENT, and that is the lesson: in
        # that run the WHOLE serial lane went parallel together, so whichever
        # file seeded the tables was still present. Splitting the lane moved
        # that file to the other side. A green parallel run is evidence about
        # ONE lane composition, not about the file.
        "test_u50_widget_backfills.py",
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
    re.compile(r"""["']run_tests(?:_core)?["']""", re.IGNORECASE),
)


@lru_cache(maxsize=1)
def parallel_allowlist() -> frozenset[str]:
    """Return validated test paths relative to ``tests/``; missing is empty."""
    try:
        lines = PARALLEL_ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()

    entries: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        normalized = line.replace("\\", "/")
        relative = PurePosixPath(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        if not relative.parts or relative.parts[0] == "tests":
            continue
        entries.add(relative.as_posix())
    return frozenset(entries)


def _capture_test_key(candidate: Path) -> str | None:
    """Normalize a collected path to a safe path relative to ``tests/``."""
    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(TESTS_ROOT)
        except (OSError, ValueError):
            return None
        parts = relative.parts
    else:
        parts = PurePosixPath(candidate.as_posix().replace("\\", "/")).parts
        if parts and parts[0] == "tests":
            parts = parts[1:]
    if not parts or ".." in parts:
        return None
    return PurePosixPath(*parts).as_posix()


def classify_capture_file(
    path: str | Path,
    *,
    source: str | None = None,
) -> str:
    """Return ``parallel`` only for reviewed, allowlisted, risk-free files."""
    candidate = Path(path)
    basename = candidate.name.lower()
    if basename in SERIAL_EXACT_BASENAMES:
        return "serial"

    # v3.66.921 -- A FILENAME IS NOT A BEHAVIOUR, so the name tokens are
    # OVERRIDABLE by an explicit allowlist entry while the SOURCE checks below
    # are not. The tokens are a proxy for "nobody has looked at this yet"; an
    # allowlist entry IS someone having looked. Measured: 88 files were serial
    # solely because their basename contained "capture" or "runner", with no
    # risky construct anywhere in them.
    #
    # The source checks stay ABSOLUTE, and that asymmetry is the point. They
    # match constructs that leak ACROSS FILES inside one xdist worker --
    # os.environ mutation, sys.modules wiping, os.chdir, a run_tests import --
    # and `--dist loadfile` does NOT prevent that: it keeps a file's tests
    # together, it does not give a file its own worker. Whichever file lands
    # next on that worker inherits the damage, and which files share a worker
    # changes between runs. So a green parallel run is not evidence for those,
    # and no allowlist entry may override them.
    # The tokens still bite for every UNLISTED file, because the allowlist
    # check at the bottom sends those to serial anyway. What changes is only
    # that they no longer VETO an explicit review.
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
    key = _capture_test_key(candidate)
    if key is None or key not in parallel_allowlist():
        return "serial"
    # Reached only when every SOURCE check above passed. An allowlisted file
    # whose only flag was its name is promoted here; one carrying a real
    # construct returned "serial" long before this point.
    return "parallel"


@lru_cache(maxsize=None)
def classify_capture_path(path: str) -> str:
    """Cached filesystem adapter for pytest's per-item collection hook."""
    return classify_capture_file(path)
