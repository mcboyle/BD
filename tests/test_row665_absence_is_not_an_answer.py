"""Row 665, second arc -- the queue API must never answer ABSENCE about a job.

WHAT THE FIRST ARC SETTLED, AND WHAT IT LEFT. `test_row665_terminal_jobs_are_
visible.py` made every terminal outcome addressable by URL *while the job is
still in* ``runner.jobs``, and pinned the bounded response shape. That is the
"finished job is visible" half. The half row 665 still names -- "polling cannot
distinguish finished from never-existed" -- is about the job that is NOT in
``runner.jobs``, or is in it wearing a status the endpoint does not recognise.
In both cases today the caller is handed silence, and silence is the one answer
that is wrong in two opposite ways at once.

THE TWO WAYS A JOB GOES SILENT, both measured on 66178294:

  1. UNRECOGNISED STATUS. `api_queue_v2` classifies with
     `if s == "running" / elif s == "pending" / elif s in
     _QUEUE_V2_TERMINAL_STATUSES`. A status outside all three -- a new state, a
     typo, a half-written migration -- matches no arm, so the job is appended to
     no bucket and leaves the payload entirely. The endpoint does not say "I
     don't know what this is"; it says nothing, which reads as "there is no such
     job".

  2. EVICTION AND THE CAP. ``runner.jobs`` is not a durable census. It is
     cleared outright by a "replace" queue import (app_sites_queue.py, `runner.
     jobs.clear()`), it does not survive a restart, and the terminal bucket is
     capped at 200. A job that finished and then fell out of any of those three
     is absent from the snapshot, and `/api/queue/v2/job_log` -- the only
     per-job lookup on this surface -- answers for it with
     ``current = {"status": "", ...}``: byte-identical to the answer for a URL
     the server has genuinely never seen.

WHY AN EMPTY STRING IS NOT A STATE. "I never got to look" and "I looked and
there is nothing" lead to opposite operator actions -- retry versus stop
waiting -- so a surface that renders both as the same bytes has not reported a
state, it has deleted one. The correction is the third value: an explicit
``unknown``, which claims only what the server can actually support. The server
cannot prove a negative here (history is prunable, memory is not durable), so
``unknown`` is not a placeholder for a better answer later; it IS the true
answer, and the defect was ever pretending otherwise.

AND THE THIRD, WHICH THE FOURTH ARM CAN REINTRODUCE. Every bucket on this
surface is capped at 200. If the new bucket's COUNT were the length of the
capped list rather than the true total, then 201 unclassifiable jobs and
exactly 200 would report identically -- and since a single renamed status
upstream makes every job in the queue unclassified at once, that is precisely
the case where the number matters. An overflow hidden behind a number is the
same defect as a job hidden behind an empty payload, so the cap is pinned here
with 201 seeded jobs, the same way the first arc pins the terminal cap with
range(202).

DENOMINATOR. The classification arms in `api_queue_v2` are three (running,
pending, terminal) over the eight statuses in `_QUEUE_V2_TERMINAL_STATUSES`;
this file adds the fourth arm, its cap-versus-count contract, and the per-job
lookup's third value. Counts below are exact and derived from the fixture,
never from the response.
"""
from __future__ import annotations

import importlib
import threading

from flask import Flask

# "repo-wide", for the same reason the first arc of row 665 carries that marker:
# a gate's scope is decided by what it ASSERTS ABOUT, and what this file asserts
# about is the standing promise that the queue API never answers ABSENCE when it
# means "I cannot classify this" or "I no longer hold this". That promise is not
# a property of one module's syntax; every caller in the tree that polls a job
# depends on it, and a diff-derived band that happens to touch neither row-665
# file would otherwise run neither half of the contract. The two halves are one
# contract -- the first says a terminal job stays addressable while it is in
# runner.jobs, this one says absence is reported as a state -- so they carry the
# same marker and ride the same CI shard. Declaring repo-wide is also what makes
# the _DECLARED entry in tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py
# legal: that file's partition requires a declared gate to be either repo-wide
# or a member of the closed legacy set, and the legacy set may only shrink.
BD_GATE_SCOPE = "repo-wide"

app_queue = importlib.import_module("bulk_downloader.app_queue")

_SITE = "row665b-site"
_COLOR = "#000000"


class _Runner:
    """The surface both endpoints actually touch: a lock, jobs, an event log."""

    def __init__(self, jobs, event_log=None):
        self._lock = threading.Lock()
        self.jobs = jobs
        self._recent_per_min = 0
        self._event_log = list(event_log or [])


