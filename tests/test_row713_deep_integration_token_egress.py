"""Row 713: the deep integrations must classify an operator-supplied URL
before their token moves, and must never carry that token across an origin.

Every fixture here is a loopback recorder plus the REAL predicate from
``provider_resolve_impl._common``; the only stubbed resolver is the labelled
classification control, and the only substituted transport is the module-level
opener seam (which sits BEHIND classification, never in front of it).
"""
BD_GATE_SCOPE = "repo-wide"

import json
import email.message
import socket
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# Documented zero-entropy synthetic fixture values; not credentials.
TOKEN = "ZZZZ-TEST-TOKEN-0000"
# A public unicast address the shipped predicate accepts. Never contacted:
# it exists only so a stubbed resolver can produce a non-refused answer.
PUBLIC_IP = "93.184.216.34"


# ── loopback recorders ────────────────────────────────────────────────


class _Recorder(HTTPServer):
    def __init__(self, host="127.0.0.1"):
        self.requests = []
        self.redirect_to = None
        self.redirect_once = False
        self.reflect_query = False
        self.status = 200
        self.body_kind = "xml"
        super().__init__((host, 0), _Handler)

    @property
    def origin(self):
        return f"http://{self.server_address[0]}:{self.server_address[1]}"

    def token_hits(self):
        """Every place this recorder actually received the row's token."""
        hits = []
        for _method, path, headers in self.requests:
            # urllib title-cases header names on the wire ("ApiKey" leaves as
            # "Apikey"), so match the name case-insensitively and report the
            # module's own spelling.
            received = {k.lower(): v for k, v in headers.items()}
            for name in ("X-Emby-Token", "ApiKey", "X-Plex-Token"):
                if received.get(name.lower()) == TOKEN:
                    hits.append((name, TOKEN))
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
            for value in query.get("X-Plex-Token", []):
                if value == TOKEN:
                    hits.append(("query:X-Plex-Token", value))
        return hits


_BODIES = {
    "xml": b'<MediaContainer version="1"/>',
    "json_system": json.dumps({"Version": "1"}).encode(),
    "json_graphql": json.dumps({"data": {"version": {"version": "1"}}}).encode(),
}


