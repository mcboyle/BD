"""C4-B — in-process player-JS YouTube signatureCipher decipher.

Replaces the v3.66.26 stub ``_decipher_signed_formats_playerjs``. Built
under an explicit, operator-recorded posture override (see the v3.66.48
handoff): functionally equivalent to the already-shipped yt-dlp backend,
done in-process so a deployment without yt-dlp on PATH can still resolve
signed YouTube formats. Scope is signature OBFUSCATION only — no DRM,
paywall, or age-gate bypass — and the leg FAILS LOUD on cipher rotation.

All tests are deterministic and offline: a synthetic player-JS bundle
with a known transform pipeline + a fabricated watch page are fed through
a URL-routing fake ``http_get``. The expected deciphered signature is
computed by hand below so the test pins exact decipher correctness, not
just "it ran".

Decipher pipeline in the fixture (applied to the signature, in order):
    reverse(),  swap(2),  splice(1)

Worked example for s = "ABCDEFGHIJ":
    split   -> [A,B,C,D,E,F,G,H,I,J]
    reverse -> [J,I,H,G,F,E,D,C,B,A]
    swap 2  -> [H,I,J,G,F,E,D,C,B,A]   (swap idx0 <-> idx2)
    splice1 -> [I,J,G,F,E,D,C,B,A]     (drop first 1)
    join    -> "IJGFEDCBA"
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import provider_resolve as pr
from bulk_downloader.provider_resolve import (
    _apply_yt_decipher_ops,
    _build_yt_decipher_ops,
    _decipher_signed_formats_playerjs,
    resolve_youtube,
)

VALID_VIDEO_ID = "dQw4w9WgXcQ"
EXPECTED_SIG = "IJGFEDCBA"          # decipher("ABCDEFGHIJ") per the pipeline
JS_PATH = "/s/player/abcd1234/player_ias.vflset/en_US/base.js"

# A synthetic player JS with the three canonical transforms + a decipher
# function that calls them in the order reverse, swap(2), splice(1).
PLAYER_JS = (
    "var Mz={"
    "Ht:function(a){a.reverse()},"
    "Gk:function(a,b){a.splice(0,b)},"
    "Pq:function(a,b){var c=a[0];a[0]=a[b%a.length];a[b%a.length]=c}"
    "};"
    'decipherSig=function(a){a=a.split("");'
    "Mz.Ht(a,0);Mz.Pq(a,2);Mz.Gk(a,1);"
    'return a.join("")};'
    "\nvar somethingElse=1;\n"
).encode("utf-8")


def _player_response(*, status="OK", signed=True):
    fmt = {"itag": 18, "height": 360, "width": 640,
           "mimeType": "video/mp4", "qualityLabel": "360p",
           "contentLength": "12345", "fps": 30, "bitrate": 500000}
    if signed:
        # url's internal '&'/'/'/':' are %-encoded the way YouTube does it.
        fmt["signatureCipher"] = (
            "s=ABCDEFGHIJ&sp=sig&url="
            "https%3A%2F%2Fr3.googlevideo.com%2Fvideoplayback"
            "%3Fid%3Dxyz%26itag%3D18")
    else:
        fmt["url"] = "https://r3.googlevideo.com/videoplayback?id=xyz&itag=18"
    return {"playabilityStatus": {"status": status},
            "streamingData": {"formats": [fmt]}}


def _watch_html(player_response, *, js_path=JS_PATH):
    body = json.dumps(player_response)
    esc = js_path.replace("/", "\\/") if js_path else None
    js_block = (f'<script>var z={{"jsUrl":"{esc}"}};</script>'
                if esc is not None else "")
    return (
        "<!doctype html><html><body><script>"
        "var ytInitialPlayerResponse = " + body + ";"
        "</script>" + js_block + "</body></html>"
    ).encode("utf-8")


def _router(watch_body, js_body, *, watch_status=200, js_status=200):
    """Fake http_get that returns the player JS for base.js URLs and the
    watch HTML for everything else."""
    def _get(url):
        if "base.js" in url or "/s/player/" in url:
            return (js_status, {}, js_body)
        return (watch_status, {}, watch_body)
    return _get


EXPECTED_URL = (
    "https://r3.googlevideo.com/videoplayback?id=xyz&itag=18"
    f"&sig={EXPECTED_SIG}")


# ── unit: the decipher math ───────────────────────────────────────────


class TestDecipherMath:
    def test_apply_ops_worked_example(self):
        ops = [("reverse", 0), ("swap", 2), ("splice", 1)]
        assert _apply_yt_decipher_ops("ABCDEFGHIJ", ops) == EXPECTED_SIG

    def test_reverse_only(self):
        assert _apply_yt_decipher_ops("abc", [("reverse", 0)]) == "cba"

    def test_splice_only(self):
        assert _apply_yt_decipher_ops("abcde", [("splice", 2)]) == "cde"

    def test_swap_only(self):
        # swap(3) on "abcde": 3 % 5 = 3, swap idx0<->idx3 -> "dbcae"
        assert _apply_yt_decipher_ops("abcde", [("swap", 3)]) == "dbcae"

    def test_build_ops_from_synthetic_player_js(self):
        ops, err = _build_yt_decipher_ops(PLAYER_JS.decode())
        assert err is None
        assert ops == [("reverse", 0), ("swap", 2), ("splice", 1)]


# ── leg: successful decipher ──────────────────────────────────────────


class TestPlayerJsSuccess:
    def _run(self):
        http = _router(_watch_html(_player_response()), PLAYER_JS)
        return _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, embed_url="https://host/embed/x", http_get=http)

    def test_produces_one_candidate(self):
        cands, err = self._run()
        assert err is None
        assert len(cands) == 1

    def test_url_is_correctly_deciphered(self):
        cands, _ = self._run()
        assert cands[0]["url"] == EXPECTED_URL

    def test_distinct_source_type(self):
        cands, _ = self._run()
        assert cands[0]["source_type"] == "youtube_resolved_cipher_playerjs"

    def test_metadata_surfaced(self):
        cands, _ = self._run()
        c = cands[0]
        assert c["itag"] == 18
        assert c["resolution"]["height"] == 360
        assert c["score"] == 80 + 3  # 360 // 100
        assert c["provider_resolved"] is True
        assert c["resolved_from"] == "https://host/embed/x"
        # honest warning about the in-process backend + TTL
        assert any("cipher rotation" in w for w in c["warnings"])


# ── leg: fail-loud paths (no silent fallback) ─────────────────────────


class TestPlayerJsFailsLoud:
    def test_rotation_missing_decipher_fn(self):
        # player JS with the transform object but NO decipher function:
        # simulates YouTube changing the markup the regex keys on.
        broken_js = (b"var Mz={Ht:function(a){a.reverse()}};"
                     b"\nvar unrelated=2;\n")
        http = _router(_watch_html(_player_response()), broken_js)
        cands, err = _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, http_get=http)
        assert cands == []
        assert "parse failed for player JS at" in err
        assert JS_PATH.split("/")[-1] in err  # the js url is named

    def test_rotation_unrecognized_transform(self):
        # An object method that is none of reverse/splice/swap -> fail loud.
        weird_js = (
            "var Mz={"
            "Ht:function(a){a.reverse()},"
            "Zz:function(a,b){a.frobnicate(b)}"
            "};"
            'decipherSig=function(a){a=a.split("");Mz.Ht(a,0);Mz.Zz(a,3);'
            'return a.join("")};'
        ).encode()
        http = _router(_watch_html(_player_response()), weird_js)
        cands, err = _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, http_get=http)
        assert cands == []
        assert "parse failed for player JS at" in err
        assert "unrecognized transform" in err

    def test_missing_js_url(self):
        http = _router(_watch_html(_player_response(), js_path=None),
                       PLAYER_JS)
        cands, err = _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, http_get=http)
        assert cands == []
        assert "could not locate jsUrl" in err

    def test_non_youtube_js_host_refused(self):
        http = _router(
            _watch_html(_player_response(), js_path="https://evil.test/x.js"),
            PLAYER_JS)
        cands, err = _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, http_get=http)
        assert cands == []
        assert "non-YouTube host" in err
        assert "evil.test" in err

    def test_playability_not_ok_refused(self):
        http = _router(
            _watch_html(_player_response(status="LOGIN_REQUIRED")), PLAYER_JS)
        cands, err = _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, http_get=http)
        assert cands == []
        assert "playabilityStatus=LOGIN_REQUIRED" in err

    def test_bad_video_id_rejected_before_network(self):
        called = []

        def _boom(url):
            called.append(url)
            raise AssertionError("must not fetch on bad video_id")

        cands, err = _decipher_signed_formats_playerjs("short", http_get=_boom)
        assert cands == []
        assert "rejected" in err.lower()
        assert called == []

    def test_watch_404(self):
        http = _router(b"", PLAYER_JS, watch_status=404)
        cands, err = _decipher_signed_formats_playerjs(
            VALID_VIDEO_ID, http_get=http)
        assert cands == []
        assert "404" in err


# ── integration: through resolve_youtube + the dispatcher ─────────────


class TestResolveYoutubeIntegration:
    def test_player_js_backend_flows_to_candidates(self, monkeypatch):
        monkeypatch.setenv("BD_YOUTUBE_CIPHER", "player-js")
        http = _router(_watch_html(_player_response()), PLAYER_JS)
        cands, err = resolve_youtube({"video_id": VALID_VIDEO_ID},
                                     http_get=http)
        assert err is None
        assert len(cands) == 1
        assert cands[0]["source_type"] == "youtube_resolved_cipher_playerjs"
        assert cands[0]["url"] == EXPECTED_URL

    def test_off_backend_still_reports_signed_count(self, monkeypatch):
        # Sanity: with the backend off, the signed format is skipped and
        # the v3.66.20 'signed' + count contract holds (player-js doesn't
        # change the default path).
        monkeypatch.delenv("BD_YOUTUBE_CIPHER", raising=False)
        http = _router(_watch_html(_player_response()), PLAYER_JS)
        cands, err = resolve_youtube({"video_id": VALID_VIDEO_ID},
                                     http_get=http)
        assert cands == []
        assert "signed" in err.lower()
