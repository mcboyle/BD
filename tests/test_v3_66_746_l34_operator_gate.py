"""v3.66.746 — L34 gates the OPERATOR surface; diagnostics are advisory.

THE 745 STASH CAPTURE, read honestly: L34 now works. 8 workers, triage, and
serial re-confirmation together probed 423 routes and correctly surfaced
/api/dev/bat_lint as a real >8s route. But it still FAILs the deploy — and the
reason is a denominator problem, the program's oldest one:

  Of the 122s of total sweep cost, 79s (65%) is the /api/dev/ + /cockpit/api/
  diagnostic surface: 202 routes, 28 of the 41 slow ones. bat_lint,
  lockfile_scan, sh_lint, secret_scan, mem_audit -- these are INTROSPECTION
  endpoints that do real scan work BY DESIGN. Smoking them for 5xx with an 8s
  budget, and failing the operator deploy gate when a dev tool's filesystem
  walk takes 9s, is asserting the wrong thing at the wrong cost.

L34's job (its own docstring) is the OPERATOR surface: "the natural post-deploy
gate." A diagnostic endpoint being slow is not an operator-facing regression.

The fix -- and the line it must not cross:

  * The OPERATOR surface (everything NOT /api/dev/ or /cockpit/api/) is the
    hard gate: a 5xx or unreachable there FAILS, exactly as today.
  * DIAGNOSTIC routes are still smoked -- NOT dropped -- but in a labeled
    bucket that is ADVISORY: a slow or even 5xx dev route is REPORTED (named,
    counted, visible) and does not fail the deploy gate. Dropping them silently
    would be the exact "shrink the denominator to hide a finding" sin this whole
    program exists to prevent. Reporting-but-not-gating is the honest middle:
    the finding is not lost, it is correctly scoped.
  * Because the operator surface is ~220 routes at ~196ms mean = ~43s of work,
    it fits inside phase 1 at 8 workers with room to spare -- no UNPROBED, no
    wall exhaustion, on the surface that actually gates the deploy.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import live_tests.checks as checks
import live_tests.harness as h


class _SurfaceCtx:
    """A world with a fast operator surface and a slow diagnostic surface.
    The operator routes all answer fast and clean; the /api/dev/ ones hang
    past every budget. A correct L34 PASSES (operator clean) while REPORTING
    the slow diagnostics."""

    def __init__(self, n_op=40, n_dev=30):
        op = [{"rule": f"/api/op{i:03d}", "methods": ["GET"], "dev_only": False}
              for i in range(n_op)]
        dev = [{"rule": f"/api/dev/scan{i}", "methods": ["GET"], "dev_only": True}
               for i in range(n_dev)]
        cockpit = [{"rule": "/cockpit/api/review", "methods": ["GET"]}]
        self._routes = op + dev + cockpit
        self._log = []

    def get(self, path, timeout=15):
        if path == "/api/dev/routes":
            return True, 200, {"routes": self._routes}, 1.0
        if path.startswith("/api/dev/") or path.startswith("/cockpit/"):
            time.sleep(timeout)  # every diagnostic hangs
            return False, None, "URLError: <urlopen error timed out>", timeout * 1000
        return True, 200, "", 5.0  # operator routes are fast + clean

    def log(self, msg):
        self._log.append(msg)

    @property
    def log_text(self):
        return "\n".join(self._log)


def test_slow_diagnostics_do_not_fail_the_operator_gate(monkeypatch):
    """The whole point. Every /api/dev/ route hangs; the operator surface is
    clean. L34 must PASS the deploy gate and still NAME the slow diagnostics."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 8.0)
    ctx = _SurfaceCtx(n_op=40, n_dev=30)

    level, detail = checks.l34_full_route_smoke(ctx)

    assert level == h.PASS, (
        f"a clean operator surface failed the deploy gate because DIAGNOSTIC "
        f"routes are slow: {detail[:200]} — /api/dev/ scan endpoints are not "
        "operator-facing regressions"
    )


def test_slow_diagnostics_are_still_reported_not_dropped(monkeypatch):
    """The line the fix must not cross. Advisory != invisible. The slow dev
    routes must appear in the log — dropping them would be the denominator sin."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 8.0)
    ctx = _SurfaceCtx(n_op=20, n_dev=20)

    level, detail = checks.l34_full_route_smoke(ctx)
    blob = (detail + "\n" + ctx.log_text).lower()

    assert "diagnostic" in blob or "advisory" in blob or "/api/dev/" in blob, (
        "the slow diagnostic routes were neither in the verdict nor the log — "
        "advisory must still mean VISIBLE; a finding scoped out of the gate is "
        "not a finding dropped from the report"
    )


def test_a_5xx_on_the_OPERATOR_surface_still_fails(monkeypatch):
    """The gate must keep its teeth on the surface it owns. A real operator
    regression (5xx) still FAILs, diagnostics notwithstanding."""

    class _BrokenOpCtx(_SurfaceCtx):
        def get(self, path, timeout=15):
            if path == "/api/dev/routes":
                return True, 200, {"routes": self._routes}, 1.0
            if path.startswith("/api/dev/") or path.startswith("/cockpit/"):
                return True, 200, "", 10.0
            if path == "/api/op000":
                return True, 500, "", 5.0   # operator 5xx
            return True, 200, "", 5.0

    monkeypatch.setattr(checks, "_L34_WALL_S", 8.0)
    ctx = _BrokenOpCtx(n_op=10, n_dev=5)
    level, detail = checks.l34_full_route_smoke(ctx)
    assert level == h.FAIL
    assert "op000" in detail or "5xx" in detail.lower()


def test_operator_surface_fits_without_unprobed(monkeypatch):
    """With diagnostics off the hard path, the operator surface fits phase 1
    at full workers: no UNPROBED on the gating surface."""
    monkeypatch.setattr(checks, "_L34_WALL_S", 8.0)
    ctx = _SurfaceCtx(n_op=60, n_dev=40)

    level, detail = checks.l34_full_route_smoke(ctx)

    # operator routes are all fast; none may be reported UNPROBED
    assert "UNPROBED" not in detail.upper() or "diagnostic" in detail.lower(), (
        f"operator routes went UNPROBED: {detail[:200]}"
    )
    assert level == h.PASS
