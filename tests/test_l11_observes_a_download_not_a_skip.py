"""L11 certifies the pipeline while the pipeline was explicitly cancelled.

TWO DEFECTS, both measured on the deploy host.

DEFECT 1 -- THE VERDICT PASSES ON A SKIP. `runner_transport.py:790` defaults
`skip_if_exists` True, so when the destination exists the runner reports the job
`done`, calls `dl.cancel()` and returns without fetching. L11 read
`/api/sites/<sid>/queue/counts` and PASSed on `done > 0`, which that row
satisfies. Eight consecutive seeded runs on test4, seven of them skips, and L11
reported "the end-to-end pipeline has worked" for every one. With the stale file
moved aside the pipeline was confirmed genuinely working -- the code was fine
and only the observation was blind.

`already on disk` is not the only no-fetch path: a Stash dedup hit, an HTTP 416
resume, a yt-dlp "has already been downloaded" and a click with no download dir
all write `done` too, and two of those record an EMPTY message. Matching on
message text is a denylist that was already five long. #63 added
`history.bytes_fetched` so the writer states the fact instead; this check now
reads it.

DEFECT 2 -- THE SUBJECT IS CHOSEN NONDETERMINISTICALLY. `_pipeline_setup` took
`seeded[0]` from every site whose name CONTAINS the marker. The seeder creates
two -- `bdseed fixture site` (queue) and `bdseed fixture login` -- so the choice
came from `/api/status` iteration order. Measured across five runs: the queue
site 4/5, the login site 1/5, and the login site correctly has no completed
download, so that run produced a false WARN.

WHY THE UNREADABLE-DB CASE IS FAIL AND THE SKIP CASE IS ONLY WARN.
`harness.py:282` keys the run's exit code on `counts[FAIL]` alone, so WARN does
not gate a capture. That argues for FAIL on the skip case -- except nothing in
the tree can CLEAR it: teardown does not remove the downloaded file
(`live_seed.py` RESIDUE_NOTE), `queue_site_config` never sets
`skip_if_exists`, and the per-run nonce is in the URL query while the filename
comes from the path. Capture 1 would fetch and pass; capture 2 would find the
file and fail forever, with no check-side change able to alter it. The
escalation belongs with the seeder fix that makes every run fetch for real.
The unreadable-DB case has no such problem and introduces no new red: L22
(`checks.py:18-21`) and L26 (`:52-55`) already FAIL on exactly those conditions
and are both in LIVE_IDS.

THIS FILE REPLACES AN EARLIER VERSION THAT COULD NOT FAIL. Seven of eight
mutations survived it. Each hole is closed below and named where it was:
the history fixture wrote a single site_id so the `site_id` clause was untested;
every fixture row was `done` so the status filter was untested; the selection
ids defeated ascending sort only; the determinism test was satisfied by a
memoisation cache; assertions read `!= PASS` so `NA` slipped through; and the
Context double reimplemented `ro_db` instead of delegating, insulating the
tests from the real harness entirely.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_tests import checks, harness  # noqa: E402


# ── the double DELEGATES; it does not reimplement ────────────────────────────

class _Ctx(harness.Context):
    """A real harness.Context with only the HTTP layer stubbed.

    Subclassing rather than reimplementing: the earlier version defined its own
    ro_db()/db_path and a mutation that broke harness.Context's real db_path
    still passed 10/10, because the tests could not see the class they were
    meant to exercise. Everything except get() is the production object.
    """

    def __init__(self, sites, db_path, counts=None):
        super().__init__("http://ctx.invalid", str(Path(db_path).parent),
                         disruptive=True)
        self.db_path = Path(db_path)
        self._sites = sites
        self._counts = counts or {}
        self.messages = []

    def log(self, msg):
        self.messages.append(str(msg))

    def get(self, path, timeout=10):
        if path == "/api/status":
            return True, 200, {"sites": self._sites}, 0.0
        if "/queue/counts" in path:
            sid = path.split("/api/sites/")[1].split("/")[0]
            return True, 200, {"counts": self._counts.get(sid, {})}, 0.0
        return False, 404, {}, 0.0


def _history(tmp_path, rows, name="h.db"):
    """rows = [(site_id, status, bytes_fetched), ...]

    Deliberately heterogeneous: callers pass more than one site_id and more than
    one status, so a fix that drops the `site_id = ?` clause or the
    `status = 'done'` clause is visible. The earlier fixture wrote one site and
    one status, and both mutations survived it 10/10.
    """
    p = tmp_path / name
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "site_id TEXT, site_name TEXT, url TEXT, status TEXT, "
               "filename TEXT, file_size INTEGER, message TEXT, "
               "screenshot TEXT, honeypot_score REAL, bytes_fetched INTEGER, "
               "ts TEXT DEFAULT(strftime('%Y-%m-%dT%H:%M:%S','now')))")
    for sid, status, bf in rows:
        cx.execute("INSERT INTO history(site_id,status,filename,file_size,"
                   "message,bytes_fetched) VALUES (?,?,?,?,?,?)",
                   (sid, status, "scene_002.mp4", 9421, "", bf))
    cx.commit()
    cx.close()
    return p


# ── defect 2: selection ──────────────────────────────────────────────────────

# The login site sorts first ASCENDING by id and by name; the queue site sorts
# first DESCENDING by name. So neither sorted()[0] nor sorted(reverse=True)[0]
# lands on the right answer, which is what the earlier fixture allowed.
_BOTH = {
    "aaa11111": {"name": "bdseed fixture login"},
    "zzz99999": {"name": "bdseed fixture site"},
}


def test_the_queue_site_is_chosen_when_both_seeded_sites_exist(tmp_path):
    sid, _ = checks._pipeline_setup(_Ctx(_BOTH, _history(tmp_path, [])))
    assert sid == "zzz99999", (
        f"_pipeline_setup chose {sid!r} with both seeded sites present. The "
        f"login site has no completed download by design, so choosing it is a "
        f"false WARN -- measured on the box in 1 of 5 runs."
    )


def test_selection_is_not_merely_sorted(tmp_path):
    """Defeats both sort directions, and a memoising fix.

    Two DIFFERENT site sets with different correct answers, so a cache that
    returns whatever it saw first is wrong on the second call. The earlier
    version asserted first == second on the SAME set, which a cache satisfies
    by construction and which survived 10/10.
    """
    a, _ = checks._pipeline_setup(_Ctx(_BOTH, _history(tmp_path, [], "a.db")))
    other = {"m5555555": {"name": "bdseed fixture login"},
             "b0000000": {"name": "bdseed fixture site"}}
    b, _ = checks._pipeline_setup(_Ctx(other, _history(tmp_path, [], "b.db")))
    assert (a, b) == ("zzz99999", "b0000000"), (
        f"got {(a, b)!r}. The queue site sorts LAST by id in the first set and "
        f"FIRST in the second, so neither sort order nor a memoised answer can "
        f"produce both. Selection must key on the site's name."
    )


def test_a_single_marked_site_is_still_chosen(tmp_path):
    sid, _ = checks._pipeline_setup(
        _Ctx({"only1": {"name": "bdseed whatever"}}, _history(tmp_path, [])))
    assert sid == "only1"


def test_an_unseeded_host_falls_back_to_the_first_site(tmp_path):
    sid, _ = checks._pipeline_setup(
        _Ctx({"real": {"name": "wow"}}, _history(tmp_path, [])))
    assert sid == "real"


# ── defect 1: the verdict ────────────────────────────────────────────────────

_ONE = {"s1": {"name": "bdseed fixture site"}}
_COUNTS = {"s1": {"done": 8, "failed": 0}}


def test_a_skip_only_history_does_not_pass(tmp_path):
    """THE DEFECT -- the box's eight-run sequence, exactly."""
    db = _history(tmp_path, [("s1", "done", 0)] * 8)
    level, msg = checks.l11_end_to_end_small_download(_Ctx(_ONE, db, _COUNTS))
    assert level == harness.WARN, (
        f"L11 returned {level}: {msg!r}. Every completion transferred zero "
        f"bytes -- the runner reported done, cancelled the download and "
        f"returned. Asserting the exact verdict, not just 'not PASS': the "
        f"earlier version read != PASS and a mutation returning NA survived it."
    )


