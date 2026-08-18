"""v3.66.1180 -- yt-dlp is usable through this interpreter even without PATH.

Fleet capture found five hosts where ``venv/bin/python -m yt_dlp --version``
returned 2026.07.04 while no ``yt-dlp`` console script was on PATH.  Status
therefore called a usable installation absent, and path-only fallback consumers
could not invoke it.  These tests pin one argv resolver for the status probe and
for the download/info command builders; no shell is involved.
"""
from types import SimpleNamespace

import pytest


BD_GATE_SCOPE = "module"


def _completed(version="2026.07.04", returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=version + "\n", stderr="broken")


def _fresh(monkeypatch):
    from bulk_downloader import ytdlp_updater as yt
    monkeypatch.setattr(yt, "_VERSION_CACHE", {
        "ts": 0.0, "version": None, "key": None,
        "source": "unavailable", "error": "unavailable",
    })
    return yt


def test_explicit_ytdlp_executable_wins_over_path(monkeypatch):
    yt = _fresh(monkeypatch)
    monkeypatch.setattr(yt.shutil, "which", lambda _name: "/path/yt-dlp")
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: object())
    assert yt.resolve_ytdlp_argv("/explicit/yt-dlp") == ("/explicit/yt-dlp",)


def test_path_ytdlp_version_probe_uses_the_path_executable(monkeypatch):
    yt = _fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if name == "yt-dlp" else None)
    monkeypatch.setattr(yt.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or _completed())

    assert yt.current_version() == "2026.07.04"
    assert calls == [(["/path/yt-dlp", "--version"], {
        "capture_output": True, "text": True, "timeout": 10, "shell": False,
    })]


def test_console_script_precedes_module_when_both_are_usable(monkeypatch):
    yt = _fresh(monkeypatch)
    monkeypatch.setattr(yt.sys, "executable", "/venv/python")
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if name == "yt-dlp" else None)

    assert yt.resolve_ytdlp_argv() == ("/path/yt-dlp",)


def test_module_only_install_is_truthful_in_status_and_uses_same_interpreter(monkeypatch):
    yt = _fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(yt.shutil, "which", lambda _name: None)
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(yt.sys, "executable", "/opt/bulk-downloader/venv/bin/python")
    monkeypatch.setattr(yt.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or _completed())
    monkeypatch.setattr(yt, "version_age_days", lambda _version: 45)

    assert yt.resolve_ytdlp_argv() == ("/opt/bulk-downloader/venv/bin/python", "-m", "yt_dlp")
    assert yt.status_dict() == {
        "installed": True, "version": "2026.07.04", "age_days": 45,
        "stale": True, "probe_source": "module", "probe_error": None,
    }
    assert calls == [([
        "/opt/bulk-downloader/venv/bin/python", "-m", "yt_dlp", "--version",
    ], {"capture_output": True, "text": True, "timeout": 10, "shell": False})]


def test_no_path_binary_and_no_interpreter_is_not_installed(monkeypatch):
    yt = _fresh(monkeypatch)
    monkeypatch.setattr(yt.shutil, "which", lambda _name: None)
    monkeypatch.setattr(yt.sys, "executable", "")
    monkeypatch.setattr(yt.subprocess, "run", lambda *_a, **_kw: pytest.fail("must not spawn"))

    assert yt.status_dict()["installed"] is False
    assert yt.status_dict()["probe_source"] == "unavailable"
    assert yt.status_dict()["probe_error"] == "unavailable"


def test_module_absence_is_visible_before_path_fallback(monkeypatch):
    yt = _fresh(monkeypatch)
    monkeypatch.setattr(yt.sys, "executable", "/venv/python")
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(yt.shutil, "which", lambda _name: None)

    status = yt.status_dict()
    assert status["installed"] is False
    assert status["probe_source"] == "unavailable"
    assert status["probe_error"] == "module_absent"


