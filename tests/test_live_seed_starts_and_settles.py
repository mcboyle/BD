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


# ─────────────────────────────────────────────────────────────────────────────
# --clear-history: the opt-in that removes the rows the default teardown leaves
# ─────────────────────────────────────────────────────────────────────────────
#
# WHY THESE LIVE IN THIS FILE. A new tests/*.py moves nine tree-enumerating
# axis-6 gates plus PIN_INDEX's `test_files_scanned` (CLAUDE.md 4); editing an
# existing one moves none of them.
#
# THE HARNESS TRAP THESE TESTS HAVE TO WORK AROUND. FakeClient keys on an EXACT
# (METHOD, path) tuple and FALLS BACK to {"ok": True} on a miss rather than
# raising. So a fixture whose key does not match the URL the tool builds makes
# the test pass WITHOUT EVER EXERCISING ITS FIXTURE. Every test below therefore
# asserts that the exact path it stubbed was actually requested, so a URL change
# in the tool turns these red instead of green-and-vacuous.
#
# THE OTHER TRAP: on pristine source argparse rejects the unknown
# --clear-history with SystemExit(2). A test that merely asserts "non-zero" or
# "raised" would be satisfied by that -- a pass for the WRONG reason, and one
# that would keep passing on a tree where the feature is fully implemented.
# _run_main() below converts SystemExit into a NAMED failure so the RED is
# always about the subject.


def _run_main(seed, argv):
    """seed.main(argv), with argparse's SystemExit turned into a named failure.

    Discriminate the exception you are hunting (CLAUDE.md 2a). SystemExit(2)
    here means the FLAG does not exist, which is a different fact from the
    tool's exit code and must never be read as one.
    """
    try:
        return seed.main(list(argv))
    except SystemExit as exc:
        pytest.fail(
            f"seed.main({argv!r}) raised SystemExit({exc.code!r}) instead of "
            f"returning an exit code. SystemExit(2) from argparse means "
            f"tools/live_seed.py has no --clear-history option at all -- that "
            f"is the missing FEATURE, not the tool's verdict on the clear, and "
            f"it must not be mistaken for one."
        )


def _owned_row(seed, rid, *, index=0):
    """A history row this tool can PROVE it owns.

    Ownership needs the marker in BOTH the url (which the server-side LIKE
    already filtered on) AND site_name -- db_log stores site_name as a literal
    (db.py:962), so it survives the site being deleted and is the only field
    that still attributes the row afterwards.
    """
    return {"id": rid, "url": seed.seeded_url(index), "status": "done",
            "filename": f"{rid}.mp4",
            "site_name": f"{seed.SEED_MARKER} fixture site"}


def _unowned_row(seed, rid, *, index=0):
    """Marker in the URL, but the site_name says it is the operator's."""
    return {"id": rid, "url": seed.seeded_url(index), "status": "done",
            "filename": f"{rid}.mp4", "site_name": "My Real Site"}


def _browse_body(rows, next_cursor=None):
    """The exact envelope app_library.py:128-129 returns."""
    return {"ok": True, "rows": [dict(r) for r in rows],
            "count": len(rows), "next_cursor": next_cursor}


_BLIND_BROWSE = {"ok": True, "rows": [], "count": 0, "next_cursor": None}
"""The body a MISSING or UNREADABLE library table produces.

library.library_browse wraps its whole query in `except Exception: return
[], None` (library.py:363-364), and app_library.py:128-129 renders that as
rows [] / count 0 / next_cursor None -- byte-identical to a library that
genuinely holds no twins. Over HTTP the two are INDISTINGUISHABLE.
"""

_HISTORY_KEY_SUFFIX = "&limit=500"


def _history_key(seed):
    return ("GET", f"/api/history?q={seed.SEED_MARKER}{_HISTORY_KEY_SUFFIX}")


def _teardown_stubs(seed, history, extra=None):
    """The three reads teardown makes before it can do anything at all.

    `extra` is POSITIONAL and a plain dict, not **kwargs: FakeClient keys on
    (METHOD, path) TUPLES, and `**` requires string keys. The first draft of
    this helper used **kwargs and every caller raised TypeError -- a harness
    defect presenting as a subject failure, which is the shape CLAUDE.md 2a
    warns about.
    """
    stubs = {
        ("GET", "/api/queue/v2"): {"ok": True, "running": [], "waiting": [],
                                   "done_today_count": 0},
        ("GET", "/api/status"): _status_body(seed),
        _history_key(seed): history,
    }
    stubs.update(extra or {})
    return FakeClient(stubs)


def _history_read_indices(seed, client):
    prefix = f"/api/history?q={seed.SEED_MARKER}"
    return [i for i, (m, p, _b) in enumerate(client.calls)
            if m == "GET" and p.startswith(prefix)]


def _batch_delete_indices(client, *, dry_run=None):
    out = []
    for i, (method, path, body) in enumerate(client.calls):
        if method != "POST" or path != "/api/batch/delete":
            continue
        if dry_run is not None and bool((body or {}).get("dry_run")) is not dry_run:
            continue
        out.append(i)
    return out


def _library_delete_indices(client):
    return [i for i, (m, p, _b) in enumerate(client.calls)
            if m == "DELETE" and p.startswith("/api/library/")]


def _library_delete_paths(client):
    return [p for m, p, _b in client.calls
            if m == "DELETE" and p.startswith("/api/library/")]


def _clear_of(plan):
    return (plan or {}).get("clear") or {}


# ── R0: the capability must exist at all (denominator canary) ───────────────

def test_the_clear_capability_exists():
    """Canary: every assertion below is about these two names.

    Without this, a missing feature reaches each test as an AttributeError or
    a SystemExit one at a time and the reason is buried in nine different
    failure modes. Fail here, once, naming the symbol.
    """
    seed = _load()
    for name in ("clear_seeded_history", "_teardown_exit_code"):
        assert callable(getattr(seed, name, None)), (
            f"tools/live_seed.py defines no callable {name!r}. The seeded "
            f"history rows accumulate on every capture (64 at v3.66.844) and "
            f"nothing can remove them, while the residue note claims -- "
            f"falsely, see db.py:988-992 -- that nothing ever could."
        )


# ── R1: a clear that failed must not exit 0 ─────────────────────────────────

def test_a_failed_clear_does_not_exit_zero():
    """main() returned 0 on every teardown outcome except a SeedRefused.

    Measured 2026-08-03 on four distinct failures -- unreadable /api/history,
    unreadable /api/status, a site DELETE answering 500, and 64 rows left
    behind -- all exit 0, two of them printing nothing at all. capture.sh's
    `|| echo WARNING` had therefore never fired in its life. A clear that did
    not happen must be readable from the exit code.
    """
    seed = _load()
    client = _teardown_stubs(
        seed, [_owned_row(seed, 11), _owned_row(seed, 12, index=1)],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
           ("POST", "/api/batch/delete"): [
               # THE CANARY MUST SUCCEED HERE. A single {"ok": False} body for
               # every call answers the dry-run probe too, so `candidates_
               # matched` is absent, the canary aborts the pass, and this test
               # would measure R4's subject (an unusable filter) while claiming
               # to measure its own (a delete the app REJECTED). Reply one is
               # the probe answering honestly; every reply after it rejects.
               _Reply({"ok": True, "candidates_matched": 2, "processed": 0,
                       "files_deleted": 0, "errors": 0, "dry_run": True}),
               _Reply({"ok": False, "error": "boom"})]})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()
    assert _history_read_indices(seed, client), (
        "the tool never read the stubbed /api/history path; this test's "
        "fixture was not exercised and its verdict is about nothing")
    assert _batch_delete_indices(client, dry_run=False), (
        f"no non-dry-run POST to /api/batch/delete was ever made, so the pass "
        f"aborted at the canary and this test's verdict is about an unusable "
        f"filter, not about a delete the app rejected. Calls: "
        f"{[(m, p) for m, p, _b in client.calls]}")
    assert rc == 4, (
        f"a clear whose every /api/batch/delete call was rejected exited "
        f"{rc}. 4 is _EXIT_CLEAR_INCOMPLETE: the clear did not happen and we "
        f"KNOW it. 0 would launder a known failure into an OK, and 5 would "
        f"claim the post-state could not be measured when it could."
    )


# ── R2: the remainder is MEASURED, and the read model is pinned ─────────────

