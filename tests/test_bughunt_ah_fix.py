"""bughunt ah-fix: the adapters-http sweep's FOUND rows (F1..F14), one test group per row.

Each test is the finder's repro (harness-work/O1789/bughunt/adapters-http/FINDINGS.md)
turned into an assertion, plus its control: the benign input that must keep working.
Hermetic: tmp_path, loopback fixtures and fake binaries only; no real yt-dlp,
gallery-dl, pip or third-party extractor library is run.
"""

from __future__ import annotations

import http.server
import os
import stat
import sys
import threading
import types
from pathlib import Path

import pytest

from bulk_downloader import (
    direct_writer,
    extractors,
    extractors_jsonapi,
    plugins,
    runner_extractors as RE,
    runner_transport as RT,
    yt_dlp_archive,
    ytdlp_extractor,
    ytdlp_updater,
)

BD_GATE_SCOPE = "module"

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4000


class Stub(RT.TransportMixin, RE.ExtractorsMixin):
    """The real mixins on the smallest host that satisfies them."""

    def __init__(self, config, site_id="ahsite"):
        self.site_id, self.config, self.cookies = site_id, config, []
        self.events, self.jobs = [], []

    def log_event(self, *a, **k):
        self.events.append(a)

    def _update_job(self, *a, **k):
        self.jobs.append((a, k))
        return True

    def _download_proxy_url(self):
        return None

    def _regional_gateway_router(self):
        return None


@pytest.fixture(autouse=True)
def _no_history(monkeypatch):
    monkeypatch.setattr(RE, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(RE, "history_title_kwargs", lambda *a, **k: {})


def _inside(path, root) -> bool:
    real, base = os.path.realpath(path), os.path.realpath(root)
    return real != base and os.path.commonpath([real, base]) == base


def _serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **k):
        pass


# ── F1 / F3: destination stays inside download_dir ──────────────────────────


class _RecordingStub(Stub):
    def _do_direct_http_download(self, *, page_url, file_url, output_path, referer=""):
        self.wrote = output_path
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"x" * 10)
        return True


@pytest.mark.parametrize(
    "result",
    [
        {"title": ".", "ext": "/../../esc/evil.sh"},
        {"title": "..", "ext": "/../x.mp4"},
        {"title": "clip", "ext": "/../../../clip.mp4"},
    ],
)
def test_f1_plugin_result_cannot_leave_download_dir(tmp_path, monkeypatch, result):
    dl = tmp_path / "a" / "b" / "dl"
    s = _RecordingStub({"download_dir": str(dl)})
    monkeypatch.setattr(
        plugins,
        "get_extractor",
        lambda site_id: (
            lambda url, ctx: {"video_url": "http://127.0.0.1:9/a.mp4", **result}
        ),
    )
    assert s._try_plugin_extractor("http://127.0.0.1:9/watch/1") is True
    assert _inside(s.wrote, dl), s.wrote
    assert not (tmp_path / "a" / "esc").exists()


def test_f1_control_benign_plugin_name_is_unchanged(tmp_path, monkeypatch):
    dl = tmp_path / "dl"
    s = _RecordingStub({"download_dir": str(dl)})
    monkeypatch.setattr(
        plugins,
        "get_extractor",
        lambda site_id: (
            lambda url, ctx: {
                "video_url": "http://127.0.0.1:9/a.mp4",
                "title": "clip",
                "ext": "mp4",
            }
        ),
    )
    assert s._try_plugin_extractor("http://127.0.0.1:9/watch/1") is True
    assert s.wrote == str(dl / "clip.mp4")


@pytest.mark.parametrize(
    "rendered",
    ["../../esc/evil.mp4", "/abs/evil.mp4", "..", "a/../../evil.mp4", ".", "a/.."],
)
def test_f3_dest_in_dir_contains_a_hostile_name(tmp_path, rendered):
    dl = tmp_path / "x" / "dl"
    dl.mkdir(parents=True)
    path, name = RE._dest_in_dir(str(dl), rendered)
    assert _inside(path, dl), path
    assert name == os.path.basename(path) and name not in ("", ".", "..")


