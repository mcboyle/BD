"""O1480 item 2: bd-limit-watch reported the codex pool UNKNOWN ("0 of 17 roster host(s)
answered (17 unreachable)") while codex seats were running and the local rollouts carried a
fresh rate_limits record.

Two defects, both measured on test5 2026-09-28:
  1. bd-codex-usage.sh --fleet refused outright when its roster file was unreadable (its
     default, bd-persist/hosts, does not exist), throwing away the local rollout record.
  2. decide_codex's ssh fallback only read ``cx-*`` panes and counted a host that answered
     with no such pane as "unreachable"; codex sessions are now ``bd-cx-*`` / ``codex-*``.

The candidate directory (harness-work/FIX/limitwatch-codex/) holds both scripts; the test
runs them against hermetic fixtures (rollout dir, roster, fake ssh/tmux on PATH).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_LIMITWATCH_CODEX_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _script(name: str) -> Path:
    path = Path(CANDIDATE) / name
    assert path.is_file(), f"candidate script missing: {path}"
    assert os.access(path, os.X_OK), f"candidate script not executable: {path}"
    return path


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _rollouts(root: Path, pct: float | None) -> Path:
    sessions = root / "sessions"
    day = sessions / "2026" / "09" / "28"
    day.mkdir(parents=True)
    if pct is not None:
        rec = {"payload": {"rate_limits": {"limit_id": "codex", "primary": {
            "used_percent": pct, "window_minutes": 10080, "resets_at": 1791050422}}}}
        (day / "rollout-fixture.jsonl").write_text(json.dumps(rec) + "\n")
    return sessions


def _fleet(tmp_path: Path, sessions: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, BD_CODEX_SESSIONS=str(sessions),
               BD_CODEX_HOSTS=str(tmp_path / "no-such-roster"))
    return subprocess.run(["bash", str(_script("bd-codex-usage.sh")), "--fleet"],
                          capture_output=True, text=True, env=env, timeout=60, check=False)


def test_fleet_uses_local_rollouts_when_roster_unreadable(tmp_path: Path) -> None:
    sessions = _rollouts(tmp_path, 18.0)
    assert any(sessions.rglob("*.jsonl")), "fixture carries no rollout"
    r = _fleet(tmp_path, sessions)
    assert r.returncode == 0, r.stdout + r.stderr
    fields = r.stdout.split()
    assert fields[0] == "18.0" and fields[2] == "1791050422" and fields[3] == "10080", r.stdout
    assert "local rollouts only" in r.stderr


def test_fleet_without_any_record_is_still_could_not_look(tmp_path: Path) -> None:
    r = _fleet(tmp_path, _rollouts(tmp_path, None))
    assert r.returncode == 2
    assert r.stdout.strip() == "- - - -"


def _watch(tmp_path: Path, **extra: str) -> str:
    """Run the candidate limit-watch hermetically; return the codex row of the state table."""
    state = tmp_path / "state.tsv"
    panes = tmp_path / "panes"
    panes.mkdir(exist_ok=True)
    env = dict(os.environ,
               BD_LIMIT_NOW="2026-09-28T12:00:00Z", BD_LIMIT_STATE=str(state),
               BD_LIMIT_CACHE_A=str(tmp_path / "a.json"), BD_LIMIT_CACHE_B=str(tmp_path / "b.json"),
               BD_LIMIT_PANES=str(panes), BD_CODEX_USAGE=str(_script("bd-codex-usage.sh")))
    env.update(extra)
    subprocess.run(["bash", str(_script("bd-limit-watch.sh"))], capture_output=True, text=True,
                   env=env, timeout=120, check=False)
    rows = [ln for ln in state.read_text().splitlines() if ln.split("\t")[0] == "codex"]
    assert rows, f"no codex row in {state}"
    return rows[-1]


def test_watch_reports_ok_from_local_rollouts(tmp_path: Path) -> None:
    row = _watch(tmp_path, BD_CODEX_SESSIONS=str(_rollouts(tmp_path, 18.0)),
                 BD_LIMIT_HOSTS=str(tmp_path / "no-such-roster"))
    cols = row.split("\t")
    assert cols[1] == "OK", row
    assert "primary 18.0%" in cols[3], row


def test_watch_without_record_or_roster_is_unknown(tmp_path: Path) -> None:
    row = _watch(tmp_path, BD_CODEX_SESSIONS=str(_rollouts(tmp_path, None)),
                 BD_LIMIT_HOSTS=str(tmp_path / "no-such-roster"))
    assert row.split("\t")[1] == "UNKNOWN", row


def _fake_fleet(tmp_path: Path, sessions: str, pane: str) -> dict[str, str]:
    """Roster of two hosts: 10.9.9.1 refuses ssh, 10.9.9.2 answers with fake tmux panes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "ssh", 'for a; do :; done; case "$*" in *10.9.9.1*) exit 255;; esac\n'
                          'bash -c "$a"\n')
    _exe(bin_dir / "tmux", f'case "$1" in list-sessions) printf "{sessions}";; '
                           f'capture-pane) printf "{pane}\\n";; esac\n')
    usage = _exe(tmp_path / "usage-none.sh", 'echo "- - - -"; exit 2\n')
    roster = tmp_path / "hosts"
    roster.write_text("10.9.9.1 dead\n10.9.9.2 alive\n")
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "BD_LIMIT_HOSTS": str(roster),
            "BD_CODEX_USAGE": str(usage)}


def test_roster_reads_bd_cx_panes_and_counts_unreachable_apart(tmp_path: Path) -> None:
    row = _watch(tmp_path, **_fake_fleet(tmp_path, "bd-cx-worker-1\\ncodex-r127c\\n", "working"))
    cols = row.split("\t")
    assert cols[1] == "OK", row
    assert "1 host(s) with codex panes" in cols[3] and "1 unreachable" in cols[3], row


def test_roster_banner_on_bd_cx_pane_is_limited(tmp_path: Path) -> None:
    row = _watch(tmp_path, **_fake_fleet(tmp_path, "bd-cx-worker-1\\n",
                                         "You've hit your usage limit"))
    assert row.split("\t")[1] == "LIMITED", row


def test_roster_with_no_codex_pane_stays_unknown(tmp_path: Path) -> None:
    row = _watch(tmp_path, **_fake_fleet(tmp_path, "bd-worker-B4-B\\n", "working"))
    cols = row.split("\t")
    assert cols[1] == "UNKNOWN", row
    assert "1 answered with none, 1 unreachable" in cols[3], row


def test_watch_stale_local_record_is_unknown_not_ok(tmp_path: Path) -> None:
    """FIXED-BY-LENS shape-B2-B (O1481): the local-only fallback makes the number path fire when the
    roster is unreadable, so the STALE gate is what keeps an old record from reading OK (rule 6)."""
    stale = _exe(tmp_path / "usage-stale.sh", 'echo "18.0 999999 1791050422 10080"\n')
    row = _watch(tmp_path, BD_CODEX_USAGE=str(stale), BD_LIMIT_HOSTS=str(tmp_path / "no-such-roster"))
    cols = row.split("\t")
    assert cols[1] == "UNKNOWN" and "STALE" in cols[3], row
    fresh = _exe(tmp_path / "usage-fresh.sh", 'echo "18.0 5 1791050422 10080"\n')
    assert _watch(tmp_path, BD_CODEX_USAGE=str(fresh), BD_LIMIT_HOSTS=str(tmp_path / "no-such-roster")).split("\t")[1] == "OK"
