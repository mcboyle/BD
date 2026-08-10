"""Fail-closed pytest lane classification used by ``capture.sh``.

Only files in the checked-in, reviewed allowlist can enter the xdist lane.
Unlisted, unreadable, malformed, and risk-matching paths default to serial.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from functools import lru_cache
from pathlib import Path, PurePosixPath


TESTS_ROOT = Path(__file__).resolve().parent
PARALLEL_ALLOWLIST_PATH = TESTS_ROOT / "capture_parallel_files.txt"


SERIAL_EXACT_BASENAMES = frozenset(
    {
        # v3.66.998 -- ELEVEN NAMES LEFT THIS SET, and each departure is a
        # claim with its evidence, because a removal without one is how a
        # refutation gets relitigated by a green run:
        #
        # * test_fixture_site / test_fixture_site2 / test_session_keeper /
        #   test_v3_66_13_phase2_p2_snapshot_replay: ORIGINAL (pre-@921)
        #   entries with no recorded refutation -- inherited from the old
        #   runner's _PINNED_TOGETHER era, whose 754c pin note calls the
        #   fixture sites "fixed-port" while both files drive Flask TEST
        #   CLIENTS ("no real socket... don't need a free port"). Reviewed
        #   per file; session_keeper's one wall-clock bound was widened.
        # * test_u50_widget_backfills (@922's refutation): FIXED, not
        #   re-promoted on a green run -- the test now creates its own
        #   schema (db_init + migrations), so the cross-file table
        #   dependency @922 named no longer exists.
        # * test_u30_runner_replay (@923's refutation): FIXED -- the
        #   empty-fleet test asserted the process-global app.runners was
        #   empty; it now pins that global to the state it is about.
        # * test_differential_oracle_frontend / test_fuzz_harness_frontend /
        #   test_reachability_frontend (@923, refuted directly): the named
        #   mechanism -- one-second AdapterBudget wall clocks under 16-64
        #   concurrent processes -- was REMOVED: non-subject budgets are now
        #   30s, timeout-side tests keep small budgets against sleeps that
        #   exceed them decisively, and the two descendant-reap tests carry
        #   10s budgets because their pid files must survive worker boot.
        # * test_coverage_map_frontend / test_semantic_diff_frontend (@923,
        #   named by SHAPE, never refuted directly): the shape claim was a
        #   grep artifact. The "(18 and 19 worker/child references)" are
        #   line-counts of /worker|child/ -- and in coverage_map those lines
        #   are fixture FILENAMES (package_a/worker.py), in semantic_diff
        #   nested `def child()` names in parsed sample source. Neither file
        #   contains a wall clock, a sleep, or an AdapterBudget at all
        #   (measured at v3.66.997), and semantic_diff's "budget assertions"
        #   are a deterministic node-count ValueError, not a timer.
        #
        # The box capture is still the gate: anything it refutes comes back
        # HERE by name with its mechanism, per the protocol below.
        #
        # v3.66.754c -- FILE-LEVEL 900s timeout under the old runner's
        # --workers=480 oversubscription (root-caused, not theorised), and the
        # heaviest in-process consumer of bulk_downloader.app module globals:
        # probe_fixtures replays ~126 MUTATING call sites against the real app
        # in a module-scoped fixture. Not promoted without an isolation story
        # for that state.
        "test_v3_66_729_body_contract_fixtures.py",
        # Drives run_tests.py end-to-end as a subprocess ~5 times; also pinned
        # by the runner-literal rule below. Cheap to keep serial; freeing it
        # is the runner-rule design decision, not a per-file fix.
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
        # (tier1b's candidate mechanism: deadlock_detector / thread_dump /
        # dev_metrics assert over PROCESS-AMBIENT threads and counters that
        # other files' residue legitimately alters. exec_bridge runs real
        # allowlisted binaries under the bridge's own hard timeout.)
        "test_dev_suite_tier1b.py",
        "test_v3_66_717_exec_bridge.py",
        # v3.66.923 -- refuted by the all-parallel box sweep (-n 64) and ZERO
        # failures survived a serial retry. perf_lab asserts over
        # process-ambient memory/thread snapshots (pl.snapshot, audit deltas),
        # which a shared worker's residue legitimately moves; it is also the
        # file CLAUDE.md section 5 once recorded as a hanger when the tree ran
        # whole (disproven as a hang at @947, but the refutation as parallel
        # stands).
        "test_perf_lab.py",
        # v3.66.923 -- refuted by the N/2 packing (-n 32) and by NOTHING ELSE.
        # The full-width run at -n 64 passed it. That is the entire case for
        # running more than one width: file-to-worker assignment is by count,
        # so halving the workers changed who shares a worker and exposed it.
        "test_t14_vpn_probe_egress.py",
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

# The ONE source risk an allowlist entry may not override. Importing the
# fallback runner rewires global interpreter state, and a dedicated test
# (test_allowlisted_file_cannot_bypass_dynamic_runner_import_risk) exists
# to keep it that way. Named separately at v3.66.923 so the rest of the
# heuristics could become overridable without taking this with them.
ABSOLUTE_SERIAL_SNIPPETS = (
    "import run_tests",
    "from run_tests",
    "run_tests.py",
)

SERIAL_SOURCE_SNIPPETS = (
    "pytest.mark.bd_module_wipe",
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

RUNTESTS_LITERAL = re.compile(r"""["']run_tests(?:_core)?["']""", re.IGNORECASE)

# File-loader callables that can execute the runner in THIS process without any
# import statement or bare "run_tests" literal ever appearing. The escape that
# forced this (found at v3.66.996): spec_from_file_location("rtc",
# "run_tests_core.py") + exec_module classified PARALLEL -- the path literal's
# trailing ".py" defeats RUNTESTS_LITERAL's closing-quote anchor and is not a
# substring match for "run_tests.py". One live instance existed in the parallel
# lane (test_harness_retry_timeout.py).
_RUNNER_LOADER_MARKERS = ("spec_from_file_location", "sourcefileloader")


def _loads_runner_dynamically(code: str) -> bool:
    """True when CODE calls a file-loader with a run_tests* argument.

    AST-scoped ON PURPOSE, not a substring: a subprocess driver that embeds
    the same call inside a heredoc STRING never executes it in this
    interpreter, and a substring check would pin those files for a hazard the
    process boundary already contains. What this walks is what THIS file's
    interpreter would run. Literals are searched through each argument's whole
    subtree, so `REPO / "run_tests_core.py"` is seen, not just bare strings.

    FAIL-CLOSED on unparseable code: judged on everything it contains, the
    same posture as code_only's fallback. (test_all_sources_parse keeps that
    population empty for tracked files.)
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return "run_tests" in code.lower()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        try:
            callee = ast.unparse(node.func).lower()
        except Exception:
            continue
        if not any(marker in callee for marker in _RUNNER_LOADER_MARKERS):
            continue
        arguments = list(node.args) + [kw.value for kw in node.keywords]
        for argument in arguments:
            for sub in ast.walk(argument):
                if (isinstance(sub, ast.Constant)
                        and isinstance(sub.value, str)
                        and "run_tests" in sub.value.lower()):
                    return True
    return False


def runner_import_hazard(code: str) -> bool:
    """The ONE source hazard no allowlist entry may override, asked of CODE.

    Exported as a single predicate so the guard suite can BORROW it instead of
    restating it -- at v3.66.992 a guard that borrowed the constants but not
    the text they applied to held a second definition of "hazard" and failed
    on every promoted file.
    """
    lowered = code.lower()
    if any(snippet in lowered for snippet in ABSOLUTE_SERIAL_SNIPPETS):
        return True
    if RUNTESTS_LITERAL.search(code):
        return True
    return _loads_runner_dynamically(code)


@lru_cache(maxsize=2048)
def code_only(source: str) -> str:
    """`source` with comments and docstrings removed.

    FAIL-CLOSED: any tokenizer or parser failure returns the ORIGINAL text, so a
    file this module cannot read is judged on everything it contains rather than
    on a partially-stripped fragment. Silently returning less text is how a
    stripper turns into a way to hide a real import behind a syntax error.
    """
    try:
        tokens = [
            tok for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type != tokenize.COMMENT
        ]
        stripped = tokenize.untokenize(tokens)
    except Exception:
        return source
    try:
        tree = ast.parse(stripped)
    except SyntaxError:
        return source

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    try:
        return ast.unparse(tree)
    except Exception:
        return source


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
    # OVERRIDABLE by an explicit allowlist entry. The tokens are a proxy for
    # "nobody has looked at this yet"; an allowlist entry IS someone having
    # looked. Measured: 88 files were serial solely because their basename
    # contained "capture" or "runner", with no risky construct anywhere in
    # them.
    #
    # v3.66.998 -- what is absolute CHANGED AT @923 and this comment lied
    # about it for 75 releases (found twice independently in one session,
    # recorded at SESSION_CARRY 15.79): @921 made every source check
    # unoverridable; @923 moved the allowlist ABOVE all of them EXCEPT the
    # runner-import rule. So today exactly ONE hazard outranks review --
    # `runner_import_hazard`, because importing or file-loading the fallback
    # runner rewires global interpreter state -- and every other heuristic
    # below is a fail-closed default for UNLISTED files only. `--dist
    # loadfile` still does not give a file its own worker, which is why a
    # green parallel run is weak evidence and reviews are per file.
    if source is None:
        try:
            source = candidate.read_text(encoding="utf-8")
        except OSError:
            # A path pytest collected but we cannot inspect is not proven safe.
            return "serial"
    lowered = source.lower()

    # ABSOLUTE, and the only source check that still is. See the constant.
    #
    # v3.66.990: asked of CODE, not prose. The risk this rule names is that
    # importing the fallback runner rewires global interpreter state -- and a
    # docstring imports nothing. Measured over 1277 tracked test files: 143 were
    # pinned here, 4 genuinely import the runner, and 139 only MENTION it in a
    # comment or docstring. Since the absolute checks sit ABOVE the allowlist,
    # those files could not be promoted by review either; they were structurally
    # unreachable. Section 0's "a comment is inside the denominator of every gate
    # that reads source text", costing ~124 files a place in the serial lane.
    #
    # The rule itself is UNCHANGED -- same snippets, same absoluteness. Only the
    # text it reads changed, and `code_only` falls back to the raw source on any
    # parse failure, so an unparseable file is still judged on everything it
    # contains. The dynamic forms the guard test pins
    # (`importlib.import_module("run_tests")`, `__import__("run_tests")`) are
    # code and survive the strip.
    code = code_only(source)
    if runner_import_hazard(code):
        return "serial"

    # v3.66.923: EXPLICIT REVIEW NOW OUTRANKS THE REMAINING HEURISTICS, on
    # whole-tree experimental evidence rather than on none. The entire tree was
    # run in ONE parallel lane on the box -- 1232 files, 14,856 tests, 4m06s --
    # and of nine failures across seven files, ZERO survived a serial retry.
    # Every file promoted below was in that run.
    #
    # This is the reverse of the v3.66.921 posture, and deliberately so. There
    # the evidence was a run of the serial lane ALONE, which is a different
    # composition from what ships -- and splitting the lane duly broke
    # test_u50_widget_backfills, whose table-seeding dependency ended up on the
    # other side. An all-parallel sweep has no other side: it is the shipping
    # configuration, measured directly.
    #
    # What it is still NOT evidence for is a different PACKING. xdist assigns
    # files to workers by count, so a second run at another -n shuffles who
    # shares a worker. Anything that surfaces there is a one-line addition to
    # SERIAL_EXACT_BASENAMES above, which is where every refutation is recorded
    # BY NAME rather than by omission -- the allowlist is generated, so an
    # omission would simply be regenerated away.
    key = _capture_test_key(candidate)
    if key is not None and key in parallel_allowlist():
        return "parallel"

    # Unlisted: the heuristics still decide, and they still fail closed.
    if any(snippet in lowered for snippet in SERIAL_SOURCE_SNIPPETS):
        return "serial"
    if any(pattern.search(source) for pattern in SERIAL_SOURCE_PATTERNS):
        return "serial"
    if any(token in basename for token in SERIAL_NAME_TOKENS):
        return "serial"

    # FAIL-CLOSED, and this line is load-bearing. Everything reaching here is
    # UNLISTED -- the allowlist returned above -- so an unreviewed file is
    # serial even when it looks pure.
    #
    # v3.66.923: moving the allowlist check above the heuristics briefly left
    # this as `return "parallel"`, which promoted every unreviewed file in the
    # repo and destroyed the property this module exists for. Two tests caught
    # it on the first run. Do not "simplify" it back.
    return "serial"


@lru_cache(maxsize=None)
def classify_capture_path(path: str) -> str:
    """Cached filesystem adapter for pytest's per-item collection hook."""
    return classify_capture_file(path)
