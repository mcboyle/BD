"""GH-2a (v3.66.693) -- the yt-dlp -> plugin ``@extractor`` adapter.

The remaining half of Batch B's "do both": now that 691 wired the plugin
``@extractor`` dispatch (``_try_plugin_extractor`` consults
``plugins.get_extractor(site_id)`` during capture), GH-2a supplies a yt-dlp
*shim* that plugs straight into it. A site that opts in (undeclared site-cfg key
``ytdlp_extractor`` truthy) registers a shim which runs ``yt-dlp -j
--skip-download`` for the URL, maps the info JSON to the extractor contract
(``{"video_url", "title", "ext", "is_hls"}``), and lets the 691 dispatch
download the progressive result via ``_do_direct_http_download``.

RED-first. On pristine v3.66.692 ``bulk_downloader.ytdlp_extractor`` does not
exist, so every import below raises ModuleNotFoundError -> all tests RED.

Scope (matches the handoff):
  * ``build_ytdlp_info_cmd`` -- pure, smuggle-safe builder for the info CLI.
  * ``info_to_extractor_result`` -- pure mapper (progressive http -> video_url;
    HLS/DASH-only -> is_hls=True so the 691 dispatch defers; nothing usable
    -> {}).
  * ``make_ytdlp_extractor`` -- the ``fn(url, ctx)`` shim (binary missing /
    nonzero exit / bad JSON all degrade to {} -> fall through).
  * ``register_ytdlp_extractor`` / ``maybe_register_from_config`` -- opt-in.
  * end-to-end through the real 691 ``_try_plugin_extractor`` dispatch.

Runtime gate: a live yt-dlp binary (live exercise deferred). Every test here
injects the subprocess runner + binary resolver, so no yt-dlp is needed.
"""
import types


# ── build_ytdlp_info_cmd (pure) ───────────────────────────────────────
def test_info_cmd_is_info_only_and_terminated():
    from bulk_downloader.ytdlp_extractor import build_ytdlp_info_cmd
    cmd = build_ytdlp_info_cmd(ytdlp="/usr/bin/yt-dlp", url="https://ex/v")
    assert cmd[0] == "/usr/bin/yt-dlp"
    assert "-j" in cmd and "--skip-download" in cmd
    assert "--no-playlist" in cmd          # single-video info, never a playlist
    # F-RUN01-02: options terminated with a bare '--', url is the LAST token
    assert cmd[-2] == "--" and cmd[-1] == "https://ex/v"


def test_info_cmd_url_with_leading_dash_is_positional():
    from bulk_downloader.ytdlp_extractor import build_ytdlp_info_cmd
    cmd = build_ytdlp_info_cmd(ytdlp="yt-dlp", url="-oops")
    assert cmd[-2] == "--" and cmd[-1] == "-oops"   # never smuggled as a flag


def test_info_cmd_proxy_forces_remote_dns():
    from bulk_downloader.ytdlp_extractor import build_ytdlp_info_cmd
    cmd = build_ytdlp_info_cmd(ytdlp="yt-dlp", url="https://ex/v",
                               proxy_url="socks5://127.0.0.1:9050")
    assert "--proxy" in cmd
    assert "socks5h://127.0.0.1:9050" in cmd   # socks5 -> socks5h (no DNS leak)


def test_info_cmd_no_proxy_no_cookies_by_default():
    from bulk_downloader.ytdlp_extractor import build_ytdlp_info_cmd
    cmd = build_ytdlp_info_cmd(ytdlp="yt-dlp", url="https://ex/v")
    assert "--proxy" not in cmd and "--cookies" not in cmd


def test_info_cmd_cookies_only_when_maintained_txt(tmp_path):
    from bulk_downloader.ytdlp_extractor import build_ytdlp_info_cmd
    ck = tmp_path / "c.txt"
    ck.write_text("# Netscape\n")
    cmd = build_ytdlp_info_cmd(ytdlp="yt-dlp", url="https://ex/v",
                               cookie_file=str(ck))
    assert "--cookies" in cmd and str(ck) in cmd
    # a non-existent / non-.txt cookie file is ignored (no --cookies)
    cmd2 = build_ytdlp_info_cmd(ytdlp="yt-dlp", url="https://ex/v",
                                cookie_file="/nope/c.sqlite")
    assert "--cookies" not in cmd2


