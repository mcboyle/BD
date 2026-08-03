"""The seeder must START the queue it seeds, and say what happened to it.

THE DEFECT THIS FILE EXISTS TO CLOSE. L11 (end-to-end-small-download), L12
(hls-dash-segmented-download) and L14 (stash-dedup-skip) all gate on `done > 0`
-- a completed download. Nothing anywhere ever started one. `tools/live_seed.py`
placed three URLs against a marked site and stopped; on the last capture the
queue held them in `waiting` with `running=0`, and the three checks reported "no
completed downloads yet" exactly as they had since they were written. That reads
as BD failing to download. BD was never asked to download.

`live_tests/` structurally cannot ask: `Context` exposes get/log/ro_db and no
write verb, and that read-only property is what makes the suite safe to point at
production (pinned by tests/test_u34_pipeline_live_tests.py). So the start
belongs to the seeder, which is the writer.

WHAT THIS FILE PINS, and why each one is load-bearing:

  * The start predicate is the MARKER, exactly as teardown's is. Starting an
    unmarked site would download real media the operator never asked for. A site
    that cannot be PROVEN marked is not started -- unknown is a third state and
    it fails (CLAUDE.md 0).
  * The wait is BOUNDED and never silent. An unbounded wait turns an unattended
    capture into a hang; a swallowed timeout turns an unknown into a false OK.
    A timeout is reported per URL and exits non-zero.
  * `skipped_duplicate` settles. It is the outcome L14 exists to observe, so
    treating it as non-terminal would make the seeder burn its whole budget on
    precisely the result it was seeding for.
  * Teardown still works after a completed download, and says what it LEAVES.
    This bullet used to read "`history` is append-only -- db_log is the only
    writer and db_prune (by AGE) the only deleter -- so a completed seeded
    download leaves a row no marker-matched teardown can remove". That claim
    was FALSE and bulk_downloader/db.py:988-992 records its retraction:
    batch_ops.bulk_delete issues `DELETE FROM history WHERE id = ?` and is
    reachable over HTTP at POST /api/batch/delete. The rows CAN be removed,
    and `--clear-history` removes them (together with their library twins over
    DELETE /api/library/<lid>, which must go FIRST because library rows carry
    history_id and PRAGMA foreign_keys is never enabled -- library.py:538-543).
    The clear is OPT-IN because capture.sh runs teardown unattended and the
    clear's predicate is the marker across ALL history, not this run's nonce.
    So the DEFAULT teardown still leaves the rows and must SAY it leaves them;
    silence would be the fixture quietly accumulating state that the next run
    reads as organic.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "tools" / "live_seed.py"
CAPTURE_SH = REPO_ROOT / "capture.sh"


def _load():
    loader = importlib.machinery.SourceFileLoader(
        "bd_live_seed_start", str(SEED_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeClient:
    """Records every request instead of issuing one.

    Same shape as tests/test_live_seed.py's, with one addition: a response may
    be a LIST of bodies, consumed one per call, so a queue that changes between
    polls can be modelled. A single body is returned for every call, as before.
    """

    def __init__(self, responses=None):
        self.calls = []
        self._responses = dict(responses or {})

    def _resolve(self, key, default):
        if key not in self._responses:
            return default
        body = self._responses[key]
        if isinstance(body, list) and body and isinstance(body[0], _Reply):
            reply = body[0] if len(body) == 1 else body.pop(0)
            return reply.body
        return body

    def get(self, path):
        self.calls.append(("GET", path, None))
        return self._resolve(("GET", path), {"ok": True})

    def post(self, path, payload):
        self.calls.append(("POST", path, payload))
        return self._resolve(("POST", path), {"ok": True})

    def delete(self, path):
        self.calls.append(("DELETE", path, None))
        return self._resolve(("DELETE", path), {"ok": True})

    def posted(self):
        return [(p, body) for method, p, body in self.calls if method == "POST"]

    def paths(self, method="GET"):
        return [p for m, p, _b in self.calls if m == method]


class _Reply:
    """One scripted response in a sequence."""

    def __init__(self, body):
        self.body = body


MARKED_SID = "seedsite"
REAL_SID = "aabbccdd"


def _status_body(seed, *, marked=True, real=True):
    body = {}
    if marked:
        body[MARKED_SID] = {"name": f"{seed.SEED_MARKER} fixture site",
                            "config": {}, "state": "idle"}
    if real:
        body[REAL_SID] = {"name": "My Real Site", "config": {}, "state": "idle"}
    return body


def _queue_body(rows):
    return {"rows": [dict(r) for r in rows], "total": len(rows),
            "offset": 0, "limit": 500}


def _queue_path_calls(client):
    return [p for p in client.paths("GET") if f"/api/sites/{MARKED_SID}/queue" in p]


# ── the capability must exist at all (denominator canary) ───────────────────

def test_the_seeder_exposes_a_start_and_a_bounded_wait():
    """Canary: every assertion below is about these two names.

    If they are renamed the rest of this file would raise AttributeError one
    test at a time and the reason would be buried. Fail here, once, clearly.
    """
    seed = _load()
    for name in ("start_seeded_site", "wait_for_settle", "start_and_settle"):
        assert callable(getattr(seed, name, None)), (
            f"tools/live_seed.py defines no callable {name!r}. The seeder "
            f"queues URLs and never starts them, so L11/L12/L14 have nothing "
            f"to observe and report 'no completed downloads yet' forever."
        )


# ── constraint 1: only ever start a site the seeder OWNS ────────────────────

def test_starting_refuses_a_site_that_is_not_provably_marked():
    """An unmarked site is the operator's; starting it downloads real media.

    The predicate is the marker, exactly as teardown's is -- never position,
    never recency, never "the site we were handed".
    """
    seed = _load()
    client = FakeClient({("GET", "/api/status"): _status_body(seed)})
    with pytest.raises(seed.SeedRefused) as excinfo:
        seed.start_seeded_site(client, REAL_SID)
    assert seed.SEED_MARKER in str(excinfo.value) or "mark" in str(excinfo.value).lower()
    started = [p for p, _b in client.posted() if p.endswith("/start")]
    assert not started, (
        f"the seeder POSTed {started} for an unmarked site; that starts the "
        f"operator's own downloads"
    )


def test_starting_refuses_when_the_marked_set_cannot_be_read():
    """Unknown is a third state and it FAILS (CLAUDE.md 0).

    If /api/status is unreadable the seeder cannot prove the site is its own.
    Proceeding on an unreadable answer is the gate-that-cannot-see-its-subject
    failure, with a real download at the end of it.
    """
    seed = _load()
    for body in ({"ok": False, "error": "boom"}, None, "not-a-dict", []):
        client = FakeClient({("GET", "/api/status"): body})
        with pytest.raises(seed.SeedRefused):
            seed.start_seeded_site(client, MARKED_SID)
        assert not [p for p, _b in client.posted() if p.endswith("/start")]


def test_starting_a_marked_site_posts_to_the_apps_own_start_endpoint():
    """The start must be BD's, not a simulation."""
    seed = _load()
    client = FakeClient({("GET", "/api/status"): _status_body(seed)})
    seed.start_seeded_site(client, MARKED_SID)
    assert any(p == f"/api/sites/{MARKED_SID}/start" for p, _b in client.posted()), (
        "the seeder never POSTed to /api/sites/<sid>/start; the queue it "
        "placed is never drained"
    )


