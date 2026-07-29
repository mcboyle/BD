"""requeue_for_dedup could never produce a dedup decision. Three defects, stacked.

The function exists so L14 has a real subject: queue an already-completed URL
again and observe BD skipping it as a duplicate. `queue.status =
'skipped_duplicate'` is the only thing L14 reads, and the dedup path is its only
writer. None of that could happen, for three independent reasons, and the third
is why fixing the first two alone would have changed nothing.

DEFECT 1 -- THE ROUTE ANSWERS 405.

    tools/live_seed.py:650    client.post("/api/queue/v2", {...})
    bulk_downloader/app_queue.py:170  @queue_bp.route("/api/queue/v2")

No `methods=` argument, so Flask registers GET only and a POST gets 405 Method
Not Allowed. The route that enqueues a URL is `/api/queue/v2/add_url`
(app_queue.py:461), declared `methods=["POST"]`.

DEFECT 2 -- THE PAYLOAD IS THE WRONG SHAPE. add_url's documented body is
`{"site_id": ..., "url": "https://..."}` -- `url`, singular, a string. The
seeder sends `{"site_id": ..., "urls": [url]}`. Even against the right route
that is a 400 (`url required`).

DEFECT 3 -- AND IT WOULD STILL BE DROPPED AT INTAKE.

    bulk_downloader/runner_queue.py:257-259
        with self._lock:
            if u in self.jobs:
                dupes += 1
                continue

A completed job stays in `self.jobs`. So re-queueing the same URL on the SAME
site is collapsed there, as a "dupe", counted and discarded -- before the dedup
preflight that would have written `skipped_duplicate` ever runs. The function's
own docstring already knew this about the batch's internal repeat ("collapsed at
INTAKE, before anything is queued"); what it missed is that the same code drops
the after-the-fact re-queue too.

So the intake drop has to be avoided, not defeated, and the code says how:

    bulk_downloader/runner_integrity.py:148 _dedup_preflight
        if self.config.get("dedup_exact_url", True):
            hit = db_find_url_in_history(url)

`db_find_url_in_history` is a GLOBAL history lookup -- not scoped to a site --
and `dedup_exact_url` defaults True. And L14's query is global too:

    SELECT COUNT(*) FROM queue WHERE status = 'skipped_duplicate'

no site_id filter. So queueing the completed URL on a SECOND seeded site both
misses the first site's `self.jobs` (each runner has its own) and hits the
history-based dedup preflight, which writes the row L14 reads. No eviction route
is needed, and no runner change: the mechanism already works, it was just being
asked on the one site where intake would swallow the question.

DEFECT 4, found while fixing the others -- THE FAILURE WAS UNREPORTABLE.

    tools/live_seed.py Client._request
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
        ...
        try:
            return json.loads(raw)

The status code is read and then DISCARDED. Flask's 405 body is an HTML error
page, so `json.loads` fails and the caller gets
`{"ok": False, "error": "non-JSON response: <!doctype html>..."}` -- an error
wearing the shape of an ordinary return value. `requeue_for_dedup` then assigns
it to `plan["queued"]` and never looks at it, so the whole thing reported
`dedup_observed: false` and looked like BD declining to dedup rather than like a
request that was never accepted. A 405 that reads as "no dedup happened" is the
same class as everything else in this file: the instrument could not see its
subject, so it reported something plausible.

WHAT MAKES THIS CHECKABLE. add_url returns `{ok, site_id, url, added, dupes,
skipped}`. `dupes: 1` IS defect 3, reported by the app itself, so the fix does
not have to infer that intake accepted the URL -- it can read it. A response of
`added: 0, dupes: 1` must be a refusal, not a shrug.

RED-first: R1 through R7 fail on pristine source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import live_seed  # noqa: E402

_SOURCE_SITE = "src00001"
_DEDUP_SITE = "ddp00001"
_URL = "http://127.0.0.1:8899/scene_002.mp4?bdseed=abc123"


class _FakeClient:
    """Records every write so the test asserts what the seeder DID.

    Models the two routes that matter with their REAL behaviour:
      * POST /api/queue/v2          -> the 405 shape, because that route is
                                       GET-only (app_queue.py:170)
      * POST /api/queue/v2/add_url  -> the documented success shape
    """

    def __init__(self, sites=None, add_url_result=None, new_ids=None):
        self._sites = dict(sites or {})
        self._add_url_result = add_url_result or {
            "ok": True, "site_id": _DEDUP_SITE, "url": _URL,
            "added": 1, "dupes": 0, "skipped": 0}
        self._new_ids = list(new_ids or [_DEDUP_SITE])
        self.calls: list = []

    def get(self, path, **kw):
        self.calls.append(("GET", path, None))
        if path == "/api/status":
            return dict(self._sites)
        return {}

    def post(self, path, payload=None, **kw):
        self.calls.append(("POST", path, payload))
        if path == "/api/queue/v2":
            # What Flask actually returns for a POST to a GET-only route, after
            # Client._request has thrown the status code away.
            return {"ok": False,
                    "error": "non-JSON response: <!doctype html>\n"
                             "<html lang=en>\n<title>405 Method Not Allowed"}
        if path == "/api/queue/v2/add_url":
            return dict(self._add_url_result)
        if path == "/api/sites":
            sid = self._new_ids.pop(0) if self._new_ids else _DEDUP_SITE
            self._sites[sid] = {"name": (payload or {}).get("name", "")}
            return {"id": sid}
        return {"ok": True}

    def delete(self, path, **kw):
        self.calls.append(("DELETE", path, None))
        return {"ok": True}

    def posts(self):
        return [(p, pay) for v, p, pay in self.calls if v == "POST"]

    def posted_to(self, path):
        return [pay for p, pay in self.posts() if p == path]


@pytest.fixture(autouse=True)
def _no_real_settling(monkeypatch):
    """start_seeded_site and wait_for_settle are not the subject.

    They poll the queue over a real budget; leaving them live would make every
    assertion here a timing test of code this cut does not touch. Stubbed to the
    OUTCOME THE FIX IS AFTER -- a skipped_duplicate -- so any test that claims
    dedup was observed is claiming it about the seeder's plumbing, which is
    what is under test, and not about the runner's dedup logic, which is not.
    """
    monkeypatch.setattr(live_seed, "start_seeded_site",
                        lambda client, sid: {"action": "start", "site_id": sid})
    monkeypatch.setattr(
        live_seed, "wait_for_settle",
        lambda client, sid, urls, **kw: {
            "settled": True,
            "per_url": {u: {"status": "skipped_duplicate"} for u in urls}})


def _requeue(client, **kw):
    return live_seed.requeue_for_dedup(client, _SOURCE_SITE, _URL, **kw)


# ── R1/R2: the route and the payload ─────────────────────────────────────────

def test_it_does_not_post_to_the_get_only_queue_route():
    """R1 -- the 405.

    `/api/queue/v2` is declared without `methods=`, so Flask allows GET only.
    Every POST the seeder aimed at it was refused before reaching any queue code.
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    _requeue(c)
    assert not c.posted_to("/api/queue/v2"), (
        "requeue_for_dedup POSTed to /api/queue/v2, which is registered "
        "GET-only at app_queue.py:170 and answers 405. Nothing was ever "
        "enqueued, so the dedup decision it was waiting for could not occur.")


