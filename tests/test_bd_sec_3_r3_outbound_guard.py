"""BD-SEC-3 r3 (api-ssrf audit 2026-10-13): a request-supplied URL passes the outbound guard before BD fetches it.

Hermetic: a loopback HTTP sink that counts what reaches it, and a fake resolver (names never leave the process).
No live host is probed.  One test group per route class of the audit:

  R1 browser navigation  -- runner page route, macro replay, login verify, takeover, scene crawl
  R3 urllib / raw socket -- request replay (body returned), TLS check, AI endpoint
  R4 external fetcher    -- Stash preview, ffmpeg HLS, generic apprise schemas
  R5 partial             -- live recorder host allow-list

NEG controls sit beside every refusal: a public https URL (fake resolver), a LAN address and loopback are still
admitted, and a guarded fetch still returns the sink's body.
"""

from __future__ import annotations

import http.server
import socket
import threading
import types
import urllib.error

import pytest

from bulk_downloader import outbound_guard as og

BD_GATE_SCOPE = "module"

METADATA = "169.254.169.254"
NAMES = {
    "public.example": ["93.184.216.34"],
    "lan.example": ["192.168.1.20"],
    "metadata.example": [METADATA],
    "mixed.example": ["93.184.216.34", METADATA],  # one bad answer among good ones
    "v6ll.example": ["fe80::1"],
    "mapped.example": ["::ffff:169.254.169.254"],
    "sink.example": ["127.0.0.1"],
    # reachable first answer + a refused one: an unguarded client connects to the sink and gets a body
    "rebound.example": ["127.0.0.1", METADATA],
    "imds6.example": ["fd00:ec2::254"],  # r4 F2: metadata addresses a range rule calls LAN or public
    "ali.example": ["100.100.100.200"],
}
_REAL_GETADDRINFO = socket.getaddrinfo


def _answers(host, port):
    out = []
    for ip in NAMES[host]:
        fam = socket.AF_INET6 if ":" in ip else socket.AF_INET
        out.append(
            (
                fam,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, port or 0) + ((0, 0) if fam == socket.AF_INET6 else ()),
            )
        )
    return out


@pytest.fixture(autouse=True)
def resolver(monkeypatch):
    """The fake resolver: NAMES answer from the table, an IP literal passes through, anything else does not resolve."""
    asked = []

    def fake(host, port, *args, **kwargs):
        asked.append(host)
        if host in NAMES:
            return _answers(host, port)
        try:
            socket.inet_pton(
                socket.AF_INET6 if ":" in str(host) else socket.AF_INET, str(host)
            )
        except OSError:
            raise socket.gaierror(
                socket.EAI_NONAME, f"fake resolver: no such name {host!r}"
            ) from None
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)

    monkeypatch.setattr(og, "_resolve", lambda host, port: fake(host, port))
    monkeypatch.setattr(
        socket, "getaddrinfo", fake
    )  # urllib_ssrf.PinnedUrlOpener resolves through it
    return asked


class Sink:
    """A loopback HTTP server that records every request; /redir?to=<url> answers 302."""

    def __init__(self):
        self.hits = hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                hits.append((self.command, self.path, self.headers.get("Host")))
                if self.path.startswith("/redir?to="):
                    self.send_response(302)
                    self.send_header("Location", self.path.split("to=", 1)[1])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = b"SINK-BODY " + self.path.encode()
                self.send_response(200)
                if self.path in _POPUP_OPENERS:  # r8: a page whose own load script opens a popup
                    body = _POPUP_OPENERS[self.path].replace("PORT", str(self.server.server_address[1])).encode()
                    self.send_header("Content-Type", "text/html")
                if self.path == "/sw.js":  # a service worker that opens a socket and fetches
                    body = (
                        b'new WebSocket("ws://0.0.0.0:" + location.port + "/sw-ws");'
                        b'fetch("http://0.0.0.0:" + location.port + "/sw-fetch").catch(() => 0);'
                    )
                    self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _serve

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sink():
    s = Sink()
    yield s
    s.close()


# ---- the judgment itself --------------------------------------------------------------------------------------------
REFUSED = {
    "metadata-literal": f"http://{METADATA}/latest/meta-data/",
    "metadata-by-name": "http://metadata.example/latest/meta-data/",
    "one-bad-answer": "https://mixed.example/x",
    "v6-link-local": "http://v6ll.example/",
    "v6-literal-link-local": "http://[fe80::1]/",
    "mapped-metadata": "http://mapped.example/",
    "mapped-literal": "http://[::ffff:169.254.169.254]/",
    "unspecified": "http://0.0.0.0:8080/",
    "multicast": "http://224.0.0.1/",
    "reserved": "http://240.0.0.1/",
    "unresolvable": "https://no-such-name.example/",
    "file-scheme": "file:///etc/passwd",
    "ftp-scheme": "ftp://public.example/x",
    "gopher-scheme": "gopher://public.example:70/_x",
    "no-scheme": "public.example/x",
    "no-host": "http:///x",
    "bad-port": "http://public.example:99999/",
    "not-a-string": None,
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_check_url_refuses(case):
    with pytest.raises(og.OutboundRefused) as exc:
        og.check_url(REFUSED[case])
    assert str(exc.value).startswith("outbound refused: "), (
        f"BD-SEC-3-R3-UNNAMED-REFUSAL: {exc.value}"
    )
    assert og.url_refusal(REFUSED[case]) == str(exc.value)


@pytest.mark.parametrize(
    "url, want",
    [
        (
            "https://public.example/video.mp4",
            ("93.184.216.34",),
        ),  # NEG: a public https URL is still fetched
        ("http://lan.example:8096/", ("192.168.1.20",)),
        ("http://127.0.0.1:5555/api", ("127.0.0.1",)),
        ("http://[::1]:5555/", ("::1",)),
        ("http://10.1.2.3/", ("10.1.2.3",)),
    ],
)
def test_check_url_admits_public_lan_and_loopback_under_lan_ok(url, want):
    assert og.check_url(url) == want, f"BD-SEC-3-R3-NEG: {url} refused"
    assert og.url_refusal(url) is None


def test_public_only_refuses_lan_and_loopback_and_admits_public():
    assert og.check_url("https://public.example/x", og.PUBLIC_ONLY) == (
        "93.184.216.34",
    )
    for url in (
        "http://lan.example/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://100.64.0.1/",
        f"http://{METADATA}/",
    ):
        assert og.url_refusal(url, og.PUBLIC_ONLY), (
            f"BD-SEC-3-R3-PUBLIC-ONLY-OPEN: {url}"
        )
    with pytest.raises(ValueError):
        og.check_url("https://public.example/", "no-such-policy")


def test_every_answer_is_judged_not_only_the_first(resolver):
    NAMES["late-bad.example"] = ["93.184.216.34", "93.184.216.35", METADATA]
    try:
        assert "link-local" in og.url_refusal("https://late-bad.example/")
    finally:
        del NAMES["late-bad.example"]


def test_an_unresolvable_name_is_refused_unless_an_external_fetcher_resolves_it():
    """Default = fail closed. unresolved_ok (browser / ffmpeg seams: the lookup may happen behind a proxy) lets the
    NAME through and nothing else: a literal and a resolvable name are judged exactly as before."""
    assert "does not resolve" in og.url_refusal("https://no-such-name.example/x")
    assert og.check_url("https://no-such-name.example/x", unresolved_ok=True) == ()
    for url in (
        f"http://{METADATA}/",
        "http://metadata.example/",
        "https://mixed.example/",
        "file:///etc/passwd",
    ):
        assert og.url_refusal(url, unresolved_ok=True), (
            f"BD-SEC-3-R3-UNRESOLVED-OK-OPENS: {url}"
        )
    assert og.url_refusal("https://no-such-name.example/x", og.PUBLIC_ONLY) is not None


# ---- guarded urlopen: pinned to the vetted literal, every redirect hop judged ---------------------------------------
def test_guarded_urlopen_fetches_through_the_vetted_literal(sink):
    with og.urlopen(f"http://sink.example:{sink.port}/hello") as resp:
        assert resp.read() == b"SINK-BODY /hello"
    assert sink.hits == [("GET", "/hello", f"sink.example:{sink.port}")], (
        f"BD-SEC-3-R3-NEG-PIN: {sink.hits}"
    )


@pytest.mark.parametrize(
    "target",
    [
        f"http://{METADATA}/latest/meta-data/",
        "http://metadata.example/x",
        "file:///etc/passwd",
    ],
)
def test_guarded_urlopen_refuses_a_redirect_hop(sink, target):
    with pytest.raises(urllib.error.URLError) as exc:
        og.urlopen(f"http://127.0.0.1:{sink.port}/redir?to={target}")
    assert len(sink.hits) == 1, f"BD-SEC-3-R3-REDIRECT-FOLLOWED: {sink.hits}"
    assert (
        exc.value is not None
    )  # the wording is urllib's for a non-http hop, the guard's otherwise


def test_guarded_urlopen_follows_an_admitted_redirect(sink):
    with og.urlopen(
        f"http://127.0.0.1:{sink.port}/redir?to=http://sink.example:{sink.port}/after"
    ) as resp:
        assert resp.read() == b"SINK-BODY /after"
    assert [h[1] for h in sink.hits][-1] == "/after"


def test_guarded_urlopen_refuses_before_any_socket(monkeypatch):
    opened = []
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError("no")),
    )
    with pytest.raises(urllib.error.URLError):
        og.urlopen("http://metadata.example/latest/")
    assert opened == [], f"BD-SEC-3-R3-SOCKET-OPENED: {opened}"


def test_connect_goes_to_the_vetted_literal_and_refuses_metadata(sink, monkeypatch):
    real, dialled = socket.create_connection, []

    def recording(address, *args, **kwargs):
        dialled.append(address)
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", recording)
    s = og.connect("sink.example", sink.port, timeout=5)
    try:
        assert s.getpeername() == ("127.0.0.1", sink.port)
    finally:
        s.close()
    assert dialled == [("127.0.0.1", sink.port)], f"BD-SEC-3-R3-CONNECT-BY-NAME: {dialled}"
    opened = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: opened.append(a))
    for host in (METADATA, "metadata.example", "mixed.example", "no-such-name.example"):
        with pytest.raises(og.OutboundRefused):
            og.connect(host, 443)
    assert opened == [], f"BD-SEC-3-R3-SOCKET-OPENED: {opened}"


# ---- R1: browser navigation -----------------------------------------------------------------------------------------
class Page:
    def __init__(self, url="about:blank"):
        self.routes, self.url, self.gotos = [], url, []

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def goto(self, url, **kwargs):
        self.gotos.append(url)

    def wait_for_timeout(self, ms):
        pass


class Route:
    action = None

    def abort(self, *args):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"

    def fulfill(self, status=200, body="", headers=None):
        self.action, self.body, self.headers = "fulfill", body, headers or {}


def _request(url, resource_type="document"):
    return types.SimpleNamespace(url=url, resource_type=resource_type)


def _runner(headless=True, asset_filter=True):
    from bulk_downloader.runner_browser import BrowserMixin

    class Runner(BrowserMixin):
        config = {"browser_asset_filter": asset_filter}
        _launched_headless = headless
        events = []

        def log_event(self, kind, message, **kw):
            self.events.append((kind, message, kw))

    Runner.events = []
    return Runner()


PAGE_REFUSED = [
    f"http://{METADATA}/latest/meta-data/iam/",
    "http://metadata.example/",
    "https://mixed.example/a.js",
    "http://[fe80::1]/",
    "http://0.0.0.0/",
    "file:///etc/passwd",
    "ftp://public.example/x",
    f"ws://{METADATA}/socket",
    "view-source:http://public.example/",
]
PAGE_ADMITTED = [
    "https://public.example/watch/1",  # NEG
    "http://lan.example:8096/web/",
    "http://127.0.0.1:5555/",
    "https://no-such-name.example/x",  # not resolvable here: the browser (or its proxy) decides
    "about:blank",
    "data:text/html,<p>x</p>",
    "blob:https://public.example/5f0c",
    "wss://public.example/live",
]


