"""O1807 R2 (SECURITY, TECH_DEBT_REPORT #10): the cockpit web-shell guard
(tools.cockpit_console._shell_request_trusted) returned True for any loopback
remote_addr before it looked at Host / Origin / Sec-Fetch-Site. A browser page on
another origin (or a DNS-rebound name pointing at 127.0.0.1, or a loopback reverse
proxy fronting remote traffic) therefore reached /cockpit/api/shell/* -- arbitrary
commands -- with no same-origin proof. Cross-site signals and the Host name are now
checked first; loopback is trusted only for a loopback Host.
r2 (codex-2 REFUTE): loopback alone is not trust either -- it needs an Origin that
is exactly scheme://Host (or, for GET/HEAD, Sec-Fetch-Site: same-origin); a
missing, cross-scheme or path-carrying Origin falls to session + CSRF.

tools.cockpit_shell.shell_open is stubbed: a request that reached the real handler
would spawn a pty.
"""
import pytest

BD_GATE_SCOPE = "module"

_LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.fixture
def opened(monkeypatch):
    from tools import cockpit_shell as sh
    calls = []

    def _stub_open():
        calls.append(1)
        return {"ok": True, "session": "stub-o1807"}

    monkeypatch.setattr(sh, "shell_open", _stub_open)
    return calls


def _client():
    from flask import Flask
    from tools.cockpit_console import bp
    app = Flask(__name__)
    app.register_blueprint(bp)
    return app.test_client()


def _post(headers):
    return _client().post("/cockpit/api/shell/open", environ_base=_LOOPBACK,
                          headers=headers, json={})


@pytest.mark.parametrize("why,headers", [
    ("cross-site Origin", {"Host": "127.0.0.1:5555", "Origin": "http://evil.example"}),
    ("cross-site Sec-Fetch-Site", {"Host": "127.0.0.1:5555", "Sec-Fetch-Site": "cross-site"}),
    ("same-site Sec-Fetch-Site", {"Host": "localhost:5555", "Sec-Fetch-Site": "same-site"}),
    ("rebound Host, matching Origin", {"Host": "evil.example:5555",
                                       "Origin": "http://evil.example:5555"}),
    ("proxied public Host", {"Host": "bd.example.org"}),
    # r2 (codex-2 REFUTE): no same-origin proof on a loopback POST.
    ("no browser headers", {}),
    ("standalone 127.0.0.1 Host, no Origin", {"Host": "127.0.0.1:5555"}),
    ("POST with only Sec-Fetch-Site same-origin", {"Host": "[::1]:5555",
                                                   "Sec-Fetch-Site": "same-origin"}),
    ("cross-scheme Origin", {"Host": "localhost:5555", "Origin": "https://localhost:5555"}),
    ("Origin with a path", {"Host": "localhost:5555", "Origin": "http://localhost:5555/"}),
    ("null Origin", {"Host": "localhost:5555", "Origin": "null"}),
])
def test_loopback_does_not_bypass_origin_checks(opened, why, headers):
    r = _post(headers)
    assert r.status_code == 403, (
        f"O1807-R2 LOOPBACK-BYPASS ({why}): loopback request with untrusted "
        f"Host/Origin reached the shell (status {r.status_code}, "
        f"shell_open calls {len(opened)})")
    assert opened == []


@pytest.mark.parametrize("why,headers", [
    ("standalone 127.0.0.1 Origin", {"Host": "127.0.0.1:5555", "Origin": "http://127.0.0.1:5555"}),
    ("same-origin Origin", {"Host": "localhost:5555", "Origin": "http://localhost:5555"}),
])
def test_loopback_same_origin_still_opens_shell(opened, why, headers):
    # Positive control: the standalone cockpit's own page (exact Origin) still opens.
    r = _post(headers)
    assert r.status_code == 200, f"O1807-R2 positive control ({why}): {r.get_data(as_text=True)}"
    assert opened == [1]


def test_loopback_get_needs_same_origin_proof():
    # GET/HEAD: a same-origin browser GET sends no Origin, but Sec-Fetch-Site:
    # same-origin (loopback is a secure context). Header-less GET is refused.
    c = _client()
    url = "/cockpit/api/shell/status"
    ok = c.get(url, environ_base=_LOOPBACK,
               headers={"Host": "localhost:5555", "Sec-Fetch-Site": "same-origin"})
    bare = c.get(url, environ_base=_LOOPBACK, headers={"Host": "localhost:5555"})
    assert ok.status_code == 200, f"O1807-R2 GET positive control: {ok.status_code}"
    assert bare.status_code == 403, (
        f"O1807-R2 LOOPBACK-BYPASS (header-less GET): status {bare.status_code}")
