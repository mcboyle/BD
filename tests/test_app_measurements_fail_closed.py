"""Seven application measurements must not collapse unavailable into permission.

Each defect arm proves the unavailable result is reachable and that its caller
does not take the permissive branch.  Each paired control proves a measured,
healthy result still passes, so fail-closed does not become always-closed.
"""
from __future__ import annotations

import hashlib
import logging
import sys
import threading
import types

from flask import Flask


BD_GATE_SCOPE = "module"


class _BrokenConnection:
    def __enter__(self):
        raise OSError("measurement store unreadable")

    def __exit__(self, *_args):
        return False


def _broken_db_conn():
    return _BrokenConnection()


class _EmptyBlocklistConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return []


def _queue_probe(monkeypatch):
    from bulk_downloader import runner_queue as rq

    monkeypatch.setattr(rq, "queue_bulk_upsert", lambda *_a, **_k: None)

    class Probe(rq.QueueMixin):
        def __init__(self):
            self.config = {"download_dir": ""}
            self.jobs = {}
            self.urls = []
            self.site_id = "measurement-probe"
            self._lock = threading.RLock()
            self.log = logging.getLogger("measurement-probe")
            self.events = []

        def log_event(self, kind, message=None, **kwargs):
            self.events.append((kind, message, kwargs))

    return Probe()


def _extension_client(monkeypatch):
    from bulk_downloader import app_extension

    monkeypatch.setattr(app_extension, "_app_runners", lambda: {})
    monkeypatch.setattr(app_extension, "_app_s_cfg", lambda: {})
    monkeypatch.setattr(app_extension, "db_conn", _broken_db_conn)
    app = Flask("content-rights-measurement-probe")
    app.register_blueprint(app_extension.extension_bp)
    return app.test_client()


def test_f36_unreadable_rights_list_is_unknown_and_refuses_enqueue(monkeypatch):
    from bulk_downloader import content_rights as cr

    monkeypatch.setattr(cr, "_ensure_tables", lambda: None)
    monkeypatch.setattr(cr._db, "db_conn", _broken_db_conn)
    result = cr.url_is_blocked("https://prohibited.example/video")
    assert result["unknown"] is True
    assert result["blocked"] is None
    assert "unreadable" in result["error"]

    probe = _queue_probe(monkeypatch)
    added, refused, skipped = probe.load_urls(
        ["https://prohibited.example/video"]
    )
    assert (added, refused, skipped) == (0, 1, 0)
    assert probe.jobs == {}, "an unknown blocklist verdict must not enqueue"
    assert [event[0] for event in probe.events].count(
        "content_rights_unknown"
    ) == 1
    lookup = _extension_client(monkeypatch).get(
        "/api/extension/lookup_url",
        query_string={"url": "https://prohibited.example/video"},
    ).get_json()
    assert lookup["blocked"] is None
    assert lookup["blocklist_status"] == "unknown"
    assert "unreadable" in lookup["blocklist_error"]


def test_f36_measured_no_match_still_enqueues(monkeypatch):
    from bulk_downloader import content_rights as cr

    monkeypatch.setattr(cr, "_ensure_tables", lambda: None)
    monkeypatch.setattr(cr._db, "db_conn", _EmptyBlocklistConnection)
    assert cr.url_is_blocked("https://allowed.example/video") is None

    probe = _queue_probe(monkeypatch)
    added, refused, skipped = probe.load_urls(["https://allowed.example/video"])
    assert (added, refused, skipped) == (1, 0, 0)
    assert "https://allowed.example/video" in probe.jobs
    assert not [e for e in probe.events if e[0] == "content_rights_unknown"]
    lookup = _extension_client(monkeypatch).get(
        "/api/extension/lookup_url",
        query_string={"url": "https://allowed.example/video"},
    ).get_json()
    assert lookup.get("blocked") is not True


class _HashProbe:
    from bulk_downloader.runner_integrity import IntegrityMixin

    _verify_hash_or_quarantine = IntegrityMixin._verify_hash_or_quarantine

    def __init__(self):
        self.site_id = "hash-probe"
        self.config = {"name": "Hash Probe"}
        self.updates = []
        self.events = []

    def _update_job(self, *args, **kwargs):
        self.updates.append((args, kwargs))

    def log_event(self, *args, **kwargs):
        self.events.append((args, kwargs))