@pytest.mark.parametrize(
    "headless, asset_filter",
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_runner_page_route_refuses_never_admissible_requests_in_every_mode(
    headless, asset_filter
):
    """runner.py:_process_one page.goto, warmup, takeover and scene-crawl pages are all set up by
    BrowserMixin._apply_stealth_library_to_page -> _install_browser_asset_filter: the guard rides that route."""
    runner, page = _runner(headless, asset_filter), Page()
    runner._install_browser_asset_filter(page)
    assert len(page.routes) == 1 and page.routes[0][0] == "**/*", (
        f"BD-SEC-3-R3-R1-NO-ROUTE: {page.routes}"
    )
    handler = page.routes[0][1]
    for url in PAGE_REFUSED:
        route = Route()
        handler(route, _request(url))
        assert route.action == "abort", (
            f"BD-SEC-3-R3-R1-PAGE-REQUEST-SENT: {url} ({headless=}, {asset_filter=})"
        )
    assert [e[0] for e in runner.events] == ["outbound_refused"] * len(PAGE_REFUSED), (
        runner.events
    )
    for url in PAGE_ADMITTED:
        route = Route()
        handler(route, _request(url))
        assert route.action == "continue", (
            f"BD-SEC-3-R3-R1-NEG: {url} aborted ({headless=}, {asset_filter=})"
        )


def test_runner_asset_rules_still_apply_after_the_guard():
    runner, page = _runner(True, True), Page("https://public.example/watch/1")
    runner._install_browser_asset_filter(page)
    handler = page.routes[0][1]
    img, doc = Route(), Route()
    handler(img, _request("https://public.example/banner.png", "image"))
    handler(doc, _request("https://public.example/watch/1", "document"))
    assert (img.action, doc.action) == ("abort", "continue")
    headed, page2 = _runner(False, True), Page()
    headed._install_browser_asset_filter(page2)
    img2 = Route()
    page2.routes[0][1](img2, _request("https://public.example/banner.png", "image"))
    assert img2.action == "continue", (
        "a headed page must still render what the site serves"
    )


def test_page_guard_judges_each_origin_once_and_fails_closed(resolver, monkeypatch):
    judge = og.page_guard()
    for _ in range(5):
        assert judge(Route(), _request("https://public.example/a")) is False
    assert resolver.count("public.example") == 1, (
        f"BD-SEC-3-R3-R1-LOOKUP-PER-REQUEST: {resolver}"
    )
    monkeypatch.setattr(
        og,
        "page_request_refusal",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    route = Route()
    assert (
        og.page_guard()(route, _request("https://public.example/b")) is True
        and route.action == "abort"
    )


def test_install_page_guard_continues_admitted_and_aborts_refused():
    page, seen = Page(), []
    og.install_page_guard(page, on_refuse=lambda url, why: seen.append((url, why)))
    handler = page.routes[0][1]
    ok, bad = Route(), Route()
    handler(ok, _request("https://public.example/"))
    handler(bad, _request(f"http://{METADATA}/"))
    assert (ok.action, bad.action) == ("continue", "abort")
    assert seen and seen[0][0] == f"http://{METADATA}/" and "link-local" in seen[0][1]


def test_an_admitted_request_falls_back_to_the_handlers_registered_before_the_guard():
    """Playwright runs the last registered handler first and ``continue_()`` ends the chain: a guard that
    continued would switch off a page's earlier routes (mocks, session routes, the context guard).  With a
    real Route (it has ``fallback``) the guard must fall back; a refused request is still aborted."""

    class ChainRoute(Route):
        def fallback(self):
            self.action = "fallback"

    page = Page()
    og.install_page_guard(page)
    ok, bad = ChainRoute(), ChainRoute()
    page.routes[0][1](ok, _request("https://public.example/"))
    page.routes[0][1](bad, _request(f"http://{METADATA}/"))
    assert (ok.action, bad.action) == ("fallback", "abort"), "BD-SEC-3-R3-GUARD-ENDS-THE-ROUTE-CHAIN"
    headed, hpage = _runner(headless=False), Page()
    headed._install_browser_asset_filter(hpage)
    ok = ChainRoute()
    hpage.routes[0][1](ok, _request("https://public.example/app.js", "script"))
    assert ok.action == "fallback", "BD-SEC-3-R3-HEADED-GUARD-ENDS-THE-ROUTE-CHAIN"


def test_runner_context_route_judges_pages_nobody_set_up():
    """A popup or probe page has no page-level route: the context-level guard installed by _install_stealth
    (called for every context the runner makes, stealth on or off) judges its requests."""

    class Ctx(Page):
        scripts = []

        def add_init_script(self, script):
            self.scripts.append(script)

    for use_stealth in (True, False):
        runner, ctx = _runner(), Ctx()
        runner.config = dict(runner.config, use_stealth=use_stealth)
        runner._install_stealth(ctx)
        assert [r[0] for r in ctx.routes] == ["**/*"], (
            f"BD-SEC-3-R3-R1-CONTEXT-UNGUARDED ({use_stealth=})"
        )
        bad, ok = Route(), Route()
        ctx.routes[0][1](bad, _request(f"http://{METADATA}/latest/"))
        ctx.routes[0][1](ok, _request("https://public.example/popup"))
        assert (bad.action, ok.action) == ("abort", "continue"), (
            f"BD-SEC-3-R3-R1-POPUP-REQUEST-SENT ({use_stealth=})"
        )


@pytest.mark.parametrize(
    "rel, owner",
    [
        ("bulk_downloader/login_impl/manual.py", "ManualLoginSession._launch"),
        ("bulk_downloader/login_impl/submit.py", "do_login"),
        ("bulk_downloader/login_impl/replay.py", "verify_login_replay"),
        ("bulk_downloader/takeover_vnc.py", "VncTakeoverSession._run"),
        ("bulk_downloader/macro_replay.py", "replay_standalone"),
    ],
)
def test_every_own_browser_installs_the_guard_before_its_first_navigation(rel, owner):
    """The login, captcha-solve, verify, takeover and macro browsers are launched inside functions too
    large to drive here; what is read from the tree is the ORDER: a real ``install_page_guard(...)`` call
    (an AST Call, so a comment or a string cannot satisfy it) before the first ``.goto(`` of the function."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    node = ast.parse((root / rel).read_text(encoding="utf-8"))
    for name in owner.split("."):
        node = next(
            n
            for n in ast.iter_child_nodes(node)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name
        )
    calls = [
        (c.lineno, c.func.attr)
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
    ]
    gotos = [line for line, attr in calls if attr == "goto"]
    installs = [line for line, attr in calls if attr == "install_page_guard"]
    assert gotos, f"{rel}::{owner} navigates nowhere -- the probe proves nothing"
    assert installs and min(installs) < min(gotos), (
        f"BD-SEC-3-R3-OWN-BROWSER-UNGUARDED {rel}::{owner}: installs at {installs}, first goto at {min(gotos)}"
    )
    # r5 (R2): the same browsers are created with service workers blocked -- a keyword, a dict entry or a
    # subscript assignment in CODE whose value is the constant "block".
    blocked = []
    for n in ast.walk(node):
        if isinstance(n, ast.keyword) and n.arg == "service_workers":
            blocked.append(n.value)
        elif isinstance(n, ast.Dict):
            blocked += [
                v
                for k, v in zip(n.keys, n.values)
                if isinstance(k, ast.Constant) and k.value == "service_workers"
            ]
        elif isinstance(n, ast.Assign):
            blocked += [
                n.value
                for t in n.targets
                if isinstance(t, ast.Subscript)
                and isinstance(t.slice, ast.Constant)
                and t.slice.value == "service_workers"
            ]
    assert blocked and all(
        isinstance(v, ast.Constant) and v.value == "block" for v in blocked
    ), f"BD-SEC-3-R5-R2-SERVICE-WORKERS-ALLOWED {rel}::{owner}"


def test_login_verify_member_url_is_judged_before_navigation():
    from bulk_downloader.login_impl import replay

    page = Page()
    ok, _ms, err = replay._probe_member_url(
        page, f"http://{METADATA}/latest/meta-data/", 5
    )
    assert page.gotos == [], f"BD-SEC-3-R3-R1-LOGIN-VERIFY-NAVIGATED: {page.gotos}"
    assert not ok and "outbound refused" in err, (
        f"BD-SEC-3-R3-R1-LOGIN-VERIFY: {ok!r} {err!r}"
    )


def test_take_over_url_is_refused_by_name():
    from bulk_downloader.runner_manual import ManualMixin

    class Runner(ManualMixin):
        site_id = "s1"
        _manual_download_session = None

    ok, why = Runner().start_manual_download(f"http://{METADATA}/latest/")
    assert ok is False and "outbound refused" in why and "link-local" in why, (
        f"BD-SEC-3-R3-R1-TAKEOVER: {why!r}"
    )
    ok, why = Runner().start_manual_download("http://metadata.example/")
    assert ok is False and "outbound refused" in why


def test_scene_crawl_listing_url_is_refused_before_the_page_is_touched(tmp_path):
    from bulk_downloader import scene_crawler

    page = Page()
    with pytest.raises(ValueError, match="outbound refused"):
        scene_crawler.crawl_with_page(
            page,
            site_id="s1",
            listing_url=f"http://{METADATA}/",
            site_config={},
            enqueue_fn=lambda *a: None,
            db_path=str(tmp_path / "scenes.db"),
        )
    assert page.gotos == [] and page.routes == []


def test_macro_replay_start_url_is_refused_and_the_page_is_guarded(monkeypatch):
    import contextlib

    from bulk_downloader import macro_replay

    page = Page()

    @contextlib.contextmanager
    def cloaked_page(**kwargs):
        yield page

    monkeypatch.setattr("bulk_downloader.cloak.cloaked_page", cloaked_page)
    monkeypatch.setattr(
        "bulk_downloader.macro_recorder.get_macro",
        lambda site_id, name: {"actions": []},
    )
    out = macro_replay.replay_standalone(
        "s1", "m1", start_url=f"http://{METADATA}/", persist_result=False
    )
    assert page.gotos == [], f"BD-SEC-3-R3-R1-MACRO-NAVIGATED: {page.gotos}"
    assert out.get("ok") is False and "outbound refused" in str(out.get("error")), (
        f"BD-SEC-3-R3-R1-MACRO: {out}"
    )
    assert len(page.routes) == 1, "BD-SEC-3-R3-R1-MACRO-PAGE-UNGUARDED"


# ---- R3: urllib clients and raw sockets -----------------------------------------------------------------------------
def _captured(monkeypatch, path="/api/x"):
    from bulk_downloader import request_replay

    monkeypatch.setattr(
        request_replay,
        "get",
        lambda rid: {
            "status": 200,
            "method": "GET",
            "path": path,
            "query": "",
            "request_body": "",
        },
    )
    return request_replay


def _replay_with_master_token(rr, monkeypatch, base_url, sink):
    """The sink is this box (loopback), so the BASE F2b gate refuses the replay before any socket opens unless
    the caller holds the master token (r10, lens C2 F1): assert that refusal, then grant the token so what the
    rest of the test measures is the outbound guard, not the F2b gate."""
    out = rr.replay("r1", base_url=base_url)
    assert (
        not out["ok"] and out["error"] == "a replay to this box needs the master token" and not sink.hits
    ), f"BD-SEC-3-R10-F2B-SELF-REPLAY-WITHOUT-TOKEN: {out} hits={sink.hits}"
    monkeypatch.setattr(rr, "_caller_has_master_token", lambda: True)
    return rr.replay("r1", base_url=base_url)


def test_request_replay_returns_the_body_of_an_admitted_target_only(sink, monkeypatch):
    rr = _captured(monkeypatch)
    out = _replay_with_master_token(rr, monkeypatch, f"http://sink.example:{sink.port}", sink)
    assert out["ok"] and out["replay_body"] == "SINK-BODY /api/x", (
        f"BD-SEC-3-R3-R3-NEG-REPLAY: {out}"
    )
    for base in (
        f"http://{METADATA}",
        "http://metadata.example",
        "http://mixed.example",
    ):
        out = rr.replay("r1", base_url=base)
        assert (
            not out["ok"]
            and out["replay_body"] == ""
            and "outbound refused" in out["error"]
        ), f"BD-SEC-3-R3-R3-REPLAY-FETCHED: {base} -> {out}"


def test_request_replay_does_not_follow_a_redirect_to_metadata(sink, monkeypatch):
    """The hop target answers on the sink for a client that does not judge it (its first address is
    loopback), so an unguarded replay returns SINK-BODY /secret: the refusal is the guard's, not a dead host's."""
    rr = _captured(
        monkeypatch, path=f"/redir?to=http://rebound.example:{sink.port}/secret"
    )
    out = _replay_with_master_token(rr, monkeypatch, f"http://127.0.0.1:{sink.port}", sink)
    assert (
        [h[1] for h in sink.hits] == [f"/redir?to=http://rebound.example:{sink.port}/secret"]
        and not out["ok"]
        and out["replay_body"] == ""
        and "outbound refused" in out.get("error", "")
    ), f"BD-SEC-3-R3-R3-REPLAY-REDIRECT: {out} hits={sink.hits}"


def test_tls_check_never_connects_to_a_refused_host(monkeypatch):
    from bulk_downloader.dev_suite import audit_security

    opened = []
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError("x")),
    )
    out = audit_security.tls_cert_check(
        hosts=[METADATA, "metadata.example", "sink.example"], timeout=1
    )
    by_host = {r["host"]: r for r in out["results"]}
    assert (
        "outbound refused" in by_host[METADATA]["error"]
        and "outbound refused" in by_host["metadata.example"]["error"]
    )
    assert [a[0][0] for a in opened] == ["127.0.0.1"], (
        f"BD-SEC-3-R3-R3-TLS-CONNECT: {opened}"
    )


def test_ai_endpoint_requests_go_through_the_guard(sink):
    from urllib.request import Request

    from bulk_downloader import ai_provider

    with ai_provider.urlopen(
        Request(f"http://sink.example:{sink.port}/api/tags"), timeout=5
    ) as resp:
        assert resp.read() == b"SINK-BODY /api/tags"
    hop = f"/redir?to=http://rebound.example:{sink.port}/v1/models"
    # rebound.example answers on the sink for a client that does not judge every address
    for url in (
        f"http://{METADATA}/api/tags",
        "http://metadata.example/v1/models",
        f"http://rebound.example:{sink.port}/api/tags",
        f"http://127.0.0.1:{sink.port}{hop}",
    ):
        with pytest.raises(urllib.error.URLError):
            ai_provider.urlopen(Request(url), timeout=5)
            pytest.fail(f"BD-SEC-3-R3-R3-AI-FETCHED: {url} hits={sink.hits}")
    assert [h[1] for h in sink.hits] == ["/api/tags", hop], (
        f"BD-SEC-3-R3-R3-AI-FETCHED: {sink.hits}"
    )


def test_ollama_endpoint_validator_refuses_link_local_and_keeps_the_lan():
    from bulk_downloader import aiassist

    for url in (
        f"http://{METADATA}:11434",
        "http://metadata.example:11434",
        "http://v6ll.example:11434",
    ):
        ok, why = aiassist.validate_endpoint(url, provider="ollama")
        assert ok is False and "link-local" in why, (
            f"BD-SEC-3-R3-R3-OLLAMA-METADATA: {url} -> {ok} {why}"
        )
    for url in (
        "http://lan.example:11434",
        "http://127.0.0.1:11434",
        "http://10.0.0.7:11434",
    ):
        ok, why = aiassist.validate_endpoint(url, provider="ollama")
        assert ok is True, f"BD-SEC-3-R3-R3-NEG-OLLAMA: {url} -> {why}"


# ---- R4: a URL handed to an external fetcher ------------------------------------------------------------------------
def test_stash_preview_hands_over_public_urls_only(monkeypatch):
    from bulk_downloader import runner_integrations

    sent = []

    class Runner(runner_integrations.IntegrationsMixin):
        config = {"stash_scrape_preview": True}

        def _get_stash_client(self):
            return types.SimpleNamespace(
                scrape_url=lambda url: sent.append(url) or {"title": "t"}
            )

    monkeypatch.setattr("bulk_downloader.stash_deep.deep_enabled", lambda cfg: True)
    runner = Runner()
    assert runner._stash_scrape_preview("https://public.example/scene/1") == {
        "title": "t"
    }
    for url in (
        f"http://{METADATA}/",
        "http://lan.example/",
        "http://127.0.0.1:9999/",
        "file:///etc/passwd",
    ):
        assert runner._stash_scrape_preview(url) is None, (
            f"BD-SEC-3-R3-R4-STASH: {url}"
        )
    assert sent == ["https://public.example/scene/1"], (
        f"BD-SEC-3-R3-R4-STASH-SENT: {sent}"
    )


def test_hls_manifest_url_is_judged_before_ffmpeg_starts(monkeypatch, tmp_path):
    from bulk_downloader import hls_downloader

    started = []
    monkeypatch.setattr(hls_downloader, "_find_ffmpeg", lambda: "/bin/false")
    monkeypatch.setattr(
        hls_downloader.subprocess,
        "Popen",
        lambda *a, **k: started.append(a) or (_ for _ in ()).throw(OSError("x")),
    )
    monkeypatch.setattr(
        hls_downloader.subprocess,
        "run",
        lambda *a, **k: started.append(a) or (_ for _ in ()).throw(OSError("x")),
    )
    for url in (
        f"http://{METADATA}/live.m3u8",
        "https://metadata.example/a.m3u8",
    ):
        res = hls_downloader.download(url, str(tmp_path / "out.mp4"))
        assert (
            res.ok is False
            and res.error == "bad_url"
            and "outbound refused" in res.error_detail
        ), f"BD-SEC-3-R3-R4-HLS: {url} -> {res}"
    assert started == [], f"BD-SEC-3-R3-R4-HLS-FFMPEG-STARTED: {started}"


# r4 (lens D190/D191 F1): every URL apprise splits out of an entry, every schema that takes a host.
APPRISE_HOST_URLS = [
    "json://{h}/hook",
    "xmls://{h}/x",
    "forms://{h}/x",
    "ntfy://{h}/topic",
    "gotify://{h}/abcdefghijklmno",
    "mmost://{h}/abcdefghijklmnopqrstuvwxyz",
    "rocket://user:pass@{h}/#chan",
    "matrix://user:pass@{h}/#room",
    "apprise://{h}/abcdefghijklmnop",
    "ncloud://user:pass@{h}/admin",
    "hassio://{h}/abcdefghijklmnopqrstuvwxyzabcdefgh",
    "kodi://{h}",
    "rsyslog://{h}",
    "mailto://user:pass@public.example?smtp={h}",
]


def _apprise_entry(monkeypatch, entry):
    """What ``notify_apprise.send`` hands to the real apprise for one configured entry:
    ``(instances added, first error code or None)``.  ``add`` and ``notify`` are recorded, never run."""
    import apprise
    from apprise.utils.parse import parse_urls

    from bulk_downloader import notify_apprise

    added = []

    def add(self, servers, asset=None, tag=None):
        for server in parse_urls(servers) if isinstance(servers, str) else servers:
            if isinstance(server, str):
                server = apprise.Apprise.instantiate(server, tag=tag)
            if server is None:
                return False
            added.append(server)
        return True

    monkeypatch.setattr(apprise.Apprise, "add", add)
    monkeypatch.setattr(apprise.Apprise, "notify", lambda self, **kw: True)
    result = notify_apprise.send([entry], "t", "b")
    return added, (result.errors[0][1] if result.errors else None)


@pytest.mark.parametrize("template", APPRISE_HOST_URLS)
def test_every_host_taking_apprise_schema_is_judged_by_its_target(template, monkeypatch):
    schema = template.split("://")[0]
    # imds6.example -> fd00:ec2::254.  (A bare [fd00:ec2::254] literal is not used here: apprise 1.12
    # itself mangles it to fd00:ec2:: for most plugins; what is judged is the host the INSTANCE dials.)
    for host in (METADATA, "metadata.example", "imds6.example"):
        instances, error = _apprise_entry(monkeypatch, template.format(h=host))
        assert (instances, error) == ([], "outbound_refused"), (
            f"BD-SEC-3-R4-F1-SCHEMA-NOT-JUDGED {schema} -> {host}: {error} {instances}"
        )
    for host in ("public.example", "lan.example"):  # NEG: the same schema to an admitted host
        instances, error = _apprise_entry(monkeypatch, template.format(h=host))
        assert error is None and len(instances) == 1, (
            f"BD-SEC-3-R4-F1-NEG-SCHEMA-REFUSED {schema} -> {host}: {error}"
        )


def test_an_apprise_entry_is_judged_after_apprises_own_split(monkeypatch):
    """One configured entry may hold several URLs (space or comma): a refused one refuses the entry."""
    for entry in (
        f"json://public.example/ json://{METADATA}/y",
        f"jsons://public.example/x,ntfy://{METADATA}/t",
        f"JSON://{METADATA}/upper",  # lens D3: the schema is matched case-insensitively
        "ntfy://[fd00:ec2::254]/t",  # a plugin that keeps the IPv6 literal whole
        "hassio://[fd00:ec2::254]/abcdefghijklmnopqrstuvwxyzabcdefgh",
        f"tgram://123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/1234 gotify://{METADATA}/abcdefghijklmno",
    ):
        instances, error = _apprise_entry(monkeypatch, entry)
        assert (instances, error) == ([], "outbound_refused"), (
            f"BD-SEC-3-R4-F1-SECOND-URL-NOT-JUDGED: {entry!r} -> {error} {instances}"
        )
    instances, error = _apprise_entry(monkeypatch, "json://public.example/a, jsons://lan.example/b")
    assert error is None and len(instances) == 2, f"BD-SEC-3-R4-F1-NEG-SPLIT: {error}"


def test_fixed_service_and_local_schemas_pass_and_an_unknown_host_is_refused(monkeypatch):
    from bulk_downloader import notify_apprise

    for entry in (
        "tgram://123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/1234",
        "discord://123456789012345678/" + "a" * 68,
        "ntfy://mytopic",  # cloud mode: the fixed ntfy.sh endpoint, "mytopic" is not a host
    ):
        instances, error = _apprise_entry(monkeypatch, entry)
        assert error is None and len(instances) == 1, (
            f"BD-SEC-3-R4-F1-NEG-FIXED-SERVICE-REFUSED: {entry} -> {error}"
        )
    # a host BD cannot resolve is refused here (BD's apprise opens the socket itself: fail closed)
    assert _apprise_entry(monkeypatch, "gotify://no-such-name.example/abcdefghijklmno") == (
        [],
        "outbound_refused",
    ), "BD-SEC-3-R4-F1-UNRESOLVED-ADMITTED"
    assert _apprise_entry(monkeypatch, "nosuchschema://x/y") == ([], "add_failed")
    # the allow-lists hold only plugins that have no host of their own in the installed apprise
    import inspect

    from apprise.manager_plugins import NotificationManager
    from apprise.plugins.base import NotifyBase

    manager = NotificationManager()
    manager.load_modules()
    checked = 0
    allowed = getattr(notify_apprise, "_FIXED_SERVICE_SCHEMAS", frozenset()) | getattr(
        notify_apprise, "_LOCAL_SCHEMAS", frozenset()
    )
    assert allowed, "BD-SEC-3-R4-F1-NO-ALLOWLIST: unknown schemas are not refused by default"
    for schema in sorted(allowed):
        cls = manager._schema_map.get(schema)
        if cls is None:
            continue
        source = "".join(
            inspect.getsource(c)
            for c in cls.__mro__
            if c is not NotifyBase and c.__module__.startswith("apprise.plugins.")
        )
        assert "self.host" not in source and "smtp_host" not in source, (
            f"BD-SEC-3-R4-F1-ALLOWLISTED-SCHEMA-TAKES-A-HOST: {schema}"
        )
        checked += 1
    assert checked >= 20, f"allow-list probe looked at {checked} plugins -- proves nothing"


def test_apprise_send_never_hands_a_refused_target_to_apprise(monkeypatch):
    """``send`` on the real apprise with ``add``/``notify`` recorded: a refused entry is skipped by name and
    NO URL of it is added; the admitted entry is added as the judged, tagged instance."""
    import apprise

    from bulk_downloader import notify_apprise

    added = []

    def add(self, servers, asset=None, tag=None):
        added.extend(servers)
        return True

    monkeypatch.setattr(apprise.Apprise, "add", add)
    monkeypatch.setattr(apprise.Apprise, "notify", lambda self, **kw: True)
    result = notify_apprise.send(
        [
            f"json://{METADATA}/latest/meta-data/",
            f"jsons://public.example/ok ntfy://{METADATA}/t",
            f"gotify://{METADATA}/abcdefghijklmno",
            "jsons://public.example/hook",
        ],
        "t",
        "b",
        tag="evt",
    )
    assert [type(i).__name__ for i in added] == ["NotifyJSON"] and added[0].host == (
        "public.example"
    ), f"BD-SEC-3-R3-R4-APPRISE-SENT: {[(type(i).__name__, i.host) for i in added]}"
    assert "evt" in added[0].tags
    assert [e[1] for e in result.errors] == ["outbound_refused"] * 3, result.errors
    assert (result.sent_count, result.skipped_count) == (1, 3), result


# ---- R5: live recorder host allow-list ------------------------------------------------------------------------------
def test_live_url_allow_list_judges_the_real_host():
    from bulk_downloader import live_recorder

    assert (
        live_recorder.parse_live_url("https://chaturbate.com/someroom/") is not None
    ), "BD-SEC-3-R3-R5-NEG-LIVE"
    assert (
        live_recorder._host_on_cam_allowlist("https://chaturbate.com/someroom/") is True
    )
    for url in (
        f"https://{METADATA}?.chaturbate.com/someroom/",
        f"http://{METADATA}#.chaturbate.com/someroom/",
        f"https://chaturbate.com@{METADATA}/someroom/",
        f"https://{METADATA}\\.chaturbate.com/someroom/",
    ):
        assert live_recorder.parse_live_url(url) is None, (
            f"BD-SEC-3-R3-R5-LIVE-BYPASS: {url}"
        )
        assert live_recorder._host_on_cam_allowlist(url) is False, (
            f"BD-SEC-3-R3-R5-LIVE-BYPASS: {url}"
        )


# ---- r4 F2: ONE cloud-metadata deny set, whatever range the address sits in ----------------------------------------
CLOUD_METADATA_URLS = [
    "http://[fd00:ec2::254]/latest/meta-data/",  # AWS IPv6 IMDS: ULA
    "http://[fd00:ec2::23]/",  # AWS ECS IPv6
    "http://100.100.100.200/latest/meta-data/",  # Alibaba: CGNAT
    "http://192.0.0.192/",  # Oracle
    "http://168.63.129.16/",  # Azure WireServer: a PUBLIC address
    "http://169.254.170.2/v2/credentials",  # AWS ECS
    "http://[::ffff:100.100.100.200]/",  # IPv4-mapped
    "http://[64:ff9b::6464:64c8]/",  # NAT64 of 100.100.100.200
    "http://[64:ff9b::a83f:8110]/",  # NAT64 of 168.63.129.16
    "http://imds6.example/",  # a name resolving to one
    "http://ali.example/",
    "http://metadata.google.internal/computeMetadata/v1/",  # a metadata NAME (does not resolve here)
    "http://METADATA.Google.Internal./",
    "http://metadata.goog/",
]


@pytest.mark.parametrize("url", CLOUD_METADATA_URLS)
def test_cloud_metadata_is_refused_under_every_policy_and_on_every_seam(url):
    for policy in (og.LAN_OK, og.PUBLIC_ONLY):
        assert og.url_refusal(url, policy, unresolved_ok=True), (
            f"BD-SEC-3-R4-F2-METADATA-ADMITTED ({policy}): {url}"
        )
    assert og.page_request_refusal(url), f"BD-SEC-3-R4-F2-METADATA-ADMITTED (page): {url}"
    assert og.page_request_refusal("ws" + url[4:]), (
        f"BD-SEC-3-R4-F2-METADATA-ADMITTED (websocket): {url}"
    )
    from bulk_downloader import aiassist

    ok, why = aiassist.validate_endpoint(url, provider="ollama")
    assert ok is False, f"BD-SEC-3-R4-F2-METADATA-ADMITTED (ollama validator): {url} -> {why}"
    with pytest.raises(RuntimeError):
        aiassist._pin_lan_endpoint(url)
        pytest.fail(f"BD-SEC-3-R4-F2-METADATA-ADMITTED (ollama pin): {url}")


def test_the_lan_ranges_the_metadata_addresses_live_in_stay_admitted():
    for url in (
        "http://[fd00::1]:8096/",  # ULA
        "http://[fd00:ec2::1]/",  # same /64 as the AWS IMDS, not it
        "http://100.64.0.1/",  # CGNAT
        "http://100.100.100.201/",
        "http://lan.example/",
        "http://127.0.0.1:11434/",
    ):
        assert og.url_refusal(url) is None, f"BD-SEC-3-R4-F2-NEG-LAN-REFUSED: {url}"
    assert og.url_refusal("https://public.example/", og.PUBLIC_ONLY) is None


# ---- r4 F3: popups and websockets ----------------------------------------------------------------------------------
class ScriptPage(Page):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def add_init_script(self, script):
        self.scripts.append(script)


WS_VERDICT_PATH = "/__bd_ws_verdict__"


def _ask(target, ws_url, origin="http://127.0.0.1:5555"):
    """What the page's WebSocket wrapper gets back from the route for ``ws_url``."""
    import urllib.parse

    route = Route()
    asked = f"{origin}{WS_VERDICT_PATH}?u=" + urllib.parse.quote(ws_url, safe="")
    target.routes[0][1](route, _request(asked, "xhr"))
    return route.action, getattr(route, "body", None)


def test_a_page_guard_also_guards_the_pages_context_and_websockets():
    """A page route does not apply to a popup the page opens, and no route sees a ws/wss handshake: the
    guard goes on the context too, and an init script makes the page ask the route before it opens a socket."""
    page = ScriptPage()
    page.context = ScriptPage()
    seen = []
    og.install_page_guard(page, on_refuse=lambda url, why: seen.append(url))
    assert len(page.routes) == 1 and len(page.context.routes) == 1, (
        f"BD-SEC-3-R4-F3-POPUP-CONTEXT-UNGUARDED: page={len(page.routes)} context={len(page.context.routes)}"
    )
    popup = Route()
    page.context.routes[0][1](popup, _request(f"http://{METADATA}/popup"))
    assert popup.action == "abort", "BD-SEC-3-R4-F3-POPUP-REQUEST-SENT"
    for target in (page, page.context):
        assert len(target.scripts) == 1 and WS_VERDICT_PATH in target.scripts[0], (
            "BD-SEC-3-R4-F3-WEBSOCKET-UNGUARDED: no init script asks the judge"
        )
        for url in (
            f"ws://{METADATA}/s",
            "wss://metadata.example/s",
            "ws://0.0.0.0:9/s",
            "ws://[fd00:ec2::254]/s",
            "ws://[fd00:ec2::254%2]/s",
            "wss://metadata.google.internal/s",
            "http://public.example/not-a-websocket",
            "",
        ):
            assert _ask(target, url) == ("fulfill", "no"), f"BD-SEC-3-R6-F1-WS-VERDICT refused socket not refused: {url!r}"
        for url in ("wss://public.example/live", "ws://lan.example:8096/socket", "ws://127.0.0.1:5555/ws"):
            assert _ask(target, url) == ("fulfill", "ok"), f"BD-SEC-3-R6-F1-ADMITTED-WEBSOCKET-BROKEN: {url}"
        # a page with no http origin (about:blank, srcdoc) asks through a name that resolves nowhere:
        # the request is answered by the route, never judged as a destination and never sent
        assert _ask(target, "wss://public.example/live", "http://bd-outbound-guard.invalid") == ("fulfill", "ok")
    assert f"ws://{METADATA}/s" in seen, "BD-SEC-3-R6-F1-WS-REFUSAL-NOT-REPORTED"
    undecided = ScriptPage()
    og.install_page_guard(undecided)
    original = og.page_request_refusal
    og.page_request_refusal = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert _ask(undecided, "wss://public.example/live") == ("fulfill", "no"), (
            "BD-SEC-3-R4-F3-UNDECIDED-WEBSOCKET-OPENED"
        )
    finally:
        og.page_request_refusal = original
    bare = Page()  # a context (or a fake) without add_init_script still gets the route
    og.install_page_guard(bare)
    assert len(bare.routes) == 1


def test_the_runner_page_route_answers_websocket_verdicts_in_every_mode():
    """A runner page carries its own route (asset filter): registered after the context guard it runs first,
    so it must answer the verdict request itself in all four headless / filter modes."""
    for headless in (True, False):
        for asset_filter in (True, False):
            runner, page = _runner(headless=headless, asset_filter=asset_filter), Page()
            runner._install_browser_asset_filter(page)
            mode = f"{headless=} {asset_filter=}"
            assert _ask(page, f"ws://{METADATA}/s") == ("fulfill", "no"), f"BD-SEC-3-R6-F1-WS-VERDICT ({mode})"
            assert _ask(page, "wss://public.example/live") == ("fulfill", "ok"), (
                f"BD-SEC-3-R6-F1-ADMITTED-WEBSOCKET-BROKEN ({mode})"
            )


def _launch(backend):
    """A real headless browser: plain Playwright Chromium, or the DEFAULT backend the runner uses (cloakbrowser,
    through BD's own cloak wrapper).  A launch failure is a FAILURE, not a skip; the one skip is a venv built
    without the optional requirements-cloak.txt (the package is not there to launch)."""
    import importlib.util
    import os

    if backend == "cloak":
        if importlib.util.find_spec("cloakbrowser") is None:
            pytest.skip("cloakbrowser (requirements-cloak.txt) is not installed in this venv")
        from bulk_downloader import runner_browser

        browser, pw, used = runner_browser._cloak.launch_browser(headless=True)
        if used != "cloakbrowser":
            browser.close()
            if pw is not None:
                pw.stop()
            pytest.fail(f"the default cloak backend did not launch here (got {used!r}) -- a FAILURE, not a skip")
        return browser, pw
    from playwright.sync_api import sync_playwright

    p = sync_playwright().start()
    kw = {"headless": True, "timeout": 20000, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    chrome = os.environ.get("BD_PW_CHROME", "")
    if chrome and os.path.exists(chrome):
        kw["executable_path"] = chrome
    try:
        return p.chromium.launch(**kw), p
    except Exception as e:  # noqa: BLE001
        p.stop()
        pytest.fail(f"chromium not launchable here -- a launch failure is a FAILURE, not a skip: {e}")


MECHANISMS = [("chromium", "route"), ("chromium", "script"), ("cloak", "script")]


def _install(target, mechanism, **kw):
    """Install the guard the way a seam does: ``route`` = the caller named plain Playwright (r7, option B:
    Playwright's WebSocket route), ``script`` = cloakbrowser or an unnamed backend (the page script)."""
    if mechanism == "route":
        if not hasattr(og, "PLAYWRIGHT_BACKEND"):
            pytest.fail("BD-SEC-3-R7-B-NO-ROUTE-MECHANISM: install_page_guard cannot be told the backend")
        kw["backend"] = og.PLAYWRIGHT_BACKEND
    og.install_page_guard(target, **kw)


@pytest.mark.parametrize("backend,mechanism", MECHANISMS)
def test_real_browser_popup_and_websocket_to_a_refused_address_never_reach_the_wire(sink, backend, mechanism):
    """A real headless browser against the loopback sink, on BOTH backends and BOTH WebSocket mechanisms.
    0.0.0.0 is refused (unspecified) and on Linux connects to loopback, so an UNGUARDED page reaches the sink
    with it -- that run is the positive control.  The guard is installed on the PAGE only, the way the
    login/verify/macro/takeover seams do.  With the guard on, the page's OWN WebSocket constructor must still
    work for an admitted socket.  Under ``route`` a refused socket does not throw: it never connects."""
    browser, pw = _launch(backend)
    refused_ctor = "threw SecurityError" if mechanism == "script" else "ok"
    told = []

    def drive(guarded, deaf=False, **context_options):
        del sink.hits[:]
        ctx = browser.new_context(**context_options)
        try:
            page = ctx.new_page()
            if guarded:
                _install(page, mechanism, on_refuse=lambda url, why: told.append(url))
            if deaf:  # a route registered later that continues everything: the judge never hears the page
                page.route("**/*", lambda route: route.continue_())
            page.goto(f"http://127.0.0.1:{sink.port}/start")
            ctor = page.evaluate(
                """(port) => {
                    const out = {};
                    const open = (name, url) => {
                        try { new WebSocket(url); out[name] = "ok"; } catch (e) { out[name] = "threw " + e.name; }
                    };
                    window.open(`http://0.0.0.0:${port}/popup`);
                    open("refused", `ws://0.0.0.0:${port}/ws-refused`);
                    open("admitted", `ws://127.0.0.1:${port}/ws-admitted`);
                    open("named", `ws://metadata.example:${port}/ws-named`);
                    open("mapped0", `ws://[::ffff:0.0.0.0]:${port}/ws-mapped0`);
                    open("mappedmeta", `ws://[::ffff:169.254.169.254]:${port}/ws-mappedmeta`);
                    open("sixtofour", `ws://[2002:a9fe:a9fe::]:${port}/ws-sixtofour`);
                    open("multicast", `ws://224.0.0.1:${port}/ws-multicast`);
                    open("lanliteral", `ws://127.0.0.1:${port}/ws-lanliteral`);
                    class Sub extends WebSocket {}
                    try { out.subclass = String(new Sub(`ws://127.0.0.1:${port}/ws-subclass`) instanceof Sub); }
                    catch (e) { out.subclass = "threw " + e.name; }
                    const frame = document.createElement("iframe");
                    frame.srcdoc = `<script>try { new WebSocket("ws://0.0.0.0:${port}/ws-frame-refused") } catch (e) {}
                        try { new WebSocket("ws://127.0.0.1:${port}/ws-frame-admitted") } catch (e) {}<\\/script>`;
                    document.documentElement.appendChild(frame);
                    fetch(`http://127.0.0.1:${port}/fetch-admitted`).catch(() => 0);
                    if (navigator.serviceWorker) navigator.serviceWorker.register("/sw.js").catch(() => 0);
                    return out;
                }""",
                sink.port,
            )
            page.wait_for_timeout(2500)
        finally:
            ctx.close()
        return {h[1] for h in sink.hits} - {"/favicon.ico"}, ctor

    try:
        unguarded, plain = drive(False)
        guarded, ctor = drive(True)
        isolated, _ = drive(True, service_workers="block")
        unheard, deaf_ctor = drive(True, deaf=True)
    finally:
        browser.close()
        if pw is not None:
            pw.stop()
    assert {"/popup", "/ws-refused", "/ws-frame-refused"} <= unguarded and plain["admitted"] == "ok", (
        f"the probe cannot say yes ({backend}): an unguarded page did not reach the sink: {sorted(unguarded)} {plain}"
    )
    assert ctor["admitted"] == "ok" and ctor["subclass"] == "true", (
        f"BD-SEC-3-R6-F1-ADMITTED-WEBSOCKET-BROKEN ({backend}/{mechanism}): {ctor}"
    )
    assert {"/start", "/ws-admitted", "/ws-subclass", "/ws-frame-admitted", "/fetch-admitted"} <= guarded, (
        f"BD-SEC-3-R4-F3-NEG-ADMITTED-TRAFFIC-BLOCKED ({backend}): {sorted(guarded)}"
    )
    assert "/popup" not in guarded, f"BD-SEC-3-R4-F3-REAL-POPUP-REACHED-THE-WIRE ({backend}): {sorted(guarded)}"
    assert ctor["refused"] == refused_ctor and not ({"/ws-refused", "/ws-frame-refused", "/ws-mapped0"} & guarded), (
        f"BD-SEC-3-R4-F3-REAL-WEBSOCKET-REACHED-THE-WIRE ({backend}/{mechanism}): {ctor} {sorted(guarded)}"
    )
    # a NAME that only the judge's lookup can refuse: the tables in the page script do not know it, so this
    # row fails when the page stops asking the judge (unguarded: the constructor itself is fine).  Under
    # ``route`` nothing throws: the judge was asked when it reported the refusal.
    assert plain["named"] == "ok" and ctor["named"] == refused_ctor, (
        f"BD-SEC-3-R6-F1-NAMED-WEBSOCKET-NOT-JUDGED ({backend}/{mechanism}): unguarded={plain} guarded={ctor}"
    )
    assert any("/ws-named" in url for url in told) and any("/ws-refused" in url for url in told), (
        f"BD-SEC-3-R7-B-REFUSED-WEBSOCKET-NOT-REPORTED ({backend}/{mechanism}): {sorted(set(told))}"
    )
    assert not any(WS_VERDICT_PATH in path for path in guarded), (
        f"BD-SEC-3-R6-F1-VERDICT-REQUEST-SENT ({backend}): {sorted(guarded)}"
    )
    # r5 (R2): a service worker's socket is outside every route -- the positive control is the guarded
    # context that still ALLOWS service workers; the login/verify/macro/takeover contexts block them.
    assert "/sw-ws" in guarded, (
        f"the probe cannot say yes ({backend}): a service worker socket did not reach the sink: {sorted(guarded)}"
    )
    assert not ({"/sw-ws", "/sw-fetch", "/popup", "/ws-refused"} & isolated), (
        f"BD-SEC-3-R5-R2-SERVICE-WORKER-REACHED-THE-WIRE ({backend}): {sorted(isolated)}"
    )
    assert {"/start", "/ws-admitted", "/fetch-admitted"} <= isolated, sorted(isolated)
    # limit WS-VERDICT-CHANNEL: with no judge answering, the page script still refuses what needs no lookup
    # and still opens an admitted socket.  (Playwright's WebSocket route does not depend on the page route.)
    assert deaf_ctor["refused"] == refused_ctor and "/ws-refused" not in unheard, (
        f"BD-SEC-3-R6-F1-STATIC-FALLBACK ({backend}/{mechanism}): {deaf_ctor} {sorted(unheard)}"
    )
    # r7 F2 (lens R54-B): the no-judge fallback DECODES a literal (same tables as the Python judge) instead
    # of comparing strings: a mapped / 6to4 / multicast literal is refused, a LAN literal still opens.
    decoded = {k: deaf_ctor[k] for k in ("mapped0", "mappedmeta", "sixtofour", "multicast")}
    assert set(decoded.values()) == {refused_ctor} and not ({"/ws-mapped0", "/ws-mappedmeta"} & unheard), (
        f"BD-SEC-3-R7-F2-LITERAL-NOT-DECODED ({backend}/{mechanism}): {decoded} {sorted(unheard)}"
    )
    assert deaf_ctor["lanliteral"] == "ok" and "/ws-lanliteral" in unheard, (
        f"BD-SEC-3-R7-F2-NEG-LAN-LITERAL-REFUSED ({backend}/{mechanism}): {deaf_ctor} {sorted(unheard)}"
    )
    assert deaf_ctor["admitted"] == "ok" and "/ws-admitted" in unheard, (
        f"BD-SEC-3-R6-F1-ADMITTED-WEBSOCKET-BROKEN ({backend}, no judge): {deaf_ctor} {sorted(unheard)}"
    )


# ---- r7 (PM 23:45Z option B; lens R54-B F2 / F3) ---------------------------------------------------------------------
class WsRoutePage(ScriptPage):
    """A page that has Playwright's WebSocket route."""

    def __init__(self):
        super().__init__()
        self.ws_routes = []

    def route_web_socket(self, matcher, handler):
        self.ws_routes.append((matcher, handler))


def test_only_a_caller_that_names_plain_playwright_gets_the_websocket_route():
    """cloakbrowser lacks the binding Playwright's WebSocket route needs: registering it there makes EVERY
    WebSocket constructor throw (the r5 regression).  So the route is used only on positive word from the
    caller; cloakbrowser, an attached browser and an unnamed backend get the page script."""
    for backend in (None, "cloakbrowser", "attached", "Playwright", ""):
        page = WsRoutePage()
        page.context = WsRoutePage()
        og.install_page_guard(page, **({} if backend is None else {"backend": backend}))
        for target in (page, page.context):
            assert not target.ws_routes and len(target.scripts) == 1, (
                f"BD-SEC-3-R7-B-CLOAK-GETS-WS-ROUTE: backend={backend!r} "
                f"ws_routes={len(target.ws_routes)} scripts={len(target.scripts)}"
            )
    seen = []
    page = WsRoutePage()
    page.context = WsRoutePage()
    _install(page, "route", on_refuse=lambda url, why: seen.append(url))
    for target in (page, page.context):
        assert len(target.ws_routes) == 1 and not target.scripts and len(target.routes) == 1, (
            f"BD-SEC-3-R7-B-PLAYWRIGHT-WITHOUT-WS-ROUTE: ws_routes={len(target.ws_routes)} scripts={len(target.scripts)}"
        )
        matcher, handler = target.ws_routes[0]
        for url in (
            f"ws://{METADATA}/s",
            "wss://metadata.example/s",
            "ws://0.0.0.0:9/s",
            "ws://[::ffff:0.0.0.0]/s",
            "wss://metadata.google.internal/s",
            "ws://[fd00:ec2::254%2]/s",
            "http://public.example/x",
            "",
        ):
            assert matcher(url) is True, f"BD-SEC-3-R7-B-WS-ROUTE-ADMITS-REFUSED: {url!r}"
        for url in ("wss://public.example/live", "ws://lan.example:8096/socket", "ws://127.0.0.1:5555/ws"):
            assert matcher(url) is False, f"BD-SEC-3-R7-B-WS-ROUTE-REFUSES-ADMITTED: {url}"
        connected = []
        handler(types.SimpleNamespace(url=f"ws://{METADATA}/s", connect_to_server=lambda: connected.append(1)))
        assert not connected, "BD-SEC-3-R7-B-REFUSED-WEBSOCKET-CONNECTED"
    assert seen.count(f"ws://{METADATA}/s") == 2, f"BD-SEC-3-R7-B-REFUSED-WEBSOCKET-NOT-REPORTED: {seen}"
    original = og.page_request_refusal
    og.page_request_refusal = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert page.ws_routes[0][0]("wss://public.example/live") is True, "BD-SEC-3-R7-B-UNDECIDED-WEBSOCKET-OPENED"
    finally:
        og.page_request_refusal = original
    old = Page()  # a Playwright without route_web_socket (< 1.48) and no init script: the http route still goes on
    _install(old, "route")
    assert len(old.routes) == 1
    older = ScriptPage()  # no WebSocket route: the page script is the fallback
    _install(older, "route")
    assert len(older.scripts) == 1, "BD-SEC-3-R7-B-NO-WEBSOCKET-GUARD-AT-ALL"


def test_the_seams_that_know_their_backend_tell_the_guard():
    """The four own-browser seams that hold the backend name BD's cloak wrapper returned pass it on; a seam
    that does not know (runner context, macro replay) gets the page script."""
    import ast
    import pathlib

    root = pathlib.Path(og.__file__).resolve().parent
    for rel, name in (
        ("login_impl/submit.py", "backend"),
        ("login_impl/manual.py", "backend"),
        ("login_impl/replay.py", "backend"),
        ("takeover_vnc.py", "_backend"),
    ):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "install_page_guard"
        ]
        assert len(calls) == 1, f"{rel}: {len(calls)} install_page_guard call(s)"
        passed = {kw.arg: getattr(kw.value, "id", None) for kw in calls[0].keywords}
        assert passed.get("backend") == name, f"BD-SEC-3-R7-B-SEAM-DOES-NOT-NAME-ITS-BACKEND: {rel} passes {passed}"


def _literal_corpus():
    """Address literals around every boundary of the refusal tables, every IPv4-carrying IPv6 form of a refused
    and an admitted IPv4, the metadata addresses, and a seeded random fill.  (text, python verdict)."""
    import ipaddress
    import random

    rng = random.Random(7)
    v4 = [
        ipaddress.IPv4Address(x)
        for x in (
            "0.0.0.0", "0.0.0.1", "169.253.255.255", "169.254.0.0", "169.254.169.254", "169.254.170.2",
            "169.254.255.255", "169.255.0.0", "100.100.100.200", "100.100.100.201", "192.0.0.192", "168.63.129.16",
            "10.0.0.7", "10.0.0.230", "192.168.1.20", "127.0.0.1", "8.8.8.8", "223.255.255.255", "224.0.0.0",
            "224.0.0.1", "239.255.255.255", "240.0.0.0", "240.0.0.1", "255.255.255.255",
        )
    ] + [ipaddress.IPv4Address(rng.getrandbits(32)) for _ in range(60)]  # fmt: skip
    found = list(v4)
    for a in v4:
        n = int(a)
        found += [
            ipaddress.IPv6Address(v)
            for v in (
                (0xFFFF << 32) | n,
                n,
                (0xFFFF0000 << 32) | n,
                (0x2002 << 112) | (n << 80) | rng.getrandbits(80),
                (0x20010000 << 96) | (n << 64) | rng.getrandbits(64),
                (0x20010000 << 96) | (rng.getrandbits(64) << 32) | (n ^ 0xFFFFFFFF),
                (0x64FF9B << 96) | n,
                (0x64FF9B0001 << 80) | ((n >> 16) << 64) | ((n & 0xFFFF) << 40),
                (0x64FF9B0001 << 80) | ((n >> 24) << 64) | ((n & 0xFFFFFF) << 32),
                (0x64FF9B0001 << 80) | (n << 24),
                (0x64FF9B0001 << 80) | n,
            )
        ]
    found += [
        ipaddress.IPv6Address(x)
        for x in (
            "::", "::1", "::2", "fd00:ec2::254", "fd00:ec2::23", "fd20:ce::254", "fd00:ec2::1", "fd00::1",
            "fe7f:ffff::1", "fe80::", "fe80::1", "fe90::1", "febf:ffff::1", "fec0::1", "feff::1", "ff00::", "ff02::1",
            "2606:4700::1111", "64:ff9b:1::", "2001:db8::a9fe:a9fe", "2001::", "2002::",
        )
    ] + [ipaddress.IPv6Address(rng.getrandbits(128)) for _ in range(60)]  # fmt: skip
    return [(f"[{a}]" if a.version == 6 else str(a), og.never_admissible(a) is not None) for a in dict.fromkeys(found)]


def test_the_refusal_table_the_page_script_reads_says_what_the_python_judge_says():
    """r7: the page script decides literals from ``_common.NEVER_ADMISSIBLE_NETWORKS``; ``never_admissible``
    decides the same four classes from the address properties.  The table must say exactly what the
    properties say -- on the address and on every IPv4 it carries."""
    import ipaddress

    table = og.never_admissible.__globals__["literal_refusal_table"]()
    ranges = [(version, int(low), int(high)) for version, low, high in table["ranges"]]
    embedded = og.never_admissible.__globals__["embedded_ipv4"]
    corpus = _literal_corpus()
    drift = []
    for text, _ in corpus:
        a = ipaddress.ip_address(text.strip("[]"))
        by_table = any(
            version == view.version and low <= int(view) <= high
            for view in (a, *embedded(a))
            for version, low, high in ranges
        )
        by_property = og.never_admissible(a) in ("unspecified", "multicast", "link-local", "reserved")
        if by_table != by_property and og.never_admissible(a) not in ("cloud-metadata", "zone-id"):
            drift.append(text)
    assert not drift, f"BD-SEC-3-R7-TABLE-DRIFT: {drift[:8]}"
    assert sum(1 for _, refused in corpus if refused) > 250 and sum(1 for _, refused in corpus if not refused) > 250
    # 2851995648 = 169.254.0.0: the script carries the table's own bounds, not a list of strings
    assert '"2851995648"' in og.ws_guard_script(), "BD-SEC-3-R7-SCRIPT-NOT-FED-FROM-THE-TABLE"


@pytest.mark.parametrize("backend", ["chromium", "cloak"])
def test_the_page_script_decodes_literals_exactly_like_the_python_judge(backend):
    """r7 F2 (lens R54-B: the no-judge fallback was 16 strings + 2 prefixes).  The script's literal decoder runs
    in the real browser over the corpus -- every host in the form the browser's own URL parser gives it -- and
    must agree with ``never_admissible`` row for row; under PUBLIC_ONLY it must refuse every literal."""
    import json

    corpus = _literal_corpus()
    browser, pw = _launch(backend)

    def verdicts(policy):
        ctx = browser.new_context()
        try:
            page = ctx.new_page()
            og.install_page_guard(page, policy)
            page.goto("about:blank")  # a new document: the init script runs; nothing is fetched
            return page.evaluate(
                """(rows) => rows.map((text) => {
                    const judge = WebSocket.__bdLiteralRefusal;
                    return judge ? judge(new URL("ws://" + text + "/").hostname) : null;
                })""",
                [text for text, _ in corpus] + ["metadata.google.internal", "METADATA.GOOG.", "public.example"],
            )
        finally:
            ctx.close()

    try:
        lan, public = verdicts(og.LAN_OK), verdicts(og.PUBLIC_ONLY)
    finally:
        browser.close()
        if pw is not None:
            pw.stop()
    wrong = [(text, want, got) for (text, want), got in zip(corpus, lan) if got is not want]
    assert not wrong, (
        f"BD-SEC-3-R7-F2-LITERAL-PARITY ({backend}): {len(wrong)} of {len(corpus)}: {json.dumps(wrong[:6])}"
    )
    assert lan[-3:] == [True, True, False], f"BD-SEC-3-R7-F2-METADATA-NAME ({backend}): {lan[-3:]}"
    admitted = [text for (text, _), got in zip(corpus, public) if got is not True]
    assert not admitted, f"BD-SEC-3-R7-F2-PUBLIC-ONLY-LITERAL-ADMITTED ({backend}): {admitted[:6]}"
    assert public[-1] is False  # a name is still the judge's to decide


_POPUP_PAGE_JS = """(port) => { setTimeout(() => {
    const w = window.open("about:blank"); const r = {};
    for (const [k, u] of [["admitted", `ws://127.0.0.1:${port}/ws-pop-admitted`],
                          ["refused", `ws://0.0.0.0:${port}/ws-pop-refused`],
                          ["named", `ws://judge-only.example:${port}/ws-pop-named`]]) {
        try { new w.WebSocket(u); r[k] = "ok"; } catch (e) { r[k] = "threw " + e.name; }
    }
    window.__r = r; }, 200); }"""
_POPUP_DRIVER = """
import http.server, json, socket, sys, threading
from bulk_downloader import outbound_guard as og
real = socket.getaddrinfo
def fake(host, port, *a, **k):  # hermetic: one name the judge refuses, literals pass, nothing else resolves
    if host == "judge-only.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port or 0))]
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in str(host) else socket.AF_INET, str(host))
    except OSError:
        raise socket.gaierror(socket.EAI_NONAME, "fake resolver") from None
    return real(host, port, *a, **k)
og._resolve = lambda host, port: fake(host, port, type=socket.SOCK_STREAM)
hits = []
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        hits.append(self.path); body = b"<html>x</html>"
        self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("0.0.0.0", 0), H); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
backend, mechanism, page_js = sys.argv[1], sys.argv[2], sys.argv[3]
args = ["--no-sandbox", "--disable-dev-shm-usage"]
if backend == "cloak":
    from bulk_downloader import runner_browser
    browser, pw, used = runner_browser._cloak.launch_browser(headless=True, args=args, config={})
    if used != "cloakbrowser":
        print(json.dumps({"launch": used})); sys.exit(3)
else:
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start(); browser = pw.chromium.launch(headless=True, args=args)
ctx = browser.new_context()
if mechanism == "route":
    og.install_page_guard(ctx, backend=og.PLAYWRIGHT_BACKEND)
elif mechanism == "script":
    og.install_page_guard(ctx)
page = ctx.new_page(); page.goto(f"http://127.0.0.1:{port}/start")
page.evaluate(page_js, port)
page.wait_for_timeout(3000)
seen = {"ctor": page.evaluate("window.__r"), "sink": sorted(h for h in set(hits) if h.startswith("/ws-"))}
print(json.dumps(seen), flush=True)
browser.close()
if pw: pw.stop()
"""


@pytest.mark.parametrize("backend,mechanism", [("chromium", "off"), ("cloak", "off")] + MECHANISMS)
def test_a_websocket_on_an_about_blank_popup_does_not_freeze_the_page(backend, mechanism):
    """r7 F3 (lens R54-B, regression vs BASE): the page's own timer opens about:blank and constructs a
    WebSocket on the popup's realm.  In r6 the popup realm's synchronous verdict request never returned and
    the opener froze for good.  A frozen page cannot be asserted on from inside, so the browser runs in a
    child process with a hard timeout.  ``off`` = no guard: the positive control (both sockets at the sink)."""
    import importlib.util
    import json
    import pathlib
    import subprocess
    import sys

    if backend == "cloak" and importlib.util.find_spec("cloakbrowser") is None:
        pytest.skip("cloakbrowser (requirements-cloak.txt) is not installed in this venv")
    if mechanism == "route" and not hasattr(og, "PLAYWRIGHT_BACKEND"):
        pytest.fail("BD-SEC-3-R7-B-NO-ROUTE-MECHANISM: install_page_guard cannot be told the backend")
    root = pathlib.Path(og.__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            [sys.executable, "-B", "-c", _POPUP_DRIVER, backend, mechanism, _POPUP_PAGE_JS],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=75,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"BD-SEC-3-R7-F3-POPUP-FREEZE ({backend}/{mechanism}): the page never answered again (75 s)")
    assert proc.returncode == 0, (
        f"popup driver failed ({backend}/{mechanism}) rc={proc.returncode}: {proc.stderr[-600:]}"
    )
    seen = json.loads(proc.stdout.strip().splitlines()[-1])
    ctor, at_sink = seen["ctor"], set(seen["sink"])
    if mechanism == "off":
        assert at_sink == {"/ws-pop-admitted", "/ws-pop-refused"} and set(ctor.values()) == {"ok"}, (
            f"the probe cannot say yes ({backend}): {seen}"
        )
        return
    assert ctor["admitted"] == "ok" and "/ws-pop-admitted" in at_sink, (
        f"BD-SEC-3-R7-F3-POPUP-ADMITTED-WEBSOCKET-BROKEN ({backend}/{mechanism}): {seen}"
    )
    refused_ctor = "threw SecurityError" if mechanism == "script" else "ok"
    assert "/ws-pop-refused" not in at_sink and ctor["refused"] == refused_ctor, (
        f"BD-SEC-3-R7-F3-POPUP-REFUSED-WEBSOCKET-REACHED-THE-WIRE ({backend}/{mechanism}): {seen}"
    )
    # the popup still reaches the JUDGE (through its opener's realm), not only the script's own literal tables
    assert ctor["named"] == refused_ctor, f"BD-SEC-3-R7-F3-POPUP-NAME-NOT-JUDGED ({backend}/{mechanism}): {seen}"


# ---- r4: lens advisory survivors ------------------------------------------------------------------------------------
def test_a_refused_origin_stays_refused_from_the_verdict_cache():
    judge = og.page_guard()
    first, again, other = Route(), Route(), Route()
    assert judge(first, _request(f"http://{METADATA}/a")) is True
    assert judge(again, _request(f"http://{METADATA}/b")) is True, "BD-SEC-3-R4-CACHE-ADMITS-A-REFUSED-ORIGIN"
    assert (first.action, again.action) == ("abort", "abort")
    assert judge(other, _request("https://public.example/")) is False and other.action is None


def test_an_empty_or_non_ip_dns_answer_is_refused(monkeypatch):
    monkeypatch.setattr(og, "_resolve", lambda host, port: [])
    with pytest.raises(og.OutboundRefused) as exc:
        og.check_url("http://empty.example/")
        pytest.fail("BD-SEC-3-R4-EMPTY-ANSWER-ADMITTED")
    assert exc.value.reason == "no-address", "BD-SEC-3-R4-EMPTY-ANSWER-ADMITTED"
    assert og.check_url("http://empty.example/", unresolved_ok=True) == ()
    monkeypatch.setattr(
        og,
        "_resolve",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", port))],
    )
    for unresolved_ok in (False, True):
        with pytest.raises(og.OutboundRefused):
            og.check_url("http://odd.example/", unresolved_ok=unresolved_ok)
            pytest.fail("BD-SEC-3-R4-NON-IP-ANSWER-ADMITTED")


def test_which_seams_leave_an_unresolvable_name_to_the_fetcher(monkeypatch, tmp_path):
    """ffmpeg resolves the manifest host itself (proxy lanes): BD does not refuse a name it cannot resolve.
    Stash is another machine: an unresolvable name is NOT handed over."""
    from bulk_downloader import hls_downloader, runner_integrations

    started = []
    monkeypatch.setattr(hls_downloader, "_find_ffmpeg", lambda: "/bin/false")
    for name in ("Popen", "run"):
        monkeypatch.setattr(
            hls_downloader.subprocess,
            name,
            lambda *a, **k: started.append(a) or (_ for _ in ()).throw(OSError("x")),
        )
    res = hls_downloader.download("https://no-such-name.example/a.m3u8", str(tmp_path / "o.mp4"))
    assert res.error != "bad_url" and started, f"BD-SEC-3-R4-HLS-UNRESOLVED-REFUSED: {res}"
    sent = []

    class Runner(runner_integrations.IntegrationsMixin):
        config = {"stash_scrape_preview": True}

        def _get_stash_client(self):
            return types.SimpleNamespace(scrape_url=lambda url: sent.append(url) or {"title": "t"})

    monkeypatch.setattr("bulk_downloader.stash_deep.deep_enabled", lambda cfg: True)
    out = Runner()._stash_scrape_preview("https://no-such-name.example/scene/1")
    assert out is None and sent == [], f"BD-SEC-3-R4-STASH-UNRESOLVED-SENT: {sent}"


# ---- r5 R1: an IPv6 zone id, and three more IPv6 forms that carry an IPv4 destination -----------------------------
ZONE_HOSTS = [
    "[fd00:ec2::254%2]",  # AWS IPv6 IMDS behind a zone id: str(addr) is "fd00:ec2::254%2"
    "[fd00:ec2::254%252]",
    "[fd00:ec2::23%1]",
    "[fd20:ce::254%1]",
    "[::ffff:100.100.100.200%1]",
    "[::1%2]",  # a zone id on any non-link-local literal: Linux ignores it and dials the bare address
    "[fd00::1%eth0]",
    "[2606:4700::1111%2]",  # a public literal: only the zone id is wrong with it
]
NAT64_LAYOUT_HOSTS = [  # r6 (lens R36-B F2): the /56 and /64 layouts inside 64:ff9b:1::/48, on the same seams
    "[64:ff9b:1:a9:fe:a9fe::]",
    "[64:ff9b:1:64:64:64c8::]",
    "[64:ff9b:1:0:64:6464:c800:0]",
    "[64:ff9b:1:0:a8:3f81:1000:0]",
]


@pytest.mark.parametrize("host", ZONE_HOSTS + NAT64_LAYOUT_HOSTS)
def test_a_zone_id_hides_no_address_on_any_seam(host, monkeypatch):
    from urllib.request import Request

    from bulk_downloader import ai_provider, aiassist
    from bulk_downloader.dev_suite import audit_security

    tag = "BD-SEC-3-R5-R1-ZONE-ID-ADMITTED" if "%" in host else "BD-SEC-3-R6-F2-NAT64-LAYOUT-ADMITTED"
    dialled = []

    def refuse_to_dial(address, *args, **kwargs):
        dialled.append(address)
        raise OSError("the test opens no socket")

    monkeypatch.setattr(socket, "create_connection", refuse_to_dial)
    url = f"http://{host}:8080/latest/"
    for policy in (og.LAN_OK, og.PUBLIC_ONLY):
        assert og.url_refusal(url, policy, unresolved_ok=True), (
            f"{tag} check_url ({policy}): {url}"
        )
    assert og.page_request_refusal(url), f"{tag} page: {url}"
    assert og.page_request_refusal("ws" + url[4:]), f"{tag} websocket: {url}"
    for name, fetch in (
        ("outbound_guard.urlopen", lambda: og.urlopen(Request(url), timeout=2)),
        ("ai_provider.urlopen", lambda: ai_provider.urlopen(Request(url), timeout=2)),
    ):
        with pytest.raises(urllib.error.URLError):
            fetch()
        assert dialled == [], f"{tag} {name}: dialled {dialled}"
    with pytest.raises(og.OutboundRefused):
        og.connect(host.strip("[]"), 8080, timeout=2)
        pytest.fail(f"{tag} outbound_guard.connect: {host}")
    out = audit_security.tls_cert_check(hosts=[host.strip("[]")], timeout=1)
    assert dialled == [], f"{tag} tls_cert_check: dialled {dialled} {out}"
    ok, why = aiassist.validate_endpoint(url, provider="ollama")
    assert ok is False, f"{tag} ollama validator: {url} -> {why}"
    with pytest.raises(RuntimeError):
        aiassist._pin_lan_endpoint(url)
        pytest.fail(f"{tag} ollama pin: {url}")


def test_the_ollama_validator_refuses_an_answer_it_cannot_judge(monkeypatch):
    from bulk_downloader import aiassist

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("not-an-ip", 0))],
    )
    ok, why = aiassist.validate_endpoint("http://odd.example:11434", provider="ollama")
    assert ok is False, f"BD-SEC-3-R5-R1-OLLAMA-UNJUDGEABLE-ADMITTED: {why}"


