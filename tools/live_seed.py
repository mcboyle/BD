#!/usr/bin/env python3
"""Seed synthetic INPUT so the live checks can exercise real code paths.

Several live checks WARN because nothing has exercised them -- no queued URLs,
no completed downloads, no login events. Those warnings are honest (and since
v3.66.818 they no longer fail the capture verdict), but on a fresh capture host
they are also permanently unactionable: only a human queueing real work clears
them. This tool supplies that work.

WHAT THIS TOOL MAY AND MAY NOT DO
---------------------------------
The failure mode it must not create is a VACUOUS PASS: a check that goes green
because the fixture handed it the answer proves the fixture ran, not that BD
works. Fourteen honest WARNs converted into fourteen fake PASSes would be
strictly worse than the status quo -- real signal destroyed, false confidence
manufactured. CLAUDE.md 0 is the rule; this is its sharpest edge.

So the contract is:

  1. SYNTHETIC DATA IS INPUT, NEVER OUTPUT. Queueing a URL so the queue has
     something to preserve across a restart is legitimate: BD still has to
     persist it, rehydrate it and report it. Writing the row that BD's own
     persistence was supposed to write is not.

  2. EVERY WRITE GOES THROUGH THE APP'S HTTP API. Never raw SQL. Two reasons,
     both load-bearing. It forces each write through the same code the UI
     drives -- which IS rule 1. And bulk_downloader/db.py has NO triggers
     (verified: no CREATE TRIGGER anywhere), so db_log() maintains history_fts
     and library_record by hand; a bare INSERT INTO history would be visible to
     a COUNT(*) but invisible to search and absent from the library, a
     denominator split manufactured by the fixture itself.

  3. EVERYTHING CARRIES A MARKER. A synthetic PASS must be distinguishable from
     an organic one in the output, and teardown must be able to find exactly
     what it created and nothing else.

  4. WHAT CANNOT BE HONESTLY EXERCISED STAYS A WARN. An honest warning beats a
     fake pass. Checks needing a real remote site, real credentials or a real
     segmented stream are out of scope by design, not by omission.

WHY tools/ AND NOT live_tests/
------------------------------
The entire safety property of the live_tests package is that it CANNOT write:
Context exposes GET and a mode=ro database handle, so the suite is safe to
point at a production deployment. Putting a writer inside it would destroy that
invariant for every future check. The seeder is a separate tool that the
operator (or capture.sh) invokes deliberately.

USAGE
    python3 tools/live_seed.py --seed --count 3
    python3 tools/live_seed.py --seed --start       # ...and run the queue
    python3 tools/live_seed.py --seed --dry-run     # report intent, change nothing
    python3 tools/live_seed.py --vpn-tunnel         # give L30 a subject (inert)
    python3 tools/live_seed.py --teardown
"""
from __future__ import annotations

import argparse
import json
import uuid
import os
import sys
import time
import urllib.error
import urllib.request


# Everything this tool creates is tagged with this. It appears in seeded URLs
# and site names so (a) a human reading a live-test log can tell synthetic
# state from organic state at a glance, and (b) teardown has an exact
# predicate rather than a heuristic.
SEED_MARKER = "bdseed"

# Unique per PROCESS, which is per run: capture.sh invokes this file as a fresh
# process each time. Without it every run seeded byte-identical URLs, so run N+1
# dedup'd against run N's history rows -- measured on the box as
#   "status": "skipped_duplicate",
#   "message": "Duplicate of history #1 (prior download, 2026-07-29T00:23:43)"
# where that timestamp was the PREVIOUS capture. Nothing downloaded, and L11/L12
# reported "no completed downloads" as though BD had failed.
#
# The collision-avoidance rationale stands on its own: not colliding is
# cheaper and safer than depending on a destructive clear. (This paragraph
# used to claim history is append-only and that teardown structurally could
# not clear the rows; that claim was FALSE -- db.py:988-992 records the
# retraction -- and --teardown --clear-history now removes the marked rows
# over POST /api/batch/delete. The nonce is still load-bearing: the clear is
# OPT-IN, so runs must not collide when nobody passes it.)
#
# It rides in the QUERY, beside the marker, so routing is untouched and
# _is_seeded() still matches.
_RUN_NONCE = uuid.uuid4().hex[:8]

# The local fixture origin. tools/fixture_site.py serves deterministic media
# here, so a seeded download exercises the real fetch path without touching any
# third-party site.
FIXTURE_ORIGIN = "http://127.0.0.1:8899"

DEFAULT_BASE_URL = "http://127.0.0.1:5555"

# How long --start will wait for the seeded queue to reach terminal states
# before giving up and SAYING it gave up. Bounded on purpose: capture.sh runs
# unattended, and an unbounded wait turns one stuck job into a hung capture.
# The fixture serves 4-16 KB files, so the normal path settles in seconds; this
# budget exists for the abnormal one.
DEFAULT_SETTLE_TIMEOUT = 180.0
DEFAULT_SETTLE_INTERVAL = 2.0

# Terminal QUEUE statuses, derived from what the runner actually writes:
# _update_job() is called with running/needs_review/done/pending/failed/
# dead_letter/stopped/skipped_duplicate, and runner.py's own _RUN_TERMINAL adds
# error and cancelled. The complement -- {pending, running} -- is the work that
# is still owed.
#
# Enumerating the TERMINAL side rather than the pending side is deliberate. If
# BD ever grows a status this tool has not heard of, an unrecognised value here
# leaves the URL unresolved: the wait spends its budget and reports the unknown
# status by name. Enumerating the pending side instead would make the same
# unknown read as "finished", which is the silent direction (CLAUDE.md 0).
#
# skipped_duplicate is terminal and that is load-bearing for L14: it is the
# exact outcome the seed set's deliberate repeat exists to produce, so treating
# it as unfinished would burn the whole budget on a success.
TERMINAL_QUEUE_STATUSES = frozenset({
    "done", "failed", "error", "needs_review", "stopped",
    "cancelled", "skipped_duplicate", "dead_letter",
})

# Page sizes for the two read-only listings this tool polls. Both endpoints cap
# server-side; these are well above anything the seed set can produce.
_QUEUE_PAGE = 500
_HISTORY_PAGE = 500

# Rows per /api/batch/delete call. _build_query truncates id_in at 1000
# SILENTLY (batch_ops.py:76), and its `LIMIT ?` is appended AFTER the IN
# clause (batch_ops.py:100-101), so an under-sized limit silently drops ids
# too (measured: 6 ids with limit=2 matched 2). Both are why the clear chunks
# and passes limit=len(chunk) rather than relying on any default.
_CLEAR_CHUNK = 200
# Each round reads at most _HISTORY_PAGE rows, so 20 rounds bound the clear at
# 10k rows. Deliberately bounded: capture.sh runs this unattended.
_CLEAR_MAX_ROUNDS = 20
# GET /api/library/browse clamps limit to 1..500 (app_library.py:105) and pages
# by after_id cursor (l.id < after_id for the default descending sort,
# library.py:331-336). next_cursor is non-None only on a full page
# (library.py:360-361). 40 pages bound the twin scan at 20k rows; capture.sh
# runs this unattended.
_TWIN_PAGE = 500
_TWIN_MAX_PAGES = 40


class SeedRefused(RuntimeError):
    """Raised when seeding would be unsafe, or when safety cannot be shown."""


