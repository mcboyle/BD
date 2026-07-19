"""v3.66.491 K5 (plugin-v3 kind): sink plugins.

First-class configured, RETRYABLE event consumers (webhook / DB / Discord /
spreadsheet) -- generalizes today's fire-and-forget hooks into a delivery layer
with guarantees: retry + backoff + dead-letter + at-least-once. Sink quarantine
on sustained failure rides R2.

Contract:
  deliver(event, payload, ctx) -> {ok, retry_after?, permanent?}
    * {"ok": True}                       -> ack
    * {"ok": False, "retry_after": N}    -> transient; framework retries
    * {"ok": False, "permanent": True}   -> permanent; framework dead-letters
    * raising                            -> treated as a transient failure

Framework (plugins.deliver_to_sinks) owns retry/backoff/dead-letter. A sink
declares idempotency (informational; surfaced).

K5 raises PLUGIN_API_MAX to 6.

Runner-safe: zero-arg fns, no pytest builtins, globals restored in try/finally.
The injected ``sleep`` recorder keeps backoff out of wall-clock.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _reset():
    P.reset()
    P.clear_quarantine()
    if hasattr(P, "drain_dead_letter"):
        P.drain_dead_letter()


def test_api_max_raised_to_6_keeps_prior_compatible():
    assert P.PLUGIN_API_MAX >= 6
    for v in (2, 3, 4, 5, 6):
        ok, _ = P.api_compatible({"api_version": v})
        assert ok


def test_sink_capability_documented():
    assert getattr(P, "CAP_SINK", None) == "sink"
    ke = P.known_events()
    assert P.CAP_SINK in ke["capabilities"]
    assert ke["api_max"] >= 6


def test_register_list_status_reset():
    _reset()
    try:
        P.register_sink(lambda e, p, c: {"ok": True}, name="s1",
                        priority=10, idempotent=True)
        rows = P.list_sinks()
        assert "s1" in [s["name"] for s in rows]
        assert rows[0]["idempotent"] is True
        assert "sinks" in P.status()
        P.reset()
        assert P.list_sinks() == []
    finally:
        _reset()


# ── (a) successful deliver acks ───────────────────────────────────────
def test_successful_deliver_acks():
    _reset()
    try:
        got = []
        P.register_sink(lambda e, p, c: got.append((e, p)) or {"ok": True},
                        name="ok")
        res = P.deliver_to_sinks("download.done", {"u": 1}, {})
        row = [r for r in res if r["name"] == "ok"][0]
        assert row["ok"] is True
        assert row["attempts"] == 1
        assert row["dead_lettered"] is False
        assert got == [("download.done", {"u": 1})]
    finally:
        _reset()


# ── (b) transient failure retries with backoff ───────────────────────
def test_transient_failure_retries_with_backoff():
    _reset()
    try:
        calls = {"n": 0}

        def flaky(e, p, c):
            calls["n"] += 1
            if calls["n"] < 3:
                return {"ok": False, "retry_after": 0.2}
            return {"ok": True}
        P.register_sink(flaky, name="flaky")
        slept = []
        res = P.deliver_to_sinks("e", {}, {}, sleep=lambda s: slept.append(s))
        row = [r for r in res if r["name"] == "flaky"][0]
        assert row["ok"] is True
        assert row["attempts"] == 3
        assert len(slept) == 2, "two backoff waits before the 3rd attempt"
        assert all(s > 0 for s in slept)
    finally:
        _reset()


# ── (c) permanent failure -> dead-letter, not infinite retry ──────────
def test_permanent_failure_dead_letters_immediately():
    _reset()
    try:
        calls = {"n": 0}

        def perm(e, p, c):
            calls["n"] += 1
            return {"ok": False, "permanent": True}
        P.register_sink(perm, name="perm")
        res = P.deliver_to_sinks("e", {"x": 1}, {}, sleep=lambda s: None)
        row = [r for r in res if r["name"] == "perm"][0]
        assert row["ok"] is False
        assert row["dead_lettered"] is True
        assert calls["n"] == 1, "permanent failure must not retry"
        dl = P.list_dead_letter()
        assert any(d["sink"] == "perm" for d in dl)
    finally:
        _reset()


def test_transient_exhaustion_is_bounded_not_infinite():
    _reset()
    try:
        calls = {"n": 0}

        def always(e, p, c):
            calls["n"] += 1
            return {"ok": False, "retry_after": 0.1}
        P.register_sink(always, name="always")
        res = P.deliver_to_sinks("e", {}, {}, max_attempts=4,
                                 sleep=lambda s: None)
        row = [r for r in res if r["name"] == "always"][0]
        assert row["dead_lettered"] is True
        assert calls["n"] == 4, "bounded at max_attempts, not infinite"
    finally:
        _reset()


# ── (d) at-least-once: never silently dropped ─────────────────────────
def test_at_least_once_delivered_after_transient():
    _reset()
    try:
        delivered = []
        calls = {"n": 0}

        def flaky(e, p, c):
            calls["n"] += 1
            if calls["n"] < 2:
                return {"ok": False, "retry_after": 0.1}
            delivered.append(p)
            return {"ok": True}
        P.register_sink(flaky, name="alo")
        P.deliver_to_sinks("e", {"id": 7}, {}, sleep=lambda s: None)
        assert delivered == [{"id": 7}], "delivered at least once, not dropped"
    finally:
        _reset()


def test_at_least_once_failed_lands_in_dead_letter_not_lost():
    _reset()
    try:
        P.register_sink(lambda e, p, c: {"ok": False, "permanent": True},
                        name="lost")
        P.deliver_to_sinks("e", {"id": 9}, {}, sleep=lambda s: None)
        dl = P.list_dead_letter()
        recovered = [d for d in dl if d["sink"] == "lost"]
        assert recovered, "an undelivered event is recoverable, never silently lost"
        assert recovered[0]["payload"] == {"id": 9}
    finally:
        _reset()


# ── (e) sink quarantine on sustained failure (rides R2) ───────────────
def test_sink_quarantined_on_sustained_failure():
    _reset()
    try:
        calls = {"n": 0}

        def bad(e, p, c):
            calls["n"] += 1
            return {"ok": False, "retry_after": 0.0}
        P.register_sink(bad, name="bad")
        # Each exhausted delivery records one R2 fail; budget=5.
        for _ in range(P._FAIL_BUDGET):
            P.deliver_to_sinks("e", {}, {}, max_attempts=1,
                               sleep=lambda s: None)
        before = calls["n"]
        # Now quarantined: the next deliver must SKIP the sink (no new call).
        res = P.deliver_to_sinks("e", {}, {}, max_attempts=1,
                                 sleep=lambda s: None)
        assert calls["n"] == before, "quarantined sink must be skipped"
        row = [r for r in res if r["name"] == "bad"]
        assert (not row) or row[0].get("skipped") is True
    finally:
        _reset()


# ── exception isolation ───────────────────────────────────────────────
def test_throwing_sink_isolated_and_others_deliver():
    _reset()
    try:
        ok_got = []

        def boom(e, p, c):
            raise RuntimeError("nope")
        P.register_sink(boom, name="boom", priority=10)
        P.register_sink(lambda e, p, c: ok_got.append(p) or {"ok": True},
                        name="ok", priority=20)
        res = P.deliver_to_sinks("e", {"z": 1}, {}, max_attempts=1,
                                 sleep=lambda s: None)  # must not raise
        assert ok_got == [{"z": 1}]
        boom_row = [r for r in res if r["name"] == "boom"][0]
        assert boom_row["ok"] is False
    finally:
        _reset()


# ── decorator parity ──────────────────────────────────────────────────
def test_sink_decorator():
    _reset()
    try:
        seen = []

        @P.sink(priority=5, name="deco", idempotent=True)
        def _s(event, payload, ctx):
            seen.append(event)
            return {"ok": True}
        P.deliver_to_sinks("evt", {}, {})
        assert seen == ["evt"]
    finally:
        _reset()
