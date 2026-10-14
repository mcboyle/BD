"""O1789 dev-console-exec F2/F3/F9 (tempdir-clean-safety r1).

F2  tempdir_clean let the caller LOWER the 1h age floor: min_age_seconds
    0/-5/"nan"/"-inf" became 0 and swept a seconds-old bdback_ dir (an
    in-flight backup). dry_run null/0/[] became a real sweep via bool().
F3  The default sweep removed bd_vpn_wg/bd_vpn_ovpn once their mtime was
    over an hour old (any tunnel started >1h ago); vpn_wireguard created
    CONF_DIR only at import, so every later WireGuard start failed
    "could not write conf ... No such file or directory".
F9  job_replay: dry_run null/[]/0/{} became a real replay (row -> pending).

Every failure message starts with TCS-F<n>.
"""

import os
import stat
import tempfile
import time
import types

import pytest

from bulk_downloader import db
from bulk_downloader import dev_suite as ds

BD_GATE_SCOPE = "module"
FLOOR = 3600.0


def _mkdir(root, name, age):
    p = os.path.join(str(root), name)
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "f"), "w") as fh:
        fh.write("x")
    old = time.time() - age
    os.utime(p, (old, old))
    return p


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    root = tmp_path / "systemp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    return root


def _clean(client, body):
    r = client.post("/api/dev/tempdir_clean", json=body)
    j = r.get_json(silent=True) or {}
    assert r.status_code == 200, ("TCS-route", r.status_code, j)
    return j


# ── F2: the age floor ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "asked",
    [0, -5, "nan", "-inf", "inf", 10**400],
    ids=["zero", "neg", "nan-str", "neg-inf-str", "inf-str", "overflow-int"],
)
def test_f2_caller_cannot_lower_the_age_floor(fresh_app, tmp_root, asked):
    fresh = _mkdir(tmp_root, "bdback_inflight", 5)
    j = _clean(fresh_app, {"dry_run": False, "min_age_seconds": asked})
    assert os.path.isdir(fresh), (
        f"TCS-F2 min_age_seconds={asked!r} swept a 5 s old backup dir",
        j.get("min_age_seconds"),
        j.get("removed"),
    )
    assert j.get("min_age_seconds") == FLOOR, (
        f"TCS-F2 min_age_seconds={asked!r} answered cut-off "
        f"{j.get('min_age_seconds')!r}, not the 1h floor"
    )


def test_f2_nan_float_cannot_lower_the_age_floor(tmp_root):
    fresh = _mkdir(tmp_root, "bdback_inflight", 5)
    r = ds.tempdir_clean(dry_run=False, min_age_seconds=float("nan"))
    assert os.path.isdir(fresh) and r["min_age_seconds"] == FLOOR, (
        "TCS-F2 float nan lowered the floor",
        r["min_age_seconds"],
    )


def test_f2_caller_can_still_raise_the_cut_off(fresh_app, tmp_root):
    """Positive control: a larger min_age is honoured, and the default
    floor still sweeps an old dir (the probe CAN say yes)."""
    mid = _mkdir(tmp_root, "bdback_ninetymin", 5400)
    j = _clean(fresh_app, {"dry_run": False, "min_age_seconds": 7200})
    assert j.get("min_age_seconds") == 7200.0 and os.path.isdir(mid), (
        "TCS-F2-CONTROL a 2h cut-off was not honoured",
        j,
    )
    j = _clean(fresh_app, {"dry_run": False})
    assert not os.path.exists(mid), (
        "TCS-F2-CONTROL the default sweep kept a 90 min old dir",
        j,
    )


# ── F2: dry_run fails closed ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "flag",
    [None, 0, [], {}, "", 0.0],
    ids=["null", "zero", "list", "dict", "empty-str", "zero-float"],
)
def test_f2_non_false_dry_run_only_reports(fresh_app, tmp_root, flag):
    old = _mkdir(tmp_root, "bdback_old", 7200)
    j = _clean(fresh_app, {"dry_run": flag})
    assert os.path.isdir(old), (
        f"TCS-F2 dry_run={flag!r} ran a real sweep",
        j.get("removed"),
    )
    assert j.get("dry_run") is True and j.get("would_remove"), (
        f"TCS-F2 dry_run={flag!r} did not report a dry run",
        j,
    )


@pytest.mark.parametrize(
    "flag", [False, "false", "no"], ids=["false", "false-str", "no-str"]
)
def test_f2_explicit_false_still_sweeps(fresh_app, tmp_root, flag):
    old = _mkdir(tmp_root, "bdback_old", 7200)
    j = _clean(fresh_app, {"dry_run": flag})
    assert j.get("dry_run") is False and not os.path.exists(old), (
        f"TCS-F2-CONTROL dry_run={flag!r} no longer sweeps",
        j,
    )


# ── F3: VPN conf dirs ─────────────────────────────────────────────────────