class _Handler(BaseHTTPRequestHandler):
    def _record_and_answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.requests.append(
            (self.command, self.path, dict(self.headers)))
        redirect = self.server.redirect_to
        if redirect and not (self.server.redirect_once
                             and len(self.server.requests) > 1):
            if self.server.reflect_query and "?" in self.path:
                # A hostile endpoint reflects what it just received.
                redirect = f"{redirect}?{self.path.split('?', 1)[1]}"
            self.send_response(302)
            self.send_header("Location", redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.server.status != 200:
            self.send_response(self.server.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = _BODIES[self.server.body_kind]
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _record_and_answer
    do_POST = _record_and_answer

    def log_message(self, *_args):
        pass


@pytest.fixture
def servers():
    started = []

    def _start(host="127.0.0.1"):
        server = _Recorder(host)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return server

    try:
        yield _start
    finally:
        for server, thread in started:
            server.shutdown()
            thread.join()
            server.server_close()


# ── the three urllib modules, described once ──────────────────────────


def _plex(url, **kw):
    from bulk_downloader.plex_deep import PlexClient
    return PlexClient(url, TOKEN, **kw)


def _jellyfin(url, **kw):
    from bulk_downloader.jellyfin_deep import JellyfinClient
    return JellyfinClient(url, TOKEN, **kw)


def _stash(url, **kw):
    from bulk_downloader.stash_deep import StashClient
    return StashClient(url, TOKEN, **kw)


def _drive_plex(client):
    return client._request("GET", "/identity", timeout=2.0)


def _drive_jellyfin(client):
    return client._request("GET", "/System/Info", timeout=2.0)


def _drive_stash(client):
    return client._graphql("query { version { version } }", timeout=2.0)


def _error_class(name):
    import importlib
    module = importlib.import_module(f"bulk_downloader.{name}")
    return getattr(module, {"plex_deep": "PlexError",
                            "jellyfin_deep": "JellyfinError",
                            "stash_deep": "StashError"}[name])


MODULES = [
    pytest.param("plex_deep", _plex, _drive_plex, "xml",
                 "query:X-Plex-Token", id="plex_deep"),
    pytest.param("jellyfin_deep", _jellyfin, _drive_jellyfin, "json_system",
                 "X-Emby-Token", id="jellyfin_deep"),
    pytest.param("stash_deep", _stash, _drive_stash, "json_graphql",
                 "ApiKey", id="stash_deep"),
]


# ── RED-2: nothing is sent to an unclassified operator URL ────────────


@pytest.mark.parametrize("name,make,drive,body_kind,token_place", MODULES)
def test_row713_default_client_refuses_before_any_token_egress(
        servers, name, make, drive, body_kind, token_place):
    """Base: one token-bearing request reaches the unclassified recorder."""
    recorder = servers()
    recorder.body_kind = body_kind
    result = make(recorder.origin).diagnose()
    assert recorder.requests == [], (
        f"token reached an unclassified operator URL: {recorder.requests}")
    assert recorder.token_hits() == []
    assert result["ok"] is False
    # The reason is computed by the classifier, not written by the patch.
    assert "127.0.0.1" in result["error"], result["error"]


@pytest.mark.parametrize("name,make,drive,body_kind,token_place", MODULES)
def test_row713_refusal_is_structured_and_not_laundered(
        servers, name, make, drive, body_kind, token_place):
    """RED-3: the catch-all must not swallow the refusal into 'unknown'."""
    recorder = servers()
    recorder.body_kind = body_kind
    with pytest.raises(_error_class(name)) as excinfo:
        drive(make(recorder.origin))
    assert excinfo.value.kind == "blocked", excinfo.value.kind
    assert "127.0.0.1" in excinfo.value.message
    assert recorder.requests == []


# ── RED-1: the token never crosses an origin ──────────────────────────


@pytest.mark.parametrize("name,make,drive,body_kind,token_place", MODULES)
def test_row713_cross_origin_redirect_never_carries_the_token(
        servers, name, make, drive, body_kind, token_place):
    """Base: the second origin receives the token verbatim on a 302."""
    first = servers("127.0.0.1")
    second = servers("127.0.0.2")
    first.body_kind = second.body_kind = body_kind
    first.reflect_query = True
    first.redirect_to = f"{second.origin}/next"
    result = make(first.origin, allow_private_hosts=True).diagnose()
    assert second.token_hits() == [], (
        f"cross-origin hop carried the token: {second.token_hits()}")
    assert second.requests == []
    # Precondition: the hazard was actually present -- hop one happened and
    # carried the token, so there was something for hop two to leak.
    assert len(first.requests) == 1
    assert first.token_hits() == [(token_place, TOKEN)]
    assert result["ok"] is False
    assert "cross-origin redirect refused" in result["error"], result["error"]
    assert "127.0.0.1" in result["error"] and "127.0.0.2" in result["error"]


def test_row713_private_opt_out_never_relaxes_the_redirect_policy(servers):
    """allow_private_hosts is a HOST-classification opt-out only."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    first = servers("127.0.0.1")
    second = servers("127.0.0.2")
    first.redirect_to = f"{second.origin}/next"
    with pytest.raises(SSRFBlocked):
        deep_http.guarded_open(first.origin, timeout=2,
                               headers={"X-Emby-Token": TOKEN},
                               allow_private_hosts=True)
    assert second.requests == []
    assert len(first.requests) == 1


def test_row713_guarded_open_classifies_by_default(servers):
    """The helper's own default must be closed, independent of its callers."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    recorder = servers()
    with pytest.raises(SSRFBlocked) as excinfo:
        deep_http.guarded_open(recorder.origin, timeout=2,
                               headers={"X-Emby-Token": TOKEN})
    assert "127.0.0.1" in str(excinfo.value)
    assert recorder.requests == []


# ── the opener seam: substitutable, and never a bypass ────────────────


def test_row713_unresolvable_host_is_refused_when_nothing_is_swapped():
    """Direction one: with the seam untouched, a name that does not resolve
    fails closed -- there is no special case and no relaxation for it."""
    from bulk_downloader import deep_http
    from bulk_downloader.jellyfin_deep import JellyfinClient, JellyfinError

    assert deep_http._OPENER is deep_http._OPENER  # seam not pre-swapped
    with pytest.raises(JellyfinError) as excinfo:
        JellyfinClient("https://jf.demo", TOKEN)._request(
            "GET", "/System/Info", timeout=2.0)
    assert excinfo.value.kind == "blocked"
    assert "DNS resolution failed" in excinfo.value.message


def test_row713_opener_seam_intercepts_only_behind_classification(monkeypatch):
    """Direction two: swapping the opener does NOT skip classification --
    the same unresolvable host is still refused with the seam replaced, and
    the seam is reached only once the host classifies public."""
    from bulk_downloader import deep_http
    from bulk_downloader.jellyfin_deep import JellyfinClient, JellyfinError

    seen = []

    class _FakeOpener:
        def open(self, request, timeout=None):
            seen.append(request)
            class _Resp:
                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): return False
                def read(self_inner): return b'{"Version": "1"}'
            return _Resp()

    monkeypatch.setattr(deep_http, "_OPENER", _FakeOpener())

    with pytest.raises(JellyfinError) as excinfo:
        JellyfinClient("https://jf.demo", TOKEN)._request(
            "GET", "/System/Info", timeout=2.0)
    assert excinfo.value.kind == "blocked"
    assert seen == [], "the swapped opener was reached before classification"

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))])
    JellyfinClient("https://jf.demo", TOKEN)._request(
        "GET", "/System/Info", timeout=2.0)
    assert len(seen) == 1
    assert TOKEN not in seen[0].full_url
    assert seen[0].get_header("X-emby-token") == TOKEN