def test_a_start_the_app_declines_is_refused_rather_than_waited_out():
    """A start BD refused, or accepted-but-blocked, must not cost the budget.

    _do_action answers {ok:false} on 404/409 and {ok:true, blocked_by:...} when
    the runner is rate-limited or out of disk. Either way the queue will not
    drain, so waiting for it is waiting for a known negative.
    """
    seed = _load()
    for start_body in ({"ok": False, "error": "Not found"},
                       {"ok": True, "blocked_by": "low_disk"},
                       {"ok": True, "blocked_by": "rate_limited"}):
        client = FakeClient({
            ("GET", "/api/status"): _status_body(seed),
            ("POST", f"/api/sites/{MARKED_SID}/start"): start_body,
        })
        with pytest.raises(seed.SeedRefused) as excinfo:
            seed.start_seeded_site(client, MARKED_SID)
        assert str(excinfo.value), "refusal carried no reason"


# ── constraint 2: bounded, and never silent ─────────────────────────────────

def test_the_wait_is_bounded_and_names_every_url_that_never_settled():
    """A hang is worse than a failure. A silent timeout is worse than both."""
    seed = _load()
    urls = [seed.seeded_url(0), seed.seeded_url(1)]
    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": urls[0], "status": "pending", "message": ""},
            {"url": urls[1], "status": "running", "message": "12%"},
        ]),
    })
    plan = seed.wait_for_settle(client, MARKED_SID, urls, timeout=0.0,
                                interval=0.01)
    assert plan.get("settled") is False, (
        f"a queue stuck in pending/running reported settled: {plan}")
    unresolved = plan.get("unresolved") or []
    assert sorted(unresolved) == sorted(urls), (
        f"the timeout did not name what never settled: {plan}")
    per_url = plan.get("per_url") or {}
    assert per_url.get(urls[0], {}).get("status") == "pending"
    assert per_url.get(urls[1], {}).get("status") == "running"
    assert _queue_path_calls(client), (
        "a zero-budget wait polled nothing at all and still reported a state; "
        "a check that never looked at its subject must not answer about it")


