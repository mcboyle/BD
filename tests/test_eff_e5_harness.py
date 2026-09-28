"""Tests for EFF-E5 harness cuts (nurse-1-001, nurse-1-002).

Covers:
1. bd-idle-reaper.sh: Unowned ephemeral test/synthetic sessions idle >60m are reported
   as zombie candidates without killing them; seats holding claims, worktrees, or
   dispatched rows are never reported or killed (Rule 22/24 / DISPATCH-EFF-E5 LIMITS).
2. bd-dispatch-check.sh: Worker seats idle >60m without active cut assignments are
   detected and flagged as STARVED for re-rolling/pausing.
nurse-1-004 (bd-turn-watch.sh early warn) was dropped at G5 (lens G4 R1/R2: fixed turn 17, suppression keyed on
files the fleet never writes); it is a PM design row, not part of this cut.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

# Opt-in: the harness is deployed from bd-persist, not this repo; no silent fallback to a host path.
CANDIDATE_DIR = os.environ.get("BD_EFF_E5_CANDIDATE_DIR", "")
pytestmark = pytest.mark.skipif(not CANDIDATE_DIR, reason="candidate opt-in required")


def get_script(name: str) -> Path:
    candidate_dir = Path(CANDIDATE_DIR)
    script_path = candidate_dir / name
    assert script_path.is_file(), f"Target script does not exist: {script_path}"
    return script_path


def test_001_reaper_reports_zombie_synthetic_and_test_sessions() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-reap-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        empty_wt = root / "empty_wt"
        empty_wt.mkdir()

        # Mock tmux that returns synthetic and test sessions idle for 7200s (2h)
        tmux = bin_dir / "tmux"
        tmux.write_text(
            """#!/bin/sh
now=$(date +%s)
old=$(( now - 7200 ))
case "$*" in
  *"list-windows"*)
    printf "synthetic-flash %s\\nkimi-test-1 %s\\ncockpit %s\\ntest-codex %s\\n" \\
      "$old" "$old" "$old" "$old"
    exit 0
    ;;
  *"capture-pane"*)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 0\n")
        rs_sh.chmod(0o755)

        log_file = root / "reaper.log"
        zombie_list = root / "zombie.list"

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_WT_ROOTS": str(empty_wt),
                "BD_REAPER_LOG": str(log_file),
                "BD_REAPER_ZOMBIE_LIST": str(zombie_list),
                "BD_ZOMBIE_IDLE_MIN": "60",
                "BD_IDLE_MIN": "20",
            }
        )

        res = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, check=False
        )
        assert res.returncode == 0, f"Reaper exited with {res.returncode}: {res.stderr}"

        # In candidate: synthetic-flash, kimi-test-1, cockpit, test-codex are reported as zombies
        # In orig: line 59 skipped them completely because they don't match ^(BD-|bd-)
        assert "decision=report-zombie" in res.stdout, (
            f"Expected report-zombie decisions in output, got: {res.stdout}"
        )
        assert "synthetic-flash" in res.stdout
        assert "cockpit" in res.stdout
        assert zombie_list.is_file() and "synthetic-flash" in zombie_list.read_text(
            encoding="utf-8"
        )


def test_002_reaper_never_kills_or_reports_seat_with_worktree_or_dispatch() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-protect-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        wt_dir = root / "worktrees"
        wt_dir.mkdir()
        (wt_dir / "cut-synthetic-flash-row123").mkdir()

        now = 1727500000
        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-windows"*)
    printf "synthetic-flash %s\\nkimi-test-1 %s\\n" "{now - 7200}" "{now - 7200}"
    exit 0
    ;;
  *"capture-pane"*)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 0\n")
        rs_sh.chmod(0o755)

        ledger = root / "DISPATCH-LEDGER.tsv"
        ledger.write_text(
            "2026-09-28T06:00:00Z\tkimi-test-1\trow-999\tdispatched\t/b.md\n"
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_WT_ROOTS": str(wt_dir),
                "BD_REAPER_LEDGER": str(ledger),
                "BD_REAPER_LOG": str(root / "reaper.log"),
                "BD_ZOMBIE_IDLE_MIN": "60",
                "BD_IDLE_MIN": "20",
            }
        )

        res = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, check=False
        )
        assert res.returncode == 0

        # synthetic-flash has a worktree -> skip:has-worktree
        # kimi-test-1 has an active dispatched row -> skip:dispatched-row
        # Neither should be reported as zombie or parked
        assert "seat=synthetic-flash" in res.stdout
        assert "skip:has-worktree" in res.stdout
        assert "seat=kimi-test-1" in res.stdout
        assert "skip:dispatched-row" in res.stdout


