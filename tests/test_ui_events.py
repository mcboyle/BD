"""Tests for ui_events module (v3.43.5).

Coverage:
  - tier whitelisting (basic events allowed at basic; verbose blocked at basic; etc.)
  - password redaction on field_change/keystroke events
  - storage key redaction for password/token/secret/auth/session keys
  - large field truncation
  - the /api/ui_events endpoint accepts valid batches and rejects bad input

Note on tmp dir: instead of pytest's tmp_path fixture (not supported by the
homegrown run_tests.py), each test that writes to disk uses a tempfile.TemporaryDirectory
context manager. Each test rebinds _LOG_PATH and _logger by hand via _with_temp_log().
"""
import json
import tempfile
from contextlib import contextmanager
from pathlib import Path


# ── Tier whitelisting ───────────────────────────────────────────────

def test_tier_categories_basic_allows_basic_only():
    from bulk_downloader.ui_events import _allowed_categories, TIER_BASIC
    cats = _allowed_categories(TIER_BASIC)
    assert "click" in cats
    assert "fetch" in cats
    assert "js_error" in cats
    assert "hover_enter" not in cats
    assert "mousemove" not in cats
    assert "keystroke" not in cats


def test_tier_categories_verbose_includes_basic():
    from bulk_downloader.ui_events import _allowed_categories, TIER_VERBOSE
    cats = _allowed_categories(TIER_VERBOSE)
    assert "click" in cats
    assert "hover_enter" in cats
    assert "focus" in cats
    assert "mousemove" not in cats
    assert "keystroke" not in cats


def test_tier_categories_extreme_allows_everything():
    from bulk_downloader.ui_events import _allowed_categories, TIER_EXTREME
    cats = _allowed_categories(TIER_EXTREME)
    assert "click" in cats
    assert "hover_enter" in cats
    assert "mousemove" in cats
    assert "keystroke" in cats
    assert "dom_mutation" in cats
    assert "console" in cats


def test_tier_categories_invalid_returns_empty():
    from bulk_downloader.ui_events import _allowed_categories
    assert _allowed_categories("INVALID") == set()
    assert _allowed_categories("") == set()


# ── Redaction (no disk needed) ──────────────────────────────────────

def test_redact_password_field():
    from bulk_downloader.ui_events import _redact
    pw = "hunter2-correct-horse-battery"  # 29 chars
    ev = {
        "category": "field_change",
        "event": "change",
        "data": {"input_type": "password", "value": pw},
    }
    out = _redact(ev)
    assert out["data"]["value"] == f"[redacted {len(pw)} chars]"
    assert out["data"]["input_type"] == "password"


def test_redact_password_keystroke():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "keystroke",
        "event": "key_settled",
        "data": {"input_type": "password", "key": "x", "value": "hunter2"},
    }
    out = _redact(ev)
    assert out["data"]["value"] == "[redacted 7 chars]"
    assert out["data"]["key"] == "x"


def test_redact_non_password_field_unchanged():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "field_change",
        "data": {"input_type": "text", "value": "username_here"},
    }
    out = _redact(ev)
    assert out["data"]["value"] == "username_here"


def test_redact_storage_password_key():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "storage_write",
        "data": {"key": "my_password", "value": "abcd1234"},
    }
    out = _redact(ev)
    assert out["data"]["value"] == "[redacted]"


def test_redact_storage_token_key():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "storage_read",
        "data": {"key": "auth_token", "value": "Bearer 12345"},
    }
    out = _redact(ev)
    assert out["data"]["value"] == "[redacted]"


def test_redact_storage_session_key_redacted():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "storage_write",
        "data": {"key": "user_session_id", "value": "abc123"},
    }
    out = _redact(ev)
    assert out["data"]["value"] == "[redacted]"


def test_redact_storage_innocent_key_unchanged():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "storage_write",
        "data": {"key": "theme_preference", "value": "dark"},
    }
    out = _redact(ev)
    assert out["data"]["value"] == "dark"


def test_redact_long_field_truncated():
    from bulk_downloader.ui_events import _redact
    ev = {
        "category": "click",
        "data": {"text": "x" * 50000},
    }
    out = _redact(ev)
    assert len(out["data"]["text"]) < 11000
    assert "[truncated 40000 chars]" in out["data"]["text"]


# ── Ingest (uses disk; needs tempdir + manual rebind) ───────────────

