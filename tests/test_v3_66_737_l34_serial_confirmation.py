"""v3.66.737 — L34: a concurrent probe must not report its OWN load as a finding.

THE BUG I SHIPPED AT 734, CAUGHT BY THE 735 CAPTURE.

734 made L34 concurrent so it could finish 523 routes inside the harness's 90s
wall. It did finish -- and then reported **37 of 523 routes over the 8s budget**.

That number is contaminated. Measured SERIALLY in the 733 capture:

    /api/community_scrapers/index   4090ms
    /api/data/kb_analytics          3613ms
    /api/data/capture_analytics     ~3600ms

All three appear in 735's ">8s" list. A route that answers in 4.1s when probed
alone is not an 8s route: it doubled because an 8-way fan-out put load on the
very app it was measuring. The instrument changed the thing it measured and
reported its own contention back as a defect -- the exact shape KB_JUDGMENT names
in bd-footguns ("the fan-out WAS the slowdown; fan-out is a bet on cores you
have"), committed while fixing a different instance of it.

THE FIX: phase 1 (concurrent) is TRIAGE and produces HYPOTHESES. Every non-ok
route is then RE-PROBED SERIALLY against a quiesced app, and only a route that
STILL misbehaves alone is reported. A route that recovers is logged as RECOVERED
-- our own load, said out loud -- and is NOT a finding.

Concurrency buys the wall clock. It does not buy a verdict.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks
import live_tests.harness as h


class _FlakyCtx:
    """A ctx whose route answers SLOWLY the first time (as it would under the
    concurrent fan-out) and FINE on the serial re-probe -- i.e. the route was
    never slow, our own load was."""

    def __init__(self, routes, contended=(), always_slow=()):
        self._routes = [{"rule": r, "methods": ["GET"]} for r in routes]
        self._contended = set(contended)
        self._always_slow = set(always_slow)
        self._hits = {}
        self._log = []

    def get(self, path, timeout=15):
        if path == "/api/dev/routes":
            return True, 200, {"routes": self._routes}, 1.0
        n = self._hits.get(path, 0)
        self._hits[path] = n + 1
        if path in self._always_slow:
            return False, None, "URLError: <urlopen error timed out>", 8000.0
        if path in self._contended and n == 0:
            # first probe = during the fan-out = times out
            return False, None, "URLError: <urlopen error timed out>", 8000.0
        return True, 200, "", 40.0

    def log(self, msg):
        self._log.append(msg)

    @property
    def log_text(self):
        return "\n".join(self._log)


def test_a_route_that_recovers_serially_is_not_a_finding():
    """The 735 false positives. /api/community_scrapers/index answered in 4090ms
    when probed alone; it only 'exceeded' under our own fan-out."""
    ctx = _FlakyCtx(
        ["/api/health", "/api/community_scrapers/index", "/api/data/kb_analytics"],
        contended=["/api/community_scrapers/index", "/api/data/kb_analytics"],
    )
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.PASS, (
        "routes that recover when probed alone were reported as findings — "
        f"L34 is reporting its own contention. got {level} — {detail}"
    )
    assert "RECOVERED" in ctx.log_text, (
        "L34 silently dropped the phase-1 flags. It must SAY that it flagged "
        "them and that the flag was its own load — a probe that quietly "
        "discards its own false positives teaches nobody anything."
    )


def test_a_route_slow_on_a_quiet_app_is_STILL_a_finding():
    """The re-confirmation must not become a laundry. A genuinely slow
    OPERATOR route survives phase 2 and still FAILS.

    v3.66.746: the route was /cockpit/api/housekeeping/preview, which is now
    on the ADVISORY diagnostic surface (a cockpit tool's slowness does not fail
    an operator deploy) -- so this test moved to an operator route, and a
    companion below pins that the cockpit case is advisory-but-visible."""
    ctx = _FlakyCtx(
        ["/api/health", "/api/housekeeping/preview"],
        always_slow=["/api/housekeeping/preview"],
    )
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.FAIL, f"a genuinely slow operator route must still FAIL; got {level}"
    assert "/api/housekeeping/preview" in detail
    assert "alone" in detail.lower(), (
        "the verdict must say the route was slow WHEN PROBED ALONE — otherwise "
        "the reader cannot tell it from a contention artifact"
    )


def test_a_slow_DIAGNOSTIC_route_is_advisory_not_a_gate_failure():
    """v3.66.746: the twin of the above on the diagnostic surface. A slow
    /cockpit or /api/dev route is REPORTED (visible) but does NOT fail the
    deploy gate -- an introspection tool's scan time is not operator-facing."""
    ctx = _FlakyCtx(
        ["/api/health", "/cockpit/api/housekeeping/preview"],
        always_slow=["/cockpit/api/housekeeping/preview"],
    )
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.PASS, (
        f"a slow diagnostic route failed the operator gate; got {level} — {detail}"
    )
    blob = detail + "\n" + ctx.log_text
    assert "housekeeping/preview" in blob or "advisory" in blob.lower(), (
        "the slow diagnostic must still be REPORTED — advisory means visible, "
        "not dropped"
    )


def test_suspects_are_re_probed_serially():
    """Phase 2 must actually re-probe: exactly one extra GET per suspect, and
    none for the healthy routes."""
    ctx = _FlakyCtx(
        ["/api/health", "/api/slow"],
        contended=["/api/slow"],
    )
    checks.l34_full_route_smoke(ctx)
    assert ctx._hits["/api/slow"] == 2, (
        f"suspect was probed {ctx._hits['/api/slow']}x — expected a phase-1 "
        "probe plus exactly one serial re-probe"
    )
    assert ctx._hits["/api/health"] == 1, (
        "a healthy route was re-probed — phase 2 must only re-probe suspects, "
        "or it costs as much as the serial sweep we replaced"
    )


def test_a_5xx_is_also_serially_confirmed():
    """A 5xx under load could equally be a load artifact. Confirm it too."""

    class _Ctx(_FlakyCtx):
        def get(self, path, timeout=15):
            if path == "/api/dev/routes":
                return True, 200, {"routes": self._routes}, 1.0
            n = self._hits.get(path, 0)
            self._hits[path] = n + 1
            if path == "/api/wobbly":
                # 500 under load, fine when quiet -> NOT a finding
                return (False, 500, None, 10.0) if n == 0 else (True, 200, "", 20.0)
            return True, 200, "", 20.0

    ctx = _Ctx(["/api/health", "/api/wobbly"])
    level, detail = checks.l34_full_route_smoke(ctx)
    assert ctx._hits["/api/wobbly"] == 2, "a 5xx was not serially re-confirmed"
    assert level == h.PASS, (
        f"a 5xx that vanishes on a quiet app is our own load, not a defect; "
        f"got {level} — {detail}"
    )