def test_a_url_absent_from_the_queue_is_unknown_not_settled():
    """Absent is not done. Treating it as done is the vacuous PASS."""
    seed = _load()
    urls = [seed.seeded_url(0), seed.seeded_url(1)]
    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": urls[0], "status": "done", "message": "ok"},
        ]),
    })
    plan = seed.wait_for_settle(client, MARKED_SID, urls, timeout=0.0,
                                interval=0.01)
    assert plan.get("settled") is False
    assert urls[1] in (plan.get("unresolved") or [])
    assert "unknown" in str((plan.get("per_url") or {}).get(urls[1], {})).lower(), (
        f"a URL missing from the queue was not reported as unknown: {plan}")


def test_an_unreadable_queue_is_unknown_not_settled():
    """The seeder must not conclude 'finished' from an answer it cannot read."""
    seed = _load()
    urls = [seed.seeded_url(0)]
    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): {"error": "Not found"},
    })
    plan = seed.wait_for_settle(client, MARKED_SID, urls, timeout=0.0,
                                interval=0.01)
    assert plan.get("settled") is False
    assert plan.get("queue_readable") is False, (
        f"an unreadable queue was not reported as unreadable: {plan}")


def test_a_dedup_skip_settles_rather_than_burning_the_whole_budget():
    """skipped_duplicate is the outcome L14 exists to observe.

    runner.py marks a filename-duplicate job 'skipped_duplicate' and stops.
    Waiting for it to become 'done' would spend the entire timeout on exactly
    the result the seed set's deliberate repeat was placed to produce.
    """
    seed = _load()
    urls = [seed.seeded_url(0), seed.seeded_url(1)]
    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": urls[0], "status": "done", "message": "ok"},
            {"url": urls[1], "status": "skipped_duplicate",
             "message": "Duplicate of history #1"},
        ]),
    })
    plan = seed.wait_for_settle(client, MARKED_SID, urls, timeout=0.0,
                                interval=0.01)
    assert plan.get("settled") is True, (
        f"skipped_duplicate was not treated as terminal: {plan}")


