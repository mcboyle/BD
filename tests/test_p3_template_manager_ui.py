"""Endpoint + render tests for the additive Template Manager blueprint (#6/P3).

These cover registration, the inventory JSON, the drift endpoint, and that the
rendered page exposes the required fields and is purely read-only (no action
affordances). They do NOT exercise a live browser — the page still
NEEDS OPERATOR CLICK-THROUGH VALIDATION.

Runs under run_tests.py: zero-arg functions, repo root from __file__.
"""
import sys
from pathlib import Path

from flask import Flask

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bulk_downloader.app_template_manager_ui import (  # noqa: E402
    register_routes, template_manager_ui_bp)


def _client():
    app = Flask(__name__)
    register_routes(app)
    return app.test_client()


def test_register_routes_adds_three():
    app = Flask(__name__)
    n = register_routes(app)
    assert n == 3, n


def test_inventory_endpoint_ok_and_shape():
    r = _client().get("/api/template_manager/inventory")
    assert r.status_code == 200, r.status_code
    data = r.get_json()
    assert data["ok"] is True
    for d in ("reviewed", "enabled", "drafts", "review_candidates"):
        assert d in data["dirs"], d
    # gold reptyle present in reviewed, fully scored + gate-ready
    reviewed = data["dirs"]["reviewed"]
    assert any(row["host"] == "app.reptyle.com" and row["completeness_score"] == 100
               and row["promotion_ready"] for row in reviewed), reviewed


def test_inventory_rows_carry_required_fields():
    data = _client().get("/api/template_manager/inventory").get_json()
    required = {"host", "status", "selector_groups", "resolutions",
                "network_patterns_count", "completeness_score", "blocked_terms",
                "promotion_ready", "download_trigger", "row_selectors_count",
                "api_base", "drift_available"}
    for rows in data["dirs"].values():
        for row in rows:
            assert required <= set(row), required - set(row)


def test_page_renders_and_marks_validation():
    r = _client().get("/cockpit/template-manager")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Template Manager" in body
    assert "Needs operator click-through validation" in body
    assert "read-only" in body
    assert "app.reptyle.com" in body  # real data rendered


def test_page_is_read_only_no_action_affordances():
    body = _client().get("/cockpit/template-manager").get_data(as_text=True)
    # purely informational: no forms, no buttons, no POST affordances
    assert "<form" not in body.lower()
    assert "<button" not in body.lower()
    assert 'method="post"' not in body.lower()


def test_drift_endpoint_reptyle_draft_vs_gold():
    """Drift diff for the reptyle app-host candidate vs its reviewed gold.

    SELF-SEEDING (v3.66.515): `app.reptyle.com.template-draft.json` is a RUNTIME
    artifact built by onboarding -- it is NOT shipped, so it is absent on a fresh
    box / the sandbox, and live capture work can rename or consume it (the v513
    on-stash flake: the box had `auth.reptyle.com.template-draft.json` +
    `app.reptyle.com.candidate.json` but not the exact name this test asks for ->
    400). Don't depend on whatever drafts happen to exist: seed a valid candidate
    from the shipped reviewed gold when the draft is absent, and clean up ONLY
    what we created (never delete a real pre-existing draft).
    """
    import json
    fname = "app.reptyle.com.template-draft.json"
    gold = _REPO / "templates" / "reviewed" / "app.reptyle.com.template.json"
    drafts = _REPO / "templates" / "drafts"
    cands = _REPO / "templates" / "review_candidates"
    pre_existing = (cands / fname).is_file() or (drafts / fname).is_file()
    seeded = None
    if not pre_existing:
        # the gold ships (reptyle reviewed is enabled); derive a real candidate
        # from it so _load + _default_gold + the drift diff all resolve.
        assert gold.is_file(), f"shipped reviewed gold missing: {gold}"
        cand = json.loads(gold.read_text("utf-8"))
        cand["host"] = "app.reptyle.com"  # ensure _default_gold resolves the host
        drafts.mkdir(parents=True, exist_ok=True)
        seeded = drafts / fname
        seeded.write_text(json.dumps(cand), "utf-8")
    try:
        r = _client().get("/api/template_manager/drift",
                          query_string={"file": fname})
        assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
        data = r.get_json()
        assert data["ok"] is True
        assert "drift" in data and isinstance(data["drift"], list)
        assert data["gold"].startswith("app.reptyle.com")
    finally:
        if seeded is not None and seeded.is_file():
            seeded.unlink()


def test_drift_endpoint_bad_file_400():
    r = _client().get("/api/template_manager/drift", query_string={"file": "nope.json"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False
