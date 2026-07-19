"""RED-first repro for F-TOOLS_OTHER02-01.

bulk_review_captures.main() writes one candidate per host as
`args.outdir / f"{host}.candidate.json"` with no sanitization of the
capture-derived host, so a host containing `../` escapes the output dir. After
the fix the host is slugified before the join.

Pristine RED: a traversing host writes a candidate OUTSIDE args.outdir.
"""
import importlib.util
import sys
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parent.parent / "tools" / "bulk_review_captures.py"
    s = importlib.util.spec_from_file_location("_brc_to02", str(p))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def test_traversing_host_stays_confined(tmp_path, monkeypatch):
    _load()  # ensure module parses
    import tools.build_template_from_wacz as wb
    import bulk_downloader.template_normalize as tn
    monkeypatch.setattr(wb, "build_template", lambda w: {"source": {"host": "../evil"}})
    monkeypatch.setattr(tn, "normalize_draft",
                        lambda d: {"host": "../evil", "selectors": {"a": ["#x"]}, "status": "ok"})
    root = tmp_path / "root"; root.mkdir(); (root / "c.wacz").write_bytes(b"x")
    outdir = tmp_path / "out"
    m = _load()
    monkeypatch.setattr(sys, "argv",
                        ["prog", "--root", str(root), "--outdir", str(outdir), "--write"])
    m.main()
    od = outdir.resolve()
    escaped = [p for p in tmp_path.rglob("*.candidate.json")
               if od not in p.resolve().parents]
    assert not escaped, f"candidate escaped outdir: {escaped}"