# ── info_to_extractor_result (pure mapper) ────────────────────────────
def test_mapper_progressive_http_returns_video_url():
    from bulk_downloader.ytdlp_extractor import info_to_extractor_result
    info = {
        "title": "Clip One", "ext": "mp4",
        "formats": [
            {"url": "https://cdn/lo.mp4", "protocol": "https",
             "vcodec": "avc1", "acodec": "mp4a", "height": 480, "ext": "mp4"},
            {"url": "https://cdn/hi.mp4", "protocol": "https",
             "vcodec": "avc1", "acodec": "mp4a", "height": 1080, "ext": "mp4"},
        ],
    }
    r = info_to_extractor_result(info)
    assert r["video_url"] == "https://cdn/hi.mp4"   # highest resolution wins
    assert r["is_hls"] is False
    assert r["title"] == "Clip One"
    assert r["ext"] == "mp4"


def test_mapper_skips_video_only_and_audio_only():
    from bulk_downloader.ytdlp_extractor import info_to_extractor_result
    # only split streams (no single muxed file) -> not directly downloadable
    info = {"title": "t", "formats": [
        {"url": "https://cdn/v.mp4", "protocol": "https",
         "vcodec": "avc1", "acodec": "none", "height": 1080},
        {"url": "https://cdn/a.m4a", "protocol": "https",
         "vcodec": "none", "acodec": "mp4a"},
    ]}
    assert info_to_extractor_result(info) == {}


def test_mapper_hls_only_flags_is_hls():
    from bulk_downloader.ytdlp_extractor import info_to_extractor_result
    info = {"title": "live", "formats": [
        {"url": "https://cdn/master.m3u8", "protocol": "m3u8_native",
         "vcodec": "avc1", "acodec": "mp4a"},
    ]}
    r = info_to_extractor_result(info)
    assert r.get("is_hls") is True
    assert r.get("video_url") == "https://cdn/master.m3u8"


def test_mapper_prefers_progressive_over_hls_when_both_present():
    from bulk_downloader.ytdlp_extractor import info_to_extractor_result
    info = {"title": "t", "formats": [
        {"url": "https://cdn/master.m3u8", "protocol": "m3u8_native",
         "vcodec": "avc1", "acodec": "mp4a"},
        {"url": "https://cdn/prog.mp4", "protocol": "https",
         "vcodec": "avc1", "acodec": "mp4a", "height": 720, "ext": "mp4"},
    ]}
    r = info_to_extractor_result(info)
    assert r["video_url"] == "https://cdn/prog.mp4" and r["is_hls"] is False


def test_mapper_empty_or_junk_returns_empty():
    from bulk_downloader.ytdlp_extractor import info_to_extractor_result
    assert info_to_extractor_result({}) == {}
    assert info_to_extractor_result({"formats": []}) == {}
    assert info_to_extractor_result(None) == {}
    assert info_to_extractor_result("not a dict") == {}


def test_mapper_toplevel_url_fallback_when_no_formats():
    from bulk_downloader.ytdlp_extractor import info_to_extractor_result
    info = {"title": "t", "url": "https://cdn/direct.mp4",
            "protocol": "https", "ext": "mp4"}
    r = info_to_extractor_result(info)
    assert r["video_url"] == "https://cdn/direct.mp4" and r["is_hls"] is False


# ── make_ytdlp_extractor (the shim) ───────────────────────────────────
def _run_ok(payload_json):
    def _run(cmd):
        return (0, payload_json, "")
    return _run


def test_shim_returns_mapped_result(tmp_path):
    from bulk_downloader.ytdlp_extractor import make_ytdlp_extractor
    payload = ('{"title":"Vid","ext":"mp4","formats":['
               '{"url":"https://cdn/x.mp4","protocol":"https",'
               '"vcodec":"avc1","acodec":"mp4a","height":720,"ext":"mp4"}]}')
    fn = make_ytdlp_extractor({}, run=_run_ok(payload),
                              which=lambda name: "/usr/bin/yt-dlp")
    r = fn("https://ex/v", {"site_id": "s", "config": {}})
    assert r["video_url"] == "https://cdn/x.mp4" and r["title"] == "Vid"


def test_shim_binary_missing_degrades_to_empty():
    from bulk_downloader.ytdlp_extractor import make_ytdlp_extractor
    fn = make_ytdlp_extractor({}, run=_run_ok("{}"),
                              which=lambda name: None)   # no yt-dlp on PATH
    assert fn("https://ex/v", {}) == {}


def test_shim_nonzero_exit_degrades_to_empty():
    from bulk_downloader.ytdlp_extractor import make_ytdlp_extractor
    fn = make_ytdlp_extractor({}, run=lambda cmd: (1, "", "ERROR: unsupported URL"),
                              which=lambda name: "/usr/bin/yt-dlp")
    assert fn("https://ex/v", {}) == {}