def test_a_real_completion_passes(tmp_path):
    db = _history(tmp_path, [("s1", "done", 9421)])
    level, msg = checks.l11_end_to_end_small_download(
        _Ctx(_ONE, db, {"s1": {"done": 1, "failed": 0}}))
    assert level == harness.PASS, f"returned {level}: {msg!r}"


def test_another_sites_download_does_not_certify_this_one(tmp_path):
    """Closes the hole that survived 10/10: the site_id clause.

    The chosen site's completions are all skips; a DIFFERENT site has a real
    one. Dropping `WHERE site_id = ?` reads the other site's row and PASSes.
    On the box that other site is the login fixture or a production site.
    """
    db = _history(tmp_path, [("s1", "done", 0), ("s1", "done", 0),
                             ("other", "done", 9421)])
    level, msg = checks.l11_end_to_end_small_download(_Ctx(_ONE, db, _COUNTS))
    assert level == harness.WARN, (
        f"L11 returned {level}: {msg!r}. The only real transfer in that "
        f"history belongs to site 'other'; site 's1' skipped every time."
    )


def test_a_failed_row_does_not_certify_a_download(tmp_path):
    """Closes the second hole that survived 10/10: the status filter.

    A `failed` row can carry a non-zero byte count -- bytes really did move
    before the job failed. Dropping `status = 'done'` reads it as success.
    """
    db = _history(tmp_path, [("s1", "done", 0), ("s1", "failed", 9421)])
    level, msg = checks.l11_end_to_end_small_download(_Ctx(_ONE, db, _COUNTS))
    assert level == harness.WARN, (
        f"L11 returned {level}: {msg!r}. The only non-zero transfer belongs to "
        f"a FAILED job."
    )


