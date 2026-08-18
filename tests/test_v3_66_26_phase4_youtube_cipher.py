"""v3.66.26 — Phase 4 C4 A3: YouTube signatureCipher decipher dispatch.

This release wires a feature-flagged decipher dispatch behind the
``BD_YOUTUBE_CIPHER`` env var. The dispatcher has three modes:

  * ``off`` (default) — preserves v3.66.20 behavior verbatim. signed-only
    responses produce no candidates and an error string carrying the
    signed-format count.
  * ``yt-dlp`` — shells out to ``yt-dlp --dump-single-json``. Subprocess
    invocation is hard-capped at 30s. Argv list, never ``shell=True``.
    Backend is gated by ``shutil.which("yt-dlp")``.
  * ``player-js`` — stub returning a "not implemented" error. Pins the
    dispatch site so the eventual swap to an in-process JS decipher
    (item A2 in OPEN_THREADS C4) lands without a public-surface change.

Test classes
------------

  TestCipherBackendSelection
      Pure-function coverage of ``_yt_cipher_backend``: env var parsing,
      case folding, alias handling, unknown-value fallthrough.

  TestCipherDispatchOff
      ``BD_YOUTUBE_CIPHER`` unset or ``=off`` — confirms the dispatcher
      returns the off-message and never invokes either backend. Pins
      the v3.66.20 contract: error mentions "signed" + the count.

  TestCipherDispatchYtdlp
      ``BD_YOUTUBE_CIPHER=yt-dlp``. Subprocess is monkeypatched. Covers:
      missing PATH, timeout, non-zero exit, empty output, malformed JSON,
      successful decipher, video_id sanitization, argv shape (no
      ``shell=True``).

  TestCipherDispatchPlayerJs
      ``BD_YOUTUBE_CIPHER=player-js`` returns the stub error. Pins the
      dispatch site for the future A2 swap.

  TestCipherInResolveYoutube
      Integration: ``resolve_youtube`` with signed-only streamingData
      reaches the dispatcher. HLS-present streamingData does NOT reach
      the dispatcher (the hot-path optimization).

Process-state hygiene
---------------------

Each test uses ``monkeypatch.setenv``/``delenv`` so pytest auto-restores
the env var. The ``_YT_CIPHER_YTDLP_PATH_CACHE`` module-level cache is
reset via the ``_reset_ytdlp_cache`` autouse fixture so a previous test's
``shutil.which`` result doesn't bleed into the next.
"""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader.provider_resolve import (
    _decipher_signed_formats,
    _decipher_signed_formats_playerjs,
    _decipher_signed_formats_ytdlp,
    _yt_cipher_backend,
    resolve_youtube,
)


# Fixtures -------------------------------------------------------------------


VALID_VIDEO_ID = "dQw4w9WgXcQ"  # 11 chars, matches the regex


@pytest.fixture(autouse=True)
def _reset_ytdlp_cache():
    """Reset the memoized ``shutil.which("yt-dlp")`` between tests.

    The cache is a 1-element list with a sentinel; restoring the
    sentinel forces re-evaluation on the next call. Without this,
    a test that mocks PATH to "not found" would leave ``None``
    cached and the next test would see "not found" even with PATH
    fixed.
    """
    pr._YT_CIPHER_YTDLP_PATH_CACHE[0] = ...
    yield
    pr._YT_CIPHER_YTDLP_PATH_CACHE[0] = ...


def _make_watch_html(player_response: dict) -> bytes:
    """Wrap player_response in the HTML shape resolve_youtube parses."""
    body = json.dumps(player_response)
    return (
        "<!doctype html><html><body><script>"
        "(function(){var ytInitialPlayerResponse = "
        f"{body};"
        "window.ytplayer = {};})();"
        "</script></body></html>"
    ).encode("utf-8")


