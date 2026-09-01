"""Row 425 -- an unobserved bandwidth supervisor must not report ok.

``_check_supervisor`` mapped its own measurement failure to ``SEV_OK`` twice
over: a raising import/``is_enabled`` returned ``SEV_OK`` with an
"unavailable" message, and a raising ``stats()`` was swallowed into
``global_bps=0`` and reported as an ACTIVE supervisor carrying a number
nothing measured.  ``run_checklist`` then counted both in ``summary['ok']``
and rolled ``overall_status`` to ``ok``, so ``GET /api/health/checklist``
reported healthy over a throttle nobody observed.

Every sibling in the same file already maps unavailable to ``SEV_WARN``
(``_check_ytdlp``, ``_check_circuit_breakers``, ``_check_account_health``,
``_check_bitrot``), which is this file's rendering of CLAUDE.md A7's
UNKNOWN-not-OK.

THE MIRROR DEFECT IS TESTED TOO.  A check that answers WARN when it CAN
measure is as broken as one that answered OK when it could not, so the two
healthy controls below assert an enabled supervisor still reports SEV_OK
active with its MEASURED global_bps and a disabled one still reports SEV_OK
idle.  The fix cannot launder every path to warn.
"""
from __future__ import annotations

import pytest

from bulk_downloader import healthcheck as hc


BD_GATE_SCOPE = "module"


class _Boom(RuntimeError):
    """A distinctive exception type, so a laundered unrelated failure cannot
    satisfy the assertions below by accident."""


def _install_supervisor(monkeypatch, *, is_enabled, stats):
    """Point ``_check_supervisor``'s function-local import at a stub.

    ``_check_supervisor`` does ``from . import download_supervisor as _sup``
    at CALL time, which resolves the already-imported module object, so
    patching that module's attributes is the real seam.  Returns a counter
    dict the caller asserts against BEFORE any verdict.
    """
    from bulk_downloader import download_supervisor as sup

    calls = {"is_enabled": 0, "stats": 0}

    def _is_enabled():
        calls["is_enabled"] += 1
        return is_enabled()

    def _stats():
        calls["stats"] += 1
        return stats()

    monkeypatch.setattr(sup, "is_enabled", _is_enabled)
    monkeypatch.setattr(sup, "stats", _stats)
    return calls


# ── RED arm 1: the supervisor cannot be queried at all ────────────────────

def test_unqueryable_supervisor_reports_warn_not_ok(monkeypatch):
    def _raise():
        raise _Boom("supervisor module wedged")

    calls = _install_supervisor(
        monkeypatch, is_enabled=_raise, stats=lambda: {"config": {}})

    result = hc._check_supervisor()

    # PRECONDITION, asserted before the verdict: the injected failure really
    # fired, exactly once, and nothing later in the function ran.
    assert calls["is_enabled"] == 1, calls
    assert calls["stats"] == 0, calls

    assert result["severity"] == hc.SEV_WARN, result
    # The refusal carries the boundary's own words rather than collapsing
    # distinct failures into one diagnostic (CLAUDE.md A7).
    assert "unavailable" in result["message"], result
    assert "supervisor module wedged" in result["message"], result


# ── RED arm 2: enabled, but stats() cannot be read ────────────────────────

def test_unreadable_stats_do_not_fabricate_a_measured_rate(monkeypatch):
    def _raise():
        raise _Boom("stats table unreadable")

    calls = _install_supervisor(
        monkeypatch, is_enabled=lambda: True, stats=_raise)

    result = hc._check_supervisor()

    # PRECONDITION: the enabled branch was taken and stats() really raised.
    assert calls["is_enabled"] == 1, calls
    assert calls["stats"] == 1, calls

    assert result["severity"] == hc.SEV_WARN, result
    # The old text claimed "active (global_bps=0)" -- an unmeasured number
    # rendered as a measurement.  It must not come back.
    assert "global_bps=0" not in result["message"], result
    assert "stats table unreadable" in result["message"], result


# ── RED arm 3: the rollup that operators actually read ────────────────────

