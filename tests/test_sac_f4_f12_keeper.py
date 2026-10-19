"""O1789 session-auth-capture F4 + F12-edge (session_keeper).

F4: after an auto-relogin wrote a fresh jar, the keeper's relaunch into its
existing keepalive profile seeded 0 cookies, so the profile kept the stale
session (heartbeat DEAD -> relogin again, spending the daily cap).
F12-edge: the httpx fallback's 'redirected to login: <Location>' reason (it
lands in session_history) carried the Location's query/fragment raw.

Drives the real SessionKeeper methods; only the browser / vault / transport
edges are stubbed. Loopback only: no network, no live app.
"""

import types

import httpx
import pytest

from bulk_downloader import cloak, cookies as ck
from bulk_downloader import session_keeper as sk
from bulk_downloader import ssrf_transport, vault_sync, download_egress

BD_GATE_SCOPE = "module"
OLD = [{"name": "sess", "value": "OLD-SESSION", "domain": ".site.test", "path": "/"}]
FRESH = [{"name": "sess", "value": "FRESH-RELOGIN", "domain": ".site.test", "path": "/"}]
ROTATED = [{"name": "sess", "value": "ROTATED-BY-SITE", "domain": ".site.test", "path": "/"}]
CFG = {"success_url": "https://site.test/account"}


class _Ctx:
    def __init__(self):
        self.added, self.jar = [], []
        self.pages = [types.SimpleNamespace(route=lambda *a, **k: None, close=lambda: None)]

    def add_cookies(self, c):
        self.added.append(list(c))
        self.jar = list(c)

    def cookies(self):
        return list(self.jar)

    def close(self):
        pass


