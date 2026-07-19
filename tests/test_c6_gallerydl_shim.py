"""C6 (8.4) -- managed gallery-dl fallback extractor, mirroring the yt-dlp shim.

Three facets, all modeled on the existing yt-dlp integration:
  * _build_gallerydl_cmd  -- pure CLI builder (unit-testable, no side effects),
    the analogue of _build_ytdlp_cmd incl. the F-RUN01-02 '--' argv terminator.
  * gallerydl_updater      -- binary version/update helper (analogue of
    ytdlp_updater). gallery-dl uses SEMVER (e.g. 1.32.5), not yt-dlp's date
    version, so age/staleness is not locally derivable -> reported as None/False
    rather than fabricated.
  * /api/gallerydl_status + /api/gallerydl_update -- the status/update API,
    thin blueprints delegating to gallerydl_updater.

RED on pristine 3.66.619: none of _build_gallerydl_cmd / gallerydl_updater /
the two routes / the use_gallerydl_fallback config key exist.
"""
import sys


# ---- pure CLI builder --------------------------------------------------------

def test_build_cmd_structure_and_terminator():
    from bulk_downloader.runner_extractors import _build_gallerydl_cmd
    cmd = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                               url="https://example.com/gallery")
    assert cmd[0] == "gallery-dl"
    # destination dir threaded via -d
    assert "-d" in cmd and cmd[cmd.index("-d") + 1] == "/dl"
    # options terminated with a bare '--' immediately before the URL, so a URL
    # beginning with '-' can never be smuggled into gallery-dl's flag surface.
    assert cmd[-2] == "--"
    assert cmd[-1] == "https://example.com/gallery"


def test_build_cmd_url_dash_is_positional():
    from bulk_downloader.runner_extractors import _build_gallerydl_cmd
    cmd = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                               url="-D/etc/passwd")
    # the '--' terminator must sit right before the hostile URL.
    assert cmd[-2] == "--"
    assert cmd[-1] == "-D/etc/passwd"


def test_build_cmd_threads_socks_remote_dns_proxy():
    from bulk_downloader.runner_extractors import _build_gallerydl_cmd
    cmd = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                               url="https://x", proxy_url="socks5://127.0.0.1:9")
    assert "--proxy" in cmd
    # bare socks5:// is rewritten to socks5h:// (remote DNS, no clear-net leak).
    assert cmd[cmd.index("--proxy") + 1] == "socks5h://127.0.0.1:9"


def test_build_cmd_cookies_only_for_existing_txt(tmp_path=None):
    import tempfile, os
    from bulk_downloader.runner_extractors import _build_gallerydl_cmd
    d = tempfile.mkdtemp()
    ck = os.path.join(d, "c.txt")
    open(ck, "w").close()
    cmd = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                               url="https://x", cookie_file=ck)
    assert "--cookies" in cmd and cmd[cmd.index("--cookies") + 1] == ck
    # a non-.txt / missing cookie file is NOT passed.
    cmd2 = _build_gallerydl_cmd(gallerydl="gallery-dl", dl_dir="/dl",
                                url="https://x", cookie_file="/nope/c.sqlite")
    assert "--cookies" not in cmd2


# ---- updater -----------------------------------------------------------------

def test_updater_status_dict_shape():
    from bulk_downloader import gallerydl_updater
    st = gallerydl_updater.status_dict()
    assert set(st) >= {"installed", "version", "stale"}
    assert isinstance(st["installed"], bool)


def test_updater_semver_has_no_fabricated_age():
    from bulk_downloader import gallerydl_updater
    # gallery-dl versions are semver, not dates -> age is not derivable and must
    # not be invented.
    assert gallerydl_updater.version_age_days("1.32.5") is None
    assert gallerydl_updater.is_stale() is False


# ---- API surface -------------------------------------------------------------

def test_status_route(fresh_app):
    r = fresh_app.get("/api/gallerydl_status")
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) >= {"installed", "version", "stale"}


def test_update_route(fresh_app):
    # Operator-initiated pip upgrade. CSRF is disabled in TESTING mode. With no
    # gallery-dl on PATH in the harness it reports ran=False, but the route must
    # exist and answer 200 with the contract shape.
    r = fresh_app.post("/api/gallerydl_update", json={"force": False})
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("ok") is True
    assert "ran" in body and "message" in body


# ---- config toggle -----------------------------------------------------------

def test_use_gallerydl_fallback_default_present():
    from bulk_downloader import app_kernel
    defaults = app_kernel.default_site_config() \
        if hasattr(app_kernel, "default_site_config") else None
    if defaults is None:
        # fall back to scanning the module source for the default entry.
        import inspect
        src = inspect.getsource(app_kernel)
        assert '"use_gallerydl_fallback"' in src
    else:
        assert defaults.get("use_gallerydl_fallback") is False
