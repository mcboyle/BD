"""U31 — systemd-unit validator (D-101) + dependency-pin checker
(D-100). A deploy-lint pair, both read-only.
"""
import os
import sys

from bulk_downloader import dev_suite as ds

# tests/ is not a package; add this file's dir to sys.path so the
# sibling _env helper imports cleanly under both pytest and the
# custom run_tests.py runner.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _env  # noqa: E402


_GOOD_UNIT = """[Unit]
Description=BulkDownloader
After=network-online.target ollama.service

[Service]
Type=simple
User=mboyle
WorkingDirectory=/home/mboyle/BulkDownloader
ExecStart=/home/mboyle/BulkDownloader/venv/bin/python \
/home/mboyle/BulkDownloader/downloader_ui.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
"""

_NO_WORKDIR = """[Unit]
Description=BulkDownloader

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/bd/downloader_ui.py
Restart=on-failure
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
"""

_MINIMAL_BAD = """[Unit]
Description=BulkDownloader

[Service]
Type=simple
ExecStart=/usr/bin/python3 /somewhere/run.py
"""


# ── systemd_unit_check (D-101) ─────────────────────────────────────

def test_unit_check_passes_a_good_unit():
    r = ds.systemd_unit_check(unit_text=_GOOD_UNIT)
    assert r["ok"] is True
    assert r["verdict"] == "unit is valid"
    assert r["critical_failures"] == []
    assert all(c["pass"] for c in r["checks"])


def test_unit_check_flags_missing_workingdirectory_as_critical():
    r = ds.systemd_unit_check(unit_text=_NO_WORKDIR)
    # WorkingDirectory is the load-bearing one (DANGER_MAP)
    assert "WorkingDirectory set" in r["critical_failures"]
    assert "CRITICAL" in r["verdict"]
    assert "do not deploy" in r["verdict"]


def test_unit_check_flags_non_critical_issues():
    r = ds.systemd_unit_check(unit_text=_MINIMAL_BAD)
    # missing WorkingDirectory is critical; also missing Restart,
    # WantedBy, TimeoutStopSec, wrong ExecStart target
    failed = {c["check"] for c in r["checks"] if not c["pass"]}
    assert "Restart configured" in failed
    assert "[Install] WantedBy set" in failed
    assert "ExecStart runs downloader_ui.py" in failed


def test_unit_check_no_unit_available_is_clean_error():
    # On a sandbox (no unit installed) the auto-locate fails and the
    # tool must report a clean error, never raise. On a real deployment
    # host the unit IS installed at /etc/systemd/system/bulkdownloader
    # .service, so the auto-locate succeeds and the tool returns the
    # checks for the real unit — also ok-true. Both branches assert
    # the right outcome for the host.
    r = ds.systemd_unit_check()
    if _env.HAS_BULKDOWNLOADER_UNIT:
        # real host: auto-located the installed unit and ran checks
        assert r["ok"] is True
        assert "checks" in r
    else:
        # sandbox: no unit to locate, clean error not a crash
        assert r["ok"] is False
        assert "no unit" in r["error"]


def test_unit_check_each_check_has_required_fields():
    r = ds.systemd_unit_check(unit_text=_GOOD_UNIT)
    for c in r["checks"]:
        assert "check" in c and "pass" in c
        assert "detail" in c and "critical" in c


# ── dependency_pin_drift (D-100) ───────────────────────────────────

def test_pin_drift_runs_against_requirements():
    r = ds.dependency_pin_drift()
    assert r["ok"] is True
    assert r["checked"] > 0
    # the three result buckets account for everything checked
    assert (r["satisfied"] + len(r["not_installed"])
            + len(r["version_drift"])) == r["checked"]


def test_pin_drift_distinguishes_missing_from_mismatch():
    r = ds.dependency_pin_drift()
    # not_installed is a flat name list; version_drift carries detail
    for name in r["not_installed"]:
        assert isinstance(name, str)
    for d in r["version_drift"]:
        assert "package" in d and "declared" in d and "installed" in d


def test_pin_drift_verdict_matches_findings():
    r = ds.dependency_pin_drift()
    if not r["not_installed"] and not r["version_drift"]:
        assert "installed and matches" in r["verdict"]
    else:
        assert "drift" in r["verdict"]


# ── routes ─────────────────────────────────────────────────────────

def test_systemd_check_route(fresh_app):
    # no path, no installed unit -> a clean error body, not a crash
    body = fresh_app.get("/api/dev/systemd_check").get_json()
    assert "ok" in body


def test_pin_drift_route(fresh_app):
    body = fresh_app.get("/api/dev/pin_drift").get_json()
    assert body["ok"] is True
    assert "checked" in body