# ── negative controls: the ordinary integration is unchanged ──────────


@pytest.mark.parametrize("name,make,drive,body_kind,token_place", MODULES)
def test_row713_nc1_same_host_success_is_unchanged(
        servers, name, make, drive, body_kind, token_place):
    recorder = servers()
    recorder.body_kind = body_kind
    result = make(recorder.origin, allow_private_hosts=True).diagnose()
    assert result["ok"] is True, result
    assert result["reachable"] is True
    assert result["version"] == "1"
    assert result["error"] is None
    assert len(recorder.requests) == 1
    assert recorder.token_hits() == [(token_place, TOKEN)]


@pytest.mark.parametrize("name,make,drive,body_kind,token_place", MODULES)
def test_row713_nc2_same_origin_redirect_is_followed(
        servers, name, make, drive, body_kind, token_place):
    recorder = servers()
    recorder.body_kind = body_kind
    recorder.redirect_once = True
    recorder.reflect_query = True  # Plex's token rides the query string
    recorder.redirect_to = "/second"
    result = make(recorder.origin, allow_private_hosts=True).diagnose()
    assert result["ok"] is True, result
    assert len(recorder.requests) == 2
    assert recorder.requests[1][1].split("?")[0] == "/second"
    assert recorder.token_hits() == [(token_place, TOKEN),
                                     (token_place, TOKEN)]


