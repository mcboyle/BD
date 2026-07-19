"""WACZ zip-bomb ceiling (rec #2).

`capture_ingest.load_capture` and the builder's `_load_capture` read a
`capture.json` member out of a `.wacz`; they now refuse a member whose DECLARED
uncompressed size exceeds a ceiling (`_MAX_CAPTURE_JSON_BYTES`, 256 MiB) before
decompressing it — a real capture.json is far below the ceiling, while a
decompression bomb must declare a large size to deliver one. Tested by lowering
the ceiling so a small fixture trips it; the default ceiling still loads it.
"""

import io
import json
import tempfile
import zipfile
import importlib.util
from pathlib import Path


def _make_wacz(payload_obj):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("archive/capture.json", json.dumps(payload_obj))
    d = tempfile.mkdtemp()
    p = Path(d) / "c.wacz"
    p.write_bytes(buf.getvalue())
    return p


def test_capture_ingest_refuses_oversize_then_loads_normal():
    import bulk_downloader.capture_ingest as ci
    p = _make_wacz({"network_log": [], "pad": "a" * 2000})
    old = ci._MAX_CAPTURE_JSON_BYTES
    ci._MAX_CAPTURE_JSON_BYTES = 200  # below the fixture's declared size
    try:
        raised = False
        try:
            ci.load_capture(str(p))
        except ValueError:
            raised = True
        assert raised, "oversize member must be skipped -> no capture -> ValueError"
    finally:
        ci._MAX_CAPTURE_JSON_BYTES = old
    # With the real (large) ceiling the same archive loads fine.
    got = ci.load_capture(str(p))
    assert "network_log" in got


def test_builder_load_capture_refuses_oversize_then_loads_normal():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "_btw_zipbomb", str(root / "tools" / "build_template_from_wacz.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    p = _make_wacz({"network_log": [], "url": "https://x/", "pad": "b" * 2000})
    old = m._MAX_CAPTURE_JSON_BYTES
    m._MAX_CAPTURE_JSON_BYTES = 200
    try:
        raised = False
        try:
            m._load_capture(p)
        except SystemExit:
            raised = True
        assert raised, "oversize capture.json must raise SystemExit before read"
    finally:
        m._MAX_CAPTURE_JSON_BYTES = old
    got = m._load_capture(p)
    assert got.get("url") == "https://x/"
