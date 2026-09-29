"""dl95-dailymotion-1 (O1513 on test2, RESULT-dailymotion.md D1): dailymotion is a public
yt-dlp host. On the teach backend with no reviewed template, both enqueued videos went
through the DOM scrape and failed "[page_shape] No download button found"
(dailymotion/dm10/DM__activity-failed.png). The site was never taught, so the scrape had
nothing to look for, and yt-dlp was never asked.

The fix, measured link by link:
1. the no-button branch runs the Phase 61 yt-dlp fallback for an UNTAUGHT site (no applied
   template, no learned download selectors) without its per-site opt-in;
2. the fallback's ``-f best[height>=N]/best`` asks for a single MUXED format, and dailymotion
   has none (video-only + audio-only HLS): "Requested format is not available". The selector
   now keeps the height preference and falls back to yt-dlp's default (``bv*+ba/b``);
3. when yt-dlp cannot fetch it either, the failure says the site needs onboarding (page_shape).

Hermetic: yt-dlp is never executed as a subprocess. ``subprocess.run`` replays REAL captures of
the fallback's own argv on test2 (tests/fixtures/ytdlp_dailymotion_xbe8y8e_download_20260928.json:
yt-dlp 2026.09.27 lands 720p+audio; the installed 2025.12.08 fails). The selector test runs
yt-dlp's own format engine offline over a REAL ``-j`` capture of the same video.
"""

from __future__ import annotations

import inspect
import json
import subprocess
import threading
import types
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

FIXTURES = Path(__file__).parent / "fixtures"
DOWNLOAD = json.loads(
    (FIXTURES / "ytdlp_dailymotion_xbe8y8e_download_20260928.json").read_text()
)
INFO = FIXTURES / "ytdlp_dailymotion_xbe8y8e_info_20260928.json"
URL = "https://www.dailymotion.com/video/xbe8y8e"
MERGED = "Massive_Landslide_Tears_Through_Chame_Nepal-xbe8y8e.mp4"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from bulk_downloader import runner_extractors as rx
    from bulk_downloader import ytdlp_updater
    from bulk_downloader.runner_transport import TransportMixin

    assert (
        DOWNLOAD["latest"]["rc"] == 0
        and f'Merging formats into "{{DL}}/{MERGED}"' in DOWNLOAD["latest"]["stdout"]
    )
    assert (
        DOWNLOAD["pinned"]["rc"] == 1
        and "No video formats found!" in DOWNLOAD["pinned"]["stderr"]
    )
    dl = tmp_path / "dl"
    w = types.SimpleNamespace(
        cmds=[], capture="latest", jobs=[], events=[], history=[], dl=dl
    )

    def run(cmd, **kw):
        w.cmds.append(cmd)
        cap = DOWNLOAD[w.capture]
        if cap["rc"] == 0:
            (dl / MERGED).write_bytes(b"\x00" * 64)
        sub = lambda s: s.replace("{DL}", str(dl))
        return subprocess.CompletedProcess(
            cmd, cap["rc"], sub(cap["stdout"]), sub(cap["stderr"])
        )

    monkeypatch.setattr(rx.subprocess, "run", run)
    monkeypatch.setattr(ytdlp_updater, "resolve_ytdlp_argv", lambda *a: ("yt-dlp",))
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: w.history.append(a))
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})

    class Runner(rx.ExtractorsMixin):
        site_id = "dailymotion"
        _download_proxy_url = TransportMixin._download_proxy_url

        def __init__(self, config):
            self.config = {"name": "dailymotion", "download_dir": str(dl), **config}
            self._stop = threading.Event()

        def _update_job(self, url, status, message, **extra):
            w.jobs.append((status, message, extra))

        def log_event(self, kind, message, url=None, extra=None):
            w.events.append((kind, message))

    w.Runner = Runner
    return w