def _signed_only_player_response() -> dict:
    """A player response where every adaptive format is signed-only."""
    return {
        "playabilityStatus": {"status": "OK"},
        "streamingData": {
            "adaptiveFormats": [
                {"itag": 137,
                 "signatureCipher": "s=ENC1&sp=sig&url=https://x.com/p1",
                 "mimeType": "video/mp4", "qualityLabel": "1080p"},
                {"itag": 248,
                 "cipher": "s=ENC2&sp=sig&url=https://x.com/p2",
                 "mimeType": "video/webm", "qualityLabel": "1080p"},
            ],
        },
    }


def _hls_present_player_response() -> dict:
    """A player response that has both HLS and signed-only formats.

    Used to assert the dispatcher is NOT invoked when HLS is available
    (the hot-path optimization).
    """
    return {
        "playabilityStatus": {"status": "OK"},
        "streamingData": {
            "hlsManifestUrl": (
                "https://manifest.googlevideo.com/api/manifest/"
                "hls_variant/expire/12345/master.m3u8"
            ),
            "adaptiveFormats": [
                {"itag": 137,
                 "signatureCipher": "s=ENC&sp=sig&url=https://x.com/p",
                 "mimeType": "video/mp4", "qualityLabel": "1080p"},
            ],
        },
    }


def _fake_get(status: int, body: bytes):
    def _get(url):
        return (status, {}, body)
    return _get


def _fake_completed_process(
    *,
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
):
    """Construct a CompletedProcess-shaped stand-in for subprocess.run."""
    return SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr,
    )


def _ytdlp_json_with_formats(formats: List[Dict[str, Any]]) -> bytes:
    """Serialize a yt-dlp --dump-single-json-shaped payload."""
    return json.dumps({"formats": formats}).encode("utf-8")


# ---------------------------------------------------------------------------
# TestCipherBackendSelection
# ---------------------------------------------------------------------------


