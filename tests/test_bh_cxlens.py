"""Exercise the actual driver with isolated transport and retained landing input."""
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH_CXLENS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def drive(tmp_path: Path, rows: str, done: str, applied: str = "", assignments: str = "", landed: str = "") -> tuple[subprocess.CompletedProcess[str], str]:
    root = tmp_path / "root"
    persist = root / "bd-persist"
    (persist / "findings").mkdir(parents=True)
    (persist / "APPLIED-HARNESS-LOG.md").write_text(applied)
    (persist / "findings/ASSIGNMENTS.tsv").write_text(assignments)
    (persist / "LANDED-T1.md").write_text(landed)
    for line in rows.splitlines():
        fields = line.split("\t")
        if len(fields) == 3:
            review = root / fields[1] / ".review"
            review.mkdir(parents=True, exist_ok=True)
            (review / "DONE.md").write_text(done)
    work = tmp_path / "work"
    work.mkdir()
    (work / "worklist.cache").write_text("old-cache\n")
    seats = tmp_path / "seats"
    seats.mkdir()
    source = tmp_path / "source"
    source.write_text("#!/bin/sh\ncat <<'ROWS'\n" + rows + "ROWS\n")
    source.chmod(0o755)
    env = dict(os.environ, BD_LENS_WORK=str(work), BD_LENS_WTROOT=str(seats), BD_LENS_WORKLIST_BIN=str(source), BD_LENS_STATE_ROOT=str(root), BD_LENS_LOADMAX="99999")
    result = subprocess.run(["bash", CANDIDATE, "driver"], env=env, text=True, capture_output=True, check=False)
    return result, (work / "worklist.cache").read_text()


def test_live_cache_landed_rb3(tmp_path: Path) -> None:
    fixture = Path(CANDIDATE).parent / "fixtures"
    rows = (fixture / "worklist.cache").read_text()
    assert "bh-rb-3-bd-agy-fixer-1-local\t" in rows
    applied = (fixture / "APPLIED-HARNESS-LOG.md").read_text()
    assert " BH-RB-3 findings=" in applied
    result, cache = drive(tmp_path, rows, (fixture / "RB3-DONE.md").read_text(), applied)
    assert result.returncode == 0, result.stderr
    assert "bh-rb-3-bd-agy-fixer-1-local\t" not in cache, "CXLENS_LANDED_RB3_REOFFERED"
    assert "NOBRIEF" not in cache, "CXLENS_NOBRIEF_REOFFERED"


@pytest.mark.parametrize("source", ["applied", "landed", "assignments"])
def test_terminal_evidence(tmp_path: Path, source: str) -> None:
    args = {"applied": "2026-09-28T00:00:00Z BH-EXACT findings=X\n", "landed": "VERDICT: LANDED\nLANDS: BH-EXACT -- fixed\n", "assignments": "id\tseverity\tpath\tseat\tdispatch_file\tassigned_at\tstate\nBH-EXACT\tMED\tx\ty\tz\t2026\tREFUTED\n"}
    result, cache = drive(tmp_path, "exact\tbd-review-wt/rowexact\tTODO\n", "CUT: BH-EXACT HARNESS\n", **{source: args[source]})
    assert result.returncode == 0, result.stderr
    assert not cache, "CXLENS_TERMINAL_REOFFERED"


def test_name_alike_and_latest_assignment_remain_live(tmp_path: Path) -> None:
    rows = "live\tbd-review-wt/rowlive\tTODO\nmissing\tbd-review-wt/rowmissing\tNOBRIEF\n"
    assignments = "BH-LIVE\tMED\tx\ty\tz\t1\tREFUTED\nBH-LIVE\tMED\tx\ty\tz\t2\tASSIGNED\n"
    result, cache = drive(tmp_path, rows, "CUT: BH-LIVE HARNESS\n", "2026-09-28T00:00:00Z BH-LIVE-OTHER findings=X\n", assignments)
    assert result.returncode == 0, result.stderr
    assert cache == rows.splitlines(keepends=True)[0], "CXLENS_LIVE_OR_EXACT_MATCH_BROKEN"


def test_malformed_refresh_preserves_cache_and_refuses(tmp_path: Path) -> None:
    result, cache = drive(tmp_path, "malformed\n", "")
    assert result.returncode == 1, "CXLENS_MALFORMED_REFRESH_ACCEPTED"
    assert "CACHE-REFRESH-FAILED" in result.stdout
    assert cache == "old-cache\n"


def test_contract_reference_is_resolved(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("Read /home/mboyle/bd-persist/roles-prompts/LENS_CORRECTNESS.md now.\n")
    contract = tmp_path / "review-correctness.prompt"
    contract.write_text("contract\n")
    env = dict(os.environ, BD_LENS_BRIEF=str(brief), BD_LENS_CONTRACT=str(contract))
    result = subprocess.run(["bash", CANDIDATE, "brief"], env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 0, "CXLENS_CONTRACT_RENDER_UNAVAILABLE"
    assert result.stdout == f"Read {contract} now.\n"
    contract.unlink()
    result = subprocess.run(["bash", CANDIDATE, "brief"], env=env, text=True, capture_output=True, check=False)
    assert result.returncode == 3
    assert "contract unreadable" in result.stderr


def test_prose_words_are_not_cut_identities(tmp_path: Path) -> None:
    """Lens fix (O1481): a LANDS line's prose ("finding", "harness", "fix") must not mark a live TODO terminal just
    because the TODO's DONE.md has a FINDING: line; only id-shaped tokens (a letter plus a hyphen or digit) match."""
    rows = "bh-new-999\tbd-review-wt/rowbh-new-999\tTODO\n"
    done = "CUT: BH-NEW-999 HARNESS\nFINDING: BH-bd-x-999; harness fix, read the finding\n"
    landed = "VERDICT: LANDED\nLANDS: BH-OLD-1 harness finding 004/005 fix -- landed\n"
    result, cache = drive(tmp_path, rows, done, landed=landed)
    assert result.returncode == 0, result.stderr
    assert cache == rows, "CXLENS_PROSE_WORD_DROPPED_LIVE_TODO"


def test_finding_id_in_lands_still_terminal(tmp_path: Path) -> None:
    """Positive control for the tightened match: the same TODO is terminal once LANDS names its finding id."""
    rows = "bh-new-999\tbd-review-wt/rowbh-new-999\tTODO\n"
    done = "CUT: BH-NEW-999 HARNESS\nFINDING: BH-bd-x-999; harness fix\n"
    landed = "VERDICT: LANDED\nLANDS: BH-bd-x-999 harness finding -- landed\n"
    result, cache = drive(tmp_path, rows, done, landed=landed)
    assert result.returncode == 0, result.stderr
    assert not cache, "CXLENS_FINDING_ID_NOT_TERMINAL"
