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


# @977/@979: the contract these two pin CHANGED, and the change was the point.
# Freshness is decided by comparing the installed version against the newest one
# on the index, not by `age_days > 30`. Age could not tell "you are behind" from
# "upstream has been quiet" -- measured on the box, the selftest asked for an
# update while the installed release was already the newest that existed, so the
# recommended action could not clear the warning.
#
# Both now pin `latest_version` explicitly. That is not tidiness: leaving it
# unmocked is exactly what made these tests reach the LIVE PyPI index at
# v3.66.977, which is how the regression surfaced. `latest_version` no longer
# fetches by default, so an unmocked call is merely UNKNOWN rather than a
# network round trip -- but pinning it states what each case is testing.
def test_current_ytdlp_is_ok_regardless_of_age(monkeypatch):
    from bulk_downloader import ytdlp_updater as yt
    monkeypatch.setattr(yt, "status_dict",
                        lambda: {"installed": True, "version": "2026.03.17",
                                 "age_days": 2, "stale": False})
    monkeypatch.setattr(yt, "latest_version", lambda **kw: "2026.03.17")
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_OK
    assert "2026.03.17" in r["message"]
    assert "has no attribute" not in r["message"]


def test_behind_the_index_warns_and_names_the_target(monkeypatch):
    from bulk_downloader import ytdlp_updater as yt
    monkeypatch.setattr(yt, "status_dict",
                        lambda: {"installed": True, "version": "2025.01.01",
                                 "age_days": 90, "stale": True})
    monkeypatch.setattr(yt, "latest_version", lambda **kw: "2026.7.4")
    r = hc._check_ytdlp()
    assert r["severity"] == hc.SEV_WARN
    # the AVAILABLE version, not the age -- age is no longer the signal
    assert "2026.7.4" in r["message"]


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
