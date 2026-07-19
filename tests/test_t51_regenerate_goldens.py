"""T51 — tools/regenerate_goldens.py CLI.

The T42 dev tool (/api/dev/golden_files) reports drift between
golden baselines and their .current siblings, read-only. T51 is the
operator's regenerate path: a CLI that overwrites .golden files
from .current under explicit consent + audit logging.

Coverage:
  - find_repo_root() locates the project root from tools/
  - goldens_dir() returns the conventional path
  - scan_goldens() distinguishes drift from clean and missing-current
  - regenerate() atomically overwrites + records meta + refuses empty
  - main() dry-run lists drift, exits 0
  - main() --apply without --reason exits 1
  - main() --apply --reason writes + appends audit log + writes meta
  - main() --only restricts to one stem
  - Dirty-tree guard refuses --apply when goldens dir has uncommitted
    changes; --force overrides it
  - Atomic-write contract: no `.tmp` siblings remain after regenerate

The CLI is importable, so most tests call its functions directly
in-process; the end-to-end CLI tests use subprocess against a
tmpdir fake-repo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


# tools/ is not a package; import the module via file path.
import importlib.util as _ilu

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOOL_PATH = _REPO_ROOT / "tools" / "regenerate_goldens.py"
_spec = _ilu.spec_from_file_location("regenerate_goldens", _TOOL_PATH)
rg = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(rg)


# ── Helpers ─────────────────────────────────────────────────────────

def _make_fake_repo(tmp_path):
    """Build a minimal repo skeleton find_repo_root recognises:
    CHANGELOG.md + bulk_downloader/ + tests/fixtures/golden/."""
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n",
                                            encoding="utf-8")
    (tmp_path / "bulk_downloader").mkdir()
    (tmp_path / "bulk_downloader" / "__init__.py").write_text("",
                                            encoding="utf-8")
    gdir = tmp_path / "tests" / "fixtures" / "golden"
    gdir.mkdir(parents=True)
    return gdir


def _write_golden_pair(gdir, stem, golden_bytes, current_bytes):
    (gdir / f"{stem}.golden").write_bytes(golden_bytes)
    (gdir / f"{stem}.current").write_bytes(current_bytes)


def _run_cli(repo_root, *extra_args):
    """Invoke the CLI in a subprocess with --repo-root set to the
    fake repo. Returns CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(_TOOL_PATH),
         "--repo-root", str(repo_root), *extra_args],
        capture_output=True, text=True, timeout=60,
    )


# ── Discovery helpers ───────────────────────────────────────────────

def test_find_repo_root_finds_real_repo():
    """find_repo_root from this file should locate the real repo."""
    root = rg.find_repo_root(Path(__file__).resolve())
    assert (root / "CHANGELOG.md").is_file()
    assert (root / "bulk_downloader").is_dir()


def test_goldens_dir_is_under_repo_root(tmp_path):
    _make_fake_repo(tmp_path)
    gd = rg.goldens_dir(tmp_path)
    assert gd == tmp_path / "tests" / "fixtures" / "golden"


# ── scan_goldens() ─────────────────────────────────────────────────

def test_scan_goldens_empty_dir(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    assert rg.scan_goldens(gdir) == []


def test_scan_goldens_distinguishes_drift_from_clean(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "clean", b"abc", b"abc")
    _write_golden_pair(gdir, "drifted", b"abc", b"xyz")
    records = rg.scan_goldens(gdir)
    by_stem = {r["stem"]: r for r in records}
    assert by_stem["clean"]["drift"] is False
    assert by_stem["drifted"]["drift"] is True
    assert by_stem["drifted"]["current_bytes"] == 3
    assert by_stem["drifted"]["golden_sha256"] != \
           by_stem["drifted"]["current_sha256"]


def test_scan_goldens_missing_current_reported(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    (gdir / "lonely.golden").write_bytes(b"x")
    records = rg.scan_goldens(gdir)
    assert len(records) == 1
    assert records[0]["error"] == "no .current sibling"


def test_scan_goldens_only_filter(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "a", b"1", b"2")
    _write_golden_pair(gdir, "b", b"1", b"2")
    records = rg.scan_goldens(gdir, only="a")
    assert len(records) == 1
    assert records[0]["stem"] == "a"


# ── regenerate() ───────────────────────────────────────────────────

def test_regenerate_overwrites_drifted_golden(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"old", b"NEW VALUE")
    records = rg.scan_goldens(gdir)
    result = rg.regenerate(gdir, records, reason="t", rev="abc1234")
    assert result["applied"] == ["x"]
    assert (gdir / "x.golden").read_bytes() == b"NEW VALUE"


def test_regenerate_records_meta(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"old", b"new")
    records = rg.scan_goldens(gdir)
    rg.regenerate(gdir, records, reason="rolled out v2",
                  rev="deadbee")
    meta = json.loads(
        (gdir / "x.golden.meta").read_text(encoding="utf-8"))
    assert meta["last_regenerated_reason"] == "rolled out v2"
    assert meta["last_regenerated_rev"] == "deadbee"
    assert "last_regenerated_ts" in meta


def test_regenerate_skips_clean(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "clean", b"same", b"same")
    records = rg.scan_goldens(gdir)
    result = rg.regenerate(gdir, records, reason="t", rev="r")
    assert result["applied"] == []
    assert any(stem == "clean" and "no drift" in reason
               for stem, reason in result["skipped"])


def test_regenerate_refuses_empty_current(tmp_path):
    """Guard against regenerating from a half-written test output."""
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"non-empty old", b"")
    records = rg.scan_goldens(gdir)
    result = rg.regenerate(gdir, records, reason="t", rev="r")
    assert result["applied"] == []
    assert any("empty .current" in e for _, e in result["errors"])
    # Golden must NOT have been overwritten.
    assert (gdir / "x.golden").read_bytes() == b"non-empty old"


def test_regenerate_atomic_no_tmp_left(tmp_path):
    """After a successful regenerate, no .tmp files remain in gdir."""
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"old", b"new")
    records = rg.scan_goldens(gdir)
    rg.regenerate(gdir, records, reason="t", rev="r")
    tmp_files = list(gdir.glob("*.tmp"))
    assert tmp_files == [], f"leftover .tmp: {tmp_files}"


