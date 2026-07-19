"""v3.66.300+ — AI-3 storage-tell promotion + builder storage_keys wiring.

Two surgical, review-only changes proven RED-first:

1. flowplayer storage markers (AI-3): real flowplayer captures store keys like
   ``flowplayer/flowplayer/uuid``, ``flowplayer/--fp-sub-*`` and
   ``flowplayerTestStorage`` — none matched the old ``^flowplayer[-_.]`` pattern,
   so flowplayer had no storage tell for the arbitration. Broadened to the
   exclusive ``^flowplayer`` brand prefix.

2. builder storage_keys (AI-1 divergence): build_template called detect() WITHOUT
   storage_keys, so the v3.66.171 storage-tell arbitration never ran in the draft
   a reviewer sees — a co-firing video.js shell beat the real engine even when its
   storage tell (e.g. THEOplayer.*) was present in the capture. The builder now
   extracts storage_snapshot key NAMES and feeds them to detect(), matching the
   scorecard/AI-1 path. F2: NAMES only.
"""
import os
import sys
import io
import json
import zipfile
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402
import build_template_from_wacz as btw  # noqa: E402


# ---- Fork 1: flowplayer storage markers ----

def test_flowplayer_storage_confirmed_uuid():
    assert "flowplayer" in pr._storage_confirmed(["flowplayer/flowplayer/uuid"])


def test_flowplayer_storage_confirmed_sub_style():
    assert "flowplayer" in pr._storage_confirmed(["flowplayer/--fp-sub-font-color"])


def test_flowplayer_storage_confirmed_test_storage():
    assert "flowplayer" in pr._storage_confirmed(["flowplayerTestStorage"])


def test_flowplayer_storage_still_matches_legacy_sep():
    # the old separator form must keep matching
    assert "flowplayer" in pr._storage_confirmed(["flowplayer-resume"])


def test_flowplayer_storage_no_false_positive():
    # an unrelated key must NOT confirm flowplayer
    assert "flowplayer" not in pr._storage_confirmed(["myflowplayerx", "videojs_preferred_res"])


# ---- Fork 2: builder feeds storage_keys to detect() ----

def _build(cap: dict) -> dict:
    """Wrap a capture dict into an in-memory .wacz and run the real build_template
    (exercises the production _load_capture + the detect() call site)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("archive/capture.json", json.dumps(cap))
    with tempfile.NamedTemporaryFile(suffix=".wacz", delete=False) as tf:
        tf.write(buf.getvalue())
        tmp = tf.name
    try:
        from pathlib import Path
        return btw.build_template(Path(tmp))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


# A capture with a co-firing video.js shell (vjs-) + theoplayer markup (theo-) and
# a THEOplayer.* storage key: video.js scores higher on markup, but the storage
# tell must flip the engine to theoplayer once the builder feeds storage_keys.
_HTML = (
    "<html><body>"
    "<div class=\"vjs-control-bar vjs-button\"></div>"
    "<div class=\"theo-player theo-controlbar\"></div>"
    "<video class=\"vjs-tech\"></video>"
    "</body></html>"
)
_CAP = {
    "url": "https://app.example.com/scene/1",
    "host": "app.example.com",
    "dom_log": [{"type": "full_snapshot", "html": _HTML}],
    "network_log": [{"url": "https://cdn.example.com/master.m3u8",
                     "response_headers": [{"name": "content-type", "value": "application/vnd.apple.mpegurl"}]}],
    "storage_snapshot": {
        "local_storage": {"THEOplayer.textTrackStyle.fontColor": "x"},
        "session_storage": {},
    },
}


def test_builder_storage_arbitration_flips_to_theoplayer():
    draft = _build(_CAP)
    fam = (draft.get("recognition") or {}).get("player_family")
    assert fam == "theoplayer", f"expected theoplayer via storage arbitration, got {fam}"


def test_builder_no_storage_keys_still_works():
    # same capture minus storage -> video.js shell wins (no tell to arbitrate on);
    # proves the change is storage-driven, not a blanket theoplayer bias.
    cap = dict(_CAP)
    cap["storage_snapshot"] = {"local_storage": {}, "session_storage": {}}
    draft = _build(cap)
    fam = (draft.get("recognition") or {}).get("player_family")
    assert fam == "videojs", f"expected videojs with no storage tell, got {fam}"