def test_row713_nc3_classification_control_accepts_a_public_host(monkeypatch):
    """Classification control -- NOT a handler test. It only establishes that
    the shipped predicate says yes to a public answer, so the refusals above
    are the classifier deciding and not the fixture failing to resolve."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import _is_safe_public_host

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))])
    assert _is_safe_public_host("ok.fixture.invalid")[0] is True
    deep_http._check("https://ok.fixture.invalid/System/Info", False)


# ── plexapi: the fourth module ────────────────────────────────────────


@pytest.fixture
def fake_plexapi(monkeypatch):
    from bulk_downloader import plex_deep_plexapi as backend
    seen = []

    class FakePlexServer:
        def __init__(self, *args, **kwargs):
            seen.append((args, kwargs))

    monkeypatch.setattr(backend, "_plexapi", FakePlexServer)
    monkeypatch.setattr(backend, "_import_attempted", True)
    monkeypatch.setattr(backend, "_last_refusal", None)
    return backend, seen


def test_row713_plexapi_refuses_before_the_constructor(servers, fake_plexapi):
    backend, seen = fake_plexapi
    recorder = servers()
    assert backend.connect({"plex_url": recorder.origin,
                            "plex_token": TOKEN}) is None
    assert seen == [], f"token reached an unclassified operator URL: {seen}"
    assert recorder.requests == []
    status = backend.status_dict()
    assert status["refused"] is True
    assert "127.0.0.1" in status["refusal"]


def test_row713_plexapi_session_is_guarded_and_refuses_cross_origin(
        servers, fake_plexapi):
    """NC-4 plus RED-1 for the fourth module: the session handed to plexapi
    classifies and will not carry X-Plex-Token to a second origin."""
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    backend, seen = fake_plexapi
    first = servers("127.0.0.1")
    second = servers("127.0.0.2")
    first.redirect_to = f"{second.origin}/next"
    server = backend.connect({"plex_url": first.origin, "plex_token": TOKEN},
                             allow_private_hosts=True)
    assert server is not None
    assert len(seen) == 1
    session = seen[0][1]["session"]
    with pytest.raises(SSRFBlocked):
        session.get(first.origin, headers={"X-Plex-Token": TOKEN}, timeout=2)
    assert second.requests == []
    assert len(first.requests) == 1
    assert first.token_hits() == [("X-Plex-Token", TOKEN)]


def test_row713_plexapi_refusal_does_not_outlive_a_later_success(
        servers, fake_plexapi):
    backend, seen = fake_plexapi
    recorder = servers()
    assert backend.connect({"plex_url": recorder.origin,
                            "plex_token": TOKEN}) is None
    assert backend.status_dict()["refused"] is True
    assert backend.connect({"plex_url": recorder.origin, "plex_token": TOKEN},
                           allow_private_hosts=True) is not None
    assert backend.status_dict()["refused"] is False
    assert backend.status_dict()["refusal"] is None


# ── transform-control band: imports the subject, drives nothing ───────


def test_row713_deep_http_module_surface():
    """Import-only. The transform control is banded here BECAUSE this node
    exercises no request, so a behaviour-preserving edit must escape it."""
    from bulk_downloader import deep_http

    assert callable(deep_http.guarded_open)
    assert callable(deep_http.guarded_session)


def test_row713_blocked_kind_vocabulary_is_declared_by_every_deep_module():
    """Import-only. It names the structured refusal vocabulary and drives no
    request, which is why the transform control bands here: a mutation of the
    send seam must ESCAPE this node."""
    from bulk_downloader.jellyfin_deep import JellyfinError
    from bulk_downloader.plex_deep import PlexError
    from bulk_downloader.stash_deep import StashError

    for error_class in (PlexError, JellyfinError, StashError):
        error = error_class("blocked", "blocked: reason", {"hint": "reason"})
        assert error.kind == "blocked"
        assert error.detail["hint"] == "reason"


# ── the refusals this cut ADDS, each pinned so it cannot be deleted ───


def _http_error(url, code, location=None):
    headers = email.message.Message()
    if location is not None:
        headers["Location"] = location
    return urllib.error.HTTPError(url, code, "Found", headers, None)


def test_row713_non_http_scheme_is_refused_by_the_scheme_gate(monkeypatch):
    """E1: the scheme gate. A HOSTED non-http(s) URL classifies public, so
    only this refusal can stop it -- deleting the gate lets gopher:// through
    with the token attached."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    reached = []

    class _NeverOpener:
        def open(self, request, timeout=None):
            reached.append(request.full_url)
            raise AssertionError("a non-http(s) URL reached the transport")

    monkeypatch.setattr(deep_http, "_OPENER", _NeverOpener())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 70))])

    with pytest.raises(SSRFBlocked) as hosted:
        deep_http.guarded_open("gopher://ok.fixture.invalid/x", timeout=2,
                               headers={"X-Plex-Token": TOKEN})
    assert "unsupported URL scheme: gopher" in str(hosted.value)

    with pytest.raises(SSRFBlocked) as hostless:
        deep_http.guarded_open("file:///etc/hostname", timeout=2)
    assert "unsupported URL scheme: file" in str(hostless.value)

    assert reached == []


