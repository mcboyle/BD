"""Keep-alive must never start solely because a config lacks its opt-in.

Six seams can each arm an automatic login on their own: the site-config
DEFAULTS, the keeper's own fallback, the spawner's fallback, the PUT
(site edit) fallback, the manual-login profile seed, and the clone strip
set.  Every test here drives the ROUTE that owns its seam, so a mutant
that restores `True` at that seam fails a NAMED test.

Trap, recorded because it produced a wrong reading during review: a
spawner or PUT probe must DELETE `BD_DISABLE_KEEPALIVE`, never set it --
with it set `_start_session_keepers` returns before the fallback is read
and the probe passes whatever the default says.
"""
from __future__ import annotations

import json

import pytest

BD_GATE_SCOPE = "repo-wide"


# ── seam (a): the site-config DEFAULTS, at the mint ──────────────────

def test_new_site_defaults_to_inert_keepalive(fresh_app, monkeypatch):
    """Restoring True as the DEFAULTS value makes this fail."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    response = fresh_app.post("/api/sites", json={"name": "inert-new-site"})
    assert response.status_code == 200
    from bulk_downloader import app as app_module
    sid = response.get_json()["id"]
    assert sid in app_module.s_cfg
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is False, (
        "a newly added site must not arm the automatic login")


def test_pre_change_config_is_inert_at_boot(fresh_app, monkeypatch, tmp_path):
    """The PERSISTENCE path: a sites_config.json written before this
    change, with no `keep_alive_enabled` key at all, is re-minted from
    DEFAULTS at every boot.  Restoring True there re-arms every such
    site on restart, which is the shape the operator reported."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as app_module
    cfg_file = tmp_path / "sites_config.json"
    cfg_file.write_text(json.dumps(
        {"legacy": {"name": "legacy", "password": "p"}}), encoding="utf-8")
    # raising=True: a renamed global must break this test, not silently
    # create an attribute while the real config file is read.
    monkeypatch.setattr(app_module, "SITES_FILE", cfg_file)
    monkeypatch.setattr(app_module, "s_cfg", {})
    app_module._load_sites_config()
    assert "legacy" in app_module.s_cfg, (
        "precondition: the fixture site was actually loaded from disk")
    assert "keep_alive_enabled" not in json.loads(
        cfg_file.read_text(encoding="utf-8"))["legacy"], (
        "precondition: the on-disk fixture omits the key entirely")
    assert app_module.s_cfg["legacy"]["keep_alive_enabled"] is False, (
        "a pre-change config must not be re-armed at boot")


# ── seam (b): the keeper's own fallback ──────────────────────────────

def test_keyless_keeper_disables_before_heartbeat(monkeypatch):
    """Restoring True as the keeper's fallback makes this fail."""
    from bulk_downloader.session_keeper import SessionKeeper
    keeper = SessionKeeper("keyless", 0, {"password": "p"}, lambda *_: (True, ""))
    beats = []
    monkeypatch.setattr(keeper, "_heartbeat", lambda: beats.append(1) or ("alive", "x"))
    # The BROWSER boundary, stubbed and asserted unused.  Without the guard
    # this keeper proceeds into the heartbeat and _launch_browser really does
    # start a persistent context -- a live browser inside a repository test,
    # and one that only appears on the RED replay, where the contract requires
    # us to go.  Stubbing it here means neither the base replay nor a mutation
    # run that restores True can launch one, and the assertion below turns
    # "disables BEFORE the heartbeat" from a name into a measurement.
    launches = []
    monkeypatch.setattr(keeper, "_launch_browser",
                        lambda: launches.append(1) or False)
    keeper._run_one_check()
    assert launches == [], "the disabled path must never launch a browser"
    assert beats == [], "a config missing the key must never reach the heartbeat"
    assert (keeper.state["state"] == "disabled"
            and keeper.state["last_detail"] == "keep_alive_enabled is False"), (
        "a config missing the key must be inert")


