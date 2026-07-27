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