def test_it_posts_to_the_add_url_route_with_a_single_url_string():
    """R2 -- the payload.

    add_url's body is `url`, a string. The seeder sent `urls`, a list, which
    that route rejects with 400 "url required" even once the path is right.
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    _requeue(c)
    bodies = c.posted_to("/api/queue/v2/add_url")
    assert bodies, (
        f"nothing was POSTed to /api/queue/v2/add_url. POSTs were: {c.posts()}")
    body = bodies[0]
    assert body.get("url") == _URL, (
        f"the body was {body!r}. add_url (app_queue.py:461) reads "
        f"body['url'] as a string; 'urls' as a list is a 400.")
    assert "urls" not in body, (
        f"the body still carries the list-shaped 'urls' key: {body!r}")


# ── R3/R4: the second site, which is the whole substance ─────────────────────

def test_the_duplicate_is_queued_on_a_different_site():
    """R3 -- THE LOAD-BEARING ONE.

    runner_queue.py:257 drops a URL already present in that runner's
    `self.jobs`, and a completed job stays there. Re-queueing on the SAME site
    is therefore collapsed at intake and never reaches the dedup preflight --
    so fixing the route and the payload alone would have produced a clean
    `added: 0, dupes: 1` and still no dedup.

    A second site works because both halves of the mechanism are global:
    _dedup_preflight looks the URL up in history via db_find_url_in_history
    (not site-scoped, default on), and L14 counts skipped_duplicate across the
    whole queue table (also not site-scoped).
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    _requeue(c)
    bodies = c.posted_to("/api/queue/v2/add_url")
    assert bodies, f"nothing enqueued: {c.posts()}"
    assert bodies[0].get("site_id") != _SOURCE_SITE, (
        f"the duplicate was queued back onto {_SOURCE_SITE}, the site that just "
        f"completed it. That URL is still in that runner's self.jobs, so "
        f"runner_queue.py:257 drops it as a dupe before the dedup preflight "
        f"runs. Body: {bodies[0]!r}")


