"""Contract for tools/live_seed.py -- synthetic INPUT for the live checks.

Several live checks WARN because nothing has exercised them: no queued URLs, no
completed downloads, no login events. Those warnings are honest, and #31 made
them non-fatal. The seeder exists so a capture host can exercise the paths
without a human queueing work by hand.

THE RISK THIS FILE EXISTS TO CONTAIN. A check that passes against data the
fixture itself produced is VACUOUS: it proves the fixture ran, not that BD
works. Fourteen honest WARNs turned into fourteen fake PASSes would be strictly
worse than the status quo -- it destroys real signal and manufactures
confidence. So the seeder may only supply INPUT to a real code path, never
substitute for its OUTPUT, and these tests pin that structurally:

  * writes go through the running app's HTTP API, never raw SQL -- so every
    write traverses the same code the UI does. A bare INSERT would also leave
    history_fts and library_record stale (db_log maintains them by hand; there
    are no triggers), manufacturing a denominator split inside the fixture.
  * everything created carries a marker, so a synthetic PASS is never mistaken
    for an organic one and teardown can remove exactly what it made.
  * the preflight refuses to touch a database that already holds real work, and
    refuses when it cannot tell -- unknown is a third state and it fails.
"""
from __future__ import annotations

import ast
import sys
import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / "tools" / "live_seed.py"


def _load():
    loader = importlib.machinery.SourceFileLoader("bd_live_seed", str(SEED_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class FakeClient:
    """Records every request instead of issuing one.

    Lets the contract be tested without a running app, and makes "what did the
    seeder actually send" an assertable fact rather than an assumption.
    """

    def __init__(self, responses=None):
        self.calls = []
        self._responses = responses or {}

    def get(self, path):
        self.calls.append(("GET", path, None))
        return self._responses.get(("GET", path), {"ok": True})

    def post(self, path, payload):
        self.calls.append(("POST", path, payload))
        return self._responses.get(("POST", path), {"ok": True})

    def delete(self, path):
        self.calls.append(("DELETE", path, None))
        return self._responses.get(("DELETE", path), {"ok": True})

    def posted(self):
        return [(p, body) for method, p, body in self.calls if method == "POST"]


# ── the structural rule: HTTP only, never raw SQL ────────────────────────────

def test_the_seeder_never_opens_the_database_for_writing():
    """Every write must traverse the app's own code path.

    AST, not grep: the predicate is "a call to sqlite3.connect", which a string
    search would also find inside a docstring explaining why we do not do it.
    A read-only connection is permitted -- the preflight needs to look before
    it leaps -- so the assertion is about WRITE access specifically.
    """
    tree = ast.parse(SEED_PATH.read_text(encoding="utf-8"), filename=str(SEED_PATH))
    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None)
        owner = getattr(getattr(func, "value", None), "id", None)
        if name == "connect" and owner == "sqlite3":
            uri = ""
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    uri = arg.value
            # A mode=ro URI is a read; anything else is a potential write.
            if "mode=ro" not in uri:
                writes.append(node.lineno)
    assert not writes, (
        f"tools/live_seed.py opens sqlite3 for writing at line(s) {writes}; "
        f"seeding must go through the app's HTTP API so the write traverses "
        f"the real code path and keeps derived stores (history_fts, "
        f"library_record) consistent"
    )


def test_every_created_entity_carries_the_seed_marker():
    """A synthetic PASS must never be mistaken for an organic one."""
    seed = _load()
    assert isinstance(seed.SEED_MARKER, str) and seed.SEED_MARKER, (
        "live_seed must define a non-empty SEED_MARKER"
    )
    client = FakeClient()
    # site_id is supplied explicitly: this test is about the marker, not about
    # site discovery, and leaving it implicit would make the assertion depend
    # on a second behaviour it does not intend to pin.
    seed.seed_queue(client, count=2, site_id="fixture-site")
    bodies = [body for _path, body in client.posted() if isinstance(body, dict)]
    assert bodies, "seed_queue issued no POSTs"
    marked = [
        b for b in bodies
        if seed.SEED_MARKER in " ".join(str(v) for v in b.values())
    ]
    assert len(marked) == len(bodies), (
        f"only {len(marked)} of {len(bodies)} seeded payloads carry "
        f"{seed.SEED_MARKER!r}; an unmarked row cannot be told from real work "
        f"and teardown cannot find it"
    )


# ── the preflight guard ──────────────────────────────────────────────────────

def test_preflight_refuses_a_database_that_already_holds_real_work():
    """Never seed on top of an operator's real data."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {
            "ok": True, "running": [], "waiting": [{"url": "https://real/1"}],
            "done_today_count": 0,
        },
    })
    with pytest.raises(seed.SeedRefused) as excinfo:
        seed.preflight(client)
    assert "real" in str(excinfo.value).lower() or "not empty" in str(excinfo.value).lower()


def test_preflight_refuses_when_it_cannot_determine_safety():
    """Unknown is a third state and it FAILS (CLAUDE.md 0).

    An unreachable or malformed queue endpoint means the seeder cannot know
    whether the host holds real work. Proceeding on an unreadable answer is
    exactly the "gate that cannot see its subject" failure -- so refuse.
    """
    seed = _load()
    for body in ({"ok": False, "error": "boom"}, {}, None, "not-a-dict"):
        client = FakeClient({("GET", "/api/queue/v2"): body})
        with pytest.raises(seed.SeedRefused):
            seed.preflight(client)


def test_preflight_allows_an_empty_queue():
    """The normal capture-host case must not be blocked."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {
            "ok": True, "running": [], "waiting": [], "done_today_count": 0,
        },
    })
    seed.preflight(client)  # must not raise