def test_a_zone_id_on_a_link_local_address_is_still_link_local():
    assert "link-local" in og.url_refusal("http://[fe80::1%2]/")
    assert og.url_refusal("http://[fd00::1]:8096/") is None  # NEG: the unscoped LAN literal


EMBEDDED_FORMS = [
    "http://[::169.254.169.254]/",  # IPv4-compatible
    "http://[::100.100.100.200]/",
    "http://[::ffff:0:169.254.169.254]/",  # IPv4-translated (SIIT)
    "http://[::ffff:0:100.100.100.200]/",
    "http://[64:ff9b:1::a9fe:a9fe]/",  # local-use NAT64, /96 inside the /48
    "http://[64:ff9b:1::6464:64c8]/",
    "http://[64:ff9b:1:a9fe:a9:fe00::]/",  # local-use NAT64, the RFC 6052 /48 layout
    "http://[64:ff9b:1:6464:64:c800::]/",
    "http://[64:ff9b:1:c0a8:1:1400:a9fe:a9fe]/",  # both readings name an address: BOTH are judged
    "http://[64:ff9b:1:a9:fe:a9fe::]/",  # r6: the /56 layout of 169.254.169.254
    "http://[64:ff9b:1:64:64:64c8::]/",  # /56 of 100.100.100.200
    "http://[64:ff9b:1:a8:3f:8110::]/",  # /56 of 168.63.129.16
    "http://[64:ff9b:1:0:a9:fea9:fe00:0]/",  # the /64 layout of 169.254.169.254
    "http://[64:ff9b:1:0:64:6464:c800:0]/",  # /64 of 100.100.100.200
    "http://[64:ff9b:1:0:a8:3f81:1000:0]/",  # /64 of 168.63.129.16
    "http://[::0.0.0.0]/",
    "http://[64:ff9b:1::]/",  # carries 0.0.0.0 in either layout
]