@pytest.mark.parametrize(
    ("outcome", "error"),
    [
        (subprocess_timeout := __import__("subprocess").TimeoutExpired(["yt-dlp"], 10), "timeout"),
        (_completed(returncode=2), "command_failed"),
        (_completed("not-a-ytdlp-version"), "malformed_output"),
        (_completed("2026.07.04\nnot-version-output"), "malformed_output"),
    ],
)
def test_probe_failures_are_visible_and_never_request_a_shell(monkeypatch, outcome, error):
    yt = _fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if name == "yt-dlp" else None)

    def run(argv, **kw):
        calls.append((argv, kw))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(yt.subprocess, "run", run)
    status = yt.status_dict()
    assert status["installed"] is False and status["version"] is None
    assert status["probe_source"] == "path" and status["probe_error"] == error
    assert calls[0][0] == ["/path/yt-dlp", "--version"]
    assert calls[0][1]["shell"] is False


@pytest.mark.parametrize("version", [
    "2026.07.04+nightly",
    "2026.07.04.123456",
    "2026.07.04-master",
    "2026.07.04 [master]",
])
def test_extended_ytdlp_versions_are_truthful_not_malformed(monkeypatch, version):
    yt = _fresh(monkeypatch)
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if name == "yt-dlp" else None)
    monkeypatch.setattr(yt.subprocess, "run", lambda *_a, **_kw: _completed(version))

    assert yt.current_version() == version


def test_cache_key_includes_resolver_source_so_module_probe_cannot_reuse_path_failure(monkeypatch):
    yt = _fresh(monkeypatch)
    source = {"path": True}
    module = {"available": False}
    calls = []
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if source["path"] and name == "yt-dlp" else None)
    monkeypatch.setattr(yt.sys, "executable", "/venv/python")
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: object() if module["available"] else None)

    def run(argv, **kw):
        calls.append(argv)
        return _completed(returncode=2) if argv[0] == "/path/yt-dlp" else _completed()

    monkeypatch.setattr(yt.subprocess, "run", run)
    assert yt.current_version() is None
    source["path"] = False
    module["available"] = True
    assert yt.current_version() == "2026.07.04"
    assert calls == [["/path/yt-dlp", "--version"], ["/venv/python", "-m", "yt_dlp", "--version"]]


def test_failed_probe_is_not_cached_for_the_same_resolved_command(monkeypatch):
    yt = _fresh(monkeypatch)
    calls = []
    outcomes = [_completed(returncode=2), _completed()]
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if name == "yt-dlp" else None)
    monkeypatch.setattr(yt.subprocess, "run",
                        lambda argv, **_kw: calls.append(argv) or outcomes.pop(0))

    assert yt.current_version() is None
    assert yt.current_version() == "2026.07.04"
    assert calls == [["/path/yt-dlp", "--version"], ["/path/yt-dlp", "--version"]]


