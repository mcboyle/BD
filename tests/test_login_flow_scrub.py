"""login-flow-scrub r1 (O1789 dev-console-config F3): a typed credential
saved through /api/dev/login_flow_save must never reach disk in plaintext.

Two scrub tiers in macro_recorder:
  * password-named fields (selector token pass/password/passwd/pwd, plus the
    existing type=/autocomplete=password match and the explicit secret flag)
    -> VAULT_MARKER + secret:true; replay types the site's vault password.
  * secret/token-named fields -> SCRUB_MARKER, NOT vault-routed; replay
    refuses loudly instead of typing the vault password into a field that is
    not a password (which would itself leak the credential).
Look-alikes (#passport, #bypass, input[name=q]) are untouched. Every writer
of a flow file creates it 0600 regardless of the process umask.

S22/S23/S24 are the finder's scenario ids (ev/probes/dcc_probe.py).
"""
import json
import os
import secrets
import stat

import pytest

BD_GATE_SCOPE = "module"

VAULT_FIXTURE = "vault-fixture-value"  # fixture value, not a credential


def _sentinel():
    return "pw" + secrets.token_hex(12)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))
    monkeypatch.delenv("BD_DEV_MODE_DISABLE", raising=False)
    return tmp_path / "macros"


@pytest.fixture
def client(home):
    from bulk_downloader import app as bd_app
    return bd_app.app.test_client()


def _save(client, name, sel, text, **extra):
    r = client.post("/api/dev/login_flow_save", json={
        "site_id": "s1", "name": name, "actions": [
            {"kind": "type", "selector": "#user", "text": "u1"},
            dict({"kind": "type", "selector": sel, "text": text}, **extra),
            {"kind": "click", "selector": "button[type=submit]"}]})
    assert r.status_code == 200 and (r.get_json() or {}).get("ok"), (
        "login_flow_save refused the fixture flow: %s %r"
        % (r.status_code, r.get_data(as_text=True)[:200]))
    return r.get_json()["path"]


class _Loc:
    def __init__(self, page, sel):
        self._page, self._sel = page, sel
        self.first = self

    def fill(self, text, **kw):
        self._page.typed.append((self._sel, text))

    type = fill

    def click(self, **kw):
        pass

    def wait_for(self, **kw):
        pass


class _Page:
    def __init__(self):
        self.typed = []

    def locator(self, sel):
        return _Loc(self, sel)


def _replay(monkeypatch, path):
    from bulk_downloader import macro_recorder as mr
    monkeypatch.setattr(mr, "_resolve_secret_for", lambda site_id: VAULT_FIXTURE)
    page = _Page()
    macro = json.loads(open(path, encoding="utf-8").read())
    res = mr.replay_macro(page, macro, site_id="s1", strict=True)
    return res, page.typed


@pytest.mark.parametrize("sel", [
    "#password", "input[name=pass]", "#pwd", "#user_password",
    "#userPassword", "input[name='passwd']", "#PASSWORD"])
def test_s23_password_named_field_never_on_disk(client, sel):
    from bulk_downloader import macro_recorder as mr
    pw = _sentinel()
    path = _save(client, "f23", sel, pw)
    raw = open(path, encoding="utf-8").read()
    assert raw.count(pw) == 0, (
        "S23 LEAK: plaintext typed into %r persisted in the flow file" % sel)
    act = json.loads(raw)["actions"][1]
    assert act["text"] == mr.VAULT_MARKER and act.get("secret") is True, (
        "S23: %r not vault-routed: %r" % (sel, {k: act.get(k) for k in ("text", "secret")}))


@pytest.mark.parametrize("sel,extra", [
    ("input[type='password']", {}), ("#pw", {"secret": True})])
def test_s22_controls_stay_scrubbed(client, sel, extra):
    pw = _sentinel()
    path = _save(client, "f22", sel, pw, **extra)
    assert open(path, encoding="utf-8").read().count(pw) == 0, (
        "S22 control regressed: %r leaked" % sel)


