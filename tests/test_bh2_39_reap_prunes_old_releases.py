"""BH2-39 (findings/BH2-claim-prep-bd-agy-sonnet-2.md, MED bd-claim-object.sh:_reap).

`bd-claim-object.sh reap` kept every RELEASE/RELEASED row forever, so review-claims.tsv only grew (4,310 RELEASED
rows of 4,771 on 2026-09-28). The candidate prunes a RELEASED row older than BD_CLAIM_RELEASE_RETENTION_DAYS
(default 7) unless a claim row kept by the same reap shares its (object, lens): the last row wins every fold, so
such a release still cancels that claim and must stay.

The candidate lives outside the repo (harness-work/FIX/bh2-39-bd-worker-B3-B/bd-claim-object.sh) and is opted in
with BD_BH2_39_CANDIDATE. The claims file, lock and worktree root are tmp_path fixtures (BD_OBJECT_CLAIMS,
BD_OBJECT_CLAIMS_LOCK, BD_CLAIM_WTROOT) and tmux is a PATH stub whose live seats come from STUB_LIVE, so the
live review-claims.tsv and tmux server are never touched.
"""

from __future__ import annotations

import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH2_39_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

OLD = "2020-01-01T00:00:00Z"
RECENT = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
LIVE = "bd-live-seat"
DEAD = "bd-dead-seat"

TMUX_STUB = r"""#!/bin/bash
# tmux stub: has-session -t =<seat> succeeds only for seats named in $STUB_LIVE.
[ "$1" = has-session ] || exit 0
s=${3#=}
for l in $STUB_LIVE; do [ "$l" = "$s" ] && exit 0; done
exit 1
"""


def _row(*f: str) -> str:
    return "\t".join(f)


class Env:
    def __init__(self, tmp_path: Path) -> None:
        cand = Path(CANDIDATE)
        assert cand.is_file(), f"BD_BH2_39_CANDIDATE={CANDIDATE} is not a file"
        self.cand = cand
        self.wt = tmp_path / "wt"
        self.wt.mkdir()
        self.cl = tmp_path / "review-claims.tsv"
        self.lk = tmp_path / "review-claims.lock"
        self.lk.touch()
        bindir = tmp_path / "bin"
        bindir.mkdir()
        tmux = bindir / "tmux"
        tmux.write_text(TMUX_STUB)
        tmux.chmod(tmux.stat().st_mode | stat.S_IXUSR)
        self.env = dict(os.environ)
        self.env.update(
            PATH=f"{bindir}:{self.env.get('PATH', '/usr/bin:/bin')}",
            STUB_LIVE=LIVE,
            BD_OBJECT_CLAIMS=str(self.cl),
            BD_OBJECT_CLAIMS_LOCK=str(self.lk),
            BD_CLAIM_WTROOT=str(self.wt),
        )

    def write(self, rows: list[str]) -> None:
        self.cl.write_text("".join(r + "\n" for r in rows))

    def rows(self) -> list[str]:
        return self.cl.read_text().splitlines()

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = dict(self.env)
        env.update(extra)
        return subprocess.run(
            ["bash", str(self.cand), *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def holder(self, obj: str, lens: str) -> str:
        r = self.run("holder", obj, lens)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout.strip()


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def test_old_release_with_no_kept_claim_is_pruned(env: Env) -> None:
    old = _row("RELEASED", "/cuts/a", "correctness", DEAD, OLD)
    recent = _row("RELEASED", "/cuts/b", "correctness", DEAD, RECENT)
    env.write([old, recent])
    r = env.run("reap")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "REAPED" in r.stdout
    assert env.rows() == [recent]


def test_retention_env_widens_the_window(env: Env) -> None:
    """Positive control on the cutoff: with 0 days even yesterday's release is past retention."""
    recent = _row("RELEASED", "/cuts/b", "correctness", DEAD, RECENT)
    env.write([recent])
    assert env.run("reap", BD_CLAIM_RELEASE_RETENTION_DAYS="0").returncode == 0
    assert env.rows() == []


def test_old_release_that_cancels_a_kept_claim_stays(env: Env) -> None:
    """A live seat name is reused: dropping this release would resurrect the claim in every fold."""
    claim = _row("CLAIM", "/cuts/c", "correctness", LIVE, OLD)
    rel = _row("RELEASED", "/cuts/c", "correctness", LIVE, OLD)
    other_lens = _row("RELEASED", "/cuts/c", "shape", LIVE, OLD)
    env.write([claim, rel, other_lens])
    before = env.holder("/cuts/c", "correctness")
    assert before.startswith("NOT-HELD")
    assert env.run("reap").returncode == 0
    assert env.rows() == [
        claim,
        rel,
    ]  # the shape release cancels nothing kept, so it goes
    assert env.holder("/cuts/c", "correctness") == before


def test_alias_spelling_is_one_object(env: Env) -> None:
    """keys() folds <wtroot>/row123, row123 and 123 together; a legacy claim under "123" keeps the release."""
    legacy_claim = _row("123", "correctness", LIVE, OLD)
    rel = _row("RELEASED", f"{env.wt}/row123", "correctness", LIVE, OLD)
    env.write([legacy_claim, rel])
    before = env.holder(f"{env.wt}/row123", "correctness")
    assert before.startswith("NOT-HELD")
    assert env.run("reap").returncode == 0
    assert env.rows() == [legacy_claim, rel]
    assert env.holder(f"{env.wt}/row123", "correctness") == before


def test_dead_claim_goes_and_its_old_release_with_it(env: Env) -> None:
    claim = _row("CLAIM", "/cuts/d", "correctness", DEAD, OLD)
    rel = _row("RELEASED", "/cuts/d", "correctness", DEAD, OLD)
    live_claim = _row("CLAIM", "/cuts/e", "correctness", LIVE, RECENT)
    env.write([claim, rel, live_claim])
    assert env.run("reap").returncode == 0
    assert env.rows() == [live_claim]
    assert env.holder("/cuts/e", "correctness") == LIVE


def test_other_verbs_and_unreadable_timestamps_are_kept(env: Env) -> None:
    kept = [
        _row("LENS-STOOD-DOWN", "/cuts/f", "correctness", DEAD, OLD),
        _row("RELEASE", "/cuts/g", "correctness", DEAD, OLD),
        _row("RELEASED", "/cuts/h", "correctness", DEAD, "yesterday"),
        _row("RELEASED", "/cuts/i", "correctness", DEAD),
    ]
    env.write(kept)
    assert env.run("reap").returncode == 0
    assert env.rows() == kept


def test_bad_retention_refuses_and_leaves_the_file(env: Env) -> None:
    rows = [_row("RELEASED", "/cuts/a", "correctness", DEAD, OLD)]
    env.write(rows)
    before = env.cl.read_bytes()
    r = env.run("reap", BD_CLAIM_RELEASE_RETENTION_DAYS="7d")
    assert r.returncode == 2
    assert (
        "BD_CLAIM_RELEASE_RETENTION_DAYS must be a whole number of days, got [7d]"
        in r.stderr
    )
    assert "REAPED" not in r.stdout
    assert env.cl.read_bytes() == before
