"""L14 reports dedup working when nothing deduped.

THE DEFECT, and it is the mirror image of the one #64 fixed in L11.
`checks.py:1162-1166` counts history rows as dedup evidence when their message
matches any of `%dedup%`, `%skip%` or `%already%`:

    SELECT COUNT(*) FROM history
    WHERE status = 'done'
      AND (message LIKE '%dedup%' OR message LIKE '%skip%'
           OR message LIKE '%already%')

`already on disk` -- written by `runner_transport.py:797` when
`skip_if_exists` fires -- matches `%already%`. So the very same row that made
L11 falsely certify the pipeline also makes L14 falsely certify dedup.

Measured against the deploy host's eight-run sequence: L14 returned
PASS "7 download(s) recorded as dedup-skipped -- the stash-dedup path works"
with zero dedup and zero Stash involved.

A FILE-EXISTENCE SKIP AND A DEDUP SKIP ARE TWO MECHANISMS, NOT ONE.

  skip_if_exists  runner_transport.py:794 -- keys on FILESYSTEM state
                  (`final_path.exists()`), writes history "already on disk".
  dedup preflight runner_integrity.py:148-185 via runner.py:2917-2920 -- keys
                  on DATABASE state, sets the QUEUE row to
                  `status='skipped_duplicate'`.

A message-text predicate cannot tell them apart, and it never could: it is
matching prose written 1400 lines away, which is the same coupling #64 removed
from L11.

THE QUEUE-SIDE EVIDENCE IS SOUND AND IS KEPT. `queue.status = 'skipped_duplicate'`
is written only by the dedup path, so it is a genuine observation of the thing
the check is named for. Removing the history half leaves the check reading the
one signal that actually means dedup.

WHAT THIS CUT DOES NOT FIX, stated so it is not mistaken for closed. L14's named
subject -- STASH dedup specifically -- is structurally unobservable on a seeded
host: the only production writer of a Stash-dedup row is
`runner_integrations.py:79`, gated on `stash_deep.deep_enabled(cfg)` AND
`cfg['stash_dedup_check']`, which defaults False and which the seeder never
sets. So after this cut L14 honestly reports on GENERIC duplicate skipping, and
the Stash-specific claim in its name remains unearned. Renaming touches five
test docstrings and is left out deliberately; the docstring is corrected here.

RED-first: the first two assertions fail on pristine source.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_tests import checks, harness  # noqa: E402


class _Ctx(harness.Context):
    """Real Context, HTTP layer stubbed. Subclassed rather than reimplemented
    so a change to harness.Context's db seam is visible here."""

    def __init__(self, db_path):
        super().__init__("http://ctx.invalid", str(Path(db_path).parent),
                         disruptive=True)
        self.db_path = Path(db_path)
        self.messages = []

    def log(self, msg):
        self.messages.append(str(msg))

    def get(self, path, timeout=10):
        return False, 404, {}, 0.0


def _db(tmp_path, history, queue=(), name="h.db"):
    """history = [(status, filename, message)], queue = [status, ...]"""
    p = tmp_path / name
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "site_id TEXT, status TEXT, filename TEXT, file_size INTEGER, "
               "message TEXT, bytes_fetched INTEGER, ts TEXT)")
    cx.execute("CREATE TABLE queue(site_id TEXT, url TEXT, status TEXT, "
               "PRIMARY KEY(site_id, url))")
    for i, (status, fn, msg) in enumerate(history):
        cx.execute("INSERT INTO history(site_id,status,filename,file_size,"
                   "message,bytes_fetched) VALUES('s1',?,?,?,?,?)",
                   (status, fn, 9421, msg, 0))
    for i, status in enumerate(queue):
        cx.execute("INSERT INTO queue(site_id,url,status) VALUES('s1',?,?)",
                   (f"https://x.invalid/{i}", status))
    cx.commit()
    cx.close()
    return p


# ── the defect ───────────────────────────────────────────────────────────────

