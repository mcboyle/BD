"""O1789 queue-headers-redact r1 (R193-C, BD-SEC-2 r3 HDR-ROUTES probe).

Per-URL custom headers (Authorization, Cookie) were served raw on the queue
GETs (export / search / grouped), on jobs/detail and in a queue template's GET.
A client now sees the header NAMES with every value masked; the values stay on
the job (and in the DB) for its own request. A masked value handed back in
(queue import, template POST / PUT) is the value already held for that URL and
name, or no header at all: the mask is never stored as a value. In /api/backup
the app DB is a sensitive member (PM ANSWER-pm-B-backup-1112Z, option C):
encrypt_scope=sensitive encrypts it, a restore keeps the values.

Failure lines carry route names and counts only, never a header value.
"""
from __future__ import annotations

import io
import json
import re
import signal
import sqlite3
import zipfile
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

BD_GATE_SCOPE = "module"

CANARY = "qhrR1canary7f"
U1 = "https://h.example/v/1"
U2 = "https://h.example/v/2"
ENTRY1 = f"{U1}\tAuthorization=Bearer {CANARY}\tCookie=sess={CANARY}x"
NAMES = ["Authorization", "Cookie"]
MASK = "[REDACTED]"  # db.QUEUE_HEADER_MASK, a literal so BASE fails on behaviour


def _has(text, c=CANARY) -> bool:
    d = text
    for _ in range(14):
        if c in d:
            return True
        nd = unquote(d)
        if nd == d:
            break
        d = nd
    return c in d


def _get(client, url) -> str:
    signal.alarm(20)
    try:
        return client.get(url).get_data(as_text=True)
    finally:
        signal.alarm(0)


def _view(path, view, *args, method="POST", body=None):
    from bulk_downloader import app as A
    with A.app.test_request_context(path, method=method, json=body):
        out = view(*args)
    resp, code = out if isinstance(out, tuple) else (out, out.status_code)
    return resp.get_json(), code


@pytest.fixture
def site(fresh_app, monkeypatch):
    """A site whose one job carries canary header values, plus a jsonify spy on
    both route modules: every JSON body they build is scanned for the canary."""
    from bulk_downloader import app as A
    from bulk_downloader import app_queue_templates, app_sites_queue
    sid = fresh_app.post("/api/sites", json={"name": "qhr"}).get_json()["id"]
    A.runners[sid].load_urls([ENTRY1, U2])
    bodies = []

    def spy(orig, mod):
        def jsonify(*a, **k):
            bodies.append((mod, _has(json.dumps([a, k], default=str))))
            return orig(*a, **k)
        return jsonify

    for mod in (app_sites_queue, app_queue_templates):
        monkeypatch.setattr(mod, "jsonify", spy(mod.jsonify, mod.__name__))
    return fresh_app, sid, A.runners[sid], bodies


def _held_everywhere(sid, runner) -> bool:
    """The job and its DB row still carry the real values (the values the
    job's own request uses)."""
    from bulk_downloader import db
    job = runner.jobs[U1].get("custom_headers") or {}
    row = {r["url"]: r for r in db.queue_load(sid)}[U1]
    return (job.get("Authorization") == f"Bearer {CANARY}"
            and _has(row.get("custom_headers") or ""))


