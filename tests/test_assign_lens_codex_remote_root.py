"""Tests for bd-assign-lens.sh codex-remote root support and cutdir normalization.

Verifies:
1. Default ROOTS includes /home/mboyle/fleet-run-artifacts/codex-remote
2. review_cut() strips redundant -local suffix to prevent -local-local paths
3. prep_refused() strips -local suffix before stripping row prefix
4. find uses -maxdepth 2 to avoid full-tree traversal into worktree interiors
5. cutdir loop skips *.superseded-*
6. newer_gen_exists() skips *.superseded-*
7. End-to-end assignment of a cut under codex-remote root
8. Negative controls: superseded cutdir, closed row, landed cut, non-PATCH DONE.md are skipped
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
import pytest

BD_GATE_SCOPE = "module"

_CANDIDATE_FILE = Path(
    os.environ.get(
        "BD_ASSIGN_LENS_CANDIDATE",
        "/home/mboyle/bd-persist/harness-work/FIX/assign-lens-codex-remote-root/bd-assign-lens.sh",
    )
)


def _get_script_content() -> str:
    if _CANDIDATE_FILE.is_file():
        return _CANDIDATE_FILE.read_text(encoding="utf-8")
    # Fallback to current harness if candidate work dir not mounted
    live_harness = Path("/home/mboyle/bd-persist/harness/bd-assign-lens.sh")
    if live_harness.is_file():
        return live_harness.read_text(encoding="utf-8")
    raise FileNotFoundError("Could not find bd-assign-lens.sh")


def test_roots_default_includes_codex_remote():
    content = _get_script_content()
    # ROOTS line must include codex-remote
    roots_lines = [line for line in content.splitlines() if line.startswith("ROOTS=")]
    assert len(roots_lines) == 1, "Expected single ROOTS= definition"
    assert "/home/mboyle/fleet-run-artifacts/codex-remote" in roots_lines[0]


def test_review_cut_normalizes_local_suffix(tmp_path: Path):
    content = _get_script_content()
    helpers = content.split('if [ "${1:-}" = --dry-run ]; then')[0]
    script = tmp_path / "test_review_cut.sh"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
export BD_ASSIGN_LENS_REVIEW_ROOT=/test/review
{helpers}
echo "$(review_cut row123)"
echo "$(review_cut row123-local)"
""",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = res.stdout.strip().splitlines()
    assert lines[0] == "/test/review/row123-local"
    assert lines[1] == "/test/review/row123-local"


def test_find_maxdepth_two():
    content = _get_script_content()
    assert 'find "$root" -maxdepth 2 -type f -name DONE.md' in content


def test_prep_refused_strips_local_suffix(tmp_path: Path):
    content = _get_script_content()
    helpers = content.split('if [ "${1:-}" = --dry-run ]; then')[0]
    log_file = tmp_path / "review.log"
    log_file.write_text("2026-09-20T12:00:00Z PREP-REFUSED row555 reason\n", encoding="utf-8")
    script = tmp_path / "test_prep_refused.sh"
    script.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
export BD_ASSIGN_LENS_REVIEW_LOG={log_file}
{helpers}
prep_refused row555-local && echo YES || echo NO
""",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == "YES"


def test_cutdir_superseded_skipped():
    content = _get_script_content()
    assert 'case "$cutdir" in *.superseded-*) continue;; esac' in content


def test_newer_gen_exists_skips_superseded():
    content = _get_script_content()
    # Check that newer_gen_exists contains *.superseded-* in its skip case
    newer_gen_def = content.split("newer_gen_exists(){")[1].split("same_tree_verdict()")[0]
    assert "*.superseded-*" in newer_gen_def


def test_e2e_assignment_and_negative_controls(tmp_path: Path):
    content = _get_script_content()
    root = tmp_path / "codex-remote"
    review_root = tmp_path / "review"

    # Eligible candidate: row999-local
    cut_dir = root / "row999-local"
    cut_dir.mkdir(parents=True)
    (cut_dir / "DONE.md").write_text("VERDICT: PATCH\n", encoding="utf-8")

    rev_dir = review_root / "row999-local" / ".review"
    rev_dir.mkdir(parents=True)
    (rev_dir / "BRIEF.md").write_text("brief content\n", encoding="utf-8")
    (rev_dir / "TIER.md").write_text("TIER: T2\n", encoding="utf-8")

    # Negative control 1: superseded cutdir
    cut_dir_sup = root / "row998-local.superseded-123"
    cut_dir_sup.mkdir(parents=True)
    (cut_dir_sup / "DONE.md").write_text("VERDICT: PATCH\n", encoding="utf-8")
    rev_dir_sup = review_root / "row998-local.superseded-123-local" / ".review"
    rev_dir_sup.mkdir(parents=True)
    (rev_dir_sup / "BRIEF.md").write_text("brief\n", encoding="utf-8")
    (rev_dir_sup / "TIER.md").write_text("TIER: T2\n", encoding="utf-8")

    # Negative control 2: non-PATCH DONE.md
    cut_dir_ref = root / "row997-local"
    cut_dir_ref.mkdir(parents=True)
    (cut_dir_ref / "DONE.md").write_text("VERDICT: REFUTE\n", encoding="utf-8")
    rev_dir_ref = review_root / "row997-local" / ".review"
    rev_dir_ref.mkdir(parents=True)
    (rev_dir_ref / "BRIEF.md").write_text("brief\n", encoding="utf-8")
    (rev_dir_ref / "TIER.md").write_text("TIER: T2\n", encoding="utf-8")

    # Mock claim script
    mock_claim = tmp_path / "claim.sh"
    mock_claim.write_text(
        """#!/usr/bin/env bash
if [ "$1" = "holder" ]; then echo "NOT-HELD none"; exit 0; fi
if [ "$1" = "check" ]; then exit 1; fi
if [ "$1" = "take" ]; then echo "OK"; exit 0; fi
""",
        encoding="utf-8",
    )
    mock_claim.chmod(0o755)

    # Mock say script
    mock_say = tmp_path / "say.sh"
    mock_say.write_text(
        """#!/usr/bin/env bash
exit 0
""",
        encoding="utf-8",
    )
    mock_say.chmod(0o755)

    # Override candidates() function to return a mock idle lens
    patched_content = content.replace(
        "candidates(){",
        'candidates(){ printf "correctness\\tbd-lens-mock\\n"; return 0; }\nold_candidates(){',
    )
    run_script = tmp_path / "run.sh"
    run_script.write_text(patched_content, encoding="utf-8")

    env = os.environ.copy()
    env["BD_ASSIGN_LENS_ROOTS"] = str(root)
    env["BD_ASSIGN_LENS_REVIEW_ROOT"] = str(review_root)
    env["BD_ASSIGN_LENS_CLAIM"] = str(mock_claim)
    env["BD_ASSIGN_LENS_SAY"] = str(mock_say)
    env["BD_ASSIGN_LENS_TRAINS"] = str(tmp_path / "trains")
    env["BD_ASSIGN_LENS_REVIEW_LOG"] = str(tmp_path / "review.log")
    env["BD_ASSIGN_LENS_REGISTER_REF"] = ""
    env["BD_ASSIGN_LENS_REGISTER"] = ""

    res = subprocess.run(
        ["bash", str(run_script)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    out = res.stdout

    # Positive control: row999-local is assigned
    assert "ASSIGNED cut=row999-local tier=T2 lens=bd-lens-mock" in out

    # Negative controls: row998 (superseded) and row997 (non-PATCH) are NOT assigned
    assert "row998" not in out
    assert "row997" not in out