def test_run_checklist_rolls_an_unobserved_supervisor_out_of_ok(monkeypatch):
    """The checklist is the surface; a warn that never reaches it is invisible."""
    def _raise():
        raise _Boom("supervisor module wedged")

    calls = _install_supervisor(
        monkeypatch, is_enabled=_raise, stats=lambda: {"config": {}})

    # Hold every SIBLING at a measured SEV_OK so the rollup verdict can only
    # be caused by the supervisor.  Without this the assertion could pass
    # because some unrelated check on this host is warning -- a green for the
    # wrong reason.
    for name in ("_check_database", "_check_disk", "_check_playwright",
                 "_check_ytdlp", "_check_ffmpeg", "_check_recent_failures",
                 "_check_circuit_breakers", "_check_account_health",
                 "_check_bitrot"):
        monkeypatch.setattr(
            hc, name,
            (lambda *_a, **_k: {"severity": hc.SEV_OK, "message": "held ok"}))

    report = hc.run_checklist(s_cfg={})

    # PRECONDITION: the injected failure fired, and the supervisor check is
    # actually present in the report we are judging (nonzero denominator).
    assert calls["is_enabled"] == 1, calls
    entries = [c for c in report["checks"] if c["name"] == "supervisor"]
    assert len(entries) == 1, report["checks"]
    assert report["check_count"] == 10, report["check_count"]
    assert (report["summary"]["ok"] + report["summary"]["warn"]
            + report["summary"]["fail"]) == 10, report["summary"]

    assert entries[0]["status"] == "warn", entries[0]
    assert report["summary"]["warn"] == 1, report["summary"]
    assert report["summary"]["ok"] == 9, report["summary"]
    assert report["overall_status"] == "warn", report["overall_status"]


# ── NEGATIVE CONTROLS: the healthy paths still read healthy ───────────────

def test_healthy_enabled_supervisor_still_reports_ok_with_its_measured_rate(
        monkeypatch):
    """THE MIRROR DEFECT.  A guard that refuses a healthy subject is the same
    bug wearing the other face."""
    calls = _install_supervisor(
        monkeypatch,
        is_enabled=lambda: True,
        stats=lambda: {"config": {"global_bps": 4_194_304}})

    result = hc._check_supervisor()

    assert calls["is_enabled"] == 1 and calls["stats"] == 1, calls
    assert result["severity"] == hc.SEV_OK, result
    assert "active" in result["message"], result
    assert "global_bps=4194304" in result["message"], result
    assert "unavailable" not in result["message"], result


def test_disabled_supervisor_still_reports_ok_idle(monkeypatch):
    calls = _install_supervisor(
        monkeypatch, is_enabled=lambda: False, stats=lambda: {"config": {}})

    result = hc._check_supervisor()

    # PRECONDITION: the disabled branch was taken -- stats() must NOT be
    # consulted for an idle supervisor, or "idle" would be a measurement of
    # something else.
    assert calls["is_enabled"] == 1 and calls["stats"] == 0, calls
    assert result["severity"] == hc.SEV_OK, result
    assert "idle" in result["message"], result


def test_healthy_supervisor_keeps_the_checklist_overall_ok(monkeypatch):
    """The rollup half of the mirror defect: a measured supervisor must not
    drag ``overall_status`` off ok."""
    calls = _install_supervisor(
        monkeypatch,
        is_enabled=lambda: True,
        stats=lambda: {"config": {"global_bps": 1024}})
    for name in ("_check_database", "_check_disk", "_check_playwright",
                 "_check_ytdlp", "_check_ffmpeg", "_check_recent_failures",
                 "_check_circuit_breakers", "_check_account_health",
                 "_check_bitrot"):
        monkeypatch.setattr(
            hc, name,
            (lambda *_a, **_k: {"severity": hc.SEV_OK, "message": "held ok"}))

    report = hc.run_checklist(s_cfg={})

    assert calls["is_enabled"] == 1 and calls["stats"] == 1, calls
    assert report["summary"]["ok"] == 10, report["summary"]
    assert report["summary"]["warn"] == 0, report["summary"]
    assert report["overall_status"] == "ok", report["overall_status"]


@pytest.mark.parametrize("severity", [hc.SEV_OK, hc.SEV_WARN])
def test_severity_labels_are_the_ones_the_rollup_reports(severity):
    """Guards the assertions above against a renamed label silently making
    every status comparison vacuous."""
    assert hc._SEV_LABEL[severity] in ("ok", "warn")
    assert hc._SEV_LABEL[hc.SEV_OK] == "ok"
    assert hc._SEV_LABEL[hc.SEV_WARN] == "warn"
