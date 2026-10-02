from collections import Counter
from pathlib import Path

import pytest
from flask import Flask
from tools import autonomy_housekeeping as hk
from tools import cockpit_core as core
from tools import cockpit_templates as ct

BD_GATE_SCOPE = "module"


@pytest.fixture
def notification_inputs(monkeypatch, tmp_path):
    sites = [{"id": "alpha"}, {"id": "beta"}]
    health = {"sites": [{"site": s["id"], "template_present": False} for s in sites]}
    monkeypatch.setattr(ct, "_load_sites_config", lambda: sites)
    monkeypatch.setattr(ct, "login_template_health", lambda: health)
    monkeypatch.setattr(ct, "video_template_health", lambda: health)
    monkeypatch.setattr(ct, "template_maturity_score", lambda: {"sites": []})
    monkeypatch.setattr(ct, "template_review_queue", lambda: {"items": []})
    monkeypatch.setattr(ct, "drift_frequency", lambda: {"sites": []})
    monkeypatch.setattr(ct, "_site_drift_events", lambda *args: [])
    monkeypatch.setattr(ct, "_site_capture_quality_map", dict)
    monkeypatch.setattr(ct, "_ops_mission", dict)
    monkeypatch.setattr(core, "reports_root", lambda: tmp_path)
    monkeypatch.setattr(core, "tasks_root", lambda: tmp_path)
    monkeypatch.setattr(core, "_debt_obj", dict)
    corpus = [
        {"id": "a", "subject": "alpha", "date": "recent", "category": "capture", "outcome": "pass"},
        {"id": "b", "subject": "beta", "date": "old", "category": "capture", "outcome": "pass"},
        {"id": "c", "subject": "other", "date": "recent", "category": "capture", "outcome": "pass"},
        {"id": "d", "subject": "beta", "date": "invalid", "category": "capture", "outcome": "pass"},
    ]
    monkeypatch.setattr(core, "_corpus", lambda: corpus)
    monkeypatch.setattr(core, "_site_of_entry", lambda entry: entry["subject"])
    monkeypatch.setattr(ct, "_days_since", lambda date: {"recent": 3, "old": 120}.get(date))
    scans = Counter()
    original_rglob = Path.rglob

    def counted_rglob(path, pattern):
        if pattern in ("site_profile.json", "rendition_profile.json"):
            scans[pattern] += 1
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", counted_rglob)
    monkeypatch.setattr(hk, "_guard", lambda *args: None)
    monkeypatch.setattr(hk, "_notif_path", lambda: tmp_path / "notifications.json")
    return scans, corpus


def test_notification_get_avoids_per_site_profile_tree_walks(notification_inputs):
    from tools.cockpit_console import api_autonomy_notifications

    scans, _ = notification_inputs
    app = Flask(__name__)
    app.add_url_rule("/cockpit/api/autonomy/notifications", view_func=api_autonomy_notifications)
    response = app.test_client().get("/cockpit/api/autonomy/notifications")
    assert response.status_code == 200
    result = response.get_json()
    assert result["notifications"] == []
    assert result["would_create"] == 4
    assert not scans, f"F044_PROFILE_WALK: notification GET scanned per-site profiles: {dict(scans)}"


def test_readiness_preserves_site_evidence_and_refreshes_between_calls(notification_inputs):
    _, corpus = notification_inputs
    first = {r["site"]: r for r in ct.site_readiness()["sites"]}
    assert first["alpha"]["inputs"]["evidence_age_days"] == 3
    assert first["beta"]["inputs"]["evidence_age_days"] == 120
    assert first["alpha"]["components"]["evidence_freshness"] == 1.0
    assert first["beta"]["components"]["evidence_freshness"] == 0.2
    corpus[1]["date"] = "recent"
    second = {r["site"]: r for r in ct.site_readiness()["sites"]}
    assert second["beta"]["inputs"]["evidence_age_days"] == 3


def test_profile_tree_walk_counter_can_detect_real_site_intelligence(notification_inputs):
    scans, _ = notification_inputs
    assert core.site_intelligence("alpha")["n_corpus_entries"] == 1
    assert scans["site_profile.json"] == 2
    assert scans["rendition_profile.json"] == 2
    assert scans.total() == 4
