"""RED-first repro for F-REC01-01.

build_template_from_wacz.main() derives its default output path from the
capture's `host` with no sanitization (`Path("templates/drafts") /
f"{host}.template-draft.json"`), so a capture whose host contains `../` escapes
the drafts dir. After the fix the host is slugified to a single safe segment.

Pristine RED: a traversing host writes a draft OUTSIDE templates/drafts.
"""
import importlib.util
import sys
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parent.parent / "tools" / "build_template_from_wacz.py"
    s = importlib.util.spec_from_file_location("_bt_rec01", str(p))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def test_traversing_host_stays_confined(tmp_path, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "build_template", lambda p: {
        "source": {"host": "../../pwned"}, "selectors": {}, "confidence": "x",
        "network_discovery": {}, "resolution_priority": []})
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "dummy.wacz"; d.write_bytes(b"x")
    monkeypatch.setattr(sys, "argv", ["prog", str(d)])
    m.main()
    escaped = [p.name for p in tmp_path.iterdir()
               if p.name.endswith(".template-draft.json")]
    assert not escaped, f"draft escaped templates/drafts to cwd: {escaped}"