@pytest.mark.parametrize("url", EMBEDDED_FORMS)
def test_more_ipv6_forms_that_carry_an_ipv4_destination_are_unpacked(url):
    for policy in (og.LAN_OK, og.PUBLIC_ONLY):
        assert og.url_refusal(url, policy), f"BD-SEC-3-R5-R1-EMBEDDED-FORM-ADMITTED ({policy}): {url}"
    assert og.page_request_refusal(url), f"BD-SEC-3-R5-R1-EMBEDDED-FORM-ADMITTED (page): {url}"


def test_the_new_unpacking_refuses_nothing_that_was_a_legitimate_peer():
    for url in (
        "http://[::1]:5555/",  # loopback is not "IPv4-compatible 0.0.0.1"
        "http://[::10.0.0.7]/",  # carries a LAN address: LAN_OK
        "http://[64:ff9b:1::c0a8:114]/",  # NAT64 of 192.168.1.20
        "http://[64:ff9b:1:c0a8:1:1400::]/",  # the same, RFC 6052 /48 layout
        "http://[64:ff9b:1:a:0:7::]/",  # 10.0.0.7, /56 layout
        "http://[64:ff9b:1:0:a:0:700:0]/",  # 10.0.0.7, /64 layout
        "http://[2001:db8::a9fe:a9fe]/",  # not a carrying prefix at all
        "http://[fd00::a9fe:a9fe]/",
    ):
        assert og.url_refusal(url) is None, f"BD-SEC-3-R5-R1-NEG-OVER-REFUSAL: {url}"