def _client(monkeypatch, jobs, event_log=None):
    runner = _Runner(jobs, event_log)
    monkeypatch.setattr(app_queue, "_app_runners", lambda: {_SITE: runner})
    monkeypatch.setattr(
        app_queue, "_app_s_cfg", lambda: {_SITE: {"name": "Row 665b Site"}})
    monkeypatch.setattr(app_queue, "_m2_avatar_color", lambda _name: _COLOR)
    flask_app = Flask("row665b-absence")
    flask_app.register_blueprint(app_queue.queue_bp)
    return flask_app.test_client(), runner


def _url(tag):
    return f"https://example.invalid/row665b/{tag}"


def _every_url_in(body):
    """Every URL the payload mentions anywhere, across all job buckets."""
    seen = set()
    for bucket in ("running", "waiting", "terminal", "unclassified"):
        for entry in body.get(bucket) or []:
            seen.add(entry.get("url"))
    return seen


def test_the_fixture_builds_the_shape_before_any_verdict(monkeypatch):
    """POSITIVE CONTROL. Every zero asserted below is only evidence if this
    harness can produce a NONZERO in the same call. Seed one ordinary done job
    and prove the endpoint reports it; if this fails, no absence measured in
    this file means anything."""
    target = _url("control-done")
    client, runner = _client(
        monkeypatch, {target: {"status": "done", "filename": "c.mp4"}})
    assert len(runner.jobs) == 1, runner.jobs

    body = client.get("/api/queue/v2").get_json()

    assert body["ok"] is True, body
    assert [e["url"] for e in body["terminal"]] == [target], body
    assert _every_url_in(body) == {target}, body


def test_an_unrecognised_status_is_reported_not_dropped(monkeypatch):
    """THE FIRST SILENCE. A job whose status matches no classification arm must
    still appear, wearing its raw status, in an explicit bucket."""
    known = _url("done")
    odd = _url("future-state")
    client, runner = _client(monkeypatch, {
        known: {"status": "done", "filename": "k.mp4"},
        odd: {"status": "future_state", "filename": "o.mp4",
              "message": "written by a newer worker"},
    })
    assert runner.jobs[odd]["status"] == "future_state"
    assert runner.jobs[odd]["status"] not in app_queue._QUEUE_V2_TERMINAL_STATUSES

    body = client.get("/api/queue/v2").get_json()

    assert odd in _every_url_in(body), (
        "a job the endpoint cannot classify left the payload entirely, so a "
        "caller polling for it is told nothing -- which is the same bytes the "
        "caller gets for a URL that never existed. buckets=%r"
        % ({b: body.get(b) for b in
            ("running", "waiting", "terminal", "unclassified")},))
    unclassified = body.get("unclassified")
    assert unclassified is not None, (
        "no explicit bucket for a job whose status is not recognised: %r"
        % (sorted(body),))
    assert [e["url"] for e in unclassified] == [odd], unclassified
    assert unclassified[0]["status"] == "future_state", unclassified
    assert body.get("unclassified_count") == 1, body.get("unclassified_count")


def test_the_unclassified_bucket_is_capped_but_its_count_is_the_whole_truth(
        monkeypatch):
    """THE OVERFLOW, which is the same defect one bucket over.

    The bucket is capped at 200 like its siblings, so the LIST alone can still
    answer "200" for any larger truth -- and one status rename upstream is
    enough to make every job in the queue unclassified at once, which is exactly
    when a caller most needs to know the real size. The count is therefore the
    TOTAL, not the length of the capped list, and that distinction is the whole
    point of the bucket: a capped list whose count is also capped has moved the
    hiding from the payload into the number.

    Seed 201 so the cap is crossed by exactly one. The first arc pins the same
    property for the terminal bucket with range(202) at
    test_row665_terminal_jobs_are_visible.py; this is that assertion for the
    bucket this arc adds."""
    jobs = {
        _url(f"future-{index:03d}"): {
            "status": "future_state", "filename": f"f{index:03d}.mp4",
        }
        for index in range(201)
    }
    client, runner = _client(monkeypatch, jobs)
    assert len(runner.jobs) == 201, len(runner.jobs)
    expected_urls = list(jobs)[:200]

    body = client.get("/api/queue/v2").get_json()

    unclassified = body.get("unclassified")
    assert unclassified is not None, (
        "there is no explicit bucket at all, so 201 unclassifiable jobs left "
        "the payload and the overflow question cannot even be asked: %r"
        % (sorted(body),))
    assert len(unclassified) == 200, (
        "the capped list itself is the sibling convention and must stay "
        "bounded: %r" % (len(unclassified),))
    assert [e["url"] for e in unclassified] == expected_urls, (
        "the capped list must be the first 200 in classification order, not an "
        "arbitrary 200")
    assert body.get("unclassified_count") == 201, (
        "the count reported the length of the CAPPED LIST, not the number of "
        "jobs the endpoint could not classify, so 201 unclassifiable jobs are "
        "indistinguishable from exactly 200 -- the overflow is hidden behind a "
        "number instead of behind an empty payload, which is the same defect "
        "this bucket exists to end. count=%r len(list)=%r"
        % (body.get("unclassified_count"), len(unclassified)))