def test_hdr_routes_serve_header_names_never_values(site):
    from bulk_downloader import app as A
    from bulk_downloader import queue_templates as qt
    client, sid, runner, bodies = site
    assert _held_everywhere(sid, runner), "QHR_PROBE_BLIND: the job holds no canary header"
    # a part with no "=" is no header to load_urls, but it is shown masked too
    tid = qt.create(name="t", origin_site_id=sid,
                    urls=[ENTRY1, U2, f"https://h.example/v/9\tjunk{CANARY}"],
                    priority_map={ENTRY1: "high"}, force_set=[ENTRY1])
    served = {}
    for r in sorted((r for r in A.app.url_map.iter_rules() if "GET" in r.methods
                     and "queue" in r.rule and set(r.arguments) <= {"sid", "site_id"}),
                    key=lambda r: r.rule):
        served[r.rule] = _get(client, r.rule.replace("<sid>", sid).replace("<site_id>", sid))
    served["jobs/detail"] = _get(client, f"/api/sites/{sid}/jobs/detail?url={quote(U1, safe='')}")
    served["queue_templates/<tid>"] = _get(client, f"/api/queue_templates/{tid}")
    # denominator from the live url_map: R193 counted 18 queue GETs + the 2 above
    assert len(served) >= 20, f"QHR_DENOMINATOR {len(served)}"
    leaks = sorted(n for n, t in served.items() if _has(t))
    assert leaks == [], f"QHR_LEAK routes={leaks}"
    # the names still ride along, masked, on every route that served them
    for name in ("/api/sites/<sid>/queue/export", "/api/sites/<sid>/queue/search",
                 "/api/sites/<sid>/queue/grouped", "jobs/detail", "queue_templates/<tid>"):
        missing = [h for h in NAMES if h not in served[name]]
        shown = MASK in served[name]
        assert missing == [] and shown, f"QHR_NAMES_LOST {name} {missing}"
    spied = [m for m, hit in bodies if hit]
    assert len(bodies) >= 5 and spied == [], f"QHR_JSONIFY_CANARY bodies={len(bodies)} hits={spied}"
    # masking built copies: the job, its DB row and the template keep the values
    assert _held_everywhere(sid, runner), "QHR_LIVE_JOB_MASKED"
    t = qt.get(tid)
    kept = (t["urls"][0] == ENTRY1 and list(t["priority_map"]) == [ENTRY1]
            and t["force_set"] == [ENTRY1])
    assert kept, "QHR_TEMPLATE_STORE_MASKED"


def test_export_shape_names_masked_and_a_plain_row_unchanged(site):
    from bulk_downloader import db
    client, sid, _runner, _bodies = site
    rows = {r["url"]: r for r in client.get(f"/api/sites/{sid}/queue/export").get_json()["rows"]}
    masked = {h: MASK for h in NAMES}
    shape = rows[U1]["custom_headers"] == masked
    assert shape, "QHR_EXPORT_SHAPE"
    assert "custom_headers" not in rows[U2], "QHR_PLAIN_ROW_GAINED_HEADERS"
    found = client.get(f"/api/sites/{sid}/queue/search").get_json()["rows"]
    texts = {r["url"]: r.get("custom_headers") for r in found}
    shape = json.loads(texts[U1]) == masked
    assert shape, "QHR_SEARCH_SHAPE"
    assert texts[U2] is None, "QHR_SEARCH_PLAIN_ROW"
    same = getattr(db, "QUEUE_HEADER_MASK", MASK) == MASK
    assert same, "QHR_MASK_LITERAL_DRIFT"


def test_masked_export_imported_back_restores_or_drops_never_stores_the_mask(site):
    from bulk_downloader import app as A
    from bulk_downloader import app_sites_queue, db
    client, sid, runner, _bodies = site
    exported = client.get(f"/api/sites/{sid}/queue/export").get_json()["rows"]
    # same site, replace: the masked values are the ones this queue held
    body, code = _view(f"/api/sites/{sid}/queue/import", app_sites_queue.api_queue_import,
                       sid, body={"rows": exported, "mode": "replace"})
    assert code == 200 and body["added"] == 2 and body.get("headers_dropped") == 0, \
        f"QHR_IMPORT_REPLACE code={code} added={body.get('added')} dropped={body.get('headers_dropped')}"
    assert _held_everywhere(sid, runner), "QHR_REPLACE_LOST_VALUES"
    # another site holds no value: the masked headers are dropped, url form too
    other = client.post("/api/sites", json={"name": "qhr2"}).get_json()["id"]
    rows = exported + [{"url": f"https://h.example/v/3\tX-Token={MASK}\tReferer=r"}]
    body, code = _view(f"/api/sites/{other}/queue/import", app_sites_queue.api_queue_import,
                       other, body={"rows": rows})
    assert code == 200 and body["added"] == 3 and body.get("headers_dropped") == 3, \
        f"QHR_IMPORT_OTHER code={code} added={body.get('added')} dropped={body.get('headers_dropped')}"
    jobs = A.runners[other].jobs
    stored = {r["url"]: r.get("custom_headers") for r in db.queue_load(other)}
    none_kept = "custom_headers" not in jobs[U1] and stored[U1] is None
    assert none_kept, "QHR_MASK_IMPORTED_AS_VALUE"
    url_form = jobs["https://h.example/v/3"].get("custom_headers") == {"Referer": "r"}
    assert url_form, "QHR_URL_FORM_MASK_KEPT"
    mask_in_db = MASK in json.dumps(stored)
    assert not mask_in_db, "QHR_MASK_IN_DB"