def test_every_terminal_status_the_runner_writes_is_treated_as_settled():
    """Derived from the runner, not guessed.

    The non-terminal set is {pending, running}; everything else the runner
    writes is an end state. A status the seeder does not recognise must count
    as UNSETTLED (it times out and says so) -- never as settled.
    """
    seed = _load()
    for status in ("done", "failed", "error", "needs_review", "stopped",
                   "cancelled", "skipped_duplicate", "dead_letter"):
        url = seed.seeded_url(0)
        client = FakeClient({
            ("GET", "/api/status"): _status_body(seed),
            ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
                {"url": url, "status": status, "message": ""}]),
        })
        plan = seed.wait_for_settle(client, MARKED_SID, [url], timeout=0.0,
                                    interval=0.01)
        assert plan.get("settled") is True, (
            f"terminal status {status!r} was not treated as settled: {plan}")

    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": seed.seeded_url(0), "status": "a_status_from_the_future",
             "message": ""}]),
    })
    plan = seed.wait_for_settle(client, MARKED_SID, [seed.seeded_url(0)],
                                timeout=0.0, interval=0.01)
    assert plan.get("settled") is False, (
        "an unrecognised status was treated as settled; a future status would "
        "silently turn 'we have no idea' into 'finished'")


def test_the_wait_stops_as_soon_as_the_queue_settles():
    """It must return on the transition, not sit out its whole budget."""
    seed = _load()
    url = seed.seeded_url(0)
    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): [
            _Reply(_queue_body([{"url": url, "status": "running", "message": ""}])),
            _Reply(_queue_body([{"url": url, "status": "done", "message": "ok"}])),
        ],
    })
    plan = seed.wait_for_settle(client, MARKED_SID, [url], timeout=6.0,
                                interval=0.01)
    assert plan.get("settled") is True, plan
    assert plan.get("elapsed_seconds", 99) < 2.0, (
        f"the wait burned {plan.get('elapsed_seconds')}s of a 6s budget on a "
        f"queue that settled on the second poll: {plan}")


def test_the_deliberate_duplicate_is_reported_as_two_subjects_not_three():
    """The repeat collapses at intake, and the report must say so.

    runner_queue.load_urls counts a URL already in self.jobs as a `dupe` and
    does not create a second job, and the queue table is keyed by
    (site_id, url). So the seed set's three URLs are two queue rows.

    THE FIRST VERSION OF THIS TEST WAS VACUOUS and its own mutation run said
    so: it asserted only `settled is True`, which holds either way because
    looking the same URL up twice returns the same terminal status twice. The
    property that actually differs is the REPORTED SUBJECT SET -- a seeder
    claiming three subjects over a queue that can only ever hold two is
    describing work that does not exist, and `unresolved` would name the same
    URL twice on a timeout.
    """
    seed = _load()
    urls = [seed.seeded_url(i) for i in range(len(seed._SEED_PATHS))]
    assert urls[2] == urls[0], "premise gone: the seed set no longer repeats"
    distinct = [urls[0], urls[1]]

    client = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": urls[0], "status": "done", "message": "ok"},
            {"url": urls[1], "status": "done", "message": "ok"},
        ]),
    })
    plan = seed.wait_for_settle(client, MARKED_SID, urls, timeout=0.0,
                                interval=0.01)
    assert plan.get("settled") is True, (
        f"the collapsed duplicate was waited on as a third job: {plan}")
    assert plan.get("urls") == distinct, (
        f"the settle report claims {plan.get('urls')} as its subjects; the "
        f"queue holds {distinct} and never more")

    # ...and on a timeout the same collapse must hold, or the operator reads
    # one stuck URL as two.
    stuck = FakeClient({
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([]),
    })
    timed_out = seed.wait_for_settle(stuck, MARKED_SID, urls, timeout=0.0,
                                     interval=0.01)
    assert timed_out.get("unresolved") == distinct, (
        f"the timeout named {timed_out.get('unresolved')}; the deliberate "
        f"repeat is one job, not two")

    started = seed.start_and_settle(client, MARKED_SID, urls, timeout=0.0,
                                    interval=0.01)
    assert started.get("duplicates_collapsed") == 1, (
        f"start_and_settle did not report the collapse "
        f"({started.get('duplicates_collapsed')}); 3 URLs in, 2 jobs out is a "
        f"fact the log has to carry or the counts read as a lost URL")


