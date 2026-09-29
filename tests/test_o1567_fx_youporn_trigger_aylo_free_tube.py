"""o1567 fx-youporn-trigger: youporn.com scene pages go through the Aylo page-config extractor.

MEASURED on test4 (10.0.70.85) 2026-09-29, youporn history rows 46/47/51:
needs_review "no dl event; looks like a modal-trigger button -- set Trigger Selector; saw:
auto(?):Download Time: | auto(?):Save and Close"; row 48 saved a 250p clip (tier=250 avail=[0]).
Live page https://www.youporn.com/watch/17133601/ carries the scene's player config
(``mediaDefinition``: indirect ``/media/hls/?s=`` and ``/media/mp4/?s=`` entries; the mp4 entry answers
a JSON list 1080/720/480/240 on ev-ph.ypncdn.com), and ``extract_media_definitions`` already parses it.

Cause: ``is_aylo_url('https://www.youporn.com/...')`` is False -- youporn.com is in neither AYLO_BRANDS nor
AYLO_FREE_TUBES -- so ``_try_aylo_extractor`` never runs and the DOM scorer picks "Download Time:" /
"Save and Close" widgets. Fix: youporn.com joins AYLO_FREE_TUBES (redtube's route, tpl95-redtube-1).
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import extractors_aylo as aylo

BD_GATE_SCOPE = "module"

SCENE = "https://www.youporn.com/watch/17133601/incredible-first-meeting-with-stepsis/"
HLS = "https:\\/\\/www.youporn.com\\/media\\/hls\\/?s=eyJ2a2V5IjoxMDIxNTgzMDF9"
MP4 = "https:\\/\\/www.youporn.com\\/media\\/mp4\\/?s=eyJ2a2V5IjoxMDIxNTgzMDF9"
# The live page's shape: singular ``mediaDefinition`` inside a player-setup object, no quality field.
PAGE_HTML = (
    "<html><script>var playerObjList = {}; playerObjList.player = {prebufferGoal: 40, "
    'mediaDefinitions: [{"format":"hls","videoUrl":"%s","remote":true},'
    '{"format":"mp4","videoUrl":"%s","remote":true}]};</script></html>' % (HLS, MP4)
)


def test_youporn_scene_url_is_an_aylo_free_tube():
    assert aylo.is_aylo_url(SCENE)
    assert aylo.is_free_tube_url(SCENE)
    assert aylo.is_free_tube_url("https://youporn.com/watch/1/")


def test_negative_control_unrelated_host_is_not_aylo():
    assert not aylo.is_aylo_url("https://www.example-tube.com/watch/17133601/")
    assert not aylo.is_free_tube_url("https://www.notyouporn.com/watch/1/")


def test_youporn_page_config_lists_only_indirect_entries():
    cfg = aylo.extract_media_definitions(PAGE_HTML)
    assert cfg is not None
    defs = cfg["mediaDefinitions"]
    assert len(defs) == 2 and all(aylo.is_indirect_definition(d) for d in defs)
    assert json.dumps(defs).count("youporn.com/media/") == 2


# ---- live finding (test4 2026-09-29 21:13Z): the extractor now wins, but a title-less scene URL ending "/" ----
# ---- (https://www.youporn.com/watch/205140451/) saved the 1080p file as the hidden name ".mp4.mp4". -------
from tests.test_tpl95_redtube_1_page_own_media_definitions import (  # noqa: E402  (fixtures + harness reuse)
    SCENE as _RT_SCENE, _Runner, browser, page, server,
)
from bulk_downloader import runner_extractors  # noqa: E402


def test_title_less_scene_url_with_trailing_slash_gets_a_visible_filename(server, page, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    url = _RT_SCENE + "/"          # same served page (video_title absent), trailing-slash scene URL like youporn
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner(tmp_path)
    r.config["min_resolution"] = 480

    assert r._try_aylo_extractor(url, page) is True

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names and not any(n.startswith(".") for n in names), (
        f"o1567 RED: title-less scene URL ending '/' saved as a hidden name: {names}")
    assert names[0].startswith("191397851"), names


def test_titled_scene_keeps_its_title_name(server, page, tmp_path, monkeypatch):
    """Control: a config that carries video_title still names the file by it."""
    monkeypatch.setattr(runner_extractors, "db_log", lambda *a, **k: None)
    url = _RT_SCENE + "?shell=flashvars"
    page.goto(url, wait_until="domcontentloaded")
    r = _Runner(tmp_path)
    r.config["min_resolution"] = 480
    assert r._try_aylo_extractor(url, page) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Scene.mp4"]