class Client:
    """Minimal HTTP client for the local app.

    Deliberately sends NO cookie and NO Origin header: bulk_downloader/app.py's
    _check_csrf() exempts exactly that shape (no session cookie -> CSRF does not
    apply; no Origin -> the cross-origin refusal does not trigger), so a
    state-changing POST from this tool is accepted without needing to scrape a
    CSRF token out of a browser session. A configured BD_AUTH_TOKEN is still
    honoured, because _check_token() gates everything when auth is enabled.
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, token: str | None = None,
                 timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token if token is not None else os.environ.get("BD_AUTH_TOKEN", "")
        self.timeout = timeout

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(self, method: str, path: str, payload=None):
        url = self.base_url + path
        data = None
        headers = self._headers()
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        # v3.66.819 -- THE STATUS CODE IS NOT NOISE.
        #
        # This used to read HTTPError's body and discard exc.code. A POST to a
        # GET-only route (which /api/queue/v2 is -- app_queue.py:170 declares no
        # methods=) answers 405 with an HTML error page, so json.loads failed and
        # the caller received {"ok": False, "error": "non-JSON response: <!doctype
        # html>..."} -- an error wearing the shape of an ordinary return value,
        # indistinguishable from a route that replied oddly. requeue_for_dedup
        # then recorded it and waited for a dedup decision that could not come.
        #
        # The status is the one fact that separates 405 (wrong route) from 400
        # (wrong payload) from a refused connection, so it travels with the body.
        # Added only on failures: a 2xx keeps its exact previous shape, because
        # every other call site in this file reads the app's own JSON directly.
        status = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = getattr(resp, "status", None)
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", "replace")
        except Exception as exc:  # unreachable app, DNS, refused connection
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            body = json.loads(raw)
        except ValueError:
            return {"ok": False, "status": status,
                    "error": (f"HTTP {status}: non-JSON response: "
                              f"{raw[:200]}")}
        if isinstance(body, dict) and status is not None and status >= 400:
            # setdefault, not assignment: the app's own error bodies already say
            # ok=False and carry the reason, and that reason is more useful than
            # anything derivable from the code alone.
            body.setdefault("ok", False)
            body["status"] = status
        return body

    def get(self, path: str):
        return self._request("GET", path)

    def post(self, path: str, payload):
        return self._request("POST", path, payload)

    def delete(self, path: str):
        return self._request("DELETE", path)


def _queue_snapshot(client):
    """Read the queue, or raise SeedRefused if it cannot be read.

    An unreadable answer is not "probably empty". Treating it as empty is the
    gate-that-cannot-see-its-subject failure, so it is refused instead.
    """
    body = client.get("/api/queue/v2")
    if not isinstance(body, dict) or not body.get("ok"):
        raise SeedRefused(
            "cannot read /api/queue/v2, so this host's state is UNKNOWN and "
            f"seeding is refused (response: {str(body)[:200]}). Start the app "
            "first, or pass --force if you are certain."
        )
    return body


def _is_seeded(entry) -> bool:
    return SEED_MARKER in str(entry.get("url", ""))


def preflight(client, *, force: bool = False) -> dict:
    """Refuse to seed a host that already holds real work.

    Returns the queue snapshot when seeding is safe. Raises SeedRefused
    otherwise -- including when safety cannot be determined at all.
    """
    body = _queue_snapshot(client)
    if force:
        return body
    real = [
        entry
        for key in ("running", "waiting")
        for entry in (body.get(key) or [])
        if isinstance(entry, dict) and not _is_seeded(entry)
    ]
    if real:
        raise SeedRefused(
            f"queue is not empty: {len(real)} real entr(y/ies) already present. "
            "Seeding could be mistaken for the operator's own work and teardown "
            "would be ambiguous. Refusing; pass --force to override."
        )
    done_today = int(body.get("done_today_count") or 0)
    if done_today:
        # Until --start existed, a seeded download never COMPLETED, so nothing
        # this tool created could reach this counter and calling the total
        # "real" was sound. A seeder that finishes downloads makes that
        # sentence false: the next --seed on the same host would refuse while
        # naming the seeder's own completions as the operator's work.
        #
        # done_today_count is a bare integer built from runner job state
        # (app_queue.py's api_queue_v2 counts jobs whose ts starts with
        # today's date); it carries no URL, so unlike the running/waiting
        # check above there is nothing for _is_seeded() to match on. The
        # refusal stays -- erring toward refusing is the right direction --
        # but it must not assert an attribution it cannot make.
        marked = _marked_site_ids(client)
        if marked:
            raise SeedRefused(
                f"{done_today} download(s) completed today on this host, and "
                f"{len(marked)} {SEED_MARKER}-marked site(s) are still "
                f"present. done_today_count is a bare integer with no per-URL "
                f"detail, so it cannot be attributed to a marker: some or all "
                f"of these may be this tool's own from an earlier run. "
                f"Refusing rather than guessing -- run --teardown first, or "
                f"pass --force."
            )
        raise SeedRefused(
            f"{done_today} real download(s) completed today on this host. "
            "Refusing to seed on top of real work; pass --force to override."
        )
    return body


# The seed set. These must be routes tools/fixture_site.py DEFINES -- verify
# against its url_map, not against this list, if you change either.
#
#   [0] /scene/2     1080p page, plain .mp4 link      -> L11 end-to-end
#   [1] /hlspage/2   1080p page, .m3u8 manifest link  -> L12 hls/dash
#   [2] /scene/2     deliberately repeats [0]         -> L14 dedup-skip
#
# The repeat is not a mistake: L14 asserts BD SKIPS a URL it already has, so
# the set has to contain one. It must stay BYTE-IDENTICAL to [0].
#
# WHY PAGES AND NOT MEDIA. Until 2026-07-28 this set was /direct/media/0.mp4
# and /hls/scene/0.m3u8 -- raw media, which BD cannot consume. Measured against
# the real app:
#
#   /direct/media/0.mp4?bdseed=1 -> worker error: Page.goto: Download is starting
#   /hls/scene/0.m3u8?bdseed=1   -> No download button found
#
# BD navigates to a PAGE and scrapes it for a download link; it never fetches
# media directly. So every seeded job failed, and L11/L12/L14 reported "no
# completed downloads" for as long as the seeder has existed -- which reads as
# BD failing to download, when BD was never handed a URL it could consume.
# test_every_seeded_url_resolves_against_the_fixtures_route_map could not see
# this: it proves the fixture SERVES a URL, which is a different subject from
# whether BD can DOWNLOAD it. Both gates now exist.
#
# WHY 2 AND 3 SPECIFICALLY. Scene resolution cycles
# ["480p","720p","1080p","2160p"][sid % 4] and min_resolution defaults to 1080
# (app_kernel.py). /scene/0 and /scene/1 park at "Best is 480p (below 1080p) --
# Approve to force" and wait for a human, so they never reach a terminal state.
#
# WHY /hlspage AND NOT /hls/scene/2.m3u8 FOR L12. A manifest is not navigable
# either -- seeding it produced "No download button found". Until now no fixture
# scene page linked to one: videojs, jwplayer, reslinks, datahref and lazy all
# point at /direct/media/<sid>.mp4, and the only m3u8 anchor in the fixture was
# a nav link on "/". fixture_site.py:/hlspage/<sid> is that missing page --
# identical to /scene/<sid> except its download link is the manifest -- so the
# seeded job navigates a page, scrapes a link, and exercises the real segmented
# path. The seeded URL is therefore a PAGE whose LINK is segmented; asserting
# the seeded URL itself ends in .m3u8 (as the old gate did) asks for something
# BD can never consume.
_SEED_PATHS = (
    "/scene/2",
    "/hlspage/2",
    "/scene/2",
)


def seeded_url(index: int) -> str:
    """A fixture URL the fixture actually serves.

    This used to return f"{FIXTURE_ORIGIN}/{SEED_MARKER}/clipN.mp4", putting
    the marker in the path. fixture_site.py has no /bdseed/ route -- the string
    appears in it zero times -- so every seeded download 404'd and L11, L12 and
    L14 reported "no completed downloads" forever. That reads as BD failing to
    download; BD was never handed a URL that resolves.

    The marker MUST stay in the URL. `_is_seeded()` is
    `SEED_MARKER in entry["url"]`, and both teardown and preflight depend on
    it: without the marker teardown orphans every seeded row, and preflight
    reads seeded work as the operator's real work and refuses the next run.
    So it moves from the PATH -- where it broke routing -- into the QUERY,
    which Flask ignores when matching and which `_is_seeded` still sees.

    The duplicate at index 2 must stay byte-identical to index 0, query
    included, or L14 has nothing to recognise as a repeat.
    """
    return (f"{FIXTURE_ORIGIN}{_SEED_PATHS[index % len(_SEED_PATHS)]}"
            f"?{SEED_MARKER}=1&run={_RUN_NONCE}")


SEED_SITE_NAME = f"{SEED_MARKER} fixture site"


def queue_site_config() -> dict:
    """Config for the site the seeded queue runs on.

    download_dir is deliberately absent: _create_site fills unset fields from
    DEFAULTS and validates paths before creating anything, and this tool is an
    HTTP client -- --base-url may point at a service with a different BD_HOME
    and cwd -- so any path computed HERE is a guess about somebody else's
    filesystem. Same reasoning as login_site_config's empty cookie_file, and
    the same test file enforces it.

    auto_teach_first_run is OFF, and that is the whole reason this builder
    exists rather than a bare {"name": ...}. runner._start_serialized returns
    BEFORE spawning any worker when the flag is on and the site has neither
    learned download selectors nor an applied template: it marks the first URL
    needs_review with "take over to teach download selectors" and leaves the
    runner idle, waiting for a click in the UI. A capture host has no human, so
    --start started nothing at all.

    Measured on 2026-07-28 against the real app in a temp BD_INSTALL_DIR with
    the real fixture on :8899 -- the seeded queue read back
    `needs_review: "Auto-teach: take over..."` plus one untouched `pending`,
    with zero workers spawned. With the flag off the same run reached the
    worker path and produced real download attempts.

    This mirrors login_site_config's identical opt-out for the sibling divert
    in runner_auth.login_async. It is scoped to the site this tool creates and
    owns; no operator site is affected.
    """
    return {
        "name": SEED_SITE_NAME,
        "auto_teach_first_run": False,
        # runner_transport.py:794 defaults skip_if_exists True, so once
        # scene_002.mp4 exists the runner reports the job done, calls
        # dl.cancel() and returns WITHOUT FETCHING. Measured on the deploy
        # host: eight consecutive seeded runs, seven of them skips, while the
        # live check certified the pipeline. The seeded site exists to exercise
        # a download, so it must never skip one.
        #
        # Explicit False, not "" or absent: app.py:4218 fills defaults for any
        # value `in ("", None)`, and False is neither, so it survives -- and
        # survives a restart via the identical guard at app.py:1335-1338.
        # Scoped to the site this tool creates and owns; no operator site is
        # affected, and the flag is read in exactly one place
        # (runner_transport.py:794, off self.config).
        #
        # Consequence, stated because teardown does not clear it: with the skip
        # off, detect.py's safe_dest appends _1, _2, ... so each capture leaves
        # another ~8 KB file in the download directory instead of reusing the
        # one already there.
        "skip_if_exists": False,
    }


def _marked_site_ids(client) -> list:
    """Site ids whose display name carries the marker.

    /api/status returns {sid: {name, config, ...}}, so the name is the
    discriminator. Anything unmarked belongs to the operator and is off limits
    for both seeding and teardown.
    """
    body = client.get("/api/status")
    if not isinstance(body, dict):
        return []
    found = []
    for sid, meta in body.items():
        if not isinstance(meta, dict):
            continue
        if SEED_MARKER in str(meta.get("name", "")):
            found.append(sid)
    return sorted(found)


def _site_name(client, sid: str) -> str:
    """Display name for a site id, from the same /api/status shape
    _marked_site_ids reads (top-level {sid: {name, ...}})."""
    body = client.get("/api/status")
    if not isinstance(body, dict):
        return ""
    meta = body.get(sid)
    return str(meta.get("name", "")) if isinstance(meta, dict) else ""


def ensure_seed_site(client, *, dry_run: bool = False):
    """Return a site id the seeder OWNS, creating one if needed.

    Deliberately never borrows an existing unmarked site. On a working
    deployment the configured site is the operator's REAL one; enqueueing
    synthetic URLs there would show them jobs they did not queue and would make
    teardown ambiguous, because the site itself is not ours to remove.

    RECREATES rather than reuses. This used to return the first marked site,
    which was wrong twice over:

      * `_marked_site_ids` matches the MARKER, and the seeder creates two
        marked sites -- the queue fixture and the login fixture. It returns
        them sorted, and site ids are uuid4().hex[:8], so a reuse run was a
        coin flip between them; landing on the login site queues the seeded
        URLs against a site that never downloads.
      * This client has no put or patch verb (get/post/delete only), so a
        reused site keeps whatever config it was created with. A site created
        before skip_if_exists=False existed would keep skipping forever, which
        makes the flag inert on exactly the runs where it matters -- the ones
        following a teardown that failed and left a site behind.

    Deleting is scoped and safe: DELETE /api/sites/<sid> stops the runner,
    drops the config and queue rows, and never touches the download directory.
    It is issued only for a site whose name is EXACTLY this tool's own.

    A fresh uuid4 site id per run is a useful side effect: it makes site_id a
    per-run key, so a live check reading history scoped to the seeded site
    cannot certify tonight's run with last night's evidence.
    """
    return _ensure_owned_site(client, SEED_SITE_NAME, queue_site_config(),
                              dry_run=dry_run)


def _ensure_owned_site(client, name: str, config: dict, *,
                       dry_run: bool = False):
    """Exact-name select, delete, recreate -- shared by every site this tool owns.

    Factored out when the dedup fixture was added rather than copied, because
    the selection rule is the load-bearing part: EXACT NAME, never the marker
    alone. `_marked_site_ids` matches the marker, and this tool now creates
    three marked sites (queue, login, dedup); selecting on the marker would let
    any one of them delete the others, which is the same coin-flip defect that
    once queued the seeded URLs against the login fixture.
    """
    ours = [sid for sid in _marked_site_ids(client)
            if _site_name(client, sid) == name]
    if dry_run:
        return None
    for sid in ours:
        client.delete(f"/api/sites/{sid}")
    created = client.post("/api/sites", config)
    sid = (created or {}).get("id") if isinstance(created, dict) else None
    if not sid:
        raise SeedRefused(
            f"could not create the site named {name!r} "
            f"(response: {str(created)[:200]})"
        )
    return sid


def seed_queue(client, count: int = 3, *, site_id: str | None = None,
               dry_run: bool = False) -> dict:
    """Enqueue `count` marked fixture URLs.

    This is INPUT: BD still has to accept, persist, rehydrate and report each
    URL. L28 asserts that running+waiting+done_today is conserved across a
    service restart -- the seeder supplies URLs to preserve, and every part of
    the preserving is BD's own work.
    """
    urls = [seeded_url(i) for i in range(count)]
    planned = {"action": "seed_queue", "urls": urls, "marker": SEED_MARKER}
    if dry_run:
        planned["dry_run"] = True
        return planned
    sid = site_id or ensure_seed_site(client)
    results = []
    for url in urls:
        results.append(client.post("/api/queue/v2/add_url",
                                   {"site_id": sid, "url": url}))
    planned["site_id"] = sid
    planned["results"] = results
    return planned


def start_seeded_site(client, site_id: str) -> dict:
    """Start the runner for a site this tool PROVABLY owns.

    A queued URL that is never started is input nothing consumed. L11, L12 and
    L14 all gate on a COMPLETED download; before this, the seeder placed three
    URLs and stopped, so all three reported "no completed downloads yet"
    forever -- which reads as BD failing to download when BD was never asked.
    live_tests/ cannot ask (Context has get/log/ro_db and no write verb, and
    that read-only property is what makes the suite safe to point at
    production), so the ask belongs here, to the writer.

    THE SAFETY PREDICATE IS THE MARKER, exactly as teardown's is. Starting an
    unmarked site would drain the OPERATOR'S queue -- real media, from real
    sites, that they did not ask for right now. `_marked_site_ids()` reads
    /api/status and keeps only names carrying SEED_MARKER; it returns [] for
    any response it cannot parse, so an unreadable /api/status refuses here
    rather than proceeding. Unknown is a third state and it fails.

    Refuses rather than waits when the app declines the start, or accepts it
    and reports `blocked_by` (rate-limited / low disk): in both cases the queue
    will not drain, and spending the settle budget on a known negative buys
    nothing but a slower capture.
    """
    owned = _marked_site_ids(client)
    if site_id not in owned:
        seen = (str(owned) if owned else
                "none -- /api/status listed no marked site, or could not "
                "be read")
        raise SeedRefused(
            f"refusing to start site {site_id!r}: it is not among the "
            f"{SEED_MARKER}-marked sites this tool owns ({seen}). Starting "
            f"an unmarked site would run the operator's own downloads."
        )
    resp = client.post(f"/api/sites/{site_id}/start", {})
    if not isinstance(resp, dict) or not resp.get("ok"):
        raise SeedRefused(
            f"the app declined to start seeded site {site_id!r} "
            f"(response: {str(resp)[:200]})"
        )
    blocked = resp.get("blocked_by")
    if blocked:
        raise SeedRefused(
            f"seeded site {site_id!r} started but BD reports it blocked by "
            f"{blocked!r}, so the queue will not drain. Not waiting on a "
            f"known negative."
        )
    return resp


def _queue_states(client, site_id: str, targets):
    """Per-URL queue rows for `targets`, or None when the queue is unreadable.

    None and {} are DIFFERENT answers and the caller depends on the
    difference: {} means the queue was read and holds none of these URLs
    (they are missing -- unknown), None means the question could not be asked
    at all. Collapsing either into "nothing pending" would report a settled
    queue over a queue nobody looked at.
    """
    body = client.get(f"/api/sites/{site_id}/queue?limit={_QUEUE_PAGE}")
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        return None
    wanted = set(targets)
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url", ""))
        if url in wanted:
            found[url] = {
                "status": str(row.get("status", "")),
                "message": str(row.get("message", ""))[:200],
                "filename": str(row.get("filename", "")),
            }
    return found


def wait_for_settle(client, site_id: str, urls, *,
                    timeout: float = DEFAULT_SETTLE_TIMEOUT,
                    interval: float = DEFAULT_SETTLE_INTERVAL) -> dict:
    """Poll until every seeded URL is terminal, or the budget runs out.

    Bounded and never silent. The return value states, per URL, the status the
    queue reported -- including "unknown" for a URL the queue does not hold and
    for a queue that could not be read at all. A timeout is not swallowed: the
    caller reports it and exits non-zero.

    DUPLICATES COLLAPSE, and waiting for them would never end. The seed set
    repeats index 0 at index 2 on purpose (L14 needs a repeat), but
    runner_queue.load_urls counts a URL already in self.jobs as a `dupe`
    without creating a second job, and the queue table is keyed by
    (site_id, url). Three seeded URLs are two queue rows. So the subject here
    is the DISTINCT set.
    """
    targets = list(dict.fromkeys(str(u) for u in urls))
    budget = max(0.0, float(timeout))
    started = time.monotonic()
    deadline = started + budget
    polls = 0
    states = {}
    readable = False
    while True:
        polls += 1
        observed = _queue_states(client, site_id, targets)
        readable = observed is not None
        states = observed or {}
        unresolved = [
            url for url in targets
            if states.get(url, {}).get("status") not in TERMINAL_QUEUE_STATUSES
        ]
        if not unresolved:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    per_url = {}
    for url in targets:
        if url in states:
            per_url[url] = states[url]
        else:
            per_url[url] = {
                "status": "unknown",
                "note": ("not present in the site's queue" if readable
                         else "the site's queue could not be read"),
            }
    return {
        "site_id": site_id,
        "urls": targets,
        "settled": not unresolved,
        "unresolved": unresolved,
        "per_url": per_url,
        "queue_readable": readable,
        "polls": polls,
        "timeout_seconds": budget,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


SEED_DEDUP_SITE_NAME = f"{SEED_MARKER} fixture dedup"


def dedup_site_config() -> dict:
    """Config for the SECOND site, the one the duplicate is queued on.

    Same two opt-outs as queue_site_config, for the same reasons, and one of
    them matters more here: the file is guaranteed to exist already, because the
    queue fixture just downloaded it. skip_if_exists
    (runner_transport.py:794, default True) keys on FILESYSTEM state and is a
    different mechanism from dedup, which keys on DATABASE state. The preflight
    runs first so dedup should win regardless -- but with the flag left on, a
    dedup failure would surface as a silent file-existence skip that looks
    exactly like success. Explicit False means a dedup failure shows up as a
    real download instead.

    No download_dir, for the same reason as queue_site_config: this is an HTTP
    client and --base-url may point at a service with a different BD_HOME, so
    any path computed here is a guess about somebody else's filesystem.
    """
    return {
        "name": SEED_DEDUP_SITE_NAME,
        "auto_teach_first_run": False,
        "skip_if_exists": False,
    }


def ensure_dedup_site(client, *, dry_run: bool = False):
    """A second site the seeder owns, for the duplicate to be queued on.

    Carries the marker, so teardown removes it (teardown -> _marked_site_ids ->
    DELETE /api/sites/<sid>), and has its own exact name so it and the queue
    fixture cannot delete each other between runs.
    """
    return _ensure_owned_site(client, SEED_DEDUP_SITE_NAME,
                              dedup_site_config(), dry_run=dry_run)


def requeue_for_dedup(client, source_site_id, url, *,
                      timeout: float = DEFAULT_SETTLE_TIMEOUT,
                      dry_run: bool = False) -> dict:
    """Queue an already-completed URL again, so L14 has a real subject.

    L14 (stash-dedup-skip) asks whether BD skipped a duplicate, and reads
    exactly one thing: `queue.status = 'skipped_duplicate'`, which the dedup
    path is the only writer of. Three things stopped this function ever
    producing that row, and the third is why fixing the first two alone would
    have changed nothing.

    1. THE ROUTE ANSWERED 405. It POSTed to `/api/queue/v2`, which
       app_queue.py:170 declares with no `methods=` -- so Flask registers GET
       only. The enqueue route is `/api/queue/v2/add_url` (app_queue.py:461).

    2. THE PAYLOAD WAS THE WRONG SHAPE. add_url reads `url`, a string; this sent
       `urls`, a list. Even against the right path that is a 400.

    3. AND THE SAME SITE WOULD HAVE DROPPED IT AT INTAKE. runner_queue.py:257
       discards a URL already present in that runner's `self.jobs`, counting it
       in `dupes` -- and a COMPLETED job stays in `self.jobs`. So re-queueing on
       the site that just finished the download is collapsed before the dedup
       preflight runs. The old docstring knew this about the batch's internal
       repeat; it did not notice the same code drops the after-the-fact
       re-queue too.

    The intake drop is AVOIDED rather than defeated, and the code says how. Both
    halves of the mechanism are global, not per-site:

        runner_integrity.py:148  _dedup_preflight -> db_find_url_in_history(url)
                                 (no site scope; dedup_exact_url defaults True)
        L14                      SELECT COUNT(*) FROM queue
                                 WHERE status = 'skipped_duplicate'   (no scope)

    So the duplicate goes on a SECOND seeded site. That site's runner has its own
    `self.jobs` and has never seen the URL, so intake accepts it; the preflight
    then finds the first site's `done` row in history and writes
    skipped_duplicate. No eviction route, no runner change -- the mechanism
    already worked, it was being asked on the one site where intake swallowed
    the question.

    AND THE ANSWER IS NOW READ. add_url returns
    `{ok, site_id, url, added, dupes, skipped}`, so `dupes: 1` is defect 3
    happening, reported by the app itself. Previously the response was assigned
    to `plan["queued"]` and never inspected, so a 405, a 400 and an unreachable
    app all ended as `dedup_observed: false` -- which reads as BD declining to
    dedup rather than as a request that was never accepted. Each is now a
    refusal that names itself.
    """
    plan = {"action": "requeue_for_dedup", "marker": SEED_MARKER,
            "source_site_id": source_site_id, "url": url}
    if dry_run:
        plan["dry_run"] = True
        return plan

    sid = ensure_dedup_site(client)
    plan["site_id"] = sid
    resp = client.post("/api/queue/v2/add_url", {"site_id": sid, "url": url})
    plan["queued"] = resp

    if not isinstance(resp, dict) or not resp.get("ok"):
        raise SeedRefused(
            f"could not queue the duplicate on the {SEED_DEDUP_SITE_NAME!r} "
            f"site: {str(resp)[:200]}. Nothing was enqueued, so no dedup "
            f"decision can follow -- this is a broken seeder, not a BD that "
            f"declined to dedup, and the two must not report the same way."
        )
    added = resp.get("added")
    if not added:
        raise SeedRefused(
            f"the duplicate was not accepted at intake: added={added!r}, "
            f"dupes={resp.get('dupes')!r}, skipped={resp.get('skipped')!r}. A "
            f"dupes count here means runner_queue.py:257 dropped the URL "
            f"because it is already in that runner's self.jobs -- so it never "
            f"reached the dedup preflight, and L14 would have no subject. "
            f"(response: {str(resp)[:200]})"
        )

    plan["started"] = start_seeded_site(client, sid)
    settle = wait_for_settle(client, sid, [url], timeout=timeout)
    plan["settle"] = settle
    entry = (settle.get("per_url") or {}).get(url) or {}
    plan["dedup_observed"] = str(entry.get("status", "")) == "skipped_duplicate"
    return plan


def start_and_settle(client, site_id, urls, *,
                     timeout: float = DEFAULT_SETTLE_TIMEOUT,
                     interval: float = DEFAULT_SETTLE_INTERVAL,
                     dry_run: bool = False) -> dict:
    """Start the seeded site, then wait for its jobs to reach end states."""
    targets = list(dict.fromkeys(str(u) for u in (urls or [])))
    plan = {
        "action": "start_and_settle",
        "marker": SEED_MARKER,
        "site_id": site_id,
        "urls": targets,
        # Reported rather than hidden: the operator asked for 3 URLs and the
        # settle report covers 2, and the reason is BD's own intake dedupe.
        "duplicates_collapsed": len(list(urls or [])) - len(targets),
        "timeout_seconds": float(timeout),
    }
    if dry_run:
        plan["dry_run"] = True
        return plan
    plan["start"] = start_seeded_site(client, site_id)
    plan["settle"] = wait_for_settle(client, site_id, targets,
                                     timeout=timeout, interval=interval)
    return plan


SEED_LOGIN_SITE_NAME = f"{SEED_MARKER} fixture login"

# The fixture site's own documented test credentials. These are NOT secrets:
# tools/fixture_site.py publishes them in its module docstring ("CREDENTIALS
# (all login prefixes) username: tester password: fixturepass") and validates
# against them in-process. They authenticate against 127.0.0.1 only. No real
# credential is ever read, written or transmitted by this tool.
FIXTURE_USERNAME = "tester"
FIXTURE_PASSWORD = "fixturepass"


def login_site_config() -> dict:
    """Config for a site whose login target is the LOCAL fixture.

    The selectors mirror tools/fixture_site.py's /formauth/login markup. On
    success the fixture sets a `fixture_session` cookie and redirects to
    /formauth/members, so a real login produces a real cookie for BD to
    persist.
    """
    return {
        "name": SEED_LOGIN_SITE_NAME,
        "login_url": f"{FIXTURE_ORIGIN}/formauth/login",
        "username": FIXTURE_USERNAME,
        "password": FIXTURE_PASSWORD,
        "user_field": "#login-username",
        "pass_field": "#login-password",
        "submit_btn": "button[type=submit]",
        # runner_auth.login_async returns early into start_manual_login when
        # this flag is on AND config["learned"]["login"] holds none of the
        # three selector keys -- and the selectors above are TOP-level, not
        # learned. Every seeded login therefore parked in manual takeover:
        # BD navigated to the form, autofilled it, and waited for a human
        # click that a capture host will never supply. Observed on test4
        # 2026-07-28 as "GET /formauth/login 200" with logins_ok=0 AND
        # logins_failed=0 on the fixture's own counters -- loaded, never
        # submitted. The seeder already knows the selectors, so it has
        # nothing to learn; opt out and let do_login run its own chain.
        "auto_teach_first_run": False,
        "success_url": f"{FIXTURE_ORIGIN}/formauth/members",
        # BD writes this file during login. The seeder never touches it -- if
        # it did, L8 and L9 would be checking the fixture's output rather than
        # BD's credential persistence, which is the vacuous PASS this design
        # exists to prevent.
        #
        # Left EMPTY on purpose, and it must stay that way. BD requires an
        # absolute path (app.py:3954) and derives one itself when the field is
        # blank (app.py:3941 returns early on empty; _save_sites_config at
        # app.py:1197-1218 fills in BD_HOME/cookies/<site_id>.json). This
        # seeder is an HTTP client -- --base-url may point at a service on
        # another machine, with a different BD_HOME and cwd -- so any absolute
        # path computed HERE is a guess about somebody else's filesystem.
        # A relative path shipped here for months and the create call 400'd on
        # every run; the suite stayed green because it drove a FakeClient that
        # validated nothing.
        "cookie_file": "",
    }


def seed_login(client, *, poll_seconds: float = 30.0, dry_run: bool = False) -> dict:
    """Create a fixture-login site and trigger BD's REAL login against it.

    What is synthetic: the site config and the decision to log in now. What is
    NOT synthetic, and is exactly what L6/L8/L9 assert on: BD driving a browser
    to the fixture's form, submitting it, receiving the cookie, persisting the
    jar and recording auth health. The seeder supplies the trigger and nothing
    downstream of it.
    """
    cfg = login_site_config()
    plan = {"action": "seed_login", "marker": SEED_MARKER,
            "login_url": cfg["login_url"]}
    if dry_run:
        plan["dry_run"] = True
        return plan

    created = client.post("/api/sites", cfg)
    if not isinstance(created, dict) or not created.get("id"):
        raise SeedRefused(
            f"could not create the fixture login site "
            f"(response: {str(created)[:200]})"
        )
    sid = created["id"]
    plan["site_id"] = sid

    # POST /api/sites routes the password to the encrypted secrets vault. On a
    # plaintext or locked backend the SITE is created but NO password is stored
    # -- so the login would fail for a reason that says nothing about BD's
    # login path, and a half-configured marked site would be left behind.
    # Refuse loudly and name the precondition instead.
    if not created.get("cred_stored"):
        reason = "the secrets backend did not accept the password"
        if created.get("secrets_locked"):
            reason = "the secrets vault is LOCKED (unlock it, then re-run)"
        elif created.get("secrets_plaintext"):
            reason = ("the secrets backend is PLAINTEXT; BD refuses to store a "
                      "credential there (switch to an encrypted backend)")
        client.delete(f"/api/sites/{sid}")  # do not strand a useless site
        raise SeedRefused(
            f"cannot seed a login: {reason}. The site was created without a "
            f"password, so a login could not succeed and L6/L8/L9 would fail "
            f"for an unrelated reason. Removed the site again."
        )

    plan["login_triggered"] = client.post(f"/api/sites/{sid}/login", {})

    # login_async() returns immediately; poll for the outcome so the caller
    # learns whether a real session was actually established rather than just
    # that a request was accepted.
    deadline = time.time() + poll_seconds
    plan["auth_state"] = "unknown"
    while time.time() < deadline:
        # /api/sites/v2, NOT /api/status. auth_state is built by the v2
        # listing at app_sites_id_core.py:322; /api/status serializes
        # runner.get_status(), which emits login_status and 36 other keys but
        # never auth_state. Polling /api/status read "" on every iteration, so
        # this loop never broke early, always burned its whole window, and
        # reported "unknown" identically on success and on failure -- a report
        # that cannot distinguish its two outcomes. v2 is also the exact field
        # L8 gates on, so the seeder and the check now read one source.
        listing = client.get("/api/sites/v2")
        state = ""
        if isinstance(listing, dict):
            for row in listing.get("sites") or []:
                if isinstance(row, dict) and str(row.get("site_id")) == sid:
                    state = str(row.get("auth_state", ""))
                    break
        if state:
            plan["auth_state"] = state
        if state == "ok":
            break
        time.sleep(1.0)

    # L6 does NOT read auth_state. It reads GET /api/auth_health/status, which
    # serves the `auth_health` TABLE -- a different surface from the v2 listing
    # L8 gates on, populated by cookie_health.check_site's live HTTP probe.
    #
    # Two things write that table, and in a capture neither one covers this
    # site. bg_scheduler registers `cookie_health.nightly_check` with
    # last_run=0.0, so it is due on the coordinator's FIRST poll -- service
    # start, capture.sh step [4] -- and then not again for 86400s; this seeder
    # runs at step [5a], after that sweep, so the site it creates was never in
    # the sweep's denominator. And no module on the login path references
    # cookie_health at all (runner_auth.py, login_impl/*, session_keeper.py:
    # zero hits). The seeded site therefore had no row of any kind, L6 saw only
    # the operator's own sites, and it reported "auto-login may be broken"
    # about a login that had just succeeded -- a verdict about a denominator
    # that structurally excluded its subject.
    #
    # This is a TRIGGER, in the same category as the /api/sites/<sid>/login
    # POST above, and it obeys the same rule: BD makes the request, BD
    # classifies the response, BD persists the row. The seeder supplies neither
    # the verdict nor the evidence for it -- run the identical call against a
    # jar with no live session and cookie_health records yellow ("200 OK but
    # landed on login URL"), so this cannot manufacture a green.
    #
    # Fired unconditionally, not only when auth_state=='ok': if the login did
    # not work, the recorded red/yellow names the SEEDED site in L6's output,
    # which is strictly more informative than the row being absent and L6
    # blaming the operator's unrelated sites.
    plan["auth_health"] = client.post(f"/api/auth_health/check/{sid}", {})
    return plan


RESIDUE_NOTE = (
    "a completed seeded download leaves a history row, a library row and a "
    "file under the seeded site's download_dir. The history row and its "
    "library twin CAN be removed -- batch_ops.bulk_delete deletes history by "
    "id over POST /api/batch/delete and maintains the external-content "
    "history_fts index while doing it (db.db_fts_forget), and DELETE "
    "/api/library/<lid> removes the twin, deleted FIRST because library rows "
    "carry history_id and PRAGMA foreign_keys is never enabled "
    "(library.py:538-543), so the other order leaves them dangling. But the "
    "default teardown does not remove them: --clear-history is the opt-in, "
    "and it is opt-in because capture.sh runs teardown unattended and the "
    "clear's predicate is the marker across ALL history, not this run's "
    "nonce. The older claim here -- that history is append-only with "
    "db_prune (which deletes by AGE, not by marker) as its only deleter -- "
    "was FALSE; db.py:988-992 records the retraction. The downloaded file is "
    "not removed by any path this tool has. Nothing is hidden; it is "
    "reported so a later reader does not mistake it for organic history."
)

# What can survive even a successful --clear-history. Named separately so a
# "0 remain" line can never be read as "the box is as it was found".
CLEAR_LEFTOVER_NOTE = (
    "three classes of residue can survive a clear that measured zero: "
    "(1) the downloaded files under the seeded site's download_dir -- no "
    "path this tool has removes them; (2) library rows the twin scan could "
    "not SEE -- library.library_browse swallows every exception into "
    "([], None) (library.py:363-364), so over this API an unreadable or "
    "missing library table produces the identical body to a library with no "
    "twins; (3) any history row whose FTS-indexed text was updated in place "
    "-- /api/batch/delete discards db_fts_forget's report, so the index "
    "outcome is unreported (check fts5vocab on the box to see it). SQLite "
    "foreign keys are off (library.py:538-543), so an UNSEEN twin's "
    "history_id dangles rather than being SET NULL -- which is exactly why "
    "twins are deleted BEFORE their history rows whenever the scan can see "
    "them."
)


def _seeded_history_rows(client):
    """(marked history rows, readable). ([], False) when it cannot be read.

    Reads through the app, like every other question this tool asks.
    /api/history returns a BARE ARRAY by default and a {rows, next_cursor}
    envelope when paginating, so both shapes are accepted; anything else is
    UNKNOWN and says so rather than counting as zero.

    THE URL IS PART OF THE CONTRACT. tests/test_live_seed_starts_and_settles
    .py stubs it verbatim, and its FakeClient falls back to a default body on
    an exact-key miss rather than raising -- so changing the query string
    here does not fail those tests, it makes one of them pass without
    exercising its fixture. Unbounded residue is handled by the clear's ROUND
    LOOP (clear_seeded_history), not by paginating this read.

    The server-side filter is a LIKE over url/filename/message (db.db_search),
    so the marker is re-checked against the url field here: the subject is a
    seeded URL, not any row whose message happens to mention one.
    """
    body = client.get(f"/api/history?q={SEED_MARKER}&limit={_HISTORY_PAGE}")
    rows = None
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict) and isinstance(body.get("rows"), list):
        rows = body["rows"]
    if rows is None:
        return [], False
    return [r for r in rows
            if isinstance(r, dict)
            and SEED_MARKER in str(r.get("url", ""))], True


def _seeded_history(client):
    """(count of marked history rows, readable). (None, False) when unreadable.

    A projection of _seeded_history_rows, kept because teardown's residue
    report and every existing caller want the number, not the rows.
    """
    rows, readable = _seeded_history_rows(client)
    return (len(rows) if readable else None), readable


def _row_ids(rows):
    """Integer ids from history rows, skipping anything that is not one."""
    out = []
    for r in rows:
        try:
            out.append(int(r["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _twin_scan(client, doomed_ids):
    """Page GET /api/library/browse and find the doomed rows' library twins.

    Derivation is by history_id MEMBERSHIP, never by text: browse's q=
    searches title/file_path/notes ONLY (library.py:326-330), so no marker
    query can find a twin. Rows are `SELECT l.*` so history_id is present
    (library.py:299).

    THE SECTION-0 TRAP, handled explicitly. library.library_browse wraps its
    whole query in `except Exception: return [], None` (library.py:363-364),
    so over this API an unreadable or missing library table produces the
    IDENTICAL body to a library holding no twins. A scan that saw zero rows
    therefore reports itself INCONCLUSIVE rather than claiming "no twins".
    The three states are DISTINCT in the returned report:
      * rows_scanned > 0 and scan_complete: conclusive -- it can say how
        many matched, including none;
      * rows_scanned == 0: it scanned NOTHING it can prove, and says so;
      * truncated or aborted: partial, and says so.
    """
    report = {"pages": 0, "rows_scanned": 0, "scan_complete": False,
              "conclusive": False, "matched_ids": [], "warnings": []}
    cursor = None
    while report["pages"] < _TWIN_MAX_PAGES:
        path = f"/api/library/browse?limit={_TWIN_PAGE}"
        if cursor is not None:
            path = f"{path}&after_id={cursor}"
        body = client.get(path)
        if not (isinstance(body, dict) and body.get("ok") is True
                and isinstance(body.get("rows"), list)):
            report["warnings"].append(
                f"library browse returned an unexpected body; twin scan "
                f"aborted after {report['pages']} page(s) / "
                f"{report['rows_scanned']} row(s): {str(body)[:200]}")
            return report
        report["pages"] += 1
        report["rows_scanned"] += len(body["rows"])
        for row in body["rows"]:
            if not isinstance(row, dict) or row.get("history_id") is None:
                continue
            try:
                hid = int(row["history_id"])
                lid = int(row["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if hid in doomed_ids:
                report["matched_ids"].append(lid)
        cursor = body.get("next_cursor")
        if cursor is None:
            report["scan_complete"] = True
            break
    if not report["scan_complete"]:
        report["warnings"].append(
            f"twin scan truncated at {_TWIN_MAX_PAGES} pages with the cursor "
            f"still set; library rows beyond that were never examined")
    if report["rows_scanned"] == 0:
        report["conclusive"] = False
        report["warnings"].append(
            f"library browse returned zero rows for {len(doomed_ids)} doomed "
            f"history id(s); library.library_browse swallows every exception "
            f"into ([], None) (library.py:363-364), so over this API an "
            f"unreadable or missing library table is indistinguishable from "
            f"a library with no twins -- 'no twins' is UNVERIFIED, not "
            f"measured")
    else:
        report["conclusive"] = bool(report["scan_complete"])
    return report


def clear_seeded_history(client, *, dry_run: bool = False,
                         chunk: int = _CLEAR_CHUNK,
                         max_rounds: int = _CLEAR_MAX_ROUNDS) -> dict:
    """Delete the marked history rows and their library twins, twins FIRST.

    WHY HTTP AND NOT A DIRECT sqlite DELETE. history_fts is an FTS5
    external-content table (content='history', db.py:113-118): SQLite
    maintains nothing for it, so a hand-rolled DELETE would leave every
    removed row's terms in the inverted index forever. batch_ops.bulk_delete
    calls db.db_fts_forget for the batch (batch_ops.py:200) and that is the
    whole reason this goes through the app.

    WHY TWINS FIRST. library rows carry history_id and PRAGMA foreign_keys
    is never enabled (library.py:538-543), so `history_id -> history(id) ON
    DELETE SET NULL` does NOT fire. Deleting the history rows first would
    leave every twin pointing at an id that no longer exists.

    OWNERSHIP. Two predicates, both required, and the narrower one is the
    one that authorises a delete: the marker must be in the row's URL (what
    _seeded_history_rows already checks) AND in its site_name (which db_log
    stores as a literal -- db.py:962/1036 -- so it survives the site being
    deleted). A row matching only the first is counted as `unowned` and
    NEVER deleted: under-deleting is the safe direction, and an unowned row
    simply keeps the remainder non-zero, which ok=False then reports.

    NO ARITHMETIC. The remainder is RE-MEASURED by re-reading /api/history,
    never computed as found-minus-deleted: db_log runs on every job-level
    transition (db.py:962-965), so a job settling during teardown appends a
    row after the first read, and /api/batch/delete answers a rejected
    filter and a genuine no-op with the SAME body (candidates_matched 0,
    processed 0, ok true -- measured), because _matching_rows swallows the
    TypeError from an unexpected filter key and returns [].

    THE CANARY. Before deleting anything, one chunk is re-sent with
    dry_run=True. Those ids were just read as present, so candidates_matched
    MUST equal len(chunk); a 0 means the filter shape is not the one
    _build_query accepts and the pass aborts having deleted NOTHING --
    neither twins nor history. The canary runs BEFORE the twin deletes
    deliberately: if the batch filter is broken, deleting twins first would
    strand history rows whose twins are already gone.
    """
    out = {"action": "clear_seeded_history", "marker": SEED_MARKER,
           "dry_run": bool(dry_run), "readable": False,
           "found": None, "unowned": 0, "targeted": 0, "deleted": 0,
           "rounds": 0, "remaining": None, "remaining_readable": False,
           "stalled": False, "canary": None,
           "errors": [], "warnings": [], "ok": False,
           "twins": {"pages": 0, "rows_scanned": 0, "scan_complete": False,
                     "matched": 0, "deleted": 0, "already_gone": 0,
                     "errors": [], "conclusive": False, "remaining": None},
           "note": CLEAR_LEFTOVER_NOTE}
    twins = out["twins"]
    chunk = max(1, min(int(chunk), 1000))
    prev_remaining = None
    exhausted = True
    targeted_ids = set()
    for round_no in range(1, max(1, int(max_rounds)) + 1):
        rows, readable = _seeded_history_rows(client)
        if not readable:
            out["errors"].append(
                f"round {round_no}: could not read /api/history, so the "
                f"{SEED_MARKER} row set is UNKNOWN")
            out["remaining"] = None
            out["remaining_readable"] = False
            return out
        out["readable"] = True
        out["rounds"] = round_no
        if out["found"] is None:
            out["found"] = len(rows)
        owned = [r for r in rows
                 if SEED_MARKER in str(r.get("site_name", ""))]
        out["unowned"] = len(rows) - len(owned)
        ids = _row_ids(owned)
        if not ids:
            out["remaining"] = len(rows)
            out["remaining_readable"] = True
            exhausted = False
            break
        if out["canary"] is None:
            probe = ids[:chunk]
            resp = client.post("/api/batch/delete",
                               {"filter": {"id_in": probe,
                                           "limit": len(probe)},
                                "dry_run": True, "delete_files": False})
            matched = resp.get("candidates_matched") \
                if isinstance(resp, dict) else None
            out["canary"] = {"sent": len(probe), "matched": matched}
            if matched != len(probe):
                out["errors"].append(
                    f"filter canary: /api/batch/delete matched {matched} of "
                    f"{len(probe)} ids that were just read as present. The "
                    f"filter shape is not the one batch_ops._build_query "
                    f"accepts; nothing was deleted -- neither twins nor "
                    f"history. Response: {str(resp)[:200]}")
                out["remaining"] = len(rows)
                out["remaining_readable"] = True
                return out
        # TWIN SCAN AND DELETE -- BEFORE the history deletes (register 15.23
        # decision 1). The scan counters record THIS round's scan; matched,
        # deleted, already_gone and errors accumulate across rounds; the
        # final verification pass below writes `remaining` and NOTHING else.
        targeted_ids.update(ids)
        scan = _twin_scan(client, set(ids))
        twins["pages"] = scan["pages"]
        twins["rows_scanned"] = scan["rows_scanned"]
        twins["scan_complete"] = scan["scan_complete"]
        twins["conclusive"] = scan["conclusive"]
        twins["matched"] += len(scan["matched_ids"])
        out["warnings"].extend(scan["warnings"])
        if dry_run:
            out["targeted"] = len(ids)
            out["remaining"] = len(rows)
            out["remaining_readable"] = True
            out["ok"] = True
            return out
        for lid in scan["matched_ids"]:
            # BODYLESS DELETE is correct: app_library.py:141-142 does
            # get_json(silent=True) or {} and delete_file defaults False, so
            # no file is touched. library_delete also NULLs the history
            # row's library_id (library.py:478-481) -- harmless, since that
            # history row dies next.
            resp = client.delete(f"/api/library/{lid}")
            if isinstance(resp, dict) and resp.get("ok") is True \
                    and resp.get("deleted_row"):
                twins["deleted"] += 1
            elif isinstance(resp, dict) and resp.get("ok") is False \
                    and "not found" in str(resp.get("error", "")).lower():
                # Another writer got it first: a warning, not an error.
                twins["already_gone"] += 1
                out["warnings"].append(
                    f"library {lid}: already gone before this tool's DELETE "
                    f"({str(resp)[:200]})")
            else:
                twins["errors"].append(
                    f"library {lid}: DELETE /api/library/{lid} answered "
                    f"{str(resp)[:200]}")
        for i in range(0, len(ids), chunk):
            batch = ids[i:i + chunk]
            out["targeted"] += len(batch)
            resp = client.post("/api/batch/delete",
                               {"filter": {"id_in": batch,
                                           "limit": len(batch)},
                                "dry_run": False, "delete_files": False})
            if not isinstance(resp, dict) or resp.get("ok") is not True:
                out["errors"].append(
                    f"/api/batch/delete rejected a {len(batch)}-id chunk: "
                    f"{str(resp)[:200]}")
                continue
            processed = resp.get("processed")
            matched = resp.get("candidates_matched")
            if not isinstance(processed, int):
                out["errors"].append(
                    f"/api/batch/delete returned no processed count: "
                    f"{str(resp)[:200]}")
                continue
            out["deleted"] += processed
            if matched == 0 and processed == 0:
                # SOFT, not hard. Another writer removing these ids first
                # gives exactly this body, and so does a malformed filter --
                # the canary above already ruled the second one out, and the
                # re-measured remainder settles the rest.
                out["warnings"].append(
                    f"a {len(batch)}-id chunk matched nothing "
                    f"(already gone?)")
            elif processed != matched:
                out["errors"].append(
                    f"/api/batch/delete processed {processed} of {matched} "
                    f"matched (errors={resp.get('errors')})")
        rows_after, readable_after = _seeded_history_rows(client)
        out["remaining"] = len(rows_after) if readable_after else None
        out["remaining_readable"] = bool(readable_after)
        if not readable_after:
            out["errors"].append(
                "could not re-read /api/history after the clear, so the "
                "remainder is UNKNOWN rather than zero")
            return out
        if not rows_after:
            exhausted = False
            break
        if prev_remaining is not None and len(rows_after) >= prev_remaining:
            out["stalled"] = True
            exhausted = False
            break
        prev_remaining = len(rows_after)
    if exhausted:
        out["stalled"] = True
    if not dry_run and targeted_ids:
        # FINAL TWIN VERIFICATION -- against the union of every id targeted
        # across rounds. Writes twins["remaining"] (plus its own warning or
        # error) and NOTHING ELSE: pages/rows_scanned/matched above are the
        # round scan's findings, and clobbering them would make the report
        # describe a different pass than the one that deleted.
        verify = _twin_scan(client, set(targeted_ids))
        if verify["conclusive"]:
            leftover = len(verify["matched_ids"])
            twins["remaining"] = leftover
            if leftover:
                twins["errors"].append(
                    f"{leftover} library twin(s) of the targeted history "
                    f"id(s) remain after the clear")
        else:
            twins["remaining"] = None
            out["warnings"].extend(verify["warnings"])
            out["warnings"].append(
                "final twin verification was inconclusive, so whether any "
                "library twin remains is UNKNOWN rather than zero")
    out["ok"] = bool(out["remaining_readable"]
                     and out["remaining"] == 0
                     and not out["errors"]
                     and not twins["errors"])
    return out
SEED_TUNNEL_NAME = f"{SEED_MARKER} synthetic tunnel"


def vpn_tunnel_config() -> dict:
    """The synthetic tunnel L30 gets to cross-check.

    INERT BY CONSTRUCTION. It is registered and never started, and nothing
    routes to it:

      * vpn.Tunnel.state defaults to "down". vpn._run_health_pass() targets
        only tunnels in ("up", "failing") and vpn_leak_tests._monitor_loop()
        only "up", so neither background thread ever touches this one.
      * No SOCKS port is allocated until vpn.start_tunnel(), which the seeder
        never calls.
      * vpn_runtime.get_socks_url_for_site() reaches a tunnel only through
        _site_to_tunnel (built from each site's `vpn` field in
        sites_config.json) or _global_tunnel_id (from `global_vpn`). NEITHER
        is derived from tunnels.json, so a tunnel that no site names cannot
        carry, block or divert any download's traffic.
      * The system (iptables) kill switch is armed only by an explicit call to
        /api/vpn/system_killswitch/<id>/apply. Registering a tunnel does not
        arm it, and the seeder never calls it.

    `enabled` must stay True: vpn_config.register_loaded_tunnels() skips
    disabled tunnels, so a disabled one would be present in the stored config
    but absent from the live registry -- which is precisely the divergence L30
    FAILs on, and the seeder would be manufacturing it.

    `config` is deliberately empty. There is no key, endpoint or credential to
    start with, so even an accidental start_tunnel() fails immediately instead
    of establishing a real link.
    """
    return {
        "name": SEED_TUNNEL_NAME,
        "provider": "generic",
        "backend": "wireguard",
        "location": "",
        "enabled": True,
        "config": {},
        "extra": {},
    }


def _vpn_status(client):
    """Read /api/vpn/status, or raise SeedRefused if the answer is unusable."""
    body = client.get("/api/vpn/status")
    if not isinstance(body, dict) or not body.get("ok"):
        raise SeedRefused(
            "cannot read /api/vpn/status, so this host's VPN state is UNKNOWN "
            f"and tunnel seeding is refused (response: {str(body)[:200]})."
        )
    return body


def _marked_tunnel_ids(client) -> list:
    """Tunnel ids whose NAME carries the marker.

    The name is the discriminator, exactly as it is for sites: BD generates
    tunnel_ids, so the seeder cannot recognise its own by id alone. Anything
    unmarked is the operator's and is never touched.
    """
    body = client.get("/api/vpn/status")
    if not isinstance(body, dict):
        return []
    found = []
    for t in body.get("tunnels") or []:
        if not isinstance(t, dict):
            continue
        if SEED_MARKER in str(t.get("name", "")):
            tid = t.get("tunnel_id") or t.get("id")
            if tid:
                found.append(str(tid))
    return sorted(found)


def seed_vpn_tunnel(client, *, dry_run: bool = False) -> dict:
    """Create one marked, inert VPN tunnel so L30 has a subject.

    This is INPUT, not output. L30 asks whether the stored config and the live
    registry agree; the seeder supplies a tunnel and BD still has to persist
    it, register it, and render both sides consistently. If BD's persist path
    and its register path diverged, the seeded tunnel would show
    registered_live=False and L30 would FAIL -- the check still decides.

    REFUSES when the stored VPN config has quarantined records. That guard is
    not defensive padding, it is the reason this is safe at all:
    vpn_config.save() serialises the in-memory tunnel list, and before the
    quarantine fix a failed load left that list EMPTY -- so creating one
    tunnel rewrote tunnels.json with ONLY the synthetic one and silently
    destroyed every tunnel the operator had. Reproduced against the real
    modules: two operator tunnels in, one bdseed tunnel out.
    """
    status = _vpn_status(client)
    errors = status.get("config_load_errors")
    if not isinstance(errors, list):
        raise SeedRefused(
            "/api/vpn/status does not report config_load_errors, so the "
            "seeder cannot tell whether this host's tunnels.json loaded "
            "cleanly or failed to parse -- both render as an empty tunnel "
            "list. Writing a tunnel blind could overwrite the operator's VPN "
            "config. Unknown is a third state and it fails; refusing. "
            "(Deploy a build that reports the field, then re-run.)"
        )
    if errors:
        named = ", ".join(
            str(e.get("tunnel_id") or f"#{e.get('index')}")
            for e in errors if isinstance(e, dict)
        )
        raise SeedRefused(
            f"the stored VPN config has {len(errors)} quarantined record(s) "
            f"({named}). Refusing to add a tunnel on top of a config that did "
            f"not fully load. Fix those records first -- "
            f"GET /api/settings/store-raw?store=vpn shows the file, and "
            f"/api/vpn/status lists the exact errors."
        )

    plan = {"action": "seed_vpn_tunnel", "marker": SEED_MARKER,
            "name": SEED_TUNNEL_NAME}
    existing = _marked_tunnel_ids(client)
    if existing:
        # Re-running a capture must not accumulate tunnels, same rule as sites.
        plan["reused"] = existing[0]
        return plan
    if dry_run:
        plan["dry_run"] = True
        return plan
    created = client.post("/api/vpn/tunnels", vpn_tunnel_config())
    if not isinstance(created, dict) or not created.get("tunnel_id"):
        raise SeedRefused(
            f"could not create the {SEED_MARKER} tunnel "
            f"(response: {str(created)[:200]})"
        )
    plan["tunnel_id"] = created["tunnel_id"]
    return plan


def teardown(client, *, dry_run: bool = False,
             clear_history: bool = False) -> dict:
    """Cancel exactly the queue entries this tool created.

    The predicate is the marker, never position or recency, so a real entry
    queued alongside a seeded one is never touched.

    Deleting the marked site also clears its queue rows (api_delete calls
    queue_delete_site), so the QUEUE side is clean whatever state the jobs
    reached -- including 'done'. History is not, and `residue` says so: see
    RESIDUE_NOTE. Reporting "removed the sites, cancelled the queue" and
    stopping would imply the box is as it was found.
    """
    body = _queue_snapshot(client)
    victims = [
        entry
        for key in ("running", "waiting")
        for entry in (body.get(key) or [])
        if isinstance(entry, dict) and _is_seeded(entry)
    ]
    ids = [entry.get("id") for entry in victims if entry.get("id") is not None]
    sites = _marked_site_ids(client)
    history_rows, history_readable = _seeded_history(client)
    tunnels = _marked_tunnel_ids(client)
    # `history_rows` ALWAYS means rows that REMAIN; `history_rows_found`
    # always means rows the read SAW. Without a clear they are the same
    # number, which is exactly why one name was enough before and is not
    # enough now: a clear makes them differ, and a single key would silently
    # change meaning between two runs of the same tool. `readable` describes
    # whichever measurement produced `history_rows`.
    plan = {"action": "teardown", "marker": SEED_MARKER,
            "ids": ids, "sites": sites, "tunnels": tunnels,
            "residue": {"history_rows_found": history_rows,
                        "history_rows": history_rows,
                        "readable": history_readable,
                        "cleared": False,
                        "note": RESIDUE_NOTE}}
    if dry_run:
        plan["dry_run"] = True
        if clear_history:
            plan["clear"] = clear_seeded_history(client, dry_run=True)
        return plan
    if ids:
        plan["cancelled"] = client.post("/api/queue/v2/bulk_cancel", {"ids": ids})
    # Delete only sites we created. An unmarked site is the operator's and is
    # never touched -- the predicate is the marker, never recency or position.
    removed = []
    for sid in sites:
        removed.append({"site_id": sid, "result": client.delete(f"/api/sites/{sid}")})
    plan["removed_sites"] = removed
    # Same predicate for tunnels. Deleting an unmarked tunnel would remove the
    # operator's egress protection, which is the worst thing this tool could
    # do, so the marker is checked on the NAME and nothing else is eligible.
    #
    # DELETE /api/vpn/tunnels/<id> 404s when the tunnel is not registered LIVE
    # (app_vpn_api.vpn_tunnel_delete returns early on vpn.get_tunnel() is
    # None) even though the stored config row still exists. A seeded tunnel is
    # created enabled, so register_loaded_tunnels() re-registers it on every
    # boot and it stays reachable -- but a tunnel that was disabled, or whose
    # registration failed, would be strandable in the stored config with no
    # HTTP route able to remove it. teardown reports what it removed so that
    # case is visible rather than assumed.
    removed_tunnels = []
    for tid in tunnels:
        removed_tunnels.append({"tunnel_id": tid,
                                "result": client.delete(f"/api/vpn/tunnels/{tid}")})
    plan["removed_tunnels"] = removed_tunnels
    # LAST, and after the site deletes on purpose: a job that settles while
    # teardown is running appends a history row (db_log runs on every
    # job-level transition), so the set is re-read by the clear rather than
    # reusing the one harvested at the top of this function.
    if clear_history:
        clear = clear_seeded_history(client, dry_run=False)
        plan["clear"] = clear
        residue = plan["residue"]
        residue["cleared"] = True
        residue["history_rows_found"] = clear.get("found")
        residue["history_rows"] = clear.get("remaining")
        residue["readable"] = bool(clear.get("remaining_readable"))
    return plan


def _report_residue(plan) -> None:
    """Say out loud what teardown could not remove.

    Silent only when the answer is a measured zero AND no clear was
    attempted. An unreadable history is NOT a clean one and says so; a
    non-zero count names itself rather than hiding inside JSON a reader may
    skim past.
    """
    plan = plan or {}
    residue = plan.get("residue") or {}
    clear = plan.get("clear") or {}
    twins = clear.get("twins") or {}
    rows = residue.get("history_rows")
    found = residue.get("history_rows_found")
    cleared = bool(residue.get("cleared"))
    if clear:
        for err in clear.get("errors") or []:
            print(f"live_seed: CLEAR ERROR - {err}", file=sys.stderr)
        for err in twins.get("errors") or []:
            print(f"live_seed: TWIN ERROR - {err}", file=sys.stderr)
        for warn in clear.get("warnings") or []:
            print(f"live_seed: CLEAR WARNING - {warn}", file=sys.stderr)
        if clear.get("unowned"):
            print(f"live_seed: CLEAR SKIPPED - {clear['unowned']} row(s) "
                  f"carry {SEED_MARKER} in the URL but not in site_name, so "
                  f"they are not provably this tool's and were not deleted.",
                  file=sys.stderr)
        if clear.get("stalled"):
            print(f"live_seed: CLEAR STALLED - a round removed nothing "
                  f"while {SEED_MARKER} history rows remained.",
                  file=sys.stderr)
        if twins and twins.get("conclusive") is False:
            print(f"live_seed: TWINS UNVERIFIED - the library twin scan "
                  f"could not prove anything: library.library_browse "
                  f"swallows every exception into ([], None) "
                  f"(library.py:363-364), so an unreadable or missing "
                  f"library table is indistinguishable over HTTP from a "
                  f"library with no twins.", file=sys.stderr)
    if rows is None:
        print(f"live_seed: RESIDUE UNKNOWN - could not read /api/history, so "
              f"whether this host still holds {SEED_MARKER} history rows is "
              f"undetermined. {RESIDUE_NOTE}", file=sys.stderr)
    elif rows:
        extra = ""
        if cleared:
            extra = (f" (found {found}, deleted {clear.get('deleted', 0)}, "
                     f"measured after the clear)")
        print(f"live_seed: RESIDUE - {rows} {SEED_MARKER} history row(s) "
              f"remain after teardown{extra}. {RESIDUE_NOTE}",
              file=sys.stderr)
    elif cleared:
        print(f"live_seed: CLEARED - {clear.get('deleted', 0)} of {found} "
              f"{SEED_MARKER} history row(s) removed; "
              f"{twins.get('deleted', 0)} library twin(s) removed; 0 "
              f"remain, re-measured not inferred. {CLEAR_LEFTOVER_NOTE}",
              file=sys.stderr)


# Exit codes. 2 = REFUSED and 3 = TIMEOUT are already taken by main().
#
# THE CONTRACT (asserted by tests/test_live_seed_starts_and_settles.py):
#   0  no clear requested; or clear requested and the remainder RE-MEASURED
#      at zero with no errors (twin errors included) -- also the dry-run
#      success path. NOTE a blind/inconclusive twin scan still exits 0: an
#      absent library table (migration 4 never ran) is a legitimate state
#      this API cannot distinguish from an empty one, and failing every
#      clear on it would be the over-sensitivity CLAUDE.md 0 counts as a
#      soundness bug. The TWINS UNVERIFIED warning is the loud artifact.
#   2  SeedRefused (pre-existing; an unreadable /api/queue/v2 during
#      --teardown exits here via _queue_snapshot, before any clear runs).
#   3  settle timeout (pre-existing, seed path only).
#   4  _EXIT_CLEAR_INCOMPLETE: the clear was requested and did not fully
#      happen and we KNOW it -- canary mismatch, batch or twin delete
#      errors, a non-zero measured remainder (unowned leftovers included),
#      a stall, round exhaustion, twins remaining after the verification
#      scan, or a plan carrying no clear dict at all.
#   5  _EXIT_CLEAR_UNKNOWN: the post-state could not be MEASURED (history
#      unreadable at a round read or at the post-delete re-read). Unknown
#      is a third state and it fails, distinctly from 4.
_EXIT_CLEAR_INCOMPLETE = 4
_EXIT_CLEAR_UNKNOWN = 5


def _teardown_exit_code(plan, *, clear_requested: bool) -> int:
    """0 only when the clear did what it was asked to do, MEASURED.

    Nothing read this before. main() returned 0 on every teardown outcome
    except a SeedRefused out of _queue_snapshot, so capture.sh's
    `|| echo WARNING` had never fired. Measured 2026-08-03 on four separate
    failures: an unreadable /api/history (prints RESIDUE UNKNOWN, exit 0),
    an unreadable /api/status so no marked site is found and none is deleted
    (prints NOTHING, exit 0), a site DELETE answering 500 (prints NOTHING,
    exit 0), and 64 rows left behind (prints RESIDUE, exit 0).

    SCOPED TO THE CLEAR, deliberately. teardown's queue-cancel step is
    independently broken -- the entries /api/queue/v2 returns carry no `id`
    key so `ids` is always empty, and the payload it would send is
    {"ids":...} where app_queue.py:400-406 reads "jobs" (measured: HTTP
    400). Making the whole teardown fatal in this cut would warn on every
    capture for a defect this cut did not introduce. Filed separately.
    """
    if not clear_requested:
        return 0
    clear = (plan or {}).get("clear") or {}
    if not clear:
        return _EXIT_CLEAR_INCOMPLETE
    if not clear.get("remaining_readable"):
        return _EXIT_CLEAR_UNKNOWN
    return 0 if clear.get("ok") else _EXIT_CLEAR_INCOMPLETE


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed synthetic INPUT for the live checks (marked, reversible).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--seed", action="store_true", help="enqueue marked fixture URLs")
    parser.add_argument("--login", action="store_true",
                        help="create a fixture-login site and trigger BD's real login")
    parser.add_argument("--vpn-tunnel", action="store_true",
                        help="create one marked, inert VPN tunnel so L30 has "
                             "a config/state pair to cross-check (never "
                             "started; carries no traffic)")
    parser.add_argument("--teardown", action="store_true", help="remove marked entries")
    # OPT-IN, and the reason is that capture.sh is the unattended caller. The
    # predicate is the marker across ALL history, not this run's nonce, so
    # the first non-dry-run clear removes every accumulated row from every
    # previous capture at once (64 at v3.66.844, up from 62 at 842 and 58 at
    # 840). That is the operator's state, not this run's, and it must not be
    # deleted by a cron-shaped process nobody is watching. Run
    # --teardown --clear-history --dry-run first; it reads, canaries the
    # filter, and deletes nothing.
    parser.add_argument("--clear-history", action="store_true",
                        help="after the teardown, delete the marked history "
                             "rows (and their library twins, twins first) "
                             "over the app's own HTTP API (modifies "
                             "--teardown; OFF by default). Combine with "
                             "--dry-run to preview without deleting.")
    # OPT-IN, NOT DEFAULT-ON, and the reasoning is the reason it is written
    # here rather than assumed. --seed's documented contract is "place URLs";
    # an operator hand-running it (and the tests that drive it) would suddenly
    # be starting real downloads and blocking for up to --start-timeout on a
    # verb that has never done either. capture.sh is the caller that wants the
    # queue drained, and it asks for it explicitly, in one greppable place,
    # visible in 05a_live_seed.log. A flag nobody passes would be a feature
    # nobody has, so the capture wiring ships in the same cut.
    parser.add_argument("--start", action="store_true",
                        help="start the seeded site and wait for its jobs to "
                             "reach terminal states (modifies --seed)")
    parser.add_argument("--start-timeout", type=float,
                        default=DEFAULT_SETTLE_TIMEOUT,
                        help=f"seconds to wait for the seeded queue to settle "
                             f"(default {DEFAULT_SETTLE_TIMEOUT:g}); a timeout "
                             f"is reported per URL and exits non-zero")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--site-id", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen; change nothing")
    parser.add_argument("--force", action="store_true",
                        help="seed even if the host already holds real work")
    args = parser.parse_args(argv)

    if not args.seed and not args.teardown and not args.login \
            and not args.vpn_tunnel:
        parser.error("choose --seed, --login, --vpn-tunnel or --teardown")

    client = Client(args.base_url)
    unsettled = None
    teardown_rc = 0
    try:
        if args.teardown:
            plan = teardown(client, dry_run=args.dry_run,
                            clear_history=args.clear_history)
            print(json.dumps(plan, indent=2))
            _report_residue(plan)
            teardown_rc = _teardown_exit_code(
                plan, clear_requested=args.clear_history)
        elif args.seed or args.login or args.vpn_tunnel:
            if not args.dry_run:
                preflight(client, force=args.force)
            # Emitted in a `finally` so a LATER refusal cannot discard an
            # EARLIER success. Previously both plans were collected and printed
            # only after both had run, so when seed_login raised -- a locked
            # vault, say -- the log showed the refusal and nothing else. On the
            # box that produced an 05a_live_seed.log reading as though nothing
            # had happened while three URLs had in fact been queued, which made
            # a separate fix to the seeded URLs unverifiable.
            #
            # A partial success reported as total silence is a false negative
            # about the tool's own behaviour. Whatever ran, say so.
            plans = []
            try:
                if args.seed:
                    seeded = seed_queue(client, args.count, site_id=args.site_id,
                                        dry_run=args.dry_run)
                    plans.append(seeded)
                    if args.start:
                        # Same `finally` discipline as above: appended BEFORE
                        # the wait can raise, so a refusal mid-start still
                        # prints the seed that succeeded.
                        started = start_and_settle(
                            client, seeded.get("site_id"),
                            seeded.get("urls") or [],
                            timeout=args.start_timeout,
                            dry_run=args.dry_run)
                        plans.append(started)
                        settle = started.get("settle") or {}
                        if not args.dry_run and not settle.get("settled", False):
                            unsettled = settle
                        # Give L14 a real subject. The batch's own repeat is
                        # collapsed at intake, and the per-run nonce removes the
                        # cross-run collision that used to stand in for one, so
                        # the duplicate has to be queued AFTER a copy completed.
                        # Appended inside the same `finally` discipline: a
                        # refusal here must not discard the seed that worked.
                        done_urls = [u for u, e in (settle.get("per_url") or {}).items()
                                     if str((e or {}).get("status", "")) == "done"]
                        if done_urls and not args.dry_run:
                            # NON-FATAL, deliberately, and this is a judgement
                            # not an omission.
                            #
                            # requeue_for_dedup now RAISES on a rejected enqueue
                            # instead of recording the failure and moving on --
                            # that strictness is the point of the fix. But
                            # letting it escape here makes the whole seed run
                            # exit 2, and capture.sh:804-809 then prints
                            # "seeding declined or failed" for a run where the
                            # queue seeding, the start and the settle all
                            # SUCCEEDED. The dedup pass is an ADDITIONAL subject
                            # layered on top of a seed that already worked;
                            # failing the report for it would be a false
                            # negative about the part that did work.
                            #
                            # Not swallowed: the reason lands in the printed
                            # plan and on stderr, and downstream L14 reports the
                            # consequence honestly ("no queue row marked
                            # skipped_duplicate"). What the old code did was
                            # different -- it recorded a 405 as
                            # dedup_observed=false with no reason at all, so the
                            # reader could not tell a broken seeder from a BD
                            # that declined to dedup.
                            try:
                                plans.append(requeue_for_dedup(
                                    client, seeded.get("site_id"), done_urls[0],
                                    timeout=args.start_timeout))
                            except SeedRefused as dedup_exc:
                                plans.append({
                                    "action": "requeue_for_dedup",
                                    "marker": SEED_MARKER,
                                    "url": done_urls[0],
                                    "dedup_observed": False,
                                    "error": str(dedup_exc)})
                                print(f"[{SEED_MARKER}] the dedup pass could "
                                      f"not be set up, so L14 will have no "
                                      f"subject: {dedup_exc}", file=sys.stderr)
                if args.login:
                    plans.append(seed_login(client, dry_run=args.dry_run))
                if args.vpn_tunnel:
                    plans.append(seed_vpn_tunnel(client, dry_run=args.dry_run))
            finally:
                if plans:
                    print(json.dumps(plans if len(plans) > 1 else plans[0], indent=2))
            # Rule 3: make the synthetic state impossible to mistake for real.
            # Only after a real seed -- a dry run changed nothing, and claiming
            # synthetic state is present would be its own small false report.
            if not args.dry_run:
                print(f"\n[{SEED_MARKER}] SYNTHETIC state is present on this host. "
                      f"Live-check PASSes covering the queue are exercising seeded "
                      f"input, not organic usage. Run --teardown to remove it.",
                      file=sys.stderr)
    except SeedRefused as exc:
        print(f"live_seed: REFUSED - {exc}", file=sys.stderr)
        return 2
    if unsettled is not None:
        # A timeout reported as success is an unknown laundered into an OK,
        # and capture.sh reads this exit code. Name every URL and the status
        # it was last seen in, so the log says what actually happened rather
        # than that something did not.
        lines = [
            f"live_seed: TIMEOUT - the seeded queue did not settle within "
            f"{unsettled.get('timeout_seconds')}s "
            f"({unsettled.get('polls')} poll(s), "
            f"queue_readable={unsettled.get('queue_readable')}). "
            f"L11/L12/L14 will report on whatever DID complete:"
        ]
        for url in unsettled.get("unresolved") or []:
            state = (unsettled.get("per_url") or {}).get(url) or {}
            lines.append(f"  {url} -> {state.get('status', '?')} "
                         f"{state.get('note') or state.get('message') or ''}".rstrip())
        print("\n".join(lines), file=sys.stderr)
        return 3
    if teardown_rc:
        # capture.sh's cleanup_live_seed tests this exit code. Returning 0
        # on a failed clear is an unknown laundered into an OK; see
        # _teardown_exit_code.
        return teardown_rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