def test_the_remainder_is_measured_after_the_clear_not_computed():
    """found-minus-deleted is not the remainder, and cannot be.

    db_log runs on every job-level transition (db.py:962-965), so a job
    settling during teardown appends a history row AFTER the first read. And
    /api/batch/delete answers a REJECTED filter and a genuine no-op with the
    same body, so its counts cannot settle the question either. The remainder
    has to be re-read.

    THE READ MODEL, pinned rather than assumed. teardown() itself calls
    _seeded_history at live_seed.py:1168 BEFORE any clear runs, so a clear
    driven through teardown performs a MINIMUM of THREE reads: teardown's own,
    the round's, and the post-delete re-read. The spec was written against a
    TWO-read model and its asserted number came from the wrong read.

    The assertions below pin that model WHERE IT IS LOAD-BEARING -- exactly two
    reads before the first /api/batch/delete (teardown's and the round's; ONE
    would mean the clear reused a set read before the site deletes) and at
    least one after -- rather than pinning a total. A total is not a property
    of the design: the round loop reads once per round plus once per
    post-delete check, so any non-zero remainder produces more than three and
    the exact number falls out of the stall detection, not the contract.
    """
    seed = _load()
    rows_before = [_owned_row(seed, 11), _owned_row(seed, 12, index=1),
                   _owned_row(seed, 13)]
    # Read 3 carries id 99, which read 1 never saw: a job that settled while
    # teardown was running. Arithmetic (3 found - 3 processed) says 0.
    rows_after = [_owned_row(seed, 12, index=1), _owned_row(seed, 99)]
    client = _teardown_stubs(
        seed,
        [_Reply(list(rows_before)), _Reply(list(rows_before)),
         _Reply(list(rows_after))],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 3, "processed": 3,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()

    reads = _history_read_indices(seed, client)
    posts = _batch_delete_indices(client)
    assert posts, (
        "the clear never POSTed to /api/batch/delete, so nothing here is "
        "about a clear at all")
    assert len(reads) >= 3, (
        f"the history was read {len(reads)} time(s). A clear driven through "
        f"teardown reads at MINIMUM three times: teardown's own read at "
        f"live_seed.py:1168, the clear round's read, and the post-delete "
        f"re-read. Fewer means one of the three is missing.")
    before = [i for i in reads if i < posts[0]]
    after = [i for i in reads if i > posts[0]]
    assert len(before) == 2, (
        f"{len(before)} history read(s) happened before the first "
        f"/api/batch/delete; expected exactly 2 (teardown's, then the clear "
        f"round's). ONE would mean the clear acted on the set teardown read "
        f"BEFORE the site deletes, which is a different pass from the one it "
        f"reports on.")
    assert after, (
        "no history read happened after the deletes, so the reported "
        "remainder cannot have been measured")

    plan_residue = None
    clear = None
    # Re-drive through teardown() directly for the residue dict, using a fresh
    # client so the sequence starts over.
    client2 = _teardown_stubs(
        seed,
        [_Reply(list(rows_before)), _Reply(list(rows_before)),
         _Reply(list(rows_after))],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 3, "processed": 3,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    plan = seed.teardown(client2, clear_history=True)
    plan_residue = plan.get("residue") or {}
    clear = _clear_of(plan)
    assert plan_residue.get("history_rows_found") == 3, (
        f"history_rows_found is {plan_residue.get('history_rows_found')!r}; "
        f"the clear SAW 3 rows. This key must always mean 'rows the read saw'."
    )
    assert plan_residue.get("history_rows") == 2, (
        f"history_rows is {plan_residue.get('history_rows')!r}; the re-read "
        f"returned 2 rows. This key must always mean 'rows that REMAIN'.")
    assert plan_residue["history_rows"] != 0, (
        "the remainder was reported as 0. found(3) - processed(3) is 0, and "
        "that is exactly the arithmetic this design refuses: the re-read "
        "returned two rows, one of which the first read never saw.")
    assert clear.get("deleted"), (
        "the clear reported deleting nothing while /api/batch/delete answered "
        "processed 3; the report is not about what happened")


# ── R3: two names, two meanings, and neither may drift into the other ───────

def test_the_two_residue_names_do_not_share_a_meaning():
    """One key cannot mean both "saw" and "remain" without changing meaning.

    Before the clear existed the two numbers were always identical, which is
    exactly why one name was enough and is not enough now. A single key would
    silently change what it means between two runs of the same tool.
    """
    seed = _load()
    rows_before = [_owned_row(seed, 11), _owned_row(seed, 12, index=1),
                   _owned_row(seed, 13)]
    rows_after = [_owned_row(seed, 12, index=1), _owned_row(seed, 99)]
    client = _teardown_stubs(
        seed,
        [_Reply(list(rows_before)), _Reply(list(rows_before)),
         _Reply(list(rows_after))],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 3, "processed": 3,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    cleared = (seed.teardown(client, clear_history=True).get("residue") or {})
    assert cleared.get("history_rows_found") != cleared.get("history_rows"), (
        f"after a clear the two residue names carry the same number "
        f"({cleared.get('history_rows_found')!r}); one of them is not "
        f"measuring what its name says: {cleared}")
    assert cleared.get("cleared") is True, (
        f"a teardown that ran the clear did not record that it did: {cleared}")

    # ...and with no clear requested they must still agree, and must match the
    # behaviour this file already pinned.
    plain_client = _teardown_stubs(seed, [_owned_row(seed, 7)])
    plain = (seed.teardown(plain_client).get("residue") or {})
    assert plain.get("history_rows_found") == 1, (
        f"a plain teardown lost the 'rows the read saw' number: {plain}")
    assert plain.get("history_rows") == 1, (
        f"a plain teardown changed the meaning of history_rows: {plain}")
    assert plain.get("readable") is True, plain
    assert plain.get("cleared") is False, (
        f"a teardown that ran no clear claims it cleared: {plain}")


# ── R4: a malformed filter is caught BEFORE anything is deleted ─────────────

def test_a_malformed_filter_is_caught_before_anything_is_deleted():
    """batch_ops answers a rejected filter and a real no-op identically.

    _matching_rows swallows the TypeError from an unexpected filter key and
    returns [] (batch_ops.py:104-110 region), so bulk_delete replies
    candidates_matched 0 / processed 0 / ok True -- the same body a genuinely
    empty match produces. The canary is the only cheap discriminator: ids that
    were JUST read as present must match, so a 0 proves the filter shape is
    wrong.

    The canary runs BEFORE the twin deletes deliberately. If the batch filter
    is broken, deleting the library twins first would strand history rows whose
    twins are already gone -- worse than doing nothing.
    """
    seed = _load()
    client = _teardown_stubs(
        seed, [_owned_row(seed, 11), _owned_row(seed, 12, index=1)],
        {("GET", "/api/library/browse?limit=500"): _browse_body(
               [{"id": 3, "history_id": 11, "title": "t", "file_path": "/x"}]),
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 0, "processed": 0,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()

    posts = _batch_delete_indices(client)
    assert len(posts) == 1, (
        f"the tool made {len(posts)} POST(s) to /api/batch/delete against a "
        f"filter that matched nothing it had just read as present. Exactly "
        f"one is correct: the dry-run canary, after which the pass aborts.")
    canary_body = client.calls[posts[0]][2] or {}
    assert canary_body.get("dry_run") is True, (
        f"the single /api/batch/delete call was not a dry run: {canary_body}. "
        f"A canary that deletes is not a canary.")
    assert not _library_delete_indices(client), (
        f"library twins were deleted ({_library_delete_paths(client)}) despite "
        f"the batch filter being unusable. The canary runs FIRST precisely so "
        f"a broken filter cannot strand history rows whose twins are gone.")

    plan = seed.teardown(
        _teardown_stubs(
            seed, [_owned_row(seed, 11), _owned_row(seed, 12, index=1)],
            {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
               ("POST", "/api/batch/delete"): {
                   "ok": True, "candidates_matched": 0, "processed": 0,
                   "files_deleted": 0, "errors": 0, "dry_run": False}}),
        clear_history=True)
    clear = _clear_of(plan)
    assert clear.get("deleted") == 0, (
        f"the clear reported deleting {clear.get('deleted')!r} rows after the "
        f"canary failed; nothing may be deleted on that path: {clear}")
    errors = " ".join(str(e) for e in (clear.get("errors") or []))
    assert "canary" in errors.lower(), (
        f"the abort did not name the canary, so a reader cannot tell a broken "
        f"filter from an empty table -- which is the one thing the canary "
        f"exists to distinguish: {clear.get('errors')!r}")
    assert rc == 4, (
        f"a clear aborted by the canary exited {rc}; it did not happen and we "
        f"know it, which is _EXIT_CLEAR_INCOMPLETE (4)")


# ── R5: an unreadable post-state is UNKNOWN, and the re-read must HAPPEN ────

def test_an_unreadable_history_after_the_clear_is_unknown_not_zero():
    """Unknown is a third state and it FAILS -- distinctly from incomplete.

    THIS TEST ASSERTS ITS OWN NAME. `remaining` is None in the report's
    initialiser too, so asserting only "it is None" is satisfied by a mutant
    that DELETES the post-clear re-read entirely -- the value would be None
    because nothing ever set it, not because the re-read failed. The read
    COUNT is what discriminates the two: three reads means the re-read was
    attempted, two means it was not.
    """
    seed = _load()
    owned = [_owned_row(seed, 11), _owned_row(seed, 12, index=1)]
    client = _teardown_stubs(
        seed,
        [_Reply(list(owned)), _Reply(list(owned)), _Reply({"error": "x"})],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 2, "processed": 2,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()

    reads = _history_read_indices(seed, client)
    assert len(reads) == 3, (
        f"the history was read {len(reads)} time(s); expected exactly 3 "
        f"(teardown's, the round's, the post-delete re-read -- the third one "
        f"errors, which ends the pass immediately). Two reads means the "
        f"post-clear re-read never happened, and then `remaining is None` is "
        f"the initialiser's value rather than a measurement that failed.")
    posts = _batch_delete_indices(client)
    assert posts and reads[-1] > posts[-1], (
        "the last history read did not happen after the last delete; the "
        "re-read this test is named for is not in the trace")

    plan = seed.teardown(
        _teardown_stubs(
            seed,
            [_Reply(list(owned)), _Reply(list(owned)), _Reply({"error": "x"})],
            {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
               ("POST", "/api/batch/delete"): {
                   "ok": True, "candidates_matched": 2, "processed": 2,
                   "files_deleted": 0, "errors": 0, "dry_run": False}}),
        clear_history=True)
    residue = plan.get("residue") or {}
    assert residue.get("history_rows") is None, (
        f"an unreadable post-clear history was counted as a number: {residue}")
    assert residue.get("readable") is False, (
        f"an unreadable post-clear history was reported as readable: "
        f"{residue}")
    assert rc == 5, (
        f"a clear whose post-state could not be MEASURED exited {rc}. 5 is "
        f"_EXIT_CLEAR_UNKNOWN and it is distinct from 4 on purpose: 4 says "
        f"'it did not fully happen and we know it', 5 says 'we cannot tell'. "
        f"0 would be the unknown laundered into an OK.")


# ── R6: under-deleting is the safe direction ────────────────────────────────

def test_a_row_this_tool_cannot_prove_it_owns_is_not_deleted():
    """Two predicates, both required, and the narrower one authorises.

    The marker in the URL is what the server-side LIKE filtered on. It is not
    proof of ownership: an operator's row whose URL merely contains the marker
    would match. site_name is the second predicate, and a row failing it is
    counted as `unowned` and NEVER deleted. It then keeps the remainder
    non-zero, which the report says out loud rather than hiding.

    THE FIXTURE PAIRS AN OWNED ROW WITH THE UNOWNED ONE, and that is not
    decoration. With the unowned row ALONE the clear finds no deletable id,
    breaks before it ever POSTs, and the payload scan below iterates over an
    EMPTY list of calls -- an assertion whose denominator structurally
    excludes its subject, which reports clean truthfully and uselessly
    (CLAUDE.md 0). Owned id 11 is what makes a real /api/batch/delete happen,
    so "4242 is not in any payload" is a measurement rather than a vacuum.
    """
    seed = _load()
    mixed = [_owned_row(seed, 11), _unowned_row(seed, 4242)]
    leftover = [_unowned_row(seed, 4242)]
    stubs = {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
             ("POST", "/api/batch/delete"): {
                 "ok": True, "candidates_matched": 1, "processed": 1,
                 "files_deleted": 0, "errors": 0, "dry_run": False}}
    client = _teardown_stubs(
        seed,
        [_Reply(list(mixed)), _Reply(list(mixed)), _Reply(list(leftover))],
        dict(stubs))
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()

    posts = _batch_delete_indices(client)
    assert posts, (
        f"the clear made no POST to /api/batch/delete at all, so the payload "
        f"scan below would have had an empty denominator and this test would "
        f"have certified nothing. Owned id 11 is in the fixture precisely so "
        f"a real delete is issued. Calls: "
        f"{[(m, p) for m, p, _b in client.calls]}")
    # Shape-independent: 4242 is distinctive, so a substring scan of every
    # /api/batch/delete payload cannot be fooled by a different filter shape
    # the way a structured `payload["filter"]["id_in"]` lookup silently would.
    for index in posts:
        body = client.calls[index][2]
        assert "4242" not in str(body), (
            f"id 4242 reached /api/batch/delete in {body!r}. Its URL carries "
            f"the marker but its site_name is 'My Real Site', so this tool "
            f"cannot prove the row is its own.")
    assert any("11" in str(client.calls[i][2]) for i in posts), (
        f"id 11 -- the row this tool CAN prove it owns -- reached no "
        f"/api/batch/delete payload either, so the clear under-deleted "
        f"everything and 'it skipped 4242' is not evidence of a predicate: "
        f"{[client.calls[i][2] for i in posts]}")

    plan = seed.teardown(
        _teardown_stubs(
            seed,
            [_Reply(list(mixed)), _Reply(list(mixed)), _Reply(list(leftover))],
            dict(stubs)),
        clear_history=True)
    clear = _clear_of(plan)
    assert clear.get("unowned") == 1, (
        f"the unowned row was not reported as unowned: {clear}")
    assert clear.get("remaining") == 1, (
        f"the remainder is {clear.get('remaining')!r}; a row that was not "
        f"deleted still remains, and saying otherwise would be the clear "
        f"reporting a state it did not produce: {clear}")
    assert clear.get("ok") is False, (
        f"a clear that left a row behind reported ok: {clear}")
    assert rc == 4, (
        f"a clear that could not reach zero exited {rc}, not 4")


# ── R7: GUARD -- passes on pristine BY DESIGN, and that is the point ────────
#
# NOT A RED. This is the guard against anyone flipping the default on, in the
# same category as test_seeding_without_start_still_starts_nothing above: it
# holds today and must keep holding. Labelled explicitly so a reader does not
# count it as evidence the feature exists.

def test_the_clear_is_off_unless_asked_for():
    """GUARD (not a RED): the destructive path must stay opt-in.

    The clear's predicate is the marker across ALL history, not this run's
    nonce, so the first non-dry-run clear removes every row accumulated by
    every previous capture at once. capture.sh runs teardown unattended. A
    default-on clear would make a cron-shaped process delete the operator's
    accumulated state with nobody watching.
    """
    seed = _load()
    client = _teardown_stubs(seed, [_owned_row(seed, 7)])
    plan = seed.teardown(client)
    assert not [p for p, _b in client.posted() if "/api/batch/delete" in p], (
        f"a plain teardown POSTed to /api/batch/delete: {client.posted()}")
    assert not _library_delete_paths(client), (
        f"a plain teardown deleted library rows: "
        f"{_library_delete_paths(client)}")
    assert (plan.get("residue") or {}).get("history_rows") == 1, (
        f"a plain teardown stopped counting the row it leaves: {plan}")
    # NOTE the assertion that is deliberately NOT here. `residue["cleared"] is
    # False` belongs to the NEW contract -- pristine has no such key, so
    # asserting it here would make this file's one always-green guard RED on
    # pristine and it would stop being a guard. test_the_two_residue_names_do
    # _not_share_a_meaning already pins it, as a RED, which is where a new-key
    # assertion belongs.

    client2 = _teardown_stubs(seed, [_owned_row(seed, 7)])
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client2)
        rc = seed.main(["--teardown"])
    finally:
        monkey.undo()
    assert rc == 0, (
        f"a teardown with no clear requested exited {rc}; the new exit codes "
        f"are SCOPED TO THE CLEAR and must not fire on a plain teardown")
    assert not [p for p, _b in client2.posted() if "/api/batch/delete" in p]
    assert not _library_delete_paths(client2)


# ── R8/R9: capture.sh -- the wiring, and the exit code it must read ────────
#
# Cut on STRUCTURE, never a fixed width. A harness that sliced a shell branch
# by width once swallowed its closing `fi` and produced a bash syntax error
# presenting as a subject failure (CLAUDE.md 2a). And no `src[pos:pos+N]`
# source window anywhere: test_source_windows_do_not_shift.py ratchets the
# COUNT of those and is one-directional.


def _capture_logical_lines():
    """capture.sh with backslash continuations resolved into logical lines.

    Same join the sibling gate uses (test_capture_requests_every_seeding_mode
    .py:103). A line-at-a-time reader sees only part of an invocation that
    spans lines, which is a denominator that excludes its own subject.
    """
    body = re.sub(r"\\\n\s*", " ", CAPTURE_SH.read_text(encoding="utf-8"))
    return body.splitlines()


def _cleanup_live_seed_body():
    """cleanup_live_seed's lines, cut at its own closing brace.

    STRUCTURAL: the opener is the function header, the closer is the first
    line that is a bare `}` at column 0 (bash's own formatting for a function
    close in this file). The brace balance of the result is then asserted, so
    an extractor that cut mid-construct FAILS SAYING SO instead of handing a
    truncated body to assertions that would report a subject failure.
    """
    lines = _capture_logical_lines()
    starts = [i for i, ln in enumerate(lines)
              if ln.startswith("cleanup_live_seed()")]
    if not starts:
        pytest.fail(
            "capture.sh no longer defines cleanup_live_seed at column 0; this "
            "gate's anchor is stale and it cannot answer about its subject")
    start = starts[0]
    closer = None
    for i in range(start + 1, len(lines)):
        if re.fullmatch(r"\}\s*", lines[i]):
            closer = i
            break
    if closer is None:
        pytest.fail(
            "cleanup_live_seed has no closing `}` at column 0, so this gate "
            "cannot cut it on structure. Refusing to guess a width.")
    body = lines[start:closer + 1]
    text = "\n".join(body)
    assert text.count("{") == text.count("}"), (
        f"the structural cut of cleanup_live_seed is unbalanced "
        f"({text.count('{')} open, {text.count('}')} close), so it lands "
        f"mid-construct and nothing asserted over it means anything:\n{text}")
    return body


def _seeder_invocation_lines(lines):
    """Lines that RUN the seeder, not lines that mention it.

    The predicate is the interpreter at the START of the command. The WARNING
    echo the failure branch prints also contains the seeder's path -- as
    PROSE, telling the operator what to re-run -- and a gate satisfiable by a
    sentence about the thing is not a gate.
    """
    return [ln.strip() for ln in lines
            if ln.strip().startswith("venv/bin/python")
            and "tools/live_seed.py" in ln]


def test_capture_does_not_arm_the_clear_by_default():
    """The capture must wire the clear, and must leave it OFF.

    An opt-in flag nobody can pass is a feature nobody has; a flag hardcoded
    into the unattended caller is a destructive default. Both are wrong, and
    the two assertions below are deliberately in tension so neither can be
    satisfied by dropping the other.
    """
    body = _cleanup_live_seed_body()
    text = "\n".join(body)
    invocations = _seeder_invocation_lines(body)
    assert len(invocations) == 1, (
        f"cleanup_live_seed runs the seeder {len(invocations)} time(s); this "
        f"gate reads exactly one teardown invocation:\n  "
        + "\n  ".join(invocations))
    call = invocations[0]
    assert "--teardown" in call, f"the invocation is not a teardown: {call}"
    assert "$_clear_flag" in call, (
        f"the teardown invocation carries no $_clear_flag, so "
        f"BD_SEED_CLEAR_HISTORY cannot reach the seeder and --clear-history "
        f"is a feature the capture can never ask for:\n  {call}")
    assert '"$_clear_flag"' not in call and "'$_clear_flag'" not in call, (
        f"$_clear_flag is QUOTED in the teardown invocation:\n  {call}\n"
        f"The expansion is intentionally unquoted (same idiom as $_seed_force "
        f"at the seed step) so an EMPTY value adds no argument. Quoted, the "
        f"unarmed default passes a literal empty-string argument that "
        f"argparse rejects with exit 2, so every default capture teardown "
        f"would fail. The cut #7 mutation battery proved the previous "
        f"assertions could not see this change.")
    guard = [ln.strip() for ln in body
             if "BD_SEED_CLEAR_HISTORY" in ln and not ln.strip().startswith("#")]
    assert guard, (
        "cleanup_live_seed never reads BD_SEED_CLEAR_HISTORY outside a "
        "comment; the switch is documented and not wired")
    assert any("${BD_SEED_CLEAR_HISTORY:-0}" in ln for ln in guard), (
        f"BD_SEED_CLEAR_HISTORY is read without the :-0 default, so an unset "
        f"variable's behaviour is whatever `[` does with an empty string "
        f"rather than a stated OFF:\n  " + "\n  ".join(guard))
    assert any('_clear_flag="--clear-history"' in ln for ln in guard), (
        f"nothing in the guard assigns --clear-history to _clear_flag, so the "
        f"flag variable is wired to nothing:\n  " + "\n  ".join(guard))
    # ...and the destructive direction, which is the half that must NOT hold.
    assert "--clear-history" not in call, (
        f"the unattended teardown invocation carries a LITERAL "
        f"--clear-history:\n  {call}\nThe clear's predicate is the marker "
        f"across ALL history, not this run's nonce, so this deletes every "
        f"bdseed row every previous capture left, with nobody watching.")
    assert '_clear_flag=""' in text, (
        f"_clear_flag is never initialised to empty, so an inherited "
        f"environment variable of that name would arm the clear:\n{text}")


def test_the_teardown_exit_code_reaches_capture_sh():
    """A produced exit code nobody reads is a code nobody has.

    tools/live_seed.py grew exit codes 4 and 5 for this caller. capture.sh
    must CAPTURE the status into a variable and branch on it, and the
    diagnostic grep must run inside the TEARDOWN's failure branch -- the
    identical grep at step [5a] runs at SEED time against a log the teardown
    has not written to yet (the seed truncates with `>`, the teardown appends
    with `>>` about 125 lines later), so it can never carry a teardown reason.
    """
    body = _cleanup_live_seed_body()
    text = "\n".join(body)
    capture_lines = [(i, ln.strip()) for i, ln in enumerate(body)
                     if re.search(r"^\s*\w+=\$\?\s*$", ln)]
    assert capture_lines, (
        f"cleanup_live_seed never captures `$?` into a variable, so the "
        f"seeder's exit code is discarded and codes 4 and 5 reach nobody:\n"
        f"{text}")
    index, assignment = capture_lines[0]
    var = assignment.split("=", 1)[0]
    invocations = [i for i, ln in enumerate(body)
                   if ln.strip().startswith("venv/bin/python")
                   and "tools/live_seed.py" in ln]
    assert invocations and invocations[0] < index, (
        f"`{assignment}` does not follow the seeder invocation, so it "
        f"captures some other command's status:\n{text}")

    branch_starts = [i for i, ln in enumerate(body)
                     if ln.strip().startswith("if ") and f"${var}" in ln]
    assert branch_starts, (
        f"nothing branches on ${var}; the exit code is captured and then "
        f"ignored, which reads as handled and is not:\n{text}")
    block = _if_block(body, branch_starts[0])
    assert block is not None, (
        f"the `if` testing ${var} has no matching `fi`, so this gate cannot "
        f"cut it on structure and refuses to guess:\n{text}")
    block_text = "\n".join(block)
    assert "-ne 0" in block_text or "!= 0" in block_text or "-gt 0" in block_text, (
        f"the branch on ${var} does not test it against zero:\n{block_text}")
    assert "live_seed: " in block_text and "grep" in block_text, (
        f"the failure branch prints no `grep 'live_seed: '` diagnostic, so a "
        f"non-zero teardown exit produces a warning with no reason attached. "
        f"Step [5a]'s identical grep cannot cover this: it runs at SEED time, "
        f"before the teardown has appended anything to the log:\n{block_text}")
    assert "tail" in block_text, (
        f"the diagnostic does not use `tail`. The teardown APPENDS with `>>` "
        f"to a log the seed already truncated with `>`, so its lines are the "
        f"LAST ones in the file and `head` would print the seed's:\n"
        f"{block_text}")


def _if_block(lines, start_index):
    """Lines of the if/fi construct opening at `start_index`, cut on its `fi`.

    Structural, and it returns None rather than a truncated block when the
    construct does not close -- unknown is a third state and the caller fails
    on it instead of asserting over a fragment.
    """
    depth = 0
    for i in range(start_index, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("if ") or stripped == "if":
            depth += 1
        if stripped == "fi" or stripped.startswith("fi ") or stripped == "fi;":
            depth -= 1
            if depth == 0:
                return lines[start_index:i + 1]
    return None


# ── R10-R13: the library twins ──────────────────────────────────────────────
#
# 15.23 decision 1 REVERSES the spec's record-only default: the twins are
# deleted. They are 100% of the Library panel's "missing" count and 63% of the
# library table, and every one is bdseed residue.
#
# ORDER IS THE WHOLE POINT. library rows carry history_id, and PRAGMA
# foreign_keys is never enabled (library.py:538-543 states it outright: "which
# we don't enable globally"), so `history_id -> history(id) ON DELETE SET NULL`
# does NOT fire. Delete the history rows first and every twin is left pointing
# at an id that no longer exists.
#
# Derivation is by history_id MEMBERSHIP, never by text: browse's q= searches
# title/file_path/notes ONLY (library.py:326-330), so no marker query can find
# a twin.


def test_the_twins_are_deleted_before_the_history_rows():
    """The ordering IS the feature, so the ordering is what is asserted.

    Asserting only that both deletes happened would pass on the sequence that
    leaves every twin dangling, which is the exact failure 15.23 named. The
    assertion is therefore on the POSITION of each call in client.calls.
    """
    seed = _load()
    owned = [_owned_row(seed, 7), _owned_row(seed, 8, index=1)]
    page = [{"id": 3, "history_id": 7, "title": "a", "file_path": "/a"},
            {"id": 4, "history_id": 8, "title": "b", "file_path": "/b"},
            {"id": 5, "history_id": 99, "title": "c", "file_path": "/c"},
            {"id": 6, "history_id": None, "title": "d", "file_path": "/d"}]
    survivors = [r for r in page if r["id"] in (5, 6)]
    client = _teardown_stubs(
        seed,
        [_Reply(list(owned)), _Reply(list(owned)), _Reply([])],
        {("GET", "/api/library/browse?limit=500"): [
               _Reply(_browse_body(page)), _Reply(_browse_body(survivors))],
           ("DELETE", "/api/library/3"): {"ok": True, "deleted_row": True,
                                          "thumbs_removed": 0},
           ("DELETE", "/api/library/4"): {"ok": True, "deleted_row": True,
                                          "thumbs_removed": 0},
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 2, "processed": 2,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()

    assert "/api/library/browse?limit=500" in client.paths("GET"), (
        f"the tool never GET the stubbed browse path, so this test's twin "
        f"fixture was never exercised. Paths seen: {client.paths('GET')}")
    deleted = _library_delete_paths(client)
    assert "/api/library/3" in deleted and "/api/library/4" in deleted, (
        f"the twins of the doomed history ids were not deleted: {deleted}")
    assert "/api/library/5" not in deleted, (
        f"library row 5 was deleted; its history_id is 99, which is not in "
        f"the doomed set. That is the operator's row: {deleted}")
    assert "/api/library/6" not in deleted, (
        f"library row 6 was deleted; its history_id is None, so nothing "
        f"attributes it to this tool: {deleted}")

    twin_indices = _library_delete_indices(client)
    real_deletes = _batch_delete_indices(client, dry_run=False)
    assert real_deletes, (
        "no non-dry-run POST to /api/batch/delete happened, so there is no "
        "ordering to check and the history rows were never removed")
    assert max(twin_indices) < min(real_deletes), (
        f"a library twin was deleted at call index {max(twin_indices)}, AFTER "
        f"the history delete at {min(real_deletes)}. Foreign keys are off "
        f"(library.py:538-543), so ON DELETE SET NULL does not fire and every "
        f"twin removed in that order was pointing at a history id that no "
        f"longer existed. Calls: "
        f"{[(m, p) for m, p, _b in client.calls]}")

    plan = seed.teardown(
        _teardown_stubs(
            seed,
            [_Reply(list(owned)), _Reply(list(owned)), _Reply([])],
            {("GET", "/api/library/browse?limit=500"): [
                   _Reply(_browse_body(page)),
                   _Reply(_browse_body(survivors))],
               ("DELETE", "/api/library/3"): {"ok": True, "deleted_row": True},
               ("DELETE", "/api/library/4"): {"ok": True, "deleted_row": True},
               ("POST", "/api/batch/delete"): {
                   "ok": True, "candidates_matched": 2, "processed": 2,
                   "files_deleted": 0, "errors": 0, "dry_run": False}}),
        clear_history=True)
    twins = _clear_of(plan).get("twins") or {}
    assert twins.get("matched") == 2, (
        f"the twin scan matched {twins.get('matched')!r} of the 2 rows whose "
        f"history_id is in the doomed set. `matched` is the scan's own "
        f"finding and the final verification pass must not clobber it -- it "
        f"writes `remaining` and nothing else: {twins}")
    assert twins.get("deleted") == 2, (
        f"the report claims {twins.get('deleted')!r} twins deleted: {twins}")
    assert rc == 0, (
        f"a clear that removed both twins and both history rows, and "
        f"re-measured zero of each, exited {rc}")


def test_a_blind_library_scan_is_unknown_not_no_twins():
    """A gate that cannot see its subject must SAY so -- and must not cry wolf.

    library.library_browse wraps its whole query in `except Exception: return
    [], None` (library.py:363-364). A missing library table (migration 4 never
    ran), an unreadable one, and one holding no twins at all produce the
    IDENTICAL HTTP body. Reporting "0 twins" over that body is the
    gate-that-cannot-see-its-subject failure verbatim.

    AND THE INVERSE. An absent library table is a legitimate state, so failing
    every clear on it would be over-sensitivity -- which CLAUDE.md 0 counts as
    a soundness bug, because a gate that cries wolf gets switched off. The
    warning is the loud, honest artifact; the exit code stays 0.
    """
    seed = _load()
    owned = [_owned_row(seed, 11), _owned_row(seed, 12, index=1)]
    stubs = {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
             ("POST", "/api/batch/delete"): {
                 "ok": True, "candidates_matched": 2, "processed": 2,
                 "files_deleted": 0, "errors": 0, "dry_run": False}}
    client = _teardown_stubs(
        seed,
        [_Reply(list(owned)), _Reply(list(owned)), _Reply([])], stubs)
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()
    assert "/api/library/browse?limit=500" in client.paths("GET"), (
        f"the twin scan never ran, so 'no twins' is not even an unverified "
        f"claim -- it is an unasked question: {client.paths('GET')}")
    assert not _library_delete_paths(client), (
        f"library rows were deleted off a scan that saw nothing: "
        f"{_library_delete_paths(client)}")

    plan = seed.teardown(
        _teardown_stubs(
            seed,
            [_Reply(list(owned)), _Reply(list(owned)), _Reply([])], stubs),
        clear_history=True)
    clear = _clear_of(plan)
    twins = clear.get("twins") or {}
    assert twins.get("rows_scanned") == 0, (
        f"the scan claims to have examined rows it was never given: {twins}")
    assert twins.get("conclusive") is False, (
        f"a scan that saw zero rows reported itself as conclusive: {twins}. "
        f"Over this API an unreadable library and an empty one are the same "
        f"body, so 'no twins' is UNVERIFIED, not measured.")
    warnings = " ".join(str(w) for w in (twins.get("warnings")
                                         or clear.get("warnings") or []))
    assert warnings, (
        f"the blind scan produced no warning at all, so the report reads as a "
        f"clean sweep: {clear}")
    assert "library.py:363-364" in warnings or "indistinguishable" in warnings.lower(), (
        f"the warning does not explain WHY the answer is unknown, so a reader "
        f"sees a hedge rather than the mechanism (library_browse swallows "
        f"every exception into ([], None)): {warnings}")
    assert not twins.get("errors"), (
        f"a blind twin scan was recorded as an ERROR: {twins}. An absent "
        f"library table is a legitimate state; failing every clear on it is "
        f"the over-sensitivity CLAUDE.md 0 calls a soundness bug.")
    assert rc == 0, (
        f"a clear that removed every history row and re-measured zero exited "
        f"{rc} because the twin scan was blind. The blind scan WARNS; it does "
        f"not fail the clear.")


def test_the_twin_scan_follows_the_cursor():
    """One page is not the library.

    /api/library/browse clamps limit to 1..500 (app_library.py:105) and pages
    by after_id (`l.id < after_id` for the default descending sort,
    library.py:334-336); next_cursor is non-None only on a FULL page
    (library.py:360-361). A scan that reads page one and stops sees the newest
    500 rows and reports every older twin as absent -- a denominator that
    excludes most of its subject.
    """
    seed = _load()
    owned = [_owned_row(seed, 7), _owned_row(seed, 8, index=1),
             _owned_row(seed, 9)]
    page1 = [{"id": 42, "history_id": 7, "title": "a", "file_path": "/a"},
             {"id": 41, "history_id": 8, "title": "b", "file_path": "/b"}]
    page2 = [{"id": 20, "history_id": 9, "title": "c", "file_path": "/c"}]
    # The FINAL twin-verification pass re-calls _twin_scan with no cursor, so
    # the no-cursor key is consumed a SECOND time. This reply must be a REAL
    # page (one surviving, unrelated row) rather than _BLIND_BROWSE: a blind
    # body makes the verification inconclusive, `remaining` stays None, and
    # the conclusive/zero-remaining path of a multi-page clear is never
    # exercised by a passing test -- the fixture-fidelity gap the cut #7
    # attack pass found. rows_scanned > 0 is what makes the scan conclusive
    # by design; an EMPTY page cannot confirm anything (see _twin_scan).
    verify_page = [{"id": 9000, "history_id": 555555,
                    "title": "operator", "file_path": "/keep"}]
    client = _teardown_stubs(
        seed,
        [_Reply(list(owned)), _Reply(list(owned)), _Reply([])],
        {("GET", "/api/library/browse?limit=500"): [
               _Reply(_browse_body(page1, next_cursor=40)),
               _Reply(_browse_body(verify_page))],
           ("GET", "/api/library/browse?limit=500&after_id=40"):
               _browse_body(page2, next_cursor=None),
           ("DELETE", "/api/library/42"): {"ok": True, "deleted_row": True},
           ("DELETE", "/api/library/41"): {"ok": True, "deleted_row": True},
           ("DELETE", "/api/library/20"): {"ok": True, "deleted_row": True},
           ("POST", "/api/batch/delete"): {
               "ok": True, "candidates_matched": 3, "processed": 3,
               "files_deleted": 0, "errors": 0, "dry_run": False}})
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()

    gets = client.paths("GET")
    assert "/api/library/browse?limit=500" in gets, (
        f"the first browse page was never requested: {gets}")
    assert "/api/library/browse?limit=500&after_id=40" in gets, (
        f"the scan stopped at page one. next_cursor was 40 and the tool never "
        f"followed it, so every library row older than the first page is "
        f"invisible to the twin derivation: {gets}")
    deleted = _library_delete_paths(client)
    for lid in ("42", "41", "20"):
        assert f"/api/library/{lid}" in deleted, (
            f"twin {lid} never entered the match set; row 20 is on PAGE TWO, "
            f"which is the whole subject of this test: {deleted}")

    plan = seed.teardown(
        _teardown_stubs(
            seed,
            [_Reply(list(owned)), _Reply(list(owned)), _Reply([])],
            {("GET", "/api/library/browse?limit=500"): [
                   _Reply(_browse_body(page1, next_cursor=40)),
                   _Reply(_browse_body(verify_page))],
               ("GET", "/api/library/browse?limit=500&after_id=40"):
                   _browse_body(page2, next_cursor=None),
               ("DELETE", "/api/library/42"): {"ok": True, "deleted_row": True},
               ("DELETE", "/api/library/41"): {"ok": True, "deleted_row": True},
               ("DELETE", "/api/library/20"): {"ok": True, "deleted_row": True},
               ("POST", "/api/batch/delete"): {
                   "ok": True, "candidates_matched": 3, "processed": 3,
                   "files_deleted": 0, "errors": 0, "dry_run": False}}),
        clear_history=True)
    twins = _clear_of(plan).get("twins") or {}
    assert twins.get("matched") == 3, (
        f"the scan matched {twins.get('matched')!r} twins across two pages; "
        f"all three doomed history ids have one: {twins}")
    assert twins.get("rows_scanned") == 3, (
        f"the scan reports {twins.get('rows_scanned')!r} rows examined; it "
        f"was handed 2 on page one and 1 on page two: {twins}")
    assert twins.get("pages") == 2, (
        f"the scan reports {twins.get('pages')!r} page(s); it followed one "
        f"cursor, so it read two: {twins}")
    assert twins.get("remaining") == 0, (
        f"twins['remaining'] is {twins.get('remaining')!r} after a clear "
        f"whose final verification scanned a real page and matched no doomed "
        f"id. The final verification pass is the only writer of this key; "
        f"None here means that pass never ran or came back inconclusive, and "
        f"a multi-page clear would ship with its zero-remaining path "
        f"unverified: {twins}")


def test_a_failed_twin_delete_fails_the_clear():
    """A twin the tool tried and failed to delete is not a twin it removed.

    A DELETE answering ok:false is a DIFFERENT fact from one answering "not
    found" -- the second means another writer got there first, which is a
    warning. This one is a db error, and swallowing it would let the report
    say the panel is clean while the row is still in it.
    """
    seed = _load()
    owned = [_owned_row(seed, 7)]
    stubs = {
        ("GET", "/api/library/browse?limit=500"): _browse_body(
            [{"id": 3, "history_id": 7, "title": "a", "file_path": "/a"}]),
        ("DELETE", "/api/library/3"): {"ok": False, "deleted_row": False,
                                       "error": "db error: locked"},
        ("POST", "/api/batch/delete"): {
            "ok": True, "candidates_matched": 1, "processed": 1,
            "files_deleted": 0, "errors": 0, "dry_run": False},
    }
    client = _teardown_stubs(
        seed, [_Reply(list(owned)), _Reply(list(owned)), _Reply([])], stubs)
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()
    assert "/api/library/3" in _library_delete_paths(client), (
        f"the failing DELETE was never attempted, so this test's fixture was "
        f"not exercised: {client.paths('DELETE')}")

    plan = seed.teardown(
        _teardown_stubs(
            seed, [_Reply(list(owned)), _Reply(list(owned)), _Reply([])],
            stubs),
        clear_history=True)
    clear = _clear_of(plan)
    twins = clear.get("twins") or {}
    errors = " ".join(str(e) for e in (twins.get("errors") or []))
    assert errors, (
        f"a DELETE that answered ok:false left no error in the twin report: "
        f"{twins}")
    assert "locked" in errors, (
        f"the twin error does not carry the response the app actually gave, "
        f"so the operator cannot tell a locked database from a missing row: "
        f"{errors}")
    assert clear.get("ok") is False, (
        f"the clear reported ok while a twin it targeted is still there: "
        f"{clear}")
    assert rc == 4, (
        f"a clear that failed to delete a twin exited {rc}; the failure is "
        f"KNOWN, which is _EXIT_CLEAR_INCOMPLETE (4), not unknown (5)")


# ── mutation-escape closures (cut #7 attack pass) ───────────────────────────
#
# Each test below exists because a specific, VALID mutant of the shipped
# source passed the whole band (measured 2026-08-03, 39/39 green under the
# mutation). The mutant is named in the docstring; the test is written so
# that exact change turns it red. Closing an escape means adding the missing
# test, never deleting the behaviour.


def _canary_reply(n, *, dry_run=True):
    return _Reply({"ok": True, "candidates_matched": n, "processed": 0,
                   "files_deleted": 0, "errors": 0, "dry_run": dry_run})


def _delete_reply(n):
    return _Reply({"ok": True, "candidates_matched": n, "processed": n,
                   "files_deleted": 0, "errors": 0, "dry_run": False})


def _clear_client(seed, history_replies, batch_replies):
    """A client for driving clear_seeded_history DIRECTLY (no teardown read).

    The blind browse body keeps the twin scan inconclusive-but-warned, which
    the exit-code contract deliberately tolerates; these tests are about the
    history side.
    """
    return FakeClient({
        _history_key(seed): history_replies,
        ("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
        ("POST", "/api/batch/delete"): batch_replies,
    })


def test_every_batch_delete_payload_carries_a_full_limit():
    """ESCAPE CLOSED: hardcoding either POST's `limit` to 1 passed the band.

    batch_ops._build_query appends `LIMIT ?` AFTER the IN clause, so an
    undersized limit silently drops ids (measured: 6 ids with limit=2 matched
    2). FakeClient keys replies on (method, path) and never inspects the
    payload, so no path-level assertion can see this value: the payloads
    themselves are the subject, and this test reads them.
    """
    seed = _load()
    rows = [_owned_row(seed, rid, index=i % 2)
            for i, rid in enumerate((11, 12, 13, 14, 15))]
    client = _clear_client(
        seed,
        [_Reply(list(rows)), _Reply([])],
        [_canary_reply(2), _delete_reply(2), _delete_reply(2),
         _delete_reply(1)])
    out = seed.clear_seeded_history(client, chunk=2)
    posts = _batch_delete_indices(client)
    assert len(posts) == 4, (
        f"expected 4 POSTs to /api/batch/delete (1 canary + ceil(5/2) "
        f"chunks); got {len(posts)}: "
        f"{[(m, p) for m, p, _b in client.calls]}")
    for index in posts:
        body = client.calls[index][2] or {}
        filt = body.get("filter") or {}
        ids = filt.get("id_in")
        assert isinstance(ids, list) and ids, (
            f"a /api/batch/delete payload carries no id_in list -- the "
            f"denominator of this test's limit check would be empty and its "
            f"verdict about nothing: {body}")
        assert filt.get("limit") == len(ids), (
            f"a /api/batch/delete payload's limit is {filt.get('limit')!r} "
            f"for {len(ids)} ids: {body}. _build_query appends `LIMIT ?` "
            f"after the IN clause, so an undersized limit silently drops "
            f"ids in production with nothing in this band to notice.")
    canary_body = client.calls[posts[0]][2] or {}
    assert canary_body.get("dry_run") is True and \
        len((canary_body.get("filter") or {}).get("id_in") or []) == 2, (
        f"the first POST is not the 2-id dry-run canary: {canary_body}")
    assert out["deleted"] == 5 and out["remaining"] == 0, out


def test_a_degenerate_chunk_is_coerced_not_crashed():
    """ESCAPE CLOSED: dropping the max(1, ...) chunk lower bound passed.

    Without the bound, chunk=0 reaches range(0, len(ids), 0) and raises
    ValueError mid-clear -- after the canary, before any delete. The bound
    coerces it to 1, which is a defined, safe outcome; this test pins it.
    """
    seed = _load()
    rows = [_owned_row(seed, 21), _owned_row(seed, 22, index=1)]
    client = _clear_client(
        seed,
        [_Reply(list(rows)), _Reply([])],
        [_canary_reply(1), _delete_reply(1), _delete_reply(1)])
    out = seed.clear_seeded_history(client, chunk=0)
    assert out["deleted"] == 2 and out["remaining"] == 0 and out["ok"], (
        f"a chunk of 0 must be coerced to 1 and the clear completed: {out}")
    for index in _batch_delete_indices(client, dry_run=False):
        ids = ((client.calls[index][2] or {}).get("filter") or {}).get("id_in")
        assert ids and len(ids) == 1, (
            f"chunk=0 was not coerced to 1-id batches: {client.calls[index]}")


def test_round_exhaustion_stops_at_max_rounds_and_reports_the_stall():
    """ESCAPE CLOSED: an off-by-one capping the loop at max_rounds-1 passed.

    The bound protects an unattended capture.sh clear (the code comment
    claims 20 rounds x 500 rows = 10k). This scripts max_rounds=3 with a
    remainder that shrinks every round but never reaches zero, and pins the
    EXACT round count at exhaustion plus the stalled flag that exhaustion
    must raise.
    """
    seed = _load()

    def rows_n(n):
        return [_owned_row(seed, 100 + i, index=i % 2) for i in range(n)]

    client = _clear_client(
        seed,
        [_Reply(rows_n(4)), _Reply(rows_n(3)),   # round 1: top, re-read
         _Reply(rows_n(3)), _Reply(rows_n(2)),   # round 2
         _Reply(rows_n(2)), _Reply(rows_n(1))],  # round 3
        [_canary_reply(4),
         _Reply({"ok": True, "candidates_matched": 0, "processed": 0,
                 "files_deleted": 0, "errors": 0, "dry_run": False})])
    out = seed.clear_seeded_history(client, max_rounds=3)
    assert out["rounds"] == 3, (
        f"the clear ran {out['rounds']} round(s) with max_rounds=3 and a "
        f"never-empty remainder; the loop bound is off: {out}")
    assert out["stalled"] is True, (
        f"round exhaustion with rows remaining did not report stalled: {out}")
    assert out["remaining"] == 1 and out["ok"] is False, out


def test_a_stalled_round_is_detected_on_equality():
    """ESCAPE CLOSED: weakening the stall boundary >= to > passed the band.

    Two consecutive rounds with an IDENTICAL non-zero remainder is the
    textbook stall (a delete that reports success and removes nothing), and
    it sits exactly on the == half of the >= boundary. Under the > mutant
    the loop spins to max_rounds instead of stopping at round 2, which the
    exact rounds assertion below turns red.
    """
    seed = _load()
    rows = [_owned_row(seed, 31), _owned_row(seed, 32, index=1)]
    client = _clear_client(
        seed,
        [_Reply(list(rows))],   # the last reply repeats: every read sees 2
        [_canary_reply(2),
         _Reply({"ok": True, "candidates_matched": 0, "processed": 0,
                 "files_deleted": 0, "errors": 0, "dry_run": False})])
    out = seed.clear_seeded_history(client)
    assert out["stalled"] is True, (
        f"two rounds with the same non-zero remainder did not stall: {out}")
    assert out["rounds"] == 2, (
        f"the stall was detected after {out['rounds']} round(s); equality "
        f"must trip it on the SECOND round, not after the loop spins to "
        f"max_rounds: {out}")
    assert out["remaining"] == 2 and out["ok"] is False, out


def test_the_post_delete_reread_is_fresh_within_a_single_round():
    """ESCAPE CLOSED: reusing the round's pre-delete rows as the re-read
    passed the band -- including the two tests written to pin the read model.

    Those tests script three teardown-driven reads, and the mutant's missing
    per-round re-read was masked by ROUND TWO's top-of-loop read consuming
    the third reply in its place: the total read count still satisfied >=3.
    This fixture scripts a SINGLE round driven directly, so there is no
    later round to launder the missing read: the mutant reports remaining=2
    off stale rows, enters a second round, and finishes with rounds=2.
    """
    seed = _load()
    rows = [_owned_row(seed, 41), _owned_row(seed, 42, index=1)]
    client = _clear_client(
        seed,
        [_Reply(list(rows)), _Reply([])],
        [_canary_reply(2), _delete_reply(2)])
    out = seed.clear_seeded_history(client)
    reads = _history_read_indices(seed, client)
    posts = _batch_delete_indices(client, dry_run=False)
    assert out["rounds"] == 1, (
        f"a clear whose one round deleted everything ran {out['rounds']} "
        f"round(s). A second round means the post-delete re-read did not "
        f"happen inside round one -- its remainder came from the stale "
        f"pre-delete rows, and the loop had to read again to find out: {out}")
    assert len(reads) == 2, (
        f"the history was read {len(reads)} time(s); a single direct-driven "
        f"round reads exactly twice (top-of-round, post-delete): "
        f"{[(m, p) for m, p, _b in client.calls]}")
    assert posts and reads[-1] > posts[-1], (
        "the read that produced `remaining` did not happen after this "
        "round's deletes; the remainder was not re-measured")
    assert out["remaining"] == 0 and out["ok"] is True, out


def test_a_plan_missing_its_clear_is_incomplete_not_ok():
    """ESCAPE CLOSED: deleting the `if not clear` branch passed the band.

    A requested clear whose plan carries no clear dict at all is a clear
    that KNOWABLY did not happen -- code 4, not 5: nothing was attempted, so
    there is no unmeasurable post-state to be unknown about. Without the
    branch the empty dict falls through to the remaining_readable check and
    reports 5, claiming a measurement failed that was never attempted.
    """
    seed = _load()
    assert seed._teardown_exit_code({}, clear_requested=True) == 4, (
        "a requested clear with no clear dict in the plan must exit 4 "
        "(_EXIT_CLEAR_INCOMPLETE): it did not happen and we know it")
    assert seed._teardown_exit_code(None, clear_requested=True) == 4, (
        "a None plan with a requested clear must exit 4, not raise and not 5")
    assert seed._teardown_exit_code({}, clear_requested=False) == 0, (
        "with no clear requested the exit code is scoped away entirely")


def test_the_residue_report_prints_unknown_only_when_unknown(capsys):
    """ESCAPE CLOSED: inverting `if rows is None` printed exactly backwards
    UNKNOWN/known messages and passed; deleting the known-count branch also
    passed. No test read _report_residue's output at all.

    The stderr lines are capture.sh's diagnostic surface (its failure branch
    greps 'live_seed: '), so a backwards or silent report is an operator
    reading the wrong fact off the box.
    """
    seed = _load()
    seed._report_residue({"residue": {"history_rows": None,
                                      "history_rows_found": None,
                                      "readable": False, "cleared": False}})
    err = capsys.readouterr().err
    assert "RESIDUE UNKNOWN" in err, (
        f"an unreadable history printed no RESIDUE UNKNOWN line: {err!r}")

    seed._report_residue({"residue": {"history_rows": 3,
                                      "history_rows_found": 3,
                                      "readable": True, "cleared": False}})
    err = capsys.readouterr().err
    assert "RESIDUE UNKNOWN" not in err, (
        f"a MEASURED remainder of 3 was reported as unknown: {err!r}")
    assert "live_seed: RESIDUE - 3" in err, (
        f"3 remaining rows printed no RESIDUE line naming the count: {err!r}")


def test_the_skipped_unowned_rows_are_said_out_loud(capsys):
    """ESCAPE CLOSED: disabling the CLEAR SKIPPED message passed the band.

    An unowned row is the one case where the clear PERMANENTLY cannot reach
    zero, and the stderr line is where the operator learns why. R6 pins the
    unowned row's survival in the report dict; this pins the words.
    """
    seed = _load()
    seed._report_residue({
        "residue": {"history_rows": 2, "history_rows_found": 2,
                    "readable": True, "cleared": True},
        "clear": {"unowned": 2, "errors": [], "warnings": [],
                  "deleted": 0, "stalled": False, "twins": {}}})
    err = capsys.readouterr().err
    assert "CLEAR SKIPPED" in err and "2 row(s)" in err, (
        f"2 unowned rows produced no CLEAR SKIPPED line: {err!r}")

    seed._report_residue({
        "residue": {"history_rows": 0, "history_rows_found": 2,
                    "readable": True, "cleared": True},
        "clear": {"unowned": 0, "errors": [], "warnings": [],
                  "deleted": 2, "stalled": False, "twins": {}}})
    err = capsys.readouterr().err
    assert "CLEAR SKIPPED" not in err, (
        f"a clear that skipped nothing printed CLEAR SKIPPED: {err!r}")


# ── adversarial pass on the SHIPPED cut #7 code (F1-F4) ─────────────────────
#
# Four defects found by reading the merged implementation against CLAUDE.md 0
# rather than against the spec. Each test below FAILS on the tree as it stands
# and names the defect it is about; none of them weakens an existing
# assertion, and each that could be satisfied by an over-sensitive fix carries
# the opposite direction in the same test.


def _blind_page_client(seed, first_page, *, cursor, second_body):
    """A FakeClient serving exactly two browse pages and nothing else.

    Used to drive _twin_scan DIRECTLY, so the verdict is about the scan and
    not about anything the clear does around it.
    """
    return FakeClient({
        ("GET", "/api/library/browse?limit=500"):
            _browse_body(first_page, next_cursor=cursor),
        ("GET", f"/api/library/browse?limit=500&after_id={cursor}"):
            second_body,
    })


def test_a_blind_continuation_page_does_not_make_the_scan_conclusive():
    """F1. The blind-library guard's denominator is the SCAN TOTAL.

    `if report["rows_scanned"] == 0` is evaluated over every page the scan
    read, so it can only ever see a blind FIRST page. A blind body arriving as
    a CONTINUATION page (page 2+) is structurally invisible to it:
    rows_scanned is already non-zero from page one, `next_cursor: null` ends
    the loop as scan_complete, and the scan reports itself CONCLUSIVE having
    never seen the rows it was asked about. That is CLAUDE.md 0 -- a gate that
    cannot see the thing it is asked about reporting OK -- inside the very
    function whose docstring claims to handle it.

    The two bodies are indistinguishable over HTTP by construction:
    library.library_browse wraps its whole query in `except Exception: return
    [], None` (library.py:363-364), and next_cursor is non-None only on a FULL
    page (library.py:360-361) -- so a cursor handed back and then answered with
    zero rows is exactly as unverified on page 2 as it is on page 1.

    BOTH DIRECTIONS ARE ASSERTED HERE ON PURPOSE. "Call every multi-page scan
    inconclusive" would satisfy the first half and is the over-sensitivity
    CLAUDE.md 0 counts as a soundness bug in its own right, so the honest case
    -- a continuation page carrying real rows and no cursor -- must stay
    conclusive, warning-free, and must still match both twins.
    """
    seed = _load()
    doomed = {7, 8}
    page1 = [{"id": 42, "history_id": 7, "title": "a", "file_path": "/a"}]
    page2_real = [{"id": 20, "history_id": 8, "title": "b", "file_path": "/b"}]

    # -- the defect: page two is the blind body -------------------------------
    blind = _blind_page_client(seed, page1, cursor=4242,
                               second_body=_BLIND_BROWSE)
    report = seed._twin_scan(blind, set(doomed))
    assert "/api/library/browse?limit=500&after_id=4242" in blind.paths("GET"), (
        f"the scan never requested the stubbed continuation page, so this "
        f"test's fixture was not exercised and its verdict is about nothing. "
        f"Paths: {blind.paths('GET')}")
    warnings = " ".join(str(w) for w in report["warnings"])
    assert report["conclusive"] is False, (
        f"a scan whose page TWO came back with the blind body reported itself "
        f"CONCLUSIVE: {report}. The guard is `rows_scanned == 0` over the scan "
        f"TOTAL, which page one already made non-zero, so the continuation "
        f"page's zero rows are invisible to it. library_browse cannot "
        f"distinguish an unreadable table from an empty page "
        f"(library.py:363-364), and that does not become false because an "
        f"earlier page succeeded -- twin 20 is in the doomed set and was "
        f"neither seen nor reported unseen.")
    assert "4242" in warnings, (
        f"no warning names the page that came back blind. The operator is "
        f"told the scan is inconclusive without being told WHERE it went "
        f"blind, which is the one fact that distinguishes this from a first- "
        f"page failure: {report['warnings']!r}")
    assert ("library.py:363-364" in warnings
            or "indistinguishable" in warnings.lower()), (
        f"the warning does not explain WHY the continuation page is "
        f"unverified, so it reads as a hedge rather than the mechanism: "
        f"{warnings!r}")

    # -- the inverse: an HONEST last page must stay conclusive ----------------
    honest = _blind_page_client(seed, page1, cursor=4242,
                                second_body=_browse_body(page2_real))
    ok_report = seed._twin_scan(honest, set(doomed))
    assert "/api/library/browse?limit=500&after_id=4242" in honest.paths("GET"), (
        f"the honest fixture's continuation page was never requested: "
        f"{honest.paths('GET')}")
    assert ok_report["scan_complete"] is True, ok_report
    assert ok_report["conclusive"] is True, (
        f"a genuine end-of-library page two -- real rows, next_cursor null -- "
        f"was reported INCONCLUSIVE: {ok_report}. Making every multi-page scan "
        f"unverified is the over-sensitivity half of CLAUDE.md 0: a gate that "
        f"cries wolf gets switched off, so this is a soundness bug and not a "
        f"safe default.")
    assert sorted(ok_report["matched_ids"]) == [20, 42], (
        f"the honest two-page scan matched {ok_report['matched_ids']!r}; both "
        f"doomed history ids have a twin, one per page: {ok_report}")
    assert not ok_report["warnings"], (
        f"an honest, complete two-page scan emitted warnings: "
        f"{ok_report['warnings']!r}")

    # -- and the report the clear writes off that scan ------------------------
    owned = [_owned_row(seed, 7), _owned_row(seed, 8, index=1)]
    survivor = [{"id": 9000, "history_id": 555555, "title": "operator",
                 "file_path": "/keep"}]
    client = FakeClient({
        _history_key(seed): [_Reply(list(owned)), _Reply([])],
        ("GET", "/api/library/browse?limit=500"): [
            _Reply(_browse_body(page1, next_cursor=4242)),
            _Reply(_browse_body(survivor, next_cursor=4242))],
        ("GET", "/api/library/browse?limit=500&after_id=4242"): _BLIND_BROWSE,
        ("DELETE", "/api/library/42"): {"ok": True, "deleted_row": True},
        ("POST", "/api/batch/delete"): [_canary_reply(2), _delete_reply(2)],
    })
    out = seed.clear_seeded_history(client)
    assert "/api/library/browse?limit=500&after_id=4242" in client.paths("GET"), (
        f"the clear's twin scan never followed the cursor into the blind "
        f"page, so this fixture was not exercised: {client.paths('GET')}")
    twins = out["twins"]
    assert twins["conclusive"] is False, (
        f"the clear recorded a conclusive twin scan off a blind continuation "
        f"page: {twins}")
    assert twins["remaining"] != 0, (
        f"twins['remaining'] was written {twins['remaining']!r} by the final "
        f"verification pass, whose page two was the blind body. History id 8's "
        f"twin was never seen, never deleted, and is now reported as zero "
        f"remaining -- a measured-sounding zero over a denominator that "
        f"excluded the subject. UNKNOWN is a third state (CLAUDE.md 0) and "
        f"None is how this report spells it: {out}")
    clear_warnings = " ".join(str(w) for w in out["warnings"])
    assert "4242" in clear_warnings, (
        f"the clear emitted no warning naming the blind page, so stderr says "
        f"nothing at all about it and _report_residue prints the CLEARED line "
        f"unqualified: {out['warnings']!r}")


def test_a_benign_concurrent_removal_does_not_abort_the_whole_clear():
    """F2. The canary is over-sensitive AND it misdiagnoses.

    `if matched != len(probe)` treats ANY shortfall as a broken filter and
    aborts the entire clear -- twins included -- while the error text asserts
    "The filter shape is not the one batch_ops._build_query accepts", which
    describes only the matched==0 case. One probe id removed by another writer
    between the history read and the canary produces matched == len(probe)-1,
    and that is a benign race the same function already handles as a SOFT
    WARNING twice over: at the batch delete ("a N-id chunk matched nothing
    (already gone?)") and at the twin delete ("Another writer got it first: a
    warning, not an error"). Fatal in one place, benign in two.

    Over-sensitivity is a soundness bug, not a safe default (CLAUDE.md 0): a
    gate that cries wolf gets switched off.

    THE CANARY'S REAL JOB IS NOT BEING WEAKENED HERE. matched==0 for every
    call must still abort having deleted nothing, and
    test_a_malformed_filter_is_caught_before_anything_is_deleted (R4, above)
    is the separate test that proves it. That test must stay green.
    """
    seed = _load()
    rows = [_owned_row(seed, 11), _owned_row(seed, 12, index=1),
            _owned_row(seed, 13)]
    client = _clear_client(
        seed,
        [_Reply(list(rows)), _Reply([])],
        # The probe is all three ids; one of them vanished under us, so the
        # app honestly answers 2. The following non-dry-run chunk then removes
        # the two that are still there.
        [_canary_reply(2), _delete_reply(2)])
    out = seed.clear_seeded_history(client)

    canary_posts = _batch_delete_indices(client, dry_run=True)
    assert canary_posts, (
        f"no dry-run canary POST was made at all, so this test's fixture was "
        f"not exercised: {[(m, p) for m, p, _b in client.calls]}")
    assert (out.get("canary") or {}).get("sent") == 3, (
        f"the canary record does not carry the probe it sent: {out['canary']!r}")
    assert (out.get("canary") or {}).get("matched") == 2, (
        f"the canary record does not carry the shortfall the app reported, so "
        f"the discrepancy is erased rather than reported: {out['canary']!r}")
    assert _batch_delete_indices(client, dry_run=False), (
        f"the clear aborted at the canary because ONE of three probe ids had "
        f"already been removed by another writer. Nothing was deleted -- "
        f"neither the two history rows that were still there nor any library "
        f"twin -- over a race this same function treats as a warning at both "
        f"of its other delete sites. Calls: "
        f"{[(m, p) for m, p, _b in client.calls]}")
    text = " ".join(str(x) for x in
                    (out.get("errors") or []) + (out.get("warnings") or []))
    assert "_build_query accepts" not in text, (
        f"the report asserts the filter shape is not the one "
        f"batch_ops._build_query accepts. The evidence is a PARTIAL match "
        f"(2 of 3), which is exactly what a concurrent removal produces and "
        f"is not evidence about the filter's shape at all -- only matched==0 "
        f"is: {text!r}")
    assert not out["errors"], (
        f"a clear that went on to delete every row still present and "
        f"re-measured a remainder of zero was reported as having ERRORED: "
        f"{out['errors']!r}")
    warnings = " ".join(str(w) for w in (out.get("warnings") or []))
    assert "canary" in warnings.lower(), (
        f"the shortfall left no warning, so it never reaches stderr -- "
        f"_report_residue prints errors and warnings, never the canary dict, "
        f"so an operator would never learn a probe id had vanished: "
        f"{out['warnings']!r}")
    assert out["deleted"] == 2 and out["remaining"] == 0, (
        f"the clear did not finish: {out}")
    assert out["ok"] is True, (
        f"the clear reported ok={out['ok']!r} after removing every row that "
        f"was still there and RE-MEASURING zero remaining. Failing this "
        f"exits 4 on a capture whose only anomaly was a benign race: {out}")


def test_the_remainder_does_not_claim_a_measurement_that_never_happened(capsys):
    """F3. A report field asserting a provenance it does not have.

    clear_seeded_history's docstring says "NO ARITHMETIC. The remainder is
    RE-MEASURED by re-reading /api/history, never computed", and
    _report_residue prints "measured after the clear" beside the number. On
    two paths there is no second read at all: the canary abort and the
    `if not ids:` break both write `out["remaining"] = len(rows)` -- the
    round's PRE-abort read -- and return.

    The number is therefore whatever the history looked like BEFORE the
    decision to stop, presented as though it were measured after it. Both
    fixtures below hand the tool a third reply showing the live marked set has
    since dropped to two rows; a genuine re-measure returns 2, the shipped
    code returns 3 and calls it measured.

    EITHER FIX IS ACCEPTED, and the assertion is written as that disjunction:
    re-read and report the live number, or stop claiming the number was
    re-read. What is not accepted is the current pair -- the stale number AND
    the claim.
    """
    seed = _load()

    def _check(label, plan, client, err):
        reads = _history_read_indices(seed, client)
        clear = _clear_of(plan)
        # The read model is pinned by R2: teardown reads once itself, then the
        # clear's round reads at the top, then the post-clear re-read. Three
        # or more reads means the remainder was re-measured; two means the
        # number came from the round's own pre-abort read.
        assert len(reads) >= 2, (
            f"{label}: the tool made {len(reads)} history read(s); this "
            f"fixture was not exercised: "
            f"{[(m, p) for m, p, _b in client.calls]}")
        remeasured = len(reads) >= 3
        claimed = "measured after the clear" in err
        if remeasured:
            assert clear.get("remaining") == 2, (
                f"{label}: the tool re-read /api/history and still reported "
                f"remaining={clear.get('remaining')!r}. The re-read's own "
                f"body carries two rows, so the number did not come from the "
                f"measurement it claims: {clear}")
        else:
            assert not claimed, (
                f"{label}: the report prints 'measured after the clear' "
                f"beside remaining={clear.get('remaining')!r}, and no read of "
                f"/api/history happened after the clear stopped -- the number "
                f"is the round's pre-abort read. The live marked set is 2. "
                f"Either re-measure it or do not call it measured "
                f"(CLAUDE.md 0: unknown is a third state). stderr: {err!r}")

    three = [_owned_row(seed, 11), _owned_row(seed, 12, index=1),
             _owned_row(seed, 13)]
    two = [_owned_row(seed, 11), _owned_row(seed, 13)]
    client_a = _teardown_stubs(
        seed, [_Reply(list(three)), _Reply(list(three)), _Reply(list(two))],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
         ("POST", "/api/batch/delete"): {
             "ok": True, "candidates_matched": 0, "processed": 0,
             "files_deleted": 0, "errors": 0, "dry_run": True}})
    plan_a = seed.teardown(client_a, clear_history=True)
    seed._report_residue(plan_a)
    err_a = capsys.readouterr().err
    assert _batch_delete_indices(client_a, dry_run=True), (
        f"path A never sent the canary, so it is not the canary-abort path "
        f"and this half of the test is about the wrong subject: "
        f"{[(m, p) for m, p, _b in client_a.calls]}")
    _check("canary-abort path", plan_a, client_a, err_a)

    un3 = [_unowned_row(seed, 21), _unowned_row(seed, 22, index=1),
           _unowned_row(seed, 23)]
    un2 = [_unowned_row(seed, 21), _unowned_row(seed, 23)]
    client_b = _teardown_stubs(
        seed, [_Reply(list(un3)), _Reply(list(un3)), _Reply(list(un2))],
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE})
    plan_b = seed.teardown(client_b, clear_history=True)
    seed._report_residue(plan_b)
    err_b = capsys.readouterr().err
    assert not _batch_delete_indices(client_b), (
        f"path B posted to /api/batch/delete; with every row unowned the "
        f"clear must break before the canary, so this half is not on the "
        f"no-owned-ids path: {[(m, p) for m, p, _b in client_b.calls]}")
    _check("no-owned-ids path", plan_b, client_b, err_b)


def test_the_cleared_line_never_reports_more_removed_than_it_found(capsys):
    """F4. `found` and `deleted` are incommensurable, and the line divides them.

    `found` is set once, from round one's single unpaginated GET, which asks
    for limit=_HISTORY_PAGE (500) -- by design; unbounded residue is handled
    by the ROUND LOOP, not by paginating that read. `deleted` accumulates
    across every round. _report_residue then prints them as "{deleted} of
    {found} ... row(s) removed", so a clear that needed two rounds prints a
    numerator larger than its denominator.

    That line is capture.sh's diagnostic surface (its failure branch greps
    'live_seed: '), so this is the number an operator reads off the box. "600
    of 500 removed" is not a rounding artifact -- it is a ratio whose two
    halves were measured over different denominators, which is the mismatch
    CLAUDE.md 8 warns about in its own terms.

    The fix is not asserted here, only the property: whatever the two numbers
    become, the CLEARED line must not claim more rows removed than it found.
    """
    seed = _load()
    page = seed._HISTORY_PAGE
    first = [_owned_row(seed, 1000 + i, index=i % 3) for i in range(page)]
    second = [_owned_row(seed, 9000 + i, index=i % 3) for i in range(100)]
    client = _teardown_stubs(
        seed,
        [_Reply(list(first)),    # teardown's own read
         _Reply(list(first)),    # round 1, top of loop -> `found` = 500
         _Reply(list(second)),   # round 1, post-delete re-read: 100 remain
         _Reply(list(second)),   # round 2, top of loop
         _Reply([])],            # round 2, post-delete re-read: none remain
        {("GET", "/api/library/browse?limit=500"): _BLIND_BROWSE,
         ("POST", "/api/batch/delete"): [
             _canary_reply(200),                      # probe = ids[:_CLEAR_CHUNK]
             _delete_reply(200), _delete_reply(200),   # round 1: 200+200+100
             _delete_reply(100),
             _delete_reply(100)]})                     # round 2: 100
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(seed, "Client", lambda base_url: client)
        rc = _run_main(seed, ["--teardown", "--clear-history"])
    finally:
        monkey.undo()
    err = capsys.readouterr().err

    reads = _history_read_indices(seed, client)
    assert len(reads) == 5, (
        f"the fixture's five scripted history reads were not consumed "
        f"({len(reads)} read(s)), so this clear did not run the two rounds "
        f"this test is about: {[(m, p) for m, p, _b in client.calls]}")
    assert len(_batch_delete_indices(client, dry_run=False)) == 4, (
        f"expected 4 non-dry-run batch deletes (200+200+100 in round one, "
        f"100 in round two): "
        f"{[(m, p) for m, p, _b in client.calls]}")
    assert rc == 0, (
        f"the two-round clear did not finish cleanly (rc={rc}), so the "
        f"CLEARED line this test reads was never the subject: {err!r}")

    match = re.search(r"CLEARED - (\d+) of (\d+) ", err)
    assert match, (
        f"no CLEARED line was printed, so there is nothing to check: {err!r}")
    removed, found = int(match.group(1)), int(match.group(2))
    assert removed <= found, (
        f"the CLEARED line reads '{match.group(0).strip()}' -- {removed} rows "
        f"removed out of {found} found. `found` came from ONE read capped at "
        f"_HISTORY_PAGE={page}; `removed` accumulated across {2} rounds. The "
        f"two are measured over different denominators and must not be "
        f"printed as a ratio. Full stderr: {err!r}")
