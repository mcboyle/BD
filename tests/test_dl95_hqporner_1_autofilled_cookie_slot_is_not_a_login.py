"""dl95-hqporner-1: a public site with an auto-filled cookie_file slot discovers.

LIVE on test2 (DOT95-LANE/live-dl95-xvideos-1/LIVE-RESULT-A1-A.md #1): hqporner
declares no login, its listing carries 56 /hdporn/ scene cards, and the crawl
found them (status scene_shapes ["/hdporn/<slug>"]) yet still ended
NOT_LOGGED_IN. The effective config reports cookie_file present: app
_save_sites_config fills cookie_file = <BD_HOME>/cookies/<sid>.json into EVERY
site on every save, and _site_is_public counted that bare path as a declared
login. The jar on test2 does not exist.

Real stack: the real _save_sites_config autofill produces the config, a real
browser renders the real hqporner listing (curl of https://hqporner.com/,
2026-09-29, CRLF -> LF), and the real crawl_with_page judges it.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

LISTING = (
    Path(__file__).parent / "fixtures" / "dl95_hqporner_home_listing.html"
).read_bytes()
N_SCENES = 50  # 56 /hdporn/ destinations = 50 scene cards + pager 2..7


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Pager pages of a real listing carry cards too: serve the capture.
        listing = self.path == "/" or self.path.rstrip("/").rsplit("/", 1)[-1].isdigit()
        body = LISTING if listing else b"<html><body>scene</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(autouse=True)
def no_cluster_vault(monkeypatch):
    # save_cookies_to_file publishes to, and load_cookies_from_file falls back
    # on, the host's Redis session vault: keep this module off it.
    from bulk_downloader import vault_sync

    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda *a, **k: None)


@pytest.fixture(scope="module")
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def page(origin):
    from bulk_downloader import cloak

    with cloak.cloaked_page(
        headless=True,
        config={"browser_backend": "playwright"},
        viewport={"width": 1280, "height": 720},
    ) as browser_page:
        # The capture references the site's CDN and ad scripts; stay hermetic.
        browser_page.route(
            "**/*",
            lambda route: (
                route.continue_()
                if route.request.url.startswith(origin)
                else route.abort()
            ),
        )
        yield browser_page


@pytest.fixture
def saved_config(monkeypatch, tmp_path):
    """The hqporner site as the app stores it: no login, then one real save."""
    import importlib

    app = importlib.import_module("bulk_downloader.app")
    sid = "c1d3d9b8"
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setattr(app, "SITES_FILE", tmp_path / "sites_config.json")
    monkeypatch.setattr(
        app, "s_cfg", {sid: {"name": "hqporner", "url": "https://hqporner.com/"}}
    )
    assert app._save_sites_config()
    return sid, app.s_cfg[sid]


def _crawl(page, origin, tmp_path, sid, config, max_pages=1):
    from bulk_downloader import scene_crawler

    queued = []
    result = scene_crawler.crawl_with_page(
        page,
        site_id=sid,
        listing_url=origin + "/",
        site_config=config,
        newest_n=0,
        max_pages=max_pages,
        max_scrolls=1,
        delay_s=0,
        title_fetch_limit=0,
        db_path=str(tmp_path / "discovery.sqlite"),
        enqueue_fn=lambda s, url: (
            queued.append(url) or {"added": 1, "dupes": 0, "skipped": 0}
        ),
    )
    return result, queued


def test_save_fills_a_cookie_slot_into_a_site_with_no_login(saved_config, tmp_path):
    # Premise control: the slot the crawl saw on test2 is the app's own doing.
    sid, cfg = saved_config
    assert cfg["cookie_file"] == str(tmp_path / "cookies" / f"{sid}.json")
    assert not Path(cfg["cookie_file"]).exists()


def test_public_listing_with_autofilled_cookie_slot_is_discovered(
    page, origin, tmp_path, saved_config
):
    sid, cfg = saved_config
    result, queued = _crawl(page, origin, tmp_path, sid, dict(cfg))
    assert result["state"] != "NOT_LOGGED_IN", (
        f"DL95_HQPORNER_1_COOKIE_SLOT_READ_AS_LOGIN shapes={result.get('scene_shapes')}"
    )
    assert result["state"] == "COMPLETED", result
    assert result["discovered"] == N_SCENES, result
    assert len(queued) == N_SCENES
    assert all(
        u.startswith(origin + "/hdporn/") and u.endswith(".html") for u in queued
    ), queued[:3]


def test_a_jar_that_holds_cookies_still_declares_a_session(
    page, origin, tmp_path, saved_config
):
    from bulk_downloader.cookies import save_cookies_to_file

    sid, cfg = saved_config
    save_cookies_to_file(
        cfg["cookie_file"],
        [{"name": "session", "value": "x", "domain": "127.0.0.1", "path": "/"}],
    )
    result, queued = _crawl(page, origin, tmp_path, sid, dict(cfg))
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_an_unreadable_jar_is_not_proof_of_a_public_site(
    page, origin, tmp_path, saved_config
):
    sid, cfg = saved_config
    Path(cfg["cookie_file"]).write_text("{not json", encoding="utf-8")
    result, queued = _crawl(page, origin, tmp_path, sid, dict(cfg))
    assert result["state"] == "NOT_LOGGED_IN", result
    assert queued == []


def test_an_account_jar_that_holds_cookies_declares_a_session(tmp_path):
    from bulk_downloader import scene_crawler
    from bulk_downloader.cookies import save_cookies_to_file

    jar = tmp_path / "acct.json"
    save_cookies_to_file(
        str(jar), [{"name": "s", "value": "x", "domain": "h.test", "path": "/"}]
    )
    assert scene_crawler._site_is_public(
        {"accounts": [{"cookie_file": str(tmp_path / "none.json")}]}
    )
    assert not scene_crawler._site_is_public({"accounts": [{"cookie_file": str(jar)}]})


def test_page_number_links_are_walked_not_queued(page, origin, tmp_path, saved_config):
    # "2" -> /hdporn/2 shares the card path shape. As a "scene" it was queued
    # and, being a scene URL, never offered to the pager walk.
    sid, cfg = saved_config
    result, queued = _crawl(page, origin, tmp_path, sid, dict(cfg), max_pages=2)
    assert result["pages_walked"] == 2, result
    assert result["page_urls"][1] == origin + "/hdporn/2", (
        f"DL95_HQPORNER_1_PAGER_NOT_WALKED {result['page_urls']}"
    )
    assert not [u for u in queued if u.rstrip("/").rsplit("/", 1)[-1].isdigit()], (
        "DL95_HQPORNER_1_PAGER_QUEUED_AS_SCENE"
    )


def _real_vault(monkeypatch, reply, *, explicit_endpoint, cached=None):
    """A real VaultSync (real crypto, real lookup) whose Redis transport is
    ``reply``: bytes/None to answer GET, or an exception instance to raise."""
    from bulk_downloader import vault_sync

    vs = vault_sync.VaultSync(secret_key=b"k" * 32, fallback_local=True)
    vs.explicit_endpoint = explicit_endpoint  # what get_vault_sync derives
    calls = []

    def transport(host, port, args, timeout=2.0, require_reply=False):
        calls.append((args[0], require_reply))
        if isinstance(reply, BaseException):
            raise reply
        return reply(vs) if callable(reply) else reply

    monkeypatch.setattr(vault_sync, "_redis_command", transport)
    if cached is not None:
        vs._local_cache["bd:vaultsync:c1d3d9b8:0"] = (
            float("inf"),
            vs.crypto.encrypt({"cookies": cached}, ttl_seconds=3600.0),
        )
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda *a, **k: vs)
    return calls


_SESSION = [{"name": "s", "value": "x", "domain": "hqporner.com", "path": "/"}]


@pytest.mark.parametrize(
    ("reply", "explicit", "cached", "public"),
    [
        # No vault service on this host (test2): the default endpoint refuses.
        (ConnectionRefusedError(), False, None, True),
        (ConnectionRefusedError(), False, _SESSION, False),
        (None, True, None, True),  # the vault answers: no session
        (
            lambda vs: vs.crypto.encrypt(
                {"cookies": _SESSION}, ttl_seconds=3600.0
            ).encode(),
            True,
            None,
            False,
        ),
        # cx-2 F1: an unknown vault answer is not proof of a public site.
        (ConnectionRefusedError(), True, None, False),
        (ConnectionError("reset"), False, None, False),
        (TimeoutError(), True, None, False),
        (RuntimeError("Redis error: LOADING"), True, None, False),
        (b"not-a-token", True, None, False),
    ],
    ids=[
        "no-service",
        "no-service-cached",
        "absent",
        "found",
        "configured-refused",
        "reset",
        "timeout",
        "redis-error",
        "undecryptable",
    ],
)
def test_vault_outcome_for_a_missing_jar(
    monkeypatch, tmp_path, reply, explicit, cached, public
):
    from bulk_downloader import scene_crawler

    calls = _real_vault(monkeypatch, reply, explicit_endpoint=explicit, cached=cached)
    got = scene_crawler._site_is_public(
        {"cookie_file": str(tmp_path / "c1d3d9b8.json")}
    )
    assert got is public, f"DL95_HQPORNER_1_VAULT_OUTCOME public={got}"
    assert calls == [("GET", True)], calls


def test_a_reply_less_close_is_not_a_nil_reply(monkeypatch):
    import socket

    from bulk_downloader import vault_sync

    class _Closed:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            pass

        def connect(self, addr):
            pass

        def sendall(self, b):
            pass

        def recv(self, n):
            return b""

        def close(self):
            pass

    monkeypatch.setattr(socket, "socket", _Closed)
    assert vault_sync._redis_command("h", 1, ["GET", "k"]) is None  # legacy callers
    with pytest.raises(ConnectionError):
        vault_sync._redis_command("h", 1, ["GET", "k"], require_reply=True)


@pytest.mark.parametrize(
    ("env", "explicit"),
    [
        ({}, False),
        ({"BD_REDIS_HOST": "10.0.0.9"}, True),
        ({"BD_REDIS_PORT": "6380"}, True),
    ],
)
def test_only_a_configured_endpoint_is_explicit(monkeypatch, env, explicit):
    from bulk_downloader import vault_sync

    monkeypatch.undo()  # drop the autouse get_vault_sync stub for this one
    monkeypatch.setattr(vault_sync, "_VAULT_SYNC_INSTANCE", None)
    for k in ("BD_REDIS_HOST", "BD_REDIS_PORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert vault_sync.get_vault_sync().explicit_endpoint is explicit