class TestCipherBackendSelection:

    def test_unset_returns_off(self, monkeypatch):
        monkeypatch.delenv("BD_YOUTUBE_CIPHER", raising=False)
        assert _yt_cipher_backend() == "off"

    def test_explicit_off_returns_off(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "off")
        assert _yt_cipher_backend() == "off"

    def test_empty_string_returns_off(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "")
        assert _yt_cipher_backend() == "off"

    def test_unknown_value_returns_off(self, monkeypatch):
        # Fail-closed semantics: unknown backend defaults to off so a
        # typo in the env var doesn't silently enable a different mode.
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "ffmpeg")
        assert _yt_cipher_backend() == "off"

    def test_ytdlp_canonical(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        assert _yt_cipher_backend() == "yt-dlp"

    def test_ytdlp_alias_no_hyphen(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "ytdlp")
        assert _yt_cipher_backend() == "yt-dlp"

    def test_ytdlp_alias_underscore(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt_dlp")
        assert _yt_cipher_backend() == "yt-dlp"

    def test_ytdlp_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "YT-DLP")
        assert _yt_cipher_backend() == "yt-dlp"

    def test_playerjs_canonical(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "player-js")
        assert _yt_cipher_backend() == "player-js"

    def test_playerjs_aliases(self, monkeypatch):
        for alias in ("playerjs", "player_js", "PLAYER-JS"):
            monkeypatch.setenv("BD_YOUTUBE_CIPHER", alias)
            assert _yt_cipher_backend() == "player-js", alias

    def test_whitespace_trimmed(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "  yt-dlp  ")
        assert _yt_cipher_backend() == "yt-dlp"


# ---------------------------------------------------------------------------
# TestCipherDispatchOff
# ---------------------------------------------------------------------------


class TestCipherDispatchOff:
    """When BD_YOUTUBE_CIPHER is off, the dispatcher must return an
    error string that satisfies v3.66.20's pinned contract: contains
    "signed" + the count. No subprocess is invoked."""

    def test_unset_returns_off_message_with_count(self, monkeypatch):
        monkeypatch.delenv("BD_YOUTUBE_CIPHER", raising=False)
        cands, err = _decipher_signed_formats(
            VALID_VIDEO_ID, 5, http_get=_fake_get(200, b""))
        assert cands == []
        assert err is not None
        assert "signed" in err.lower()
        assert "5" in err
        assert "BD_YOUTUBE_CIPHER" in err  # operator hint

    def test_off_explicit(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "off")
        cands, err = _decipher_signed_formats(
            VALID_VIDEO_ID, 2, http_get=_fake_get(200, b""))
        assert cands == []
        assert "2" in err
        assert "off" in err

    def test_off_does_not_invoke_subprocess(self, monkeypatch):
        """When backend is off, _decipher_signed_formats_ytdlp must
        not be called — verified by a sentinel monkeypatch that would
        fail loudly if invoked."""
        called = []

        def _fail(*a, **kw):
            called.append((a, kw))
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.delenv("BD_YOUTUBE_CIPHER", raising=False)
        monkeypatch.setattr(subprocess, "run", _fail)
        _decipher_signed_formats(
            VALID_VIDEO_ID, 1, http_get=_fake_get(200, b""))
        assert called == []

    def test_off_accepts_invalid_video_id(self, monkeypatch):
        """The off path doesn't reach the regex sanitizer — only the
        backend branches do. So an invalid video_id with backend=off
        still produces the off-message, not a sanitizer-rejection
        message. This matches the v3.66.20 contract where the
        signed-skipped path returns regardless of the video_id shape."""
        monkeypatch.delenv("BD_YOUTUBE_CIPHER", raising=False)
        cands, err = _decipher_signed_formats(
            "x",  # 1 char, fails the regex
            3,
            http_get=_fake_get(200, b""),
        )
        assert cands == []
        assert "BD_YOUTUBE_CIPHER=off" in err
        assert "rejected" not in err.lower()


# ---------------------------------------------------------------------------
# TestCipherDispatchYtdlp
# ---------------------------------------------------------------------------


class TestCipherDispatchYtdlp:

    def test_video_id_sanitizer_rejects_short(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp("short")
        assert cands == []
        assert "rejected" in err.lower()
        assert "short" in err

    def test_video_id_sanitizer_rejects_shell_meta(self, monkeypatch):
        """A shell metacharacter in the video_id must be rejected
        before any subprocess is touched. This is belt-and-suspenders
        — we use argv lists, not shell=True, so a shell meta in argv
        is harmless — but the sanitizer pins the contract."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        called = []

        def _fail(*a, **kw):
            called.append((a, kw))
            return _fake_completed_process(stdout=b"{}")

        cands, err = _decipher_signed_formats_ytdlp(
            "abc; rm -rf",  # shell meta + wrong length
            _run=_fail,
        )
        assert cands == []
        assert "rejected" in err.lower()
        assert called == []  # subprocess never invoked

    def test_ytdlp_not_on_path(self, monkeypatch):
        from bulk_downloader import ytdlp_updater
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path", lambda: None)
        monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda: None)
        cands, err = _decipher_signed_formats_ytdlp(VALID_VIDEO_ID)
        assert cands == []
        assert "not installed" in err
        assert "BD_YOUTUBE_CIPHER=off" in err

    def test_timeout_produces_clear_error(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")

        def _timeout(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=kw["timeout"])

        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID, _run=_timeout)
        assert cands == []
        assert "timed out" in err
        assert "30s" in err
        assert VALID_VIDEO_ID in err

    def test_subprocess_oserror(self, monkeypatch):
        """If subprocess.run itself raises OSError (e.g. ENOMEM, fork
        failure), the dispatch must surface that cleanly rather than
        letting it propagate up to the deep_detect glue."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")

        def _oserror(*a, **kw):
            raise OSError(12, "Cannot allocate memory")

        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID, _run=_oserror)
        assert cands == []
        assert "failed to launch" in err
        assert "OSError" in err

    def test_nonzero_exit_carries_stderr_first_line(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        stderr = (b"ERROR: Video unavailable. The uploader has not "
                  b"made this video available in your country\n"
                  b"second line should not appear\n")

        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                returncode=1, stderr=stderr),
        )
        assert cands == []
        assert "exit code 1" in err
        assert "Video unavailable" in err
        assert "second line should not appear" not in err

    def test_empty_stdout(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(stdout=b""),
        )
        assert cands == []
        assert "not parseable" in err

    def test_malformed_json(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=b"<<<not json>>>"),
        )
        assert cands == []
        assert "not parseable" in err

    def test_json_not_object(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=b'["array_not_object"]'),
        )
        assert cands == []
        assert "not a JSON object" in err

    def test_no_formats_field(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=b'{"title": "x"}'),
        )
        assert cands == []
        assert "no formats with direct URLs" in err
        assert VALID_VIDEO_ID in err

    def test_formats_all_without_url(self, monkeypatch):
        """yt-dlp's --dump-single-json sometimes emits format entries
        with no ``url`` (e.g. when even yt-dlp couldn't decipher).
        Those entries must be silently skipped, and if no candidates
        result, we surface the empty-candidates error."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=_ytdlp_json_with_formats([
                    {"format_id": "137", "height": 1080},
                    {"format_id": "248", "vcodec": "vp9"},
                ])),
        )
        assert cands == []
        assert "no formats with direct URLs" in err

    def test_success_single_format(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            embed_url="https://www.youtube.com/embed/" + VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=_ytdlp_json_with_formats([
                    {
                        "format_id": "137",
                        "url": "https://r2.gvt.com/cipher_deciphered_url",
                        "height": 1080, "width": 1920,
                        "tbr": 3500, "fps": 30,
                        "filesize": 12345678,
                        "vcodec": "avc1.640028", "acodec": "none",
                        "ext": "mp4", "format_note": "1080p",
                    },
                ])),
        )
        assert err is None
        assert len(cands) == 1
        c = cands[0]
        assert c["source_type"] == "youtube_resolved_cipher_ytdlp"
        assert c["url"] == "https://r2.gvt.com/cipher_deciphered_url"
        assert c["score"] == 80 + 10  # 80 + 1080//100
        assert c["resolution"]["height"] == 1080
        assert c["resolution"]["width"] == 1920
        assert c["resolution"]["label"] == "1080p"
        assert c["bitrate"] == 3500
        assert c["fps"] == 30.0
        assert c["size_bytes"] == 12345678
        assert c["itag"] == "137"
        assert c["codec"] == "avc1.640028"  # acodec=none filtered
        assert c["provider_resolved"] is True
        assert c["resolved_from"] == \
            "https://www.youtube.com/embed/" + VALID_VIDEO_ID
        assert c["requires_click"] is False
        # Warning about TTL is the contract — downstream cache code
        # uses this to set short TTLs on deciphered URLs.
        assert any("TTL" in w for w in c["warnings"])
        assert any("yt-dlp" in r for r in c["reasons"])

    def test_success_multiple_formats_filters_no_url(self, monkeypatch):
        """Mixed bag: some formats have urls, some don't. Only the
        ones with urls become candidates."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=_ytdlp_json_with_formats([
                    {"format_id": "137",
                     "url": "https://r2.gvt.com/A",
                     "height": 1080, "vcodec": "avc1", "ext": "mp4"},
                    {"format_id": "248",  # no url — skipped
                     "height": 1080, "vcodec": "vp9"},
                    {"format_id": "18",
                     "url": "https://r2.gvt.com/B",
                     "height": 360, "vcodec": "avc1", "acodec": "mp4a",
                     "ext": "mp4"},
                    "not-a-dict",  # type-skipped
                    {"format_id": "noheight",
                     "url": "https://r2.gvt.com/C", "ext": "mp4"},
                ])),
        )
        assert err is None
        assert len(cands) == 3
        urls = [c["url"] for c in cands]
        assert urls == [
            "https://r2.gvt.com/A",
            "https://r2.gvt.com/B",
            "https://r2.gvt.com/C",
        ]
        # noheight format scores 80 (no height bonus)
        scores = [c["score"] for c in cands]
        assert scores == [90, 83, 80]
        # All carry the distinct source_type
        for c in cands:
            assert c["source_type"] == "youtube_resolved_cipher_ytdlp"

    def test_argv_shape(self, monkeypatch):
        """Pin the argv shape: yt-dlp binary, --dump-single-json, the
        flags we expect, and the watch URL — no shell=True."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/local/bin/yt-dlp")
        captured = {}

        def _capture(argv, **kw):
            captured["argv"] = argv
            captured["kwargs"] = kw
            return _fake_completed_process(
                stdout=_ytdlp_json_with_formats([
                    {"format_id": "137", "url": "https://r2.gvt.com/x",
                     "height": 1080},
                ]))

        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID, _run=_capture)
        assert err is None
        assert captured["argv"][0] == "/usr/local/bin/yt-dlp"
        assert "--dump-single-json" in captured["argv"]
        assert "--no-warnings" in captured["argv"]
        assert "--no-playlist" in captured["argv"]
        assert "--skip-download" in captured["argv"]
        assert captured["argv"][-1] == (
            f"https://www.youtube.com/watch?v={VALID_VIDEO_ID}"
        )
        # No shell=True. capture_output=True. timeout=30.
        assert captured["kwargs"].get("shell", False) is False
        assert "shell" not in captured["kwargs"] or \
            captured["kwargs"]["shell"] is False
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["timeout"] == 30
        assert captured["kwargs"]["check"] is False

    def test_acodec_only_format(self, monkeypatch):
        """Audio-only format (vcodec=none, acodec set) — codec field
        should reflect acodec; no height; score is the bare 80."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        cands, err = _decipher_signed_formats_ytdlp(
            VALID_VIDEO_ID,
            _run=lambda *a, **kw: _fake_completed_process(
                stdout=_ytdlp_json_with_formats([
                    {"format_id": "140",
                     "url": "https://r2.gvt.com/audio",
                     "vcodec": "none", "acodec": "mp4a.40.2",
                     "ext": "m4a", "abr": 128, "format_note": "audio"},
                ])),
        )
        assert err is None
        assert len(cands) == 1
        assert cands[0]["codec"] == "mp4a.40.2"
        assert cands[0]["score"] == 80
        assert cands[0]["resolution"] is None
        assert cands[0]["bitrate"] == 128


