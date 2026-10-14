"""Bug-hunt qti-csv-export (deep-queue-templates-io F8, F19; deep-spa-sites-queue-history F3).

GET /api/sites/<sid>/history/export?fmt=csv built each line with an f-string:
no quoting (a comma or newline in a filename / message split the record), no
formula neutralizing, and a silent 200-row cap. Any fmt other than csv
answered the txt URL list. eol_export's history.csv / provenance.csv wrote
cells raw, and exports.to_opml left "<" and ">" unescaped.

Scratch workdir + scratch DB per test (clean_workdir). No network.
"""
from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET

import pytest

from bulk_downloader import app as app_module
from bulk_downloader import app_sites_queue, db, eol_export, exports

BD_GATE_SCOPE = "module"

SID = "site1"
COLS = ["url", "status", "filename", "file_size", "message", "ts"]
FORMULA_LEADS = ["=", "+", "-", "@", "\t", "\r"]


class _Runner:
    def export_urls(self, status_filter=None):
        return "https://example.test/queued-1\nhttps://example.test/queued-2"


@pytest.fixture
def site(clean_workdir, monkeypatch):
    db.db_init()
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: {SID: _Runner()})
    return SID


def _log(url, filename="f.mp4", message="ok", status="done", size=10, sid=SID):
    db.db_log(sid, "Site One", url, status, filename, size, message)


def _export(query="fmt=csv", sid=SID):
    with app_module.app.test_request_context(
            f"/api/sites/{sid}/history/export?{query}"):
        out = app_sites_queue.api_export(sid)
    resp, code = out if isinstance(out, tuple) else (out, out.status_code)
    return resp, code


def _records(resp):
    return list(csv.reader(io.StringIO(resp.get_data(as_text=True))))


# ── F8(b) / D-F3(a): quoting ──────────────────────────────────────────

def test_comma_quote_and_newline_cells_stay_one_six_column_record(site):
    _log("https://example.test/plain")
    _log("https://example.test/v?a=1,2", filename='a,b "q".mp4',
         message="err, line1\nline2\r\nline3")
    resp, code = _export()
    assert code == 200 and resp.mimetype == "text/csv"
    recs = _records(resp)
    assert recs[0] == COLS
    assert [len(r) for r in recs] == [6, 6, 6]
    newest = recs[1]
    assert newest[0] == "https://example.test/v?a=1,2"
    assert newest[2] == 'a,b "q".mp4'
    assert newest[4] == "err, line1\nline2\r\nline3"
    assert recs[2][:5] == ["https://example.test/plain", "done", "f.mp4", "10", "ok"]


# ── F8(c): formula cells ──────────────────────────────────────────────

@pytest.mark.parametrize("lead", FORMULA_LEADS, ids=repr)
def test_formula_lead_cells_are_neutralized_in_history_export(site, lead):
    payload = lead + 'HYPERLINK("http://evil.test","x")'
    _log(payload, filename=payload, message=payload)
    recs = _records(_export()[0])
    assert len(recs) == 2 and len(recs[1]) == 6
    for col in (0, 2, 4):
        assert recs[1][col] == "'" + payload, COLS[col]
        assert recs[1][col][0] not in FORMULA_LEADS


def test_ordinary_cells_are_exported_unchanged(site):
    _log("https://example.test/a=b+c-d@e", filename="a-b=c.mp4", message="3 - 2 = 1", size=0)
    rec = _records(_export()[0])[1]
    assert rec[:5] == ["https://example.test/a=b+c-d@e", "done", "a-b=c.mp4", "0", "3 - 2 = 1"]
    assert rec[5]                                   # ts present


def test_csv_cells_helper():
    assert exports.csv_cells([None, 0, 12, "x", "=1", "-1", " =1"]) == \
        ["", "0", "12", "x", "'=1", "'-1", " =1"]


# ── F8(a) / D-F3(b): the row cap ──────────────────────────────────────

def test_more_than_200_rows_are_all_exported(site):
    for i in range(251):
        _log(f"https://example.test/{i}")
    resp, _ = _export()
    recs = _records(resp)
    assert len(recs) == 252
    assert resp.headers["X-BD-Export-Rows"] == "251"
    assert resp.headers["X-BD-Export-Truncated"] == "0"