def test_f3_control_template_subdir_is_kept(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    path, name = RE._dest_in_dir(str(dl), "Studio/Scene 1.mp4")
    assert path == str(dl / "Studio" / "Scene 1.mp4") and name == "Scene 1.mp4"


# ── F2: site cookies stay on the configured API origin ──────────────────────


def _cookie_servers():
    seen = {"home": [], "away": []}

    class Away(_Quiet):
        def do_GET(self):
            seen["away"].append(self.headers.get("Cookie"))
            body = b'{"title": "t", "media": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    away_srv, away = _serve(Away)

    class Home(_Quiet):
        def do_GET(self):
            seen["home"].append((self.path, self.headers.get("Cookie")))
            if self.path.endswith("/same"):
                body = b'{"title": "same", "media": []}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            # "localhost" is another origin than 127.0.0.1 on the wire.
            target = (
                away.replace("127.0.0.1", "localhost") + "/x"
                if self.path.endswith(("/1", "/deovr"))
                else "/heresphere/same"
            )
            self.send_header("Location", target)
            self.send_header("Content-Length", "0")
            self.end_headers()

    home_srv, home = _serve(Home)
    return seen, home, (home_srv, away_srv)


def test_f2_cookies_do_not_follow_a_redirect_to_another_origin():
    seen, home, servers = _cookie_servers()
    try:
        data = extractors_jsonapi.fetch_scene(
            home + "/heresphere", "1", cookies={"session": "SECRET"}
        )
    finally:
        for s in servers:
            s.shutdown()
    assert data == {"title": "t", "media": []}  # the hop is still followed
    assert seen["home"] == [("/heresphere/1", "session=SECRET")]
    assert seen["away"] == [None], seen["away"]


def test_f2_control_same_origin_redirect_keeps_the_cookies():
    seen, home, servers = _cookie_servers()
    try:
        data = extractors_jsonapi.fetch_scene(
            home + "/heresphere", "2", cookies={"session": "SECRET"}
        )
    finally:
        for s in servers:
            s.shutdown()
    assert data == {"title": "same", "media": []}
    assert [c for _p, c in seen["home"]] == ["session=SECRET", "session=SECRET"]


def test_f2_probe_site_keeps_cookies_home_too():
    seen, home, servers = _cookie_servers()
    try:
        extractors_jsonapi.probe_site(home, cookies={"session": "SECRET"})
    finally:
        for s in servers:
            s.shutdown()
    assert seen["home"] and all(c == "session=SECRET" for _p, c in seen["home"])
    assert seen["away"] == [None], seen["away"]  # /deovr hop: followed, no cookies


# ── F4: one job fetches one playlist entry ──────────────────────────────────


def test_f4_ytdlp_cmd_fetches_a_single_entry():
    cmd = RE._build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/d", url="http://h/two.html")
    i = cmd.index("--playlist-items")
    assert cmd[i + 1] == "1" and i < cmd.index("--")
    assert cmd[-2:] == [
        "--",
        "http://h/two.html",
    ]  # control: the URL stays the lone positional


# ── F5: gallery-dl output is what gallery-dl says it wrote ──────────────────


def _fake_gallerydl(tmp_path, body):
    exe = tmp_path / "bin" / "gallery-dl"
    exe.parent.mkdir()
    exe.write_text(
        "#!%s\nimport os, sys\ndl = sys.argv[sys.argv.index('-d') + 1]\n%s\n"
        % (sys.executable, body)
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


_FOREIGN = (
    "open(os.path.join(dl, 'OTHER-JOB-video.mp4.part'), 'wb').write(b'x' * 1048576)\n"
)
_OWN = (
    "p = os.path.join(dl, 'gallery-dl', 'site'); os.makedirs(p, exist_ok=True)\n"
    "f = os.path.join(p, 'job-image.jpg'); open(f, 'wb').write(b'j' * 2048); print(f)\n"
)


def test_f5_another_jobs_file_is_not_this_jobs_output(tmp_path, monkeypatch):
    exe = _fake_gallerydl(tmp_path, _FOREIGN + _OWN)
    monkeypatch.setattr(
        "shutil.which", lambda name: exe if name == "gallery-dl" else None
    )
    dl = tmp_path / "dl"
    ok, msg, filename, size, fetched = Stub(
        {"download_dir": str(dl), "use_gallerydl_fallback": True}
    )._try_gallerydl_fallback("http://h/g/1")
    assert ok is True, msg
    assert os.path.basename(filename) == "job-image.jpg" and size == fetched == 2048


def test_f5_only_a_foreign_file_is_a_miss(tmp_path, monkeypatch):
    exe = _fake_gallerydl(tmp_path, _FOREIGN)
    monkeypatch.setattr(
        "shutil.which", lambda name: exe if name == "gallery-dl" else None
    )
    res = Stub(
        {"download_dir": str(tmp_path / "dl"), "use_gallerydl_fallback": True}
    )._try_gallerydl_fallback("http://h/g/1")
    assert res == (False, "gallery-dl produced no new file", None, 0, 0)


# ── F6 / F14: yt-dlp shim ───────────────────────────────────────────────────


def test_f6_unknown_codecs_are_not_absent_streams():
    info = {
        "title": "clip",
        "ext": "mp4",
        "formats": [
            {"url": "http://h/a.mp4", "protocol": "http", "vcodec": None, "ext": "mp4"}
        ],
    }
    assert ytdlp_extractor.info_to_extractor_result(info) == {
        "video_url": "http://h/a.mp4",
        "is_hls": False,
        "title": "clip",
        "ext": "mp4",
    }


def test_f6_control_declared_absent_stream_is_still_skipped():
    for fmt in (
        {"vcodec": "none", "acodec": "mp4a"},
        {"vcodec": "avc1", "acodec": "none"},
    ):
        info = {"formats": [{"url": "http://h/a.mp4", "protocol": "https", **fmt}]}
        assert ytdlp_extractor.info_to_extractor_result(info) == {}


@pytest.mark.parametrize("title", [12345, ["a"], {"x": 1}, 1.5])
def test_f14_shim_survives_a_non_string_title(title):
    info = {"title": title, "ext": 7, "url": "http://h/a.mp4", "protocol": "https"}
    assert ytdlp_extractor.info_to_extractor_result(info) == {
        "video_url": "http://h/a.mp4",
        "is_hls": False,
    }


# ── F7: a signed playlist is HLS ────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,is_hls",
    [
        ("https://c/v/index.m3u8?token=abc", True),
        ("https://c/v/playlist.M3U8?hdnea=exp~1", True),
        ("https://c/v/master.m3u8", True),
        ("https://c/v/scene.mp4?next=list.m3u8", False),  # control
        ("https://c/v/scene.mp4", False),  # control
    ],
)
def test_f7_hls_is_read_from_the_path(url, is_hls):
    assert extractors_jsonapi._normalize_source({"url": url}).is_hls is is_hls


# ── F8: scene id ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,scene_id",
    [
        ("https://s/scene/12345?v=2", "12345"),
        ("https://s/scene/12345?video=hd", "12345"),
        ("https://s/watch?id=../../account", "watch"),
        ("https://s/watch?id=777", "777"),  # control: query id when the path has none
        ("https://s/scene/12345", "12345"),  # control
    ],
)
def test_f8_scene_id_prefers_the_path_and_is_never_a_path(url, scene_id):
    assert extractors_jsonapi._extract_scene_id(url) == scene_id