def test_untaught_dailymotion_downloads_through_the_ytdlp_fallback(world):
    ok = world.Runner({})._try_ytdlp_untaught(URL)
    assert ok is True, f"jobs={world.jobs} events={world.events}"
    assert len(world.cmds) == 1 and world.cmds[0][-1] == URL
    assert (
        world.cmds[0][world.cmds[0].index("-f") + 1]
        == "bv*[height>=1080]+ba/b[height>=1080]/bv*+ba/b"
    )
    status, message, extra = world.jobs[-1]
    assert status == "done" and message == "Downloaded via yt-dlp fallback", world.jobs
    assert extra == {"filename": str(world.dl / MERGED), "file_size": 64}
    assert [h[3] for h in world.history] == ["done"]


@pytest.mark.parametrize(
    "config",
    [
        {"applied_template": "dailymotion-reviewed"},
        {"learned": {"download": {"trigger_selectors": ["button.dl"]}}},
        {"learned": {"download": {"row_selectors": ["tr.file"]}}},
    ],
    ids=["template", "trigger-selectors", "row-selectors"],
)
def test_taught_site_keeps_the_page_shape_failure_and_never_runs_ytdlp(world, config):
    # A taught site that finds no button is drift: the scrape had something to look for.
    from bulk_downloader.runner_extractors import site_untaught

    assert site_untaught(config) is False
    assert world.Runner(config)._try_ytdlp_untaught(URL) is False
    assert world.cmds == [] and world.jobs == [] and world.history == []


def test_ytdlp_that_cannot_fetch_falls_through_with_its_reason(world):
    # test2's installed yt-dlp 2025.12.08 on this very video (measured 2026-09-28).
    world.capture = "pinned"
    assert world.Runner({})._try_ytdlp_untaught(URL) is False
    assert len(world.cmds) == 1 and world.jobs == [] and world.history == []
    assert any(
        k == "ytdlp" and "No video formats found!" in m for k, m in world.events
    ), world.events


def test_the_per_site_opt_in_still_gates_every_other_caller(world):
    # The captcha path calls _try_ytdlp_fallback without force: unchanged.
    assert world.Runner({})._try_ytdlp_fallback(URL, "captcha challenge") == (
        False,
        "ytdlp_fallback disabled",
        None,
        0,
        0,
    )
    assert world.cmds == []


def test_the_selector_admits_a_split_stream_host():
    yt_dlp = pytest.importorskip("yt_dlp")
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd

    info = json.loads(INFO.read_text())
    assert info["extractor"] == "dailymotion" and len(info["formats"]) == 5, (
        "capture is empty"
    )
    cmd = _build_ytdlp_cmd(ytdlp=["yt-dlp"], dl_dir="/d", url=URL, min_res=1080)
    spec = cmd[cmd.index("-f") + 1]
    opts = {"quiet": True, "simulate": True, "skip_download": True}
    picked = yt_dlp.YoutubeDL({**opts, "format": spec}).process_ie_result(
        dict(info), download=False
    )
    assert picked["format_id"] == "hls-720+hls-0_aac_q2-_original_"
    # Control: the old single-muxed-format selector finds nothing on this capture.
    with pytest.raises(Exception, match="Requested format is not available"):
        yt_dlp.YoutubeDL(
            {**opts, "format": "best[height>=1080]/best"}
        ).process_ie_result(dict(info), download=False)
    assert "-f" not in _build_ytdlp_cmd(
        ytdlp=["yt-dlp"], dl_dir="/d", url=URL, min_res=0
    )