def test_a_recognised_job_never_lands_in_the_unclassified_bucket(monkeypatch):
    """NEGATIVE CONTROL for the bucket, and it fails for the intended reason.

    A correction that routed EVERYTHING through the new bucket would satisfy the
    test above while destroying the classification this row's first arc built.
    Seed one job of each recognised kind and pin the bucket empty with an exact
    count -- the number is 0 because the fixture holds three jobs and all three
    are classifiable, not because the bucket is inert."""
    client, runner = _client(monkeypatch, {
        _url("run"): {"status": "running", "filename": "r.mp4"},
        _url("pend"): {"status": "pending", "priority": 1, "queued_ts": 2},
        _url("term"): {"status": "failed", "message": "boom"},
    })
    assert len(runner.jobs) == 3, runner.jobs

    body = client.get("/api/queue/v2").get_json()

    assert len(body["running"]) == 1, body["running"]
    assert len(body["waiting"]) == 1, body["waiting"]
    assert len(body["terminal"]) == 1, body["terminal"]
    assert body.get("unclassified") == [], (
        "three classifiable jobs were seeded and the unclassified bucket is "
        "not empty, so the new arm is swallowing jobs the endpoint can "
        "already name: %r" % (body.get("unclassified"),))
    assert body.get("unclassified_count") == 0, body.get("unclassified_count")


def test_a_job_absent_from_the_queue_is_unknown_not_never_existed(monkeypatch):
    """THE SECOND SILENCE. The per-job lookup must name the third state."""
    evicted = _url("finished-then-evicted")
    client, runner = _client(monkeypatch, {}, event_log=[])
    assert evicted not in runner.jobs, runner.jobs

    body = client.get(
        "/api/queue/v2/job_log",
        query_string={"site_id": _SITE, "url": evicted}).get_json()

    assert body["ok"] is True, body
    current = body["current"]
    assert current.get("known") is False, (
        "the lookup does not say whether it knows this job at all: %r"
        % (current,))
    assert current.get("state") == "unknown", (
        "a URL absent from runner.jobs was answered with %r. runner.jobs is "
        "cleared by a replace-import, lost on restart, and the terminal view "
        "is capped at 200, so absence here cannot mean the job never existed "
        "-- and an empty status string is read as exactly that."
        % (current.get("state"),))


def test_a_present_job_is_known_and_keeps_its_own_state(monkeypatch):
    """NEGATIVE CONTROL for the lookup, failing for the intended reason.

    A correction that answered "unknown" unconditionally would pass the test
    above and destroy the lookup. This pins the other side: a job that IS in
    runner.jobs is known, and its state is its own status, not the placeholder."""
    present = _url("present-done")
    client, runner = _client(
        monkeypatch,
        {present: {"status": "done", "filename": "p.mp4", "message": "ok"}},
        event_log=[{"ts": 1, "kind": "info", "message": "started",
                    "url": present}])
    assert len(runner.jobs) == 1, runner.jobs

    body = client.get(
        "/api/queue/v2/job_log",
        query_string={"site_id": _SITE, "url": present}).get_json()

    current = body["current"]
    assert current.get("known") is True, current
    assert current.get("state") == "done", (
        "a job sitting in runner.jobs with status 'done' was reported as %r"
        % (current.get("state"),))
    assert current["status"] == "done", (
        "the pre-existing status key changed meaning; the SPA reads it: %r"
        % (current,))
    assert len(body["events"]) == 1, body["events"]


def test_an_unrecognised_status_is_still_that_status_to_the_lookup(monkeypatch):
    """The two halves agree. A job the snapshot could not classify is not
    'unknown' to the lookup -- the server knows exactly what it holds, it just
    cannot bucket it. Conflating the two would put a live job in the same class
    as an evicted one."""
    odd = _url("odd")
    client, _runner = _client(
        monkeypatch, {odd: {"status": "future_state", "filename": "o.mp4"}})

    body = client.get(
        "/api/queue/v2/job_log",
        query_string={"site_id": _SITE, "url": odd}).get_json()

    current = body["current"]
    assert current.get("known") is True, current
    assert current.get("state") == "future_state", current