def test_f8_scene_id_is_one_segment_of_the_api_url():
    paths = []

    class H(_Quiet):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv, base = _serve(H)
    try:
        extractors_jsonapi.fetch_scene(base + "/api/heresphere", "a/../../account?x=1")
        assert extractors_jsonapi.fetch_scene(base + "/api/heresphere", "..") is None
    finally:
        srv.shutdown()
    assert paths == ["/api/heresphere/a%2F..%2F..%2Faccount%3Fx%3D1"]


# ── F9: yt-dlp archive ids ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/embed/videoseries?list=PL123",
        "https://www.youtube.com/subscription_center?add_user=x",
        "https://notredtube.com/12345",
        "https://example.org/out?to=redtube.com/12345",
        "https://example.org/out?to=https://www.redtube.com/12345",
    ],
)
def test_f9_no_id_for_a_url_that_is_not_that_video(url):
    assert yt_dlp_archive.derive_id(url) is None


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ("youtube", "dQw4w9WgXcQ")),
        ("https://youtu.be/dQw4w9WgXcQ", ("youtube", "dQw4w9WgXcQ")),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", ("youtube", "dQw4w9WgXcQ")),
        ("https://www.redtube.com/12345", ("redtube", "12345")),
        ("redtube.com/12345", ("redtube", "12345")),
        (
            "https://www.pornhub.com/view_video.php?viewkey=ph5f1a",
            ("pornhub", "ph5f1a"),
        ),
        ("https://spankbang.com/abc12/video/title", ("spankbang", "abc12")),
    ],
)
def test_f9_control_real_video_urls_still_derive(url, expected):
    assert yt_dlp_archive.derive_id(url) == expected


def test_f9_append_after_a_line_without_newline(tmp_path):
    arc = tmp_path / "archive.txt"
    arc.write_text("youtube AAAAAAAAAAA")  # no final newline
    assert yt_dlp_archive.append_entry(str(arc), "youtube", "BBBBBBBBBBB") is True
    assert arc.read_text() == "youtube AAAAAAAAAAA\nyoutube BBBBBBBBBBB\n"
    assert yt_dlp_archive.is_in_archive(str(arc), "youtube", "AAAAAAAAAAA")
    assert yt_dlp_archive.is_in_archive(str(arc), "youtube", "BBBBBBBBBBB")
    assert (
        yt_dlp_archive.append_entry(str(arc), "youtube", "CCCCCCCCCCC") is True
    )  # control
    assert arc.read_text().count("\n") == 3 and "\n\n" not in arc.read_text()


# ── F10: quality preference ─────────────────────────────────────────────────


def _fake_eaf(monkeypatch, asked):
    lib = types.ModuleType("fake_eaf_api")

    class Quality:
        BEST, HALF, WORST = "Q_BEST", "Q_HALF", "Q_WORST"

    class Video:
        title = "t"

        def get_m3u8_by_quality(self, quality):
            asked.append(quality)
            return "https://c/v/master.m3u8"

    class Client:
        def get_video(self, url):
            return Video()

    lib.Quality, lib.Client = Quality, Client
    monkeypatch.setattr(extractors, "_try_import", lambda name: lib)


@pytest.mark.parametrize(
    "pref,asked_for,label",
    [
        (["720"], "Q_BEST", "best"),
        (["480", "best"], "Q_BEST", "best"),
        (["1080"], "Q_BEST", "best"),
        (["worst"], "Q_WORST", "worst"),  # control
        (["best"], "Q_BEST", "best"),  # control
    ],
)
def test_f10_eaf_result_names_the_quality_it_asked_for(
    monkeypatch, pref, asked_for, label
):
    asked = []
    _fake_eaf(monkeypatch, asked)
    res = extractors._generic_eaf_adapter("fake_eaf_api", "https://s/v/1", pref, None)
    assert res.ok and asked == [asked_for] and res.quality == label


