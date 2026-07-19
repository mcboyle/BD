"""v3.66.661 -- healthcheck._check_ytdlp shape fix (loose end from 657's ROB-3).

healthcheck._check_ytdlp treated ytdlp_updater.current_version()'s return as a dict
(info.get("age_days")), but current_version() returns a VERSION STRING (or None). So a
normal install produced `'str' object has no attribute 'get'`, caught and degraded to a
noisy WARN -- visible in the boot selftest since 657 folded this probe into startup. The
fix reads ytdlp_updater.status_dict() (the purpose-built {installed, version, age_days,
stale} dict) instead, so the check reports real freshness. Pure robustness; no behavior
change beyond correct severity + a clean message.
"""
from bulk_downloader import healthcheck as hc


def test_fresh_ytdlp_is_ok(monkeypatch):
    from bulk_downloader import ytdlp_updater as yt
    monkeypatch.setattr(yt, "status_dict",
                        lambda: {"installed": True, "version": "2026.03.17",
                                 "age_days": 2, "stale": False})
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_OK
    assert "2026.03.17" in r["message"]
    assert "has no attribute" not in r["message"]


def test_stale_ytdlp_warns(monkeypatch):
    from bulk_downloader import ytdlp_updater as yt
    monkeypatch.setattr(yt, "status_dict",
                        lambda: {"installed": True, "version": "2025.01.01",
                                 "age_days": 90, "stale": True})
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_WARN
    assert "90" in r["message"]


def test_unknown_age_warns(monkeypatch):
    from bulk_downloader import ytdlp_updater as yt
    monkeypatch.setattr(yt, "status_dict",
                        lambda: {"installed": True, "version": "weird",
                                 "age_days": None, "stale": False})
    assert hc._check_ytdlp()["severity"] == hc.SEV_WARN


def test_never_leaks_attribute_error(monkeypatch):
    # even if status_dict raises, the check degrades to WARN with a clean message,
    # never the '"str" object has no attribute "get"' regression.
    from bulk_downloader import ytdlp_updater as yt
    def boom():
        raise RuntimeError("probe failed")
    monkeypatch.setattr(yt, "status_dict", boom)
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_WARN
    assert "has no attribute" not in r["message"]
