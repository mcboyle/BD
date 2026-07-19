"""Storage-capture wiring — tests.

Verifies localStorage/sessionStorage snapshots record key presence + structure
with VALUES redacted (no raw value / token / secret persisted), that the existing
delta model is preserved, and that the snapshot survives export / WACZ and is
tolerated by downstream consumers. The live `page.evaluate` wiring in the
orchestrator is stash-only (a real browser); these tests cover the sink-side
redaction guarantee the live wiring relies on, plus serialization/export.
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

from bulk_downloader.dom_capture import DomCapture
from bulk_downloader.capture_redact import PLACEHOLDER
from bulk_downloader.wacz_export import write_wacz, verify_wacz_bytes

_TOKENY = {"theme": "dark", "auth_token": "JWT.HEADER.SIGNATURE", "uid": "u-123"}


# ── localStorage / sessionStorage snapshots ───────────────────────
def test_localstorage_snapshot_redacted():
    c = DomCapture(redact=True)
    c.snapshot_storage(local=dict(_TOKENY), session={})
    ls = c.storage_snapshot["local_storage"]
    assert set(ls.keys()) == {"theme", "auth_token", "uid"}      # key presence
    assert all(v == PLACEHOLDER for v in ls.values())            # redacted values
    assert c.storage_snapshot["session_storage"] == {}


def test_sessionstorage_snapshot_redacted():
    c = DomCapture(redact=True)
    c.snapshot_storage(local={}, session=dict(_TOKENY))
    ss = c.storage_snapshot["session_storage"]
    assert set(ss.keys()) == {"theme", "auth_token", "uid"}
    assert all(v == PLACEHOLDER for v in ss.values())


def test_key_presence_and_structure_preserved():
    c = DomCapture(redact=True)
    c.snapshot_storage(local={"a": "1", "b": "2"}, session={"s": "x"})
    snap = c.storage_snapshot
    assert list(snap["local_storage"].keys()) == ["a", "b"]
    assert list(snap["session_storage"].keys()) == ["s"]
    assert "at" in snap  # structural timestamp


def test_empty_storage_structure():
    c = DomCapture(redact=True)
    c.snapshot_storage(local={}, session={})
    assert c.storage_snapshot["local_storage"] == {}
    assert c.storage_snapshot["session_storage"] == {}


def test_snapshot_raw_only_under_no_redact():
    # dev/bd_dev_inspect path keeps raw, consistent with the rest of the capture
    c = DomCapture(redact=False)
    c.snapshot_storage(local={"theme": "dark"}, session={})
    assert c.storage_snapshot["local_storage"] == {"theme": "dark"}


# ── redaction validation: no raw value/token/secret on disk ───────
def test_no_raw_values_or_tokens_persisted():
    c = DomCapture(url="https://app.reptyle.com", redact=True)
    c.snapshot_storage(local=dict(_TOKENY), session={"sid": "SESSIONSECRET"})
    blob = json.dumps(c.to_capture_dict())
    for secret in ("JWT.HEADER.SIGNATURE", "SESSIONSECRET", "u-123", "dark"):
        assert secret not in blob, f"raw value leaked into capture dict: {secret}"
    # placeholder present where values were
    assert PLACEHOLDER in blob


# ── existing delta model preserved ────────────────────────────────
def test_delta_model_still_redacts():
    c = DomCapture(redact=True)
    c.record_storage_delta(area="local", key="auth_token", new_value="JWT.SECRET")
    assert c.storage_deltas[0]["new_value"] == PLACEHOLDER
    assert c.storage_deltas[0]["key"] == "auth_token"  # key kept


# ── export / WACZ compatibility ───────────────────────────────────
def _capture_with_storage():
    c = DomCapture(url="https://app.reptyle.com", redact=True)
    c.set_page_context(title="Reptyle")
    c.snapshot_storage(local=dict(_TOKENY), session={"sid": "SESSIONSECRET"})
    return c.to_capture_dict()


def test_export_and_wacz_compat():
    capd = _capture_with_storage()
    assert "storage_snapshot" in capd  # serialized by to_capture_dict
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "cap.wacz")
        write_wacz(capd, out)
        res = verify_wacz_bytes(Path(out).read_bytes())
        assert res.get("ok") is True, res
        with zipfile.ZipFile(out) as z:
            name = next(n for n in z.namelist() if n.endswith("capture.json"))
            raw = z.read(name)
        cap2 = json.loads(raw)
        snap = cap2.get("storage_snapshot") or {}
        assert set(snap.get("local_storage", {}).keys()) == {"theme", "auth_token", "uid"}
        # no raw secret survived into the archive
        assert b"JWT.HEADER.SIGNATURE" not in raw


# ── downstream consumer compatibility ─────────────────────────────
def test_template_generation_tolerates_storage_snapshot():
    import build_template_from_wacz as B
    capd = _capture_with_storage()
    capd["dom_log"] = [{
        "dom_seq": 0, "timestamp": 1, "iso": "x", "type": "full_snapshot",
        "source": -1, "frame_path": ["main"], "data": {},
        "html": "<html><body><a download href='/x.mp4'>Download</a></body></html>",
    }]
    with tempfile.TemporaryDirectory() as d:
        out = str(Path(d) / "cap.wacz")
        write_wacz(capd, out)
        draft = B.build_template(Path(out))  # must not choke on storage_snapshot
    assert isinstance(draft, dict)
