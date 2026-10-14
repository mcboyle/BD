"""qti-user-tpl (O1789 deep-queue-templates-io F1 F2 F3 F4 F11 F12 F13): the
user-template store must not lose, wipe or misreport templates, must not keep
secrets on disk, and the import preview must say what the import does.

Each test asserts the CORRECT behaviour and fails on the pre-fix tree with a
QTI_F<n>_* diagnostic. Disk handling mirrors test_user_templates.py: the store
path is rebound to a tempdir per test.
"""

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

import json
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def _store(subdir=None):
    from bulk_downloader import user_templates as ut
    with tempfile.TemporaryDirectory() as td:
        orig = ut.USER_TEMPLATES_FILE
        base = Path(td) / subdir if subdir else Path(td)
        ut.USER_TEMPLATES_FILE = base / "user_templates.json"
        try:
            yield ut
        finally:
            ut.USER_TEMPLATES_FILE = orig


def _learned():
    return {"download": {"row_selectors": ["a.btn[href]"], "url_attribute": "href"}}


def _tpl(tid, name=None):
    t = {"name": name or f"T {tid}", "description": "d", "patterns": ["qti\\.example"],
         "learned": _learned()}
    if tid is not None:
        t["id"] = tid
    return t


def _client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    return A.app.test_client()


def _disk(ut):
    p = ut.USER_TEMPLATES_FILE
    return json.loads(p.read_text(encoding="utf-8"))["templates"] if p.exists() else []


def test_f1_concurrent_saves_all_persist():
    with _store() as ut:
        n = 16
        gate = threading.Barrier(n)
        results = []

        def one(i):
            gate.wait()
            results.append(ut.save_user_template(
                name=f"Conc {i}", description="d", patterns=[], learned=_learned(),
                tid=f"user_conc_{i}"))

        threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert len(results) == n and all(ok for ok, _ in results), results
        try:
            on_disk = sorted(t["id"] for t in _disk(ut))
        except ValueError as exc:
            pytest.fail(f"QTI_F1_CONCURRENT_SAVE_LOST: {n} concurrent saves all returned "
                        f"ok and left an unparseable store: {exc}", pytrace=False)
        assert len(on_disk) == n, (
            f"QTI_F1_CONCURRENT_SAVE_LOST: {n} concurrent saves all returned ok "
            f"but {len(on_disk)} templates are on disk: {on_disk}")


def test_f1_concurrent_imports_and_deletes_all_land():
    with _store() as ut:
        for i in range(8):
            ut.save_user_template(name=f"Del {i}", description="d", patterns=[],
                                  learned=_learned(), tid=f"user_del_{i}")
        n = 16
        gate = threading.Barrier(n)

        def imp(i):
            gate.wait()
            ut.import_user_templates({"templates": [_tpl(f"user_imp_{i}")]}, merge=True)

        def dele(i):
            gate.wait()
            ut.delete_user_template(f"user_del_{i}")

        threads = ([threading.Thread(target=imp, args=(i,)) for i in range(8)]
                   + [threading.Thread(target=dele, args=(i,)) for i in range(8)])
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        try:
            ids = sorted(t["id"] for t in _disk(ut))
        except ValueError as exc:
            pytest.fail(f"QTI_F1_CONCURRENT_SAVE_LOST: concurrent imports/deletes left an "
                        f"unparseable store: {exc}", pytrace=False)
        want = sorted(f"user_imp_{i}" for i in range(8))
        assert ids == want, (
            f"QTI_F1_CONCURRENT_SAVE_LOST: 8 imports + 8 deletes in parallel left {ids}")


def test_f1_control_sequential_saves_persist():
    with _store() as ut:
        for i in range(16):
            ut.save_user_template(name=f"Seq {i}", description="d", patterns=[],
                                  learned=_learned(), tid=f"user_seq_{i}")
        assert len(_disk(ut)) == 16


def test_f2_damaged_store_is_not_wiped_by_a_save():
    with _store() as ut:
        ut.USER_TEMPLATES_FILE.write_text(json.dumps(
            {"version": 1, "templates": [_tpl(f"user_k{i}", f"Keep {i}") for i in range(3)]}),
            encoding="utf-8")
        damaged = ut.USER_TEMPLATES_FILE.read_bytes()[:-7]
        ut.USER_TEMPLATES_FILE.write_bytes(damaged)
        r = _client().post("/api/user_templates", json=_tpl(None, "New"))
        after = ut.USER_TEMPLATES_FILE.read_bytes()
        assert after == damaged, (
            f"QTI_F2_DAMAGED_STORE_WIPED: a save over an unparseable store answered "
            f"{r.status_code} {r.get_json()} and rewrote it ({len(damaged)} -> "
            f"{len(after)} bytes): the 3 stored templates are gone")
        assert r.status_code >= 400 and r.get_json()["ok"] is False, r.get_json()


