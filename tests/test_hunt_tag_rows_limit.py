import sqlite3

from flask import Flask

from bulk_downloader import app_tags, tags

BD_GATE_SCOPE = "module"


def test_tag_rows_rejects_negative_limit(tmp_path, monkeypatch):
    database = tmp_path / "tags.db"

    def db_conn():
        cx = sqlite3.connect(database)
        cx.row_factory = sqlite3.Row
        return cx

    monkeypatch.setattr(tags._db, "db_conn", db_conn)
    monkeypatch.setattr(tags, "_tags_table_ready", True)
    with db_conn() as cx:
        cx.execute("CREATE TABLE history (id INTEGER, site_id TEXT, url TEXT, filename TEXT, file_size INTEGER, status TEXT, ts REAL)")
        cx.execute("CREATE TABLE history_tags (history_id INTEGER, tag TEXT, ts_assigned REAL)")
        for row_id in range(1, 4):
            cx.execute("INSERT INTO history VALUES (?, 's', 'u', 'f', 1, 'done', 0)", (row_id,))
            cx.execute("INSERT INTO history_tags VALUES (?, 'test', 0)", (row_id,))

    app = Flask(__name__)
    app.register_blueprint(app_tags.tags_bp)
    client = app.test_client()
    assert len(client.get("/api/tags/rows/test?limit=1").json["rows"]) == 1
    result = client.get("/api/tags/rows/test?limit=-1")
    assert result.status_code == 400
    assert client.get("/api/tags/rows/test?limit=oops").status_code == 400
    assert client.get("/api/tags/rows/test?limit=0").status_code == 400
