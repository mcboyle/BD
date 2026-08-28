"""Health measurements distinguish measured-empty from unavailable.

The ffmpeg capability check originally lived here.  Row 334 makes this the
runtime gate for four health surfaces that used an empty collection as both a
successful measurement and their exception fallback.  Every unavailable arm
has a measured-empty/healthy control: failing closed must not become always
closed.

RED at dcd8201 recorded the old runtime values exactly: account census failure
was ``([], severity 0 / '0 account(s) tracked, all healthy', 100.0)``; ffmpeg
capability-list timeout was ``({'missing': []}, severity 0 / 'mpegts + https
ok')``; bitrot issue-query failure was ``([], HTTP 200, {'issues': []})``; and
circuit census failure was ``(severity 0 / 'circuit module unavailable',
wrapper status 'ok')``.
"""
from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

from flask import Flask

from bulk_downloader import healthcheck


BD_GATE_SCOPE = "module"


def test_ffmpeg_capability_flags_broken_binary():
    cap = healthcheck._ffmpeg_capability("/nonexistent/ffmpeg")
    assert cap.get("error"), "a missing/broken ffmpeg binary must report an error"


def test_ffmpeg_capability_real_binary_has_mpegts_https():
    ff = shutil.which("ffmpeg")
    if not ff:
        return  # no ffmpeg here; the presence branch covers that case
    cap = healthcheck._ffmpeg_capability(ff)
    assert not cap.get("error"), f"real ffmpeg should run: {cap}"
    assert cap.get("missing") == [], f"real ffmpeg should support mpegts+https: {cap}"


def test_check_ffmpeg_reflects_capability_not_just_presence():
    if not shutil.which("ffmpeg"):
        return
    r = healthcheck._check_ffmpeg()
    # the healthy path now names the capability it verified (was presence-only)
    assert "mpegts" in r["message"], f"check should reflect capability, not just presence: {r}"