def test_template_put_and_post_resolve_masked_values(site):
    from bulk_downloader import app_queue_templates as aqt
    from bulk_downloader import queue_templates as qt
    client, sid, _runner, _bodies = site
    tid = qt.create(name="t", origin_site_id=sid, urls=[ENTRY1, U2],
                    priority_map={ENTRY1: "high"}, force_set=[ENTRY1])
    shown = client.get(f"/api/queue_templates/{tid}").get_json()["template"]
    put = {"urls": shown["urls"], "priority_map": shown["priority_map"],
           "force_set": shown["force_set"], "note": "edited"}
    body, code = _view(f"/api/queue_templates/{tid}", aqt.api_queue_template_one,
                       tid, method="PUT", body=put)
    assert code == 200 and body["ok"], f"QHR_PUT code={code}"
    t = qt.get(tid)
    restored = (t["urls"] == [ENTRY1, U2] and list(t["priority_map"]) == [ENTRY1]
                and t["force_set"] == [ENTRY1] and t["note"] == "edited")
    assert restored, "QHR_PUT_STORED_MASK"
    # a new template holds nothing to resolve a mask with: the header is dropped
    post = {"name": "copy", "urls": shown["urls"] + [f"{U2}\tReferer=r"],
            "priority_map": shown["priority_map"], "force_set": shown["force_set"]}
    body, code = _view("/api/queue_templates", aqt.api_queue_templates, body=post)
    assert code == 200 and body["ok"], f"QHR_POST code={code}"
    t2 = qt.get(body["id"])
    mask_stored = MASK in json.dumps(t2)
    assert not mask_stored, "QHR_POST_STORED_MASK"
    entries = t2["urls"] == [U1, U2, f"{U2}\tReferer=r"]
    assert entries, "QHR_POST_ENTRIES"
    maps = list(t2["priority_map"]) == [U1] and t2["force_set"] == [U1]
    assert maps, "QHR_POST_MAPS"


def test_jsonify_guard_no_raw_header_text_reaches_a_json_body():
    """Static guard (ORDER: custom_headers values never reach jsonify): in the
    queue route module every custom_headers value written into a response is
    a masked one, and no queue DB row list is handed to jsonify as it is."""
    src = (Path(__file__).resolve().parents[1] / "bulk_downloader" / "app_sites_queue.py").read_text()
    writes = re.findall(r'\["custom_headers"\]\s*=\s*(.+)', src)
    assert writes and all("queue_headers_masked(" in w for w in writes), \
        f"QHR_GUARD_UNMASKED_WRITE {len(writes)}"
    raw_rows = re.findall(r'"rows":\s*(rows|v)\b\s*[,}]', src)
    assert raw_rows == [], f"QHR_GUARD_RAW_ROWS {raw_rows}"
    tsrc = (Path(__file__).resolve().parents[1] / "bulk_downloader" / "app_queue_templates.py").read_text()
    assert '"template": t}' not in tsrc and '"template": _masked(t)' in tsrc, "QHR_GUARD_TEMPLATE_RAW"


def _seen(raw: bytes) -> bytes:
    """The archive bytes plus every member inflated (a plain zip deflates
    the DB, so the raw bytes alone could hide a value in clear)."""
    chunks = [raw]
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            chunks += [z.read(n) for n in z.namelist()]
    except zipfile.BadZipFile:
        pass
    return b"".join(chunks)


