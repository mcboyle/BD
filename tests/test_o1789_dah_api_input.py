"""dah-api-input (O1789 deep-adapters-http F8..F12): the yt-dlp / gallery-dl update,
yt-dlp archive and multi-conn probe endpoints must answer malformed input with a
client error instead of a 500, parse a string "force" honestly, validate before the
outbound probe, survive a non-string archive path, and derive a YouTube id whatever
the query-parameter order.

Each test asserts the CORRECT behaviour; on the pre-fix tree it fails with a
DAH_F<n>_* diagnostic. Outbound probes are faked: nothing leaves the process.
"""
from __future__ import annotations

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

import json
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from bulk_downloader import (app_gallerydl_update, app_multi_conn, app_ytdlp_archive,
                             app_ytdlp_update, multi_conn, yt_dlp_archive)

_URL = "https://cdn.example/a.mp4"


def _make_client(monkeypatch, site_cfg: dict | None = None):
    app = Flask(__name__)
    app.config["TESTING"] = True
    # Serve an uncaught view error as the 500 a real client gets, so each
    # finding reports its DAH_F diagnostic instead of the raw exception.
    app.config["PROPAGATE_EXCEPTIONS"] = False
    for mod in (app_ytdlp_update, app_gallerydl_update, app_ytdlp_archive, app_multi_conn):
        monkeypatch.setattr(mod, "_check_csrf", lambda *_a, **_k: None)
    monkeypatch.setattr(app_ytdlp_archive, "_app__YTDLP_ARCH_AVAILABLE", lambda: True)
    monkeypatch.setattr(app_ytdlp_archive, "_app__ytdlp_archive_module", lambda: yt_dlp_archive)
    monkeypatch.setattr(app_ytdlp_archive, "_app_s_cfg", lambda: site_cfg if site_cfg is not None else {})
    monkeypatch.setattr(app_multi_conn, "_app__MULTI_CONN_AVAILABLE", lambda: True)
    monkeypatch.setattr(app_multi_conn, "_app__multi_conn", lambda: multi_conn)
    monkeypatch.setattr(app_multi_conn, "_app_s_cfg", lambda: site_cfg if site_cfg is not None else {})
    for bp in (app_ytdlp_update.ytdlp_update_bp, app_gallerydl_update.gallerydl_update_bp,
               app_ytdlp_archive.ytdlp_archive_bp, app_multi_conn.multi_conn_bp):
        app.register_blueprint(bp)
    return app.test_client()


def _fake_probe(monkeypatch, length):
    probed: list[str] = []

    def probe(url, **_kw):
        probed.append(url)
        return multi_conn.ProbeResult(ok=True, content_length=length, accept_ranges=True)
    monkeypatch.setattr(multi_conn, "probe", probe)
    return probed


def test_f8_updater_endpoints_body_and_force_parsing(monkeypatch):
    c = _make_client(monkeypatch)
    for target_mod, path in (("bulk_downloader.ytdlp_updater", "/api/ytdlp_update"),
                             ("bulk_downloader.gallerydl_updater", "/api/gallerydl_update")):
        forced: list[bool] = []
        with patch(f"{target_mod}.maybe_update",
                   side_effect=lambda force=False: forced.append(force) or (False, "noop")), \
                patch(f"{target_mod}.status_dict", return_value={"installed": True}):
            r = c.post(path, data="", content_type="text/plain")
            assert r.status_code == 200 and forced == [False], (
                f"DAH_F8_BODY_500: {path} text/plain empty body -> {r.status_code}, force calls {forced}")
            forced.clear()
            r = c.post(path, data="[1]", content_type="application/json")
            assert r.status_code == 400 and forced == [], (
                f"DAH_F8_BODY_500: {path} body [1] -> {r.status_code}, force calls {forced}")
            for false_val in ("false", "False", "0", "no", "off", ""):
                forced.clear()
                r = c.post(path, data=json.dumps({"force": false_val}), content_type="application/json")
                assert r.status_code == 200 and forced == [False], (
                    f"DAH_F8_STRING_FORCE_TRUE: {path} force={false_val!r} ran maybe_update(force={forced})")
            forced.clear()
            assert c.post(path, json={"force": False}).status_code == 200 and forced == [False]
            forced.clear()
            assert c.post(path, json={"force": True}).status_code == 200 and forced == [True]  # control