# ── append_log() ────────────────────────────────────────────────────

def test_append_log_creates_and_appends(tmp_path):
    _make_fake_repo(tmp_path)
    log_path = rg.append_log(tmp_path, "first", "r1",
                              {"applied": ["a"], "errors": []})
    rg.append_log(tmp_path, "second", "r2",
                   {"applied": ["b"], "errors": []})
    lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "reason=first" in lines[0]
    assert "reason=second" in lines[1]
    assert "applied=a" in lines[0]
    assert "applied=b" in lines[1]


# ── CLI: dry-run path ──────────────────────────────────────────────

def test_cli_dry_run_lists_drift_exits_zero(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "drifted", b"old", b"new")
    _write_golden_pair(gdir, "clean", b"same", b"same")
    cp = _run_cli(tmp_path)
    assert cp.returncode == 0, cp.stderr
    assert "DRIFT" in cp.stdout
    assert "drifted" in cp.stdout
    # Golden untouched.
    assert (gdir / "drifted.golden").read_bytes() == b"old"


def test_cli_no_goldens_dir_exits_zero(tmp_path):
    # Build the repo skeleton but DON'T create the goldens dir.
    (tmp_path / "CHANGELOG.md").write_text("", encoding="utf-8")
    (tmp_path / "bulk_downloader").mkdir()
    cp = _run_cli(tmp_path)
    assert cp.returncode == 0
    assert "no goldens dir" in cp.stdout


# ── CLI: apply path ────────────────────────────────────────────────

def test_cli_apply_without_reason_exits_one(tmp_path):
    _make_fake_repo(tmp_path)
    cp = _run_cli(tmp_path, "--apply")
    assert cp.returncode == 1
    assert "requires --reason" in cp.stderr


def test_cli_apply_with_reason_writes_and_logs(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "feature_x", b"old", b"NEW")
    cp = _run_cli(tmp_path,
                  "--apply", "--reason", "schema bump 2026-05-21")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    assert (gdir / "feature_x.golden").read_bytes() == b"NEW"
    log = (tmp_path / "tools" / "regenerate_goldens.log"
           ).read_text(encoding="utf-8")
    assert "schema bump 2026-05-21" in log
    assert "applied=feature_x" in log


def test_cli_only_restricts_scope(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "a", b"old_a", b"new_a")
    _write_golden_pair(gdir, "b", b"old_b", b"new_b")
    cp = _run_cli(tmp_path, "--apply", "--reason", "test",
                  "--only", "a")
    assert cp.returncode == 0
    assert (gdir / "a.golden").read_bytes() == b"new_a"
    # b must be untouched.
    assert (gdir / "b.golden").read_bytes() == b"old_b"


# ── Dirty-tree guard ───────────────────────────────────────────────

def _git_init(repo_root):
    """Initialise a fresh git repo at repo_root with a baseline
    commit so subsequent edits show up as 'dirty'."""
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", "-C", str(repo_root), "init", "-q"],
                   check=True, env=env)
    subprocess.run(["git", "-C", str(repo_root), "add", "."],
                   check=True, env=env)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-q",
                    "-m", "initial"],
                   check=True, env=env)


def test_cli_apply_refuses_when_goldens_dirty(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"old", b"new")
    _git_init(tmp_path)
    # Now make the goldens dir dirty by editing the .golden.
    (gdir / "x.golden").write_bytes(b"dirty_edit")
    cp = _run_cli(tmp_path, "--apply", "--reason", "test")
    assert cp.returncode == 2, (cp.stdout, cp.stderr)
    assert "REFUSED" in cp.stderr or "uncommitted" in cp.stderr.lower()


def test_cli_apply_force_overrides_dirty_tree(tmp_path):
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"old", b"new")
    _git_init(tmp_path)
    # Edit unrelated file inside goldens dir to dirty it.
    (gdir / "stray.txt").write_text("dirty", encoding="utf-8")
    cp = _run_cli(tmp_path, "--apply", "--reason", "test",
                  "--force")
    assert cp.returncode == 0, (cp.stdout, cp.stderr)
    assert (gdir / "x.golden").read_bytes() == b"new"


def test_cli_dry_run_ignores_dirty_tree(tmp_path):
    """Dry-run never writes, so dirty-tree guard doesn't apply."""
    gdir = _make_fake_repo(tmp_path)
    _write_golden_pair(gdir, "x", b"old", b"new")
    _git_init(tmp_path)
    (gdir / "x.golden").write_bytes(b"manually_edited")
    cp = _run_cli(tmp_path)
    assert cp.returncode == 0
    # Still reports drift relative to .current.
    assert "DRIFT" in cp.stdout or "drift" in cp.stdout.lower()


# ── Source-grep guard ──────────────────────────────────────────────

def test_tool_is_off_network():
    """The CLI must not import urllib/requests/http — regeneration
    is off the network by design."""
    src = _TOOL_PATH.read_text(encoding="utf-8")
    # Allow these names only in comments/strings — quick guard: no
    # `import` of them at module level.
    forbidden = ["import urllib", "import requests", "import http",
                 "from urllib", "from requests", "from http"]
    for needle in forbidden:
        assert needle not in src, (
            f"regenerate_goldens.py must not network-import: "
            f"found {needle!r}")