# ---- r8 (r7 REFUTE F1, lens R24-C): an about:blank popup's HTTP ------------------------------------------------------
_POPUP_OPENERS = {
    # the page's own load script, no gesture: the popup's request comes from the popup's realm
    "/pop-direct": (
        '<!doctype html><script>const w = open("about:blank"); if (w) {'
        ' w.fetch("http://0.0.0.0:PORT/pop-refused").catch(() => 0);'
        ' w.fetch("http://127.0.0.1:PORT/pop-admitted").catch(() => 0); }</script>'
    ),
    # one level down: an http popup whose own load script opens the about:blank popup
    "/pop-lvl2": '<!doctype html><script>open("/pop-direct");</script>',
    # race-r6 F1 (lens R74-B): sandboxed srcdoc frames, each fetching from its own (opaque-origin) realm
    # r9 (lens R116-D F1): requests made after load, while the takeover browser's thread idles
    "/takeover-idle": (
        "<!doctype html><script>setTimeout(() => {"
        ' fetch("http://0.0.0.0:PORT/idle-refused").catch(() => 0);'
        ' fetch("http://127.0.0.1:PORT/idle-admitted").catch(() => 0); }, 1000)</script>'
    ),
    "/sbx-frames": "<!doctype html><body>" + "".join(
        f'<iframe sandbox="allow-scripts" srcdoc="<script>fetch(&quot;http://0.0.0.0:PORT/sbx-refused-{i}&quot;)'
        f'.catch(() => 0); fetch(&quot;http://127.0.0.1:PORT/sbx-admitted-{i}&quot;).catch(() => 0);</script>">'
        "</iframe>"
        for i in range(8)
    ),
}