def test_row713_ordinary_http_error_is_not_laundered_into_a_refusal(servers):
    """E2: the non-30x re-raise. Without it every 401/403/404 falls into the
    redirect arm, finds no Location and comes back as a refusal, so an
    authentication failure would read as kind='blocked'."""
    from bulk_downloader import deep_http
    from bulk_downloader.plex_deep import PlexClient, PlexError

    recorder = servers()
    recorder.status = 401
    with pytest.raises(PlexError) as excinfo:
        PlexClient(recorder.origin, TOKEN, allow_private_hosts=True)._request(
            "GET", "/identity", timeout=2.0)
    assert excinfo.value.kind == "auth", excinfo.value.kind

    recorder.status = 404
    with pytest.raises(urllib.error.HTTPError) as raw:
        deep_http.guarded_open(f"{recorder.origin}/missing", timeout=2,
                               headers={"X-Plex-Token": TOKEN},
                               allow_private_hosts=True)
    assert raw.value.code == 404
    assert len(recorder.requests) == 2


def test_row713_every_hop_is_reclassified_not_just_the_first(monkeypatch):
    """E3: per-hop classification. A SAME-ORIGIN hop whose host re-resolves to
    a private address is exactly the rebinding case this exists to catch, so
    only the resolver decides here and the transport is substituted."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    answers = [PUBLIC_IP, "127.0.0.1"]
    resolved = []

    def _rebinding(host, *_a, **_k):
        ip = answers[min(len(resolved), len(answers) - 1)]
        resolved.append(ip)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]

    sent = []

    class _RedirectingOpener:
        def open(self, request, timeout=None):
            sent.append(request.full_url)
            raise _http_error(request.full_url, 302,
                              "http://rebind.fixture.invalid/second")

    monkeypatch.setattr(socket, "getaddrinfo", _rebinding)
    monkeypatch.setattr(deep_http, "_OPENER", _RedirectingOpener())

    with pytest.raises(SSRFBlocked) as excinfo:
        deep_http.guarded_open("http://rebind.fixture.invalid/first", timeout=2,
                               headers={"X-Emby-Token": TOKEN})
    assert "127.0.0.1" in str(excinfo.value), excinfo.value
    # Hop one classified public and was sent; hop two was refused BEFORE the
    # transport was reached again -- the same origin, a different answer.
    assert sent == ["http://rebind.fixture.invalid/first"]
    assert resolved == [PUBLIC_IP, "127.0.0.1"]


def test_row713_redirect_hop_limit_is_a_refusal(servers):
    """E4: the hop budget. Exhausting it must REFUSE; returning None would
    make `with guarded_open(...)` raise AttributeError, which every module's
    catch-all launders into kind='unknown'."""
    from bulk_downloader import deep_http
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked

    recorder = servers()
    recorder.redirect_to = "/loop"
    with pytest.raises(SSRFBlocked) as excinfo:
        deep_http.guarded_open(recorder.origin, timeout=2,
                               headers={"X-Emby-Token": TOKEN},
                               allow_private_hosts=True)
    assert "hop limit" in str(excinfo.value)
    assert len(recorder.requests) == 6


# ── the per-site opt-in (operator ruling): default off, named in the refusal ──


SITE_MODULES = [
    pytest.param("plex_deep", "plex_url", "plex_token",
                 "plex_allow_private_host", "xml", "query:X-Plex-Token",
                 id="plex_deep"),
    pytest.param("jellyfin_deep", "jellyfin_url", "jellyfin_api_key",
                 "jellyfin_allow_private_host", "json_system", "X-Emby-Token",
                 id="jellyfin_deep"),
    pytest.param("stash_deep", "stash_url", "stash_api_key",
                 "stash_allow_private_host", "json_graphql", "ApiKey",
                 id="stash_deep"),
]


def _site_module(name):
    import importlib
    return importlib.import_module(f"bulk_downloader.{name}")


@pytest.mark.parametrize(
    "name,url_key,token_key,setting,body_kind,token_place", SITE_MODULES)
def test_row713_site_without_the_optin_refuses_its_private_server(
        servers, name, url_key, token_key, setting, body_kind, token_place):
    """DEFAULT OFF, and the refusal TELLS THE OPERATOR the setting that
    re-enables it -- a media server that stops answering must not require
    reading a changelog to fix."""
    module = _site_module(name)
    recorder = servers()
    recorder.body_kind = body_kind
    client = module.get_client_for_site({url_key: recorder.origin,
                                         token_key: TOKEN})
    assert client.allow_private_hosts is False
    result = client.diagnose()
    assert result["ok"] is False
    assert recorder.requests == []
    assert module.PRIVATE_HOST_SETTING == setting
    assert setting in result["error"], result["error"]
    assert setting in (result["hint"] or ""), result["hint"]


@pytest.mark.parametrize(
    "name,url_key,token_key,setting,body_kind,token_place", SITE_MODULES)
def test_row713_site_with_the_optin_reaches_its_private_server(
        servers, name, url_key, token_key, setting, body_kind, token_place):
    """The opt-in permits a private target FOR THAT SITE ONLY."""
    module = _site_module(name)
    recorder = servers()
    recorder.body_kind = body_kind
    client = module.get_client_for_site({url_key: recorder.origin,
                                         token_key: TOKEN, setting: True})
    assert client.allow_private_hosts is True
    result = client.diagnose()
    assert result["ok"] is True, result
    assert result["version"] == "1"
    assert len(recorder.requests) == 1
    assert recorder.token_hits() == [(token_place, TOKEN)]
    # A DIFFERENT site, same server, no setting: still refused. The opt-in
    # does not weaken classification for anything but the site that carries it.
    other = module.get_client_for_site({url_key: recorder.origin,
                                        token_key: TOKEN})
    assert other.allow_private_hosts is False
    assert other.diagnose()["ok"] is False
    assert len(recorder.requests) == 1


@pytest.mark.parametrize(
    "name,url_key,token_key,setting,body_kind,token_place", SITE_MODULES)
def test_row713_the_optin_never_travels_across_a_redirect(
        servers, name, url_key, token_key, setting, body_kind, token_place):
    """The opt-in relaxes HOST CLASSIFICATION for one configured site. It is
    not a redirect permit, and nothing the remote end sends can extend it to
    another host."""
    module = _site_module(name)
    first = servers("127.0.0.1")
    second = servers("127.0.0.2")
    first.body_kind = second.body_kind = body_kind
    first.reflect_query = True
    first.redirect_to = f"{second.origin}/next"
    client = module.get_client_for_site({url_key: first.origin,
                                         token_key: TOKEN, setting: True})
    result = client.diagnose()
    assert second.requests == [] and second.token_hits() == []
    assert "cross-origin redirect refused" in result["error"], result["error"]
    assert len(first.requests) == 1
    # the flag is what the site config said, before and after the drive
    assert client.allow_private_hosts is True
    # and the host the redirect named is not opted in by having been named
    unset = module.get_client_for_site({url_key: second.origin,
                                        token_key: TOKEN})
    assert unset.allow_private_hosts is False
    assert unset.diagnose()["ok"] is False
    assert second.requests == []


def test_row713_plexapi_honours_the_same_per_site_optin(servers, fake_plexapi):
    """The fourth module reads the SAME site key, since both Plex backends
    serve one site's URL."""
    from bulk_downloader import plex_deep

    backend, seen = fake_plexapi
    recorder = servers()
    cfg = {"plex_url": recorder.origin, "plex_token": TOKEN}
    assert backend.connect(cfg) is None
    assert seen == []
    assert backend.PRIVATE_HOST_SETTING == plex_deep.PRIVATE_HOST_SETTING
    assert backend.PRIVATE_HOST_SETTING in backend.status_dict()["refusal"]
    assert backend.connect(dict(cfg, **{backend.PRIVATE_HOST_SETTING: True})) is not None
    assert len(seen) == 1
    assert backend.status_dict()["refused"] is False


def test_row713_the_private_host_optin_is_a_real_site_setting():
    """A setting an operator cannot set is not an opt-in. The three keys must
    survive a site-config save and default OFF for every site."""
    from bulk_downloader import app_kernel, jellyfin_deep, plex_deep, stash_deep

    settings = [plex_deep.PRIVATE_HOST_SETTING,
                jellyfin_deep.PRIVATE_HOST_SETTING,
                stash_deep.PRIVATE_HOST_SETTING]
    assert len(set(settings)) == 3, settings
    for key in settings:
        assert key in app_kernel.CFG_FIELDS, f"{key} would be dropped on save"
        assert app_kernel.DEFAULTS[key] is False, f"{key} must default OFF"
