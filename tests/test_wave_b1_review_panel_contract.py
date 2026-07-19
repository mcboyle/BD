"""B1 — post-capture review loop: the four safety-gate contracts.

The finish->build-draft->normalize->test->approve loop is assembled from existing
endpoints; this pins the safety POSTURE the panel depends on:

  G1 no-value      — nothing value-bearing survives build_template -> normalize
                     into the review candidate (scan_artifact_secrets clean).
  G2 never-enabled — normalize_draft never emits an `enabled` candidate; promotion
                     stays an explicit separate step (fail-open-into-review).
  G3 review-only   — the operator-recorded workflow (action_timeline -> derived_steps
                     + trigger_candidate) REACHES the review candidate, but as
                     provenance only — never as the runtime download trigger.
  G4 live-test     — the live-page selector test (api_template_sandbox) refuses
                     non-http(s) schemes and a non-object template; it is a
                     read-only matcher that enables nothing.

Browser-free; stdlib + project modules. G1-G3 are pure; G4 uses a request context
(CSRF is skipped without a session cookie, so no client/session dance needed).
"""
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import capture_model_golden as G                # signed synthetic capture w/ action_timeline
import build_template_from_wacz as BTW
from bulk_downloader import template_normalize as TN
from bulk_downloader.wacz_export import write_wacz
from bulk_downloader.capture_artifact_redact import scan_artifact_secrets


def _candidate():
    cap = G.fixed_capture()                      # token=SECRET page url + sig=ABC api + media + action_timeline
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "f.wacz"
        write_wacz(cap, str(w))
        draft = BTW.build_template(w)
    return draft, TN.normalize_draft(draft)


def test_g1_no_value_leaks_into_review_candidate():
    _draft, cand = _candidate()
    findings = scan_artifact_secrets(cand)
    assert findings == [], findings


def test_g2_candidate_is_never_enabled():
    _draft, cand = _candidate()
    assert cand["status"] in ("review_ready", "draft_review_required"), cand["status"]
    assert cand["status"] != "enabled"


def test_g3_recorded_workflow_reaches_candidate_review_only():
    _draft, cand = _candidate()
    wf = cand.get("workflow")
    assert wf is not None, "recorded workflow did not reach the review candidate"
    assert wf.get("source") == "action_timeline", wf.get("source")
    assert wf.get("trigger_candidate") == "button.dl"
    assert wf.get("derived_steps"), "derived_steps empty on the candidate"
    # review-only: the recorded trigger_candidate is provenance, not wired as the
    # runtime download trigger (runtime runs off selectors.download.trigger).
    dl = cand.get("selectors", {}).get("download", {})
    assert "trigger_candidate" not in dl, "recorded candidate leaked into runtime selectors"


def test_g4_live_page_test_refuses_non_http_and_non_object_template():
    from bulk_downloader.app import app
    from bulk_downloader.app_template import api_template_sandbox
    for bad_url in ("file:///etc/passwd", "ftp://x/y", "javascript:1"):
        with app.test_request_context(
                "/api/template_sandbox", method="POST",
                json={"url": bad_url, "template": {"selectors": {}}}):
            resp = api_template_sandbox()
            status = resp[1] if isinstance(resp, tuple) else 200
            assert status == 400, (bad_url, status)
    # non-object template is rejected
    with app.test_request_context(
            "/api/template_sandbox", method="POST",
            json={"url": "https://demo.example/v/9", "template": "not-an-object"}):
        resp = api_template_sandbox()
        status = resp[1] if isinstance(resp, tuple) else 200
        assert status == 400, status
