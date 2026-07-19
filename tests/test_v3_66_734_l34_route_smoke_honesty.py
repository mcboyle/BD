"""v3.66.734 — L34 full-route-smoke: an EXCEEDED route is a finding, not a story.

THE BUG THIS PINS. In the 732 and 733 stash captures L34 FAILED with
"TIMEOUT after 90.0s — thread leaked". The 90s was fully accounted for:

    398 routes  ->   7.5s   (mean 19ms)
     28 routes  ->  42.3s   (>=500ms each)
      5 routes  ->  40.0s   (8s per-route timeout each)
                    -----
                    89.8s   of a 90s budget

Five routes ate 44% of the budget, and L34 logged each of them as

    TIMEOUT  /cockpit/api/housekeeping/preview (likely a stream)

That parenthetical is an ASSERTED CAUSE WITH NO EVIDENCE. All five are plain
`return jsonify(...)` views -- not streams, just slow:

    /api/data/capture_diagnostics        _safe(collect_capture_diagnostics)
    /api/data/replay_validation          _safe(collect_replay_validation)
    /cockpit/api/housekeeping/preview    ahk.run_housekeeping(mode="suggest")
    /cockpit/api/autonomy/queue          ac.queue_intelligence()
    /cockpit/api/autonomy/notifications  ac.notification_center()

...and four of them are operator-facing (Report Center sections / cockpit views).
The label turned a real performance defect into "probably fine", and the verdict
returned WARN "(likely streaming endpoints)" -- so even a COMPLETED run would have
shrugged at it. This is the aspirational-comment + unknown-laundered-into-OK shape,
living inside the live-test harness itself.

THE FIX IS NOT THE SKIP LIST. Adding these five to _L34_STREAMING_SKIP would make
L34 green and hide the finding -- and `test_skip_set_only_contains_streaming_routes`
in test_u44 already forbids it. The skip list is for routes that stream FOREVER
(/api/stream); these terminate. An unskipped route that blows its budget is either
a defect or an undeclared stream, and the operator must say which.

So: EXCEEDED is a THIRD STATE, it names no cause, and IT FAILS.

And L34 must be able to FINISH: 523 routes served serially cannot fit 90s and the
route count only grows. Raising the ceiling is the wrong lever (the SLOW_TOOLS
lesson). The routes are I/O-bound and the app is threaded (Flask app.run defaults
threaded=True; L20/L21 already drive 8-180 concurrent readers), so L34 smokes them
concurrently.
"""
from __future__ import annotations

import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks
import live_tests.harness as h


class _FakeCtx:
    """Route table + canned responses; no live app."""

    def __init__(self, routes, responses=None):
        self._routes = routes
        self._responses = responses or {}
        self._log = []
        self._gets = []

    def get(self, path, timeout=15):
        self._gets.append(path)
        if path == "/api/dev/routes":
            return True, 200, {"count": len(self._routes),
                               "routes": self._routes}, 1.0
        return self._responses.get(path, (True, 200, "", 1.0))

    def log(self, msg):
        self._log.append(msg)

    @property
    def log_text(self):
        return "\n".join(self._log)


# A per-route budget blow-out, exactly as harness.ctx.get reports it:
# (False, None, "<urlopen error timed out>", ms).
_TIMED_OUT = (False, None, "URLError: <urlopen error timed out>", 8000.0)


def _routes(*rules):
    return [{"rule": r, "methods": ["GET"]} for r in rules]


# ── the label must not invent a cause ─────────────────────────────

def test_l34_does_not_assert_a_cause_for_an_exceeded_route():
    """'(likely a stream)' is a guess. The log must report what was OBSERVED
    (the route blew its budget) and must not assert why."""
    ctx = _FakeCtx(_routes("/api/health", "/cockpit/api/autonomy/queue"),
                   {"/cockpit/api/autonomy/queue": _TIMED_OUT})
    checks.l34_full_route_smoke(ctx)
    log = ctx.log_text.lower()
    assert "likely a stream" not in log, (
        "L34 still asserts '(likely a stream)' for a route that merely blew "
        "its budget -- an unevidenced cause. All five real-world instances "
        "were plain jsonify() views."
    )
    assert "/cockpit/api/autonomy/queue" in ctx.log_text
    assert "exceeded" in log or "unknown" in log, (
        "L34 must SAY that the route exceeded its budget and that the cause "
        "is unknown -- unknown is a third state, not a shrug."
    )


