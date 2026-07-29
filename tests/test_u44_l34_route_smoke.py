"""U44 — L34 full-route-smoke: streaming skip + 4xx-is-OK semantics.

L34 walks every parameter-free GET route on the live app and confirms
none returns a 5xx or is unreachable. v3.63.4 carries two fixes:

  Fix 1 (unit #1): streaming routes must be skipped, not GET'd. A route
  that returns 200 then blocks the response body forever (SSE, log
  tail, progress stream) wedges the loop, because
  `urllib.request.urlopen(timeout=N)` only bounds the connect+headers
  phase — `r.read()` on a streamer is unbounded. The live-test
  harness's per-check wall (T55, 60s default) would eventually kill
  the check with a misleading FAIL on what is normal endpoint
  behaviour.

  Fix 2 (unit #2): a route that responded with a 4xx must be treated
  as wired, not "unreachable." The harness's `ctx.get` returns
  `(False, code, None, ms)` on `urllib.error.HTTPError` — i.e. the
  route DID respond, just with a non-2xx. L34's docstring intent
  ("A 4xx is treated as OK — the route is wired and responding (it
  may simply require auth)") was being violated by the loop because
  the `not rok` branch lumped 4xx in with unreachable. Post-fix,
  4xx → wired (only 5xx counts against the smoke); a truly
  unreachable response (`rstatus is None`) still buckets as
  unreachable.

This file pins three properties so the regression cannot return
silently:

  1. L34 is registered, non-disruptive, in the live-test registry.
  2. The `_L34_STREAMING_SKIP` set exists in `live_tests/checks.py`,
     and the source body of `l34_full_route_smoke` references it
     in the target-filter expression. (A reorder that moves the
     skip below the loop would still pass at runtime when the live
     app does not yet expose a new streamer, but the diagnostic
     value of the gate is its placement — pinning the source-grep
     keeps the gate where it can do work.)
  3. `/api/stream` — the v3.43.34 SSE endpoint, alphabetically the
     first parameter-free streamer in the URL map — is in the skip
     set. A future addition of another streamer should be added
     to `_L34_STREAMING_SKIP` and to the assertion below.

Functional verification of the skip running in-process against a
real Flask app is left to L35 (`test_u43_sse_live_test.py`), which
already exercises an SSE connection end-to-end against a local
server — duplicating that here would double-count without adding
signal.
"""
from __future__ import annotations

import inspect
import os
import re
import sys

# tests/ is not a package — load helpers via file-relative sys.path.
sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks  # noqa: F401  (registers the checks)
import live_tests.harness as h


# Read the harness tuple rather than restating it: a local copy goes stale
# the moment the verdict vocabulary grows (it did -- h.NA was added and every
# copy of this line silently began rejecting a valid level).
_LEVELS = h._LEVELS


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l34_is_registered():
    t = _get_test("L34")
    assert t is not None, "L34 missing from live-test registry"
    assert t.name == "full-route-smoke"


def test_l34_is_not_disruptive():
    # L34 is a read-only GET sweep; it must never be flagged disruptive
    # or it stops running outside an explicit `include_disruptive=True`.
    t = _get_test("L34")
    assert t is not None
    assert t.disruptive is False


# ── the streaming-route skip set ───────────────────────────────────

def test_streaming_skip_set_exists():
    assert hasattr(checks, "_L34_STREAMING_SKIP"), (
        "_L34_STREAMING_SKIP missing — L34 will wedge on /api/stream "
        "again (pre-v3.63.4 regression)"
    )
    skip = checks._L34_STREAMING_SKIP
    # Use a frozenset to make accidental mutation noisy.
    assert isinstance(skip, (frozenset, set)), (
        f"_L34_STREAMING_SKIP must be a (frozen)set, got {type(skip)}"
    )


def test_api_stream_is_in_skip_set():
    # /api/stream is the v3.43.34 SSE endpoint. It is alphabetically
    # the first parameter-free streaming route in the URL map, and
    # the one that wedged L34 in the v3.63.3 capture.
    assert "/api/stream" in checks._L34_STREAMING_SKIP