# ---------------------------------------------------------------------------
# TestCipherDispatchPlayerJs
# ---------------------------------------------------------------------------


class TestCipherDispatchPlayerJs:
    """The player-js backend was a stub through v3.66.47; C4-B
    (v3.66.48) implements the real in-process decipher. These tests pin
    the dispatch routing + video_id sanitation; full decipher coverage
    lives in test_v3_66_48_c4b_player_js.py."""

    def test_playerjs_dispatch_reaches_real_leg(self, monkeypatch):
        """player-js backend routes to the in-process leg (not yt-dlp/
        off). With an empty watch body the leg now does real work and
        fails loud with a clear, player-js-tagged error — no longer the
        stub 'not yet implemented'."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "player-js")
        cands, err = _decipher_signed_formats(
            VALID_VIDEO_ID, 1, http_get=_fake_get(200, b""))
        assert cands == []
        assert "player-js" in err
        assert "not yet implemented" not in err
        # Reached the real leg: it attempted to parse a watch page.
        assert "ytInitialPlayerResponse" in err or "watch" in err

    def test_playerjs_video_id_sanitized(self, monkeypatch):
        """The stub still sanitizes the video_id — keeps the contract
        consistent across backends so the A2 swap doesn't have to add
        the sanitizer later."""
        cands, err = _decipher_signed_formats_playerjs(
            "x",  # too short
            http_get=_fake_get(200, b""),
        )
        assert cands == []
        assert "rejected" in err.lower()


# ---------------------------------------------------------------------------
# TestCipherInResolveYoutube
# ---------------------------------------------------------------------------


class TestCipherInResolveYoutube:
    """Integration with resolve_youtube. These tests fabricate a watch
    response (signed-only or HLS+signed) and assert the dispatch is or
    is not invoked appropriately."""

    def test_signed_only_off_preserves_v3_66_20_contract(self, monkeypatch):
        """v3.66.20 contract: err contains 'signed' + the count. This
        must still hold after v3.66.26's wiring change."""
        monkeypatch.delenv("BD_YOUTUBE_CIPHER", raising=False)
        body = _make_watch_html(_signed_only_player_response())
        cands, err = resolve_youtube(
            {"video_id": VALID_VIDEO_ID}, http_get=_fake_get(200, body))
        assert cands == []
        assert err is not None
        assert "signed" in err.lower()
        assert "2" in err  # 2 signed formats in the fixture

    def test_signed_only_ytdlp_success_flows_to_candidates(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path",
                            lambda: "/usr/bin/yt-dlp")
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: _fake_completed_process(
                stdout=_ytdlp_json_with_formats([
                    {"format_id": "137",
                     "url": "https://r2.gvt.com/deciphered_137",
                     "height": 1080, "vcodec": "avc1", "ext": "mp4"},
                ])),
        )
        body = _make_watch_html(_signed_only_player_response())
        cands, err = resolve_youtube(
            {"video_id": VALID_VIDEO_ID}, http_get=_fake_get(200, body))
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "youtube_resolved_cipher_ytdlp"
        assert cands[0]["url"] == "https://r2.gvt.com/deciphered_137"

    def test_signed_only_ytdlp_failure_returns_dispatch_error(
            self, monkeypatch):
        """When yt-dlp fails (here: not on PATH), the resolver returns
        the dispatcher's error string, not v3.66.20's old "out of
        scope" wording."""
        from bulk_downloader import ytdlp_updater
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")
        monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path", lambda: None)
        monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda: None)
        body = _make_watch_html(_signed_only_player_response())
        cands, err = resolve_youtube(
            {"video_id": VALID_VIDEO_ID}, http_get=_fake_get(200, body))
        assert cands == []
        assert "not installed" in err

    def test_hls_present_does_not_invoke_dispatcher(self, monkeypatch):
        """The hot-path optimization: when HLS is in streamingData,
        resolve_youtube never reaches the dispatcher. We confirm by
        making the dispatcher explode if called."""
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "yt-dlp")

        called = []

        def _fail(*a, **kw):
            called.append((a, kw))
            raise AssertionError(
                "_decipher_signed_formats should not be invoked when "
                "HLS is present"
            )

        monkeypatch.setattr(pr, "_decipher_signed_formats", _fail)
        body = _make_watch_html(_hls_present_player_response())
        cands, err = resolve_youtube(
            {"video_id": VALID_VIDEO_ID}, http_get=_fake_get(200, body))
        assert err is None
        # HLS candidate present; signed adaptive silently skipped
        assert any(
            c["source_type"] == "youtube_resolved_hls" for c in cands
        )
        assert called == []

    def test_playerjs_backend_in_resolve(self, monkeypatch):
        # C4-B (v3.66.48): player-js is now implemented. The
        # _signed_only fixture carries no jsUrl, so the leg fails loud
        # with a clear, player-js-tagged error (no longer the stub
        # 'not yet implemented'). This confirms the backend is reached
        # through resolve_youtube; full success coverage lives in
        # test_v3_66_48_c4b_player_js.py.
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "player-js")
        body = _make_watch_html(_signed_only_player_response())
        cands, err = resolve_youtube(
            {"video_id": VALID_VIDEO_ID}, http_get=_fake_get(200, body))
        assert cands == []
        assert "player-js" in err
        assert "not yet implemented" not in err
        assert "jsUrl" in err  # reached the real leg's player-JS step
