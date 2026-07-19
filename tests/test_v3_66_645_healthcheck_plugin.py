"""v3.66.645 -- X-PLUG-1: @healthcheck plugin kind (K7) + selftest consumption.

A plugin can contribute a self-test probe (a zero-arg fn returning {ok, message}).
The host runs every probe under full error isolation (a throwing probe is isolated,
never a crash) and selftest.check_plugin_health() folds the results into one row on
the health page. Mirrors the K5 sink-kind contract (CAP constant + known_events()
documentation + register/list/run + reset isolation).

Sandbox-safe: uses the plugin registry reset, zero-arg tests, no pytest builtins.
"""
from __future__ import annotations

import bulk_downloader.plugins as P
from bulk_downloader import selftest as st


def _reset():
    # the registry reset helper the other plugin-kind tests use
    for cand in ("_reset_registries", "_reset", "reset_for_tests"):
        fn = getattr(P, cand, None)
        if callable(fn):
            fn()
            return
    # fallback: clear the healthcheck registry directly
    P._healthchecks.clear()


def test_healthcheck_capability_documented():
    assert getattr(P, "CAP_HEALTHCHECK", None) == "healthcheck"
    ke = P.known_events()
    assert P.CAP_HEALTHCHECK in ke["capabilities"]


def test_register_and_run_healthcheck_passing():
    _reset()
    try:
        P.register_healthcheck(lambda: {"ok": True, "message": "db reachable"},
                               name="db_probe", priority=10)
        rows = P.run_healthchecks()
        assert any(r["name"] == "db_probe" and r["ok"] for r in rows), rows
        assert "db_probe" in [h["name"] for h in P.list_healthchecks()]
    finally:
        _reset()


def test_run_healthcheck_isolates_a_throwing_probe():
    """A probe that raises must be captured as ok=False, never propagate."""
    _reset()
    try:
        def _boom():
            raise RuntimeError("subsystem down")
        P.register_healthcheck(_boom, name="boom_probe")
        rows = P.run_healthchecks()
        row = next(r for r in rows if r["name"] == "boom_probe")
        assert row["ok"] is False, f"a throwing probe must report ok=False, got {row}"
    finally:
        _reset()


def test_bare_bool_return_is_coerced():
    _reset()
    try:
        P.register_healthcheck(lambda: True, name="bool_ok")
        P.register_healthcheck(lambda: False, name="bool_bad")
        rows = {r["name"]: r for r in P.run_healthchecks()}
        assert rows["bool_ok"]["ok"] is True
        assert rows["bool_bad"]["ok"] is False
    finally:
        _reset()


def test_selftest_folds_plugin_health():
    """selftest.check_plugin_health() folds probe results: OK when all pass,
    WARN when any is unhealthy."""
    _reset()
    try:
        # no probes -> OK / count 0
        rec = st.check_plugin_health()
        assert rec["status"] == st.OK and rec["detail"]["count"] == 0, rec

        P.register_healthcheck(lambda: {"ok": True}, name="ok1")
        rec = st.check_plugin_health()
        assert rec["status"] == st.OK and rec["detail"]["count"] == 1, rec

        P.register_healthcheck(lambda: {"ok": False, "message": "bad"}, name="bad1")
        rec = st.check_plugin_health()
        assert rec["status"] == st.WARN, f"an unhealthy probe should WARN, got {rec}"
    finally:
        _reset()