@pytest.fixture
def teaser_site(tmp_path, monkeypatch):
    """A loopback 'members' page whose only media is an embedded teaser: yt-dlp's GENERIC
    extractor claims it (review R1, bd-review-shape-A1-A). Real yt-dlp, real subprocess."""
    import http.server
    import socketserver
    import sys

    pytest.importorskip("yt_dlp")
    from bulk_downloader import runner_extractors as rx
    from bulk_downloader import ytdlp_updater
    from bulk_downloader.runner_transport import TransportMixin

    site = tmp_path / "site"
    site.mkdir()
    (site / "teaser.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 30000)
    (site / "scene-123.html").write_text(
        "<html><head><title>Members only: Full Scene 123</title></head><body>"
        '<video controls src="teaser.mp4"></video></body></html>'
    )
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(
        *a, directory=str(site), **k
    )
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    for var in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        ytdlp_updater, "resolve_ytdlp_argv", lambda *a: (sys.executable, "-m", "yt_dlp")
    )
    monkeypatch.setattr(rx, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(rx, "history_title_kwargs", lambda *a, **k: {})
    dl = tmp_path / "dl"
    jobs = []

    class Runner(rx.ExtractorsMixin):
        site_id = "members-fixture"
        _download_proxy_url = TransportMixin._download_proxy_url

        def __init__(self, config):
            self.config = {
                "name": "members",
                "download_dir": str(dl),
                "min_resolution": 0,
                **config,
            }

        def _update_job(self, url, status, message, **extra):
            jobs.append(status)

        def log_event(self, kind, message, url=None, extra=None):
            pass

    yield types.SimpleNamespace(
        url=f"http://127.0.0.1:{httpd.server_address[1]}/scene-123.html",
        dl=dl,
        jobs=jobs,
        Runner=Runner,
    )
    httpd.shutdown()
    httpd.server_close()


def test_untaught_page_only_the_generic_extractor_claims_is_not_marked_done(
    teaser_site,
):
    # Positive control: an opted-in site's fallback (generic allowed) DOES take the teaser.
    ok, msg, fn, _sz, _f = teaser_site.Runner(
        {"use_ytdlp_fallback": True}
    )._try_ytdlp_fallback(teaser_site.url)
    assert ok is True and fn and Path(fn).name.startswith("Members_only"), msg
    Path(fn).unlink()
    # The forced untaught route must not: a teaser is not the scene.
    assert teaser_site.Runner({})._try_ytdlp_untaught(teaser_site.url) is False
    assert not any(teaser_site.dl.iterdir()) and "done" not in teaser_site.jobs


def test_the_forced_argv_still_reaches_the_dedicated_dailymotion_extractor():
    yt_dlp = pytest.importorskip("yt_dlp")
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd

    cmd = _build_ytdlp_cmd(ytdlp=["yt-dlp"], dl_dir="/d", url=URL, dedicated_only=True)
    assert cmd[cmd.index("--ies") + 1] == "default,-generic"
    assert "--ies" not in _build_ytdlp_cmd(ytdlp=["yt-dlp"], dl_dir="/d", url=URL)
    suitable = [
        ie.IE_NAME
        for ie in yt_dlp.extractor.gen_extractor_classes()
        if ie.suitable(URL)
    ]
    assert "dailymotion" in suitable, suitable


def _no_button_failures():
    import ast

    from bulk_downloader import runner

    tree = ast.parse(inspect.getsource(runner))
    return [
        n.args[1].value
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "attr", "") == "_handle_failure"
        and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant)
        and str(n.args[1].value).startswith("No download button found")
    ]


def test_onboarding_message_keeps_the_page_shape_classification():
    from bulk_downloader.runner_telemetry import TelemetryMixin

    msgs = _no_button_failures()
    onboarding = [m for m in msgs if "needs onboarding" in m]
    assert "No download button found" in msgs and len(onboarding) == 1, msgs
    assert TelemetryMixin._classify_error(None, onboarding[0]) == "page_shape"


def test_no_button_branch_tries_ytdlp_then_names_onboarding():
    # Wiring in runner._process_one: SPA fallback -> yt-dlp -> failure; onboarding only when untaught.
    from bulk_downloader import runner

    src = inspect.getsource(runner)
    spa = src.index("if self._try_spa_api_media_extractor(url, page):")
    ytd = src.index("if self._try_ytdlp_untaught(url):", spa)
    gate = src.index("if site_untaught(self.config):", ytd)
    onboard = src.index('"No download button found -- site needs onboarding:', gate)
    plain = src.index(
        'self._handle_failure(url,"No download button found",screenshot=ss)', onboard
    )
    assert spa < ytd < gate < onboard < plain
    assert src[ytd:].split("\n", 2)[1].strip() == "return"
    assert src[gate:plain].count("return") == 1
