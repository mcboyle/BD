"""v3.66.13 — P10 tests for `bdctl dd-diff`.

Drives the dd-diff subcommand by calling bdctl.main() directly with
sandbox HOME so baselines land in tmp_path rather than the developer's
real ~/.config/bdctl/. Covers:

  * --save round-trips: save a baseline, diff against it → 0 diffs
  * --list-baselines (text + JSON modes)
  * Diff detection: perturb a fixture's meta.json, confirm DIFF +
    exit code 1 + per-fixture status in JSON output
  * Error path: --against a nonexistent baseline exits 2
  * --only filter actually filters

The corpus this test exercises is the same one P2 exercises.
"""
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# v3.66.13: same wipe contract as the rest of the P2-era tests.
pytestmark = pytest.mark.bd_module_wipe


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_bdctl():
    """Import bdctl.py as a module (lazy; it pulls urllib + argparse +
    a 1500-line module body, not free at collect time)."""
    spec = importlib.util.spec_from_file_location(
        "bdctl_module", REPO_ROOT / "bdctl.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """Redirect bdctl's config dir to a per-test tmp dir so baselines
    don't pollute the developer's real ~/.config/bdctl. bdctl resolves
    its config dir via $HOME (or $APPDATA on Windows), so setting HOME
    is sufficient on every platform we test on."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Don't leave a stale APPDATA in scope (would override HOME on win)
    monkeypatch.delenv("APPDATA", raising=False)
    return tmp_path


def _run_bdctl(monkeypatch, capsys, *argv):
    """Invoke bdctl.main() with argv as sys.argv. Returns
    (exit_code, stdout, stderr). SystemExit raised by main() is caught
    and converted to the exit code."""
    bdctl = _load_bdctl()
    monkeypatch.setattr(sys, "argv", ["bdctl"] + list(argv))
    try:
        bdctl.main()
        rc = 0
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


# ── --save + --against round-trip ─────────────────────────────────────


def test_save_then_diff_clean(sandbox_home, monkeypatch, capsys):
    rc, out, err = _run_bdctl(monkeypatch, capsys,
                              "dd-diff", "--save", "--name", "baseline")
    assert rc == 0, err
    assert "baseline 'baseline' saved" in out
    # The file should exist where bdctl says it did.
    baseline_path = sandbox_home / ".config" / "bdctl" / "dd-baselines" / "baseline.json"
    assert baseline_path.exists()

    rc, out, err = _run_bdctl(monkeypatch, capsys,
                              "dd-diff", "--against", "baseline", "-q")
    assert rc == 0
    assert "0 changed, 0 new, 0 gone" in out


def test_save_json_emits_structured_data(sandbox_home, monkeypatch, capsys):
    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--save", "--name", "j", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["saved"] == "j"
    assert payload["path"].endswith("/j.json")
    assert isinstance(payload["fixtures"], list)
    assert len(payload["fixtures"]) >= 5  # at least the committed corpus


def test_list_baselines_empty(sandbox_home, monkeypatch, capsys):
    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--list-baselines")
    assert rc == 0
    assert "no baselines saved yet" in out


def test_list_baselines_json(sandbox_home, monkeypatch, capsys):
    _run_bdctl(monkeypatch, capsys,
               "dd-diff", "--save", "--name", "alpha")
    _run_bdctl(monkeypatch, capsys,
               "dd-diff", "--save", "--name", "beta")
    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--list-baselines", "--json")
    assert rc == 0
    payload = json.loads(out)
    names = {b["name"] for b in payload["baselines"]}
    assert names == {"alpha", "beta"}
    for entry in payload["baselines"]:
        assert entry["fixtures"] >= 5
        assert entry["size_bytes"] > 0


# ── Diff detection ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _corpus_copy(tmp_path, monkeypatch):
    """Every test in this file works on a COPY of the corpus.

    autouse because the protection must not depend on a future test author
    remembering to ask for it -- the tracked corpus being writable at all is
    the defect. DD_REPLAY_CORPUS is read by dd-replay at import time, and
    bdctl re-imports it per invocation, so the redirect reaches the in-process
    delegation as well as any direct call.
    """
    dst = tmp_path / "deep_detect"
    shutil.copytree(REPO_ROOT / "tests" / "fixtures" / "deep_detect", dst)
    monkeypatch.setenv("DD_REPLAY_CORPUS", str(dst))
    return dst


def _corpus_dir() -> Path:
    """The corpus these tests may mutate.

    v3.66.1022: THE TRACKED CORPUS IS NOT IT. This helper used to write into
    tests/fixtures/deep_detect/01_hls_master/meta.json and restore it in a
    finally:, and tests/test_v3_66_13_phase2_p2_snapshot_replay.py re-reads
    that file from disk per call -- so on the box's 88-worker lane a read
    landing inside the window saw the swapped host and the snapshot diffed
    against a URL nobody wrote. It failed a real capture.

    The finally: also made it a residue hazard rather than only a race: a
    worker killed mid-window never runs it, and the tracked file stays mutated
    on disk, so the NEXT run fails deterministically on a dirty tree.

    `conftest`-free by design -- the copy is made by the autouse fixture below
    and pointed at through DD_REPLAY_CORPUS, which is what bdctl's freshly
    exec'd copy of dd-replay reads.
    """
    return Path(os.environ.get("DD_REPLAY_CORPUS")
                or (REPO_ROOT / "tests" / "fixtures" / "deep_detect"))


def _perturb_first_fixture(new_value: str) -> str:
    """Mutate the first fixture's base_url. Returns the original value
    so the caller can restore."""
    corpus = _corpus_dir()
    first = sorted(corpus.iterdir())[0]
    meta_path = first / "meta.json"
    meta = json.loads(meta_path.read_text())
    orig = meta["base_url"]
    meta["base_url"] = new_value
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return orig


def _restore_first_fixture(value: str) -> None:
    corpus = _corpus_dir()
    first = sorted(corpus.iterdir())[0]
    meta_path = first / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["base_url"] = value
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")


def test_diff_detects_change_and_exits_1(sandbox_home, monkeypatch, capsys):
    _run_bdctl(monkeypatch, capsys,
               "dd-diff", "--save", "--name", "before")
    orig = _perturb_first_fixture("https://changed.example.test/")
    try:
        rc, out, _ = _run_bdctl(monkeypatch, capsys,
                                "dd-diff", "--against", "before", "-q")
        assert rc == 1, "exit code should be 1 when at least one fixture diffs"
        assert "DIFF" in out
        assert "1 changed" in out
    finally:
        _restore_first_fixture(orig)

    # And clean again afterwards
    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--against", "before", "-q")
    assert rc == 0
    assert "0 changed" in out


def test_diff_json_per_fixture_status(sandbox_home, monkeypatch, capsys):
    _run_bdctl(monkeypatch, capsys,
               "dd-diff", "--save", "--name", "b")
    orig = _perturb_first_fixture("https://different.example.test/")
    try:
        rc, out, _ = _run_bdctl(monkeypatch, capsys,
                                "dd-diff", "--against", "b", "--json")
        assert rc == 1
        payload = json.loads(out)
        assert payload["summary"]["changed"] == 1
        assert payload["summary"]["new"] == 0
        assert payload["summary"]["gone"] == 0
        # Exactly one fixture has status=changed; the rest are same.
        statuses = [r["status"] for r in payload["results"]]
        assert statuses.count("changed") == 1
        assert statuses.count("same") == len(statuses) - 1
        # Changed entry must carry a non-empty diff
        changed = [r for r in payload["results"] if r["status"] == "changed"]
        assert changed[0]["diff"], "JSON 'diff' field empty on changed fixture"
    finally:
        _restore_first_fixture(orig)


# ── Error paths ───────────────────────────────────────────────────────


def test_diff_against_missing_baseline_exits_2(sandbox_home, monkeypatch,
                                               capsys):
    rc, _, err = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--against", "nope")
    assert rc == 2
    assert "no baseline 'nope'" in err


def test_diff_against_missing_baseline_json(sandbox_home, monkeypatch,
                                            capsys):
    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--against", "nope", "--json")
    assert rc == 2
    payload = json.loads(out)
    assert "error" in payload
    assert "no baseline 'nope'" in payload["error"]


# ── --only filter ─────────────────────────────────────────────────────


def test_only_filter_narrows_scope(sandbox_home, monkeypatch, capsys):
    # Save a baseline scoped to a single fixture
    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--save", "--name", "narrow",
                            "--only", "01_hls_master", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["fixtures"] == ["01_hls_master"]

    rc, out, _ = _run_bdctl(monkeypatch, capsys,
                            "dd-diff", "--against", "narrow",
                            "--only", "01_hls_master", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["summary"]["total"] == 1
    assert payload["results"][0]["name"] == "01_hls_master"