def test_the_dedup_site_is_created_and_carries_the_marker():
    """The second site has to be one the seeder OWNS.

    The marker is what teardown keys on (teardown -> _marked_site_ids ->
    DELETE /api/sites/<sid>), so an unmarked dedup site would survive teardown
    and leave synthetic state behind claiming to be the operator's.
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    _requeue(c)
    created = c.posted_to("/api/sites")
    assert created, f"no site was created for the duplicate: {c.posts()}"
    name = created[0].get("name") or ""
    assert live_seed.SEED_MARKER in name, (
        f"the dedup site was created as {name!r}, which does not carry "
        f"{live_seed.SEED_MARKER!r}. teardown deletes by marker, so this site "
        f"would outlive the run.")
    assert name != live_seed.SEED_SITE_NAME, (
        f"the dedup site reuses the queue fixture's exact name ({name!r}). "
        f"ensure_seed_site selects on that exact name and deletes what it "
        f"finds, so the two would destroy each other between runs.")


def test_the_dedup_site_does_not_skip_on_file_existence():
    """The file WILL already exist -- site one just downloaded it.

    skip_if_exists (runner_transport.py:794, default True) keys on filesystem
    state and is a different mechanism from dedup. The preflight runs first, so
    dedup should win either way, but leaving the flag on means that if dedup
    ever stops firing the result is a silent file-existence skip that looks
    like success. Explicit False so a dedup failure shows up as a real download.
    """
    cfg = live_seed.dedup_site_config()
    assert cfg.get("skip_if_exists") is False, (
        f"dedup_site_config gave skip_if_exists={cfg.get('skip_if_exists')!r}; "
        f"app.py:4218 fills any value in ('', None) with the default True, so "
        f"it must be an explicit False.")
    assert cfg.get("auto_teach_first_run") is False, (
        f"auto_teach_first_run must be off, or runner._start_serialized returns "
        f"before spawning a worker and marks the URL needs_review waiting for a "
        f"human click. Config was {cfg!r}")


# ── R5: the app's own answer must be read ────────────────────────────────────

def test_an_intake_dupe_response_is_a_refusal_not_a_shrug():
    """R5 -- add_url reports the intake drop itself.

    `{"added": 0, "dupes": 1}` is defect 3 happening, stated by the app. If the
    seeder accepts that and goes on to wait for a settle, it reports
    `dedup_observed: false` and the operator reads it as BD choosing not to
    dedup -- when in fact nothing was ever queued.
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}},
                    add_url_result={"ok": True, "added": 0, "dupes": 1,
                                    "skipped": 0})
    with pytest.raises(live_seed.SeedRefused) as exc:
        _requeue(c)
    assert "dupes" in str(exc.value).lower() or "intake" in str(exc.value).lower(), (
        f"the refusal does not say the URL was dropped at intake: {exc.value}")


