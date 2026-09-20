"""Row 834 -- Elasticsearch full-text subtitle/dialogue indexer.

Acceptance:
  1. SRT subtitle dialogue is correctly parsed and indexed with
     millisecond timestamps.
  2. A full-text query returns accurate video seek offsets (ms).
  3. Fail-soft degradation when Elasticsearch is unreachable.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

from unittest.mock import patch

import pytest
import requests
from bulk_downloader import subtitle_search as ss

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,500
Hello there, general Kenobi.

2
00:00:04,250 --> 00:00:06,000
You are a bold one.
"""


def test_parse_srt_dialogue_with_ms_timestamps():
    chunks = ss.parse_srt(SAMPLE_SRT)
    assert chunks == [
        {"start_ms": 1000, "end_ms": 3500, "text": "Hello there, general Kenobi."},
        {"start_ms": 4250, "end_ms": 6000, "text": "You are a bold one."},
    ]


def test_index_video_bulk_indexes_parsed_chunks(tmp_path):
    srt_path = tmp_path / "movie.srt"
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")

    captured = {}

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"errors": False}

    def fake_post(url, data=None, headers=None, timeout=None, **kw):
        captured["url"] = url
        captured["data"] = data
        return FakeResp()

    with patch.object(ss.requests, "post", side_effect=fake_post):
        result = ss.index_video("vid-1", srt_path, es_url="http://es.example:9200")

    assert result == {"ok": True, "indexed": 2, "error": None}
    assert captured["url"] == "http://es.example:9200/_bulk"
    assert '"video_id": "vid-1"' in captured["data"]
    assert '"start_ms": 1000' in captured["data"]


def test_search_returns_accurate_video_seek_offsets():
    canned = {
        "hits": {
            "hits": [
                {
                    "_score": 1.23,
                    "_source": {
                        "video_id": "vid-1",
                        "start_ms": 4250,
                        "end_ms": 6000,
                        "text": "You are a bold one.",
                    },
                }
            ]
        }
    }

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return canned

    with patch.object(ss.requests, "post", return_value=FakeResp()):
        result = ss.search("bold one", es_url="http://es.example:9200")

    assert result["ok"] is True
    assert result["degraded"] is False
    assert result["results"] == [
        {"video_id": "vid-1", "start_ms": 4250, "end_ms": 6000,
         "text": "You are a bold one.", "score": 1.23},
    ]


def test_search_fails_soft_when_elasticsearch_unreachable():
    with patch.object(ss.requests, "post",
                       side_effect=requests.ConnectionError("refused")):
        result = ss.search("bold one", es_url="http://es.example:9200")

    assert result["ok"] is True
    assert result["degraded"] is True
    assert result["results"] == []
    assert result["error"]


def test_index_video_fails_soft_when_elasticsearch_unreachable(tmp_path):
    srt_path = tmp_path / "movie.srt"
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")

    with patch.object(ss.requests, "post",
                       side_effect=requests.ConnectionError("refused")):
        result = ss.index_video("vid-1", srt_path, es_url="http://es.example:9200")

    assert result["ok"] is False
    assert result["indexed"] == 0
    assert result["error"]


def test_search_without_es_url_uses_the_configured_default(monkeypatch):
    """No es_url override -> _es_url() supplies it from the env var
    (or the loopback default), not a hardcoded/short-circuited value."""
    monkeypatch.setenv("SUBTITLE_SEARCH_ELASTICSEARCH_URL", "http://configured-es:9200")
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": {"hits": []}}

    def fake_post(url, **kw):
        captured["url"] = url
        return FakeResp()

    with patch.object(ss.requests, "post", side_effect=fake_post):
        result = ss.search("anything")

    assert captured["url"] == f"http://configured-es:9200/{ss.INDEX_NAME}/_search"
    assert result == {"ok": True, "degraded": False, "results": [], "error": None}


def test_es_url_falls_back_to_loopback_default(monkeypatch):
    monkeypatch.delenv("SUBTITLE_SEARCH_ELASTICSEARCH_URL", raising=False)
    assert ss._es_url() == ss._DEFAULT_ES_URL == "http://127.0.0.1:9200"


# ── Correctness lens (row834-local REFUTE): E2 accounting, E1 integration ──────

class _BulkResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _bulk(items_status):
    items = [{"index": {"_index": "bd_subtitles", "status": st}} for st in items_status]
    return {"errors": any(st >= 300 for st in items_status), "items": items}


@pytest.mark.parametrize("statuses,indexed,ok", [
    ([400, 400], 0, False),   # E2: every item rejected used to report indexed=2
    ([201, 400], 1, False),
    ([201, 201], 2, True),
])
def test_index_video_counts_only_the_items_elasticsearch_accepted(tmp_path, statuses, indexed, ok):
    srt_path = tmp_path / "movie.srt"
    srt_path.write_text(SAMPLE_SRT, encoding="utf-8")
    with patch.object(ss.requests, "post", return_value=_BulkResp(_bulk(statuses))):
        result = ss.index_video("vid", srt_path, es_url="http://es.example:9200")
    assert (result["indexed"], result["ok"]) == (indexed, ok), result
    if not ok:
        assert f"accepted {indexed} of 2" in result["error"]


