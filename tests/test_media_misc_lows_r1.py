"""media-misc-lows r1: media-serving FINDINGS F6, F7, F8 (LOW).

F6  stream_relay._parse_range_header / serve_range: per RFC 9110 14.2 a
    Range header with an unknown unit or an invalid ranges-specifier is
    ignored (200, full body). BASE answered 416 for those and let int()
    accept "+0" / "1_0" / non-ASCII digits (206 for a range the client
    never wrote). Unsatisfiable ranges stay 416.
F7  /api/subtitles/fetch: `languages` must be a list of strings (else 400)
    of at most _MAX_LANGUAGES; /api/subtitles/search: q at most
    _MAX_QUERY_CHARS. BASE iterated "eng" per character, 500'd on 5 / [1],
    took a 100k-item list and forwarded a 1 MB q.
F8  subtitles.download_track (no product caller): redirects are not
    followed, and the body is streamed under a _TRACK_MAX_BYTES cap.
    BASE followed redirects to any host and held r.content whole.
"""

# An ordinary module test: its subject is the module under test, not the
# tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import httpx
import pytest

from bulk_downloader import app_subtitles, ssrf_transport, subtitles
from bulk_downloader.stream_relay import serve_range

_BODY = bytes(range(100))
# subtitles._TRACK_MAX_BYTES, spelled out so BASE fails on behaviour, not a name
_TRACK_CAP = 5 * 1024 * 1024


def _serve(tmp_path, range_header, body=_BODY):
    f = tmp_path / "v.mp4"
    f.write_bytes(body)
    gen, headers, status = serve_range(str(f), range_header)
    data = b"".join(gen) if gen else b""
    return status, data, headers.get("Content-Range")


# ---- F6 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "items=0-5",  # unknown unit
        "bytes=abc",
        "bytes=+0-+9",  # int() leniency: sign
        "bytes=1_0-1_9",  # int() leniency: underscores
        "bytes=٠-٩",  # int() leniency: Arabic-Indic digits
        "bytes=5-3",  # last-pos < first-pos (RFC 9110 14.1.1: invalid)
        "bytes=",
        "bytes=-",
    ],
)
def test_ignored_range_serves_full_body(tmp_path, header):
    status, data, crange = _serve(tmp_path, header)
    assert (status, data, crange) == (200, _BODY, None), (
        f"MML1-F6-RANGE-NOT-IGNORED {header!r}: {status} {len(data)} {crange}"
    )


@pytest.mark.parametrize(
    "header,status,body,crange",
    [
        ("bytes=0-9", 206, _BODY[:10], "bytes 0-9/100"),
        ("bytes=-5", 206, _BODY[95:], "bytes 95-99/100"),
        ("bytes=90-", 206, _BODY[90:], "bytes 90-99/100"),
        ("bytes=0-99999999", 206, _BODY, "bytes 0-99/100"),
        ("bytes=0-1,4-5", 206, _BODY[:2], "bytes 0-1/100"),
        ("", 200, _BODY, None),
        # Valid syntax, unsatisfiable: still 416, not ignored.
        ("bytes=-0", 416, b"", "bytes */100"),
        ("bytes=200-300", 416, b"", "bytes */100"),
        ("bytes=999999999-", 416, b"", "bytes */100"),
        ("bytes=" + "9" * 5000 + "-", 416, b"", "bytes */100"),
    ],
)
def test_control_valid_and_unsatisfiable_ranges_unchanged(
    tmp_path, header, status, body, crange
):
    got = _serve(tmp_path, header)
    assert got == (status, body, crange), f"MML1-F6-CONTROL {header[:24]!r}: {got[0]}"


def test_control_empty_file(tmp_path):
    assert _serve(tmp_path, "bytes=0-0", b"") == (416, b"", "bytes */0")
    assert _serve(tmp_path, "", b"") == (200, b"", None)


# ---- F7 -----------------------------------------------------------------