def test_f3_default_sweep_keeps_vpn_conf_dirs(fresh_app, tmp_root):
    wg = _mkdir(tmp_root, "bd_vpn_wg", 7200)
    ovpn = _mkdir(tmp_root, "bd_vpn_ovpn", 7200)
    old = _mkdir(tmp_root, "bdback_old", 7200)
    j = _clean(fresh_app, {"dry_run": False})
    assert not os.path.exists(old), ("TCS-F3-CONTROL the sweep did not run", j)
    gone = [p for p in (wg, ovpn) if not os.path.isdir(p)]
    assert not gone, f"TCS-F3 the default sweep removed live VPN conf dirs {gone}"
    assert os.path.exists(os.path.join(wg, "f")), "TCS-F3 a live conf was removed"


def test_f3_scan_reports_vpn_dirs_not_sweepable(tmp_root):
    _mkdir(tmp_root, "bd_vpn_wg", 7200)
    _mkdir(tmp_root, "bdback_old", 7200)
    r = ds.lockfile_scan()
    by = {d["name"]: d for d in r["bd_temp_dirs"]}
    assert by["bd_vpn_wg"]["kind"] == "vpn_config", by
    assert by["bd_vpn_wg"]["sweepable"] is False, (
        "TCS-F3 lockfile_scan calls a VPN conf dir sweepable",
        by["bd_vpn_wg"],
    )
    assert by["bdback_old"]["sweepable"] is True, (
        "TCS-F3-CONTROL an old backup dir is no longer sweepable",
        by,
    )
    assert r["stale_temp_dir_count"] == 1, r["stale_temp_dir_count"]


@pytest.fixture
def wg_stub(tmp_path, monkeypatch):
    import bulk_downloader.vpn_config as vc
    import bulk_downloader.vpn_wireguard as wg

    conf_dir = tmp_path / "bd_vpn_wg"
    monkeypatch.setattr(wg, "CONF_DIR", conf_dir)
    monkeypatch.setattr(wg, "_WG_BINARY", "/usr/bin/wg", raising=False)
    monkeypatch.setattr(vc, "resolve_secrets", lambda cfg: cfg, raising=False)
    monkeypatch.setattr(wg, "render_conf", lambda cfg: "[Interface]\n")
    seen = []

    def _up(conf_path, iface):
        seen.append(os.path.exists(conf_path))
        raise RuntimeError("tcs-stub-wg-up")

    monkeypatch.setattr(wg, "_wg_up", _up)
    tun = types.SimpleNamespace(
        tunnel_id="tcs-t1", socks_port=1080, config={}, last_error="", public_ip=None
    )
    return wg, conf_dir, tun, seen


def test_f3_wireguard_start_recreates_a_removed_conf_dir(wg_stub):
    wg, conf_dir, tun, seen = wg_stub
    assert not conf_dir.exists(), "precondition: CONF_DIR is gone"
    assert wg.start(tun) is False
    assert "could not write conf" not in tun.last_error, (
        f"TCS-F3 wg start after the conf dir was removed: {tun.last_error}"
    )
    assert seen == [True], ("TCS-F3 wg-up never saw the conf", tun.last_error)
    assert stat.S_IMODE(conf_dir.stat().st_mode) == 0o700, (
        "TCS-F3 recreated CONF_DIR is not 0700",
        oct(conf_dir.stat().st_mode),
    )


def test_f3_wireguard_start_with_conf_dir_present(wg_stub):
    """Positive control: the stub reaches wg-up when the dir exists."""
    wg, conf_dir, tun, seen = wg_stub
    conf_dir.mkdir(mode=0o700)
    assert wg.start(tun) is False
    assert "tcs-stub-wg-up" in tun.last_error and seen == [True], (
        "TCS-F3-CONTROL",
        tun.last_error,
        seen,
    )


# ── F9: job_replay dry_run fails closed ───────────────────────────────────


def _history_row(url):
    db.db_init()
    db.db_log("s1", "Site One", url, "failed", message="boom")
    with db.db_conn() as cx:
        return cx.execute("SELECT id FROM history WHERE url=?", (url,)).fetchone()[0]


def _status(hid):
    with db.db_conn() as cx:
        return cx.execute("SELECT status FROM history WHERE id=?", (hid,)).fetchone()[0]


@pytest.mark.parametrize(
    "flag", [None, 0, [], {}, ""], ids=["null", "zero", "list", "dict", "empty-str"]
)
def test_f9_non_false_dry_run_does_not_replay(fresh_app, flag):
    hid = _history_row(f"https://example.com/tcs-{id(flag)}")
    r = fresh_app.post("/api/dev/job_replay", json={"history_id": hid, "dry_run": flag})
    body = r.get_json(silent=True) or {}
    assert _status(hid) == "failed", (
        f"TCS-F9 dry_run={flag!r} replayed the row for real",
        body,
    )
    assert body.get("dry_run") is True, (
        f"TCS-F9 dry_run={flag!r} did not report a dry run",
        body,
    )


@pytest.mark.parametrize("flag", [False, "false"], ids=["false", "false-str"])
def test_f9_explicit_false_still_replays(fresh_app, flag):
    hid = _history_row(f"https://example.com/tcs-real-{flag}")
    r = fresh_app.post("/api/dev/job_replay", json={"history_id": hid, "dry_run": flag})
    assert _status(hid) == "pending", (
        f"TCS-F9-CONTROL dry_run={flag!r} no longer replays",
        r.get_json(silent=True),
    )