def test_f37_hash_io_or_algorithm_failure_is_not_verified(monkeypatch, tmp_path):
    from bulk_downloader import runner_integrity as ri

    db_rows = []
    monkeypatch.setattr(ri, "db_log", lambda *args: db_rows.append(args))
    final = tmp_path / "completed.mp4"
    final.write_bytes(b"structurally-valid-media")
    probe = _HashProbe()

    result = probe._verify_hash_or_quarantine(
        "https://example.test/v", "definitely-not-a-hash", "abcd",
        final, final.name, final.stat().st_size,
    )

    assert result is False, "uncomputed digest must not equal verified digest"
    assert not final.exists()
    assert (tmp_path / "_failed" / final.name).exists()
    assert len(probe.updates) == 1 and probe.updates[0][0][1] == "failed"
    assert "unavailable" in probe.updates[0][0][2].lower()
    assert len(db_rows) == 1 and "unavailable" in db_rows[0][-1]


def test_f37_matching_digest_still_passes(monkeypatch, tmp_path):
    from bulk_downloader import runner_integrity as ri

    monkeypatch.setattr(ri, "db_log", lambda *_args: None)
    final = tmp_path / "completed.mp4"
    final.write_bytes(b"verified bytes")
    expected = hashlib.sha256(final.read_bytes()).hexdigest()
    probe = _HashProbe()

    assert probe._verify_hash_or_quarantine(
        "https://example.test/v", "sha256", expected,
        final, final.name, final.stat().st_size,
    ) is True
    assert final.exists()
    assert probe.updates == []
    assert len(probe.events) == 1


def _admission_probe(config):
    from bulk_downloader.runner import SiteRunner

    probe = SiteRunner.__new__(SiteRunner)
    probe.site_id = "admission-probe"
    probe.config = config
    probe._state = "running"
    return probe


def test_f38_unreadable_daily_counters_are_unknown_and_hold_admission(monkeypatch):
    from bulk_downloader import daily_budget as db

    monkeypatch.setattr(db, "_ensure_table", lambda: None)
    monkeypatch.setattr(db._db, "db_conn", _broken_db_conn)
    monkeypatch.setattr(db, "_GLOBAL_BUDGET", 100)

    assert db.bytes_today("site") is None
    assert db.bytes_today_all() is None
    site = db.is_over_budget("site", site_cfg={"daily_byte_budget": 100})
    global_ = db.is_over_global_budget()
    for result in (site, global_):
        assert result["over"] is None
        assert result["unknown"] is True
        assert result["available"] is False
        assert result["used_bytes"] is None

    site_probe = _admission_probe({"daily_byte_budget": 100})
    site_hold = site_probe._resource_admission_hold()
    assert site_hold["state"] == "daily_budget_unknown"
    assert site_hold["report"]["unknown"] is True
    assert site_probe._state == "daily_budget_unknown"

    global_probe = _admission_probe({})
    global_hold = global_probe._resource_admission_hold()
    assert global_hold["state"] == "global_daily_budget_unknown"
    assert global_hold["report"]["unknown"] is True
    assert global_probe._state == "global_daily_budget_unknown"


def test_f38_measured_under_cap_still_admits(monkeypatch):
    from bulk_downloader import daily_budget as db

    monkeypatch.setattr(db, "bytes_today", lambda _site: 40)
    monkeypatch.setattr(db, "bytes_today_all", lambda: 70)
    monkeypatch.setattr(db, "_GLOBAL_BUDGET", 100)
    site = db.is_over_budget("site", site_cfg={"daily_byte_budget": 100})
    global_ = db.is_over_global_budget()
    assert site["over"] is False and site["used_bytes"] == 40
    assert global_["over"] is False and global_["used_bytes"] == 70


def _arm_pipeline(monkeypatch):
    from bulk_downloader import automation_controller as ac

    monkeypatch.setattr(ac, "off_switch_engaged", lambda: False)
    monkeypatch.setattr(ac, "controller_armed", lambda: True)
    monkeypatch.setattr(ac, "is_trusted_auto", lambda _host, _policy: True)
    monkeypatch.setattr(ac, "classify_boundary", lambda *_args: [])
    monkeypatch.setattr(ac.la, "is_enabled", lambda _name: True)