def test_a_failed_enqueue_is_raised_not_recorded_and_ignored():
    """R6 -- the swallow.

    The pristine code assigns the response to plan["queued"] and never inspects
    it, so a 405, a 400 or an unreachable app all end as `dedup_observed:
    false`. Every one of those is a broken seeder, not a BD that declined to
    dedup, and they must not be reported as the same thing.

    The MESSAGE is asserted, not just the exception, because the two refusals
    were otherwise interchangeable: removing the ok check entirely still raised,
    via `added` coming back None from an error body. A mutation proving that
    survived until this assertion existed. They are different diagnoses -- "the
    request was rejected" versus "the request was accepted and then dropped at
    intake" -- and only the second implicates runner_queue.py:257.
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}},
                    add_url_result={"ok": False, "error": "site_id required"})
    with pytest.raises(live_seed.SeedRefused) as exc:
        _requeue(c)
    msg = str(exc.value).lower()
    assert "could not queue" in msg, (
        f"a rejected request must be reported as a rejection: {exc.value}")
    assert "intake" not in msg, (
        f"a rejected request was misreported as an intake drop, which points "
        f"the reader at runner_queue.py for a request that never got there: "
        f"{exc.value}")


def test_the_source_site_is_never_deleted():
    """The queue fixture holds the evidence. Deleting it destroys the subject.

    Caught by a surviving mutation: reverting `_ensure_owned_site`'s selection
    from EXACT NAME to the marker made it delete every marked site it found --
    which, at the moment requeue_for_dedup runs, is the queue fixture that just
    completed the download. Its history row is what _dedup_preflight matches on,
    and its queue rows are what L11 reads. The run would have deleted its own
    evidence and then reported no dedup.
    """
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    _requeue(c)
    deleted = [p for v, p, _ in c.calls if v == "DELETE"]
    assert f"/api/sites/{_SOURCE_SITE}" not in deleted, (
        f"the source site was deleted while re-queueing its own URL: "
        f"{deleted}. Site selection must be by EXACT NAME -- the marker alone "
        f"matches all three seeded sites.")


def test_a_successful_requeue_reports_the_dedup_observation():
    """The happy path still works and still answers L14's question."""
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    plan = _requeue(c)
    assert plan.get("dedup_observed") is True, f"plan was {plan!r}"
    assert plan.get("action") == "requeue_for_dedup", f"plan was {plan!r}"


def test_dry_run_writes_nothing():
    c = _FakeClient({_SOURCE_SITE: {"name": live_seed.SEED_SITE_NAME}})
    plan = _requeue(c, dry_run=True)
    assert plan.get("dry_run") is True
    assert not [p for p, _ in c.posts()], f"dry_run POSTed: {c.posts()}"
    assert not [c for c in c.calls if c[0] == "DELETE"], (
        f"dry_run deleted something: {c.calls}")


# ── R7: the client must stop dressing errors as data ─────────────────────────

def test_the_client_surfaces_the_http_status_on_an_error_response():
    """R7 -- at the source.

    Client._request reads HTTPError's body and discards `exc.code`. A 405
    therefore arrives as a non-JSON parse failure and a 400 arrives as whatever
    the app's JSON body happens to say, with no way to distinguish either from a
    successful call that returned odd data. The status is the one fact that
    makes a failure identifiable, and throwing it away is why defect 1 survived
    long enough to reach the box.
    """
    import json as _json
    import urllib.error
    import urllib.request

    class _Err(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://x/api/queue/v2", 405, "Method Not Allowed",
                             {}, None)

        def read(self):
            return b"<!doctype html>\n<title>405 Method Not Allowed</title>"

    def _boom(req, timeout=None):
        raise _Err()

    client = live_seed.Client("http://127.0.0.1:1")
    orig = urllib.request.urlopen
    urllib.request.urlopen = _boom
    try:
        got = client.post("/api/queue/v2", {"site_id": "x"})
    finally:
        urllib.request.urlopen = orig

    assert isinstance(got, dict), f"got {got!r}"
    assert got.get("ok") is False, f"a 405 must not read as ok: {got!r}"
    assert got.get("status") == 405, (
        f"the response carries no HTTP status: {got!r}. Without it a caller "
        f"cannot tell 405 (wrong route) from 400 (wrong payload) from a "
        f"connection failure, and all three arrive looking like data.")