def test_force_overrides_the_guard_but_must_be_explicit():
    """An escape hatch is fine; a silent one is not."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {
            "ok": True, "running": [], "waiting": [{"url": "https://real/1"}],
            "done_today_count": 0,
        },
    })
    with pytest.raises(seed.SeedRefused):
        seed.preflight(client)
    seed.preflight(client, force=True)  # explicit override, must not raise


# ── teardown ─────────────────────────────────────────────────────────────────

def test_teardown_targets_only_marked_entities():
    """Teardown must never remove work the seeder did not create."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {
            "ok": True,
            "running": [],
            "waiting": [
                {"id": 1, "url": f"http://127.0.0.1:8899/{seed.SEED_MARKER}/a"},
                {"id": 2, "url": "https://real-site.example/video/999"},
            ],
            "done_today_count": 0,
        },
    })
    seed.teardown(client)
    cancelled = [
        body for path, body in client.posted() if "cancel" in path
    ]
    blob = " ".join(str(b) for b in cancelled)
    assert "999" not in blob, (
        "teardown targeted a real queue row; it must only remove entries "
        f"carrying {seed.SEED_MARKER!r}"
    )


def test_dry_run_issues_no_state_changing_request():
    """--dry-run must be honest: report intent, change nothing."""
    seed = _load()
    client = FakeClient()
    seed.seed_queue(client, count=3, dry_run=True)
    assert not client.posted(), (
        f"dry-run issued {len(client.posted())} POST(s); it must not mutate"
    )


# ── the seeder must own its site, never borrow the operator's ───────────────

def test_seeding_never_enqueues_against_an_unmarked_site():
    """Synthetic URLs must never land in a real site's queue.

    The first version picked whichever site happened to be configured, which
    on a working deployment is the operator's REAL site. That mixes synthetic
    rows into real work: the operator sees jobs they did not queue, and
    teardown becomes ambiguous because the site itself is not ours to remove.
    The seeder must use a site it created and marked.
    """
    seed = _load()
    real_sid = "aabbccdd"
    client = FakeClient({
        ("GET", "/api/status"): {
            real_sid: {"name": "My Real Site", "config": {}},
        },
        ("POST", "/api/sites"): {"id": "11223344"},
    })
    seed.seed_queue(client, count=1)
    enqueued = [
        body for path, body in client.posted()
        if path.endswith("/add_url") and isinstance(body, dict)
    ]
    assert enqueued, "no URL was enqueued"
    for body in enqueued:
        assert body.get("site_id") != real_sid, (
            f"seeded against the operator's real site {real_sid!r}; the seeder "
            f"must create and use its own marked site"
        )


