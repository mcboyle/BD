"""v3.66.326 — credentials route through the secrets vault, never plaintext.

The SPA Add-Site wizard's password field claimed "(stored encrypted)" but
POST /api/sites wrote the plaintext password into sites_config.json, and
neither create nor update consulted the secrets backend. This change routes a
typed password through secrets_store as a @cred: reference (mirroring
/api/captures/setup_site): plaintext backend -> 400 (encrypt first), locked
encrypted backend -> 401 secrets_locked (the SPA's unlock-prompt trigger),
unlocked -> stored. Plaintext is NEVER persisted to sites_config.json.

Runner conventions: zero-arg tests, restore module globals in try/finally.
(This file carried "no pytest fixtures" until 2026-09-01. It now has exactly
one -- the autouse SITES_FILE pin restore below, which needs a teardown that
runs after EVERY test in the file, including one that fails. The tests
themselves are still zero-arg.) The default sandbox backend is PlaintextBackend, so
the create/update e2e tests exercise the plaintext-refusal path (which is
exactly what proves "no plaintext leak"); the locked/unlocked branches are
covered by the guard-helper unit test with stub backends.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _sites_file_pin_is_returned_as_found():
    """This file must not poison its neighbours either.

    Booting the app REPLACES ``app.SITES_FILE`` with an absolute path built
    from the current cwd (see ``_require_live_sites_file`` below), and the
    autouse ``isolated_bd_home`` fixture in conftest puts every test in its
    own ``tmp_path``.  So each test here pins the module inside a directory
    that belongs to that test alone -- measured 2026-09-01 with a session-end
    scan of ``bulk_downloader.*`` globals: after this file ran, both
    ``app.SITES_FILE`` and ``app._SITES_FILE_LAST_AUTO_OBJECT`` pointed at a
    directory that no longer existed.

    That is the SAME defect this file was the victim of.  A fix that repairs
    only the file that bit us and leaves us biting the next one is not a fix,
    so restore the pin here too -- in a fixture teardown, unconditionally.

    Being defined in the test module, this fixture tears down INSIDE
    conftest's ``isolated_bd_home``, so the pin is restored while that
    fixture's cwd/env sandbox is still standing.
    """
    from bulk_downloader import app as a
    saved = a.SITES_FILE
    saved_latch = a._SITES_FILE_LAST_AUTO_OBJECT
    try:
        yield
    finally:
        a.SITES_FILE = saved
        a._SITES_FILE_LAST_AUTO_OBJECT = saved_latch


def _require_live_sites_file(a):
    """Precondition: the config directory this file was handed must EXIST.

    ``app.SITES_FILE`` is PROCESS-GLOBAL state.  It starts life as the
    relative ``Path("sites_config.json")`` and the first boot in the process
    (``_publish_sites_file_for_runtime``, reached from ``boot_once`` and
    ``_activate_configured_runtime_once``) replaces it with the ABSOLUTE
    resolution of that relative path -- i.e. with whatever directory the
    booting test happened to be standing in.  ``conftest``'s autouse
    ``isolated_bd_home`` resets ``_SITE_RUNTIME_PATH``/``_BOOTED_PATHS``
    between tests but does NOT restore ``SITES_FILE``, so the pin outlives
    the test that made it.

    A file that boots while chdir'd into a ``tempfile.TemporaryDirectory``
    therefore hands every later file a path inside a directory that is
    DELETED on the way out.  Every write below then died six assertions deep
    with a bare ``FileNotFoundError`` that named a tmp path and nothing else.
    Assert the precondition instead, so a future leak reports its own cause.
    """
    assert a.SITES_FILE.parent.is_dir(), (
        "precondition failed: app.SITES_FILE is pinned to a directory that "
        f"does not exist ({a.SITES_FILE}). An earlier test file booted the "
        "app while chdir'd into a temporary directory and did not restore "
        "app.SITES_FILE / app._SITES_FILE_LAST_AUTO_OBJECT before that "
        "directory was deleted. This file's own assertions were never "
        "reached.")


def _boot_empty():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    _require_live_sites_file(a)
    a.SITES_FILE.write_text(json.dumps({}), encoding="utf-8")
    a._load_sites_config()
    return a, a.app.test_client()


def _boot_with_site():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    _require_live_sites_file(a)
    a.SITES_FILE.write_text(json.dumps({"demo": {
        "name": "Demo", "max_concurrent": 4, "wait": 5,
        "login_url": "https://demo.example/login",
        "password": "ORIG_PW"}}), encoding="utf-8")
    a._load_sites_config()
    a.runners["demo"].update_config = lambda *_a, **_k: None
    return a, a.app.test_client()


# ── the precondition is a live measurement, not decoration ─────────────

def test_sites_file_precondition_fires_on_a_dead_pin_and_passes_on_a_live_one():
    """Negative control for ``_require_live_sites_file``.

    A precondition that cannot fail is not a precondition -- it would let this
    file report green while measuring nothing, which is precisely the shape
    that let a leaked pin surface as six unrelated FileNotFoundErrors.  Prove
    BOTH outcomes are reachable from here: it refuses a deleted directory and
    names it, and it passes for a live one.
    """
    from bulk_downloader import app as a

    dead = tempfile.mkdtemp(prefix="bd-326-dead-pin-")
    os.rmdir(dead)
    # Precondition OF THE CONTROL: the fixture really did build a dead path.
    assert not Path(dead).exists(), "the control failed to delete its own dir"

    orig_sites_file = a.SITES_FILE
    orig_latch = a._SITES_FILE_LAST_AUTO_OBJECT
    try:
        a.SITES_FILE = Path(dead) / "sites_config.json"
        with pytest.raises(AssertionError) as caught:
            _require_live_sites_file(a)
        message = str(caught.value)
        # The distinctive diagnostic, not merely "an AssertionError".
        assert "app.SITES_FILE is pinned to a directory that does not exist" \
            in message
        assert dead in message, message
        assert "_SITES_FILE_LAST_AUTO_OBJECT" in message
    finally:
        a.SITES_FILE = orig_sites_file
        a._SITES_FILE_LAST_AUTO_OBJECT = orig_latch

    # Positive control: the same call is silent for a directory that exists,
    # so the guard is not a constant refusal.
    live = tempfile.mkdtemp(prefix="bd-326-live-pin-")
    try:
        a.SITES_FILE = Path(live) / "sites_config.json"
        assert Path(live).is_dir()
        _require_live_sites_file(a)
    finally:
        a.SITES_FILE = orig_sites_file
        a._SITES_FILE_LAST_AUTO_OBJECT = orig_latch
        os.rmdir(live)


# ── core guarantee: a typed password never lands as plaintext ───────────────

def test_create_with_password_never_persists_plaintext():
    a, c = _boot_empty()
    c.post("/api/sites", json={
        "name": "VaultSite",
        "login_url": "https://x.example/login",
        "password": "SUPERSECRET_CREATE_PW"})
    raw = a.SITES_FILE.read_text(encoding="utf-8")
    assert "SUPERSECRET_CREATE_PW" not in raw, \
        "plaintext password leaked into sites_config.json on create"


def test_create_with_password_on_plaintext_backend_flags_not_stored():
    # Plaintext backend -> the SITE is still created (200, {id}), but the
    # credential is NOT stored (no plaintext leak) and secrets_plaintext flags
    # it so the SPA can prompt to switch backends.
    from bulk_downloader import secrets_store as ss
    a, c = _boot_empty()
    orig = ss.get_backend
    try:
        class _Plain:
            name = "plaintext"
        ss.get_backend = lambda: _Plain()
        r = c.post("/api/sites", json={
            "name": "VaultSite",
            "login_url": "https://x.example/login",
            "password": "PW"})
        assert r.status_code == 200
        body = r.get_json() or {}
        assert "id" in body
        assert body.get("secrets_plaintext") is True
        assert body.get("cred_stored") is False
        # site created, but no plaintext anywhere
        raw = a.SITES_FILE.read_text(encoding="utf-8")
        assert "PW" not in raw or '"PW"' not in raw
        disk = json.loads(raw)
        assert any(s.get("name") == "VaultSite" for s in disk.values())
        created = next(s for s in disk.values() if s.get("name") == "VaultSite")
        # no plaintext password stored (absent, blank, or a @cred ref only)
        pw = created.get("password", "")
        assert pw in ("", None) or pw.startswith("@cred:")
    finally:
        ss.get_backend = orig


def test_create_with_password_on_locked_backend_flags_secrets_locked():
    # Locked encrypted backend -> site created (200, {id}), credential not
    # stored, secrets_locked set (the SPA's unlock-prompt trigger). No plaintext.
    from bulk_downloader import secrets_store as ss
    a, c = _boot_empty()
    orig = ss.get_backend
    try:
        class _Locked:
            name = "master_password"
            def is_unlocked(self):
                return False
        ss.get_backend = lambda: _Locked()
        r = c.post("/api/sites", json={
            "name": "LockedSite",
            "login_url": "https://x.example/login",
            "password": "PW"})
        assert r.status_code == 200
        body = r.get_json() or {}
        assert "id" in body
        assert body.get("secrets_locked") is True
        assert body.get("cred_stored") is False
        disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))
        created = next(s for s in disk.values() if s.get("name") == "LockedSite")
        pw = created.get("password", "")
        assert pw in ("", None) or pw.startswith("@cred:")
    finally:
        ss.get_backend = orig


def test_create_without_password_still_works_on_plaintext_backend():
    # No password -> no vault needed -> create proceeds normally.
    a, c = _boot_empty()
    r = c.post("/api/sites", json={
        "name": "NoPwSite", "login_url": "https://y.example/login"})
    assert r.status_code == 200
    disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))
    assert any(s.get("name") == "NoPwSite" for s in disk.values())


def test_update_with_new_password_never_persists_plaintext():
    a, c = _boot_with_site()
    c.put("/api/sites/demo", json={"password": "NEWTYPED_SECRET_PW"})
    raw = a.SITES_FILE.read_text(encoding="utf-8")
    assert "NEWTYPED_SECRET_PW" not in raw, \
        "plaintext password leaked into sites_config.json on update"


def test_update_blank_password_preserves_existing():
    # preserve-on-blank must still hold after the vault routing change.
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"max_concurrent": 6, "password": ""})
    assert r.status_code == 200
    disk = json.loads(a.SITES_FILE.read_text(encoding="utf-8"))["demo"]
    assert disk["password"] == "ORIG_PW"
    assert disk["max_concurrent"] == 6


# ── guard branches that drive the SPA unlock prompt ─────────────────────────

def test_vault_guard_branches():
    from bulk_downloader import app as a
    from bulk_downloader import secrets_store as ss
    orig = ss.get_backend
    try:
        class _Plain:
            name = "plaintext"
        ss.get_backend = lambda: _Plain()
        allowed, status, body = a._vault_guard_for_password()
        assert allowed is False and status == 400 and body.get("secrets_plaintext")

        class _Locked:
            name = "master_password"
            def is_unlocked(self):
                return False
        ss.get_backend = lambda: _Locked()
        allowed, status, body = a._vault_guard_for_password()
        assert allowed is False and status == 401 and body.get("secrets_locked")

        class _Open:
            name = "master_password"
            def is_unlocked(self):
                return True
        ss.get_backend = lambda: _Open()
        allowed, status, body = a._vault_guard_for_password()
        assert allowed is True and status is None and body is None
    finally:
        ss.get_backend = orig


def test_store_helper_writes_cred_ref_not_plaintext():
    # With an unlocked stub backend, the helper stores via backend.set and
    # writes a @cred: reference onto s_cfg[sid]["password"].
    from bulk_downloader import app as a
    from bulk_downloader import secrets_store as ss
    orig = ss.get_backend
    try:
        stored = {}

        class _Open:
            name = "master_password"
            def is_unlocked(self):
                return True
            def set(self, key, password):
                stored[key] = password
        ss.get_backend = lambda: _Open()
        a.s_cfg["zz"] = {"name": "ZZ"}
        ok, err = a._store_site_password_in_vault("zz", "PLAINTEXT_PW")
        assert ok is True and err is None
        ref = a.s_cfg["zz"]["password"]
        assert ref.startswith(ss.CRED_PREFIX)
        assert "PLAINTEXT_PW" not in ref
        # the actual secret went to the backend, not the config ref
        assert "PLAINTEXT_PW" in stored.values()
    finally:
        ss.get_backend = orig
        a.s_cfg.pop("zz", None)
