"""recovery-retention residuals left after crd/rms landed (FINDINGS F8, F9, F13).

F8  crash_recovery delete with a NUL or 10k-char path must be a 400, not a
    500 carrying exception text.
F9  inventory: a request-named path outside every download root must not be
    stat'd, and must get the same answer whether it exists or not (no host
    existence oracle). Covers both inventory routes and the plan routes,
    which inventory the same request paths.
F13 rebalance max_moves "abc" must be a 400, not a 500 with int() text.
"""
import pytest

BD_GATE_SCOPE = "module"


def _seed(monkeypatch, lib):
    from bulk_downloader import app_state
    monkeypatch.setitem(app_state.s_cfg, "rrA", {"name": "A", "download_dir": str(lib)})


# ── F8 ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["nul", "long"])
def test_f8_delete_with_unusable_path_is_400(fresh_app, clean_workdir, monkeypatch, kind):
    lib = clean_workdir / "lib1"
    lib.mkdir()
    _seed(monkeypatch, lib)
    path = (str(lib / "a\x00b.part") if kind == "nul"
            else str(lib / ("a" * 10000 + ".part")))
    r = fresh_app.post("/api/crash_recovery/delete", json={"path": path})
    assert r.status_code == 400, (
        f"RR_F8_DELETE_{kind.upper()}_NOT_400: {r.status_code} {r.get_data()[:200]!r}")


# ── F9 ────────────────────────────────────────────────────────────────────────


def _outside_pair(clean_workdir):
    real = clean_workdir / "outside_real"
    real.mkdir()
    return str(real), str(clean_workdir / "outside_absent")


def test_f9_rebalance_inventory_does_not_stat_outside_paths(fresh_app, clean_workdir, monkeypatch):
    lib = clean_workdir / "lib1"
    lib.mkdir()
    _seed(monkeypatch, lib)
    real, absent = _outside_pair(clean_workdir)
    r = fresh_app.get("/api/rebalance/inventory",
                      query_string=[("path", real), ("path", absent), ("path", str(lib))])
    assert r.status_code == 200, r.get_data()[:200]
    inv = {e["path"]: e for e in r.get_json()["inventory"]}
    assert "total_gb" not in inv[real], f"RR_F9_INVENTORY_STATS_OUTSIDE_ROOT: {inv[real]}"
    assert inv[real] == {**inv[absent], "path": real}, (
        f"RR_F9_INVENTORY_EXISTENCE_ORACLE: {inv[real]} vs {inv[absent]}")
    # Control: the configured root is still inventoried.
    assert "total_gb" in inv[str(lib)], inv[str(lib)]


def test_f9_storage_inventory_does_not_stat_outside_paths(fresh_app, clean_workdir, monkeypatch):
    lib = clean_workdir / "lib1"
    lib.mkdir()
    _seed(monkeypatch, lib)
    real, absent = _outside_pair(clean_workdir)
    r = fresh_app.post("/api/storage_rebalance/inventory",
                       json={"paths": [real, absent, str(lib)]})
    assert r.status_code == 200, r.get_data()[:200]
    inv = {e["path"]: e for e in r.get_json()["inventory"]}
    assert "total_gb" not in inv[real], f"RR_F9_STORAGE_INVENTORY_STATS_OUTSIDE_ROOT: {inv[real]}"
    assert inv[real] == {**inv[absent], "path": real}, (
        f"RR_F9_STORAGE_INVENTORY_EXISTENCE_ORACLE: {inv[real]} vs {inv[absent]}")
    assert "total_gb" in inv[str(lib)], inv[str(lib)]


def test_f9_plan_does_not_inventory_outside_paths(fresh_app, clean_workdir, monkeypatch):
    lib = clean_workdir / "lib1"
    lib.mkdir()
    _seed(monkeypatch, lib)
    real, absent = _outside_pair(clean_workdir)
    for route in ("/api/rebalance/plan", "/api/storage_rebalance/plan"):
        r = fresh_app.post(route, json={"paths": [real, str(lib)]})
        assert r.status_code == 200, r.get_data()[:200]
        body = r.get_json()
        inv = (body.get("summary") or {}).get("inventory") or []
        stat = [e for e in inv if e.get("path") == real]
        assert not stat, f"RR_F9_PLAN_STATS_OUTSIDE_ROOT {route}: {stat}"
        # An existing and a missing outside path must plan identically: an
        # outside path never counts as a healthy rebalance target.
        b_absent = fresh_app.post(route, json={"paths": [absent, str(lib)]}).get_json()
        assert body == b_absent, f"RR_F9_PLAN_EXISTENCE_ORACLE {route}: {body} vs {b_absent}"


# ── F13 ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("route", ["/api/rebalance/plan", "/api/storage_rebalance/plan"])
def test_f13_non_integer_max_moves_is_400(fresh_app, clean_workdir, monkeypatch, route):
    lib = clean_workdir / "lib1"
    lib.mkdir()
    _seed(monkeypatch, lib)
    r = fresh_app.post(route, json={"paths": [str(lib)], "max_moves": "abc"})
    assert r.status_code == 400, (
        f"RR_F13_MAX_MOVES_NOT_400 {route}: {r.status_code} {r.get_data()[:200]!r}")
    assert "invalid literal" not in r.get_data(as_text=True), r.get_data()[:200]
    # Control: an integer max_moves still plans.
    ok = fresh_app.post(route, json={"paths": [str(lib)], "max_moves": 5})
    assert ok.status_code == 200, ok.get_data()[:200]


@pytest.mark.parametrize("route", ["/api/rebalance/plan", "/api/storage_rebalance/plan"])
def test_f13_overflowing_max_moves_is_400(fresh_app, clean_workdir, monkeypatch, route):
    # 1e400 is valid JSON and parses to float('inf'): int() raises OverflowError.
    lib = clean_workdir / "lib1"
    lib.mkdir()
    _seed(monkeypatch, lib)
    r = fresh_app.post(route, data='{"paths": ["%s"], "max_moves": 1e400}' % lib,
                       content_type="application/json")
    assert r.status_code == 400, f"RR_F13_MAX_MOVES_INF_NOT_400 {route}: {r.status_code}"