_DAMAGED = {
    "truncated": None,  # filled from a real 3-template store, tail cut
    "templates-not-a-list": b'{"version": 1, "templates": "x"}',
    "top-level-list": b"[1, 2]",
}


@pytest.mark.parametrize("shape", sorted(_DAMAGED))
@pytest.mark.parametrize("op", ["create", "update", "delete", "import-merge"])
def test_f2_every_writer_refuses_a_damaged_store(shape, op):
    with _store() as ut:
        good = json.dumps({"version": 1, "templates": [_tpl(f"user_k{i}", f"Keep {i}") for i in range(3)]})
        damaged = _DAMAGED[shape] or good.encode()[:-7]
        ut.USER_TEMPLATES_FILE.write_bytes(damaged)
        c = _client()
        if op == "create":
            r = c.post("/api/user_templates", json=_tpl(None, "New"))
        elif op == "update":
            r = c.post("/api/user_templates", json=_tpl("user_k0", "Changed"))
        elif op == "delete":
            r = c.delete("/api/user_templates/user_k0")
        else:
            r = c.post("/api/user_templates/import?merge=1", json={"templates": [_tpl("user_new")]})
        after = ut.USER_TEMPLATES_FILE.read_bytes()
        assert after == damaged and r.status_code == 503, (
            f"QTI_F2_DAMAGED_STORE_WIPED: {op} over a {shape} store answered {r.status_code} "
            f"and left {len(after)} of {len(damaged)} bytes")


def test_f3_store_write_failure_is_not_reported_as_success():
    # The store directory does not exist, so every write fails (root-proof
    # stand-in for a read-only / full disk).
    with _store(subdir="absent-dir") as ut:
        c = _client()
        r1 = c.post("/api/user_templates", json=_tpl(None, "Lost"))
        r2 = c.post("/api/user_templates/import?merge=1",
                    json={"templates": [_tpl("user_imp")]})
        seen = [(r1.status_code, r1.get_json()), (r2.status_code, r2.get_json())]
        assert not ut.USER_TEMPLATES_FILE.exists()
        assert r1.status_code >= 500 and r1.get_json()["ok"] is False, (
            f"QTI_F3_WRITE_FAILURE_REPORTED_OK: create/import with an unwritable "
            f"store answered {seen}")
        assert r2.status_code >= 500 and r2.get_json()["ok"] is False, (
            f"QTI_F3_WRITE_FAILURE_REPORTED_OK: import with an unwritable store "
            f"answered {seen}")


def test_f3_delete_write_failure_is_not_reported_as_success(monkeypatch):
    with _store() as ut:
        ut.save_user_template(name="Keep", description="d", patterns=[],
                              learned=_learned(), tid="user_keep")
        before = ut.USER_TEMPLATES_FILE.read_bytes()

        def refuse(self, *a, **k):
            raise OSError(28, "No space left on device (qti test)")
        monkeypatch.setattr(Path, "replace", refuse)
        r = _client().delete("/api/user_templates/user_keep")
        monkeypatch.undo()
        assert ut.USER_TEMPLATES_FILE.read_bytes() == before
        left = sorted(p.name for p in ut.USER_TEMPLATES_FILE.parent.iterdir()
                      if p.name != ut.USER_TEMPLATES_FILE.name)
        assert left == [], f"QTI_F3_TEMP_LEFT_BEHIND: a failed write left {left}"
        assert r.status_code >= 500 and r.get_json()["ok"] is False, (
            f"QTI_F3_WRITE_FAILURE_REPORTED_OK: delete with a failing write "
            f"answered {r.status_code} {r.get_json()}")


@pytest.mark.parametrize("bad", ["mysite.com", {"mysite": 1}, 123])
def test_f4_non_list_patterns_are_refused(bad):
    with _store() as ut:
        r = _client().post("/api/user_templates", json=dict(_tpl(None, "P"), patterns=bad))
        assert r.status_code == 400 and not _disk(ut), (
            f"QTI_F4_PATTERNS_NOT_A_LIST_ACCEPTED: patterns={bad!r} answered "
            f"{r.status_code}; stored patterns "
            f"{[t.get('patterns') for t in _disk(ut)]}; suggest(unrelated)="
            f"{ut.suggest_for_url('https://unrelated.example.org/x')}")