def test_003_dispatch_check_flags_starved_worker_idle_over_60m() -> None:
    script = get_script("bd-dispatch-check.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-starved-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        now = 1727500000

        # Mock tmux with worker-A1-A idle for 4000s (>3600s)
        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-sessions"*)
    echo "bd-worker-A1-A"
    exit 0
    ;;
  *"has-session"*)
    exit 0
    ;;
  *"list-windows"*)
    echo "{now - 4000}"
    exit 0
    ;;
  *"capture-pane"*)
    printf "\\342\\235\\257\\n"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        say = bin_dir / "say.sh"
        say.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "' + str(root / "said") + '"\nexit 0\n'
        )
        say.chmod(0o755)

        ledger = root / "DISPATCH-LEDGER.tsv"
        # Only an older done cut or no active cuts
        ledger.write_text(
            "2026-09-28T02:00:00Z\tbd-worker-A1-A\trow-100\tdone\t/b.md\n"
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_DISPATCH_P": str(root),
                "BD_DISPATCH_LEDGER": str(ledger),
                "BD_DISPATCH_SAY": str(say),
                "BD_DISPATCH_PM": "bd-pm-test",
                "BD_DISPATCH_FIXER": "bd-fixer-test",
                "BD_STARVED_WORKER_SEC": "3600",
            }
        )

        res = subprocess.run(
            ["bash", str(script)], env=env, capture_output=True, text=True, check=False
        )
        assert res.returncode == 0

        # In candidate: starved worker detected and printed in summary
        # In orig: starved check does not exist
        assert "STARVED bd-worker-A1-A" in res.stdout, (
            f"Expected STARVED in output, got: {res.stdout}"
        )
        starved_tsv = root / "logs" / "starved-workers.tsv"
        assert starved_tsv.is_file() and "bd-worker-A1-A" in starved_tsv.read_text(
            encoding="utf-8"
        )


def test_005_reaper_exits_2_when_ledger_unreadable_and_never_kills() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-r1-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        wt_dir = root / "wt"
        wt_dir.mkdir()
        now = 1727500000

        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-windows"*)
    printf "bd-cx-nurse1 %s\\n" "{now - 7200}"
    exit 0
    ;;
  *"capture-pane"*)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 0\n")
        rs_sh.chmod(0o755)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_LEDGER": str(root / "nonexistent-ledger.tsv"),
                "BD_REAPER_WT_ROOTS": str(wt_dir),
                "BD_REAPER_LOG": str(root / "reaper.log"),
                "BD_IDLE_MIN": "0",
            }
        )

        res = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        # Rule 6 / REFUTE R1: unreadable ledger must exit 2 (COULD NOT LOOK), never park or kill
        assert res.returncode == 2
        assert "LEDGER-UNKNOWN" in res.stdout


def test_006_reaper_exits_2_when_all_wt_roots_absent_and_never_kills() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-r1-wt-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        now = 1727500000

        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-windows"*)
    printf "bd-cx-nurse1 %s\\n" "{now - 7200}"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 0\n")
        rs_sh.chmod(0o755)

        ledger = root / "DISPATCH-LEDGER.tsv"
        ledger.write_text("")

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_LEDGER": str(ledger),
                "BD_REAPER_WT_ROOTS": str(root / "nonexistent-wt-root"),
                "BD_REAPER_LOG": str(root / "reaper.log"),
                "BD_IDLE_MIN": "0",
            }
        )

        res = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        # Shape R1' case (i): all WT roots absent must exit 2 WT-ROOT-UNKNOWN (kills M2)
        assert res.returncode == 2
        assert "WT-ROOT-UNKNOWN" in res.stdout


