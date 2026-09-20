import json
import os
import wave

import pytest

BD_GATE_SCOPE = "module"

from bulk_downloader.audio_transcriber import (
    AudioTranscriber,
    TranscriberError,
    extract_topics,
    index_audio,
    post_download_hook,
    segments_to_vtt,
)


def _write_fixture_wav(path: str) -> None:
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x00\x00" * 800)  # 0.1s of silence


class FakeTranscribeHttp:
    """Fixture-controlled transport standing in for the satellite endpoint."""

    def __init__(self, response=None, exc=None):
        self.calls = []
        self._response = response
        self._exc = exc

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self._exc is not None:
            raise self._exc
        return self._response


FIXTURE_SEGMENTS = [
    {"start": 0.0, "end": 1.5, "text": "audio about rockets and orbital mechanics"},
    {"start": 1.5, "end": 3.0, "text": "rockets need fuel and telemetry"},
]


def test_segments_to_vtt_renders_cues_with_timestamps():
    vtt = segments_to_vtt(FIXTURE_SEGMENTS)
    assert vtt.startswith("WEBVTT\n\n")
    assert "00:00:00.000 --> 00:00:01.500" in vtt
    assert "rockets need fuel and telemetry" in vtt


def test_extract_topics_ranks_repeated_content_words():
    topics = extract_topics("rockets rockets fuel telemetry the a rockets")
    assert topics[0] == "rockets"
    assert "the" not in topics and "a" not in topics


def test_index_audio_produces_vtt_and_topics_from_fixture(tmp_path):
    audio_path = tmp_path / "clip.wav"
    _write_fixture_wav(str(audio_path))
    http = FakeTranscribeHttp(response={"segments": FIXTURE_SEGMENTS, "language": "en"})

    result = index_audio(str(audio_path), "http://satellite.local:9000", request_json=http)

    assert result["ok"] is True
    assert result["vtt"].startswith("WEBVTT")
    assert "rockets" in result["topics"]
    assert result["language"] == "en"
    assert http.calls[0][0] == "http://satellite.local:9000/v1/transcribe"


def test_index_audio_transcription_failure_is_reported_not_raised(tmp_path):
    audio_path = tmp_path / "clip.wav"
    _write_fixture_wav(str(audio_path))
    http = FakeTranscribeHttp(exc=ConnectionError("satellite unreachable"))

    result = index_audio(str(audio_path), "http://satellite.local:9000", request_json=http)

    assert result["ok"] is False
    assert result["code"] == "transcribe_request_failed"


def test_transcriber_raises_typed_error_on_unreadable_audio(tmp_path):
    http = FakeTranscribeHttp(response={"segments": []})
    transcriber = AudioTranscriber("http://satellite.local:9000", request_json=http)
    with pytest.raises(TranscriberError) as exc_info:
        transcriber.transcribe(str(tmp_path / "missing.wav"))
    assert exc_info.value.code == "audio_unreadable"
    assert "missing.wav" in str(exc_info.value)  # message carries the OSError detail, not just the code
    assert http.calls == []  # never reached the network for a file that can't be read


def test_post_download_hook_writes_sidecar_files_and_does_not_block(tmp_path):
    audio_path = tmp_path / "downloaded.wav"
    _write_fixture_wav(str(audio_path))
    out_dir = tmp_path / "out"
    http = FakeTranscribeHttp(response={"segments": FIXTURE_SEGMENTS, "language": "en"})

    result = post_download_hook(str(audio_path), str(out_dir), "http://satellite.local:9000",
                                 request_json=http)

    assert result["ok"] is True
    assert os.path.exists(result["vtt_path"])
    assert os.path.exists(result["topics_path"])
    with open(result["topics_path"]) as fh:
        topics_doc = json.load(fh)
    assert "rockets" in topics_doc["topics"]


def test_post_download_hook_failure_is_non_blocking(tmp_path):
    audio_path = tmp_path / "downloaded.wav"
    _write_fixture_wav(str(audio_path))
    out_dir = tmp_path / "out"
    http = FakeTranscribeHttp(exc=TimeoutError("satellite timed out"))

    result = post_download_hook(str(audio_path), str(out_dir), "http://satellite.local:9000",
                                 request_json=http)

    assert result["ok"] is False
    assert result["skipped"] is False
    assert not os.path.exists(out_dir)  # no partial sidecar output on failure


def test_post_download_hook_with_no_endpoint_configured_is_skipped_not_failed(tmp_path):
    audio_path = tmp_path / "downloaded.wav"
    _write_fixture_wav(str(audio_path))

    result = post_download_hook(str(audio_path), str(tmp_path / "out"), None)

    assert result["ok"] is False
    assert result["skipped"] is True
    assert result["code"] == "no_endpoint"


