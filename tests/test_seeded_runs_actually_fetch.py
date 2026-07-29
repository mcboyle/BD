"""The seeder's own site skips its downloads, and the flag would not reach it.

THE DEFECT. `runner_transport.py:794` defaults `skip_if_exists` True, so once
`scene_002.mp4` exists the runner reports the job done, calls `dl.cancel()` and
returns without fetching. Measured on the deploy host: eight consecutive seeded
runs, seven of them skips. #64 made L11 report that honestly instead of
certifying the pipeline; this makes the condition stop happening.

Setting the flag is one line. It is NOT sufficient on its own, and that is the
substance of this cut:

  `ensure_seed_site` REUSES an existing marked site and never re-POSTs its
  config -- and the seeder's HTTP client has no put or patch verb at all
  (its methods are __init__/_headers/_request/get/post/delete). So a site
  created before this change keeps skip_if_exists=True forever, and the new
  flag applies only to sites created after it. The runs where the flag matters
  most -- the ones where a previous teardown failed and left a site behind --
  are exactly the runs it would never reach. A flag that cannot reach its
  subject is CLAUDE.md section 0 with the config on the outside.

  Worse, the reuse path picks `sorted(_marked_site_ids(client))[0]`, and the
  seeder creates TWO marked sites: "bdseed fixture site" and
  "bdseed fixture login". Both carry the marker. Site ids are `uuid4().hex[:8]`,
  so the sort is a coin flip, and on a reuse run the seeded URLs can be queued
  against the LOGIN fixture. That is the seeder-side twin of the selection
  defect #64 fixed on the check side.

SO THE CUT IS: set the flag, select by exact name, and delete-and-recreate
rather than reuse. Delete-and-recreate is not tidiness -- it is what makes the
config apply, and it gives every run a fresh `uuid4` site id, which makes
site_id a per-run key. That in turn closes a recency hole in L11 for free:
without it, a site holding run 1's real fetch and run 2's skip returns PASS on
run 1's evidence.

DELETING IS SAFE AND SCOPED. Site delete (app_sites_id_core.py:861-906) stops
the runner, drops the config and the queue rows, and never touches the download
directory. It is issued only for ids whose name carries the seeder's own marker.

RESIDUE, STATED HONESTLY. With the skip disabled, `safe_dest`
(detect.py:537-543) appends _1, _2, ... so each capture leaves another ~8 KB
file in the download directory rather than reusing the one already there.
Measured chain: scene_002.mp4, scene_002_1.mp4, scene_002_2.mp4, ... Teardown
does not remove downloaded files, and extending it to delete under a shared
download directory is a strictly larger hazard than this cut -- that is an
operator decision, recorded here rather than taken.

RED-first: R1, R2 and R3 below fail on pristine source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import live_seed  # noqa: E402


class _FakeClient:
    """Records writes so the test can assert on what the seeder DID, not only
    on what it returned. The shipped FakeClient in
    tests/test_live_seed_starts_and_settles.py answers /api/status with {},
    so the reuse branch is currently uncovered by any test."""

    def __init__(self, sites=None, new_id="new00001"):
        self._sites = sites or {}
        self._new_id = new_id
        self.calls = []

    def get(self, path, **kw):
        self.calls.append(("GET", path, None))
        if path == "/api/status":
            # TOP-LEVEL {sid: {name, ...}}, which is what _marked_site_ids
            # iterates. An earlier version of this fake wrapped it in
            # {"sites": ...}; body.items() then yielded one ("sites", {...})
            # pair whose meta had no name, so no marked site was ever found and
            # FOUR assertions below passed against an empty site list --
            # including one that asserts the login fixture is not chosen.
            return dict(self._sites)
        return {}

    def post(self, path, payload=None, **kw):
        self.calls.append(("POST", path, payload))
        return {"id": self._new_id}

    def delete(self, path, **kw):
        self.calls.append(("DELETE", path, None))
        return {"ok": True}

    def writes(self):
        return [(v, p) for v, p, _ in self.calls if v in ("POST", "DELETE")]


_QUEUE = live_seed.SEED_SITE_NAME
_LOGIN = live_seed.SEED_LOGIN_SITE_NAME


# ── R1: the flag ─────────────────────────────────────────────────────────────

def test_the_seeded_site_does_not_skip_existing_files():
    """The site the seeder owns must fetch every run.

    Asserting `is False`, not falsiness: the DEFAULTS fill at app.py:4218 guards
    on `cfg.get(k) in ("", None)`, so an absent key or an empty string is
    replaced by the default True while an explicit False survives. A fix that
    set it to "" would read as off here and be on in production.
    """
    cfg = live_seed.queue_site_config()
    assert cfg.get("skip_if_exists") is False, (
        f"queue_site_config() gave skip_if_exists={cfg.get('skip_if_exists')!r}. "
        f"Absent or empty is filled with the default True at app.py:4218, so "
        f"the seeded download skips whenever the file already exists -- "
        f"measured on the box as seven skips in eight runs."
    )


def test_the_flag_is_scoped_to_the_seeders_own_site():
    """It must not appear on the login fixture, which downloads nothing, and
    must never be a global or a CLI mode."""
    assert "skip_if_exists" not in live_seed.login_site_config()


# ── R2: selection ────────────────────────────────────────────────────────────

def test_reuse_does_not_pick_the_login_fixture():
    """Both seeded sites carry the marker; only one is the queue fixture.

    ids chosen so the login site sorts FIRST -- which is what the pristine
    implementation returns.
    """
    c = _FakeClient({"aaa11111": {"name": _LOGIN},
                     "zzz99999": {"name": _QUEUE}})
    sid = live_seed.ensure_seed_site(c)
    assert sid != "aaa11111", (
        "ensure_seed_site selected the LOGIN fixture. The seeded URLs would be "
        "queued against a site that never downloads, while L11 reads the queue "
        "fixture -- the seeder-side twin of the defect #64 fixed check-side."
    )


# ── R3: reuse must not strand a stale config ─────────────────────────────────

def test_an_existing_seed_site_is_recreated_not_reused():
    """THE LOAD-BEARING ONE.

    The client has no put/patch verb, so a reused site keeps whatever config it
    was created with -- including skip_if_exists=True from before this cut.
    Recreating is what makes the flag reach the site, and it gives each run a
    fresh site id, which makes site_id a per-run key for L11.
    """
    c = _FakeClient({"old00001": {"name": _QUEUE}}, new_id="fresh001")
    sid = live_seed.ensure_seed_site(c)
    assert ("DELETE", "/api/sites/old00001") in c.writes(), (
        f"ensure_seed_site did not delete the stale seeded site. Writes: "
        f"{c.writes()}. Without this the site keeps its old config forever and "
        f"the new flag is inert on exactly the runs it matters for."
    )
    assert any(v == "POST" and p == "/api/sites" for v, p in c.writes()), (
        f"no site was created after the delete. Writes: {c.writes()}"
    )
    assert sid == "fresh001", f"returned {sid!r}, not the newly created site"


def test_the_recreated_site_carries_the_new_config():
    """Recreating with the OLD payload would satisfy the test above and change
    nothing. The POST body must be the current queue_site_config()."""
    c = _FakeClient({"old00001": {"name": _QUEUE}})
    live_seed.ensure_seed_site(c)
    posted = [p for v, path, p in c.calls
              if v == "POST" and path == "/api/sites"]
    assert posted and posted[0].get("skip_if_exists") is False, (
        f"the recreated site was POSTed with {posted!r} -- it must carry the "
        f"current config, or recreating buys nothing."
    )


def test_the_login_fixture_is_not_deleted_by_queue_seeding():
    """Scoped deletion. Removing the login site here would break L9 and the
    auth checks that run after seeding."""
    c = _FakeClient({"aaa11111": {"name": _LOGIN},
                     "zzz99999": {"name": _QUEUE}})
    live_seed.ensure_seed_site(c)
    assert ("DELETE", "/api/sites/aaa11111") not in c.writes(), (
        f"the login fixture was deleted by queue seeding. Writes: {c.writes()}"
    )


# ── behaviour that must survive ──────────────────────────────────────────────

def test_a_clean_host_still_creates_one_site():
    c = _FakeClient({})
    assert live_seed.ensure_seed_site(c) == "new00001"
    assert [v for v, _ in c.writes()] == ["POST"], (
        f"a clean host should issue exactly one write. Got {c.writes()}"
    )


def test_an_unmarked_operator_site_is_never_touched():
    """The seeder must never borrow or delete a site it does not own."""
    c = _FakeClient({"real0001": {"name": "wow"}})
    sid = live_seed.ensure_seed_site(c)
    assert sid == "new00001"
    assert ("DELETE", "/api/sites/real0001") not in c.writes()


def test_dry_run_writes_nothing():
    c = _FakeClient({"old00001": {"name": _QUEUE}})
    live_seed.ensure_seed_site(c, dry_run=True)
    assert c.writes() == [], f"dry_run performed writes: {c.writes()}"