def test_explicit_opt_in_keeper_still_reaches_the_heartbeat(monkeypatch):
    """Negative control for seam (b): operator intent is untouched."""
    from bulk_downloader.session_keeper import SessionKeeper
    keeper = SessionKeeper(
        "opted-in", 0, {"password": "p", "keep_alive_enabled": True},
        lambda *_: (True, ""))
    beats = []
    monkeypatch.setattr(keeper, "_heartbeat",
                        lambda: beats.append(1) or ("inconclusive", "stub"))
    monkeypatch.setattr(keeper, "_record_event", lambda *a, **k: None)
    keeper._run_one_check()
    assert len(beats) == 1, "the heartbeat must fire exactly once"
    assert keeper.state["state"] != "disabled"


# ── seam (c): the spawner's fallback ─────────────────────────────────

def test_keyless_config_never_spawns_a_keeper(monkeypatch):
    """Restoring True as the spawner's fallback makes this fail."""
    from bulk_downloader import app as app_module
    from bulk_downloader import session_keeper
    captured = []
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app_module, "s_cfg", {"keyless": {"password": "p"}})
    monkeypatch.setattr(session_keeper, "start_keeper", lambda *args: captured.append(args))
    app_module._start_session_keepers()
    assert len(captured) == 0, "no keeper may be spawned from a missing keep_alive_enabled"


def test_explicit_opt_in_still_spawns_a_keeper(monkeypatch):
    """Negative control for seam (c): explicit operator intent stays active."""
    from bulk_downloader import app as app_module
    from bulk_downloader import session_keeper
    captured = []
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app_module, "s_cfg",
                        {"opted-in": {"password": "p", "keep_alive_enabled": True}})
    monkeypatch.setattr(session_keeper, "start_keeper", lambda *args: captured.append(args))
    app_module._start_session_keepers()
    assert len(captured) == 1


# ── seam (d): the PUT (site edit) fallback ───────────────────────────

def test_put_without_the_key_does_not_respawn_a_keeper(fresh_app, monkeypatch):
    """A site edit that never mentions the key must not arm a login.

    NOTE, and this is why this test is not the PUT mutant's catcher: the
    PUT fallback is EQUIVALENT relative to the spawn route, because the
    spawner it calls also defaults False.  Measured pairwise in DONE.md.
    The pin is defence-in-depth: it fires the day either default moves.
    """
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    from bulk_downloader import app as app_module
    from bulk_downloader import session_keeper
    r = fresh_app.post("/api/sites", json={"name": "put-site"})
    assert r.status_code == 200
    sid = r.get_json()["id"]
    app_module.s_cfg[sid]["password"] = "p"
    app_module.s_cfg[sid].pop("keep_alive_enabled", None)  # predates the key
    assert "keep_alive_enabled" not in app_module.s_cfg[sid], (
        "precondition: the stored config omits the key")
    started = []
    monkeypatch.setattr(session_keeper, "start_keeper", lambda *a: started.append(a))
    rp = fresh_app.put("/api/sites/%s" % sid, json={"name": "put-site-renamed"})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    assert started == [], "a PUT that never mentions the key must not arm a login"


def test_put_with_explicit_opt_in_still_respawns(fresh_app, monkeypatch):
    """Negative control for seam (d): an operator PUT that says True works."""
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    from bulk_downloader import app as app_module
    from bulk_downloader import session_keeper
    r = fresh_app.post("/api/sites", json={"name": "put-on-site"})
    sid = r.get_json()["id"]
    # The PUT body does not carry a password through (credentials have their
    # own route); seed it the way the OFF case above does so the keepalive
    # branch's `and s_cfg[sid].get("password")` guard is satisfied.
    app_module.s_cfg[sid]["password"] = "p"
    started = []
    monkeypatch.setattr(session_keeper, "start_keeper", lambda *a: started.append(a))
    rp = fresh_app.put("/api/sites/%s" % sid,
                       json={"name": "put-on-site", "keep_alive_enabled": True})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is True
    assert len(started) == 1, "an explicit opt-in PUT must respawn the keeper"


# ── seam (e): the manual-login profile seed ──────────────────────────

