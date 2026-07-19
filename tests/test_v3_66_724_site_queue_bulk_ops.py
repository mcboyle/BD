"""v3.66.724 -- the site queue bulk-ops surface, and it ACTUALLY WORKS.

The endpoint gap triage listed 12 CONTROL endpoints on /api/sites/<sid>/. Checking each
against the source and the live app rather than trusting the label, the list was wrong in
BOTH directions:

  * jobs/bulk_delete is NOT missing a control -- it has one that is DEAD. SiteActions.tsx
    renders "Delete ALL jobs" behind a hard-confirm ("DELETE ALL JOBS"), and its generic
    mutation posts {} to /api/sites/<sid>/${suffix}. The endpoint requires {urls: [...]}
    and answers 400 "urls must be a non-empty list". It fails 100% of the time, AFTER the
    operator types the scary confirmation. And the label lies twice: the endpoint has no
    "all" semantic AT ALL -- it deletes only URLs you name. No body that button could send
    would do what it says.

      A DEAD CONTROL IS WORSE THAN A MISSING ONE. A missing control tells the truth. This
      one lets an operator believe the jobs are gone.

  * accounts/rotate and captcha/test are FALSE POSITIVES -- they are wired, through the
    same generic ${suffix} POST, and they take no body, so {} is correct for them. They
    read as CONTROL only because the parity scanner cannot resolve a two-interpolation
    template (`/api/sites/${siteId}/${suffix}`).

  * bulk_reorder is a SHADOW of jobs/reorder (which the SortableQueueGroup already uses).
    Deliberately NOT wired here: a second path to the same job is not reachability, it is
    debt. Flagged for removal.

WHY THE LEDGER COULD NOT SEE ANY OF THIS: its denominator is "route literals appearing in
frontend source". So it cannot see a control wired through a template (false negative), and
it cannot see whether a wired control WORKS (a dead control reads as wired). It measures the
presence of a STRING, not the existence of a working CONTROL.

    Hence the rule this suite enforces: assert the ROUTE PATH **and the request BODY**.
    Body shape is exactly what the ledger is blind to, and it is exactly what was broken.

RED-first: every FE assertion below fails on pristine v3.66.723.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FE = os.path.join(ROOT, "frontend", "src")


def _read(*parts):
    with open(os.path.join(FE, *parts), encoding="utf-8") as fh:
        return fh.read()


def _queue():
    return _read("routes", "Queue.tsx")


def _site_actions():
    return _read("routes", "SiteActions.tsx")


# ── the dead control ────────────────────────────────────────────────────────
def test_the_lying_delete_all_jobs_button_is_gone():
    """It posted {} to an endpoint demanding {urls:[...]} -> 400, every time. And it
    promised "ALL", which the endpoint cannot do at any body. There is no fix that keeps
    the button; the control has to move to where a selection exists."""
    src = _site_actions()
    assert "Delete ALL jobs" not in src, (
        "the 'Delete ALL jobs' control is still present -- it posts {} and the endpoint "
        "answers 400 'urls must be a non-empty list'. It cannot ever work.")
    assert "jobs/bulk_delete" not in src, (
        "SiteActions still routes jobs/bulk_delete through the bodyless generic ${suffix} "
        "POST; that call shape is precisely the bug")


def test_bulk_delete_is_reachable_from_the_selection_instead():
    """The control belongs where the URLs are: the queue selection."""
    src = _queue()
    assert "jobs/bulk_delete" in src


def _call_windows(src, suffix, window=200):
    """Every occurrence of the route literal, plus the text that follows it.

    Asserting `"priority" in src` is WORTHLESS here: Queue.tsx already contains the word
    'priority' for an unrelated single-job mutation, so such a test passes on a button that
    sends nothing. (I wrote three of those in the first draft of this file, one hour after
    fixing the identical bug in 711. The shape is genuinely easy to fall into.) So scope the
    assertion to the CALL SITE and derive the body from it.

    ALL occurrences, not the first: a PROSE COMMENT mentioning the route will otherwise
    shadow the real call site and fail the test for the wrong reason (it did).
    """
    out, start = [], 0
    while True:
        ix = src.find(f"/{suffix}", start)
        if ix == -1:
            break
        out.append(src[ix:ix + window])
        start = ix + 1
    assert out, f"{suffix} is not called at all"
    return out


def _body_has(src, suffix, key):
    """True if SOME call site for `suffix` sends `key` in its body."""
    return any(key in w for w in _call_windows(src, suffix))


def test_destructive_delete_is_confirm_gated():
    """Scoped to the delete control, not the whole file."""
    q = _queue()
    assert "confirmDelete" in q or "deleteConfirm" in q, (
        "bulk delete has no dedicated confirm gate")


# ── the six genuinely-unwired ops, asserted by ROUTE PATH ───────────────────
@pytest.mark.parametrize("suffix", [
    "bulk_pause",
    "bulk_resume",
    "bulk_retry",
    "bulk_priority",
    "jobs/bulk_mark",
    "jobs/bulk_priority",
])
def test_queue_page_calls_the_route(suffix):
    """Assert the ROUTE PATH -- not a label. A test asserting `"bulk_pause" in src` passes
    on a BUTTON THAT CALLS NOTHING; that is how the 711 gate died."""
    src = _queue()
    assert f"/{suffix}" in src, f"{suffix} is not called from the queue page"


# ── and the BODY, which is what the ledger cannot see ───────────────────────
@pytest.mark.parametrize("suffix", [
    "bulk_pause", "bulk_resume", "bulk_retry", "bulk_priority",
    "jobs/bulk_mark", "jobs/bulk_priority", "jobs/bulk_delete",
])
def test_every_bulk_call_sends_urls(suffix):
    """THE 724 BUG IN ONE ASSERTION. Every one of these endpoints validates {urls: [...]}
    and 400s without it. A wired button with an empty body is a DEAD button -- and the
    reachability ledger scores it as reachable, because it can only see the string."""
    assert _body_has(_queue(), suffix, "urls"), (
        f"{suffix} is called with no urls in its body -- it will 400 exactly like the "
        f"bodyless control it replaces did")


def test_priority_calls_send_a_priority():
    for suffix in ("bulk_priority", "jobs/bulk_priority"):
        assert _body_has(_queue(), suffix, "priority"), f"{suffix} sends no priority"


def test_mark_sends_a_status():
    """jobs/bulk_mark requires status in {pending, failed, needs_review, done}."""
    assert _body_has(_queue(), "jobs/bulk_mark", "status"), (
        "jobs/bulk_mark sends no status -> 400")


# ── the per-site fan-out ────────────────────────────────────────────────────
def test_selection_is_grouped_by_site_before_dispatch():
    """The queue selection spans SITES (keys are `${site_id}|${url}`), but every one of
    these endpoints is PER-SITE (/api/sites/<sid>/...). Dispatching a cross-site selection
    at a single sid would silently drop every job belonging to the other sites -- an
    operator would select 20 and pause 7, with a success toast. The selection must be
    grouped by site and fanned out."""
    src = _queue()
    assert "groupBySite" in src, (
        "no per-site grouping helper; a cross-site selection cannot be dispatched to "
        "per-site endpoints without silently dropping jobs")


# ── backend contract pins (guard the bug from coming back) ──────────────────
def _client():
    from bulk_downloader.app import app

    return app.test_client()


def _csrf(c):
    c.get("/")
    t = (c.get("/api/csrf").get_json() or {}).get("csrf_token")
    return {"X-CSRFToken": t, "X-CSRF-Token": t, "Content-Type": "application/json"}


def test_bulk_delete_still_rejects_an_empty_body():
    """Pin the contract that the dead button violated. If this ever starts returning 200
    on {}, a bodyless caller becomes silently 'fine' again."""
    import bulk_downloader.app as A

    class _R:
        def bulk_delete(self, urls):
            return len(urls)

    A.runners["t724"] = _R()
    try:
        c = _client()
        r = c.post("/api/sites/t724/jobs/bulk_delete", json={}, headers=_csrf(c))
        assert r.status_code == 400
        assert "urls" in (r.get_json() or {}).get("error", "")
    finally:
        A.runners.pop("t724", None)


SUFFIXES = ("bulk_pause", "bulk_resume", "bulk_retry", "bulk_priority",
            "jobs/bulk_mark", "jobs/bulk_priority", "jobs/bulk_delete")


def test_browser_session_without_a_token_is_refused():
    """The browser case: a cookie session and no X-CSRF-Token -> 403."""
    c = _client()
    c.get("/")  # establish bd_session
    for suffix in SUFFIXES:
        r = c.post(f"/api/sites/t724/{suffix}", json={"urls": ["u"]})
        assert r.status_code == 403, f"{suffix} accepted a session request with no token"


def test_cross_origin_write_is_refused_even_without_a_session():
    """The ACTUAL CSRF attack is a browser on another origin, and the sessionless path
    must not be a way around the gate.

    Worth stating plainly, because I got this wrong first: a bare test_client POST (no
    session, no Origin) DOES reach the handler and mutate. That looks like a hole and is
    not one -- _check_csrf deliberately falls through for sessionless, Origin-less callers
    (curl/CLI), which cannot be CSRF vectors, since CSRF requires a BROWSER riding ambient
    credentials. The browser case is closed by the Origin check below. Pin it, so nobody
    'simplifies' that fall-through away later thinking it is dead code."""
    import bulk_downloader.app as A

    class _R:
        def __init__(self):
            self.calls = []

        def bulk_pause(self, urls):
            self.calls.append(urls)
            return len(urls)

    r_ = _R()
    A.runners["t724"] = r_
    try:
        c = _client()
        resp = c.post("/api/sites/t724/bulk_pause", json={"urls": ["u"]},
                      headers={"Origin": "http://evil.example", "Host": "localhost:5555"})
        assert resp.status_code == 403
        assert not r_.calls, "a cross-origin write MUTATED the queue"
    finally:
        A.runners.pop("t724", None)