def test_an_existing_marked_site_is_reused_rather_than_duplicated():
    """Re-running the seeder must not accumulate sites."""
    seed = _load()
    marked_sid = "99887766"
    client = FakeClient({
        ("GET", "/api/status"): {
            marked_sid: {"name": f"{seed.SEED_MARKER} fixture", "config": {}},
        },
    })
    seed.seed_queue(client, count=1)
    created = [p for p, _b in client.posted() if p == "/api/sites"]
    assert not created, (
        "a marked site already existed but the seeder created another; "
        "repeated capture runs would accumulate sites"
    )
    enqueued = [b for p, b in client.posted() if p.endswith("/add_url")]
    assert enqueued and all(b.get("site_id") == marked_sid for b in enqueued)


def test_teardown_removes_marked_sites_but_never_unmarked_ones():
    """Teardown must leave the operator's sites untouched."""
    seed = _load()
    client = FakeClient({
        ("GET", "/api/queue/v2"): {
            "ok": True, "running": [], "waiting": [], "done_today_count": 0,
        },
        ("GET", "/api/status"): {
            "deadbeef": {"name": f"{seed.SEED_MARKER} fixture", "config": {}},
            "aabbccdd": {"name": "My Real Site", "config": {}},
        },
    })
    seed.teardown(client)
    deleted = [p for method, p, _b in client.calls if method == "DELETE"]
    assert any("deadbeef" in p for p in deleted), (
        "teardown left its own marked site behind"
    )
    assert not any("aabbccdd" in p for p in deleted), (
        "teardown deleted the operator's real site"
    )


# ── the login seed (L6 / L8 / L9) ───────────────────────────────────────────

def test_the_seeder_never_writes_a_cookie_jar_itself():
    """THE anti-vacuity assertion for the login seed.

    L8 asserts that every site reporting auth_state=ok has a NON-EMPTY cookie
    file on disk -- it is checking BD's own credential persistence. If the
    seeder wrote that file, L8 would be verifying the fixture's handiwork and
    would pass just as happily with BD's persistence completely broken. That is
    the vacuous PASS this whole design exists to prevent, and it would be worse
    than the honest WARN it replaced.

    So the seeder supplies the site config and TRIGGERS a real login; BD drives
    the browser, receives the cookie and writes the jar. AST, not grep: the
    predicate is "a call to open(...) in write mode", which a string search for
    'cookie' would miss entirely and a search for 'open(' would over-match.
    """
    tree = ast.parse(SEED_PATH.read_text(encoding="utf-8"), filename=str(SEED_PATH))
    writes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if target not in ("open", "write_text", "write_bytes"):
            continue
        if target in ("write_text", "write_bytes"):
            writes.append(node.lineno)
            continue
        for arg in node.args[1:]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if any(m in arg.value for m in ("w", "a", "x", "+")):
                    writes.append(node.lineno)
    assert not writes, (
        f"tools/live_seed.py writes files at line(s) {writes}; the login seed "
        f"must TRIGGER a real login and let BD persist the cookie jar, or L8 "
        f"and L9 verify the fixture's own output instead of BD's"
    )


def test_login_seed_refuses_when_the_password_cannot_be_vaulted():
    """A site created without a password is a site that cannot log in.

    POST /api/sites routes the password to the encrypted secrets vault. When
    the backend is plaintext or locked the SITE is still created but NO
    password is stored -- the response says so via secrets_plaintext /
    secrets_locked / cred_stored=false. Proceeding would leave a half-configured
    marked site behind and make L6 fail for a reason that has nothing to do with
    BD's login path. Refuse, and say which precondition failed.
    """
    seed = _load()
    for flag in ({"id": "abc123", "cred_stored": False, "secrets_locked": True},
                 {"id": "abc123", "cred_stored": False, "secrets_plaintext": True},
                 {"id": "abc123", "cred_stored": False}):
        client = FakeClient({("POST", "/api/sites"): flag})
        with pytest.raises(seed.SeedRefused) as excinfo:
            seed.seed_login(client)
        msg = str(excinfo.value).lower()
        assert "password" in msg or "vault" in msg or "secret" in msg, (
            f"refusal did not explain the vault precondition: {excinfo.value}"
        )