def test_index_sidecars_indexes_every_srt_next_to_the_video(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    (tmp_path / "clip.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    (tmp_path / "clip.en.srt").write_text(SAMPLE_SRT, encoding="utf-8")
    (tmp_path / "other.srt").write_text(SAMPLE_SRT, encoding="utf-8")  # not this video's
    calls = []

    def fake_index_video(video_id, path, es_url=None):
        calls.append((video_id, path.name))
        return {"ok": True, "indexed": 2, "error": None}

    with patch.object(ss, "index_video", side_effect=fake_index_video):
        result = ss.index_sidecars(video, video_id="41")
    assert calls == [("41:default", "clip.srt"), ("41:en", "clip.en.srt")]
    assert result["ok"] is True and result["indexed"] == 4 and len(result["files"]) == 2
    # negative: no sidecar -> ok False, nothing indexed, no exception
    with patch.object(ss, "index_video", side_effect=AssertionError("must not be called")):
        none = ss.index_sidecars(tmp_path / "missing.mp4", video_id="42")
    assert none == {"ok": False, "indexed": 0, "files": [], "error": f"no .srt sidecar next to {tmp_path / 'missing.mp4'}"}


def _client(monkeypatch):
    from bulk_downloader import app_subtitles
    from flask import Flask
    app = Flask(__name__)
    app_subtitles.register_routes(app)
    monkeypatch.setattr(app_subtitles, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(app_subtitles, "_history_row", lambda hid: ({"site_id": "s", "filename": f"/videos/{hid}.mp4"}, None))
    return app.test_client()


def test_api_subtitles_search_route_returns_the_engines_seek_offsets(monkeypatch):
    seen = []

    def fake_search(query, k=10, es_url=None):
        seen.append((query, k))
        return {"ok": True, "degraded": False,
                "hits": [{"video_id": "7:en", "start_ms": 1000, "end_ms": 3500, "text": "Hello there"}]}

    monkeypatch.setattr(ss, "search", fake_search)
    client = _client(monkeypatch)
    r = client.get("/api/subtitles/search?q=kenobi&k=5")
    assert r.status_code == 200 and r.get_json()["hits"][0]["start_ms"] == 1000
    assert seen == [("kenobi", 5)]
    assert client.get("/api/subtitles/search").status_code == 400          # missing q
    assert client.get("/api/subtitles/search?q=x&k=zz").status_code == 400  # bad k
    assert seen == [("kenobi", 5)], "a rejected request must not reach the engine"


def test_api_subtitles_index_route_runs_the_ingestion_pipeline_for_the_history_row(monkeypatch):
    calls = []
    monkeypatch.setattr(ss, "index_sidecars", lambda path, video_id, es_url=None: (
        calls.append((path, video_id)) or {"ok": False, "indexed": 0, "files": [], "error": "elasticsearch unreachable"}))
    client = _client(monkeypatch)
    r = client.post("/api/subtitles/index/7")
    assert r.status_code == 200, r.get_data(as_text=True)   # fail-soft: ES down is a body, not a 5xx
    assert r.get_json()["error"] == "elasticsearch unreachable"
    assert calls == [("/videos/7.mp4", "7")]


def test_api_subtitles_fetch_indexes_only_when_asked_and_only_after_a_download(monkeypatch):
    from bulk_downloader import app_subtitles, subtitles
    monkeypatch.setattr(subtitles, "is_available", lambda: True)
    downloads = [{"ok": True, "downloaded": ["en"], "skipped": [], "error": None},
                 {"ok": True, "downloaded": ["en"], "skipped": [], "error": None},
                 {"ok": True, "downloaded": [], "skipped": [], "error": None, "reason": "none found"}]
    monkeypatch.setattr(subtitles, "download_for_file", lambda filename, languages=None: downloads.pop(0))
    monkeypatch.setattr(app_subtitles, "_app_s_cfg", dict)
    indexed = []
    monkeypatch.setattr(ss, "index_sidecars", lambda path, video_id, es_url=None: (
        indexed.append((path, video_id)) or {"ok": True, "indexed": 2, "files": [], "error": None}))
    client = _client(monkeypatch)
    plain = client.post("/api/subtitles/fetch/9", json={"languages": ["en"]})
    assert plain.status_code == 200 and "index" not in plain.get_json() and indexed == []
    asked = client.post("/api/subtitles/fetch/9", json={"languages": ["en"], "index": True})
    assert asked.get_json()["index"]["indexed"] == 2 and indexed == [("/videos/9.mp4", "9")]
    nothing = client.post("/api/subtitles/fetch/9", json={"index": True})
    assert "index" not in nothing.get_json() and len(indexed) == 1, "nothing downloaded -> nothing to index"