def test_the_client_still_returns_a_json_error_body_when_there_is_one():
    """The app's own error shape must survive -- 400 bodies carry the reason,
    and replacing them with a bare status would lose it."""
    import urllib.error
    import urllib.request

    class _Err400(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://x/api/queue/v2/add_url", 400, "Bad Request",
                             {}, None)

        def read(self):
            return b'{"ok": false, "error": "url required"}'

    def _boom(req, timeout=None):
        raise _Err400()

    client = live_seed.Client("http://127.0.0.1:1")
    orig = urllib.request.urlopen
    urllib.request.urlopen = _boom
    try:
        got = client.post("/api/queue/v2/add_url", {"site_id": "x"})
    finally:
        urllib.request.urlopen = orig

    assert got.get("error") == "url required", (
        f"the app's own error message was lost: {got!r}")
    assert got.get("status") == 400, f"no status on a JSON error body: {got!r}"


# ── the call site has to pass the new shape ──────────────────────────────────

def test_a_dedup_setup_failure_does_not_fail_the_whole_seed():
    """The strictness belongs in the function, not in the exit code.

    requeue_for_dedup raises on a rejected enqueue -- that is the fix. But an
    escape from main() makes the run exit 2, and capture.sh:804-809 then prints
    "seeding declined or failed" for a run whose queue seed, start and settle all
    SUCCEEDED. The dedup pass is an additional subject on top of work that
    already worked; failing the report for it is a false negative about the part
    that did.

    Asserted structurally over main()'s AST: the requeue_for_dedup call must sit
    inside a try that handles SeedRefused. A behavioural test would need the
    whole main() path stubbed, and the property here is about control flow.
    """
    import ast
    src = (ROOT / "tools" / "live_seed.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert fn is not None, "live_seed.main not found"

    guarded = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        calls_it = any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == "requeue_for_dedup"
            for b in node.body for c in ast.walk(b))
        if not calls_it:
            continue
        for h in node.handlers:
            names = []
            if isinstance(h.type, ast.Name):
                names = [h.type.id]
            elif isinstance(h.type, ast.Tuple):
                names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
            if "SeedRefused" in names:
                guarded = True
    assert guarded, (
        "main() calls requeue_for_dedup without catching SeedRefused, so a "
        "dedup-setup failure exits 2 and capture.sh reports the entire seeding "
        "as declined or failed -- including the queue seed that succeeded.")


def test_the_recorded_failure_names_a_reason():
    """And the non-fatal path must not become the old swallow.

    The pristine code recorded a 405 as `dedup_observed: false` with no reason,
    so a broken seeder and a BD that declined to dedup read identically. The
    handler must write the reason into the plan.
    """
    import ast
    src = (ROOT / "tools" / "live_seed.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)

    # EXISTENCE, not the first match. The first draft asserted about whichever
    # qualifying Try ast.walk reached first, and that is main()'s OUTER try --
    # whose SeedRefused handler correctly prints "REFUSED" and returns 2 for
    # real refusals (an unreadable queue, a host that cannot be proven safe).
    # Its body transitively contains the dedup call, so it matched, and the test
    # failed about the right code. What must exist is ONE handler that records
    # the reason; the outer one keeping its exit-2 behaviour is not a defect.
    handlers = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        if not any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                   and c.func.id == "requeue_for_dedup"
                   for b in node.body for c in ast.walk(b)):
            continue
        for h in node.handlers:
            names = ([h.type.id] if isinstance(h.type, ast.Name)
                     else [e.id for e in getattr(h.type, "elts", [])
                           if isinstance(e, ast.Name)])
            if "SeedRefused" in names:
                handlers.append("\n".join(ast.unparse(s) for s in h.body))
    assert handlers, "no SeedRefused handler wraps the requeue_for_dedup call"
    assert any("error" in h for h in handlers), (
        "no SeedRefused handler around the dedup call records a reason, which "
        "is the swallow this cut removes wearing a different coat. Handlers "
        "found:\n" + "\n---\n".join(handlers))


def test_main_still_reaches_the_dedup_pass():
    """A signature change that main() does not follow would silently remove the
    dedup pass while every unit test above kept passing."""
    import ast
    src = (ROOT / "tools" / "live_seed.py").read_text(encoding="utf-8")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    assert fn is not None, "live_seed.main not found"
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "requeue_for_dedup"]
    assert calls, (
        "main() no longer calls requeue_for_dedup, so the dedup pass exists as "
        "a function nothing invokes.")