def test_shim_bad_json_degrades_to_empty():
    from bulk_downloader.ytdlp_extractor import make_ytdlp_extractor
    fn = make_ytdlp_extractor({}, run=_run_ok("not json at all"),
                              which=lambda name: "/usr/bin/yt-dlp")
    assert fn("https://ex/v", {}) == {}


def test_shim_first_json_line_used():
    from bulk_downloader.ytdlp_extractor import make_ytdlp_extractor
    # --no-playlist should yield one object, but be robust to a trailing line
    payload = ('{"title":"A","url":"https://cdn/a.mp4","protocol":"https","ext":"mp4"}\n'
               '{"title":"B","url":"https://cdn/b.mp4","protocol":"https","ext":"mp4"}')
    fn = make_ytdlp_extractor({}, run=_run_ok(payload),
                              which=lambda name: "/usr/bin/yt-dlp")
    r = fn("https://ex/v", {})
    assert r["video_url"] == "https://cdn/a.mp4"


# ── register / opt-in ─────────────────────────────────────────────────
def test_register_ytdlp_extractor_is_gettable():
    from bulk_downloader import plugins as P
    from bulk_downloader.ytdlp_extractor import register_ytdlp_extractor
    P.unregister_extractor("regsite")
    try:
        register_ytdlp_extractor("regsite", {}, run=_run_ok("{}"),
                                 which=lambda n: "/usr/bin/yt-dlp")
        assert callable(P.get_extractor("regsite"))
    finally:
        P.unregister_extractor("regsite")


def test_opt_in_only_registers_when_flag_truthy():
    from bulk_downloader import plugins as P
    from bulk_downloader.ytdlp_extractor import maybe_register_from_config
    P.unregister_extractor("optA"); P.unregister_extractor("optB")
    try:
        assert maybe_register_from_config("optA", {"ytdlp_extractor": True}) is True
        assert P.get_extractor("optA") is not None
        assert maybe_register_from_config("optB", {"ytdlp_extractor": False}) is False
        assert P.get_extractor("optB") is None
        assert maybe_register_from_config("optC", {}) is False   # absent key
    finally:
        P.unregister_extractor("optA"); P.unregister_extractor("optB")


# ── end-to-end through the real 691 dispatch ──────────────────────────
def _fake_runner(site_id, config, dl_calls):
    def _ddhd(page_url, file_url, output_path, referer=""):
        dl_calls.append(file_url)
        try:
            open(output_path, "w").close()
        except Exception:
            pass
        return True
    return types.SimpleNamespace(
        site_id=site_id, config=config,
        _do_direct_http_download=_ddhd,
        _update_job=lambda *a, **k: None,
        log_event=lambda *a, **k: None,
    )


def test_end_to_end_progressive_downloads_via_dispatch(tmp_path):
    from bulk_downloader import plugins as P
    from bulk_downloader.runner_extractors import ExtractorsMixin
    from bulk_downloader.ytdlp_extractor import register_ytdlp_extractor
    payload = ('{"title":"clip","ext":"mp4","formats":['
               '{"url":"https://cdn/hi.mp4","protocol":"https",'
               '"vcodec":"avc1","acodec":"mp4a","height":1080,"ext":"mp4"}]}')
    P.unregister_extractor("e2e")
    register_ytdlp_extractor("e2e", {}, run=_run_ok(payload),
                             which=lambda n: "/usr/bin/yt-dlp")
    try:
        calls = []
        fs = _fake_runner("e2e", {"download_dir": str(tmp_path)}, calls)
        ok = ExtractorsMixin._try_plugin_extractor(fs, "https://ex/v")
        assert ok is True
        assert calls == ["https://cdn/hi.mp4"], calls
    finally:
        P.unregister_extractor("e2e")


def test_end_to_end_hls_only_falls_through_dispatch(tmp_path):
    from bulk_downloader import plugins as P
    from bulk_downloader.runner_extractors import ExtractorsMixin
    from bulk_downloader.ytdlp_extractor import register_ytdlp_extractor
    payload = ('{"title":"live","formats":['
               '{"url":"https://cdn/master.m3u8","protocol":"m3u8_native",'
               '"vcodec":"avc1","acodec":"mp4a"}]}')
    P.unregister_extractor("e2ehls")
    register_ytdlp_extractor("e2ehls", {}, run=_run_ok(payload),
                             which=lambda n: "/usr/bin/yt-dlp")
    try:
        calls = []
        fs = _fake_runner("e2ehls", {"download_dir": str(tmp_path)}, calls)
        # is_hls result -> 691 dispatch defers -> False, no download
        assert ExtractorsMixin._try_plugin_extractor(fs, "https://ex/v") is False
        assert calls == []
    finally:
        P.unregister_extractor("e2ehls")
