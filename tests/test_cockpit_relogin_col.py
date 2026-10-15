"""cockpit-relogin-col: the cockpit's relogin history must read the REAL
cookie_relogin_log schema.

cookie_relogin._ensure_table() creates the column ``triggered_score``; the
cockpit selected ``cookie_score``, so every query raised "no such column" and
the guard turned it into ``[]`` -- login history was always empty on a real DB.
The fixture builds the table with the production _ensure_table(), not a copy,
so the test fails if the two drift apart again. The output key stays
``cookie_score`` (the cockpit's own contract).
"""
import sqlite3
import time

import pytest

from bulk_downloader import cookie_relogin, db
from tools import cockpit_templates as ct

BD_GATE_SCOPE = "module"

SITE = "relogin-col-site"


@pytest.fixture
def relogin_db(tmp_path, monkeypatch):
    path = str(tmp_path / "downloader_history.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    cookie_relogin._ensure_table()
    now = time.time()
    with sqlite3.connect(path) as cx:
        cols = [r[1] for r in cx.execute("PRAGMA table_info(cookie_relogin_log)")]
        assert "triggered_score" in cols and "cookie_score" not in cols, cols
        cx.executemany(
            "INSERT INTO cookie_relogin_log(ts, site_id, triggered_score, action, outcome)"
            " VALUES (?, ?, ?, ?, ?)",
            [(now - 60, SITE, 31, "relogin", "ok"),
             (now - 10, SITE, 12, "relogin", "failed: captcha"),
             (now - 5, "other-site", 50, "relogin", "ok")])
    return now


def test_relogin_history_returns_events_from_real_schema(relogin_db):
    ev = ct._relogin_history(SITE)
    assert len(ev) == 2, f"COCKPIT-RELOGIN-COL: expected 2 events on real schema, got {ev!r}"
    assert [e["cookie_score"] for e in ev] == [12, 31]  # newest first, key kept
    assert [e["outcome"] for e in ev] == ["failed: captcha", "ok"]
    assert all(e["action"] == "relogin" for e in ev)


def test_login_history_lists_site_from_real_schema(relogin_db, monkeypatch):
    monkeypatch.setattr(ct, "_load_sites_config", lambda: [{"id": SITE}])
    out = ct.login_history()
    assert out["history_available"] is True, f"COCKPIT-RELOGIN-COL: login_history empty {out!r}"
    (site,) = out["sites"]
    assert site["recent_total"] == 2 and site["recent_ok"] == 1
    assert site["last"]["cookie_score"] == 12
