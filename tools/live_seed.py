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
# History is append-only -- db_log() is its only writer, db_prune() (by AGE, not
# by marker) its only deleter -- and there is no marker-scoped history delete
# over HTTP, so teardown structurally cannot clear the rows. Not colliding is
# cheaper and safer than adding a destructive route to the app.
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
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
        except Exception as exc:  # unreachable app, DNS, refused connection
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            return json.loads(raw)
        except ValueError:
            return {"ok": False, "error": f"non-JSON response: {raw[:200]}"}

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


def ensure_seed_site(client, *, dry_run: bool = False):
    """Return a site id the seeder OWNS, creating one if needed.

    Deliberately never borrows an existing unmarked site. On a working
    deployment the configured site is the operator's REAL one; enqueueing
    synthetic URLs there would show them jobs they did not queue and would make
    teardown ambiguous, because the site itself is not ours to remove. Reusing
    an already-marked site keeps repeated capture runs from accumulating sites.
    """
    existing = _marked_site_ids(client)
    if existing:
        return existing[0]
    if dry_run:
        return None
    created = client.post("/api/sites", queue_site_config())
    sid = (created or {}).get("id") if isinstance(created, dict) else None
    if not sid:
        raise SeedRefused(
            f"could not create the {SEED_MARKER} fixture site "
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


def requeue_for_dedup(client, site_id, url, *,
                      timeout: float = DEFAULT_SETTLE_TIMEOUT,
                      dry_run: bool = False) -> dict:
    """Queue an already-completed URL again, so L14 has a real subject.

    L14 (stash-dedup-skip) asks whether BD skipped a duplicate. Two things stop
    the seed set answering that on its own:

      * the deliberate repeat inside one batch is collapsed at INTAKE, before
        anything is queued (measured: `"dupes": 1` in the seed plan), so the
        runner never sees it;
      * the only remaining route to green was a COLLISION with the previous
        run's history, which the per-run nonce now removes -- and which was
        never the right evidence anyway. It certified that dedup fired at some
        point, not that BD skipped the duplicate just handed to it.

    Queuing the URL again after its first copy has completed produces the
    genuine same-run decision. A skip here is the assertion L14 wants to make.
    """
    plan = {"action": "requeue_for_dedup", "marker": SEED_MARKER,
            "site_id": site_id, "url": url}
    if dry_run:
        plan["dry_run"] = True
        return plan
    plan["queued"] = client.post("/api/queue/v2",
                                 {"site_id": site_id, "urls": [url]})
    plan["started"] = start_seeded_site(client, site_id)
    settle = wait_for_settle(client, site_id, [url], timeout=timeout)
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
    "history is append-only: db_log() is its only writer and db_prune() -- "
    "which deletes by AGE, not by marker -- its only deleter, so no "
    "marker-matched teardown over the HTTP API can remove the row a completed "
    "seeded download leaves. Its library_record row and the downloaded file "
    "under the seeded site's download_dir are in the same class. Nothing here "
    "is removed; it is reported so a later reader does not mistake it for "
    "organic history."
)


def _seeded_history(client):
    """(marked history rows, readable). (None, False) when it cannot be read.

    Reads through the app, like every other question this tool asks.
    /api/history returns a BARE ARRAY by default and a {rows, next_cursor}
    envelope when paginating, so both shapes are accepted; anything else is
    UNKNOWN and says so rather than counting as zero.
    """
    body = client.get(f"/api/history?q={SEED_MARKER}&limit={_HISTORY_PAGE}")
    rows = None
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict) and isinstance(body.get("rows"), list):
        rows = body["rows"]
    if rows is None:
        return None, False
    # The server-side filter is a LIKE over url/filename/message, so re-check
    # the marker against the url field here: the subject is a seeded URL, not
    # any row whose message happens to mention one.
    return sum(1 for row in rows
               if isinstance(row, dict)
               and SEED_MARKER in str(row.get("url", ""))), True
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


def teardown(client, *, dry_run: bool = False) -> dict:
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
    plan = {"action": "teardown", "marker": SEED_MARKER,
            "ids": ids, "sites": sites, "tunnels": tunnels,
            "residue": {"history_rows": history_rows,
                        "readable": history_readable,
                        "note": RESIDUE_NOTE}}
    if dry_run:
        plan["dry_run"] = True
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
    return plan


def _report_residue(plan) -> None:
    """Say out loud what teardown could not remove.

    Silent only when the answer is a measured zero. An unreadable history is
    NOT a clean one and says so; a non-zero count names itself rather than
    hiding inside the JSON a reader may skim past.
    """
    residue = (plan or {}).get("residue") or {}
    rows = residue.get("history_rows")
    if rows is None:
        print(f"live_seed: RESIDUE UNKNOWN - could not read /api/history, so "
              f"whether this host still holds {SEED_MARKER} history rows is "
              f"undetermined. {RESIDUE_NOTE}", file=sys.stderr)
    elif rows:
        print(f"live_seed: RESIDUE - {rows} {SEED_MARKER} history row(s) "
              f"remain after teardown. {RESIDUE_NOTE}", file=sys.stderr)


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
    try:
        if args.teardown:
            plan = teardown(client, dry_run=args.dry_run)
            print(json.dumps(plan, indent=2))
            _report_residue(plan)
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
                            plans.append(requeue_for_dedup(
                                client, seeded.get("site_id"), done_urls[0],
                                timeout=args.start_timeout))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
