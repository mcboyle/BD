"""O1428: scope skips are reported but do not block cutover."""
import io
import json

import pytest
from bulk_downloader import pg_backend as pg

BD_GATE_SCOPE = "module"


def health(*, compared=2921, skipped=25015, skip_reasons=None):
    reasons = {"scope": 23717, "strftime": 1298} if skip_reasons is None else skip_reasons
    return {"mod3": {"dual_write": True, "shadow_read": True,
                     "stats": {"degraded_reason": None},
                     "shadow": {"compared": compared, "skipped": skipped,
                                "diverged": 0, "errors": 0,
                                "skip_reasons": reasons}}}


def test_scope_skips_reported_and_dialect_ratio_judged():
    result = pg.preflight_cutover(health())
    assert result["ok"] is True, result
    checks = result["checks"]
    assert checks["shadow_skipped"] == 25015
    assert checks["shadow_scope_skipped"] == 23717
    assert checks["shadow_dialect_skipped"] == 1298
    assert checks["shadow_skip_ratio"] == 1298 / 4219


@pytest.mark.parametrize("bad", [None, [], {"scope": -1}, {"scope": True},
                                  {"scope": 1.5}, {"scope": 1, "strftime": "2"}])
def test_unverifiable_reasons_refuse(bad):
    payload = health()
    if bad is None:
        del payload["mod3"]["shadow"]["skip_reasons"]
    else:
        payload["mod3"]["shadow"]["skip_reasons"] = bad
    result = pg.preflight_cutover(payload)
    assert result["ok"] is False, result
    assert "skip_reasons" in " ".join(result["reasons"])


def test_scope_greater_than_total_refuses():
    result = pg.preflight_cutover(health(skipped=5, skip_reasons={"scope": 6}))
    assert result["ok"] is False, result
    assert "skip_reasons" in " ".join(result["reasons"])


def test_high_dialect_ratio_refuses_with_distinctive_reason():
    result = pg.preflight_cutover(health(compared=75, skipped=90,
                                        skip_reasons={"scope": 10, "strftime": 80}))
    assert result["ok"] is False, result
    assert ("shadow dialect skip ratio 0.516 is at least 0.5 "
            "(scope skips 10 reported, not counted)") in result["reasons"]


def test_cli_url_reports_scope_dialect_and_ratio(monkeypatch, capsys):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, timeout: Response(json.dumps(health()).encode()))
    assert pg.main(["preflight", "--health", "http://localhost/api/health"]) == 0
    checks = json.loads(capsys.readouterr().out)["checks"]
    assert (checks["shadow_scope_skipped"], checks["shadow_dialect_skipped"],
            checks["shadow_skip_ratio"]) == (23717, 1298, 1298 / 4219)


def test_in_process_uses_same_arithmetic(monkeypatch):
    monkeypatch.setattr(pg, "shadow_stats", lambda: {"compared": 2921,
        "skipped": 25015, "diverged": 0, "errors": 0})
    monkeypatch.setattr(pg, "shadow_skip_reasons", lambda: {"scope": 23717,
        "strftime": 1298})
    monkeypatch.setattr(pg, "dual_write_enabled", lambda: True)
    monkeypatch.setattr(pg, "shadow_read_enabled", lambda: True)
    monkeypatch.setattr(pg, "stats", lambda: {"degraded_reason": None})
    monkeypatch.setattr(pg, "_connect_ok", lambda: True)
    result = pg.preflight_cutover()
    assert result["ok"] is True, result
    assert result["checks"]["shadow_skip_ratio"] == 1298 / 4219