def _drive_popup(browser, sink, shape, start):
    """Load ``start``, then close the pages in the order they were opened, the opener first.  Playwright never
    hands an about:blank popup's request to a route: it holds it while the opener lives and the browser sends
    it the moment the opener closes.  Events are pumped on a page of ANOTHER context (sync Playwright only
    answers while the thread is inside a Playwright call), so a held request is released and judged here,
    not left parked."""
    del sink.hits[:]
    idle = browser.new_context()
    ctx = browser.new_context()
    try:
        pump = idle.new_page()
        page = ctx.new_page()
        if shape == "page":
            og.install_page_guard(page)
        elif shape == "context":
            og.install_page_guard(ctx)
        page.goto(f"http://127.0.0.1:{sink.port}{start}")
        pump.wait_for_timeout(2000)
        opened = len(ctx.pages)
        for each in list(ctx.pages):
            each.close()
            pump.wait_for_timeout(2000)
    finally:
        ctx.close()
        idle.close()
    return opened, {h[1] for h in sink.hits if h[1].startswith("/pop-")}


@pytest.mark.parametrize("backend", ["chromium", "cloak"])
def test_an_about_blank_popups_http_is_judged_not_released_when_its_opener_closes(sink, backend):
    """r7 REFUTE F1 (lens R24-C): the request an about:blank popup makes was never handed to the route; it was
    held while the opener lived and sent UNJUDGED when the opener closed -- both backends, page and context
    installs, gesture-free.  ``/pop-lvl2`` is the same popup one level down (opened by an http popup's own
    load script): a guard attached to each new page in turn is too late for it.  Unguarded = the positive
    control; the admitted request from the same popup must still be sent."""
    browser, pw = _launch(backend)
    try:
        runs = {(shape, start): _drive_popup(browser, sink, shape, start)
                for shape in ("off", "page", "context") for start in ("/pop-direct", "/pop-lvl2")}
    finally:
        browser.close()
        if pw is not None:
            pw.stop()
    for start, pages in (("/pop-direct", 2), ("/pop-lvl2", 3)):
        opened, plain = runs[("off", start)]
        assert opened == pages and {"/pop-refused", "/pop-admitted"} <= plain, (
            f"the probe cannot say yes ({backend} {start}): pages={opened} reached={sorted(plain)}"
        )
    guarded = {key: reached for key, (_, reached) in sorted(runs.items()) if key[0] != "off"}
    released = [key for key, reached in guarded.items() if "/pop-refused" in reached]
    assert not released, f"BD-SEC-3-R8-F1-POPUP-REQUEST-RELEASED-UNJUDGED ({backend}): {released}"
    blocked = [key for key, reached in guarded.items() if "/pop-admitted" not in reached]
    assert not blocked, f"BD-SEC-3-R8-F1-NEG-ADMITTED-POPUP-REQUEST-BLOCKED ({backend}): {blocked}"