def test_login_seed_points_at_the_local_fixture_origin_only():
    """A seeded login must never reach a third-party site."""
    seed = _load()
    client = FakeClient({("POST", "/api/sites"): {"id": "abc123", "cred_stored": True}})
    try:
        # poll_seconds=0 so the readiness loop does not burn its real 30s
        # budget against a fake that will never report auth_state=ok.
        seed.seed_login(client, poll_seconds=0)
    except seed.SeedRefused:
        pass  # polling may refuse without a live app; the config is the subject
    created = [b for p, b in client.posted() if p == "/api/sites" and isinstance(b, dict)]
    assert created, "no site was created"
    cfg = created[0]
    login_url = str(cfg.get("login_url", ""))
    assert login_url.startswith(seed.FIXTURE_ORIGIN), (
        f"login_url {login_url!r} does not point at the local fixture origin; "
        f"a seeded login must never touch a real site"
    )


def test_login_seed_triggers_the_real_login_endpoint():
    """The login itself must be BD's, not a simulation."""
    seed = _load()
    client = FakeClient({("POST", "/api/sites"): {"id": "abc123", "cred_stored": True}})
    try:
        seed.seed_login(client, poll_seconds=0)
    except seed.SeedRefused:
        pass
    assert any(p.endswith("/login") for p, _b in client.posted()), (
        "the seeder never POSTed to /api/sites/<sid>/login; it must trigger "
        "BD's own login path rather than fabricate the resulting state"
    )


def test_a_dry_run_does_not_claim_synthetic_state_is_present(tmp_path):
    """A dry run changed nothing, so it must not announce synthetic state.

    Regression: main() printed the "SYNTHETIC state is present on this host"
    banner unconditionally, including for --dry-run. The banner exists so a
    reader is never misled about what is real -- printing it when nothing was
    written is its own small false report, and precisely the kind that erodes
    trust in every other line the tool prints.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(SEED_PATH), "--seed", "--dry-run", "--count", "1"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "SYNTHETIC" not in proc.stderr, (
        "a dry run announced synthetic state despite writing nothing:\n"
        + proc.stderr
    )


def test_refusal_exits_non_zero_so_a_caller_can_detect_it():
    """A refusal that exits 0 is indistinguishable from success.

    capture.sh will call this tool; if REFUSED exited 0 the caller would
    proceed as though the host were seeded.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(SEED_PATH), "--seed", "--count", "1",
         "--base-url", "http://127.0.0.1:59999"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT),
    )
    assert proc.returncode != 0, (
        "seeding against an unreachable app exited 0; an unreadable host state "
        "is UNKNOWN and must fail loudly"
    )
    assert "REFUSED" in proc.stderr


def test_the_module_is_a_standalone_tool_not_a_live_tests_import():
    """live_tests must stay read-only.

    The whole safety property of the live_tests package is that it cannot
    write: Context exposes GET and a mode=ro DB handle, so the suite is safe to
    point at production. Putting a writer inside it would destroy that
    invariant for every future check, so the seeder lives in tools/.
    """
    assert SEED_PATH.parent.name == "tools"
    tree = ast.parse(SEED_PATH.read_text(encoding="utf-8"), filename=str(SEED_PATH))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("live_tests"):
            pytest.fail("live_seed must not import live_tests; the suite stays read-only")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("live_tests"):
                    pytest.fail("live_seed must not import live_tests")


def test_a_later_refusal_does_not_swallow_an_earlier_success(capsys, monkeypatch):
    """seed_queue's result must survive seed_login raising.

    main() collected both plans and printed them only AFTER both had run:

        plans = []
        if args.seed:  plans.append(seed_queue(...))   # succeeds
        if args.login: plans.append(seed_login(...))   # raises SeedRefused
        print(json.dumps(plans ...))                   # never reached

    So on a host where the queue seeded fine and the login could not -- a
    locked vault, say -- the log showed the refusal and nothing else. Observed
    exactly that on the box: 05a_live_seed.log read as though nothing had
    happened while three URLs had in fact been queued, which made it impossible
    to tell whether a separate fix to the seeded URLs was working.

    A partial success reported as total silence is a false negative about the
    tool's own behaviour: the same shape as a gate reporting OK over nothing,
    inverted.
    """
    seed = _load()
    client = FakeClient()
    monkeypatch.setattr(seed, "Client", lambda base_url: client)
    monkeypatch.setattr(seed, "preflight", lambda c, force=False: {"ok": True})

    def _vault_locked(c, dry_run=False):
        raise seed.SeedRefused("the secrets vault is LOCKED")

    monkeypatch.setattr(seed, "seed_login", _vault_locked)

    rc = seed.main(["--seed", "--login", "--count", "2",
                    "--site-id", "fixture-site"])
    out = capsys.readouterr()

    assert rc != 0, "a refused login must still be a non-zero exit"
    assert "LOCKED" in out.err, f"the refusal was not reported:\n{out.err}"
    assert out.out.strip(), (
        "seed_queue succeeded and its plan was never printed. stdout is empty, "
        "so the operator cannot tell a partial seed from no seed at all.\n"
        f"stderr was:\n{out.err}"
    )
    assert seed.SEED_MARKER in out.out, (
        f"stdout does not describe the queue seed that ran:\n{out.out}"
    )