# ── an unexplained slow route is a FINDING ────────────────────────

def test_l34_fails_on_an_exceeded_route():
    """Previously: WARN 'all routes responded; N timed out (likely streaming
    endpoints)'. A read-only GET that cannot answer inside its budget is a
    defect or an undeclared stream. Either way the operator must act, so it
    FAILS -- and the failure NAMES the route."""
    ctx = _FakeCtx(_routes("/api/health", "/api/data/replay_validation"),
                   {"/api/data/replay_validation": _TIMED_OUT})
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.FAIL, (
        f"an unskipped route that blew its budget must FAIL, got {level} — {detail}"
    )
    assert "/api/data/replay_validation" in detail, (
        "the verdict must NAME the offending route -- a gate that refuses "
        "without naming teaches the operator to override it"
    )


def test_l34_exceeded_is_distinct_from_unreachable_and_5xx():
    """Three different facts, three different buckets. A slow route is not a
    dead route, and neither is a 5xx."""
    ctx = _FakeCtx(
        _routes("/api/ok", "/api/slow", "/api/broken", "/api/gone"),
        {
            "/api/slow": _TIMED_OUT,
            "/api/broken": (False, 500, None, 5.0),
            "/api/gone": (False, None, "ConnectionRefusedError", 5.0),
        },
    )
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.FAIL
    log = ctx.log_text
    assert "/api/slow" in log and "/api/broken" in log and "/api/gone" in log
    # the slow one must NOT be reported as unreachable -- it responded to the
    # connection, it just did not finish.
    assert "UNREACHABLE  /api/slow" not in log, (
        "a budget blow-out was bucketed as UNREACHABLE -- collapsing "
        "'did not finish' into 'is not there' is the bug itself"
    )


def test_l34_still_passes_a_clean_sweep():
    """The new state must not fire on a healthy app."""
    ctx = _FakeCtx(_routes("/api/health", "/api/status", "/api/stream"))
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.PASS, f"clean sweep should PASS, got {level} — {detail}"


# ── it must be able to FINISH ─────────────────────────────────────

def test_l34_smokes_routes_concurrently():
    """523 routes served serially do not fit a 90s wall, and the route count
    only grows. Raising the ceiling is the wrong lever -- the routes are
    I/O-bound, so smoke them concurrently. Pinned at source level because a
    'simplifying' refactor back to a serial for-loop reintroduces the wedge
    and nothing else would catch it."""
    src = inspect.getsource(checks.l34_full_route_smoke)
    assert re.search(r"ThreadPool|Executor|concurrent\.futures|map\(", src), (
        "l34_full_route_smoke smokes routes serially -- it cannot finish "
        "523 routes inside the 90s per-check wall, and the count only grows"
    )


def test_l34_log_stays_in_route_order_despite_concurrency():
    """Concurrency must not scramble the log: an operator reads it top-down to
    find where the sweep went wrong. Results are collected, then logged in
    route order."""
    rules = [f"/api/r{i:02d}" for i in range(12)]
    ctx = _FakeCtx(_routes(*rules))
    checks.l34_full_route_smoke(ctx)
    seen = [r for r in rules if r in ctx.log_text]
    positions = [ctx.log_text.index(r) for r in seen]
    assert positions == sorted(positions), (
        "L34's per-route log lines are out of route order under concurrency"
    )


# ── the skip list must not become the silencer ────────────────────

def test_the_five_slow_routes_are_not_in_the_skip_set():
    """The tempting fix. These five are plain jsonify() views, not streams;
    skipping them would make L34 green and delete the finding. Named here so
    a future session cannot quietly add them."""
    for rule in (
        "/api/data/capture_diagnostics",
        "/api/data/replay_validation",
        "/cockpit/api/housekeeping/preview",
        "/cockpit/api/autonomy/queue",
        "/cockpit/api/autonomy/notifications",
    ):
        assert rule not in checks._L34_STREAMING_SKIP, (
            f"{rule} was added to _L34_STREAMING_SKIP. It is not a stream -- "
            "it is a slow read-only view. Silencing it hides a real, "
            "operator-facing performance defect."
        )
