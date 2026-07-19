"""Wave B1 panel UI — surface the REVIEW-ONLY observed-workflow block on the
review-candidate workbench.

233 (Cut B1 data) had ``template_normalize._review_workflow`` carry the observed
``workflow`` (derived_steps + trigger_candidate + advisory verify) onto the
review candidate. This cut is the PANEL UI:

  * GET /api/review-candidates now forwards a STRUCTURAL ``workflow`` summary per
    candidate (known keys only — never ``**wf``), or ``None`` when the candidate
    has no workflow block (pre-Wave-B drafts).
  * The ``templatereview`` cockpit page renders that block (observed steps, the
    observed trigger labelled provenance-only, the advisory verify readout).

No new route (it is the same ``/api/review-candidates`` decorator — body only),
so route counts / parity / G12 are untouched. POSTURE: the block is structural;
derived_steps are scrubbed pre-formatted strings; the endpoint forwards only the
named keys — a positive control plants an unexpected value-bearing key and
asserts it never crosses the boundary.

Synthetic candidate files only; cc._ROOT is redirected to a temp tree so the
repo is never written.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import cockpit_console as console  # noqa: E402
import flask  # noqa: E402

cc = console.cc
_ORIG_ROOT = cc._ROOT


def _client():
    app = flask.Flask(__name__)
    app.register_blueprint(console.bp)
    return app.test_client()


def _redirect_root() -> Path:
    cc._ROOT = Path(tempfile.mkdtemp(prefix="rootb1_"))
    return cc._ROOT


def _write_candidate(root: Path, name: str, body: dict) -> None:
    d = root / "templates" / "review_candidates"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(body), encoding="utf-8")


_WORKFLOW = {
    "derived_steps": [
        "navigate: page loaded (meta)",
        "interact: button (button[aria-label='play']) -> req:3 manifest:1 seg:0 direct:0",
    ],
    "trigger_candidate": "button[aria-label='play']",
    "trigger_evidence": "operator click whose effect first produced media (action_timeline)",
    "source": "action_timeline",
    "verify": {
        "tier": "ready",
        "checks": ["load", "play"],
        "warnings": ["1 click fired 0 network"],
        "gap_count": 0,
        "action_count": 2,
    },
}


def _candidate_with_workflow() -> dict:
    return {
        "host": "example.com",
        "status": "review_ready",
        "warnings": [],
        "resolutions": ["selectors"],
        "selectors": {"download": {"trigger": "a.dl"}},
        "source": {"captured_at": "2026-06-13"},
        "network_patterns": [],
        "workflow": dict(_WORKFLOW),
    }


def test_endpoint_forwards_workflow_block() -> None:
    root = _redirect_root()
    try:
        _write_candidate(root, "example.com.candidate.json", _candidate_with_workflow())
        r = _client().get("/cockpit/api/review-candidates")
        assert r.status_code == 200
        c = r.get_json()["candidates"][0]
        wf = c["workflow"]
        assert wf is not None
        assert wf["source"] == "action_timeline"
        assert wf["trigger_candidate"] == "button[aria-label='play']"
        assert len(wf["derived_steps"]) == 2
        assert all(isinstance(s, str) for s in wf["derived_steps"])
        assert wf["verify"]["tier"] == "ready"
        assert wf["verify"]["gap_count"] == 0
        assert wf["verify"]["action_count"] == 2
        assert wf["verify"]["checks"] == ["load", "play"]
    finally:
        cc._ROOT = _ORIG_ROOT


def test_endpoint_workflow_null_when_absent() -> None:
    root = _redirect_root()
    try:
        body = _candidate_with_workflow()
        body.pop("workflow")
        _write_candidate(root, "noworkflow.candidate.json", body)
        r = _client().get("/cockpit/api/review-candidates")
        c = r.get_json()["candidates"][0]
        assert c["workflow"] is None
    finally:
        cc._ROOT = _ORIG_ROOT


def test_endpoint_forwards_only_named_keys_posture() -> None:
    """Positive control: plant value-bearing keys the forwarder does NOT name
    (a leaked URL and a secret in verify). They must never cross the boundary —
    only the structural keys appear."""
    root = _redirect_root()
    try:
        body = _candidate_with_workflow()
        body["workflow"]["url"] = "https://example.com/secret/manifest.m3u8?token=SHHH"
        body["workflow"]["verify"]["raw_token"] = "SECRET-XYZ"
        _write_candidate(root, "planted.candidate.json", body)
        r = _client().get("/cockpit/api/review-candidates")
        wf = r.get_json()["candidates"][0]["workflow"]
        assert set(wf.keys()) == {
            "derived_steps", "trigger_candidate", "trigger_evidence", "source", "verify",
        }
        assert set(wf["verify"].keys()) == {
            "tier", "checks", "warnings", "gap_count", "action_count",
        }
        blob = json.dumps(r.get_json())
        assert "SHHH" not in blob
        assert "SECRET-XYZ" not in blob
        assert "manifest.m3u8" not in blob
    finally:
        cc._ROOT = _ORIG_ROOT


def test_page_renders_workflow_markers() -> None:
    """The templatereview page JS must render the observed-workflow affordances
    (the renderer reads x.workflow). Assert the source markers are present."""
    page = console._PAGE if hasattr(console, "_PAGE") else None
    if page is None:
        # _PAGE is module-level in cockpit_console; fall back to the served page.
        r = _client().get("/cockpit")
        page = r.get_data(as_text=True)
    assert "const wf=x.workflow" in page
    assert "observed workflow" in page
    assert "NOT the runtime download trigger" in page
    assert "review provenance" in page


# ── Wave B1 sequencing panel (this cut) ──────────────────────────────────────
# The guided lifecycle stepper + the "test static / test live" buttons that wire
# the EXISTING POST /api/template/sandbox. Route-less: the only new backend
# surface is a flat ``sandbox_template`` forwarded from the same
# /api/review-candidates decorator. POSTURE: the flat shape is structural
# selectors only (the candidate was scrubbed at the normalize boundary); the
# forwarder names its keys and never copies an unexpected value-bearing field.


def test_sandbox_template_mapper_nested_to_flat() -> None:
    """The nested runtime-shape selectors collapse to the flat shape
    /api/template/sandbox consumes; a fallback CHAIN takes its first entry."""
    m = console._candidate_sandbox_template({
        "download": {"trigger": "button.play",
                      "row_selectors": ["a.dl-1080", "a.dl-720"]},
        "login": {"user_field": ["#u", "input[name=user]"],
                   "pass_field": "#p", "submit_btn": ["#go"]},
    })
    assert m == {
        "trigger_selector": "button.play",
        "dl_selector": "a.dl-1080",     # first of the row_selectors chain
        "user_field": "#u",             # first of the fallback chain
        "pass_field": "#p",
        "submit_btn": "#go",
        "dismiss_selectors": "",        # candidates carry none
    }


def test_sandbox_template_mapper_empty_and_garbage() -> None:
    """Missing / empty / non-string selectors degrade to "" — never raise."""
    empty = console._candidate_sandbox_template({})
    assert set(empty.values()) == {""}
    assert console._first_selector([None, "", "  x  "]) == "x"
    assert console._first_selector(None) == ""
    assert console._first_selector(123) == ""
    assert console._first_selector([]) == ""


def test_endpoint_forwards_sandbox_template_named_keys_only() -> None:
    """GET /api/review-candidates forwards a flat ``sandbox_template`` per
    candidate (named keys only). Positive control: a value-bearing selector
    sibling key must NOT cross the boundary."""
    root = _redirect_root()
    try:
        body = {
            "host": "x.com", "status": "review_ready", "warnings": [],
            "resolutions": ["selectors"],
            "selectors": {
                "download": {"trigger": "button.play", "row_selectors": ["a.dl"]},
                "login": {"user_field": ["#u"], "pass_field": ["#p"],
                           "submit_btn": ["#go"]},
                # planted leak — a value-bearing key the mapper must ignore
                "secret_token": "SHHH-LEAK",
            },
            "source": {"captured_at": "2026-06-14"}, "network_patterns": [],
        }
        _write_candidate(root, "x.com.candidate.json", body)
        r = _client().get("/cockpit/api/review-candidates")
        c = r.get_json()["candidates"][0]
        sb = c["sandbox_template"]
        assert set(sb.keys()) == {
            "trigger_selector", "dl_selector", "user_field", "pass_field",
            "submit_btn", "dismiss_selectors",
        }
        assert sb["trigger_selector"] == "button.play"
        assert sb["dl_selector"] == "a.dl"
        assert sb["user_field"] == "#u"
        assert "SHHH-LEAK" not in json.dumps(r.get_json())
    finally:
        cc._ROOT = _ORIG_ROOT


def test_page_renders_sequencing_panel_markers() -> None:
    """The templatereview page JS must carry the stepper + test-static/live
    wiring + the apiRoot helper + the no-download/no-enable posture line."""
    r = _client().get("/cockpit")
    page = r.get_data(as_text=True)
    for marker in (
        "function apiRoot(",                 # main-app route helper (CSRF-seeded)
        "async function runSandbox(",        # the test-static/live runner
        "/api/template/sandbox",             # the EXISTING route it calls
        "data-sbx-static", "data-sbx-live",  # the two buttons
        "sbx_s3_", "sbx_s4_",                # the ③/④ stepper pills it flips
        "window.__bd_b1_cands",              # candidate stash for the handler
        "test draft selectors against a live page",
        "never enables the draft",           # posture line
    ):
        assert marker in page, f"MISSING sequencing-panel marker: {marker}"

