"""RED-first repro for F-SWEEP-N4.

``exports.to_csv`` writes site-controlled cells (site_name, url, filename,
message) verbatim via ``csv.writer(QUOTE_MINIMAL)``. A scraped value beginning
with a spreadsheet formula/command trigger (``= + - @`` or a leading TAB/CR)
therefore lands unmodified and executes when the operator opens the export in
Excel or Google Sheets (CSV formula injection). After the fix such a cell is
neutralized with a leading single quote (which the spreadsheet strips on
display), so it can never be interpreted as a formula.

Pristine-source RED: the injected cells below are emitted un-neutralized, so the
``not injected`` assertion fails until to_csv neutralizes them.
"""
import csv

from bulk_downloader import exports


def test_formula_cells_are_neutralized(monkeypatch):
    payload_name = '=HYPERLINK("http://evil.example/x","click")'
    payload_msg = "@SUM(1+1)"
    rows = [{"id": 1, "site_id": 1, "site_name": payload_name,
             "url": "http://x/y", "filename": "f.mp4", "file_size": 0,
             "ts": "", "status": "ok", "message": payload_msg}]
    monkeypatch.setattr(exports, "_rows_from_filter", lambda _f=None: rows)

    out = exports.to_csv().decode("utf-8")
    lines = out.splitlines()
    assert len(lines) >= 2, out
    fields = next(csv.reader([lines[1]]))

    injected = [f for f in fields if f[:1] in ("=", "+", "-", "@", "\t", "\r")]
    assert not injected, f"un-neutralized formula cell(s): {injected}"

    # the site values are preserved, only prefixed with a neutralizing quote
    assert ("'" + payload_name) in fields, fields
    assert ("'" + payload_msg) in fields, fields

    # a benign cell (a URL) is untouched
    assert "http://x/y" in fields, fields