class _FinishManualLoginHarness:
    """The smallest object that drives the REAL AuthMixin.finish_manual_login.

    Only the collaborators the route calls out to are stubbed; the
    `ensure` decision under test is the production code's own.
    """

    def __init__(self, config):
        import logging
        self.config = config
        self.site_id = "manual-site"
        self.log = logging.getLogger("keepalive-test")
        self._manual_login_handle = object()
        self._manual_cookie_snapshot = []
        self.cookies_set = []

    def set_cookies(self, cl):
        self.cookies_set.append(cl)

    def _override_suppresses_persist(self):
        return True


def _drive_finish_manual_login(monkeypatch, config):
    """Run the real route; return the `ensure` list profile_sync was given."""
    from bulk_downloader import runner_auth, login, profile_sync
    cls = type("_H", (runner_auth.AuthMixin, _FinishManualLoginHarness), {})
    runner = cls(config)
    monkeypatch.setattr(
        login, "finalize_manual_login",
        lambda h: (True, "ok", [{"name": "sid", "value": "0" * 10}],
                   {"clicks": [], "inputs": []}))
    seen = []
    monkeypatch.setattr(
        profile_sync, "sync_manual_to_runtime",
        lambda site_id, ensure=None: seen.append(list(ensure or [])) or {"synced": {}})
    ok, _msg = runner.finish_manual_login()
    assert ok is True, "precondition: the manual login must have succeeded"
    assert len(seen) == 1, "precondition: profile_sync ran exactly once"
    return seen[0]


def test_manual_login_does_not_seed_a_keepalive_profile(monkeypatch):
    """Restoring True as the manual-login fallback makes this fail: a
    keepalive_0 profile would be seeded with a live session for a keeper
    the operator never enabled."""
    ensure = _drive_finish_manual_login(monkeypatch, {"password": "p"})
    assert ensure == ["main"], (
        "a config missing the key must not seed a keepalive profile")


def test_manual_login_seeds_keepalive_when_opted_in(monkeypatch):
    """Negative control for seam (e): explicit ON still seeds keepalive_0."""
    ensure = _drive_finish_manual_login(
        monkeypatch, {"password": "p", "keep_alive_enabled": True})
    assert ensure == ["main", "keepalive_0"]


# ── seam (f): the clone strip set ────────────────────────────────────

def test_clone_does_not_inherit_the_parents_opt_in(fresh_app, monkeypatch):
    """Removing `keep_alive_enabled` from the clone strip set makes this
    fail: a clone is an ADD, and an add must never arm a login."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as app_module
    r = fresh_app.post("/api/sites", json={"name": "parent-on"})
    assert r.status_code == 200
    sid = r.get_json()["id"]
    app_module.s_cfg[sid]["keep_alive_enabled"] = True
    app_module.s_cfg[sid]["password"] = "p"
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is True, (
        "precondition: the parent is explicitly opted IN")
    rc = fresh_app.post("/api/sites/%s/clone" % sid, json={})
    assert rc.status_code == 200, rc.get_data(as_text=True)[:300]
    new_sid = rc.get_json()["id"]
    assert new_sid != sid, "precondition: the clone is a distinct site"
    assert app_module.s_cfg[new_sid]["keep_alive_enabled"] is False, (
        "a clone must not inherit the parent's automatic login")
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is True, (
        "cloning must not disturb the parent's own setting")


# ── the operator's own switch, end to end ────────────────────────────

def test_toggle_on_then_off_is_persisted_and_respawns(fresh_app, monkeypatch):
    """Negative control across the whole cut: the explicit-OFF route is
    unchanged and the explicit-ON route still spawns exactly one keeper."""
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    from bulk_downloader import app as app_module
    from bulk_downloader import session_keeper
    r = fresh_app.post("/api/sites", json={"name": "toggle-site"})
    sid = r.get_json()["id"]
    app_module.s_cfg[sid]["password"] = "p"
    started = []
    monkeypatch.setattr(session_keeper, "start_keeper", lambda *a: started.append(a))
    on = fresh_app.post("/api/sites/%s/keep_alive_toggle" % sid, json={"enabled": True})
    assert on.status_code == 200 and on.get_json()["enabled"] is True
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is True
    assert len(started) == 1, "toggling ON must spawn exactly one keeper"
    off = fresh_app.post("/api/sites/%s/keep_alive_toggle" % sid, json={"enabled": False})
    assert off.status_code == 200
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is False


def test_explicit_opt_in_in_the_create_body_survives_the_backfill(fresh_app, monkeypatch):
    """Negative control for seam (a): the flipped DEFAULTS must not eat
    an operator-supplied True in the POST body."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as app_module
    r = fresh_app.post("/api/sites",
                       json={"name": "opt-in-at-create", "keep_alive_enabled": True})
    assert r.status_code == 200
    sid = r.get_json()["id"]
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is True, (
        "an explicit opt-in at create time must survive the DEFAULTS backfill")