def _fixture_app():
    """The real fixture Flask app, so the denominator is its actual url_map.

    Not a grep for @app.route strings: the routes carry converters
    (`<int:sid>`), so only Werkzeug's own matcher can answer whether a concrete
    seeded path resolves. Deriving reachability beats asserting it.
    """
    loader = importlib.machinery.SourceFileLoader(
        "bd_fixture_site", str(REPO_ROOT / "tools" / "fixture_site.py")
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module.make_app()


def test_every_seeded_url_resolves_against_the_fixtures_route_map():
    """The seeded URLs must be servable by the fixture, or the seed is a no-op.

    THE DEFECT THIS GATE COVERS, WHICH NOTHING COVERED BEFORE. seeded_url once
    returned `{origin}/bdseed/clipN.mp4`, putting the marker in the PATH.
    fixture_site.py has no /bdseed/ route, so every seeded download 404'd and
    L11, L12 and L14 reported "no completed downloads" forever. That reads as
    BD failing to download; BD was never handed a URL that resolved. The fix
    moved the marker into the QUERY, which Flask ignores when matching.

    Twenty tests already cover this seeder -- markers, teardown, preflight,
    refusals -- and not one of them asked whether the URL it queues can be
    served. The seeder's own comment says to verify against fixture_site's
    url_map rather than against its local list; this is that check.

    DENOMINATOR: every rule in the fixture's Flask url_map, matched by Werkzeug.
    SUBJECT: every URL seeded_url() emits, read from the shipped _SEED_PATHS so
    the set cannot drift out from under the assertion.
    """
    from urllib.parse import urlsplit

    from werkzeug.exceptions import HTTPException

    seed = _load()
    adapter = _fixture_app().url_map.bind("127.0.0.1")

    urls = [seed.seeded_url(i) for i in range(len(seed._SEED_PATHS))]
    assert urls, "the seed set is empty; this gate would examine nothing"

    unroutable = []
    for url in urls:
        path = urlsplit(url).path
        try:
            adapter.match(path, method="GET")
        except HTTPException as exc:
            unroutable.append(f"{url}  (path {path!r} -> {type(exc).__name__})")

    assert not unroutable, (
        "the seeder queues URLs the fixture does not serve, so every seeded "
        "download 404s and the live checks it feeds report 'no completed "
        "downloads' -- which reads as a BD failure rather than a seeding "
        "one:\n  " + "\n  ".join(unroutable)
    )


def test_the_seed_marker_survives_in_every_seeded_url():
    """Routable is not sufficient: the marker is what makes cleanup possible.

    `_is_seeded` is `SEED_MARKER in entry["url"]`. Both teardown and preflight
    depend on it -- without the marker teardown orphans every seeded row, and
    preflight reads seeded work as the operator's real work and refuses the
    next run. So a "fix" that made the URLs routable by dropping the marker
    would satisfy the route gate above and break the seeder in a quieter way.

    The duplicate is asserted here too. seeded_url(2) must be byte-identical to
    seeded_url(0), query included, because L14 asserts BD SKIPS a URL it
    already holds and needs a repeat to recognise.
    """
    seed = _load()
    urls = [seed.seeded_url(i) for i in range(len(seed._SEED_PATHS))]

    unmarked = [u for u in urls if seed.SEED_MARKER not in u]
    assert not unmarked, (
        "seeded URLs carry no marker, so _is_seeded() cannot recognise them. "
        "Teardown will orphan these rows and the next preflight will read them "
        f"as the operator's real work:\n  " + "\n  ".join(unmarked)
    )

    assert urls[2] == urls[0], (
        "the deliberate duplicate is no longer byte-identical to index 0, so "
        f"L14 has nothing to recognise as a repeat:\n  {urls[0]!r}\n  {urls[2]!r}"
    )