def test_f39_result_shaped_stage_failure_halts_before_apply(monkeypatch):
    from bulk_downloader import automation_pipeline as ap

    _arm_pipeline(monkeypatch)
    calls = []
    stages = [
        ("lint", lambda *_args: calls.append("lint") or
         {"ok": False, "error": "blocked term"}),
        ("apply", lambda *_args: calls.append("apply") or {"ok": True}),
    ]
    result = ap.run_pipeline("host", {"candidate": {}}, {}, stages=stages)
    assert calls == ["lint"], "dependent apply must not run after ok:false"
    assert result["ok"] is False
    assert result["halted_at"] == "lint"
    assert "blocked term" in result["error"]
    assert result["audit"][0]["result"]["ok"] is False


def test_f39_result_shaped_success_still_reaches_apply(monkeypatch):
    from bulk_downloader import automation_pipeline as ap

    _arm_pipeline(monkeypatch)
    calls = []
    stages = [
        ("lint", lambda *_args: calls.append("lint") or {"ok": True}),
        ("apply", lambda *_args: calls.append("apply") or {"ok": True}),
    ]
    result = ap.run_pipeline("host", {"candidate": {}}, {}, stages=stages)
    assert calls == ["lint", "apply"]
    assert result["ok"] is True


class _Cursor:
    def __init__(self, *, one=None, rows=()):
        self._one = one
        self._rows = list(rows)

    def fetchone(self):
        return self._one

    def __iter__(self):
        return iter(self._rows)


class _HealthyBitrotConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, *_args):
        normalized = " ".join(sql.split())
        if "COUNT(*) AS n FROM provenance" in normalized:
            return _Cursor(one={"n": 0})
        if "GROUP BY kind" in normalized:
            return _Cursor(rows=[])
        if "WHERE repaired = 1" in normalized:
            return _Cursor(one=(0,))
        if "MAX(last_verified_ts)" in normalized:
            return _Cursor(one=(0,))
        raise AssertionError(f"unexpected bitrot query: {normalized}")


def _bitrot_app(monkeypatch):
    from bulk_downloader import app_bitrot

    monkeypatch.setattr(app_bitrot, "_check_csrf", lambda *_a, **_k: None)
    app = Flask("bitrot-measurement-probe")
    app.register_blueprint(app_bitrot.bitrot_bp)
    return app.test_client()


def test_f40_failed_bitrot_inventory_is_unknown_not_clean(monkeypatch):
    from bulk_downloader import alerts_engine
    from bulk_downloader import bitrot
    from bulk_downloader import healthcheck

    monkeypatch.setattr(bitrot, "_ensure_integrity_table", lambda: None)
    from bulk_downloader import db
    monkeypatch.setattr(db, "db_conn", _broken_db_conn)

    scan = bitrot.run_scan()
    status = bitrot.stats()
    for result in (scan, status):
        assert result["ok"] is False
        assert result["available"] is False
        assert result["inventory_status"] == "unknown"
        assert "unreadable" in result["error"]
    assert scan["total_library"] is None
    assert status["open_issues"] is None

    health = healthcheck._check_bitrot()
    assert health["severity"] != healthcheck.SEV_OK
    assert "unavailable" in health["message"].lower()
    assert alerts_engine._evaluate_metric("bd_bitrot_open_issues") is None

    client = _bitrot_app(monkeypatch)
    assert client.post("/api/bitrot/scan", json={}).status_code == 503
    assert client.get("/api/bitrot/stats").status_code == 503


def test_f40_measured_empty_inventory_is_still_healthy(monkeypatch):
    from bulk_downloader import alerts_engine
    from bulk_downloader import bitrot
    from bulk_downloader import db
    from bulk_downloader import healthcheck

    monkeypatch.setattr(bitrot, "_ensure_integrity_table", lambda: None)
    monkeypatch.setattr(db, "db_conn", _HealthyBitrotConnection)
    scan = bitrot.run_scan()
    status = bitrot.stats()
    assert scan["total_library"] == 0
    assert status["open_issues"] == 0
    assert healthcheck._check_bitrot()["severity"] == healthcheck.SEV_OK
    assert alerts_engine._evaluate_metric("bd_bitrot_open_issues") == 0.0

    client = _bitrot_app(monkeypatch)
    assert client.post("/api/bitrot/scan", json={}).status_code == 200
    assert client.get("/api/bitrot/stats").status_code == 200