def _sources(*heights):
    return [
        extractors_jsonapi._normalize_source(
            {"url": f"https://c/{i}.mp4", "resolution": h}
        )
        for i, h in enumerate(heights)
    ]


def test_f10_worst_picks_the_lowest_tier():
    srcs = _sources(2160, 1080, 480)
    for word in ("worst", "lowest", "min"):
        assert extractors_jsonapi.pick_source(srcs, [word]).height == 480
    assert extractors_jsonapi.pick_source(srcs, ["best"]).height == 2160  # control
    assert extractors_jsonapi.pick_source(srcs, [1080]).height == 1080  # control


@pytest.mark.parametrize(
    "raw,height",
    [
        ("1080.0", 1080),
        ("1920x1080", 1080),
        ("4K", 2160),
        (1080.0, 1080),
        ("1080p", 1080),
        (720, 720),
        ("n/a", 0),
        (None, 0),
    ],
)
def test_f10_height_shapes(raw, height):
    assert (
        extractors_jsonapi._normalize_source(
            {"url": "https://c/a.mp4", "resolution": raw}
        ).height
        == height
    )


# ── F11: an empty 200 is not a file ─────────────────────────────────────────


class _Body(_Quiet):
    def do_GET(self):
        body = b"" if self.path.startswith("/empty") else MP4
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        if not self.path.startswith("/empty-nolen"):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.mark.parametrize("path", ["/empty", "/empty-nolen"])
def test_f11_empty_body_is_a_failed_transfer(tmp_path, path):
    srv, base = _serve(_Body)
    out = tmp_path / "dl" / "scene.mp4"
    out.parent.mkdir()
    try:
        ok = Stub({"download_dir": str(out.parent)})._do_direct_http_download(
            page_url=base + "/p", file_url=base + path, output_path=str(out)
        )
    finally:
        srv.shutdown()
    assert ok is False and not out.exists()


def test_f11_control_a_real_body_lands(tmp_path):
    srv, base = _serve(_Body)
    out = tmp_path / "dl" / "scene.mp4"
    out.parent.mkdir()
    try:
        ok = Stub({"download_dir": str(out.parent)})._do_direct_http_download(
            page_url=base + "/p", file_url=base + "/full", output_path=str(out)
        )
    finally:
        srv.shutdown()
    assert ok is True and out.read_bytes() == MP4


# ── F12: the fallbacks keep their five-value contract ───────────────────────


def _failing_exe(tmp_path, name):
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\necho \"$@\" > '%s.argv'\nexit 3\n" % exe)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return str(exe)


@pytest.mark.parametrize(
    "config,expect_flag",
    [
        ({"download_rate_limit": 500000}, "--limit-rate 500000"),
        ({"min_resolution": "720p"}, "height>=720"),
        ({"min_resolution": "high"}, "--no-playlist"),
        (
            {"download_rate_limit": "2M", "min_resolution": 1080},
            "--limit-rate 2M",
        ),  # control
    ],
)
def test_f12_ytdlp_fallback_returns_five_values(
    tmp_path, monkeypatch, config, expect_flag
):
    exe = _failing_exe(tmp_path, "yt-dlp")
    monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda *a, **k: (exe,))
    res = Stub(
        {"download_dir": str(tmp_path / "dl"), "use_ytdlp_fallback": True, **config}
    )._try_ytdlp_fallback("http://h/v/1")
    assert len(res) == 5 and res[0] is False and res[1].startswith("yt-dlp exit 3"), res
    assert expect_flag in Path(exe + ".argv").read_text()


def test_f12_gallerydl_fallback_returns_five_values(tmp_path, monkeypatch):
    exe = _failing_exe(tmp_path, "gallery-dl")
    monkeypatch.setattr(
        "shutil.which", lambda name: exe if name == "gallery-dl" else None
    )
    res = Stub(
        {
            "download_dir": str(tmp_path / "dl"),
            "use_gallerydl_fallback": True,
            "min_resolution": "720p",
        }
    )._try_gallerydl_fallback("http://h/g/1")
    assert (
        len(res) == 5 and res[0] is False and res[1].startswith("gallery-dl exit 3")
    ), res


# ── F13: forced update ──────────────────────────────────────────────────────


def _updater(monkeypatch, argv, source, versions):
    launches = []
    monkeypatch.setattr(ytdlp_updater, "_LAST_UPDATE_CHECK", {})
    monkeypatch.setattr(
        ytdlp_updater,
        "_VERSION_CACHE",
        {"ts": 0.0, "key": (source, tuple(argv)), "version": versions[0]},
    )
    monkeypatch.setattr(
        ytdlp_updater, "_resolve_ytdlp_argv", lambda *a, **k: (argv, source, None)
    )
    monkeypatch.setattr(
        ytdlp_updater,
        "_version_probe",
        lambda *a, **k: {"version": versions[0], "source": source, "error": None},
    )
    monkeypatch.setattr(ytdlp_updater, "current_version", lambda *a, **k: versions[1])
    monkeypatch.setattr(ytdlp_updater, "latest_version", lambda **k: versions[1])

    def run(cmd, **k):
        launches.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ytdlp_updater.subprocess, "run", run)
    return launches


