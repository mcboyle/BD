"""F0.3 scrub-on-capture hook — unit + end-to-end tests.

Two layers:
  * injected ``_runner`` for deterministic logic (manifest shape, status
    mapping, idempotence, disabled, tool-missing, fail-soft) without
    spawning the real tool;
  * one real end-to-end run of the shipped ``tools/capture_scrub.py`` over a
    tiny synthetic WACZ — the spec pin: emits a scrubbed twin + a manifest
    line, the raw is byte-for-byte untouched.

Harness notes: zero-arg test functions; tempfile.mkdtemp; module-global
swaps restored in try/finally.
"""

import io
import json
import tempfile
import zipfile
from pathlib import Path

from bulk_downloader import capture_scrub_hook as h


_SAMPLE_STDOUT = """capture_scrub — mode=safe token-min=32
input : /x/foo.wacz
redactions:
       6  signed_query_param
       2  email
       1  jwt

  Post-redaction verify: CLEAN.
"""


def _tmp():
    return Path(tempfile.mkdtemp(prefix="bd_scrub_"))


# ── stdout parsing ───────────────────────────────────────────────────────────
def test_parse_redactions_counts_only():
    r = h._parse_redactions(_SAMPLE_STDOUT)
    assert r == {"signed_query_param": 6, "email": 2, "jwt": 1}
    assert h._parse_redactions("no block here") == {}
    assert h._parse_redactions("") == {}


# ── happy path via injected runner ───────────────────────────────────────────
def test_scrub_writes_twin_and_manifest_raw_untouched():
    d = _tmp()
    raw = d / "cap.wacz"
    raw.write_bytes(b"RAWBYTES-DO-NOT-TOUCH")
    manifest = d / "scrub_manifest.jsonl"

    def fake_runner(tool, wacz_path, mode):
        out = h._expected_output(wacz_path)
        Path(out).write_bytes(b"SCRUBBED")
        return 0, _SAMPLE_STDOUT, out

    res = h.scrub_on_capture(str(raw), manifest_path=manifest, _runner=fake_runner)
    assert res["ran"] is True
    assert res["status"] == "clean"
    assert res["redaction_total"] == 9
    # twin produced with the policy-recognised .redacted.wacz name
    twin = d / "cap.redacted.wacz"
    assert twin.exists()
    # raw byte-for-byte untouched
    assert raw.read_bytes() == b"RAWBYTES-DO-NOT-TOUCH"
    # manifest line: counts only, version pin, no values
    line = json.loads(manifest.read_text().splitlines()[-1])
    assert line["status"] == "clean"
    assert line["source"] == "cap.wacz"
    assert line["output"] == "cap.redacted.wacz"
    assert line["redactions"] == {"signed_query_param": 6, "email": 2, "jwt": 1}
    assert line["redaction_total"] == 9
    assert line["scrub_tool"].endswith("capture_scrub.py")
    assert line["scrub_tool_sha"].startswith("sha256:")


def test_residual_exit2_records_no_output_raw_untouched():
    d = _tmp()
    raw = d / "cap.wacz"
    raw.write_bytes(b"RAW")
    manifest = d / "m.jsonl"

    def fake_runner(tool, wacz_path, mode):
        # exit 2 = residual after redaction -> tool writes NOTHING
        return 2, "capture_scrub — mode=safe\ninput : x\nredactions:\n       1  jwt\n", h._expected_output(wacz_path)

    res = h.scrub_on_capture(str(raw), manifest_path=manifest, _runner=fake_runner)
    assert res["ran"] is True
    assert res["status"] == "residual"
    assert res["output"] is None
    assert not (d / "cap.redacted.wacz").exists()
    assert raw.read_bytes() == b"RAW"
    line = json.loads(manifest.read_text().splitlines()[-1])
    assert line["status"] == "residual" and line["output"] is None


def test_runner_exception_is_failsoft():
    d = _tmp()
    raw = d / "cap.wacz"
    raw.write_bytes(b"RAW")
    manifest = d / "m.jsonl"

    def boom(tool, wacz_path, mode):
        raise RuntimeError("subprocess blew up")

    res = h.scrub_on_capture(str(raw), manifest_path=manifest, _runner=boom)
    assert res["ran"] is False
    assert res["reason"].startswith("error:")
    assert raw.read_bytes() == b"RAW"          # save never harmed
    line = json.loads(manifest.read_text().splitlines()[-1])
    assert line["status"] == "error"


def test_idempotent_skips_already_redacted():
    res = h.scrub_on_capture("/x/cap.redacted.wacz")
    assert res == {"ran": False, "reason": "not_a_raw_wacz"} or res["reason"] == "not_a_raw_wacz"
    res2 = h.scrub_on_capture("/x/notawacz.json")
    assert res2["reason"] == "not_a_raw_wacz"


def test_disabled_does_nothing():
    d = _tmp()
    raw = d / "cap.wacz"
    raw.write_bytes(b"RAW")
    manifest = d / "m.jsonl"
    called = []

    def fake_runner(tool, wacz_path, mode):
        called.append(1)
        return 0, "", h._expected_output(wacz_path)

    res = h.scrub_on_capture(str(raw), enabled=False, manifest_path=manifest, _runner=fake_runner)
    assert res["ran"] is False and res["reason"] == "disabled"
    assert called == []
    assert not manifest.exists()


def test_tool_missing_records_and_softfails():
    d = _tmp()
    raw = d / "cap.wacz"
    raw.write_bytes(b"RAW")
    manifest = d / "m.jsonl"
    orig = h._tool_path
    h._tool_path = lambda: None
    try:
        res = h.scrub_on_capture(str(raw), manifest_path=manifest)
        assert res["ran"] is False and res["reason"] == "tool_missing"
        line = json.loads(manifest.read_text().splitlines()[-1])
        assert line["status"] == "tool_missing"
    finally:
        h._tool_path = orig


# ── end-to-end with the REAL shipped tool ────────────────────────────────────
def _tiny_wacz(path: Path):
    """A minimal WACZ-shaped zip with one benign capture.json member."""
    cap = {
        "url": "https://example.com/watch/123",
        "title": "sample",
        "network_log": [{"url": "https://cdn.example.com/v/seg1.ts",
                         "method": "GET", "response_status": 200}],
        "dom_log": [{"type": "full_snapshot", "data": {"tag": "div"}}],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("archive/capture.json", json.dumps(cap))
        zf.writestr("pages/pages.jsonl",
                    json.dumps({"format": "json-pages-1.0", "id": "pages"}) + "\n")
    path.write_bytes(buf.getvalue())


def test_real_tool_emits_twin_and_manifest():
    d = _tmp()
    raw = d / "cap.wacz"
    _tiny_wacz(raw)
    raw_bytes = raw.read_bytes()
    manifest = d / "scrub_manifest.jsonl"

    res = h.scrub_on_capture(str(raw), manifest_path=manifest)  # REAL runner
    assert res["ran"] is True
    # raw untouched no matter the outcome
    assert raw.read_bytes() == raw_bytes
    # a manifest line was recorded
    lines = manifest.read_text().splitlines()
    assert len(lines) >= 1
    line = json.loads(lines[-1])
    assert line["source"] == "cap.wacz"
    assert line["scrub_tool"].endswith("capture_scrub.py")
    # benign synthetic capture -> verify clean -> twin written
    assert line["status"] == "clean"
    assert (d / "cap.redacted.wacz").exists()
    # and the twin is a valid zip (the tool re-packs it)
    assert zipfile.is_zipfile(d / "cap.redacted.wacz")
