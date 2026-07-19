"""Phase 9.11 -- doc/runbook drift detection (RED-first)."""
from bulk_downloader import doc_drift

class _Fake:
    def __init__(self, ok=True, text="summary", error_kind=""):
        self.ok=ok; self.text=text; self.error=""; self.error_kind=error_kind
        self.provider="ollama"; self.model="bd-text-small"; self.latency_ms=1
def _ok(p,image_b64=None,max_tokens=1024,temperature=0.1,timeout=60.0): return _Fake()
def _offline(p,image_b64=None,max_tokens=1024,temperature=0.1,timeout=60.0): return _Fake(ok=False,error_kind="network")

_STATE={"live_version":"3.66.380"}

def test_stale_version_flagged():
    r=doc_drift.scan({"d.md":"The current version is 3.66.300 and stable."}, state=_STATE)
    kinds=[f["kind"] for f in r["findings"]]
    assert "stale_version" in kinds and r["ok"] is False

def test_missing_route_flagged():
    r=doc_drift.scan({"d.md":"Call /api/totally_made_up to do it."}, state=_STATE, routes={"/api/health","/api/runs"})
    assert any(f["kind"]=="missing_route" for f in r["findings"])

def test_known_route_not_flagged():
    r=doc_drift.scan({"d.md":"Use /api/health for status."}, state=_STATE, routes={"/api/health"})
    assert not any(f["kind"]=="missing_route" for f in r["findings"])

def test_missing_file_flagged():
    r=doc_drift.scan({"d.md":"see bulk_downloader/ghost.py"}, state=_STATE, files={"bulk_downloader/app.py"})
    assert any(f["kind"]=="missing_file" for f in r["findings"])

def test_stale_live_claim_enforced():
    r=doc_drift.scan({"d.md":"This is live at 3.66.300 right now."}, state=_STATE, health_version="3.66.380")
    assert any(f["kind"]=="stale_live_claim" for f in r["findings"])

def test_superseded_pack_flagged():
    r=doc_drift.scan({"d.md":"Bootstrap from pack v3_66_300."}, state=_STATE)
    assert any(f["kind"]=="superseded_pack" for f in r["findings"])

def test_clean_doc_ok():
    r=doc_drift.scan({"d.md":"This references 3.66.380 and /api/health."}, state=_STATE, routes={"/api/health"})
    assert r["ok"] is True

def test_summary_optional_and_advisory():
    findings=[{"doc":"d.md","kind":"stale_version","severity":"warning","detail":"x"}]
    out=doc_drift.summarize(findings, _call=_ok)
    assert out["advisory"] is True and out["summary"]=="summary"
    assert out["findings"]==findings

def test_findings_preserved_when_model_offline():
    findings=[{"doc":"d.md","kind":"missing_route","severity":"warning","detail":"x"}]
    out=doc_drift.summarize(findings, _call=_offline)
    assert out["findings"]==findings   # deterministic findings preserved
    assert out["summary"]==""          # model offline -> no summary, no crash