def test_f9_derive_and_probe_reject_non_object_body_and_non_string_url(monkeypatch):
    c = _make_client(monkeypatch)
    probed = _fake_probe(monkeypatch, 1024)
    for path in ("/api/ytdlp_archive/derive", "/api/multi_conn/probe"):
        r = c.post(path, data="[1]", content_type="application/json")
        assert r.status_code == 400 and r.get_json()["ok"] is False, (
            f"DAH_F9_MALFORMED_500: {path} body [1] -> {r.status_code}")
        r = c.post(path, json={"url": 123})
        assert r.status_code in (200, 400) and r.get_json()["ok"] is False, (
            f"DAH_F9_MALFORMED_500: {path} url=123 -> {r.status_code}")
    assert probed == []
    r = c.post("/api/ytdlp_archive/derive", json={"url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"})
    assert r.status_code == 200 and r.get_json()["archive_line"] == "youtube AAAAAAAAAAA"  # control


def test_f10_min_size_mb_validated_before_the_outbound_probe(monkeypatch):
    c = _make_client(monkeypatch)
    probed = _fake_probe(monkeypatch, 10 * 1024 * 1024)
    for bad in ("abc", [1], {"a": 1}, True):
        r = c.post("/api/multi_conn/probe", json={"url": _URL, "min_size_mb": bad})
        assert r.status_code == 400 and r.get_json()["ok"] is False, (
            f"DAH_F10_MIN_SIZE_NOT_VALIDATED: min_size_mb={bad!r} -> {r.status_code}, probes run {probed}")
    assert probed == [], f"DAH_F10_PROBE_BEFORE_VALIDATION: outbound probe ran for bad input: {probed}"
    r = c.post("/api/multi_conn/probe", json={"url": _URL, "min_size_mb": 5})
    assert r.status_code == 200 and r.get_json()["viable"] is True and probed == [_URL]  # control


def test_f11_archive_stats_survive_a_non_string_archive_path(monkeypatch, tmp_path: Path):
    archive = tmp_path / "archive.txt"
    archive.write_text("youtube AAAAAAAAAAA\n", encoding="utf-8")
    r = _make_client(monkeypatch, {"bad_site": {"ytdlp_archive_path": 12345}}).get("/api/ytdlp_archive/stats")
    assert r.status_code == 200 and r.get_json()["configured"] is False, (
        f"DAH_F11_ARCHIVE_PATH_TYPE_500: one non-string ytdlp_archive_path -> {r.status_code}")
    try:
        helper = yt_dlp_archive.stats_for_archive(12345)
    except TypeError as exc:
        helper = f"raised {exc}"
    assert helper == {"total": 0, "by_extractor": {}}, (
        f"DAH_F11_ARCHIVE_PATH_TYPE_500: stats_for_archive(12345) -> {helper}")
    r = _make_client(monkeypatch, {"bad_site": {"ytdlp_archive_path": 12345},
                                   "ok_site": {"ytdlp_archive_path": str(archive)}}).get("/api/ytdlp_archive/stats")
    body = r.get_json()
    assert r.status_code == 200 and body["archive_path"] == str(archive) and body["stats"]["total"] == 1, (
        f"DAH_F11_ARCHIVE_PATH_TYPE_500: mixed sites -> {r.status_code} {body}")


def test_f12_derive_id_youtube_v_after_other_query_params():
    for url in ("https://www.youtube.com/watch?t=1&v=AAAAAAAAAAA",
                "https://www.youtube.com/watch?feature=shared&t=30s&v=AAAAAAAAAAA"):
        got = yt_dlp_archive.derive_id(url)
        assert got == ("youtube", "AAAAAAAAAAA"), f"DAH_F12_V_PARAM_MISSED: derive_id({url!r}) -> {got}"
    # controls: forms that already worked, and an unknown host
    assert yt_dlp_archive.derive_id("https://www.youtube.com/watch?v=AAAAAAAAAAA&t=1") == ("youtube", "AAAAAAAAAAA")
    assert yt_dlp_archive.derive_id("https://youtu.be/AAAAAAAAAAA?si=x") == ("youtube", "AAAAAAAAAAA")
    assert yt_dlp_archive.derive_id("https://www.youtube.com/shorts/AAAAAAAAAAA") == ("youtube", "AAAAAAAAAAA")
    assert yt_dlp_archive.derive_id("https://unknown.example/videos/abc") is None
