"""O1567 fx-youporn-title: youporn scenes were saved as ``<id>.mp4`` with an empty title.

MEASURED on test4 (10.0.70.85) 2026-09-29, site youporn 02178bdb: every Aylo download logged
``MP4 metadata written: title='' performer=''`` and landed as 103614901.mp4, 103764961.mp4, ... (one earlier
build even wrote the hidden ``.mp4.mp4``).  A plain curl of https://www.youporn.com/watch/103614901/ shows why:
there is no ``flashvars_<id>`` variable; the player setup script holds ONE JSON object literal
``{..."video_duration":"974",...,"image_url":"...","video_title":"...",...,"mediaDefinitions":[...]}``.
extract_media_definitions falls back to the bare ``mediaDefinitions`` array and returns
``{"mediaDefinitions": [...]}`` -- the title, duration and thumbnail sitting beside it are dropped.

Rule: when the bare mediaDefinitions key sits inside a JSON object literal, that object is the player config.
"""
from __future__ import annotations

import json

from bulk_downloader import extractors_aylo as A

BD_GATE_SCOPE = "module"

CDN = "https://ev-h.ypncdn.com/videos/202401/03/445857111"
DEFS = [{"format": "mp4", "quality": "1080", "videoUrl": f"{CDN}/1080P_4000K_445857111.mp4?validfrom=1"},
        {"format": "mp4", "quality": "480", "videoUrl": f"{CDN}/480P_2000K_445857111.mp4?validfrom=1"}]
TITLE = "2023's Explosive Male Orgasms: Epic Compilation!"
CONFIG = {"video_duration": "974", "link_url": "https://www.youporn.com/watch/103614901/",
          "image_url": "https://fi1-ph.ypncdn.com/videos/202401/03/445857111/original/0.jpg",
          "video_title": TITLE, "video_id": "103614901", "mediaDefinitions": DEFS}


def _page(config_json: str) -> str:
    return ('<html><head><title>x - Free Porn Videos - YouPorn</title></head><body>'
            '<script id="tm_pc_player_setup">function getCookieWatch(a){return a}'
            '\nvar page_params = {"a": {"b": 1}};\n'
            'playerSetup(' + config_json + ');</script></body></html>')


YOUPORN_HTML = _page(json.dumps(CONFIG).replace("/", "\\/"))


def test_bare_media_definitions_keep_their_enclosing_config_object():
    fv = A.extract_media_definitions(YOUPORN_HTML)
    assert fv is not None
    assert fv.get("video_title") == TITLE, (
        f"O1567 YOUPORN TITLE: config beside mediaDefinitions dropped -> title={fv.get('video_title')!r} "
        "(files save as <id>.mp4)")


def test_the_scene_file_carries_title_and_duration():
    res = A.extract_from_html(YOUPORN_HTML, quality_pref=["best"])
    assert res.ok, (res.error, res.error_detail)
    assert "1080P_4000K" in res.variant.url
    assert (res.title, res.duration_sec) == (TITLE, 974), (res.title, res.duration_sec)


def test_control_js_object_literal_still_yields_the_bare_array():
    # unquoted keys: not JSON -> the old bare-array answer, never a crash
    html = _page('{video_title: "t", mediaDefinitions: ' + json.dumps(DEFS) + '}')
    fv = A.extract_media_definitions(html)
    assert fv == {"mediaDefinitions": DEFS}


def test_control_flashvars_pages_are_unchanged():
    html = ("<script>var flashvars_1 = " + json.dumps({"video_title": "fv", "mediaDefinitions": DEFS})
            + ";</script>")
    assert A.extract_media_definitions(html)["video_title"] == "fv"