def _browser(monkeypatch, tmp_path):
    """Stub the browser edge; each launch opens a new context and, like
    Chromium, leaves state files in the profile dir."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **k: None)
    opened = []

    def _open(user_data_dir, **_k):
        ctx = _Ctx()
        opened.append(ctx)
        (tmp_path / user_data_dir / "Local State").write_text("{}")
        return ctx, types.SimpleNamespace(stop=lambda: None), "stub"
    monkeypatch.setattr(cloak, "open_persistent_context", _open)
    return opened


def _jar():
    return ck.cookie_jar_path("siteA", 0, CFG)


def _relogin_writes(cookies):
    def _cb(site_id, account_idx, config):
        ck.save_cookies_to_file(_jar(), cookies)
        return True, "relogin ok"
    return _cb


def _values(ctx):
    return [c["value"] for batch in ctx.added for c in batch]


def _first_launch_and_persist(monkeypatch, tmp_path, callback):
    opened = _browser(monkeypatch, tmp_path)
    ck.save_cookies_to_file(_jar(), OLD)
    k = sk.SessionKeeper("siteA", 0, dict(CFG), callback)
    assert k._launch_browser() is True, k._last_launch_failure
    assert _values(opened[0]) == ["OLD-SESSION"]
    k._persist_cookies()
    return k, opened


def test_f4_relaunch_after_relogin_reseeds_profile(monkeypatch, tmp_path):
    k, opened = _first_launch_and_persist(monkeypatch, tmp_path, _relogin_writes(FRESH))
    ok, _detail = k._auto_relogin()
    assert ok
    assert k._launch_browser() is True, k._last_launch_failure
    assert _values(opened[1]) == ["FRESH-RELOGIN"], (
        f"SAC-F4: relaunch after relogin seeded {_values(opened[1])} into the existing "
        f"keepalive profile; jar holds FRESH-RELOGIN (profile keeps the stale session)")


def test_f4_restarted_keeper_reseeds_jar_written_while_down(monkeypatch, tmp_path):
    _k, opened = _first_launch_and_persist(monkeypatch, tmp_path, lambda *a: (False, "x"))
    ck.save_cookies_to_file(_jar(), FRESH)  # e.g. a manual login while the keeper was down
    k2 = sk.SessionKeeper("siteA", 0, dict(CFG), lambda *a: (False, "x"))
    assert k2._launch_browser() is True, k2._last_launch_failure
    assert _values(opened[1]) == ["FRESH-RELOGIN"], (
        f"SAC-F4: restarted keeper seeded {_values(opened[1])}; jar rewritten since persist")


def test_f4_control_plain_relaunch_keeps_profile_cookies(monkeypatch, tmp_path):
    # v3.64.2 design: the jar unchanged since the keeper's own persist ->
    # the persistent profile stays the source of truth (no re-inject).
    k, opened = _first_launch_and_persist(monkeypatch, tmp_path, lambda *a: (False, "x"))
    assert k._launch_browser() is True
    assert opened[1].added == []


def test_f4_control_profile_without_record_is_not_reseeded(monkeypatch, tmp_path):
    # A profile from before the sync record adopts the jar as its baseline.
    opened = _browser(monkeypatch, tmp_path)
    ck.save_cookies_to_file(_jar(), OLD)
    prof = tmp_path / "profiles" / "siteA" / "keepalive_0"
    prof.mkdir(parents=True)
    (prof / "Cookies").write_text("browser state")
    k = sk.SessionKeeper("siteA", 0, dict(CFG), lambda *a: (False, "x"))
    assert k._launch_browser() is True
    assert opened[0].added == []


def _httpx_redirect(monkeypatch, tmp_path, location):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    ck.save_cookies_to_file(_jar(), OLD)
    monkeypatch.setattr(
        ssrf_transport, "guarded_transport",
        lambda *a, **k: httpx.MockTransport(
            lambda req: httpx.Response(302, headers={"location": location})))
    monkeypatch.setattr(download_egress, "effective_download_proxy", lambda *a, **k: None)
    return sk.SessionKeeper("siteA", 0, dict(CFG), lambda *a: (False, "x"))._heartbeat_httpx_fallback()


def test_f12_edge_httpx_redirect_withholds_query_and_fragment(monkeypatch, tmp_path):
    verdict, detail = _httpx_redirect(
        monkeypatch, tmp_path,
        "https://u:PW-SECRET@site.test/login?code=CODE-SECRET#access_token=FRAG-SECRET")
    assert verdict is sk.DEAD
    for secret in ("PW-SECRET", "CODE-SECRET", "FRAG-SECRET"):
        assert secret not in detail, f"SAC-F12-edge: httpx redirect reason carries {secret}: {detail}"


def test_f12_edge_relative_location_withholds_query(monkeypatch, tmp_path):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, "/signin?sso=REL-SECRET")
    assert verdict is sk.DEAD
    assert "REL-SECRET" not in detail, f"SAC-F12-edge: relative Location kept raw: {detail}"


def test_f12_edge_control_reason_still_names_login_target(monkeypatch, tmp_path):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, "https://site.test/login")
    assert verdict is sk.DEAD
    assert detail == "redirected to login: https://site.test/login"


def test_f4_first_launch_without_jar_reseeds_after_relogin(monkeypatch, tmp_path):
    # LENS-FIXED F4: a first launch that seeded nothing (no jar yet) must not
    # leave the profile looking like a pre-record one that adopts the jar.
    opened = _browser(monkeypatch, tmp_path)
    k = sk.SessionKeeper("siteA", 0, dict(CFG), _relogin_writes(FRESH))
    assert k._launch_browser() is True
    assert opened[0].added == []
    ok, _detail = k._auto_relogin()
    assert ok
    assert k._launch_browser() is True
    assert _values(opened[1]) == ["FRESH-RELOGIN"], (
        f"SAC-F4: first launch had no jar; relaunch after relogin seeded {_values(opened[1])}")


def test_f4_persist_then_relaunch_keeps_profile_cookies(monkeypatch, tmp_path):
    # r2 A4: the site rotated the session in the browser; the keeper's own
    # persist wrote it to the jar, so the relaunch must not re-inject it.
    k, opened = _first_launch_and_persist(monkeypatch, tmp_path, lambda *a: (False, "x"))
    k._ctx.jar = list(ROTATED)
    k._persist_cookies()
    assert [c["value"] for c in ck.load_cookies_from_file(_jar())] == ["ROTATED-BY-SITE"]
    assert k._launch_browser() is True
    assert opened[1].added == [], (
        f"SAC-F4-A4: relaunch after the keeper's own persist re-seeded {_values(opened[1])}")


def test_f4_jar_written_during_persist_is_reseeded(monkeypatch, tmp_path):
    # r2 A3: a writer landing between the persist's save and its record must
    # not be taken as synced (the record names the bytes this save wrote).
    k, opened = _first_launch_and_persist(monkeypatch, tmp_path, lambda *a: (False, "x"))
    real_save = ck.save_cookies_to_file

    def _save_then_race(path, cookies, **kw):
        out = real_save(path, cookies, **kw)
        real_save(_jar(), FRESH)
        return out
    monkeypatch.setattr(ck, "save_cookies_to_file", _save_then_race)
    k._ctx.jar = list(ROTATED)
    k._persist_cookies()
    assert k._launch_browser() is True
    assert _values(opened[1]) == ["FRESH-RELOGIN"], (
        f"SAC-F4-A3: jar rewritten during persist was marked synced; relaunch seeded {_values(opened[1])}")


@pytest.mark.parametrize("location", [
    "//bob:PW-SECRET@site.test/login",
    "\\\\bob:PW-SECRET@site.test/login",
    "/\\bob:PW-SECRET@site.test/login",
])
def test_f12_edge_scheme_relative_and_backslash_userinfo_masked(monkeypatch, tmp_path, location):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert "PW-SECRET" not in detail and "bob" not in detail, (
        f"SAC-F12-F1: authority userinfo kept in redirect reason: {detail}")
    assert detail == "redirected to login: //<REDACTED>@site.test/login"


def test_f12_edge_control_path_at_sign_kept(monkeypatch, tmp_path):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, "/login/@me")
    assert verdict is sk.DEAD
    assert detail == "redirected to login: /login/@me"


# r3 F2: the r1 SACFS-C and r2 SAC2S-B lens probe cases. Every form a browser
# (WHATWG) may resolve to an authority carries no userinfo into the reason.
_AUTHORITY_LEAKS = [
    ("sr", "//bob:PW-SECRET@site.test/login"),
    ("bs2", "\\\\bob:PW-SECRET@site.test/login"),
    ("fsbs", "/\\bob:PW-SECRET@site.test/login"),
    ("bsfs", "\\/bob:PW-SECRET@site.test/login"),
    ("httpbs", "http:\\\\bob:PW-SECRET@site.test/login"),
    ("HTTPup", "HTTP://bob:PW-SECRET@site.test/login"),
    ("triple", "///bob:PW-SECRET@site.test/login"),
    ("multiat", "//bob@x:PW-SECRET@site.test/login"),
    ("colonpw", "//bob:PW:SECRET-X@site.test/login"),
    ("ipv6", "//bob:PW-SECRET@[::1]/login"),
    ("enc40", "//bob%40x:PW-SECRET@site.test/login"),
    ("http1slash", "http:/bob:PW-SECRET@site.test/login"),
    ("http0slash", "http:bob:PW-SECRET@site.test/login"),
    ("https1bs", "https:\\bob:PW-SECRET@site.test/login"),
    ("tabsr", "/\t/bob:PW-SECRET@site.test/login"),
    ("pct2f", "%2F%2Fbob:PW-SECRET@site.test/login"),
    ("wsprefix", " //bob:PW-SECRET@site.test/login"),
    # r1 SACFS-C (its path-borne A1 cases enc_q/path_token/semicolon are owed)
    ("r1_abs_query", "https://site.test/login?token=PW-SECRET"),
    ("r1_abs_frag", "https://site.test/login#access_token=PW-SECRET"),
    ("r1_abs_userinfo", "https://bob:PW-SECRET@site.test/login"),
    ("r1_upper_scheme_userinfo", "HTTPS://bob:PW-SECRET@site.test/LOGIN?x=PW-SECRET"),
    ("r1_relative", "/login?sso=PW-SECRET"),
    ("r1_scheme_rel_query", "//site.test/login?x=PW-SECRET"),
    ("r1_scheme_rel_userinfo", "//bob:PW-SECRET@site.test/login"),
    ("r1_backslash_userinfo", "https:\\\\bob:PW-SECRET@site.test/login"),
    ("r1_backslash_rel_userinfo", "\\\\bob:PW-SECRET@site.test/login"),
]


@pytest.mark.parametrize("name,location", _AUTHORITY_LEAKS, ids=[n for n, _ in _AUTHORITY_LEAKS])
def test_f12_edge_authority_userinfo_masked_every_form(monkeypatch, tmp_path, name, location):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert "PW-SECRET" not in detail and "SECRET-X" not in detail, (
        f"SAC-F12-F2: {name} authority userinfo kept in redirect reason: {detail}")


@pytest.mark.parametrize("name,location,expected", [
    ("pathat", "/login/a@b", "redirected to login: /login/a@b"),
    ("nextat", "/login?next=a@b", "redirected to login: /login?<REDACTED>"),
    ("abs", "https://site.test/login", "redirected to login: https://site.test/login"),
    ("srnoui", "//site.test/login", "redirected to login: //site.test/login"),
], ids=["pathat", "nextat", "abs", "srnoui"])
def test_f12_edge_control_no_over_masking(monkeypatch, tmp_path, name, location, expected):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert detail == expected, f"SAC-F12-F2: {name} over-masked: {detail}"


# r4 F3: the r3 SAC3C-A probe cases. The authority ends at the first / \ ? or #
# (WHATWG), so an "@" in a query or fragment opens no userinfo: the host stays
# shown and the query/fragment is still withheld.
_QUERY_AT_LEAKS = [
    ("q_hint_code", "https://sso.site.test?action=login&login_hint=bob@corp.test&code=CODE-SECRET",
     "https://sso.site.test?<REDACTED>"),
    ("q_sr_tok", "//site.test?next=login&tok=a@CODE-SECRET", "//site.test?<REDACTED>"),
    ("q_user_pass", "https://site.test?page=login&user=bob@x.test&pass=PW-SECRET",
     "https://site.test?<REDACTED>"),
    ("f_token", "https://site.test#login&access_token=a@CODE-SECRET", "https://site.test?<REDACTED>"),
    ("q_bs", "\\\\site.test?login&e=a@CODE-SECRET", "//site.test?<REDACTED>"),
]


@pytest.mark.parametrize("name,location,expected", _QUERY_AT_LEAKS, ids=[n for n, _, _ in _QUERY_AT_LEAKS])
def test_f12_edge_query_or_fragment_at_sign_withheld(monkeypatch, tmp_path, name, location, expected):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert "SECRET" not in detail, f"SAC-F12-F3: {name} query/fragment shown raw: {detail}"
    assert detail == "redirected to login: " + expected, f"SAC-F12-F3: {name} host lost: {detail}"


# The probe's "site.test:443/login" and "  https://...\t" controls are not
# here: httpx rejects those Location headers before this hunk runs, on any tree.
@pytest.mark.parametrize("location,expected", [
    ("/login/a@b", "/login/a@b"),
    ("//site.test/login", "//site.test/login"),
    ("https://site.test/login", "https://site.test/login"),
    ("/signin", "/signin"),
    ("login", "login"),
])
def test_f12_edge_control_query_at_fix_keeps_plain_forms(monkeypatch, tmp_path, location, expected):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert detail == "redirected to login: " + expected, f"SAC-F12-F3: {location!r} over-masked: {detail}"


# r4 AMEND (SAC3S-C F3): "\" ends the authority only for a special scheme (http
# https ws wss ftp file, or none: the base is https); a non-special scheme keeps
# a literal "\" inside its userinfo (WHATWG).
@pytest.mark.parametrize("location", [
    "foo://bob:SEC\\RETPW@site.test/login",
    "git://bob\\x:SECRETPW@site.test/login",
    "ssh://bob:SEC\\RETPW@site.test/login",
    "h+t.t-p://bob:SEC\\RETPW@site.test/login",
])
def test_f12_edge_nonspecial_scheme_backslash_userinfo_masked(monkeypatch, tmp_path, location):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert "RETPW" not in detail, f"SAC-F12-F3S: non-special userinfo kept: {detail}"


@pytest.mark.parametrize("location,expected", [
    ("//site.test\\login@x", "//site.test\\login@x"),
    ("ws://site.test\\login@x", "ws://site.test\\login@x"),
    ("foo://site.test?login&e=a@CODE-SECRET", "foo://site.test?<REDACTED>"),
    ("foo://site.test/login", "foo://site.test/login"),
])
def test_f12_edge_control_scheme_aware_authority_end(monkeypatch, tmp_path, location, expected):
    verdict, detail = _httpx_redirect(monkeypatch, tmp_path, location)
    assert verdict is sk.DEAD
    assert detail == "redirected to login: " + expected, f"SAC-F12-F3S: {location!r} over-masked: {detail}"
