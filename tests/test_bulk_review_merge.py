#!/usr/bin/env python3
"""test_bulk_review_merge.py -- consensus merge / selector-stability / modal-row
recovery tests for tools/bulk_review_captures.py.

run_tests.py harness conventions: zero-arg test_* functions, plain asserts, no
pytest builtins. Import is layout-flexible so it runs both from the repo
(tests/) via run_tests.py and standalone during development.

RED-first map (fail on PRE-upgrade source, pass after):
  - test_selector_tier_*          : _selector_tier did not exist  -> RED
  - test_weak_selector_flagged    : selector_quality did not exist -> RED
  - test_modal_row_recovery       : modal_row_candidates absent     -> RED
  - test_consensus_majority_wins  : v1 was newest-wins, not votes   -> RED
Regression guards (also held on v1):
  - test_gap_fill_recovers_missing_selector
  - test_drift_conflict_flags_and_downgrades
"""
import importlib
import importlib.util
import sys
from pathlib import Path


def _load():
    # 1) normal repo import
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        return importlib.import_module("tools.bulk_review_captures")
    except Exception:  # noqa: BLE001
        pass
    # 2) load by file path (dev / standalone)
    for cand in (root / "tools" / "bulk_review_captures.py",
                 Path(__file__).resolve().parent / "bulk_review_captures.py"):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("bulk_review_captures",
                                                          cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("bulk_review_captures not found")


br = _load()


def _cap(file, when, selectors, warnings=None, **extra):
    """Build a synthetic normalize_draft-shaped candidate."""
    c = {
        "host": "auth.example.com",
        "status": "review_ready",
        "selectors": selectors,
        "warnings": warnings or [],
        "source": {"capture_file": file, "capture_sha256": file + "sha",
                   "captured_at": when},
        "review_notes": [],
    }
    c.update(extra)
    return c


def _entries(*caps):
    return [{"cand": c, "n": br._selector_count(c), "src": Path(c["source"]["capture_file"])}
            for c in caps]


# ---- selector stability heuristic ------------------------------------------

def test_selector_tier_strong():
    assert br._selector_tier("#player") == "strong"
    assert br._selector_tier("[data-test=dl]") == "strong"
    assert br._selector_tier("[aria-label='play']") == "strong"
    assert br._selector_tier("input[name=q]") == "strong"


def test_selector_tier_weak():
    assert br._selector_tier("div:nth-child(3)") == "weak"
    assert br._selector_tier("ul li:last-child a") == "weak"


def test_selector_tier_medium():
    assert br._selector_tier(".dl-button") == "medium"
    assert br._selector_tier("video") == "medium"


# ---- consensus: majority beats lone-newest ---------------------------------

def test_consensus_majority_wins():
    # newest carries the MINORITY value; v1 newest-wins would have picked it.
    new = _cap("c3.wacz", "2026-06-03T00:00:00+00:00",
               {"download": {"trigger": "a.dl"}})
    mid = _cap("c2.wacz", "2026-06-02T00:00:00+00:00",
               {"download": {"trigger": "a[download]"}})
    old = _cap("c1.wacz", "2026-06-01T00:00:00+00:00",
               {"download": {"trigger": "a[download]"}})
    _, merged = br._merge_host_candidates(_entries(new, mid, old), 30)
    assert merged["selectors"]["download"]["trigger"] == "a[download]"
    alts = merged["merge_alternatives"]
    assert any(a["kept"] == "a[download]" and a["kept_votes"] == 2 for a in alts)
    assert any(lv["value"] == "a.dl" and lv["votes"] == 1
               for a in alts for lv in a["alternatives"])


# ---- gap fill ---------------------------------------------------------------

def test_gap_fill_recovers_missing_selector():
    # newest lacks download.row; older sibling has it -> recovered.
    new = _cap("c2.wacz", "2026-06-02T00:00:00+00:00",
               {"player": {"container": "video"},
                "download": {"trigger": "a[download]"}})
    old = _cap("c1.wacz", "2026-06-01T00:00:00+00:00",
               {"player": {"container": "video"},
                "download": {"trigger": "a[download]", "row": "tr.item a"}})
    _, merged = br._merge_host_candidates(_entries(new, old), 30)
    assert merged["selectors"]["download"].get("row") == "tr.item a"
    assert merged["merge_stats"]["gaps_filled"] >= 1


# ---- drift conflict beyond window flags + downgrades ------------------------

def test_drift_conflict_flags_and_downgrades():
    new = _cap("c2.wacz", "2026-06-01T00:00:00+00:00",
               {"player": {"container": "#newplayer"}})
    old = _cap("c1.wacz", "2026-03-01T00:00:00+00:00",   # ~92 days earlier
               {"player": {"container": "#oldplayer"}})
    _, merged = br._merge_host_candidates(_entries(new, old), 30)
    assert merged["merge_stats"]["flagged"] >= 1
    assert merged["status"] == "draft_review_required"


def test_in_window_conflict_not_flagged():
    new = _cap("c2.wacz", "2026-06-10T00:00:00+00:00",
               {"player": {"container": "#a"}})
    old = _cap("c1.wacz", "2026-06-01T00:00:00+00:00",   # 9 days, within 30
               {"player": {"container": "#b"}})
    _, merged = br._merge_host_candidates(_entries(new, old), 30)
    assert merged["merge_stats"]["flagged"] == 0


# ---- weak selector flagged in quality + notes -------------------------------

def test_weak_selector_flagged():
    new = _cap("c2.wacz", "2026-06-02T00:00:00+00:00",
               {"download": {"trigger": "div:nth-child(20) > a"}})
    old = _cap("c1.wacz", "2026-06-01T00:00:00+00:00",
               {"download": {"trigger": "div:nth-child(20) > a"}})
    _, merged = br._merge_host_candidates(_entries(new, old), 30)
    q = merged["selector_quality"]
    assert "download.trigger" in q["weak"]
    assert merged["merge_stats"]["weak_selectors"] >= 1
    assert any("brittle" in n.lower() for n in merged["review_notes"])


# ---- modal-row recovery from build warnings ---------------------------------

def test_modal_row_recovery():
    warn = ("dropped row selector (not modal-scoped or unsafe): "
            "a.ct_dl_button[data-framerate]")
    new = _cap("c2.wacz", "2026-06-02T00:00:00+00:00",
               {"download": {"trigger": "a[download]"}}, warnings=[warn])
    old = _cap("c1.wacz", "2026-06-01T00:00:00+00:00",
               {"download": {"trigger": "a[download]"}})
    _, merged = br._merge_host_candidates(_entries(new, old), 30)
    rows = merged["modal_row_candidates"]
    assert any("a.ct_dl_button[data-framerate]" in r["selector"] for r in rows)
    assert merged["merge_stats"]["recovered_rows"] >= 1
    assert any("modal-row" in n.lower() for n in merged["review_notes"])


# ---- never auto-enables -----------------------------------------------------

def test_merge_never_enables():
    new = _cap("c2.wacz", "2026-06-02T00:00:00+00:00",
               {"download": {"trigger": "a[download]"}})
    old = _cap("c1.wacz", "2026-06-01T00:00:00+00:00",
               {"download": {"trigger": "a[download]"}})
    _, merged = br._merge_host_candidates(_entries(new, old), 30)
    assert merged.get("status") != "enabled"
    assert "enabled" not in merged or merged.get("enabled") in (None, False)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