# ── the CLI: opt-in, honest exit codes, dry-run safe ────────────────────────

def test_a_dry_run_starts_nothing(monkeypatch):
    """--dry-run must be honest: report intent, change nothing."""
    seed = _load()
    client = FakeClient({("GET", "/api/status"): _status_body(seed)})
    monkeypatch.setattr(seed, "Client", lambda base_url: client)
    rc = seed.main(["--seed", "--start", "--dry-run", "--count", "1"])
    assert rc == 0
    assert not client.posted(), (
        f"a dry run issued {client.posted()}; it must not mutate")


def test_a_settle_timeout_exits_non_zero_and_says_so(capsys, monkeypatch):
    """A timeout reported as success is an unknown laundered into an OK.

    capture.sh reads the exit code: a swallowed timeout would print "seeded 3
    marked URLs" over a queue that never ran.
    """
    seed = _load()
    urls = [seed.seeded_url(i) for i in range(3)]
    client = FakeClient({
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 0},
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": urls[0], "status": "pending", "message": ""},
            {"url": urls[1], "status": "pending", "message": ""},
        ]),
    })
    monkeypatch.setattr(seed, "Client", lambda base_url: client)
    rc = seed.main(["--seed", "--start", "--count", "3",
                    "--site-id", MARKED_SID, "--start-timeout", "0"])
    out = capsys.readouterr()
    assert rc != 0, "a queue that never ran exited 0"
    assert "TIMEOUT" in out.err or "timed out" in out.err.lower(), (
        f"the timeout was not reported on stderr:\n{out.err}")
    assert urls[0] in out.err or urls[0] in out.out, (
        f"the report does not name the URL that never settled:\n"
        f"{out.out}\n{out.err}")
    assert seed.SEED_MARKER in out.out, (
        f"the seed plan that DID succeed was not printed:\n{out.out}")


def test_a_successful_settle_exits_zero_and_reports_per_url(capsys, monkeypatch):
    """The normal path: every seeded URL reached a terminal state."""
    seed = _load()
    urls = [seed.seeded_url(i) for i in range(3)]
    client = FakeClient({
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 0},
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/sites/{MARKED_SID}/queue?limit=500"): _queue_body([
            {"url": urls[0], "status": "done", "message": "ok",
             "filename": "0.mp4"},
            {"url": urls[1], "status": "done", "message": "ok",
             "filename": "0.mp4"},
        ]),
    })
    monkeypatch.setattr(seed, "Client", lambda base_url: client)
    rc = seed.main(["--seed", "--start", "--count", "3",
                    "--site-id", MARKED_SID, "--start-timeout", "5"])
    out = capsys.readouterr()
    assert rc == 0, f"a settled queue exited {rc}:\n{out.err}"
    assert "done" in out.out, f"no per-URL outcome was printed:\n{out.out}"


def test_seeding_without_start_still_starts_nothing(monkeypatch):
    """--seed keeps its documented meaning: place URLs, run nothing.

    Making the start unconditional would turn every hand `--seed` -- and the
    dry-run and refusal tests that drive it -- into a real download plus a
    blocking wait. The capture asks for the start explicitly.
    """
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 0},
        ("GET", "/api/status"): _status_body(seed),
    })
    monkeypatch.setattr(seed, "Client", lambda base_url: client)
    seed.main(["--seed", "--count", "3", "--site-id", MARKED_SID])
    started = [p for p, _b in client.posted() if p.endswith("/start")]
    assert not started, (
        f"--seed alone started a download ({started}); that changes what an "
        f"operator's hand run does")


