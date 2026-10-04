"""O1826 C45: observability swallows (M003, M024, M083, M105, M147).

Each forced failure must leave one log line (and, where a result is
returned, an error field) instead of a bare ``pass``; the scene-filter
fallback must catch ImportError only. Success paths stay silent.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import sys

import flask
import pytest

import bulk_downloader
from bulk_downloader import (
    account_health,
    aiassist,
    app_template,
    capture_login_wire,
    db,
    knowledge,
    llm_audit,
    llm_cache,
    playlist_extractor,
    search_extractor,
)
from bulk_downloader.llm_exec import LLMCallSpec, execute

BD_GATE_SCOPE = "module"


def _records(caplog, logger_name):
    return [
        r
        for r in caplog.records
        if r.name == logger_name and r.levelno >= logging.WARNING
    ]


# ── M003 account_health monitor loop ──────────────────────────────────────
def _run_loop_once(run_once_exc):
    mon = account_health.SessionLivenessMonitor(interval_seconds=0)

    def _run_once():
        mon._stop_event.set()
        if run_once_exc is not None:
            raise run_once_exc
        return []

    mon.run_once = _run_once
    mon._monitor_loop()


def test_monitor_loop_failure_is_logged(caplog):
    caplog.set_level(logging.WARNING)
    _run_loop_once(RuntimeError("c45-monitor-boom"))
    recs = _records(caplog, "bulk_downloader.account_health")
    assert len(recs) == 1, caplog.text
    assert recs[0].exc_info and "c45-monitor-boom" in str(recs[0].exc_info[1])


def test_monitor_loop_success_is_silent(caplog):
    caplog.set_level(logging.WARNING)
    _run_loop_once(None)
    assert _records(caplog, "bulk_downloader.account_health") == []


# ── M024 app_template login-selector seeding ──────────────────────────────
def _post_test_extract(monkeypatch, seed):
    app = flask.Flask("c45_template_test")
    app.register_blueprint(app_template.template_bp)
    cfg = {"name": "s1"}
    monkeypatch.setattr(app_template, "_app_app", lambda: app)
    monkeypatch.setattr(app_template, "_app_runners", lambda: {})
    monkeypatch.setattr(app_template, "_app_s_cfg", lambda: {"s1": cfg})
    monkeypatch.setattr(app_template, "_app_s_meta", lambda: {})
    monkeypatch.setattr(app_template, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(app_template, "_build_meta", lambda *a, **k: {})
    monkeypatch.setattr(app_template, "_save_sites_config", lambda *a, **k: None)
    monkeypatch.setattr(capture_login_wire, "apply_draft_login_selectors", seed)
    resp = app.test_client().post(
        "/api/template/test_extract",
        json={"site_id": "s1", "template": {"login": {"user_field": "#u"}}},
    )
    return resp, cfg, app


def test_login_seed_failure_is_logged_and_reported(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)

    def _boom(cfg, login):
        raise RuntimeError("c45-seed-boom")

    resp, cfg, app = _post_test_extract(monkeypatch, _boom)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "c45-seed-boom" in body.get("login_seed_error", "")
    recs = _records(caplog, app.logger.name)
    assert len(recs) == 1, caplog.text
    assert "c45-seed-boom" in recs[0].getMessage()


def test_login_seed_success_is_silent(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)

    def _seed(cfg, login):
        cfg["user_field"] = login["user_field"]
        return ["user_field"]

    resp, cfg, app = _post_test_extract(monkeypatch, _seed)
    body = resp.get_json()
    assert body["ok"] is True
    assert "login_seed_error" not in body
    assert cfg["draft_test_override"]["seeded_login"] == {"user_field": "#u"}
    assert _records(caplog, app.logger.name) == []


# ── M083 knowledge runbook + note helpers ─────────────────────────────────
def _failing_db(monkeypatch):
    @contextlib.contextmanager
    def _conn(path=None):
        raise sqlite3.OperationalError("c45-db-boom")
        yield  # pragma: no cover

    monkeypatch.setattr(db, "db_conn", _conn)


def _memory_db(monkeypatch):
    cx = sqlite3.connect(":memory:")
    cx.row_factory = sqlite3.Row
    cx.execute("CREATE TABLE history(site_id TEXT, status TEXT, message TEXT, ts TEXT)")
    cx.execute(
        "INSERT INTO history VALUES ('s1', 'failed', 'c45 fail', datetime('now'))"
    )
    cx.execute("INSERT INTO history VALUES ('s1', 'done', 'ok', datetime('now'))")

    @contextlib.contextmanager
    def _conn(path=None):
        yield cx

    monkeypatch.setattr(db, "db_conn", _conn)
    return cx


def test_runbook_db_failure_is_logged_and_reported(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    _failing_db(monkeypatch)
    out = knowledge.runbook("s1", s_cfg={"s1": {"name": "S1"}})
    assert "c45-db-boom" in out.get("error", "")
    msgs = [r.getMessage() for r in _records(caplog, "bulk_downloader.knowledge")]
    assert any("runbook" in m and "c45-db-boom" in m for m in msgs), caplog.text


def test_runbook_success_is_silent(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    _memory_db(monkeypatch)
    out = knowledge.runbook("s1", s_cfg={"s1": {"name": "S1"}})
    assert "error" not in out
    assert out["top_failures"] and out["top_failures"][0]["message"] == "c45 fail"
    assert _records(caplog, "bulk_downloader.knowledge") == []


@pytest.mark.parametrize(
    "call, default",
    [
        (lambda: knowledge.add_note(pattern="p", resolution="r"), None),
        (lambda: knowledge.remove_note(1), False),
        (lambda: knowledge.find_matching_notes(message="m"), []),
        (lambda: knowledge.list_notes(), []),
    ],
)
def test_note_helpers_db_failure_is_logged(monkeypatch, caplog, call, default):
    caplog.set_level(logging.WARNING)
    _failing_db(monkeypatch)
    assert call() == default
    msgs = [r.getMessage() for r in _records(caplog, "bulk_downloader.knowledge")]
    assert len(msgs) == 1 and "c45-db-boom" in msgs[0], caplog.text


# ── M105 llm_exec audit / cache / metrics ─────────────────────────────────
class _Fake:
    ok = True
    text = '{"role": "download"}'
    error = ""
    error_kind = ""
    provider = "ollama"
    model = "bd-text-small"
    latency_ms = 5


def _call(prompt, image_b64=None, max_tokens=1024, temperature=0.1, timeout=60.0):
    return _Fake()


def _spec(**kw):
    base = dict(
        task_id="classify",
        prompt_id="classify_role",
        prompt_version="1",
        input="role of <button>Download</button>",
        provider="ollama",
        model="bd-text-small",
        schema={
            "type": "object",
            "required": ["role"],
            "properties": {"role": {"type": "string"}},
        },
    )
    base.update(kw)
    return LLMCallSpec(**base)


def _quiet_collaborators(monkeypatch):
    monkeypatch.setattr(llm_audit, "record", lambda md: None)
    monkeypatch.setattr(aiassist, "_record_call", lambda *a, **k: None)
    monkeypatch.setattr(llm_cache, "get", lambda key: None)
    monkeypatch.setattr(llm_cache, "set", lambda *a, **k: None)


def _boom(tag):
    def _raise(*a, **k):
        raise RuntimeError(tag)

    return _raise


@pytest.mark.parametrize(
    "module, attr, tag",
    [
        (llm_audit, "record", "c45-audit-boom"),
        (aiassist, "_record_call", "c45-metrics-boom"),
        (llm_cache, "get", "c45-cache-read-boom"),
        (llm_cache, "set", "c45-cache-write-boom"),
    ],
)
def test_llm_exec_side_path_failure_is_logged(monkeypatch, caplog, module, attr, tag):
    caplog.set_level(logging.WARNING)
    _quiet_collaborators(monkeypatch)
    monkeypatch.setattr(module, attr, _boom(tag))
    res = execute(_spec(use_cache=True), _call=_call)
    assert res.ok is True and res.value == {"role": "download"}
    recs = [
        r
        for r in _records(caplog, "bulk_downloader.llm_exec")
        if r.exc_info and tag in str(r.exc_info[1])
    ]
    assert len(recs) == 1, caplog.text


def test_llm_exec_success_is_silent(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    _quiet_collaborators(monkeypatch)
    res = execute(_spec(use_cache=True), _call=_call)
    assert res.ok is True and res.via == "model"
    assert _records(caplog, "bulk_downloader.llm_exec") == []


# ── M147 search_extractor scene-filter fallback ───────────────────────────
def test_scene_filter_error_propagates(monkeypatch):
    monkeypatch.setattr(playlist_extractor, "_looks_like_scene_url", _boom_value_error)
    with pytest.raises(ValueError, match="c45-scene-boom"):
        search_extractor._looks_like_scene_url("https://x.test/category/a")


def _boom_value_error(url, template=None):
    raise ValueError("c45-scene-boom")


def test_scene_filter_import_error_uses_fallback(monkeypatch):
    monkeypatch.delattr(bulk_downloader, "playlist_extractor")
    monkeypatch.setitem(sys.modules, "bulk_downloader.playlist_extractor", None)
    assert search_extractor._looks_like_scene_url("https://x.test/video/1") is True
    assert search_extractor._looks_like_scene_url("https://x.test/category/a") is False


def test_scene_filter_success_delegates(monkeypatch):
    seen = []

    def _pe(url, template=None):
        seen.append((url, template))
        return True

    monkeypatch.setattr(playlist_extractor, "_looks_like_scene_url", _pe)
    assert search_extractor._looks_like_scene_url("https://x.test/a", {"t": 1}) is True
    assert seen == [("https://x.test/a", {"t": 1})]