def test_f13_force_does_not_skip_the_24h_limit(monkeypatch):
    launches = _updater(
        monkeypatch,
        (sys.executable, "-m", "yt_dlp"),
        "module",
        ("2025.01.01", "2025.12.08"),
    )
    results = [ytdlp_updater.maybe_update(force=True) for _ in range(3)]
    assert len(launches) == 1, launches
    assert results[0] == (
        True,
        "yt-dlp upgraded to 2025.12.08",
    )  # control: the first one runs
    assert results[1] == results[2] == (False, "update check rate-limited (24h)")


def test_f13_a_yt_dlp_outside_the_environment_is_not_pip_updated(monkeypatch):
    launches = _updater(
        monkeypatch, ("/usr/bin/yt-dlp",), "path", ("2025.01.01", "2025.01.01")
    )
    ran, msg = ytdlp_updater.maybe_update(force=True)
    assert (
        ran is False
        and launches == []
        and "/usr/bin/yt-dlp" in msg
        and "outside" in msg
    )


def test_f13_control_own_console_script_is_updated(monkeypatch):
    own = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "yt-dlp")
    launches = _updater(monkeypatch, (own,), "path", ("2025.01.01", "2025.12.08"))
    assert ytdlp_updater.maybe_update(force=True) == (
        True,
        "yt-dlp upgraded to 2025.12.08",
    )
    assert launches == [
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"]
    ]


def test_f13_same_version_is_not_reported_as_an_upgrade(monkeypatch):
    _updater(
        monkeypatch,
        (sys.executable, "-m", "yt_dlp"),
        "module",
        ("2025.12.08", "2025.12.08"),
    )
    assert ytdlp_updater.maybe_update(force=True) == (
        True,
        "yt-dlp already at the newest version (2025.12.08)",
    )


# ── F14: write_direct onto itself ───────────────────────────────────────────


@pytest.mark.parametrize("min_direct", [1 << 40, 0])  # buffered branch, O_DIRECT branch
def test_f14_write_direct_refuses_its_own_source(tmp_path, min_direct):
    p = tmp_path / "f.bin"
    data = os.urandom(8192 + 17)
    p.write_bytes(data)
    with pytest.raises(ValueError, match="same file"):
        direct_writer.write_direct(p, p, min_direct_bytes=min_direct)
    assert p.read_bytes() == data
    link = tmp_path / "link.bin"
    os.link(p, link)
    with pytest.raises(ValueError, match="same file"):
        direct_writer.write_direct(p, link, min_direct_bytes=min_direct)
    assert p.read_bytes() == data


