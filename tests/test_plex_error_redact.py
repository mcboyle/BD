"""plex-error-redact r1 (outbound-integrations F3-F6 + media F10).

F3/F4: plex_advanced answered a failure after connect with str(e)[:200], so a
Plex error naming the URL handed the client the token and any user:pw@ userinfo.
Now the client gets a fixed message + request id and the detail goes to the
server log, redacted (bulk_downloader.error_reply). _connect's own messages
(refusal naming plex_allow_private_host, config missing) still reach the client.
F3: the site save stored a pasted token with its trailing newline; it is now
stored stripped, and plex_advanced strips it before use.
F5: ?count= was int()'d inside the try (count=abc -> 500 echoing the error) and
passed through unbounded and unsigned; now 400 on bad, 0/negative refused, clamped.
F6: the mark body had to be an object; a list body was a 500 echo; now 400.
F10: /stream answered a fault with f"error: {e}" (an absolute library path).
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import json
import logging

import pytest

# Short, like a real Plex token: below the 40-char opaque-token floor of
# capture_artifact_redact, so only the literal pass can catch it.
TOK = "PLXtok-per-7c1e9a"
USERINFO_PW = "hunter2pw"
FAULT = (f"(401) unauthorized; http://admin:{USERINFO_PW}@plex.example:32400/status/sessions"
         f"?X-Plex-Token={TOK} X-Plex-Token: {TOK}\n"
         # own line: no '?' (redact_query would scrub the rest of a URL line) and no
         # name=value, so only the literal pass removes it
         f"server said: token {TOK} rejected")


@pytest.fixture
def server_log():
    """Messages logged under the bulk_downloader logger (it does not propagate
    to the root logger, so caplog would see nothing)."""
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    h = _H(level=logging.DEBUG)
    lg = logging.getLogger("bulk_downloader")
    lg.addHandler(h)
    yield records
    lg.removeHandler(h)


def _logged_for(server_log, request_id):
    return [line for line in server_log if request_id and request_id in line]


class _Faulting:
    """A connected server whose every call fails with FAULT."""

    def __getattr__(self, name):
        raise RuntimeError(FAULT)


@pytest.fixture
def pa(monkeypatch):
    from bulk_downloader import plex_advanced

    monkeypatch.setattr(plex_advanced, "is_available", lambda: True)
    return plex_advanced


CALLS = {
    "server_info": lambda pa, cfg: pa.server_info(cfg),
    "library_stats": lambda pa, cfg: pa.library_stats(cfg),
    "recently_added": lambda pa, cfg: pa.recently_added(cfg, count=3),
    "on_deck": lambda pa, cfg: pa.on_deck(cfg, count=3),
    "search": lambda pa, cfg: pa.search(cfg, "x", count=3),
    "mark_watched": lambda pa, cfg: pa.mark_watched(cfg, 5),
    "mark_unwatched": lambda pa, cfg: pa.mark_unwatched(cfg, 5),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_plex_error_after_connect_never_reaches_the_client(pa, monkeypatch, server_log, name):
    monkeypatch.setattr(pa, "_connect", lambda cfg: _Faulting())
    out = CALLS[name](pa, {"plex_url": "http://plex.example:32400", "plex_token": TOK + "\n"})
    dumped = json.dumps(out)
    assert TOK not in dumped, ("PER-TOKEN-ECHO", name, out)
    assert USERINFO_PW not in dumped, ("PER-USERINFO-ECHO", name, out)
    from bulk_downloader import error_reply

    assert out.get("error") == error_reply.GENERIC_ERROR and out.get("request_id"), out
    assert out.get("available") is True
    lines = _logged_for(server_log, out["request_id"])
    assert len(lines) == 1, ("PER-NO-REQUEST-ID-LOG", name, server_log)
    assert "plex.example" in lines[0]                      # the detail is kept ...
    assert TOK not in lines[0] and USERINFO_PW not in lines[0], (
        "PER-TOKEN-IN-LOG", lines[0])                       # ... redacted


def test_connect_own_message_still_reaches_the_client(pa):
    out = pa.server_info({"plex_url": "", "plex_token": TOK})
    assert "missing plex_url" in out.get("error", "") or "plexapi not installed" in out.get(
        "error", ""), out
    assert "request_id" not in out and TOK not in json.dumps(out)


def test_blank_token_is_missing_config_not_a_connect(pa, monkeypatch):
    from bulk_downloader import plex_deep_plexapi as pdp

    calls = []
    monkeypatch.setattr(pdp, "connect_or_refusal",
                        lambda cfg, **kw: calls.append(cfg) or (None, None), raising=False)
    monkeypatch.setattr(pdp, "connect", lambda cfg, **kw: calls.append(cfg), raising=False)
    out = pa.server_info({"plex_url": "http://plex.example:32400", "plex_token": " \n"})
    assert calls == [], ("PER-BLANK-TOKEN-USED", out)
    assert "missing plex_url" in out.get("error", "") or "plexapi not installed" in out.get(
        "error", ""), out


def test_redact_detail_masks_the_header_class_without_a_known_secret():
    from bulk_downloader import error_reply

    text = "X-Plex-Token: abcdef123456 at http://u:pw9@h.example/x?X-Plex-Token=zzz999 Session=s3s"
    out = error_reply.redact_detail(text)
    for secret in ("abcdef123456", "zzz999", "pw9", "s3s"):
        assert secret not in out, ("PER-HEADER-CLASS-MISS", secret, out)
    assert "h.example" in out and "X-Plex-Token" in out


# ── routes (real app, fresh_app) ────────────────────────────────────────────


@pytest.fixture
def site(fresh_app):
    r = fresh_app.post("/api/sites", json={"name": "per-plex",
                                           "plex_url": "http://plex.example:32400",
                                           "plex_token": TOK + "\n"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return fresh_app, r.get_json()["id"]


def _stored_token(sid):
    from bulk_downloader import app as app_module

    return app_module.s_cfg[sid].get("plex_token")


def test_post_stores_the_token_stripped(site):
    _client, sid = site
    assert _stored_token(sid) == TOK, ("PER-STORED-UNSTRIPPED", repr(_stored_token(sid)))


def test_put_stores_the_token_stripped(site):
    client, sid = site
    r = client.put(f"/api/sites/{sid}", json={"plex_token": " NEW" + TOK + "\r\n"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _stored_token(sid) == "NEW" + TOK, ("PER-STORED-UNSTRIPPED", repr(_stored_token(sid)))


@pytest.fixture
def recorded(site, monkeypatch):
    from bulk_downloader import plex_advanced

    seen = []
    for fn in ("recently_added", "on_deck"):
        monkeypatch.setattr(plex_advanced, fn,
                            lambda cfg, *, count, _fn=fn: seen.append((_fn, count)) or {"items": []})
    monkeypatch.setattr(plex_advanced, "search",
                        lambda cfg, q, *, count: seen.append(("search", count)) or {"items": []})
    for fn in ("mark_watched", "mark_unwatched"):
        monkeypatch.setattr(plex_advanced, fn,
                            lambda cfg, rk, _fn=fn: seen.append((_fn, rk)) or {"ok": True})
    return site[0], site[1], seen


ROUTES = {"recently_added": 20, "on_deck": 20, "search": 25}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_count_is_a_bounded_positive_int(recorded, route):
    client, sid, seen = recorded
    url = f"/api/plex_advanced/{route}/{sid}?q=x"
    for bad in ("abc", "1e9", "2.5"):
        r = client.get(f"{url}&count={bad}")
        assert r.status_code == 400, ("PER-COUNT-500", route, bad, r.status_code,
                                      r.get_data(as_text=True))
        assert "invalid literal" not in r.get_data(as_text=True)
    for bad in ("0", "-1"):
        r = client.get(f"{url}&count={bad}")
        assert r.status_code == 400, ("PER-COUNT-SLICED", route, bad, r.status_code, seen)
    assert seen == []
    client.get(f"{url}&count=100000")
    assert seen[-1] == (route, 200), ("PER-COUNT-UNBOUNDED", seen)
    client.get(f"{url}&count=7")                            # controls: kept as sent ...
    client.get(url)                                         # ... and the default
    assert seen[-2:] == [(route, 7), (route, ROUTES[route])], seen


def test_mark_body_must_be_an_object(recorded):
    client, sid, seen = recorded
    url = f"/api/plex_advanced/mark/{sid}/5"
    r = client.post(url, json=[True])
    assert r.status_code == 400, ("PER-MARK-500", r.status_code, r.get_data(as_text=True))
    r = client.post(url, data="watched", content_type="text/plain")
    assert r.status_code in (400, 415), r.status_code
    assert seen == []
    r = client.post(url, json={"watched": True})            # control
    assert r.status_code == 200 and seen == [("mark_watched", 5)], (r.status_code, seen)


def test_route_fault_answers_generic_with_a_logged_request_id(site, monkeypatch, server_log):
    client, sid = site
    from bulk_downloader import plex_advanced

    def boom(cfg):
        raise RuntimeError(FAULT)

    monkeypatch.setattr(plex_advanced, "server_info", boom)
    r = client.get(f"/api/plex_advanced/server_info/{sid}")
    body = r.get_json()
    assert r.status_code == 500
    assert TOK not in r.get_data(as_text=True), ("PER-TOKEN-ECHO", "route", body)
    assert USERINFO_PW not in r.get_data(as_text=True), ("PER-USERINFO-ECHO", "route", body)
    assert body.get("request_id") and _logged_for(server_log, body["request_id"]), (
        "PER-NO-REQUEST-ID-LOG", body, server_log)


# ── media F10: /stream ──────────────────────────────────────────────────────

LIB_PATH = "/srv/per-library/Private Name/clip.mp4"


def test_stream_fault_body_has_no_exception_text(fresh_app, monkeypatch, server_log):
    from bulk_downloader import db, stream_relay

    # The first request runs the app's boot hook, which opens the db too; the
    # fault below is for the route's own history lookup.
    monkeypatch.setattr(stream_relay, "verify_token", lambda t: {"ok": False, "error": "warm"})
    assert fresh_app.get("/stream/per-stream-token").status_code == 403
    monkeypatch.setattr(stream_relay, "verify_token", lambda t: {"ok": True, "history_id": 1})

    def broken_conn():
        raise OSError(2, "No such file or directory", LIB_PATH)

    monkeypatch.setattr(db, "db_conn", broken_conn)
    r = fresh_app.get("/stream/per-stream-token")
    text = r.get_data(as_text=True)
    assert r.status_code == 500, (r.status_code, text)
    assert "/srv/per-library" not in text and "No such file" not in text, ("PER-STREAM-ECHO", text)
    from bulk_downloader import error_reply

    assert text.startswith(error_reply.GENERIC_ERROR), text
    request_id = text.rsplit("request id ", 1)[-1].rstrip(")")
    lines = _logged_for(server_log, request_id)
    assert len(lines) == 1 and "stream_serve" in lines[0], ("PER-NO-REQUEST-ID-LOG", text, server_log)


def test_stream_denial_text_is_kept(fresh_app, monkeypatch):
    from bulk_downloader import stream_relay

    monkeypatch.setattr(stream_relay, "verify_token", lambda t: {"ok": False, "error": "expired"})
    r = fresh_app.get("/stream/per-stream-token")
    assert r.status_code == 403 and r.get_data(as_text=True) == "expired"