def test_successful_probe_is_reused_for_the_same_resolved_command(monkeypatch):
    yt = _fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(yt.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(yt.shutil, "which", lambda name: "/path/yt-dlp" if name == "yt-dlp" else None)
    monkeypatch.setattr(yt.subprocess, "run",
                        lambda argv, **_kw: calls.append(argv) or _completed())

    assert yt.current_version() == "2026.07.04"
    assert yt.current_version() == "2026.07.04"
    assert calls == [["/path/yt-dlp", "--version"]]


def test_module_argv_stays_flat_and_url_stays_after_option_separator():
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd
    from bulk_downloader.ytdlp_extractor import build_ytdlp_info_cmd

    module_argv = ["/venv/python", "-m", "yt_dlp"]
    dangerous_url = "--exec=touch /tmp/not-run"
    download = _build_ytdlp_cmd(ytdlp=module_argv, dl_dir="/tmp/dl", url=dangerous_url)
    info = build_ytdlp_info_cmd(ytdlp=module_argv, url=dangerous_url)
    assert download[:3] == module_argv and download[-2:] == ["--", dangerous_url]
    assert info[:3] == module_argv and info[-2:] == ["--", dangerous_url]


def test_info_plugin_consumer_executes_the_module_argv(monkeypatch):
    from bulk_downloader.ytdlp_extractor import make_ytdlp_extractor

    calls = []
    payload = ('{"title":"v","url":"https://cdn/v.mp4","protocol":"https","ext":"mp4"}')
    fn = make_ytdlp_extractor(
        {}, resolve=lambda: ("/venv/python", "-m", "yt_dlp"),
        run=lambda argv: calls.append(argv) or (0, payload, ""),
    )
    assert fn("https://example/video", {})["video_url"] == "https://cdn/v.mp4"
    assert calls[0][:3] == ["/venv/python", "-m", "yt_dlp"]


def test_download_fallback_consumer_executes_the_module_argv(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from bulk_downloader import runner_extractors as rx
    from bulk_downloader import ytdlp_updater as yt

    @contextmanager
    def no_netns(*_args, **_kwargs):
        yield None

    calls = []
    monkeypatch.setattr(yt, "resolve_ytdlp_argv", lambda: ("/venv/python", "-m", "yt_dlp"))
    monkeypatch.setattr(rx.netns_isolation, "capture_netns", no_netns)
    monkeypatch.setattr(rx.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or _completed(returncode=1))
    runner = SimpleNamespace(
        config={"use_ytdlp_fallback": True, "download_dir": str(tmp_path)},
        site_id="module-only", _download_proxy_url=lambda: None,
        log_event=lambda *_args, **_kwargs: None,
    )
    result = rx.ExtractorsMixin._try_ytdlp_fallback(runner, "https://example/video")
    assert result[0] is False
    assert calls[0][0][:3] == ["/venv/python", "-m", "yt_dlp"]
    assert calls[0][0][-2:] == ["--", "https://example/video"]


def test_update_uses_module_resolver_without_broadening_absence(monkeypatch):
    yt = _fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(yt, "_resolve_ytdlp_argv",
                        lambda _executable=None: (("/venv/python", "-m", "yt_dlp"), "module", None))
    monkeypatch.setattr(yt, "current_version", lambda: "2026.07.04")
    monkeypatch.setattr(yt, "latest_version", lambda **_kw: None)
    monkeypatch.setattr(yt.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or _completed())

    assert yt.maybe_update(force=True)[0] is True
    assert calls[0][0] == [yt.sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"]
    assert ("module", ("/venv/python", "-m", "yt_dlp")) in yt._LAST_UPDATE_CHECK


def test_update_resolves_once_when_a_fresh_version_skips_pip(monkeypatch):
    yt = _fresh(monkeypatch)
    resolutions = []

    def resolve_once(_executable=None):
        resolutions.append(True)
        return (("/path/yt-dlp",), "path", None)

    monkeypatch.setattr(yt, "_resolve_ytdlp_argv", resolve_once)
    monkeypatch.setattr(yt, "version_age_days", lambda _version=None: 0)
    def version_only(argv, **_kw):
        assert argv == ["/path/yt-dlp", "--version"]
        return _completed()
    monkeypatch.setattr(yt.subprocess, "run", version_only)

    assert yt.maybe_update() == (False, "yt-dlp is fresh (≤30d old)")
    assert len(resolutions) == 1


def test_youtube_cipher_consumer_uses_module_resolver_when_path_is_absent(monkeypatch):
    from bulk_downloader import provider_resolve as pr
    from bulk_downloader import ytdlp_updater as yt

    calls = []
    monkeypatch.setattr(pr, "_yt_cipher_ytdlp_path", lambda: None)
    monkeypatch.setattr(yt, "resolve_ytdlp_argv", lambda: ["/venv/python", "-m", "yt_dlp"])

    def run(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(returncode=0, stdout=b'{"formats": []}', stderr=b"")

    candidates, error = pr._decipher_signed_formats_ytdlp("dQw4w9WgXcQ", _run=run)
    assert candidates == [] and "no formats" in error
    assert calls[0][0][:3] == ["/venv/python", "-m", "yt_dlp"]


def test_youtube_cipher_ignores_cached_path_and_uses_canonical_resolver(monkeypatch):
    from bulk_downloader import provider_resolve as pr
    from bulk_downloader import ytdlp_updater as yt

    calls = []
    stale_cache = pr._YT_CIPHER_YTDLP_PATH_CACHE[0]
    pr._YT_CIPHER_YTDLP_PATH_CACHE[0] = "/stale/yt-dlp"
    monkeypatch.setattr(yt, "resolve_ytdlp_argv", lambda: ("/canonical/yt-dlp",))

    def run(argv, **_kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=b'{"formats": []}', stderr=b"")

    try:
        candidates, error = pr._decipher_signed_formats_ytdlp("dQw4w9WgXcQ", _run=run)
        assert candidates == [] and "no formats" in error
        assert calls[0][0] == "/canonical/yt-dlp"
    finally:
        pr._YT_CIPHER_YTDLP_PATH_CACHE[0] = stale_cache
