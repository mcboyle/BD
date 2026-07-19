"""v3.66.171 — recognizer storage-tell arbitration (SYNTHETIC).

A runtime storage marker (a localStorage key the engine actually wrote) is
stronger evidence of the ACTIVE engine than static HTML/script markers. When a
storage-confirmed engine co-fires with a static shell (e.g. a leftover video.js
skin), the confirmed engine supersedes the shell — the reptyle videojs-vs-
THEOplayer case. Output stays review-only; demoted families remain in
`candidates`. storage_keys is optional → backward compatible.
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402

# video.js scores strictly higher than THEOplayer on static markup alone
# (data-vjs-player + video-js + vjs-big-play-button + script), reproducing the
# real reptyle shape where detect() picks videojs without the storage tell.
_VJ = ('<div data-vjs-player><video class="video-js"></video>'
       '<button class="vjs-big-play-button"></button></div>')
_SCRIPTS = ["https://cdn/video.min.js", "https://cdn/THEOplayer.chromeless.js"]


def test_storage_keys_optional_backward_compat():
    r = pr.detect(_VJ, script_srcs=["https://cdn/video.min.js"])
    assert r["player_family"] == "videojs"
    assert r["storage_confirmed"] == []


def test_storage_tell_supersedes_static_shell():
    r0 = pr.detect(_VJ, script_srcs=_SCRIPTS)
    assert r0["player_family"] == "videojs"          # static markup: videojs wins
    r1 = pr.detect(_VJ, script_srcs=_SCRIPTS,
                   storage_keys=["THEOplayer.cache.v1", "theoplayer-session-id", "volume"])
    assert r1["player_family"] == "theoplayer"        # runtime storage flips it
    assert "theoplayer" in r1["storage_confirmed"]
    assert any(c["family"] == "videojs" for c in r1["candidates"])  # demoted, still listed
    assert any("Storage tells" in n for n in r1["notes"])


def test_self_confirmed_engine_not_demoted():
    r = pr.detect(_VJ, script_srcs=["https://cdn/video.min.js"],
                  storage_keys=["vjs-volume", "vjs-text-track-settings"])
    assert r["player_family"] == "videojs"
    assert "videojs" in r["storage_confirmed"]


def test_unrelated_storage_keys_have_no_effect():
    r = pr.detect(_VJ, script_srcs=["https://cdn/video.min.js"],
                  storage_keys=["theme", "consent", "_ga"])
    assert r["player_family"] == "videojs"
    assert r["storage_confirmed"] == []