# ── a start that spawns no workers is not a start ───────────────────────────
#
# Measured locally on 2026-07-28 against the REAL app (temp BD_INSTALL_DIR, the
# real tools/fixture_site.py on :8899, the seeder's own endpoint sequence): the
# seeded site was created with a name and nothing else, so POST
# /api/sites/<sid>/start returned {"ok": true} and then parked. The queue read
# back:
#
#   /direct/media/0.mp4?bdseed=1 -> needs_review "Auto-teach: take over to
#       teach download selectors. Click 'Take over' on this row..."
#   /hls/scene/0.m3u8?bdseed=1   -> pending
#
# runner._start_serialized returns BEFORE spawning any worker when
# auto_teach_first_run is on and the site has neither learned download
# selectors nor an applied template: it flags the first URL needs_review and
# leaves the runner idle, waiting for a human to click "Take over" in the UI. A
# capture host has no human. So the start started nothing.
#
# The seeded LOGIN site already had to opt out of the sibling divert in
# runner_auth.login_async, for the same reason and with the same one-line
# answer. This is that decision applied to the site the queue actually runs on.

_RUNNER_PATH = REPO_ROOT / "bulk_downloader" / "runner.py"


def _start_still_diverts_on_auto_teach() -> bool:
    """True while runner._start_serialized carries the auto-teach pre-flight.

    The test below encodes that branch's CONDITION rather than its effect,
    because the effect (a runner idling for a click nobody will make) is not
    reachable from a unit test. If BD drops or renames the flag the condition
    is about nothing, so the premise is asserted separately and fails loudly
    instead of passing over a predicate that no longer exists. AST, not grep: a
    string search would also match the explanatory comment beside it.
    """
    import ast

    tree = ast.parse(_RUNNER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_start_serialized":
            literals = {n.value for n in ast.walk(node)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)}
            return {"auto_teach_first_run", "learned", "download",
                    "applied_template"} <= literals
    return False


def test_the_seeded_queue_site_does_not_park_in_auto_teach_takeover():
    """The site the seeder starts must reach the worker path."""
    assert _start_still_diverts_on_auto_teach(), (
        "runner._start_serialized no longer carries the auto_teach_first_run "
        "pre-flight; this test's premise is gone. Re-derive the branch before "
        "trusting this assertion again.")
    seed = _load()
    cfg = seed.queue_site_config()
    learned = (cfg.get("learned") or {}).get("download") or {}
    has_dl = bool(learned.get("trigger_selectors") or learned.get("row_selectors"))
    has_template = bool(cfg.get("applied_template"))
    diverts = (bool(cfg.get("auto_teach_first_run", True))
               and not has_dl and not has_template)
    assert not diverts, (
        "the seeded QUEUE site trips runner._start_serialized's auto-teach "
        "pre-flight, so --start spawns no workers at all: the first URL is "
        "flagged needs_review and the runner goes idle waiting for a human to "
        "click 'Take over'. Observed locally against the real app. Either set "
        "auto_teach_first_run False or give the site learned download "
        "selectors / an applied template.")


def test_the_seeded_queue_site_is_created_from_that_config():
    """The builder must be what ensure_seed_site actually POSTs.

    Otherwise the test above pins a function nobody calls -- a gate whose
    subject is not the thing that runs.
    """
    seed = _load()
    client = FakeClient({
        ("GET", "/api/status"): {},
        ("POST", "/api/sites"): {"id": "brandnew"},
    })
    seed.ensure_seed_site(client)
    created = [body for path, body in client.posted() if path == "/api/sites"]
    assert created == [seed.queue_site_config()], (
        f"ensure_seed_site POSTed {created}, not queue_site_config() "
        f"({seed.queue_site_config()})")


# ── constraint 3: teardown after a completed download ───────────────────────