@pytest.fixture
def subs_client(monkeypatch):
    from flask import Flask

    asked = []
    searched = []
    monkeypatch.setattr(subtitles, "is_available", lambda: True)
    monkeypatch.setattr(
        subtitles,
        "download_for_file",
        lambda filename, languages=None: (
            asked.append(languages)
            or {"ok": True, "downloaded": [], "skipped": [], "error": None}
        ),
    )
    from bulk_downloader import subtitle_search

    monkeypatch.setattr(
        subtitle_search,
        "search",
        lambda query, k=10, es_url=None: (
            searched.append(query) or {"ok": True, "hits": []}
        ),
    )
    monkeypatch.setattr(app_subtitles, "_app_s_cfg", dict)
    monkeypatch.setattr(app_subtitles, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(
        app_subtitles,
        "_history_row",
        lambda hid: ({"site_id": "s", "filename": f"/videos/{hid}.mp4"}, None),
    )
    app = Flask(__name__)
    app_subtitles.register_routes(app)
    return app.test_client(), asked, searched


@pytest.mark.parametrize(
    "case,body",
    [
        ("str", {"languages": "eng"}),
        ("int", {"languages": 5}),
        ("int-item", {"languages": [1]}),
        ("dict", {"languages": {"en": 1}}),
        ("over-cap", {"languages": ["en"] * 33}),
        ("100k", {"languages": ["en"] * 100_000}),
        ("list-body", [1]),
    ],
)
def test_fetch_rejects_bad_languages(subs_client, case, body):
    client, asked, _ = subs_client
    r = client.post("/api/subtitles/fetch/9", json=body)
    assert r.status_code == 400 and asked == [], (
        f"MML1-F7-LANGUAGES-{case}: {r.status_code} provider got {len(asked)} calls"
    )
    assert r.get_json()["ok"] is False


@pytest.mark.parametrize(
    "body,want",
    [
        ({"languages": ["en", "es"]}, ["en", "es"]),
        ({"languages": ["en"] * 32}, ["en"] * 32),
        ({}, ["en"]),  # site config, then the default
        ({"languages": []}, ["en"]),
        ({"languages": None}, ["en"]),
    ],
)
def test_control_fetch_languages_reach_provider(subs_client, body, want):
    client, asked, _ = subs_client
    r = client.post("/api/subtitles/fetch/9", json=body)
    assert (r.status_code, asked) == (200, [want]), f"MML1-F7-CONTROL {r.status_code}"


def test_search_q_length_bounded(subs_client):
    client, _, searched = subs_client
    r = client.get("/api/subtitles/search?q=" + "a" * 513)
    assert r.status_code == 400 and searched == [], (
        f"MML1-F7-Q-UNBOUNDED: {r.status_code}, forwarded {len(searched)}"
    )
    r = client.get("/api/subtitles/search?q=" + "a" * 512)
    assert r.status_code == 200 and searched == ["a" * 512], "MML1-F7-Q-CONTROL"


# ---- F8 -----------------------------------------------------------------


def _mock_transport(monkeypatch, handler):
    monkeypatch.setattr(
        ssrf_transport, "guarded_transport", lambda policy: httpx.MockTransport(handler)
    )


def test_download_track_does_not_follow_redirects(tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "cdn.example":
            return httpx.Response(302, headers={"Location": "http://10.0.0.5/x.vtt"})
        return httpx.Response(200, content=b"WEBVTT\n")

    _mock_transport(monkeypatch, handler)
    dest = tmp_path / "v.en.vtt"
    res = subtitles.download_track("https://cdn.example/a.vtt", dest)
    assert seen == ["https://cdn.example/a.vtt"], f"MML1-F8-REDIRECT-FOLLOWED: {seen}"
    assert res == {"ok": False, "path": None, "error": "HTTP 302"}
    assert not dest.exists()


def test_download_track_caps_streamed_body(tmp_path, monkeypatch):
    pulled = []
    mb = b"x" * (1024 * 1024)

    def chunks():
        for i in range(20):
            pulled.append(i)
            yield mb

    _mock_transport(monkeypatch, lambda request: httpx.Response(200, content=chunks()))
    dest = tmp_path / "v.en.vtt"
    res = subtitles.download_track("https://cdn.example/a.vtt", dest)
    assert res["ok"] is False and len(pulled) <= 6, (
        f"MML1-F8-UNCAPPED-BODY: ok={res['ok']} pulled {len(pulled)} MB"
    )
    assert res["error"] == f"track over {_TRACK_CAP} bytes"
    assert not dest.exists()


@pytest.mark.parametrize("extra,ok", [(0, True), (1, False)])
def test_download_track_cap_boundary(tmp_path, monkeypatch, extra, ok):
    payload = b"\xef\xbb\xbfWEBVTT\n" + b"y" * (_TRACK_CAP - 10 + extra)
    _mock_transport(monkeypatch, lambda request: httpx.Response(200, content=payload))
    dest = tmp_path / "v.en.vtt"
    res = subtitles.download_track("https://cdn.example/a.vtt", dest)
    assert res["ok"] is ok, f"MML1-F8-CAP-BOUNDARY +{extra}: {res['error']}"
    if ok:
        assert dest.read_bytes() == payload