def test_no_site_interaction_only_the_declared_satellite_endpoint_is_called(tmp_path):
    """Negative control: the hook only ever calls the configured transcription
    endpoint URL, never a download-site URL, and never authenticates anywhere."""
    audio_path = tmp_path / "downloaded.wav"
    _write_fixture_wav(str(audio_path))
    http = FakeTranscribeHttp(response={"segments": FIXTURE_SEGMENTS, "language": "en"})

    post_download_hook(str(audio_path), str(tmp_path / "out"), "http://satellite.local:9000",
                        request_json=http)

    called_urls = [call[0] for call in http.calls]
    assert called_urls == ["http://satellite.local:9000/v1/transcribe"]
    for url, payload, _ in http.calls:
        assert "cookie" not in json.dumps(payload).lower()
        assert "login" not in url.lower()


# ---- fixer (O928) controls for the correctness REFUTE E1-E4 --------------

def test_e1_audio_payload_is_real_base64_of_the_wav_bytes(tmp_path):
    import base64
    from bulk_downloader.audio_transcriber import AudioTranscriber
    audio_path = tmp_path / "clip.wav"
    _write_fixture_wav(str(audio_path))
    http = FakeTranscribeHttp(response={"segments": [], "language": "en"})
    AudioTranscriber("http://satellite.local:9000", request_json=http).transcribe(str(audio_path))
    payload = http.calls[0][1]
    raw = audio_path.read_bytes()
    assert base64.b64decode(payload["audio_b64"], validate=True) == raw
    assert raw[:4] == b"RIFF"


@pytest.mark.parametrize("seconds,expected", [
    (59.9999, "00:01:00.000"),
    (3599.9999, "01:00:00.000"),
    (0.0, "00:00:00.000"),
    (61.5, "00:01:01.500"),
    (3661.0015, "01:01:01.002"),
    (-3.0, "00:00:00.000"),
    ("nope", "00:00:00.000"),
    (None, "00:00:00.000"),
])
def test_e2_timestamps_round_to_milliseconds_and_carry(seconds, expected):
    from bulk_downloader.audio_transcriber import _format_ts
    assert _format_ts(seconds) == expected


def test_e3_null_segments_and_lone_surrogates_are_contained(tmp_path):
    from bulk_downloader.audio_transcriber import index_audio, post_download_hook, segments_to_vtt
    audio_path = tmp_path / "clip.wav"
    _write_fixture_wav(str(audio_path))
    bad_segments = [None, {"start": None, "end": None, "text": None},
                    {"start": 0.0, "end": 1.0, "text": "ok \ud800 text"}]
    http = FakeTranscribeHttp(response={"segments": bad_segments, "language": "\udc00en"})
    res = index_audio(str(audio_path), "http://satellite.local:9000", request_json=http)
    assert res["ok"] is True, res
    assert "00:00:00.000 --> 00:00:00.000" in res["vtt"]
    assert "ok  text" in res["vtt"] or "ok text" in res["vtt"]
    out = post_download_hook(str(audio_path), str(tmp_path / "out"), "http://satellite.local:9000", request_json=http)
    assert out["ok"] is True, out
    (tmp_path / "out" / "clip.vtt").read_text(encoding="utf-8")
    json.loads((tmp_path / "out" / "clip.topics.json").read_text(encoding="utf-8"))
    assert segments_to_vtt([None]) == "WEBVTT\n"


def test_e4_redirects_off_the_declared_endpoint_are_refused(tmp_path):
    """A real local HTTP server answers the declared endpoint with a 302 to
    another origin; urllib must NOT follow it -- the other origin sees no request."""
    import http.server
    import socketserver
    import threading
    from bulk_downloader.audio_transcriber import AudioTranscriber, TranscriberError, _request_json

    other_hits = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            other_hits.append(self.path)
            self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
        do_GET = do_POST
        def log_message(self, *a): pass

    other = socketserver.TCPServer(("127.0.0.1", 0), Other)
    other_port = other.server_address[1]

    class Declared(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{other_port}/other-origin")
            self.send_header("Content-Length", "0"); self.end_headers()
        def log_message(self, *a): pass

    declared = socketserver.TCPServer(("127.0.0.1", 0), Declared)
    for srv in (other, declared):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        audio_path = tmp_path / "clip.wav"
        _write_fixture_wav(str(audio_path))
        endpoint = f"http://127.0.0.1:{declared.server_address[1]}"
        with pytest.raises(TranscriberError) as exc:
            AudioTranscriber(endpoint, timeout=5.0, request_json=_request_json).transcribe(str(audio_path))
        assert exc.value.code == "transcribe_request_failed"
        assert "redirect" in str(exc.value.args[1]).lower()
        assert other_hits == []
    finally:
        for srv in (other, declared):
            srv.shutdown(); srv.server_close()
