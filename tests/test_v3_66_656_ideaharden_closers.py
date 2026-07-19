"""v3.66.656 -- IDEA-HARDEN closers.

CAP-1: the warm-up path (aiassist.warmup / provider.warmup) existed but had no
caller -- the model was never pre-loaded, so the first inference paid the cold
load (the open L19 thread). This wires a one-shot, idempotent, best-effort
``aiassist.warm_once()`` into the two inference entry points (aiassist._call_model
and ai_chat.chat) so the provider is warmed on FIRST USE, exactly once per process,
never on a disabled provider, and never fatally (a warm failure must not block the
inference that follows).

OBS-1 (fold): the 641 after-hook already FLAGS any over-budget request, but two
collect-style routes loop UNBOUNDED at the route level with no budget --
collect_workflow_analytics (every draft/review JSON) and collect_site_health (a
per-site DB query per site). The capture_diagnostics/replay routes are already
budgeted (budget_s at the tool level), so they're left alone. This adds a
cooperative ``_budget_tick()`` at those two loop boundaries: inside a request past
REQUEST_BUDGET_MS it raises RequestBudgetExceeded (-> the app errorhandler maps it
to a retryable 503); outside a request or with the clock unset it no-ops, so
background/test calls are unaffected.
"""
import time
import pytest

from bulk_downloader import aiassist, ai_chat
from bulk_downloader import app_data_layer as adl
from bulk_downloader import dev_metrics


# ============================ CAP-1: warm_once ============================

def _reset():
    aiassist._reset_warm()


def test_warm_once_is_idempotent(monkeypatch):
    _reset()
    calls = {"n": 0}
    monkeypatch.setattr(aiassist, "warmup", lambda timeout=120.0: calls.__setitem__("n", calls["n"] + 1) or True)
    aiassist.warm_once()
    aiassist.warm_once()
    aiassist.warm_once()
    assert calls["n"] == 1, "warm_once must warm exactly once per process"


def test_warm_once_best_effort_never_raises(monkeypatch):
    _reset()
    def boom(timeout=120.0):
        raise RuntimeError("provider down")
    monkeypatch.setattr(aiassist, "warmup", boom)
    # must not propagate, and must still mark attempted (no hammering on failure)
    aiassist.warm_once()
    calls = {"n": 0}
    monkeypatch.setattr(aiassist, "warmup", lambda timeout=120.0: calls.__setitem__("n", calls["n"] + 1) or True)
    aiassist.warm_once()
    assert calls["n"] == 0, "a failed first attempt still counts -- no retry storm"


def test_call_model_warms_on_first_use(monkeypatch):
    _reset()
    seen = {"warm": 0}
    monkeypatch.setattr(aiassist, "warm_once", lambda: seen.__setitem__("warm", seen["warm"] + 1))

    class _P:
        def generate(self, *a, **k):
            from bulk_downloader import ai_provider
            return ai_provider.GenerationResult(ok=True, provider="stub", text="hi")
    monkeypatch.setattr(aiassist, "_get_provider", lambda: _P())
    aiassist._call_model("hello")
    assert seen["warm"] == 1, "_call_model must warm on first use"


def test_ai_chat_warms_when_enabled(monkeypatch):
    _reset()
    seen = {"warm": 0}
    monkeypatch.setattr(aiassist, "warm_once", lambda: seen.__setitem__("warm", seen["warm"] + 1))

    # warm_once fires before execute() regardless of the inference outcome; the
    # execute() result contract is exercised by the ai_chat suite, not here.
    ai_chat.chat({"prompt": "hey"},
                 config={"enabled": True, "provider": "p", "model_text": "m"})
    # >=1: the execute path may re-enter _call_model's warm too; the real one-shot
    # _warmed flag collapses both to a single actual warm in production.
    assert seen["warm"] >= 1, "ai_chat.chat must warm on first use when enabled"


def test_ai_chat_does_not_warm_when_disabled(monkeypatch):
    _reset()
    seen = {"warm": 0}
    monkeypatch.setattr(aiassist, "warm_once", lambda: seen.__setitem__("warm", seen["warm"] + 1))
    res = ai_chat.chat({"prompt": "hey"}, config={"enabled": False})
    assert res["ok"] is False
    assert seen["warm"] == 0, "a disabled provider must not be warmed"


# ============================ OBS-1: _budget_tick ============================

def test_budget_tick_noop_outside_request():
    # no request context -> must not raise
    adl._budget_tick()


def test_budget_tick_raises_when_over_budget():
    from bulk_downloader.app import app
    with app.test_request_context("/api/data/workflow_analytics"):
        from flask import g
        g._dev_t0 = time.time() - (dev_metrics.REQUEST_BUDGET_MS / 1000.0) - 5
        with pytest.raises(dev_metrics.RequestBudgetExceeded):
            adl._budget_tick()


def test_budget_tick_noop_when_fresh():
    from bulk_downloader.app import app
    with app.test_request_context("/api/data/site_health"):
        from flask import g
        g._dev_t0 = time.time()  # just started -> well under budget
        adl._budget_tick()       # must not raise


def test_collect_loops_call_budget_tick(monkeypatch, tmp_path):
    # collect_site_health must tick inside its per-site loop: force an over-budget
    # tick and assert the route aborts cooperatively.
    from bulk_downloader.app import app
    from bulk_downloader import cookie_health as _ch, db as _db
    monkeypatch.setattr(adl, "_root", lambda: str(tmp_path))
    # seed two sites so the loop body runs at least once
    monkeypatch.setattr(_ch, "status_all",
                        lambda: [{"site_id": "a", "status": "green"},
                                 {"site_id": "b", "status": "red"}])
    monkeypatch.setattr(_db, "db_session_failure_clusters",
                        lambda lookback_days=7: {"per_site": {}, "total_failures": 0, "clusters": []})
    monkeypatch.setattr(_db, "session_lifetime_observations",
                        lambda sid, lookback_days=7: [])
    with app.test_request_context("/api/data/site_health"):
        from flask import g
        g._dev_t0 = time.time() - (dev_metrics.REQUEST_BUDGET_MS / 1000.0) - 5
        with pytest.raises(dev_metrics.RequestBudgetExceeded):
            adl.collect_site_health()