def _takeover_idle_hits(monkeypatch, sink, backend, guard):
    """Run the REAL ``takeover_vnc`` session (its own thread, its own idle loop) on a headless browser of
    ``backend``, open ``/takeover-idle`` and return the ``/idle-*`` paths the sink heard within 10 s of the
    session going idle.  ``guard=False`` is the positive control (the BASE shape: no page guard)."""
    import contextlib
    import time

    from bulk_downloader import cloak, netns_isolation, takeover_vnc

    real_launch = cloak.launch_browser

    def launch(**_kw):  # on the session's thread: sync Playwright is thread-bound
        if backend == "cloak":  # the default backend, headless instead of on X
            browser, pw, used = real_launch(headless=True)
            if used != "cloakbrowser":
                browser.close()
                if pw is not None:
                    pw.stop()
                raise RuntimeError(f"the default cloak backend did not launch here (got {used!r})")
            return browser, pw, used
        browser, pw = _launch(backend)
        return browser, pw, og.PLAYWRIGHT_BACKEND

    monkeypatch.setattr(cloak, "launch_browser", launch)
    monkeypatch.setattr(netns_isolation, "capture_netns", lambda *a, **k: contextlib.nullcontext(None))
    if not guard:
        monkeypatch.setattr(og, "install_page_guard", lambda *a, **k: None)
    del sink.hits[:]
    session = takeover_vnc.VncTakeoverSession(
        {}, f"r9-idle-{backend}", ":0", url=f"http://127.0.0.1:{sink.port}/takeover-idle"
    ).start(timeout=60)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(h[1] == "/idle-admitted" for h in sink.hits):
            time.sleep(0.2)
        time.sleep(1)
        return {h[1] for h in sink.hits if h[1].startswith("/idle-")}
    finally:
        session.stop(timeout=20)