def test_backup_db_is_a_sensitive_member_values_never_in_clear_with_a_passphrase(
        site, tmp_path, monkeypatch):
    import _sqlite3

    from bulk_downloader import backup as bk
    from bulk_downloader import db
    from bulk_downloader import queue_templates as qt
    _client, sid, runner, _bodies = site
    tcanary = "qhrR1tplcanary3e"
    qt.create(name="t", origin_site_id=sid, urls=[f"{U2}\tX-Api-Key={tcanary}"])
    assert _held_everywhere(sid, runner), "QHR_PROBE_BLIND: the job holds no canary header"
    src = tmp_path / "home"
    src.mkdir()
    live = sqlite3.connect(db._resolve_db_path())
    snap = sqlite3.connect(src / "downloader_history.db")
    live.backup(snap)
    live.close()
    snap.close()
    # the fresh_app DB fixture sends every connect to a "*downloader_history.db"
    # path to its shared in-memory DB; backup + restore must open the real files
    monkeypatch.setattr(sqlite3, "connect", _sqlite3.connect)
    found = {}
    outs = {}
    for scope, pw in (("none", None), ("sensitive", "qhr-pw"), ("all", "qhr-pw")):
        outs[scope] = tmp_path / f"bk-{scope}.out"
        r = bk.create_backup(outs[scope], base_dir=src, include_db=True,
                             passphrase=pw, encrypt_scope=scope if pw else "all")
        dropped = [x for x in r.get("skipped", []) if "downloader_history.db" in str(x)]
        assert r["ok"] and dropped == [], f"QHR_BACKUP_CREATE {scope} ok={r['ok']} dropped={len(dropped)}"
        seen = _seen(outs[scope].read_bytes())
        found[scope] = (CANARY.encode() in seen, tcanary.encode() in seen)
    # positive control: a plain backup carries both values in clear (by design)
    assert found["none"] == (True, True), f"QHR_BACKUP_PROBE_BLIND {found['none']}"
    assert found["sensitive"] == (False, False), f"QHR_BACKUP_SENSITIVE_CLEAR {found['sensitive']}"
    assert found["all"] == (False, False), f"QHR_BACKUP_ALL_CLEAR {found['all']}"
    with zipfile.ZipFile(outs["sensitive"]) as z:
        listed = json.loads(z.read("_manifest.json"))["encryption"]["members"]
    assert "downloader_history.db" in listed, "QHR_BACKUP_DB_NOT_LISTED"
    # a restore with the passphrase brings the values back
    dest = tmp_path / "restored"
    r = bk.restore_backup(outs["sensitive"], target_dir=dest, passphrase="qhr-pw")
    assert r["ok"], "QHR_BACKUP_RESTORE"
    # the DB checksum is over the plaintext, so the restored DB verifies
    verified = r.get("integrity_ok") is True and r.get("checksum_failures") == []
    assert verified, f"QHR_BACKUP_RESTORE_INTEGRITY {len(r.get('checksum_failures') or [])}"
    cx = sqlite3.connect(dest / "downloader_history.db")
    try:
        qrow = cx.execute("SELECT custom_headers FROM queue WHERE url=?", (U1,)).fetchone()
        trow = [t[0] for t in cx.execute("SELECT urls FROM queue_templates")]
    finally:
        cx.close()
    kept = bool(qrow) and _has(qrow[0] or "") and any(tcanary in (u or "") for u in trow)
    assert kept, "QHR_BACKUP_RESTORE_LOST_VALUES"


def test_backup_encrypted_db_over_the_restore_cap_is_left_out_and_named(
        site, tmp_path, monkeypatch):
    """Ciphertext is about 4/3 of the DB: a DB under the restore cap can encrypt
    to a member restore refuses. It is left out and named in skipped instead."""
    import _sqlite3

    from bulk_downloader import backup as bk
    from bulk_downloader import db
    src = tmp_path / "home"
    src.mkdir()
    live = sqlite3.connect(db._resolve_db_path())
    snap = sqlite3.connect(src / "downloader_history.db")
    live.backup(snap)
    live.close()
    snap.close()
    monkeypatch.setattr(sqlite3, "connect", _sqlite3.connect)
    plain = bk.create_backup(tmp_path / "p.zip", base_dir=src, include_db=True)
    with zipfile.ZipFile(tmp_path / "p.zip") as z:
        size = z.getinfo("downloader_history.db").file_size
    assert plain["ok"] and size > 0, "QHR_CAP_PROBE_BLIND"
    monkeypatch.setattr(bk, "_RESTORE_MEMBER_CAP", size + 1)
    r = bk.create_backup(tmp_path / "s.zip", base_dir=src, include_db=True,
                         passphrase="qhr-pw", encrypt_scope="sensitive")
    named = [x for x in r.get("skipped", []) if "downloader_history.db" in str(x)
             and "encrypted bytes" in str(x)]
    with zipfile.ZipFile(tmp_path / "s.zip") as z:
        left_out = "downloader_history.db" not in z.namelist()
        listed = json.loads(z.read("_manifest.json"))["encryption"]["members"]
    assert r["ok"] and len(named) == 1 and left_out, \
        f"QHR_CAP_OVER_WRITTEN named={len(named)} left_out={left_out}"
    assert "downloader_history.db" not in listed, "QHR_CAP_LISTED_ABSENT_MEMBER"
