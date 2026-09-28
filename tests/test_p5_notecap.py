"""P5 NOTECAP (O1487): bd-say warns when a DISPATCH-*.md it carries is over the cap, never refuses.

REFUTE VERDICT-correctness-bd-worker-A2-A.md: notes are written by Bash heredocs in every pool, so a Write|Edit hook
metered none of them; every pool delivers the note by sending its path through bd-say, so the meter lives there.
BD_P5_NOTECAP_CANDIDATE = the bd-say script under test (opt-in). Hermetic: fake tmux on PATH, ledger, inbox and warn
log in tmp_path; nothing is delivered anywhere. The replay fixture is a real note and the real text that sent it.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

SAY = os.environ.get("BD_P5_NOTECAP_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    not SAY, reason="candidate opt-in required (BD_P5_NOTECAP_CANDIDATE)"
)
FIXTURE = Path(__file__).parent / "fixtures" / "p5_notecap"
REAL_NOTE = "DISPATCH-BUGHUNT-FIX-WAVE1-bd-pm-A.md"  # 2847 B; say.log 2026-09-28T04:51:38Z bd-pm-A -> bd-fixer-A1-A
BEGIN, END = "# P5-NOTECAP BEGIN", "# P5-NOTECAP END"


def block(tmp_path: Path) -> Path:
    text = Path(SAY).read_text()
    m = re.search(rf"^{re.escape(BEGIN)}.*?^{re.escape(END)}$", text, re.S | re.M)
    assert m, "P5-NOTECAP block missing from the say script"
    p = tmp_path / "blk.sh"
    p.write_text(m.group(0) + "\n")
    return p


def run_block(tmp_path: Path, text: str, **env):
    log = tmp_path / "cap.log"
    e = {k: v for k, v in os.environ.items() if k != "BD_DISPATCH_NOTE_CAP"}
    e.update(T=text, S="bd-test-Z", BD_DISPATCH_NOTE_CAP_LOG=str(log), LC_ALL="C", **env)
    r = subprocess.run(
        ["bash", "-c", 'set -u; source "$0"; echo "SENTINEL rc=$?"', str(block(tmp_path))],
        capture_output=True,
        text=True,
        env=e,
        timeout=30,
        check=False,
    )
    return r, (log.read_text() if log.exists() else "")


def note(tmp_path: Path, name: str, size: int) -> Path:
    p = tmp_path / name
    p.write_text("# DISPATCH\n" + "x" * (size - 11))
    assert p.stat().st_size == size
    return p


def test_block_runs_before_the_first_refusal_and_after_the_text_is_read():
    lines = Path(SAY).read_text().splitlines()
    b = next(i for i, x in enumerate(lines) if x.startswith(BEGIN))
    t = next(i for i, x in enumerate(lines) if re.match(r"^S=\$1; shift;.*T=", x))
    d = next(i for i, x in enumerate(lines) if re.search(r"\bdie [0-9]", x) and not x.lstrip().startswith("#"))
    assert t < b < d


def test_oversized_note_warns_on_stderr_logs_and_continues(tmp_path):
    p = note(tmp_path, "DISPATCH-P9-big-bd-worker-Z.md", 4000)
    r, log = run_block(tmp_path, f"[from bd-dispatch-Z] {p}")
    assert r.stdout.strip() == "SENTINEL rc=0", r.stdout
    assert "DISPATCH-NOTE-CAP" in r.stderr and "4000 B > 2048 B" in r.stderr, r.stderr
    assert "DISPATCH-NOTE-CAP" in log and "bd-test-Z" in log


def test_cap_is_overridable(tmp_path):
    p = note(tmp_path, "DISPATCH-P9-mid-bd-worker-Z.md", 1500)
    r, _ = run_block(tmp_path, str(p), BD_DISPATCH_NOTE_CAP="1024")
    assert "1500 B > 1024 B" in r.stderr, r.stderr


def test_every_note_path_in_one_message_is_metered(tmp_path):
    a = note(tmp_path, "DISPATCH-A-bd-worker-Z.md", 3000)
    b = note(tmp_path, "DISPATCH-B-bd-worker-Z.md", 3100)
    r, _ = run_block(tmp_path, f"two orders: {a} and ({b})")
    assert "3000 B" in r.stderr and "3100 B" in r.stderr, r.stderr


def test_unmeasurable_note_is_named_not_passed_silently(tmp_path):
    d = tmp_path / "DISPATCH-P9-dir-bd-worker-Z.md"
    d.mkdir()
    r, log = run_block(tmp_path, str(d))
    assert r.stdout.strip() == "SENTINEL rc=0"
    assert "could not be measured" in r.stderr and "could not be measured" in log, r.stderr


@pytest.mark.parametrize("size", [900, 2048])
def test_negative_control_note_within_cap_is_silent(tmp_path, size):
    p = note(tmp_path, "DISPATCH-P9-ok-bd-worker-Z.md", size)
    r, log = run_block(tmp_path, str(p))
    assert r.stdout.strip() == "SENTINEL rc=0" and r.stderr == "" and log == ""


def test_negative_control_big_non_dispatch_file_and_missing_path_are_silent(tmp_path):
    p = note(tmp_path, "NOTE-big-bd-worker-Z.md", 9000)
    r, log = run_block(tmp_path, f"{p} {tmp_path}/DISPATCH-typo-bd-worker-Z.md")
    assert r.stderr == "" and log == ""


# --- replay: the whole candidate script, a real note and the real text that sent it ------------------------------------

FAKE_TMUX = """#!/bin/bash
case "$1" in
  ls) printf 'bd-worker-Z1-Z\\nbd-worker-Z2-Z\\n';;
  display) printf 'bd-worker-Z1-Z\\n';;
  list-panes) :;;
  has-session) exit 1;;
  capture-pane) printf 'Edit file?\\n  1. Yes\\n  2. No\\nDo you want to proceed? (y/n)\\n';;
  *) exit 0;;