def test_007_reaper_exits_2_when_wt_root_unreadable_and_never_kills() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-r1-unr-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        unreadable_wt = root / "unreadable-wt"
        unreadable_wt.mkdir()
        unreadable_wt.chmod(0o000)
        now = 1727500000

        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-windows"*)
    printf "bd-cx-nurse1 %s\\n" "{now - 7200}"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 0\n")
        rs_sh.chmod(0o755)

        ledger = root / "DISPATCH-LEDGER.tsv"
        ledger.write_text("")

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_LEDGER": str(ledger),
                "BD_REAPER_WT_ROOTS": str(unreadable_wt),
                "BD_REAPER_LOG": str(root / "reaper.log"),
                "BD_IDLE_MIN": "0",
            }
        )

        try:
            res = subprocess.run(
                ["bash", str(script)],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            # Shape R1' case (ii): unreadable WT root must exit 2 WT-ROOT-UNKNOWN (kills M3)
            assert res.returncode == 2
            assert "WT-ROOT-UNKNOWN" in res.stdout
        finally:
            unreadable_wt.chmod(0o755)


def test_008_reaper_skips_live_seat_names_never_parked_or_killed() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-live-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        wt_dir = root / "wt"
        wt_dir.mkdir()
        now = 1727500000

        killed_log = root / "killed.log"
        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-windows"*)
    printf "bd-cx-worker1 %s\\nbd-cx-nurse1 %s\\nbd-worker-B2-B %s\\ncodex-lens1 %s\\n" \\
      "{now - 7200}" "{now - 7200}" "{now - 7200}" "{now - 7200}"
    exit 0
    ;;
  *"capture-pane"*)
    exit 0
    ;;
  *"kill-session"*)
    echo "$*" >> "{killed_log}"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 0\n")
        rs_sh.chmod(0o755)

        ledger = root / "DISPATCH-LEDGER.tsv"
        ledger.write_text("")

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_LEDGER": str(ledger),
                "BD_REAPER_WT_ROOTS": str(wt_dir),
                "BD_REAPER_LOG": str(root / "reaper.log"),
                "BD_IDLE_MIN": "0",
            }
        )

        res = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        # Codex R1: live-seat name classes (bd-*, codex-*, *-A/*-B) must be protected, never killed
        assert not killed_log.exists(), (
            f"Live seats were killed: {killed_log.read_text() if killed_log.exists() else ''}"
        )
        assert (
            "seat=bd-cx-worker1" in res.stdout
            and "decision=skip:protected" in res.stdout
        )
        assert (
            "seat=bd-cx-nurse1" in res.stdout
            and "decision=skip:protected" in res.stdout
        )
        assert (
            "seat=bd-worker-B2-B" in res.stdout
            and "decision=skip:protected" in res.stdout
        )
        assert (
            "seat=codex-lens1" in res.stdout and "decision=skip:protected" in res.stdout
        )


def test_009_reaper_skips_when_role_state_fails_never_reports_zombie() -> None:
    script = get_script("bd-idle-reaper.sh")
    with tempfile.TemporaryDirectory(prefix="eff-e5-r2-") as td:
        root = Path(td)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        wt_dir = root / "wt"
        wt_dir.mkdir()
        now = 1727500000

        tmux = bin_dir / "tmux"
        tmux.write_text(
            f"""#!/bin/sh
case "$*" in
  *"list-windows"*)
    printf "synthetic-r127c %s\\n" "{now - 7200}"
    exit 0
    ;;
  *"capture-pane"*)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        tmux.chmod(0o755)

        claim_sh = bin_dir / "claim.sh"
        claim_sh.write_text("#!/bin/sh\nexit 0\n")
        claim_sh.chmod(0o755)

        # Role state command fails with rc 3
        rs_sh = bin_dir / "rs.sh"
        rs_sh.write_text("#!/bin/sh\nexit 3\n")
        rs_sh.chmod(0o755)

        ledger = root / "DISPATCH-LEDGER.tsv"
        ledger.write_text("")
        zombie_list = root / "zombie.list"

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "BD_REAPER_TMUX": str(tmux),
                "BD_REAPER_CLAIM": str(claim_sh),
                "BD_REAPER_ROLE_STATE": str(rs_sh),
                "BD_REAPER_LEDGER": str(ledger),
                "BD_REAPER_WT_ROOTS": str(wt_dir),
                "BD_REAPER_LOG": str(root / "reaper.log"),
                "BD_REAPER_ZOMBIE_LIST": str(zombie_list),
                "BD_ZOMBIE_IDLE_MIN": "0",
                "BD_IDLE_MIN": "0",
            }
        )

        res = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert res.returncode == 0
        # Codex R2: failed role-state command must skip:role-state-unknown, NOT label synthetic as unowned zombie
        assert "decision=skip:role-state-unknown" in res.stdout
        assert (
            not zombie_list.exists() or "synthetic-r127c" not in zombie_list.read_text()
        )
