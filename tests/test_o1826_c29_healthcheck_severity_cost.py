"""O1826 BRIEF-29 (M061, M062): healthcheck severity + probe cost.

M061 -- ``healthcheck._check_disk`` decided FAIL by searching the joined issue
text for ``"only"``. The issue text embeds the site id and the path, so a
low-space WARN for site ``onlyfans`` (or a dir named ``only_media``) became
FAIL. Severity must come from a flag recorded with each issue.

M062 -- every ``run_checklist`` call re-ran the Playwright driver, the ffmpeg
subprocess probes and the yt-dlp index query. Those probes are served from a
short-TTL cache inside ``run_checklist``.

Fixtures only: disk usage, download roots and every check are faked; nothing
live is touched.
"""
import types

BD_GATE_SCOPE = "module"

from bulk_downloader import healthcheck as hc
from bulk_downloader import library_final


_GB = 1024 ** 3


def _fake_disk(monkeypatch, roots, free_by_path):
    monkeypatch.setattr(library_final, "download_roots_by_site",
                        lambda _cfg: list(roots))
    capture_store = hc.os.path.dirname(
        hc.os.path.dirname(hc.os.path.abspath(hc.__file__)))

    def disk_usage(path):
        free = free_by_path.get(path, 500 * _GB)
        return types.SimpleNamespace(total=free, used=0, free=free)

    monkeypatch.setattr(hc.shutil, "disk_usage", disk_usage)
    return capture_store


# ── M061: severity is a per-issue flag, not a substring of the text ───────

def test_low_space_warn_for_site_named_onlyfans_stays_warn(monkeypatch,
                                                            tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    _fake_disk(monkeypatch, [("onlyfans", str(d))], {str(d): 5 * _GB})

    r = hc._check_disk({})

    # PRECONDITION: the issue text really carries the site id, i.e. the
    # substring the old rule matched is present.
    assert "onlyfans" in r["message"] and "(low)" in r["message"], r
    assert r["severity"] == hc.SEV_WARN, r


def test_low_space_warn_for_path_containing_only_stays_warn(monkeypatch,
                                                             tmp_path):
    d = tmp_path / "only_media"
    d.mkdir()
    _fake_disk(monkeypatch, [("vixen", str(d))], {str(d): 5 * _GB})

    r = hc._check_disk({})

    assert "only_media" in r["message"] and "(low)" in r["message"], r
    assert r["severity"] == hc.SEV_WARN, r


def test_missing_dir_is_still_fail_even_beside_a_warn(monkeypatch, tmp_path):
    low = tmp_path / "low"
    low.mkdir()
    gone = tmp_path / "gone"
    _fake_disk(monkeypatch,
               [("onlyfans", str(low)), ("vixen", str(gone))],
               {str(low): 5 * _GB})

    r = hc._check_disk({})

    assert "does not exist" in r["message"], r
    assert r["severity"] == hc.SEV_FAIL, r


def test_under_one_gb_is_still_fail(monkeypatch, tmp_path):
    d = tmp_path / "media"
    d.mkdir()
    _fake_disk(monkeypatch, [("vixen", str(d))], {str(d): _GB // 2})

    r = hc._check_disk({})

    assert "0.5 GB free" in r["message"], r
    assert r["severity"] == hc.SEV_FAIL, r


def test_plenty_of_space_is_ok(monkeypatch, tmp_path):
    d = tmp_path / "only_media"
    d.mkdir()
    _fake_disk(monkeypatch, [("onlyfans", str(d))], {})

    r = hc._check_disk({})

    assert r["severity"] == hc.SEV_OK, r
    assert r["message"] == "2 path(s) ok (incl. capture store)", r


# ── M062: expensive probes are cached inside run_checklist ────────────────

_EXPENSIVE = ("_check_playwright", "_check_ffmpeg", "_check_ytdlp")
_CHEAP = ("_check_database", "_check_disk", "_check_recent_failures",
          "_check_circuit_breakers", "_check_account_health",
          "_check_bitrot", "_check_supervisor")


def _install_counting_checks(monkeypatch):
    calls = {name: 0 for name in _EXPENSIVE + _CHEAP}

    def make(name):
        def probe(*_a, **_k):
            calls[name] += 1
            return {"severity": hc.SEV_OK, "message": f"{name} ok"}
        return probe

    for name in calls:
        monkeypatch.setattr(hc, name, make(name))
    return calls


def test_second_run_checklist_serves_expensive_probes_from_cache(
        monkeypatch):
    calls = _install_counting_checks(monkeypatch)

    first = hc.run_checklist(s_cfg={})
    second = hc.run_checklist(s_cfg={})

    # PRECONDITION: the first run probed everything once.
    assert first["summary"]["ok"] == 10, first["summary"]
    assert second["summary"]["ok"] == 10, second["summary"]
    for name in _EXPENSIVE:
        assert calls[name] == 1, (name, calls)
    # Cheap checks are NOT cached: they still run every time.
    for name in _CHEAP:
        assert calls[name] == 2, (name, calls)


def test_expired_cache_reprobes(monkeypatch):
    calls = _install_counting_checks(monkeypatch)
    monkeypatch.setattr(hc, "_PROBE_TTL_S", 0.0)

    hc.run_checklist(s_cfg={})
    hc.run_checklist(s_cfg={})

    for name in _EXPENSIVE:
        assert calls[name] == 2, (name, calls)


def test_replaced_probe_is_not_answered_from_the_old_probes_cache(
        monkeypatch):
    calls = _install_counting_checks(monkeypatch)
    hc.run_checklist(s_cfg={})

    def ffmpeg_fail():
        return {"severity": hc.SEV_FAIL, "message": "ffmpeg broken"}

    monkeypatch.setattr(hc, "_check_ffmpeg", ffmpeg_fail)
    report = hc.run_checklist(s_cfg={})

    entry = [c for c in report["checks"] if c["name"] == "ffmpeg"][0]
    assert entry["status"] == "fail", entry
    assert calls["_check_playwright"] == 1, calls
