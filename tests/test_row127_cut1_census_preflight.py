"""Daemon counter gate for row 127 cut 1."""
import io
import json

import pytest

from bulk_downloader import pg_backend as pg

BD_GATE_SCOPE = "module"


def health(*, compared=100, skipped=0, diverged=0, errors=0,
           degraded=None, dual=True, shadow=True):
    return {"mod3": {"dual_write": dual, "shadow_read": shadow,
                     "stats": {"degraded_reason": degraded},
                     "shadow": {"compared": compared, "skipped": skipped,
                                "diverged": diverged, "errors": errors}}}


def test_daemon_health_passes_at_100_and_below_half_skip_ratio(monkeypatch):
    monkeypatch.setattr(pg, "_connect", lambda: pytest.fail("local PG used"))
    result = pg.preflight_cutover(health(compared=100, skipped=99))
    assert result["ok"] is True, result
    assert result["checks"]["shadow_skip_ratio"] == 99 / 199


@pytest.mark.parametrize("change,expected", [
    ({"compared": 99}, "compared"),
    ({"diverged": 1}, "divergence"),
    ({"errors": 1}, "error"),
    ({"degraded": "write failed"}, "degraded"),
    ({"dual": False}, "dual-write"),
    ({"shadow": False}, "shadow-read"),
    ({"compared": 100, "skipped": 100}, "skip ratio"),
    ({"compared": 100, "skipped": 101}, "skip ratio"),
])
def test_daemon_health_refuses(change, expected):
    result = pg.preflight_cutover(health(**change))
    assert result["ok"] is False, result
    assert expected in " ".join(result["reasons"])


def test_missing_counter_refuses():
    payload = health()
    del payload["mod3"]["shadow"]["errors"]
    result = pg.preflight_cutover(payload)
    assert result["ok"] is False
    assert "shadow.errors" in " ".join(result["reasons"])


def test_url_and_cli_use_daemon_health(monkeypatch, capsys):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    seen = []

    def urlopen(url, timeout):
        seen.append((url, timeout))
        return Response(json.dumps(health()).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert pg.main(["preflight", "--health", "http://127.0.0.1:5555/api/health"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["checks"]["shadow_skip_ratio"] == 0.0
    assert seen == [("http://127.0.0.1:5555/api/health", 5)]

    def unavailable(url, timeout):
        raise OSError("daemon down")

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    assert pg.main(["preflight", "--health", "http://127.0.0.1:5555/api/health"]) == 1
    assert "daemon down" in capsys.readouterr().out


def test_skip_reasons_limited_and_ordered(monkeypatch):
    monkeypatch.setattr(pg, "_shadow_skip_reasons", {})
    for n in range(14):
        for _ in range(n + 1):
            pg._shadow_skip(f"dialect:reason{n}")
    reasons = pg.shadow_skip_reasons()
    assert len(reasons) == 12
    assert list(reasons)[:2] == ["reason13", "reason12"]
    assert "reason0" not in reasons


@pytest.fixture
def in_process_ready(monkeypatch):
    """Exercise the live cutover gate without a DSN or PG connection."""
    monkeypatch.delenv("MOD3_PG_DSN", raising=False)
    monkeypatch.setattr(pg, "dual_write_enabled", lambda: True)
    monkeypatch.setattr(pg, "shadow_read_enabled", lambda: True)
    monkeypatch.setattr(pg, "_connect_ok", lambda: True)
    for key in ("compared", "diverged", "errors", "skipped"):
        monkeypatch.setitem(pg._shadow, key, 0)
    monkeypatch.setitem(pg._shadow, "compared", 100)
    monkeypatch.setitem(pg._stats, "degraded_reason", None)
    return pg


def test_in_process_refuses_sticky_degraded_reason(in_process_ready, monkeypatch):
    monkeypatch.setitem(in_process_ready._stats, "degraded_reason", "write failed")
    result = in_process_ready.preflight_cutover()
    assert result["ok"] is False, result
    assert "mirror degraded: write failed" in result["reasons"]


def test_in_process_refuses_shadow_errors(in_process_ready, monkeypatch):
    monkeypatch.setitem(in_process_ready._shadow, "errors", 1)
    result = in_process_ready.preflight_cutover()
    assert result["ok"] is False, result
    assert "shadow-read recorded 1 error(s)" in result["reasons"]


def test_in_process_clean_counters_pass(in_process_ready):
    result = in_process_ready.preflight_cutover()
    assert result["ok"] is True, result
    assert result["checks"]["shadow_compared"] == 100