def test_a_file_existence_skip_is_not_dedup_evidence(tmp_path):
    """THE DEFECT -- the box's eight-run sequence exactly.

    Seven `already on disk` completions, no dedup anywhere. `%already%` matches
    every one of them.
    """
    db = _db(tmp_path, [("done", "scene_002.mp4", "already on disk")] * 7
                       + [("done", "scene_002.mp4", "")])
    level, msg = checks.l14_stash_dedup_skip(_Ctx(db))
    assert level != harness.PASS, (
        f"L14 returned {level}: {msg!r}. Every one of those rows is a "
        f"skip_if_exists hit -- runner_transport.py:797 writes 'already on "
        f"disk' when the FILE already exists, which is filesystem state, not "
        f"the database-state dedup decision this check is named for."
    )


def test_the_word_skip_in_a_message_is_not_dedup_evidence(tmp_path):
    """The predicate must not be prose.

    A Stash-dedup message happens to contain 'Skipped', but so would any future
    message. Matching text couples the check to a string written elsewhere --
    exactly the coupling removed from L11.
    """
    db = _db(tmp_path, [("done", "a.mp4", "Skipped: user cancelled"),
                        ("done", "b.mp4", "")])
    level, msg = checks.l14_stash_dedup_skip(_Ctx(db))
    assert level != harness.PASS, (
        f"L14 returned {level}: {msg!r} on a message that merely contains "
        f"'Skipped'."
    )


# ── the signal that must survive ─────────────────────────────────────────────

def test_a_real_queue_dedup_skip_still_passes(tmp_path):
    """queue.status='skipped_duplicate' is written only by the dedup path
    (runner.py:2919 via runner_integrity.py:151). It is the genuine signal and
    the check must keep reading it."""
    db = _db(tmp_path, [("done", "a.mp4", "")],
             queue=["skipped_duplicate"])
    level, msg = checks.l14_stash_dedup_skip(_Ctx(db))
    assert level == harness.PASS, (
        f"L14 returned {level}: {msg!r} despite a genuine "
        f"skipped_duplicate queue row."
    )


def test_an_ordinary_queue_row_is_not_dedup_evidence(tmp_path):
    """Closes a mutation that survived: dropping the WHERE clause.

    Counting ANY queue row rather than only skipped_duplicate ones passes every
    other assertion here, because no other fixture pairs a completion with a
    non-dedup queue row. A pending job is the ordinary state of a live queue and
    must never read as a duplicate skip.
    """
    db = _db(tmp_path, [("done", "a.mp4", "")],
             queue=["pending", "running", "done"])
    level, msg = checks.l14_stash_dedup_skip(_Ctx(db))
    assert level == harness.WARN, (
        f"L14 returned {level}: {msg!r}. Those queue rows are pending, running "
        f"and done -- none is a duplicate skip. Only "
        f"status='skipped_duplicate' is written by the dedup path."
    )


def test_no_completions_is_still_not_exercisable(tmp_path):
    """Pre-existing behaviour: dedup cannot be judged with nothing downloaded."""
    level, _ = checks.l14_stash_dedup_skip(_Ctx(_db(tmp_path, [])))
    assert level == harness.WARN


def test_completions_without_dedup_still_warn(tmp_path):
    """Real downloads, no duplicate ever queued -- honest WARN, not a failure."""
    db = _db(tmp_path, [("done", "a.mp4", ""), ("done", "b.mp4", "")])
    level, msg = checks.l14_stash_dedup_skip(_Ctx(db))
    assert level == harness.WARN, f"returned {level}: {msg!r}"


def test_an_unreadable_db_still_fails(tmp_path):
    """Unchanged, and consistent with L22/L26."""
    level, _ = checks.l14_stash_dedup_skip(_Ctx(tmp_path / "absent.db"))
    assert level in (harness.WARN, harness.FAIL)


# ── the message must not claim more than it observed ─────────────────────────

def test_the_verdict_does_not_claim_stash_when_it_saw_a_generic_skip(tmp_path):
    """L14's named subject is Stash dedup, which is unobservable on a seeded
    host -- runner_integrations.py:46-49 gates it on stash_dedup_check, which
    defaults False and which the seeder never sets. The PASS text must not
    assert 'the stash-dedup path works' on evidence that is generic."""
    db = _db(tmp_path, [("done", "a.mp4", "")], queue=["skipped_duplicate"])
    _, msg = checks.l14_stash_dedup_skip(_Ctx(db))
    assert "stash" not in msg.lower(), (
        f"verdict text was {msg!r}. The observation is a queue-level duplicate "
        f"skip; nothing in it distinguishes Stash, and claiming so is the same "
        f"kind of unearned assertion this cut removes."
    )
