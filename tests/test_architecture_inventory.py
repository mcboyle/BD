"""architecture_inventory (A9) — characterization test.

Confirms the generator re-derives from the live graph + LOC (not stale copy):
the largest module is app.py, the top coupling hotspot is db.py, the doc carries
its four sections, and orphan-candidate triage excludes modules that do have
importers. Read-only; runs the generator's build() against the real tree.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import architecture_inventory as AI


def test_build_rederives_known_leaders():
    d = AI.build(str(_ROOT))
    assert d["edge_count"] and d["module_count"] > 100
    # largest module is app.py; top coupling hotspot is db.py (load-bearing facts)
    assert d["largest"][0][0] == "bulk_downloader/app.py"
    assert d["hotspots"][0][0] == "bulk_downloader/db.py"
    assert d["hotspots"][0][1] >= 30


def test_orphan_triage_excludes_imported_modules():
    d = AI.build(str(_ROOT))
    orphans = set(d["orphan_candidates"])
    # app.py / runner.py have internal importers -> not orphan candidates
    assert "bulk_downloader/app.py" not in orphans
    assert "bulk_downloader/runner.py" not in orphans


def test_render_has_all_sections():
    d = AI.build(str(_ROOT))
    md = AI.render_md(d)
    for h in ("# Architecture inventory", "## Largest modules",
              "## Coupling hotspots", "## Orphan candidates", "## Blueprints"):
        assert h in md, h
    # never-blind-delete caveat must be present on the orphan section
    assert "never blind-delete" in md