def test_f14_control_copy_to_another_path(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc" * 1000)
    res = direct_writer.write_direct(p, tmp_path / "out" / "g.bin")
    assert (
        tmp_path / "out" / "g.bin"
    ).read_bytes() == b"abc" * 1000 and res.total_bytes == 3000


def test_f9_watch_url_with_leading_params():
    assert yt_dlp_archive.derive_id(
        "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ&t=3"
    ) == ("youtube", "dQw4w9WgXcQ")


# ── r2 (lens R1 + coverage debt) ────────────────────────────────────────────


def _session_server(set_cookie):
    """An API that answers its first request with Set-Cookie + a 302 to the same
    URL and serves the JSON only once the cookie comes back."""
    seen = []
    name, value = set_cookie.split("=")

    class Home(_Quiet):
        def do_GET(self):
            cookie = self.headers.get("Cookie") or ""
            seen.append(cookie)
            if f"{name}={value}" in cookie.split("; "):
                body = b'{"title": "in", "media": []}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            self.send_header("Set-Cookie", f"{set_cookie}; Path=/")
            self.send_header("Location", self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv, base = _serve(Home)
    return seen, base, srv


@pytest.mark.parametrize(
    "site_cookies,set_cookie,second_hop",
    [
        ({"session": "SECRET"}, "sid=OK", "session=SECRET; sid=OK"),
        (
            {"session": "OLD", "x": "1"},
            "session=NEW",
            "session=NEW; x=1",
        ),  # a refreshed session wins
        ({}, "sid=OK", "sid=OK"),  # control: no site cookies, httpx's own jar
    ],
)
def test_r1_a_cookie_the_api_sets_comes_back_to_it(
    site_cookies, set_cookie, second_hop
):
    seen, base, srv = _session_server(set_cookie)
    try:
        data = extractors_jsonapi.fetch_scene(
            base + "/heresphere", "1", cookies=site_cookies
        )
    finally:
        srv.shutdown()
    assert data == {"title": "in", "media": []}, seen
    assert len(seen) == 2 and seen[1] == second_hop, seen


def test_r1_probe_site_gets_through_a_session_redirect():
    seen, base, srv = _session_server("sid=OK")
    try:
        out = extractors_jsonapi.probe_site(base, cookies={"session": "SECRET"})
    finally:
        srv.shutdown()
    assert [status for _url, status in out.probed_urls] == [200, 200], (
        out.probed_urls,
        seen,
    )


def test_r1_a_cookie_set_at_home_does_not_travel_either():
    seen = {"away": []}

    class Away(_Quiet):
        def do_GET(self):
            seen["away"].append(self.headers.get("Cookie"))
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    away_srv, away = _serve(Away)

    class Home(_Quiet):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Set-Cookie", "sid=HOME-ONLY; Path=/")
            self.send_header(
                "Location", away + "/x"
            )  # same host, another PORT: another origin
            self.send_header("Content-Length", "0")
            self.end_headers()

    home_srv, home = _serve(Home)
    try:
        extractors_jsonapi.fetch_scene(
            home + "/heresphere", "1", cookies={"session": "SECRET"}
        )
    finally:
        home_srv.shutdown()
        away_srv.shutdown()
    # http.cookiejar does not scope by port, so the jar's sid may arrive; the site's cookie must not.
    assert len(seen["away"]) == 1 and "SECRET" not in (seen["away"][0] or ""), seen


def test_f2_redirect_loop_is_bounded():
    hits = []

    class Loop(_Quiet):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv, base = _serve(Loop)
    try:
        assert (
            extractors_jsonapi.fetch_scene(
                base + "/heresphere", "1", cookies={"a": "b"}
            )
            is None
        )
    finally:
        srv.shutdown()
    assert len(hits) == extractors_jsonapi._MAX_REDIRECTS + 1 == 6


def test_f5_a_skipped_path_line_is_not_a_written_file(tmp_path, monkeypatch):
    body = _FOREIGN + "print('# ' + os.path.join(dl, 'OTHER-JOB-video.mp4.part'))\n"
    exe = _fake_gallerydl(tmp_path, body)
    monkeypatch.setattr(
        "shutil.which", lambda name: exe if name == "gallery-dl" else None
    )
    res = Stub(
        {"download_dir": str(tmp_path / "dl"), "use_gallerydl_fallback": True}
    )._try_gallerydl_fallback("http://h/g/1")
    assert res == (False, "gallery-dl produced no new file", None, 0, 0)


def test_f10_worst_ignores_unknown_heights_and_keeps_tiebreaks():
    S = extractors_jsonapi.JsonApiSource
    srcs = [
        S(url="https://c/unknown.mp4", height=0),
        S(url="https://c/1080.mp4", height=1080),
        S(url="https://c/360/index.m3u8", height=360, is_hls=True),
        S(url="https://c/360.mp4", height=360),
    ]
    assert extractors_jsonapi.pick_source(srcs, ["worst"]).url == "https://c/360.mp4"
    only_unknown = [S(url="https://c/a.mp4", height=0)]
    assert (
        extractors_jsonapi.pick_source(only_unknown, ["worst"]).url == "https://c/a.mp4"
    )  # control


@pytest.mark.parametrize(
    "configured,passed",
    [
        ("worst", ["worst"]),
        ("lowest,best", ["worst", "best"]),
        ("720p, best", [720, "best"]),
    ],
)
def test_f10_runner_hands_worst_to_the_jsonapi_picker(
    tmp_path, monkeypatch, configured, passed
):
    got = {}

    def fetch_and_extract(api_base, url, **kw):
        got.update(kw)
        return extractors_jsonapi.JsonApiResult(ok=False, error="stop-here")

    monkeypatch.setattr(RE, "_JSONAPI_AVAILABLE", True)
    monkeypatch.setattr(RE._jsonapi, "fetch_and_extract", fetch_and_extract)
    s = Stub(
        {
            "download_dir": str(tmp_path),
            "jsonapi_url": "http://127.0.0.1:9/heresphere",
            "quality_preference": configured,
        }
    )
    assert s._try_jsonapi_extractor("http://127.0.0.1:9/scene/1") is False
    assert got["quality_pref"] == passed


def _failing_pip(monkeypatch):
    launches = _updater(
        monkeypatch,
        (sys.executable, "-m", "yt_dlp"),
        "module",
        ("2025.01.01", "2025.01.01"),
    )

    def run(cmd, **k):
        launches.append(cmd)
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no network")

    monkeypatch.setattr(ytdlp_updater.subprocess, "run", run)
    return launches


def test_f13_a_failed_forced_update_can_be_retried(monkeypatch):
    launches = _failing_pip(monkeypatch)
    for _ in range(2):
        ran, msg = ytdlp_updater.maybe_update(force=True)
        assert ran is False and "failed: no network" in msg
    assert len(launches) == 2


def test_f13_a_failed_automatic_update_keeps_the_limit(monkeypatch):
    launches = _failing_pip(monkeypatch)
    monkeypatch.setattr(ytdlp_updater, "version_age_days", lambda _v=None: 400)
    assert "failed: no network" in ytdlp_updater.maybe_update()[1]
    assert ytdlp_updater.maybe_update() == (False, "update check rate-limited (24h)")
    assert ytdlp_updater.maybe_update(force=True) == (
        False,
        "update check rate-limited (24h)",
    )
    assert len(launches) == 1


def test_r1_another_origins_set_cookie_is_not_brought_home():
    seen = []

    class Home(_Quiet):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Cookie")))
            if self.path.endswith("/back"):
                body = b'{"title": "back", "media": []}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(302)
            # another HOST ("localhost"): cookies are not port-scoped, so only
            # a different host name is another cookie owner for the jar
            self.send_header("Location", away.replace("127.0.0.1", "localhost") + "/x")
            self.send_header("Content-Length", "0")
            self.end_headers()

    home_srv, home = _serve(Home)

    class Away(_Quiet):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Set-Cookie", "session=INJECTED; Path=/")
            self.send_header("Location", home + "/heresphere/back")
            self.send_header("Content-Length", "0")
            self.end_headers()

    away_srv, away = _serve(Away)
    try:
        data = extractors_jsonapi.fetch_scene(
            home + "/heresphere", "1", cookies={"session": "SECRET"}
        )
    finally:
        home_srv.shutdown()
        away_srv.shutdown()
    assert data == {"title": "back", "media": []}
    assert seen == [
        ("/heresphere/1", "session=SECRET"),
        ("/heresphere/back", "session=SECRET"),
    ]


# ── r3 (lens D176 R2-1): the jar decides, across requests and domains ───────


@pytest.mark.parametrize(
    "site_cookies,deovr_cookie",
    [
        ({"session": "SECRET"}, "session=SECRET; sid=OK"),
        ({}, "sid=OK"),  # neg-control: no site cookies, httpx's own jar
    ],
)
def test_r3_probe_site_carries_a_cookie_from_one_probe_request_to_the_next(
    site_cookies, deovr_cookie
):
    """D176 p_r2c.py: /heresphere answers 404 + Set-Cookie sid=OK; /deovr serves the
    JSON only with sid. Real loopback server, real guarded_transport."""
    seen = []

    class H(_Quiet):
        def do_GET(self):
            cookie = self.headers.get("Cookie") or ""
            seen.append((self.path, cookie))
            if self.path == "/heresphere":
                self.send_response(404)
                self.send_header("Set-Cookie", "sid=OK; Path=/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if "sid=OK" not in cookie.split("; "):
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'{"title": "ok", "media": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv, base = _serve(H)
    try:
        out = extractors_jsonapi.probe_site(base, cookies=site_cookies)
    finally:
        srv.shutdown()
    assert out.ok is True and out.deovr_url == base + "/deovr", (out.probed_urls, seen)
    assert seen == [
        ("/heresphere", "; ".join(f"{k}={v}" for k, v in site_cookies.items())),
        ("/deovr", deovr_cookie),
    ]


def _named_hosts(monkeypatch, handler):
    """Hosts by NAME (cookie Domain scoping needs names): an httpx.MockTransport
    stands in for guarded_transport; every request is recorded."""
    import httpx

    from bulk_downloader import ssrf_transport

    seen = []

    def handle(request):
        seen.append((request.url.host, request.url.path, request.headers.get("cookie")))
        return handler(request)

    monkeypatch.setattr(
        ssrf_transport, "guarded_transport", lambda *a, **k: httpx.MockTransport(handle)
    )
    return seen, httpx


_SCENE = {"title": "in", "media": []}


@pytest.mark.parametrize(
    "site_cookies", [{"session": "SECRET"}, {}]
)  # {} = neg-control
def test_r3_a_parent_domain_cookie_from_a_sibling_host_reaches_home(
    monkeypatch, site_cookies
):
    """D176 p_r2.py s3: sso.site.test sets auth=1; Domain=site.test and bounces home."""

    def handler(request):
        if request.url.host == "sso.site.test":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://api.site.test/heresphere/7",
                    "Set-Cookie": "auth=1; Domain=site.test; Path=/",
                },
            )
        if "auth=1" in (request.headers.get("cookie") or "").split("; "):
            return httpx.Response(200, json=_SCENE)
        return httpx.Response(302, headers={"Location": "https://sso.site.test/login"})

    seen, httpx = _named_hosts(monkeypatch, handler)
    assert (
        extractors_jsonapi.fetch_scene(
            "https://api.site.test/heresphere", "7", cookies=site_cookies
        )
        == _SCENE
    )
    site = "; ".join(f"{k}={v}" for k, v in site_cookies.items())
    assert seen == [
        ("api.site.test", "/heresphere/7", site or None),
        (
            "sso.site.test",
            "/login",
            None,
        ),  # the site's cookies never leave the configured origin
        ("api.site.test", "/heresphere/7", (site + "; " if site else "") + "auth=1"),
    ]


def test_r3_another_hosts_cookie_of_the_same_name_does_not_replace_the_sites(
    monkeypatch,
):
    """D176 s7: an away host sets session=EVIL (host-only) and bounces back."""

    def handler(request):
        if request.url.host == "cdn.other.test":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://api.site.test/back",
                    "Set-Cookie": "session=EVIL; Path=/",
                },
            )
        if request.url.path == "/back":
            return httpx.Response(200, json=_SCENE)
        return httpx.Response(302, headers={"Location": "https://cdn.other.test/x"})

    seen, httpx = _named_hosts(monkeypatch, handler)
    assert (
        extractors_jsonapi.fetch_scene(
            "https://api.site.test/heresphere", "7", cookies={"session": "SECRET"}
        )
        == _SCENE
    )
    assert seen == [
        ("api.site.test", "/heresphere/7", "session=SECRET"),
        ("cdn.other.test", "/x", None),
        ("api.site.test", "/back", "session=SECRET"),
    ]


