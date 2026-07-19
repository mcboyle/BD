"""A6-1 download.trigger + A6-2 download.row_selectors DOM derivations.

Hermetic oracle over the synthetic download-modal capture (the same substrate the
builder gap report uses). Locks: the trigger is the modal-OPEN element (outside
the dialog), not the a[download] row link; row selectors expand to class-stable
families (href-download anchors + resolution buttons) under EACH modal scope.
All guard-free (build_template_from_wacz / template_normalize); extraction_core
untouched.
"""
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import builder_gap_report as GR   # noqa: E402
import build_template_from_wacz as B   # noqa: E402
from bulk_downloader import template_normalize as TN   # noqa: E402
from bulk_downloader.wacz_export import write_wacz   # noqa: E402


def _cand():
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "s.wacz"
        write_wacz(GR._synthetic_capture(), w)
        return TN.normalize_draft(B.build_template(w))


def test_trigger_is_modal_opener_not_row_link():
    dl = (_cand().get("selectors") or {}).get("download") or {}
    trig = str(dl.get("trigger") or "")
    # an element selector for the opener (data-tooltip download affordance), NOT
    # the a[download] row link or a text= guess
    assert "download" in trig.lower()
    assert trig != "a[download]"
    assert not trig.startswith("text=")
    assert GR._trigger_kind(trig) == "selector"


def test_trigger_derivation_unit_prefers_attr_over_class():
    # opener carries BOTH a download class and a data-tooltip -> attr wins
    btn = {"id": 1, "type": 2, "tagName": "button",
           "attributes": {"class": "download-open", "data-tooltip": "Download Full Movie"},
           "childNodes": []}
    root = {"id": 2, "type": 2, "tagName": "div", "attributes": {}, "childNodes": [btn]}
    dom_log = [{"type": "full_snapshot", "data": {"node": root}}]
    assert B._derive_download_trigger(dom_log) == 'button[data-tooltip*="download" i]'


def test_opener_inside_modal_is_ignored():
    # a download button INSIDE the dialog is a row action, not the opener
    inner = {"id": 1, "type": 2, "tagName": "button",
             "attributes": {"class": "download-row"}, "childNodes": []}
    modal = {"id": 2, "type": 2, "tagName": "div",
             "attributes": {"role": "dialog"}, "childNodes": [inner]}
    root = {"id": 3, "type": 2, "tagName": "div", "attributes": {}, "childNodes": [modal]}
    dom_log = [{"type": "full_snapshot", "data": {"node": root}}]
    assert B._derive_download_trigger(dom_log) is None


def test_row_selectors_class_stable_families_under_each_scope():
    dl = (_cand().get("selectors") or {}).get("download") or {}
    rows = dl.get("row_selectors") or []
    # both modal scopes present
    assert any(r.startswith('[role="dialog"] ') for r in rows)
    assert any(r.startswith(".ant-modal ") for r in rows)
    # class-stable families derived: href-download anchors + resolution buttons
    assert any('a[href*="download-resolution" i]' in r for r in rows)
    assert any('button:has-text("1080")' in r for r in rows)
    assert any('button:has-text("2160")' in r for r in rows)
    # every emitted shape is modal-scoped (safety: never page-wide)
    assert all(r.startswith(('[role="dialog"]', ".ant-modal")) for r in rows)


def test_gap_report_targets_closed_on_synthetic():
    rep = GR.report()
    # synthetic builder profile closes all four A6 targets against every gold
    for row in rep["rows"]:
        assert row["targets"] == [], (row["gold"], row["targets"])