def test_skip_set_only_contains_streaming_routes():
    # If this list ever grows to non-streaming routes, the gate has
    # been misused. Streaming routes ALWAYS start with /api/stream,
    # /stream, /events, or contain "stream" / "events" in the last
    # path segment. Loose, but enough to catch a typo or a wrong add.
    for rule in checks._L34_STREAMING_SKIP:
        last = rule.rsplit("/", 1)[-1]
        assert ("stream" in rule.lower()
                or "events" in rule.lower()
                or "sse" in last.lower()), (
            f"_L34_STREAMING_SKIP contains {rule!r}, which does not "
            "look like a streaming route — gate is being misused"
        )


# ── source-level gate: the filter applies the skip ────────────────

def test_l34_filter_applies_skip_set():
    # Grep the function source. The target-filter list comprehension
    # MUST reference _L34_STREAMING_SKIP, or the skip set is defined
    # and ignored (silent regression — the wedge returns even though
    # this file's runtime tests pass).
    src = inspect.getsource(checks.l34_full_route_smoke)
    # Be permissive on whitespace / line breaks; just require the
    # name appears inside the function body.
    assert "_L34_STREAMING_SKIP" in src, (
        "l34_full_route_smoke does not reference _L34_STREAMING_SKIP — "
        "the skip set is defined but the loop does not honour it"
    )
    # And the reference must be in a filter context — i.e. paired
    # with a `not in` or `in ... SKIP` — not just commented out.
    # A simple regex over the source body is enough to catch a delete
    # that left only the constant.
    filter_re = re.compile(r"not\s+in\s+_L34_STREAMING_SKIP"
                           r"|_L34_STREAMING_SKIP\s*$",
                           re.MULTILINE)
    assert filter_re.search(src), (
        "l34_full_route_smoke names _L34_STREAMING_SKIP but does not "
        "use it as a filter — gate is decorative, wedge will return"
    )


# ── helper: returning a value from l34 with a fake ctx ────────────

class _FakeCtx:
    """Stand-in Context that does NOT need a live app — fixed route
    table, fixed responses. Lets us assert L34's behaviour around the
    skip set without running against the deployment."""

    def __init__(self, routes, responses):
        self._routes = routes
        self._responses = responses
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


def test_l34_does_not_get_streaming_routes():
    # Fake route table includes /api/stream alongside two normal
    # routes. Run L34 with a fake ctx; assert /api/stream was never
    # GET'd by the loop. The /api/dev/routes enumeration call is
    # expected; we filter it out of the assertion.
    routes = [
        {"rule": "/api/health", "methods": ["GET"]},
        {"rule": "/api/stream", "methods": ["GET"]},
        {"rule": "/api/status", "methods": ["GET"]},
    ]
    ctx = _FakeCtx(routes, responses={})
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level in _LEVELS, f"unexpected level {level!r}"
    # Loop GETs (everything except the enumeration call):
    loop_gets = [p for p in ctx._gets if p != "/api/dev/routes"]
    assert "/api/stream" not in loop_gets, (
        f"L34 GET'd /api/stream — skip set not honoured. "
        f"gets={loop_gets}"
    )
    # Sanity: the non-streaming routes WERE hit.
    assert "/api/health" in loop_gets
    assert "/api/status" in loop_gets


def test_l34_log_announces_the_skip():
    # When a streaming route is in the live app's route table, L34's
    # log line must announce the skip so an operator reading the
    # capture knows L34 deliberately didn't smoke it.
    routes = [
        {"rule": "/api/health", "methods": ["GET"]},
        {"rule": "/api/stream", "methods": ["GET"]},
    ]
    ctx = _FakeCtx(routes, responses={})
    checks.l34_full_route_smoke(ctx)
    log = ctx.log_text
    assert "/api/stream" in log, (
        "L34 did not announce the /api/stream skip in its log — "
        "operator cannot tell skip from miss"
    )
    assert "streaming" in log.lower() or "skipped" in log.lower(), (
        "L34 skip announcement does not name what kind of skip it is"
    )


# ── Fix 2: a 4xx must be treated as wired, not unreachable ────────

