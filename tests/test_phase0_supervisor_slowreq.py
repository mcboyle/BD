"""Phase-0 finish (sub-items B + C).
B (supervisor -> app health): the health checklist surfaces the in-app bandwidth
supervisor's state. App-PROCESS supervision is systemd's job (auto-restart) and
/api/health already reports uptime; this connects the in-app supervisor to the
health view so 'supervisor' appears in app health.
C (soft cost ceiling): a pathologically slow request logs an immediate warning.
The per-endpoint timing aggregate already exists (record_request); this is the
immediate operator signal for the 'expensive endpoint' class."""
from bulk_downloader import healthcheck, dev_metrics


def test_healthcheck_has_supervisor_item():
    r = healthcheck._check_supervisor()
    assert "severity" in r and "message" in r
    assert "supervisor" in r["message"].lower()


def test_run_checklist_includes_supervisor():
    rep = healthcheck.run_checklist(s_cfg={})
    names = [c.get("name") for c in rep.get("checks", [])]
    assert "supervisor" in names, f"supervisor missing from checklist: {names}"


def test_slow_request_note_warns_above_threshold_only():
    assert dev_metrics.slow_request_note("GET", "/api/x", 12000.0) is not None
    assert dev_metrics.slow_request_note("GET", "/api/x", 500.0) is None
    note = dev_metrics.slow_request_note("POST", "/api/scan", 15000.0)
    assert "/api/scan" in note and "15" in note
