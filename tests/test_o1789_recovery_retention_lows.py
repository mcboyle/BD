"""O1789 recovery-retention lows: F6 MED, F7 F10 F11 F12 F13 LOW (GO-unpark O3335).

Findings: harness-work/O1789/bughunt/recovery-retention/FINDINGS.md, re-measured on 444a05db.

F6  POST /api/retention/apply {dry_run: false} with no confirm_ids deleted every site's policy
    candidates in one request. GREEN: a real apply must name the previewed ids (400 otherwise).
F7  a row whose file retention deleted stayed status='done' and was re-listed by the preview.
    GREEN: a retention-deleted row (real audit row) is no longer a candidate.
F10 confirm_ids ["x"] / [None] -> 500; audit ?limit=abc -> 500, limit=-1 -> unbounded.
    GREEN: 400 with a fixed message; limit bounded.
F11 bitrot start accepted download_dirs ["/etc"]. GREEN: every dir must sit in a download root.
F12 target_mbps "inf" turned the throttle off, "nan" clamped to 1024 B/s. GREEN: 400.
F13 target_mbps / min_age_days "x" -> 500 carrying the exception text. GREEN: 400, field named.

Hermetic: the real blueprints on a bare Flask app, a temp DB and temp roots. No network.
"""
from __future__ import annotations

import os
import time

import pytest
from flask import Flask

from bulk_downloader import app_bitrot, app_retention, bitrot, retention  # noqa: E402
from bulk_downloader import db as _db  # noqa: E402

BD_GATE_SCOPE = "module"

_OLD = "2020-01-01T00:00:00"


@pytest.fixture(autouse=True)
def _isolate(clean_workdir, monkeypatch):
    monkeypatch.setattr(retention, "_tables_ready", False)
    _db.db_init()
    retention._ensure_tables()
    yield clean_workdir


def _mk(path) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * 16)
    return str(path)


def _hist(site, path, ts=_OLD) -> int:
    with _db.db_conn() as cx:
        return cx.execute(
            """INSERT INTO history(site_id, site_name, url, status, filename, file_size, ts)
               VALUES (?,?,?,?,?,?,?)""",
            (site, site, f"https://x/{site}/{time.time_ns()}", "done", path, 16, ts),
        ).lastrowid