@contextmanager
def _with_temp_log():
    """Bind _LOG_PATH and _logger to a tempdir for the test duration."""
    from bulk_downloader import ui_events as uie
    with tempfile.TemporaryDirectory() as td:
        orig_path = uie._LOG_PATH
        orig_logger = uie._logger
        uie._LOG_PATH = Path(td) / "ui_events.log"
        uie._logger = None
        try:
            yield uie, uie._LOG_PATH
        finally:
            if uie._logger is not None:
                for h in list(uie._logger.handlers):
                    try: h.close()
                    except Exception: pass
                    uie._logger.removeHandler(h)
            uie._LOG_PATH = orig_path
            uie._logger = orig_logger


def test_ingest_drops_events_above_tier():
    with _with_temp_log() as (uie, _):
        events = [
            {"ts": 1, "category": "click", "event": "c1", "data": {}},
            {"ts": 2, "category": "mousemove", "event": "m1", "data": {"x": 5, "y": 5}},
            {"ts": 3, "category": "hover_enter", "event": "h1", "data": {}},
        ]
        accepted, dropped = uie.ingest(events, "basic")
        assert accepted == 1, f"expected 1 accepted, got {accepted}"
        assert dropped == 2, f"expected 2 dropped, got {dropped}"


def test_ingest_at_extreme_accepts_all():
    with _with_temp_log() as (uie, _):
        events = [
            {"ts": 1, "category": "click", "event": "c1", "data": {}},
            {"ts": 2, "category": "mousemove", "event": "m1", "data": {"x": 5, "y": 5}},
            {"ts": 3, "category": "hover_enter", "event": "h1", "data": {}},
        ]
        accepted, dropped = uie.ingest(events, "extreme")
        assert accepted == 3
        assert dropped == 0


def test_ingest_invalid_tier_drops_all():
    with _with_temp_log() as (uie, _):
        events = [{"ts": 1, "category": "click", "event": "c", "data": {}}]
        accepted, dropped = uie.ingest(events, "INVALID_TIER")
        assert accepted == 0
        assert dropped == 1


def test_ingest_empty_batch_returns_zero():
    with _with_temp_log() as (uie, _):
        accepted, dropped = uie.ingest([], "basic")
        assert accepted == 0
        assert dropped == 0


def test_ingest_writes_one_line_per_event():
    with _with_temp_log() as (uie, log_path):
        events = [
            {"ts": 1, "category": "click", "event": "btn_save", "data": {"id": "save"}},
            {"ts": 2, "category": "click", "event": "btn_cancel", "data": {"id": "cancel"}},
        ]
        uie.ingest(events, "basic")
        if uie._logger:
            for h in uie._logger.handlers:
                h.flush()
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["event"] == "btn_save"
        assert parsed[1]["event"] == "btn_cancel"
        assert "server_ts" in parsed[0]


def test_ingest_redacts_password_on_write():
    with _with_temp_log() as (uie, log_path):
        events = [{
            "ts": 1, "category": "field_change", "event": "change",
            "data": {"input_type": "password", "value": "super_secret_pw"},
        }]
        uie.ingest(events, "verbose")
        if uie._logger:
            for h in uie._logger.handlers:
                h.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "super_secret_pw" not in content
        assert "[redacted" in content


# ── API endpoint integration ────────────────────────────────────────

def _pair_and_csrf():
    from bulk_downloader import app as A
    c = A.app.test_client()
    r = c.get('/api/pair')
    token = r.get_json()['token']
    r = c.post('/api/pair/redeem', json={'token': token})
    csrf = r.get_json()['csrf_token']
    return c, {'X-CSRF-Token': csrf}


def test_api_endpoint_basic_flow():
    with _with_temp_log() as (uie, _):
        c, H = _pair_and_csrf()
        r = c.post('/api/global_config', json={'ui_logging_level': 'verbose'}, headers=H)
        assert r.status_code == 200
        r = c.post('/api/ui_events', json={
            'events': [
                {'ts': 1, 'category': 'click', 'event': 'btn1', 'data': {}},
                {'ts': 2, 'category': 'hover_enter', 'event': 'h1', 'data': {}},
                {'ts': 3, 'category': 'mousemove', 'event': 'm1', 'data': {}},
            ]
        }, headers=H)
        assert r.status_code == 200
        d = r.get_json()
        assert d['ok'] is True
        assert d['accepted'] == 2
        assert d['dropped'] == 1


def test_api_endpoint_rejects_huge_batch():
    with _with_temp_log() as (uie, _):
        c, H = _pair_and_csrf()
        events = [{'ts': i, 'category': 'click', 'event': f'c{i}', 'data': {}}
                  for i in range(1001)]
        r = c.post('/api/ui_events', json={'events': events}, headers=H)
        assert r.status_code == 413
        assert 'too large' in r.get_json()['error']


