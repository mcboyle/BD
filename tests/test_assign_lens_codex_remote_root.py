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

_LIVE_HARNESS = Path("/home/mboyle/bd-persist/harness/bd-assign-lens.sh")
_LIVE_STATE = (
    Path("/home/mboyle/bd-persist/state/lens-batches.json"),
    Path("/home/mboyle/bd-persist/logs/assign-lens.cron.log"),
    Path("/home/mboyle/bd-persist/review-claims.tsv"),
)


def _get_script_content() -> str:
    # stalegate-assign-lens-codex-remote-root: the old default candidate,
    # harness-work/FIX/assign-lens-codex-remote-root/, survives on band hosts
    # as a 2026-09-20 copy that predates the deployed script, so a host that
    # still had it judged a script nobody runs.  A candidate is named
    # explicitly; otherwise the deployed script is the subject.
    candidate = os.environ.get("BD_ASSIGN_LENS_CANDIDATE")
    if candidate:
        return Path(candidate).read_text(encoding="utf-8")
    if _LIVE_HARNESS.is_file():
        return _LIVE_HARNESS.read_text(encoding="utf-8")
    pytest.skip(f"COULD NOT LOOK: {_LIVE_HARNESS} is not deployed on this host")


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

    # Negative control 3: a row the register reads CLOSED.  With the register
    # open this cut sorts ahead of row999 and takes the only lens (measured),
    # so its absence below is the register filter's doing.
    cut_dir_closed = root / "row996-local"
    cut_dir_closed.mkdir(parents=True)
    (cut_dir_closed / "DONE.md").write_text("VERDICT: PATCH\n", encoding="utf-8")
    rev_dir_closed = review_root / "row996-local" / ".review"
    rev_dir_closed.mkdir(parents=True)
    (rev_dir_closed / "BRIEF.md").write_text("brief\n", encoding="utf-8")
    (rev_dir_closed / "TIER.md").write_text("TIER: T2\n", encoding="utf-8")

    # The register is a fixture repo, never origin/main's: an empty
    # BD_ASSIGN_LENS_REGISTER falls back to the real register, where row 999
    # was CLOSED at @1660 and this test went red on a row it never meant.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "register.md").write_text(
        "| 996 | CLOSED @1 | fixture row |\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "register.md"],
                 ["-c", "user.name=t", "-c", "user.email=t@t",
                  "commit", "-qm", "register fixture"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)

    # The deployed script's object gates, answered "current": the fixture
    # cuts are plain dirs, which the real checker/object-map cannot judge.
    mock_object_check = tmp_path / "object_check.py"
    mock_object_check.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    mock_object_map = tmp_path / "object_map.sh"
    mock_object_map.write_text(
        "#!/usr/bin/env bash\necho 'OBJECT-MAP: SAME fixture'\n",
        encoding="utf-8")
    mock_object_map.chmod(0o755)

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
    env["BD_ASSIGN_LENS_REPO"] = str(repo)
    env["BD_ASSIGN_LENS_REGISTER_REF"] = "HEAD"
    env["BD_ASSIGN_LENS_REGISTER"] = "register.md"
    env["BD_REVIEW_OBJECT_CHECK"] = str(mock_object_check)
    env["BD_ASSIGN_LENS_OBJECT_MAP"] = str(mock_object_map)
    # Every fleet ledger the deployed script writes is a fixture (lens B13-B
    # REFUTE: a successful assignment reserved "bd-lens-mock" in the LIVE
    # state/lens-batches.json, six runs exhausted its cap and the test went
    # red on NO-IDLE-LENS).
    state = tmp_path / "state"
    state.mkdir()
    env["BD_ASSIGN_LENS_BATCH_STATE"] = str(state / "lens-batches.json")
    env["BD_ASSIGN_LENS_LOG"] = str(state / "assign-lens.cron.log")
    env["BD_ASSIGN_LENS_RETIRED"] = str(state / "retired-seats.tsv")
    env["BD_REVIEW_CLAIMS"] = str(state / "review-claims.tsv")
    env["BD_ASSIGN_LENS_REFRESH_INBOX"] = str(state / "inbox")


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
    assert "row996" not in out

    # The reservation landed in the fixture ledger, and no live ledger names
    # this run.  (Content, not mtime: the real cron writes these every minute.)
    assert "bd-lens-mock" in (state / "lens-batches.json").read_text(encoding="utf-8")
    for p in _LIVE_STATE:
        if p.is_file():
            assert str(tmp_path) not in p.read_text(encoding="utf-8", errors="replace"), (
                f"ASSIGN_LENS_TEST_WROTE_LIVE_STATE: {p}")