def test_a_cut_export_says_so(site, monkeypatch):
    for i in range(7):
        _log(f"https://example.test/{i}")
    monkeypatch.setattr(app_sites_queue, "_HISTORY_EXPORT_MAX_ROWS", 5)
    resp, _ = _export()
    assert len(_records(resp)) == 6
    assert resp.headers["X-BD-Export-Rows"] == "5"
    assert resp.headers["X-BD-Export-Truncated"] == "1"
    monkeypatch.setattr(app_sites_queue, "_HISTORY_EXPORT_MAX_ROWS", 7)   # exactly at the cap
    resp, _ = _export()
    assert resp.headers["X-BD-Export-Rows"] == "7"
    assert resp.headers["X-BD-Export-Truncated"] == "0"


def test_status_filter_and_site_scope_still_apply(site):
    _log("https://example.test/ok")
    _log("https://example.test/bad", status="failed")
    _log("https://other.test/x", sid="other")
    recs = _records(_export("fmt=csv&status=failed")[0])
    assert [r[0] for r in recs[1:]] == ["https://example.test/bad"]
    assert len(_records(_export()[0])) == 3


# ── D-F3(c): fmt ──────────────────────────────────────────────────────

@pytest.mark.parametrize("fmt", ["json", "bogus", "CSV", "", "csv "])
def test_unknown_fmt_is_refused_not_answered_with_the_url_list(site, fmt):
    resp, code = _export(f"fmt={fmt}")
    assert code == 400
    assert "fmt" in resp.get_json()["error"]


@pytest.mark.parametrize("query", ["", "fmt=txt"])
def test_txt_url_list_is_unchanged(site, query):
    resp, code = _export(query)
    assert code == 200 and resp.mimetype == "text/plain"
    assert resp.get_data(as_text=True).splitlines() == [
        "https://example.test/queued-1", "https://example.test/queued-2"]


def test_unknown_site_is_still_404(site):
    assert _export(sid="nope")[1] == 404
    assert _export("fmt=bogus", sid="nope")[1] == 404


# ── F19: eol_export CSVs and OPML ─────────────────────────────────────

def test_eol_history_csv_cells_are_neutralized(site, tmp_path):
    _log("=cmd|' /C calc'!A0", filename="@SUM(1)", message="+1\nsecond line")
    _log("https://example.test/plain", filename="plain.mp4", message="fine")
    dest = tmp_path / "eol"
    out = eol_export.full_export(str(dest), s_cfg={})
    assert out["history_rows"] == 2, out
    recs = list(csv.reader(io.StringIO((dest / "history.csv").read_text())))
    assert [len(r) for r in recs] == [9, 9, 9]
    bad = recs[1]
    assert bad[3] == "'=cmd|' /C calc'!A0" and bad[5] == "'@SUM(1)" and bad[8] == "'+1\nsecond line"
    assert recs[2][3] == "https://example.test/plain" and recs[2][5] == "plain.mp4"


def test_eol_provenance_csv_cells_are_neutralized(site, tmp_path):
    from bulk_downloader import provenance
    provenance._ensure_table()
    with db.db_conn() as cx:
        names = [r[1] for r in cx.execute("PRAGMA table_info(provenance)")]
        row = {"ts": 1.0, "site_id": "=site", "source_url": "@url",
               "final_filename": "-name.mp4", "file_size": 5, "sha256": "ab"}
        needed = {r[1]: "" for r in cx.execute("PRAGMA table_info(provenance)")
                  if r[3] and r[4] is None and r[1] not in row and r[1] != "id"}
        row.update(needed)
        assert set(row) <= set(names)
        cx.execute(f"INSERT INTO provenance({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                   list(row.values()))
    dest = tmp_path / "eol"
    out = eol_export.full_export(str(dest), s_cfg={})
    assert out.get("provenance_rows") == 1, out
    recs = list(csv.reader(io.StringIO((dest / "provenance.csv").read_text())))
    assert recs[1][2:5] == ["'=site", "'@url", "'-name.mp4"]
    assert recs[1][5:] == ["5", "ab"]


def test_opml_with_markup_in_a_site_name_is_well_formed():
    name = 'A <b> & "C" > D'
    doc = exports.to_opml({"s<1>": {"name": name}, "plain": {"name": "Plain"}})
    root = ET.fromstring(doc)                       # raised ParseError before
    outlines = root.findall("./body/outline")
    assert [o.get("text") for o in outlines] == [name, "Plain"]
    assert outlines[0].get("xmlUrl") == "/api/recent/s<1>.rss"