esac
"""


def send(tmp_path: Path, text: str):
    (tmp_path / "bin").mkdir(exist_ok=True)
    (tmp_path / "inbox").mkdir(exist_ok=True)
    tmux = tmp_path / "bin" / "tmux"
    tmux.write_text(FAKE_TMUX)
    tmux.chmod(0o755)
    gencheck = tmp_path / "gencheck.sh"
    gencheck.write_text("exit 0\n")
    log = tmp_path / "cap.log"
    log.unlink(missing_ok=True)
    e = {k: v for k, v in os.environ.items() if k != "BD_DISPATCH_NOTE_CAP"}
    e.update(
        PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        TMUX_PANE="%9",
        BD_SAY_LOG=str(tmp_path / "say.log"),
        BD_SAY_INBOX_ROOT=str(tmp_path / "inbox"),
        BD_SAY_GENWARN_LOG=str(tmp_path / "gen.log"),
        BD_SAY_GENCHECK=str(gencheck),
        BD_SAY_NOSPOOL="1",
        BD_DISPATCH_NOTE_CAP_LOG=str(log),
    )
    r = subprocess.run(
        ["bash", SAY, "bd-worker-Z2-Z", text], capture_output=True, text=True, env=e, timeout=60, check=False
    )
    return r, (log.read_text() if log.exists() else "")


def test_replay_real_dispatcher_send_warns_and_delivery_is_unchanged(tmp_path):
    real = tmp_path / REAL_NOTE
    shutil.copyfile(FIXTURE / REAL_NOTE, real)
    small = note(tmp_path, "DISPATCH-small-bd-worker-Z.md", 600)
    big_r, big_log = send(tmp_path, f"[from bd-pm-A] {real}")
    small_r, small_log = send(tmp_path, f"[from bd-pm-A] {small}")
    assert "DISPATCH-NOTE-CAP" in big_r.stderr and "2847 B > 2048 B" in big_r.stderr, big_r.stderr
    assert "2847 B" in big_log
    assert "DISPATCH-NOTE-CAP" not in small_r.stderr and small_log == ""
    assert big_r.returncode == small_r.returncode, (big_r.returncode, small_r.returncode, big_r.stderr)