def test_l34_treats_4xx_as_wired_not_unreachable():
    # Real-world reproduction: the v3.63.3 stash capture saw 10 routes
    # bucketed as "unreachable" with body None. None body == ctx.get
    # caught urllib.error.HTTPError — the route returned a 4xx. L34's
    # docstring promises 4xx is treated as wired; the loop must agree.
    routes = [
        {"rule": "/api/health", "methods": ["GET"]},      # 200 OK
        {"rule": "/api/quick_add", "methods": ["GET"]},   # 400 (missing param)
        {"rule": "/api/route_preview", "methods": ["GET"]},  # 401 (auth)
        {"rule": "/api/status", "methods": ["GET"]},      # 200 OK
    ]
    responses = {
        "/api/quick_add":     (False, 400, None, 5.0),
        "/api/route_preview": (False, 401, None, 5.0),
    }
    ctx = _FakeCtx(routes, responses=responses)
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.PASS, (
        f"L34 should PASS when only 4xx-wired routes are present "
        f"(no 5xx, no unreachable, no timeout); got {level} — {detail}"
    )
    # And the failure verb "unreachable" must NOT appear in the log
    # for these routes — they were wired.
    log = ctx.log_text
    assert "UNREACHABLE  /api/quick_add" not in log, (
        "L34 logged /api/quick_add as UNREACHABLE despite 400 response"
    )
    assert "UNREACHABLE  /api/route_preview" not in log, (
        "L34 logged /api/route_preview as UNREACHABLE despite 401"
    )
    # The 4xx routes should be logged as normal hits with their status.
    assert "400" in log and "/api/quick_add" in log
    assert "401" in log and "/api/route_preview" in log


def test_l34_5xx_still_counts_as_server_error():
    # 4xx is wired-OK. 5xx is still a real failure, even via the
    # HTTPError path. Guard against a fix that lumps all HTTPError
    # codes into the wired bucket.
    routes = [
        {"rule": "/api/health", "methods": ["GET"]},
        {"rule": "/api/broken", "methods": ["GET"]},   # 500
    ]
    responses = {
        "/api/broken": (False, 500, None, 5.0),
    }
    ctx = _FakeCtx(routes, responses=responses)
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.FAIL, (
        f"L34 must FAIL when a route 500s, even via HTTPError. "
        f"got {level} — {detail}"
    )
    assert "/api/broken" in detail
    assert "500" in detail


def test_l34_truly_unreachable_still_buckets_as_unreachable():
    # When the harness's `ctx.get` returns (False, None, "<exc>", ms)
    # — i.e. a connection-level failure (refused, DNS, etc.), not an
    # HTTPError — that IS unreachable. The 4xx-wired fix must not
    # silently turn connection failures into wired-OK.
    routes = [
        {"rule": "/api/health", "methods": ["GET"]},
        {"rule": "/api/gone", "methods": ["GET"]},
    ]
    responses = {
        # rstatus is None — the connection itself failed.
        "/api/gone": (False, None, "ConnectionRefusedError: [Errno 111]", 5.0),
    }
    ctx = _FakeCtx(routes, responses=responses)
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.FAIL, (
        f"L34 must FAIL when a route is truly unreachable. "
        f"got {level} — {detail}"
    )
    assert "/api/gone" in detail
    assert "unreachable" in detail.lower()


# ── source-level gate: the loop discriminates on rstatus ─────────

def test_l34_loop_discriminates_on_rstatus():
    # Grep the function source. The not-ok branch MUST check whether
    # `rstatus is not None` before bucketing into unreachable, or the
    # 4xx-is-OK promise is silently violated again. Catches a refactor
    # that "simplifies" the branch back to its pre-v3.63.4 shape.
    src = inspect.getsource(checks.l34_full_route_smoke)
    # Look for an elif/branch that compares rstatus to None within
    # the loop body.
    discriminator = re.compile(
        r"elif\s+rstatus\s+is\s+not\s+None"
        r"|if\s+rstatus\s+is\s+not\s+None",
        re.MULTILINE
    )
    assert discriminator.search(src), (
        "l34_full_route_smoke does not discriminate on `rstatus is "
        "not None` — a 4xx response will be misclassified as "
        "unreachable (pre-v3.63.4 regression)"
    )