def _rt_client(monkeypatch, s_cfg):
    monkeypatch.setattr(app_retention, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(app_retention, "_app_s_cfg", lambda: s_cfg)
    app = Flask("rrl_retention")
    app.register_blueprint(app_retention.retention_bp)
    return app.test_client()


def _two_sites(tmp_path):
    fa = _mk(tmp_path / "liba" / "a.mp4")
    fb = _mk(tmp_path / "libb" / "b.mp4")
    s_cfg = {"rrl-a": {"retention_days": 30, "download_dir": str(tmp_path / "liba")},
             "rrl-b": {"retention_days": 30, "download_dir": str(tmp_path / "libb")}}
    return s_cfg, fa, _hist("rrl-a", fa), fb, _hist("rrl-b", fb)


# ── F6 ──────────────────────────────────────────────────────────────────────

def test_f6_real_apply_without_confirm_ids_is_refused(monkeypatch, tmp_path):
    s_cfg, fa, _ia, fb, _ib = _two_sites(tmp_path)
    c = _rt_client(monkeypatch, s_cfg)
    r = c.post("/api/retention/apply", json={"dry_run": False})
    gone = [f for f in (fa, fb) if not os.path.exists(f)]
    assert not gone, (
        f"RRL-F6_UNBOUND_SWEEP: one POST without confirm_ids deleted {gone}: "
        f"{r.status_code} {r.get_json()}")
    assert r.status_code == 400, (r.status_code, r.get_json())
    assert "confirm_ids" in r.get_json()["error"], r.get_json()


def test_f6_control_bound_apply_deletes_only_the_confirmed_row(monkeypatch, tmp_path):
    s_cfg, fa, ia, fb, _ib = _two_sites(tmp_path)
    c = _rt_client(monkeypatch, s_cfg)
    r = c.post("/api/retention/apply", json={"dry_run": False, "confirm_ids": [ia], "site_id": "rrl-a"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert not os.path.exists(fa) and os.path.exists(fb), r.get_json()


def test_f6_control_dry_run_without_confirm_ids_still_previews(monkeypatch, tmp_path):
    s_cfg, fa, _ia, fb, _ib = _two_sites(tmp_path)
    c = _rt_client(monkeypatch, s_cfg)
    r = c.post("/api/retention/apply", json={})
    assert r.status_code == 200 and r.get_json()["dry_run"] is True, r.get_json()
    assert r.get_json()["total_candidates"] == 2 and os.path.exists(fa) and os.path.exists(fb)


# ── F7 ──────────────────────────────────────────────────────────────────────

def test_f7_deleted_row_is_not_listed_again_by_the_preview(monkeypatch, tmp_path):
    s_cfg, fa, ia, _fb, _ib = _two_sites(tmp_path)
    res = retention.apply_retention(s_cfg, dry_run=False, confirm_ids=[ia], site_id="rrl-a")
    assert res["total_deleted"] == 1 and not os.path.exists(fa), res
    c = _rt_client(monkeypatch, s_cfg)
    pv = c.get("/api/retention/preview/rrl-a").get_json()
    assert ia not in [x["id"] for x in pv["candidates"]], (
        f"RRL-F7_RELISTED: row {ia} whose file retention deleted is still a candidate: {pv}")
    assert pv["candidate_count"] == 0, pv


def test_f7_control_dry_run_does_not_hide_the_row(monkeypatch, tmp_path):
    s_cfg, fa, ia, _fb, _ib = _two_sites(tmp_path)
    retention.apply_retention(s_cfg, dry_run=True, site_id="rrl-a")
    assert [x["id"] for x in retention.find_candidates("rrl-a", s_cfg["rrl-a"])] == [ia]
    assert os.path.exists(fa)


# ── F10 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [["x"], [None], [True], [{"id": 1}], ["\u00b2"], ["9" * 5000]])
def test_f10_malformed_confirm_ids_are_a_400(monkeypatch, tmp_path, bad):
    s_cfg, fa, _ia, fb, _ib = _two_sites(tmp_path)
    c = _rt_client(monkeypatch, s_cfg)
    r = c.post("/api/retention/apply", json={"dry_run": False, "confirm_ids": bad})
    assert r.status_code == 400, (
        f"RRL-F10_CONFIRM_IDS: confirm_ids={bad!r} -> {r.status_code} {r.get_json()}")
    assert os.path.exists(fa) and os.path.exists(fb)


@pytest.mark.parametrize("limit", ["abc", "-1", "0", "1.5", "\u00b2", "9" * 5000])
def test_f10_bad_audit_limit_is_a_400(monkeypatch, limit):
    c = _rt_client(monkeypatch, {})
    r = c.get(f"/api/retention/audit?limit={limit}")
    assert r.status_code == 400, (
        f"RRL-F10_AUDIT_LIMIT: limit={limit!r} -> {r.status_code} {r.get_json()}")
    assert "limit" in r.get_json()["error"], r.get_json()


def test_f10_audit_limit_is_capped_and_defaults(monkeypatch):
    seen = []
    monkeypatch.setattr(retention, "audit_log", lambda **k: seen.append(k["limit"]) or [])
    c = _rt_client(monkeypatch, {})
    assert c.get("/api/retention/audit?limit=10000000").status_code == 200
    assert c.get("/api/retention/audit").status_code == 200
    assert c.get("/api/retention/audit?limit=7").status_code == 200
    assert seen == [app_retention.AUDIT_LIMIT_MAX, 100, 7], seen


# ── F11 F12 F13 (bitrot) ────────────────────────────────────────────────────

class _FakeScrubber:
    def __init__(self):
        self.pacer = bitrot.AdaptiveIOPacer()
        self.starts = []

    def start(self, **kw):
        self.starts.append(kw)
        return True

    def status(self):
        return {"state": "fake"}


def _br_client(monkeypatch, tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    from bulk_downloader import app_state
    monkeypatch.setattr(app_state, "s_cfg", {"rrl-br": {"download_dir": str(root)}})
    fake = _FakeScrubber()
    monkeypatch.setattr(bitrot, "get_scrubber", lambda: fake)
    monkeypatch.setattr(app_bitrot, "_check_csrf", lambda *a, **k: None)
    app = Flask("rrl_bitrot")
    app.register_blueprint(app_bitrot.bitrot_bp)
    return app.test_client(), fake, root


@pytest.mark.parametrize("dirs", [["/etc"], "/etc", ["lib"]])
def test_f11_start_refuses_dirs_outside_the_download_roots(monkeypatch, tmp_path, dirs):
    c, fake, _root = _br_client(monkeypatch, tmp_path)
    r = c.post("/api/bitrot/scan", json={"action": "start", "download_dirs": dirs})
    assert not fake.starts, (
        f"RRL-F11_UNBOUND_DIRS: start accepted download_dirs={dirs!r}: {fake.starts}")
    assert r.status_code == 400, (r.status_code, r.get_json())


def test_f11_control_dirs_inside_a_root_start(monkeypatch, tmp_path):
    c, fake, root = _br_client(monkeypatch, tmp_path)
    (root / "sub").mkdir()
    r = c.post("/api/bitrot/scan", json={"action": "start", "download_dirs": [str(root / "sub")]})
    assert r.status_code == 200, r.get_json()
    assert fake.starts[0]["download_dirs"] == [str(root / "sub")], fake.starts
    r = c.post("/api/bitrot/scan", json={"action": "start"})
    assert r.status_code == 200 and fake.starts[1]["download_dirs"] is None, fake.starts


@pytest.mark.parametrize("mbps", ["inf", "nan", "-inf", -5, 0, 1e300])
def test_f12_non_finite_or_non_positive_target_mbps_is_a_400(monkeypatch, tmp_path, mbps):
    c, fake, _root = _br_client(monkeypatch, tmp_path)
    before = fake.pacer.nominal_bytes_per_sec
    r = c.post("/api/bitrot/scan", json={"action": "status", "target_mbps": mbps})
    assert fake.pacer.nominal_bytes_per_sec == before, (
        f"RRL-F12_PACING: target_mbps={mbps!r} set the nominal rate to "
        f"{fake.pacer.nominal_bytes_per_sec}")
    assert r.status_code == 400, (r.status_code, r.get_json())


def test_f12_pacer_refuses_a_non_finite_rate():
    p = bitrot.AdaptiveIOPacer()
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValueError):
            p.set_nominal_rate(bad)
    assert p.nominal_bytes_per_sec == 10 * 1024 * 1024
    p.set_nominal_rate(5 * 125000)
    assert p.nominal_bytes_per_sec == 625000.0


def test_f12_control_finite_target_mbps_sets_the_rate(monkeypatch, tmp_path):
    c, fake, _root = _br_client(monkeypatch, tmp_path)
    r = c.post("/api/bitrot/scan", json={"action": "status", "target_mbps": 5})
    assert r.status_code == 200 and fake.pacer.nominal_bytes_per_sec == 625000.0


@pytest.mark.parametrize("body, field", [
    ({"action": "start", "target_mbps": "x"}, "target_mbps"),
    ({"action": "start", "min_age_days": "x"}, "min_age_days"),
    ({"action": "start", "batch_size": "x"}, "batch_size"),
    ({"action": "start", "idle_sleep_seconds": "x"}, "idle_sleep_seconds"),
    ({"min_age_days": "x"}, "min_age_days"),
    ({"scan_fraction": "x"}, "scan_fraction"),
    ({"max_files": "x"}, "max_files"),
])
def test_f13_non_numeric_bitrot_params_are_a_400_naming_the_field(monkeypatch, tmp_path, body, field):
    c, fake, _root = _br_client(monkeypatch, tmp_path)
    monkeypatch.setattr(bitrot, "run_scan", lambda **k: {"ran": k})
    r = c.post("/api/bitrot/scan", json=body)
    err = (r.get_json() or {}).get("error", "")
    assert r.status_code == 400, (
        f"RRL-F13_PARSE_500: {body} -> {r.status_code} {err!r}")
    assert field in err and "invalid literal" not in err and "could not convert" not in err, err
    assert not fake.starts


def test_f13_control_numeric_scan_params_reach_run_scan(monkeypatch, tmp_path):
    c, _fake, _root = _br_client(monkeypatch, tmp_path)
    monkeypatch.setattr(bitrot, "run_scan", lambda **k: {"ran": k})
    r = c.post("/api/bitrot/scan", json={"scan_fraction": "0.5", "min_age_days": 3, "max_files": "9"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["ran"] == {"scan_fraction": 0.5, "min_age_days": 3, "max_files": 9}