def test_teardown_reports_the_completed_rows_it_leaves_by_default():
    """The DEFAULT teardown leaves the history rows, and must say so.

    THIS TEST'S SUBJECT CHANGED, so its assertion changed with it. It used to
    be named ..._it_cannot_remove and asserted that the residue text contained
    "append-only" or "prune", because db_prune (which deletes by AGE, not by
    marker) was believed to be history's only deleter. That premise was FALSE:
    bulk_downloader/db.py:988-992 records the retraction -- batch_ops.bulk_delete
    issues `DELETE FROM history WHERE id = ?` and is reachable over HTTP at
    POST /api/batch/delete, which is what --clear-history now uses.

    Keeping the old keyword assertion alive by engineering those two words into
    a note that says the OPPOSITE would be a gate passing for the wrong reason
    BY CONSTRUCTION, and it would hand the retracted claim to the next reader as
    authority from a test file. So the assertion below is about the note's
    ACTUAL claim: the row is removable, and the default teardown does not remove
    it. Reporting "removed the sites, cancelled the queue" and stopping would
    still imply the box is as it was found. It is not.
    """
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 0},
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/history?q={seed.SEED_MARKER}&limit=500"): [
            {"id": 7, "url": seed.seeded_url(0), "status": "done",
             "filename": "0.mp4"},
        ],
    })
    plan = seed.teardown(client)
    residue = plan.get("residue")
    assert isinstance(residue, dict), (
        f"teardown reported no residue at all: {plan}")
    assert residue.get("history_rows") == 1, (
        f"teardown did not count the seeded history row it leaves behind: "
        f"{residue}")
    text = str(residue)
    lowered = text.lower()
    assert "--clear-history" in text, (
        f"the residue report does not name the opt-in that DOES remove the "
        f"row, so a reader is left with the retracted 'nothing can remove "
        f"this' reading: {residue}")
    assert ("can be removed" in lowered or "removable" in lowered), (
        f"the residue report does not say the row IS removable. The retracted "
        f"claim (db.py:988-992) was that it is not; a note that neither "
        f"asserts nor denies removability leaves the old reading standing: "
        f"{residue}")
    assert "db.py:988-992" in text, (
        f"the residue report does not cite the retraction it is correcting. "
        f"The citation is the only thing that stops a later reader "
        f"'restoring' the append-only sentence: {residue}")


def test_teardown_says_unknown_when_it_cannot_read_history():
    """An unreadable history is not an empty one."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 0},
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/history?q={seed.SEED_MARKER}&limit=500"): {
            "error": "boom"},
    })
    residue = seed.teardown(client).get("residue") or {}
    assert residue.get("readable") is False, (
        f"an unreadable history was not reported as unreadable: {residue}")
    assert residue.get("history_rows") is None, (
        f"an unreadable history was counted as a number: {residue}")


def test_teardown_still_cancels_and_deletes_after_a_completed_download():
    """The completion must not stop teardown doing the part it CAN do."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {
            "ok": True, "running": [],
            "waiting": [{"id": 4, "url": seed.seeded_url(1)}],
            "done_today_count": 1,
        },
        ("GET", "/api/status"): _status_body(seed),
        ("GET", f"/api/history?q={seed.SEED_MARKER}&limit=500"): [
            {"id": 7, "url": seed.seeded_url(0), "status": "done"},
        ],
    })
    plan = seed.teardown(client)
    assert [p for p, _b in client.posted() if "cancel" in p], (
        f"teardown cancelled nothing: {plan}")
    assert any(MARKED_SID in p for p in client.paths("DELETE")), (
        f"teardown left its own marked site behind: {plan}")
    assert not any(REAL_SID in p for p in client.paths("DELETE")), (
        "teardown deleted the operator's real site")


# ── the refusal a completed seed makes reachable ────────────────────────────

