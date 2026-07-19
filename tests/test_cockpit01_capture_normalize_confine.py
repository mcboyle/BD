"""RED-first repro for F-COCKPIT01-01.

The cockpit ``/api/captures/normalize`` handler confined the operator-supplied
``wacz`` name with a raw ``str(p).startswith(str(root))`` prefix check, which a
prefix-SIBLING directory defeats (``.../captures_evil`` starts with
``.../captures``), so a ``wacz`` of ``../captures_evil/x.wacz`` escapes the
captures root. After the fix the canonical ``cc.confine()`` (which uses
``Path.relative_to``) rejects the escape with a 400.

Pristine RED: the escaping wacz is NOT rejected at the confinement check.
"""
import importlib.util
from pathlib import Path


def _client(monkeypatch, captures_root):
    p = Path(__file__).resolve().parent.parent / "tools" / "cockpit_console.py"
    spec = importlib.util.spec_from_file_location("_cockpit_console_cn", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m.cc, "captures_root", lambda: captures_root)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(m.bp)
    return app.test_client()


def test_prefix_sibling_traversal_is_rejected(tmp_path, monkeypatch):
    captures = tmp_path / "captures"
    captures.mkdir()
    sibling = tmp_path / "captures_evil"       # prefix-sibling of "captures"
    sibling.mkdir()
    (sibling / "x.wacz").write_bytes(b"PK\x03\x04not-a-real-wacz")
    client = _client(monkeypatch, captures)
    r = client.post("/cockpit/api/captures/normalize",
                    json={"wacz": "../captures_evil/x.wacz"})
    assert r.status_code == 400, r.status_code
    assert b"captures root" in r.data, r.data
