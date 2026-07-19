"""v3.66.13 — P2 snapshot replay tests.

Run every fixture in tests/fixtures/deep_detect/ through deep_detect()
and verify the result matches the committed expected.json. Catches
silent behaviour changes in the offline analyzer (new bugs, score
shifts, new fields, etc.).

When a behaviour change is intentional:
    python3 tools/dd-replay.py --update [--only NAME]

then re-commit the changed expected.json files alongside the code change.

Fixtures with no expected.json are SKIPPED — that's the path for adding
a new fixture before recording its baseline. To force a fixture to
fail-when-missing, add it to ``REQUIRED_FIXTURES`` below.
"""
import json
import sys
from pathlib import Path

import pytest

# We don't want the conftest module wipe here — the dd-replay tool
# imports deep_detect once and uses it throughout, and we exercise the
# same module object. (The fixture's env+cwd half still applies; only
# the sys.modules wipe is gated.)

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "deep_detect"
TOOLS_DIR = REPO_ROOT / "tools"

# Ensure tools/ is importable so we can reuse dd-replay's normalizer +
# runner. Single source of truth for the comparison logic.
sys.path.insert(0, str(TOOLS_DIR))

# dd-replay uses a hyphen in its filename; importlib gets it via
# importlib.util. Lazy-load on first use to keep collection cheap.
_dd_replay = None


def _replay_module():
    global _dd_replay
    if _dd_replay is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dd_replay", TOOLS_DIR / "dd-replay.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _dd_replay = mod
    return _dd_replay


def _fixtures() -> list[Path]:
    if not CORPUS_DIR.is_dir():
        return []
    return sorted(d for d in CORPUS_DIR.iterdir()
                  if d.is_dir() and (d / "meta.json").exists())


# Add fixture names here to make their absence (no expected.json) a
# hard failure rather than a SKIP. Useful once a fixture is "blessed"
# as a regression check.
REQUIRED_FIXTURES = {
    "01_hls_master",
    "02_jsonld_video",
    "03_state_blob",
    "04_dash_manifest",
    "05_login_form",
}


@pytest.mark.parametrize(
    "fixture_dir",
    _fixtures(),
    ids=lambda d: d.name,
)
def test_replay_matches_expected(fixture_dir: Path):
    expected_path = fixture_dir / "expected.json"
    if not expected_path.exists():
        if fixture_dir.name in REQUIRED_FIXTURES:
            pytest.fail(
                f"required fixture {fixture_dir.name} has no "
                f"expected.json — run `python3 tools/dd-replay.py "
                f"--update --only {fixture_dir.name}` to record one")
        pytest.skip(f"no expected.json for {fixture_dir.name} "
                    f"(run dd-replay --update)")

    replay = _replay_module()
    actual = replay._run_fixture(fixture_dir)
    expected = json.loads(expected_path.read_text())

    diff = replay._diff(expected, actual, fixture_dir.name)
    if diff:
        pytest.fail(
            f"deep_detect output for {fixture_dir.name} differs from "
            f"recorded expected.json. If the change is intentional, "
            f"run `python3 tools/dd-replay.py --update --only "
            f"{fixture_dir.name}`.\n\n{diff}")


def test_corpus_is_nonempty():
    """Sanity check: if someone accidentally deletes the corpus, fail
    loudly here rather than silently passing zero parametrized cases."""
    fixtures = _fixtures()
    assert fixtures, (
        f"no fixtures in {CORPUS_DIR} — did the corpus get deleted?")
    assert len(fixtures) >= len(REQUIRED_FIXTURES), (
        f"expected at least {len(REQUIRED_FIXTURES)} fixtures, "
        f"found {len(fixtures)}")


def test_replay_tool_round_trips(tmp_path):
    """End-to-end check: invoking dd-replay via its main() with no
    args should exit 0 against the committed corpus. Verifies the
    tool wiring (argparse, exit codes) hasn't drifted from this
    harness's direct-import usage."""
    replay = _replay_module()
    rc = replay.main([])
    assert rc == 0, "dd-replay run mode returned non-zero against committed corpus"
