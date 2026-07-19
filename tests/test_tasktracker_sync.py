"""Pin test for tools/tasktracker_sync.py — the xlsx<->md drift gate.

Zero-arg functions; repo root via __file__. Builds a tiny xlsx with openpyxl
(present in sandbox/venv); skips cleanly if openpyxl is unavailable.
"""
import importlib.util
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "tasktracker_sync", REPO / "tools" / "tasktracker_sync.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_xlsx(path, inc_ids, comp_ids):
    import openpyxl
    wb = openpyxl.Workbook()
    inc = wb.active
    inc.title = "Incomplete"
    inc.append(["ID", "Category"])
    for i in inc_ids:
        inc.append([i, "x"])
    comp = wb.create_sheet("Completed")
    comp.append(["ID", "Category"])
    for i in comp_ids:
        comp.append([i, "x"])
    wb.save(path)


def _md(inc_ids, comp_ids):
    lines = ["# tracker", "", "## Incomplete (running)", "",
             "| ID | Category |", "| --- | --- |"]
    lines += [f"| {i} | x |" for i in inc_ids]
    lines += ["", "## Completed (running)", "", "| ID | Category |", "| --- | --- |"]
    lines += [f"| {i} | x |" for i in comp_ids]
    return "\n".join(lines) + "\n"


def _setup(inc_x, comp_x, inc_m, comp_m):
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return None
    d = Path(tempfile.mkdtemp(prefix="bd_ttsync_"))
    _make_xlsx(d / "TASK_TRACKER.xlsx", inc_x, comp_x)
    (d / "TASK_TRACKER.md").write_text(_md(inc_m, comp_m))
    return d


def test_in_sync_returns_zero():
    d = _setup(["A", "B"], ["C"], ["A", "B"], ["C"])
    if d is None:
        return  # openpyxl absent — skip
    m = _load()
    assert m.check(str(d)) == 0


def test_md_missing_row_is_drift():
    # row B in xlsx but not md -> the exact GCW-3 class of drift
    d = _setup(["A", "B"], ["C"], ["A"], ["C"])
    if d is None:
        return
    m = _load()
    assert m.check(str(d)) == 1


def test_extra_md_row_is_drift():
    d = _setup(["A"], ["C"], ["A", "Z"], ["C"])
    if d is None:
        return
    m = _load()
    assert m.check(str(d)) == 1


def test_regen_makes_md_match_xlsx():
    # regen md from xlsx, then --check must report IN-SYNC (exit 0)
    d = _setup(["A", "B"], ["C"], ["WRONG"], ["DRIFT"])  # md starts mismatched
    if d is None:
        return
    m = _load()
    assert m.check(str(d)) == 1          # mismatched before
    assert m.regen(str(d)) == 0          # regenerate md from xlsx
    assert m.check(str(d)) == 0          # now in sync
