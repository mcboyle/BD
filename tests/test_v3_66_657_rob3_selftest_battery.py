"""v3.66.657 -- ROB-3: extend the STARTUP selftest battery with the two health
signals it lacked that healthcheck.py already computes on-demand -- ffmpeg HLS/TS
capability and yt-dlp (extractor) freshness -- so a broken ffmpeg build or a stale
extractor is caught at boot, not only when someone opens the health page.

Both new selftest checks DELEGATE to the proven healthcheck probes
(_check_ffmpeg / _check_ytdlp) and adapt their {severity, message} shape to
selftest's _result(status, test, message) shape. No probe logic is reimplemented.
"""
from bulk_downloader import selftest
from bulk_downloader import healthcheck


def test_check_ffmpeg_hls_maps_ok(monkeypatch):
    monkeypatch.setattr(healthcheck, "_check_ffmpeg",
                        lambda: {"severity": healthcheck.SEV_OK, "message": "ready"})
    r = selftest.check_ffmpeg_hls()
    assert r["status"] == selftest.OK
    assert r["test"] == "ffmpeg_hls"
    assert "ready" in r["message"]


def test_check_ffmpeg_hls_maps_fail(monkeypatch):
    monkeypatch.setattr(healthcheck, "_check_ffmpeg",
                        lambda: {"severity": healthcheck.SEV_FAIL, "message": "not usable"})
    assert selftest.check_ffmpeg_hls()["status"] == selftest.FAIL


def test_check_ffmpeg_hls_maps_warn(monkeypatch):
    monkeypatch.setattr(healthcheck, "_check_ffmpeg",
                        lambda: {"severity": healthcheck.SEV_WARN, "message": "missing mpegts"})
    assert selftest.check_ffmpeg_hls()["status"] == selftest.WARN


def test_check_extractor_freshness_maps_warn(monkeypatch):
    monkeypatch.setattr(healthcheck, "_check_ytdlp",
                        lambda: {"severity": healthcheck.SEV_WARN, "message": "90 days old"})
    r = selftest.check_extractor_freshness()
    assert r["status"] == selftest.WARN
    assert r["test"] == "extractor_freshness"


def test_check_extractor_freshness_maps_ok(monkeypatch):
    monkeypatch.setattr(healthcheck, "_check_ytdlp",
                        lambda: {"severity": healthcheck.SEV_OK, "message": "fresh"})
    assert selftest.check_extractor_freshness()["status"] == selftest.OK


def test_delegates_never_raise(monkeypatch):
    # a probe that blows up must degrade to WARN, never propagate at startup
    def boom():
        raise RuntimeError("subprocess died")
    monkeypatch.setattr(healthcheck, "_check_ffmpeg", boom)
    monkeypatch.setattr(healthcheck, "_check_ytdlp", boom)
    assert selftest.check_ffmpeg_hls()["status"] == selftest.WARN
    assert selftest.check_extractor_freshness()["status"] == selftest.WARN


def test_battery_includes_the_two_new_checks(monkeypatch, tmp_path):
    # run_selftest must now surface ffmpeg_hls + extractor_freshness
    res = selftest.run_all(
        db_path=":memory:", cookies_dir=str(tmp_path),
        sites_config_path=None, download_dirs=[str(tmp_path)],
        captures_root=str(tmp_path))
    tests = {c["test"] for c in res["checks"]}
    assert "ffmpeg_hls" in tests
    assert "extractor_freshness" in tests