def test_pre_migration_rows_are_unknown_not_proof(tmp_path):
    """NULL is the third state and it is not a download.

    Every history row written before #63 has bytes_fetched NULL. Treating NULL
    as a transfer would reopen the hole for the operator's entire existing
    history the moment this ships.
    """
    db = _history(tmp_path, [("s1", "done", None)] * 5)
    level, msg = checks.l11_end_to_end_small_download(_Ctx(_ONE, db, _COUNTS))
    assert level == harness.WARN, (
        f"L11 returned {level}: {msg!r} on rows that never recorded whether "
        f"anything was fetched."
    )


def test_the_message_names_the_skip(tmp_path):
    """'no completed downloads yet' would send the operator to the seeder
    when the actual finding is that the pipeline was short-circuited."""
    db = _history(tmp_path, [("s1", "done", 0)] * 3)
    _, msg = checks.l11_end_to_end_small_download(_Ctx(_ONE, db, _COUNTS))
    assert "0 bytes" in msg or "transferred" in msg.lower() or "skip" in msg.lower(), (
        f"message was {msg!r} and does not say the completions moved no bytes."
    )


def test_an_unreadable_db_fails(tmp_path):
    """FAIL, not WARN and not NA -- and it adds no new red.

    NA's contract is "the subject is not observable on this host"; here the
    subject is present (two 200s got us this far) and what is missing is the
    check's ability to READ its evidence. L22 and L26 already FAIL on exactly
    these conditions and are both in LIVE_IDS, so any host that can reach this
    state already fails its capture.
    """
    level, msg = checks.l11_end_to_end_small_download(
        _Ctx(_ONE, tmp_path / "absent.db", _COUNTS))
    assert level == harness.FAIL, (
        f"L11 returned {level}: {msg!r} with no history DB. It cannot tell a "
        f"download from a skip without one."
    )


# ── the constants this check copies from the seeder ──────────────────────────

def test_the_queue_site_name_matches_the_seeder(tmp_path):
    """A third copied seeder constant needs the same pin as SEED_MARKER.

    tests/test_u34_pipeline_live_tests.py already pins SEED_MARKER equal across
    the two files. Without the same treatment for the queue-site name, this is
    the copy nobody updates -- and a silent mismatch sends selection straight
    back to the arbitrary branch.
    """
    seeder = (ROOT / "tools" / "live_seed.py").read_text(encoding="utf-8")
    assert f'"{checks.SEED_QUEUE_SITE_NAME}"' in seeder \
        or f"'{checks.SEED_QUEUE_SITE_NAME}'" in seeder \
        or checks.SEED_QUEUE_SITE_NAME.split(maxsplit=1)[1] in seeder, (
        f"live_tests/checks.py selects on "
        f"{checks.SEED_QUEUE_SITE_NAME!r} but tools/live_seed.py does not "
        f"create a site by that name. Selection would fall through to the "
        f"arbitrary branch this cut exists to remove."
    )