# ── r4 (lens D188 R3-1): the jar's header is passed through, never folded ──


@pytest.mark.parametrize(
    "site_cookies,deovr_cookie",
    [
        ({"session": "SECRET"}, "session=SECRET; sid=DEEP; sid=ROOT"),
        ({}, "sid=DEEP; sid=ROOT"),  # neg-control: no site cookies, httpx's own jar
    ],
)
def test_r4_one_name_at_two_paths_reaches_home_most_specific_first(
    site_cookies, deovr_cookie
):
    """D188 p_r3c.py: /heresphere answers 404 + sid=ROOT (Path=/) + sid=DEEP
    (Path=/deovr); /deovr serves only when the first sid it gets is DEEP (RFC 6265
    5.4: longer path first). A dict fold kept the last pair, sid=ROOT."""
    seen = []

    class H(_Quiet):
        def do_GET(self):
            cookie = self.headers.get("Cookie") or ""
            seen.append((self.path, cookie))
            if self.path == "/heresphere":
                self.send_response(404)
                self.send_header("Set-Cookie", "sid=ROOT; Path=/")
                self.send_header("Set-Cookie", "sid=DEEP; Path=/deovr")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            sids = [c for c in cookie.split("; ") if c.startswith("sid=")]
            if not sids or sids[0] != "sid=DEEP":
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b'{"title": "deep", "media": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv, base = _serve(H)
    try:
        out = extractors_jsonapi.probe_site(base, cookies=site_cookies)
    finally:
        srv.shutdown()
    assert out.ok is True and out.deovr_url == base + "/deovr", (out.probed_urls, seen)
    assert seen[-1] == ("/deovr", deovr_cookie)


