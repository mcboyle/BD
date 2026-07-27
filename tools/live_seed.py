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
    python3 tools/live_seed.py --seed --dry-run     # report intent, change nothing
    python3 tools/live_seed.py --teardown
"""
from __future__ import annotations

import argparse
import json
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

# The local fixture origin. tools/fixture_site.py serves deterministic media
# here, so a seeded download exercises the real fetch path without touching any
# third-party site.
FIXTURE_ORIGIN = "http://127.0.0.1:8899"

DEFAULT_BASE_URL = "http://127.0.0.1:5555"


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
        raise SeedRefused(
            f"{done_today} real download(s) completed today on this host. "
            "Refusing to seed on top of real work; pass --force to override."
        )
    return body


def seeded_url(index: int) -> str:
    """A fixture URL that carries the marker in its path."""
    return f"{FIXTURE_ORIGIN}/{SEED_MARKER}/clip{index}.mp4"


SEED_SITE_NAME = f"{SEED_MARKER} fixture site"


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
    created = client.post("/api/sites", {
        "name": SEED_SITE_NAME,
        # download_dir is left to the app's own default; _create_site fills
        # unset fields from DEFAULTS and validates paths before creating
        # anything, so a bad value cannot leave half-initialised state.
    })
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
        "success_url": f"{FIXTURE_ORIGIN}/formauth/members",
        # BD writes this file during login. The seeder never touches it -- if
        # it did, L8 and L9 would be checking the fixture's output rather than
        # BD's credential persistence, which is the vacuous PASS this design
        # exists to prevent.
        "cookie_file": f"cookies/{SEED_MARKER}_fixture.json",
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
        status = client.get("/api/status")
        if isinstance(status, dict):
            meta = status.get(sid)
            if isinstance(meta, dict):
                state = str(meta.get("auth_state", ""))
                if state:
                    plan["auth_state"] = state
                if state == "ok":
                    break
        time.sleep(1.0)
    return plan


def teardown(client, *, dry_run: bool = False) -> dict:
    """Cancel exactly the queue entries this tool created.

    The predicate is the marker, never position or recency, so a real entry
    queued alongside a seeded one is never touched.
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
    plan = {"action": "teardown", "marker": SEED_MARKER,
            "ids": ids, "sites": sites}
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
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed synthetic INPUT for the live checks (marked, reversible).")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--seed", action="store_true", help="enqueue marked fixture URLs")
    parser.add_argument("--login", action="store_true",
                        help="create a fixture-login site and trigger BD's real login")
    parser.add_argument("--teardown", action="store_true", help="remove marked entries")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--site-id", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen; change nothing")
    parser.add_argument("--force", action="store_true",
                        help="seed even if the host already holds real work")
    args = parser.parse_args(argv)

    if not args.seed and not args.teardown and not args.login:
        parser.error("choose --seed, --login or --teardown")

    client = Client(args.base_url)
    try:
        if args.teardown:
            print(json.dumps(teardown(client, dry_run=args.dry_run), indent=2))
        elif args.seed or args.login:
            if not args.dry_run:
                preflight(client, force=args.force)
            plans = []
            if args.seed:
                plans.append(seed_queue(client, args.count, site_id=args.site_id,
                                        dry_run=args.dry_run))
            if args.login:
                plans.append(seed_login(client, dry_run=args.dry_run))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