@pytest.mark.parametrize("backend", ["chromium", "cloak"])
def test_the_takeover_browser_judges_and_sends_requests_while_it_idles(sink, backend, monkeypatch):
    """r8 REFUTE F1 (lens R116-D): the captcha VNC takeover session idled in ``Event.wait`` after its first
    goto, outside any Playwright call, so the guard's route/Fetch handlers never ran and every request the
    operator's clicks made was held for the whole session.  The idle loop must pump Playwright: an admitted
    request made after load is sent, a refused one still never reaches the wire.  Unguarded = the positive
    control (both are sent while the session idles)."""
    import importlib.util

    if backend == "cloak" and importlib.util.find_spec("cloakbrowser") is None:
        pytest.skip("cloakbrowser (requirements-cloak.txt) is not installed in this venv")
    plain = _takeover_idle_hits(monkeypatch, sink, backend, guard=False)
    assert plain == {"/idle-refused", "/idle-admitted"}, f"the probe cannot say yes ({backend}): {sorted(plain)}"
    monkeypatch.undo()
    guarded = _takeover_idle_hits(monkeypatch, sink, backend, guard=True)
    assert "/idle-admitted" in guarded, f"BD-SEC-3-R9-F1-TAKEOVER-IDLE-REQUEST-HELD ({backend}): {sorted(guarded)}"
    assert "/idle-refused" not in guarded, (
        f"BD-SEC-3-R9-F1-NEG-TAKEOVER-IDLE-REFUSED-SENT ({backend}): {sorted(guarded)}"
    )


def _owner_idle_hits(session, sink, backend, guard):
    """Run the REAL ``_run`` loop of a human-operated session (its own thread, its own idle tick) with only
    ``_launch`` replaced: a headless browser of ``backend`` whose context carries the session's own guard shape,
    opened on ``/takeover-idle``.  No command is sent; return the ``/idle-*`` paths the sink heard within 10 s
    of the session going idle.  ``guard=False`` is the positive control (the BASE shape: no route)."""
    import time

    del sink.hits[:]
    port = sink.port

    class Probe(session["cls"]):
        def _launch(self):  # on the session's thread: sync Playwright is thread-bound
            browser, pw = _launch(backend)
            ctx = browser.new_context()
            if guard:
                og.install_page_guard(ctx, **session["guard_kw"](backend))
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{port}/takeover-idle", wait_until="domcontentloaded", timeout=30000)
            return browser, ctx, page, pw

    s = session["make"](Probe)
    try:
        assert s.ready, f"the {session['name']} session did not start ({backend}): {s.error}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(h[1] == "/idle-admitted" for h in sink.hits):
            time.sleep(0.2)
        time.sleep(1)
        return {h[1] for h in sink.hits if h[1].startswith("/idle-")}
    finally:
        s.cancel(timeout=20)


def _manual_login_session():
    from bulk_downloader.login_impl import manual

    return {
        "name": "manual login",
        "cls": manual.ManualLoginSession,
        # login_impl/manual.py _launch: install_page_guard(ctx, backend=backend)
        "guard_kw": lambda backend: {"backend": og.PLAYWRIGHT_BACKEND if backend == "chromium" else "cloakbrowser"},
        "make": lambda cls: cls({"name": "r10-idle"}, ""),  # visible mode, no stored credentials: no autofill
    }


def _manual_download_session():
    from bulk_downloader import runner_manual

    class Runner:
        site_id, config, cookies = "r10-idle", {}, []

    return {
        "name": "manual download",
        "cls": runner_manual._ManualDownloadSession,
        # runner_browser._install_stealth(ctx): install_page_guard(ctx)
        "guard_kw": lambda backend: {},
        "make": lambda cls: cls(Runner(), "http://127.0.0.1/", ""),
    }


@pytest.mark.parametrize("backend", ["chromium", "cloak"])
@pytest.mark.parametrize("make_session", [_manual_login_session, _manual_download_session])
def test_human_operated_sessions_judge_and_send_requests_while_they_idle(sink, backend, make_session):
    """r9 REFUTE F1 (lens A7, adjudicator CONCUR): the r8 F1 class re-entered through the two other
    human-operated headed sessions whose context carries the guard -- manual login (visible mode) and manual
    download/teach idled in ``queue.get(timeout=2)`` + ``browser.is_connected()``, outside any Playwright
    call, so every request their page made after first paint was held until an operator command happened to
    pump.  Their idle tick must pump Playwright: an admitted request made after load is sent with no command
    sent, a refused one still never reaches the wire.  Unguarded = the positive control."""
    import importlib.util

    if backend == "cloak" and importlib.util.find_spec("cloakbrowser") is None:
        pytest.skip("cloakbrowser (requirements-cloak.txt) is not installed in this venv")
    session = make_session()
    plain = _owner_idle_hits(session, sink, backend, guard=False)
    assert plain == {"/idle-refused", "/idle-admitted"}, (
        f"the probe cannot say yes ({session['name']} {backend}): {sorted(plain)}"
    )
    guarded = _owner_idle_hits(session, sink, backend, guard=True)
    assert "/idle-admitted" in guarded, (
        f"BD-SEC-3-R10-F1-OWNER-IDLE-REQUEST-HELD ({session['name']} {backend}): {sorted(guarded)}"
    )
    assert "/idle-refused" not in guarded, (
        f"BD-SEC-3-R10-F1-NEG-OWNER-IDLE-REFUSED-SENT ({session['name']} {backend}): {sorted(guarded)}"
    )


class FakeCdpSession:
    def __init__(self):
        self.sent, self.handlers = [], {}

    def on(self, event, handler):
        self.handlers[event] = handler

    def send(self, method, params=None):
        self.sent.append((method, params or {}))

    def pause(self, url, request_id="r1"):
        self.handlers["Fetch.requestPaused"]({"requestId": request_id, "request": {"url": url}})
        return self.sent[-1]


class FakeBrowser:
    def __init__(self):
        self.sessions = []

    def new_browser_cdp_session(self):
        self.sessions.append(FakeCdpSession())
        return self.sessions[-1]


def test_one_fetch_backstop_per_browser_and_none_on_the_operators_attached_browser():
    """r8: every guarded page or context arms the ONE browser-level backstop of its browser; the attached
    challenge browser (the operator's own Chrome, login_impl/submit) is never intercepted browser-wide."""
    browser = FakeBrowser()
    page = ScriptPage()
    page.context = types.SimpleNamespace(browser=browser, route=lambda *a: None, add_init_script=lambda s: None)
    og.install_page_guard(page)
    og.install_page_guard(page.context)
    assert len(browser.sessions) == 1, f"BD-SEC-3-R8-F1-BACKSTOP-NOT-ONE-PER-BROWSER: {len(browser.sessions)}"
    session = browser.sessions[0]
    assert session.sent[0] == ("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
    attached = FakeBrowser()
    other = ScriptPage()
    other.context = types.SimpleNamespace(browser=attached, route=lambda *a: None, add_init_script=lambda s: None)
    og.install_page_guard(other, backend=og.ATTACHED_BACKEND)
    assert attached.sessions == [], "BD-SEC-3-R8-F1-ATTACHED-BROWSER-INTERCEPTED"


def test_the_backstop_answers_with_the_page_judge():
    """Refused -> failed, admitted -> continued, a WebSocket verdict request -> answered (never sent), an
    undecidable request -> failed; a later PUBLIC_ONLY install makes the browser's backstop stricter."""
    browser = FakeBrowser()
    assert og.install_fetch_backstop(browser) is True
    session = browser.sessions[0]
    assert session.pause(f"http://{METADATA}/x") == (
        "Fetch.failRequest", {"requestId": "r1", "errorReason": "BlockedByClient"}
    ), "BD-SEC-3-R8-F1-BACKSTOP-CONTINUED-A-REFUSED-REQUEST"
    assert session.pause("http://127.0.0.1:5555/ok") == ("Fetch.continueRequest", {"requestId": "r1"}), (
        "BD-SEC-3-R8-F1-NEG-BACKSTOP-FAILED-AN-ADMITTED-REQUEST"
    )
    method, params = session.pause(_ask(f"ws://{METADATA}/s", None) if False else (
        f"http://127.0.0.1:5555{WS_VERDICT_PATH}?u=" + __import__("urllib.parse").parse.quote(f"ws://{METADATA}/s")
    ))
    assert method == "Fetch.fulfillRequest" and params["responseCode"] == 200, (method, params)
    assert __import__("base64").b64decode(params["body"]) == b"no", "BD-SEC-3-R8-F1-BACKSTOP-WS-VERDICT-WRONG"
    for odd in ({"requestId": "r2"}, {"requestId": "r3", "request": "not-a-request"}):  # undecidable events
        session.handlers["Fetch.requestPaused"](odd)
        failed = ("Fetch.failRequest", {"requestId": odd["requestId"], "errorReason": "BlockedByClient"})
        assert session.sent[-1] == failed, f"BD-SEC-3-R8-F1-BACKSTOP-FAILS-OPEN: {odd} -> {session.sent[-1]}"
    assert og.install_fetch_backstop(browser, og.PUBLIC_ONLY) is True and len(browser.sessions) == 1
    assert session.pause("http://127.0.0.1:5555/lan")[0] == "Fetch.failRequest", (
        "BD-SEC-3-R8-F1-BACKSTOP-NOT-STRICTER-AFTER-PUBLIC-ONLY"
    )
    assert og.install_fetch_backstop(object()) is False


@pytest.mark.parametrize("backend", ["chromium", "cloak"])
def test_redirect_hops_and_sandboxed_srcdoc_frames_are_judged_below_the_route(sink, backend):
    """r8, by the same browser-level backstop: (1) A2 (lens R24-C) -- a 302 hop to a refused address is now
    judged for fetch, frame and navigation (Playwright never hands a hop to a route); (2) race-r6 F1 (lens
    R74-B) -- on cloakbrowser a sandboxed srcdoc frame is its own out-of-process target, which the context
    route reaches late or not at all, so its HTTP request could leave unjudged (eight frames per run; the
    race is per frame).  Unguarded = the positive control; it runs before any guard is armed on this browser
    (the backstop is browser-wide).  WebSockets of such a frame are NOT covered here (page realm)."""
    browser, pw = _launch(backend)
    hop = f"http://127.0.0.1:{sink.port}/redir?to=http://0.0.0.0:{sink.port}"
    ok = f"http://127.0.0.1:{sink.port}/redir?to=http://127.0.0.1:{sink.port}"

    def drive(guarded):
        del sink.hits[:]
        ctx = browser.new_context()
        try:
            if guarded:
                og.install_page_guard(ctx)
            page = ctx.new_page()
            page.goto(f"http://127.0.0.1:{sink.port}/sbx-frames")
            page.evaluate(
                """([hop, ok]) => {
                    fetch(hop + "/hop-fetch-refused").catch(() => 0);
                    fetch(ok + "/hop-fetch-admitted").catch(() => 0);
                    const f = document.createElement("iframe"); f.src = hop + "/hop-frame-refused";
                    document.body.appendChild(f);
                }""",
                [hop, ok],
            )
            page.wait_for_timeout(2500)
            try:
                page.goto(hop + "/hop-nav-refused", timeout=8000)
            except Exception:  # noqa: BLE001, S110 -- a refused navigation throws; the sink says what was sent
                pass
            page.wait_for_timeout(1000)
        finally:
            ctx.close()
        return {h[1] for h in sink.hits if not h[1].startswith("/redir")}

    try:
        plain = drive(False)
        guarded = drive(True)
    finally:
        browser.close()
        if pw is not None:
            pw.stop()
    refused = {"/hop-fetch-refused", "/hop-frame-refused", "/hop-nav-refused"} | {f"/sbx-refused-{i}" for i in range(8)}
    admitted = {"/hop-fetch-admitted"} | {f"/sbx-admitted-{i}" for i in range(8)}
    assert refused | admitted <= plain, f"the probe cannot say yes ({backend}): {sorted(refused | admitted - plain)}"
    frames = sorted(p for p in refused & guarded if p.startswith("/sbx-"))
    assert not frames, f"BD-SEC-3-R8-R74-SANDBOXED-SRCDOC-HTTP-REACHED-THE-WIRE ({backend}): {frames}"
    hops = sorted(p for p in refused & guarded if p.startswith("/hop-"))
    assert not hops, f"BD-SEC-3-R8-A2-REDIRECT-HOP-REACHED-THE-WIRE ({backend}): {hops}"
    assert admitted <= guarded, f"BD-SEC-3-R8-NEG-ADMITTED-BLOCKED ({backend}): {sorted(admitted - guarded)}"