def _path_dup(request):
    """D188 p_r3.py t4 by host name: the scene sets one name at two paths and
    bounces to the deeper one, which needs the deeper value first."""
    import httpx

    if request.url.path == "/heresphere/7":
        return httpx.Response(
            302,
            headers=[
                ("Location", "https://api.site.test/heresphere/7/x"),
                ("Set-Cookie", "sid=ROOT; Path=/"),
                ("Set-Cookie", "sid=DEEP; Path=/heresphere/7"),
            ],
        )
    sids = [
        c
        for c in (request.headers.get("cookie") or "").split("; ")
        if c.startswith("sid=")
    ]
    if sids and sids[0] == "sid=DEEP":
        return httpx.Response(200, json=_SCENE)
    return httpx.Response(403)


@pytest.mark.parametrize(
    "site_cookies,hop_cookie",
    [
        ({"session": "SECRET"}, "session=SECRET; sid=DEEP; sid=ROOT"),
        ({}, "sid=DEEP; sid=ROOT"),  # neg-control
        # a site cookie of the same name yields to every jar value, in jar order
        ({"sid": "SITE"}, "sid=DEEP; sid=ROOT"),
    ],
)
def test_r4_fetch_scene_keeps_both_same_name_cookies_in_jar_order(
    monkeypatch, site_cookies, hop_cookie
):
    seen, httpx = _named_hosts(monkeypatch, _path_dup)
    assert (
        extractors_jsonapi.fetch_scene(
            "https://api.site.test/heresphere", "7", cookies=site_cookies
        )
        == _SCENE
    )
    assert seen[-1] == ("api.site.test", "/heresphere/7/x", hop_cookie)


@pytest.mark.parametrize(
    "site_cookies", [{"session": "SECRET"}, {}]
)  # {} = neg-control
def test_r4_a_valueless_cookie_the_origin_set_still_reaches_it(
    monkeypatch, site_cookies
):
    """D188 AD3 (p_r3.py t5): Set-Cookie "flag" (no '='); BASE and the client jar
    send it back, the r3 fold dropped it when site cookies were configured."""

    def handler(request):
        parts = [p.strip() for p in (request.headers.get("cookie") or "").split(";")]
        if "flag" in parts:
            return httpx.Response(200, json=_SCENE)
        if request.url.path == "/heresphere/7":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://api.site.test/heresphere/7/y",
                    "Set-Cookie": "flag; Path=/",
                },
            )
        return httpx.Response(403)

    seen, httpx = _named_hosts(monkeypatch, handler)
    assert (
        extractors_jsonapi.fetch_scene(
            "https://api.site.test/heresphere", "7", cookies=site_cookies
        )
        == _SCENE
    ), seen


@pytest.mark.parametrize(
    "boom", [ytdlp_updater.subprocess.TimeoutExpired("pip", 120), OSError("no pip")]
)
def test_f13_a_forced_update_that_timed_out_or_raised_can_be_retried(monkeypatch, boom):
    launches = _updater(
        monkeypatch,
        (sys.executable, "-m", "yt_dlp"),
        "module",
        ("2025.01.01", "2025.01.01"),
    )

    def run(cmd, **k):
        launches.append(cmd)
        raise boom

    monkeypatch.setattr(ytdlp_updater.subprocess, "run", run)
    for _ in range(2):
        ran, msg = ytdlp_updater.maybe_update(force=True)
        assert ran is False and ("timed out" in msg or "raised" in msg)
    assert len(launches) == 2