def test_api_endpoint_rejects_non_array():
    c, H = _pair_and_csrf()
    r = c.post('/api/ui_events', json={'events': 'not an array'}, headers=H)
    assert r.status_code == 400


def test_api_global_config_rejects_invalid_tier():
    c, H = _pair_and_csrf()
    r = c.post('/api/global_config', json={'ui_logging_level': 'maximum'}, headers=H)
    assert r.status_code == 400


def test_api_global_config_default_is_basic():
    """When no value has been written, /api/global_config returns 'basic'
    as the ui_logging_level default. This test resets the value first
    in case earlier tests set it, since _app_cfg lives in the module
    and persists across test invocations within a single process."""
    from bulk_downloader import app as A
    # Reset to default by removing the key (the endpoint will inject 'basic')
    A._app_cfg.pop('ui_logging_level', None)
    c = A.app.test_client()
    r = c.get('/api/global_config')
    d = r.get_json()
    assert d.get('ui_logging_level') == 'basic'


def test_foreign_handler_does_not_suppress_the_ui_events_log():
    """A handler someone ELSE attached must not stop ui_events opening its own.

    The guard's own comment says it exists so a concurrent second caller does
    not add a SECOND handler -- its subject is OUR handler. It keyed on
    ``if lg.handlers:`` instead, whose denominator is EVERY handler, so anything
    attaching to the named logger first makes _get_logger() return before
    creating its TimedRotatingFileHandler. ui_events.log is then never written,
    and because propagate is False the events do not reach root either: they are
    silently dropped, which is the whole feature failing quietly.

    pytest 9's logging plugin is one such attacher, which is how this surfaced
    (two failures on the box at v3.66.902 while the container's pytest 8.4.2
    stayed green). THE DEFECT IS NOT ABOUT PYTEST -- this test attaches the
    foreign handler itself, so it is RED on any runner and does not depend on
    which plugins happen to be loaded.
    """
    import logging
    import logging.handlers
    with _with_temp_log() as (uie, path):
        lg = logging.getLogger("bulk_downloader.ui_events")
        saved = list(lg.handlers)
        for h in saved:
            lg.removeHandler(h)
        foreign = logging.StreamHandler()
        lg.addHandler(foreign)
        try:
            accepted, _ = uie.ingest(
                [{"ts": 1, "category": "click", "event": "c1", "data": {}}],
                "basic")
            assert accepted == 1, accepted
            for h in lg.handlers:
                try: h.flush()
                except Exception: pass
            own = [h for h in lg.handlers
                   if isinstance(h, logging.handlers.TimedRotatingFileHandler)]
            assert own, (
                "ui_events never opened its own file handler because a foreign "
                f"one was present: {[type(h).__name__ for h in lg.handlers]}")
            assert path.exists(), f"{path} was never created"
            body = path.read_text(encoding="utf-8")
            assert "c1" in body, f"event never reached the log file: {body!r}"
        finally:
            lg.removeHandler(foreign)
            try: foreign.close()
            except Exception: pass
            for h in saved:
                lg.addHandler(h)


def test_get_logger_attaches_its_own_handler_exactly_once():
    """Re-entering _get_logger must not stack a second file handler.

    This is the guard's ORIGINAL purpose, and the foreign-handler test above
    does not constrain it: two mutants (dropping the tag, and forcing the guard
    false) leave that test green while every re-entry piles on another
    TimedRotatingFileHandler -- duplicating every line written thereafter.

    The module-level _logger cache HIDES this, so the cache is cleared between
    the two calls. That is not an impossible state: the guard's own comment is
    about being "called twice concurrently", which is precisely two callers
    passing `if _logger is not None` before either assigns it. Without clearing
    it, _get_logger returns on its first line, the guard is never reached, and
    the assertion would hold no matter what the guard says -- a test that
    cannot see its subject.
    """
    import logging
    import logging.handlers
    with _with_temp_log() as (uie, _):
        lg = logging.getLogger("bulk_downloader.ui_events")
        saved = list(lg.handlers)
        for h in saved:
            lg.removeHandler(h)
        try:
            uie._logger = None
            uie._get_logger()
            uie._logger = None      # force the guard, not the cache, to decide
            uie._get_logger()
            own = [h for h in lg.handlers
                   if isinstance(h, logging.handlers.TimedRotatingFileHandler)]
            assert len(own) == 1, (
                f"expected exactly one file handler after re-entry, got "
                f"{len(own)}: {[type(h).__name__ for h in lg.handlers]}")
        finally:
            for h in list(lg.handlers):
                try: h.close()
                except Exception: pass
                lg.removeHandler(h)
            for h in saved:
                lg.addHandler(h)