@pytest.mark.parametrize("sel", [
    "#api_token", "input[name=client_secret]", "#totpToken"])
def test_secret_token_field_scrubbed_but_never_vault_resolved(client, monkeypatch, sel):
    from bulk_downloader import macro_recorder as mr
    pw = _sentinel()
    path = _save(client, "ftok", sel, pw)
    raw = open(path, encoding="utf-8").read()
    assert raw.count(pw) == 0, (
        "TOKEN LEAK: plaintext typed into %r persisted in the flow file" % sel)
    act = json.loads(raw)["actions"][1]
    assert act["text"] == mr.SCRUB_MARKER and not act.get("secret"), (
        "TOKEN TIER: %r must carry SCRUB_MARKER without the vault flag: %r"
        % (sel, {k: act.get(k) for k in ("text", "secret")}))
    res, typed = _replay(monkeypatch, path)
    assert not res["ok"] and res["failed_at"] == 1, (
        "TOKEN REPLAY: scrubbed %r replayed instead of refusing: %r" % (sel, res))
    assert all(t != VAULT_FIXTURE for _, t in typed), (
        "TOKEN REPLAY LEAK: vault password typed into non-password %r" % sel)
    assert all(t != mr.SCRUB_MARKER for _, t in typed), (
        "TOKEN REPLAY: scrub marker typed literally into %r" % sel)


def test_password_named_flow_replays_vault_value(client, monkeypatch):
    path = _save(client, "frep", "#password", _sentinel())
    res, typed = _replay(monkeypatch, path)
    assert res["ok"], res
    assert ("#password", VAULT_FIXTURE) in typed, (
        "VAULT REPLAY: #password did not receive the vault value: %r"
        % [s for s, _ in typed])


@pytest.mark.parametrize("sel", [
    "#passport", "#PASSPORT", "#bypass", "input[name='q']", "#secretary", "#tokenizer"])
def test_lookalike_fields_untouched(client, sel):
    path = _save(client, "flook", sel, "AB1234567")
    act = json.loads(open(path, encoding="utf-8").read())["actions"][1]
    assert act["text"] == "AB1234567" and not act.get("secret"), (
        "FALSE POSITIVE: non-credential field %r was scrubbed: %r" % (sel, act))


def test_s24_list_route_does_not_echo(client):
    pw = _sentinel()
    _save(client, "f24", "#password", pw)
    for q in ("/api/dev/login_flows", "/api/dev/login_flows?site_id=s1"):
        r = client.get(q)
        assert r.status_code == 200, (q, r.status_code)
        assert r.get_data(as_text=True).count(pw) == 0, "S24 LEAK via %s" % q


def _mode(p):
    return stat.S_IMODE(os.stat(p).st_mode)


def test_every_flow_writer_creates_0600(home):
    from bulk_downloader import macro_recorder as mr
    old = os.umask(0o022)
    try:
        r = mr.record_macro("s1", "fmode", [{"kind": "click", "selector": "a"}])
        assert r["ok"], r
        p = r["path"]
        assert _mode(p) == 0o600, "MODE record_macro: %o" % _mode(p)

        os.chmod(p, 0o644)
        stale_tmp = p + ".tmp"
        open(stale_tmp, "w").close()
        os.chmod(stale_tmp, 0o644)
        assert mr.mark_replay_result("s1", "fmode", True, "ok")
        assert _mode(p) == 0o600, "MODE mark_replay_result: %o" % _mode(p)

        legacy = home / "s1_legacy.json"
        legacy.write_text(json.dumps({"site_id": "s1", "name": "legacy", "actions": [
            {"kind": "type", "selector": "#password", "text": _sentinel()}]}))
        os.chmod(legacy, 0o644)
        res = mr.scrub_stored_passwords("s1")
        assert str(legacy) in res["files"], res
        assert _mode(legacy) == 0o600, "MODE scrub_stored_passwords: %o" % _mode(legacy)
    finally:
        os.umask(old)