def test_f41_missing_or_failed_rss_is_unknown_and_holds_admission(monkeypatch):
    from bulk_downloader import daily_budget as db
    from bulk_downloader import run_budget as rb

    class BrokenProcess:
        def memory_info(self):
            raise OSError("rss sampler unavailable")

    monkeypatch.setitem(
        sys.modules, "psutil", types.SimpleNamespace(Process=BrokenProcess)
    )
    monkeypatch.setattr(db, "_GLOBAL_BUDGET", 0)
    assert rb.current_rss_mb() is None
    result = rb.is_over_mem_budget({"run_mem_budget_mb": 512})
    assert result["over"] is None
    assert result["rss_mb"] is None
    assert result["unknown"] is True
    assert result["available"] is False

    probe = _admission_probe({"run_mem_budget_mb": 512})
    hold = probe._resource_admission_hold()
    assert hold["state"] == "mem_budget_unknown"
    assert hold["report"]["unknown"] is True
    assert probe._state == "mem_budget_unknown"


def test_f41_measured_rss_below_cap_still_admits(monkeypatch):
    from bulk_downloader import daily_budget as db
    from bulk_downloader import run_budget as rb

    monkeypatch.setattr(db, "_GLOBAL_BUDGET", 0)
    monkeypatch.setattr(rb, "current_rss_mb", lambda: 128.0)
    result = rb.is_over_mem_budget({"run_mem_budget_mb": 512})
    assert result["over"] is False
    assert result["rss_mb"] == 128.0


class _FakeThread:
    started = []

    def __init__(self, *args, **kwargs):
        self.name = kwargs.get("name", "unnamed")

    def start(self):
        self.started.append(self.name)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


def _site_runner_without_backgrounds(monkeypatch, tmp_path):
    from bulk_downloader import runner

    monkeypatch.setattr(runner.SiteRunner, "_restore_queue", lambda _self: None)
    monkeypatch.setattr(runner.SiteRunner, "start_scheduler", lambda _self: None)
    monkeypatch.setattr(runner.SiteRunner, "_start_auto_retry", lambda _self: None)
    monkeypatch.setattr(runner, "disk_free_gb", lambda _path: 1000.0)
    config = {
        "name": "quota-probe",
        "download_dir": str(tmp_path),
        "site_quota_gb": 1,
        "auto_teach_first_run": False,
        "max_concurrent": 1,
    }
    probe = runner.SiteRunner("quota-probe", config)
    url = "https://example.test/video"
    probe.urls = [url]
    probe.jobs = {
        url: {"status": "pending", "retry_after": 0, "priority": "normal"}
    }
    return runner, probe


def test_f42_partial_site_tree_read_is_unknown_and_refuses_start(monkeypatch, tmp_path):
    from bulk_downloader import runner

    probe = runner.SiteRunner.__new__(runner.SiteRunner)
    with monkeypatch.context() as patch:
        patch.setattr(
            runner.os, "walk",
            lambda _root, onerror=None: iter([("/library", [], ["seen", "hidden"])]),
        )

        def size(path):
            if path.endswith("seen"):
                return 90
            raise PermissionError("hidden subtree unreadable")

        patch.setattr(runner.os.path, "getsize", size)
        assert probe._compute_site_usage("/library") is None

    runner_module, start_probe = _site_runner_without_backgrounds(
        monkeypatch, tmp_path
    )
    monkeypatch.setattr(start_probe, "_compute_site_usage", lambda _path: None)
    _FakeThread.started = []
    monkeypatch.setattr(runner_module.threading, "Thread", _FakeThread)
    start_probe.start()
    assert start_probe._state == "quota_usage_unknown"
    assert _FakeThread.started == [], "unknown quota usage must not start workers"
    unknown_events = [
        event for event in start_probe._event_log
        if event.get("kind") == "quota_unknown"
    ]
    assert len(unknown_events) == 1


def test_f42_measured_usage_below_quota_still_starts(monkeypatch, tmp_path):
    from bulk_downloader import runner

    payload = tmp_path / "measured.bin"
    payload.write_bytes(b"measured")
    direct = runner.SiteRunner.__new__(runner.SiteRunner)
    assert direct._compute_site_usage(str(tmp_path)) == len(b"measured")

    runner_module, probe = _site_runner_without_backgrounds(monkeypatch, tmp_path)
    monkeypatch.setattr(
        probe, "_compute_site_usage", lambda _path: len(b"measured")
    )
    _FakeThread.started = []
    monkeypatch.setattr(runner_module.threading, "Thread", _FakeThread)
    probe.start()
    assert probe._state == "running"
    assert len(_FakeThread.started) == 3