def test_the_preflight_does_not_call_the_seeders_own_completions_real():
    """done_today_count carries no marker, so it cannot be attributed.

    Before anything ever completed, seeded work could not appear in this
    counter and calling it "real download(s)" was sound. A seeder that finishes
    downloads makes the sentence false: the very next --seed on the same host
    refuses, naming the seeder's own completions as the operator's work. The
    refusal itself stays -- erring toward refusing is right -- but it must not
    assert something it cannot know.
    """
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 2},
        ("GET", "/api/status"): _status_body(seed),
    })
    with pytest.raises(seed.SeedRefused) as excinfo:
        seed.preflight(client)
    msg = str(excinfo.value)
    assert "2 real download" not in msg, (
        f"the refusal calls the seeder's own completions real while "
        f"{seed.SEED_MARKER!r}-marked sites are present on the host:\n  {msg}")
    assert seed.SEED_MARKER in msg or "teardown" in msg.lower(), (
        f"the refusal does not tell the operator that seeded state may be "
        f"what it is refusing over:\n  {msg}")


# ── the capture must actually ask for it ────────────────────────────────────

def _capture_seed_invocations():
    lines = []
    for line in CAPTURE_SH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "tools/live_seed.py" in stripped and "--seed" in stripped:
            lines.append(stripped)
    return lines


def test_capture_asks_the_seeder_to_start_what_it_seeds():
    """An opt-in flag nobody passes is a feature nobody has.

    capture.sh is the only caller, and the whole point is that L11/L12/L14
    become exercisable on a capture host without a human queueing work.
    """
    invocations = _capture_seed_invocations()
    assert invocations, "capture.sh no longer invokes the seeder -- anchor stale"
    assert any("--start" in line for line in invocations), (
        "capture.sh seeds but never asks the seeder to start the queue, so "
        "L11/L12/L14 still observe a queue that never ran:\n  "
        + "\n  ".join(invocations))


def test_the_capture_start_is_bounded_so_a_capture_stays_unattended():
    """An unbounded wait inside capture.sh is a hang with no operator present."""
    seed = _load()
    invocations = _capture_seed_invocations()
    bounded = [ln for ln in invocations if "--start" in ln]
    assert bounded, "no --start invocation to bound"
    for line in bounded:
        match = re.search(r"--start-timeout\s+(\S+)", line)
        assert match, (
            f"capture.sh starts the seeded queue with no explicit bound; the "
            f"default would be invisible to anyone reading the capture:\n  {line}")
    assert isinstance(seed.DEFAULT_SETTLE_TIMEOUT, (int, float)), (
        "the seeder has no default settle bound")
    assert 0 < seed.DEFAULT_SETTLE_TIMEOUT <= 600, (
        f"the default settle bound is {seed.DEFAULT_SETTLE_TIMEOUT}s; an "
        f"unattended capture cannot absorb that")


def _first_code_line(predicate):
    """Index of the first NON-COMMENT line satisfying `predicate`, or None.

    Line-based and comment-skipping on purpose. The first version of the test
    below did `raw.find("--start")` over the whole file, and its own mutation
    run caught it: deleting --start from the real invocation left the flag
    named in the explanatory COMMENT above it, so the offset check still
    passed over prose. That is test_capture_seeds_live_input.py's
    _strip_comments lesson arriving a second time -- a gate must not be
    satisfiable by a sentence about the thing.
    """
    for index, line in enumerate(CAPTURE_SH.read_text(encoding="utf-8").splitlines()):
        if line.strip().startswith("#"):
            continue
        if predicate(line):
            return index
    return None


def test_the_start_happens_before_the_live_suite_reads_the_result():
    """Work started after the checks have run is work nothing observed."""
    start_at = _first_code_line(
        lambda ln: "tools/live_seed.py" in ln and "--start" in ln)
    lane_at = _first_code_line(lambda ln: "live_tests.run" in ln)
    assert start_at is not None, (
        "no non-comment line invokes tools/live_seed.py with --start; the "
        "queue is seeded and never run")
    assert lane_at is not None, "capture.sh no longer runs the live lane"
    assert start_at < lane_at, (
        f"--start is invoked on line {start_at}, after the live lane on line "
        f"{lane_at}; the checks would read a queue that had not started")