# ── negative control (d): the mint paths must not EAT operator intent ─
#
# Three routes create a site, and all three backfill DEFAULTS with the
# same `if cfg.get(k) in ("", None)` test.  Flipping the DEFAULTS value
# is only safe if an explicit True in the body survives that backfill,
# so each route gets its own assertion.  `False` is not `in ("", None)`,
# which is also why an explicit False survives -- control (e) below.

def test_explicit_opt_in_through_sites_import_survives_the_backfill(fresh_app, monkeypatch):
    """POST /api/sites/import inlines its own create path (row 395), so
    the create-body control does not cover it."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as app_module
    r = fresh_app.post("/api/sites/import",
                       json={"name": "imported-opt-in", "keep_alive_enabled": True})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    body = r.get_json()
    assert body["ok"] is True, "precondition: the import was accepted"
    sid = body["id"]
    assert sid in app_module.s_cfg, "precondition: the import really created a site"
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is True, (
        "an explicit opt-in in a /api/sites/import body must survive the DEFAULTS backfill")


def test_explicit_opt_in_through_config_import_survives_the_backfill(fresh_app, monkeypatch):
    """POST /api/config/import normalizes every site in the envelope
    through its own copy of the same backfill."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as app_module
    before = set(app_module.s_cfg)
    r = fresh_app.post("/api/config/import",
                       json={"mode": "merge",
                             "sites": [{"name": "cfg-imported-opt-in",
                                        "keep_alive_enabled": True}]})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    minted = [sid for sid in app_module.s_cfg if sid not in before]
    assert len(minted) == 1, (
        "precondition: the import created exactly one new site, got %r" % (minted,))
    assert app_module.s_cfg[minted[0]]["keep_alive_enabled"] is True, (
        "an explicit opt-in in a /api/config/import body must survive the DEFAULTS backfill")


# ── negative control (e): an explicit OFF stays OFF ──────────────────

def test_explicit_off_stays_off_through_create_and_reload(fresh_app, monkeypatch, tmp_path):
    """The off-switch is persistent, not a checkbox someone remembers:
    an explicit False must survive both the create backfill and a boot
    from disk, and must not be re-minted into anything else."""
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader import app as app_module
    r = fresh_app.post("/api/sites",
                       json={"name": "explicit-off", "keep_alive_enabled": False})
    assert r.status_code == 200
    sid = r.get_json()["id"]
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is False, (
        "an explicit False must survive the DEFAULTS backfill")
    cfg_file = tmp_path / "sites_config.json"
    cfg_file.write_text(json.dumps(
        {sid: {"name": "explicit-off", "password": "p",
               "keep_alive_enabled": False}}), encoding="utf-8")
    monkeypatch.setattr(app_module, "SITES_FILE", cfg_file)
    monkeypatch.setattr(app_module, "s_cfg", {})
    app_module._load_sites_config()
    assert sid in app_module.s_cfg, "precondition: the site was loaded from disk"
    assert app_module.s_cfg[sid]["keep_alive_enabled"] is False, (
        "an explicit OFF on disk must still be OFF after a boot")