def _call_state(fn):
    try:
        return {"status": "measured", "value": fn()}
    except Exception as exc:
        return {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def test_account_census_failure_is_unknown_to_both_health_consumers(monkeypatch):
    from bulk_downloader import account_health
    from bulk_downloader import account_pool
    from bulk_downloader import alerts_engine

    def unavailable():
        raise OSError("account pool census unavailable")

    monkeypatch.setattr(account_pool, "get_all_pools_status", unavailable)
    observed = {
        "report": _call_state(account_health.report_all),
        "health": healthcheck._check_account_health(None),
        "alert_metric": alerts_engine._evaluate_metric(
            "bd_account_health_min"
        ),
    }

    assert observed["report"]["status"] == "unknown", f"observed={observed!r}"
    assert observed["health"]["severity"] == healthcheck.SEV_WARN
    assert "unknown" in observed["health"]["message"].lower()
    assert observed["alert_metric"] is None


def test_measured_empty_account_census_remains_healthy(monkeypatch):
    from bulk_downloader import account_health
    from bulk_downloader import account_pool
    from bulk_downloader import alerts_engine

    monkeypatch.setattr(account_pool, "get_all_pools_status", lambda: [])
    assert account_health.report_all() == []
    assert healthcheck._check_account_health(None) == {
        "severity": healthcheck.SEV_OK,
        "message": "0 account(s) tracked, all healthy",
    }
    assert alerts_engine._evaluate_metric("bd_account_health_min") == 100.0


def test_ffmpeg_capability_list_failure_is_unknown_not_capable(monkeypatch):
    from bulk_downloader import ffmpeg_bin

    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/probe/ffmpeg")
    monkeypatch.setattr(ffmpeg_bin, "ffprobe", lambda: "/probe/ffprobe")
    for unavailable_option in ("-muxers", "-protocols"):
        def run(command, **_kwargs):
            option = command[-1]
            if option == unavailable_option:
                raise subprocess.TimeoutExpired(command, 10)
            output = {
                "-version": "ffmpeg version probe",
                "-muxers": " E mpegts MPEG-TS (MPEG-2 Transport Stream)",
                "-protocols": "https",
            }[option]
            return SimpleNamespace(returncode=0, stdout=output)

        monkeypatch.setattr(subprocess, "run", run)
        observed = {
            "capability": healthcheck._ffmpeg_capability("/probe/ffmpeg"),
            "health": healthcheck._check_ffmpeg(),
        }

        assert observed["capability"].get("capability_status") == "unknown", (
            f"option={unavailable_option}, observed={observed!r}"
        )
        assert observed["capability"].get("available") is False
        assert observed["health"]["severity"] == healthcheck.SEV_WARN
        assert "unknown" in observed["health"]["message"].lower()


def test_measured_ffmpeg_capabilities_remain_healthy(monkeypatch):
    from bulk_downloader import ffmpeg_bin

    def run(command, **_kwargs):
        output = {
            "-version": "ffmpeg version probe",
            "-muxers": " E mpegts MPEG-TS (MPEG-2 Transport Stream)",
            "-protocols": "https",
        }[command[-1]]
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(ffmpeg_bin, "ffmpeg", lambda: "/probe/ffmpeg")
    monkeypatch.setattr(ffmpeg_bin, "ffprobe", lambda: "/probe/ffprobe")
    cap = healthcheck._ffmpeg_capability("/probe/ffmpeg")
    assert cap["missing"] == []
    assert cap.get("capability_status", "measured") == "measured"
    assert healthcheck._check_ffmpeg() == {
        "severity": healthcheck.SEV_OK,
        "message": "ffmpeg + ffprobe ready (mpegts + https ok)",
    }


class _BrokenConnection:
    def __enter__(self):
        raise OSError("integrity issue inventory unavailable")

    def __exit__(self, *_args):
        return False


class _EmptyConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return self

    def fetchall(self):
        return []


def _bitrot_client(monkeypatch):
    from bulk_downloader import app_bitrot

    monkeypatch.setattr(app_bitrot, "_check_csrf", lambda *_a, **_k: None)
    app = Flask("row-334-bitrot-measurement")
    app.register_blueprint(app_bitrot.bitrot_bp)
    return app.test_client()


def test_bitrot_issue_inventory_failure_is_unknown_and_not_http_200(monkeypatch):
    from bulk_downloader import bitrot
    from bulk_downloader import db

    monkeypatch.setattr(bitrot, "_ensure_integrity_table", lambda: None)
    monkeypatch.setattr(db, "db_conn", lambda: _BrokenConnection())
    direct = _call_state(bitrot.list_issues)
    response = _bitrot_client(monkeypatch).get("/api/bitrot/issues")
    observed = {
        "list": direct,
        "http_status": response.status_code,
        "json": response.get_json(),
    }

    assert observed["list"]["status"] == "unknown", f"observed={observed!r}"
    assert observed["http_status"] == 503
    assert observed["json"]["available"] is False
    assert observed["json"]["inventory_status"] == "unknown"
    assert observed["json"]["issues"] is None
    assert "unavailable" in observed["json"]["error"]


def test_measured_empty_bitrot_issue_inventory_remains_http_200(monkeypatch):
    from bulk_downloader import bitrot
    from bulk_downloader import db

    monkeypatch.setattr(bitrot, "_ensure_integrity_table", lambda: None)
    monkeypatch.setattr(db, "db_conn", lambda: _EmptyConnection())
    assert bitrot.list_issues() == []
    response = _bitrot_client(monkeypatch).get("/api/bitrot/issues")
    assert response.status_code == 200
    assert response.get_json()["issues"] == []


def test_circuit_census_failure_is_warn_unknown_not_literal_ok(monkeypatch):
    from bulk_downloader import circuit_breaker

    def unavailable():
        raise OSError("circuit census unavailable")

    monkeypatch.setattr(circuit_breaker, "report", unavailable)
    direct = healthcheck._check_circuit_breakers()
    wrapped = healthcheck._check("circuit_breakers",
                                 healthcheck._check_circuit_breakers)
    observed = {
        "direct": direct,
        "wrapper_status": wrapped["status"],
        "wrapper_message": wrapped["message"],
    }

    assert observed["direct"]["severity"] == healthcheck.SEV_WARN, (
        f"observed={observed!r}"
    )
    assert "unknown" in observed["direct"]["message"].lower()
    assert observed["wrapper_status"] == "warn"


def test_measured_empty_circuit_census_remains_ok(monkeypatch):
    from bulk_downloader import circuit_breaker

    monkeypatch.setattr(circuit_breaker, "report", lambda: {})
    assert healthcheck._check_circuit_breakers() == {
        "severity": healthcheck.SEV_OK,
        "message": "0 host(s) tracked, none tripped",
    }
    assert healthcheck._check(
        "circuit_breakers", healthcheck._check_circuit_breakers
    )["status"] == "ok"