def test_f4_control_list_patterns_saved_and_scoped():
    with _store() as ut:
        r = _client().post("/api/user_templates",
                           json=dict(_tpl(None, "P"), patterns=["mysite\\.com"]))
        assert r.status_code == 200
        assert ut.suggest_for_url("https://unrelated.example.org/x") == []
        assert ut.suggest_for_url("https://mysite.com/x") == [r.get_json()["template"]["id"]]


def test_f11_create_does_not_keep_secret_keys_on_disk():
    with _store() as ut:
        learned = dict(_learned(), login={"password": "QTI-F11-PW", "api_key": "QTI-F11-KEY",
                                          "user_field": "#u"})
        r = _client().post("/api/user_templates", json=dict(_tpl(None, "S"), learned=learned))
        assert r.status_code == 200, r.get_json()
        raw = ut.USER_TEMPLATES_FILE.read_text(encoding="utf-8")
        assert "#u" in raw and "a.btn[href]" in raw  # control: the template itself is stored
        leaked = [s for s in ("QTI-F11-PW", "QTI-F11-KEY") if s in raw]
        assert not leaked, f"QTI_F11_SECRET_ON_DISK: created template keeps {leaked} in {ut.USER_TEMPLATES_FILE.name}"
        echoed = [s for s in ("QTI-F11-PW", "QTI-F11-KEY") if s in json.dumps(r.get_json())]
        assert not echoed, f"QTI_F11_SECRET_ECHOED: create response carries {echoed}"


def test_f11_secret_already_on_disk_is_scrubbed_by_the_next_write():
    # A store written before the fix keeps credentials; any later save must not
    # carry them forward.
    with _store() as ut:
        legacy = dict(_tpl("user_legacy", "Legacy"),
                      learned=dict(_learned(), login={"password": "QTI-F11-OLD"}))
        ut.USER_TEMPLATES_FILE.write_text(json.dumps({"version": 1, "templates": [legacy]}),
                                          encoding="utf-8")
        r = _client().post("/api/user_templates", json=_tpl(None, "Other"))
        assert r.status_code == 200, r.get_json()
        raw = ut.USER_TEMPLATES_FILE.read_text(encoding="utf-8")
        assert "user_legacy" in raw  # control: the legacy template itself survives
        assert "QTI-F11-OLD" not in raw, (
            "QTI_F11_SECRET_ON_DISK: a save carried a stored template's credential forward")


def test_f12_duplicate_ids_in_one_import():
    with _store() as ut:
        c = _client()
        dup = {"templates": [_tpl("ut_dup", "A"), _tpl("ut_dup", "B")]}
        c.post("/api/user_templates/import?merge=0", json=dup)
        ids = [t["id"] for t in c.get("/api/user_templates").get_json()["templates"]]
        assert ids.count("ut_dup") == 1, f"QTI_F12_DUP_ID_STORED_TWICE: replace import stored ids {ids}"
        ut.USER_TEMPLATES_FILE.unlink()
        prev = c.post("/api/user_templates/import/preview?merge=1", json=dup).get_json()
        imp = c.post("/api/user_templates/import?merge=1", json=dup).get_json()
        assert prev["counts"]["new"] == imp["added"], (
            f"QTI_F12_PREVIEW_DISAGREES: preview counts {prev['counts']} vs import {imp}")


def test_f12_control_distinct_ids():
    with _store():
        c = _client()
        two = {"templates": [_tpl("ut_a"), _tpl("ut_b")]}
        prev = c.post("/api/user_templates/import/preview?merge=1", json=two).get_json()
        imp = c.post("/api/user_templates/import?merge=1", json=two).get_json()
        assert prev["counts"]["new"] == imp["added"] == 2


def test_f13_id_less_import_is_reported_as_it_lands():
    with _store():
        c = _client()
        idless = {"templates": [_tpl(None, "X"), _tpl(None, "Y"), _tpl("   ", "Blank")]}
        prev = c.post("/api/user_templates/import/preview?merge=1", json=idless).get_json()
        imp = c.post("/api/user_templates/import?merge=1", json=idless).get_json()
        listed = c.get("/api/user_templates").get_json()["templates"]
        assert prev["counts"]["new"] == imp["added"] == len(listed), (
            f"QTI_F13_IDLESS_IMPORT_MISREPORTED: preview new={prev['counts']['new']} "
            f"import={imp} but GET lists {len(listed)}")
        assert imp["added"] == 0 and len(imp["errors"]) == 3, (
            f"QTI_F13_IDLESS_IMPORT_MISREPORTED: missing/blank ids were not all refused: {imp}")
