"""Storage rebalance must never destroy a file it was asked to move (HIGH-F2).

THE DEFECT, measured on origin/main 88ea4929 (recovery-retention HIGH-F2):

  * Both planners target ``join(target_root, basename(src))``. Two files with
    the same basename in different folders of the full disk are planned onto
    ONE target path; execute_plan renames the first, then renames the second
    over it. The first file is gone, both history rows point at the survivor.
  * execute_plan calls ``os.rename`` / ``shutil.move`` with no existence check.
    Both silently replace an existing destination file.
  * The planners select history rows with ``filename LIKE root || '%'``: root
    ``lib1`` also selects ``lib10/...``, and ``_`` in a root is a wildcard.
  * The history UPDATE is keyed on the plan's ``history_id`` only, so a plan
    naming the wrong id rewrites an unrelated row; a null id makes the FTS
    snapshot raise AFTER the file already moved, and the move is reported as
    an error.
  * /api/rebalance/execute runs a client-supplied plan verbatim, so a plan can
    move any file the app user can write, to anywhere.
  * /api/storage_rebalance/execute (R271-B F1) hands a client plan to the same
    execute_plan with no root check at all, so the sink itself checks: every
    from AND to inside a download root, or the whole plan is refused, 0 moved.

Every RED case asserts the FILE-SYSTEM outcome (bytes on disk), not only the
response: a move that "errors" after the rename already ran is still a loss.
"""

from __future__ import annotations

# H622 slice A. An ordinary module test: its subject is the module under
# test, not the tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import os
import shutil
from collections import namedtuple

import pytest

from bulk_downloader import db
from bulk_downloader import storage_rebalance as sr

GB = 1024**3
_Usage = namedtuple("_Usage", "total used free")


# ── helpers ──────────────────────────────────────────────────────────────────


def _write(path, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return str(path)


def _read(path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _row(filename: str, size: int, ts: str = "2020-01-01 00:00:00") -> int:
    """One done history row (FTS in sync). Returns its id."""
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id, site_name, url, status, filename, "
            "file_size, ts) VALUES(?,?,?,?,?,?,?)",
            (
                "siteA",
                "Acme",
                "https://a.example/" + os.path.basename(filename),
                "done",
                filename,
                size,
                ts,
            ),
        )
        rid = cx.execute("SELECT last_insert_rowid()").fetchone()[0]
        cx.execute(
            "INSERT INTO history_fts(rowid, site_name, url, filename, message, status) "
            "SELECT id, COALESCE(site_name,''), COALESCE(url,''), "
            "COALESCE(filename,''), COALESCE(message,''), COALESCE(status,'') "
            "FROM history WHERE id = ?",
            (rid,),
        )
    return rid


def _filename(rid: int) -> str:
    with db.db_conn() as cx:
        return cx.execute(
            "SELECT filename FROM history WHERE id = ?", (rid,)
        ).fetchone()[0]


@pytest.fixture
def lib_roots(clean_workdir, monkeypatch):
    """lib1 and lib2 under the workdir are the configured download roots.
    execute_plan moves nothing outside the roots, so a test that drives it
    directly must configure them, exactly as a running app has."""
    from bulk_downloader import app_state

    lib1, lib2 = str(clean_workdir / "lib1"), str(clean_workdir / "lib2")
    monkeypatch.setitem(app_state.s_cfg, "rbA", {"name": "A", "download_dir": lib1})
    monkeypatch.setitem(app_state.s_cfg, "rbB", {"name": "B", "download_dir": lib2})
    return lib1, lib2


def _stub_disks(monkeypatch, free_by_root: dict) -> None:
    """Make the named roots report the given free GB; even_fill needs a gap of
    at least 10 GB or it returns [] ("already balanced") and a test that
    asserts what the plan EXCLUDES would pass vacuously."""
    real = shutil.disk_usage

    def fake(p):
        for root, free_gb in free_by_root.items():
            if os.path.normpath(str(p)) == os.path.normpath(root):
                return _Usage(1000 * GB, (1000 - free_gb) * GB, free_gb * GB)
        return real(p)

    monkeypatch.setattr(sr.shutil, "disk_usage", fake)


# ── RED: planner collisions ──────────────────────────────────────────────────


def test_same_basename_plan_is_an_error_and_both_files_survive(
    clean_workdir, lib_roots, monkeypatch
):
    """B3: siteA/scene.mp4 + siteB/scene.mp4 on the full disk, one target."""
    db.db_init()
    lib1, lib2 = str(clean_workdir / "lib1"), str(clean_workdir / "lib2")
    a = _write(os.path.join(lib1, "siteA", "scene.mp4"), b"FIRST")
    b = _write(os.path.join(lib1, "siteB", "scene.mp4"), b"SECOND")
    os.makedirs(lib2)
    _row(a, 5)
    _row(b, 6)
    _stub_disks(monkeypatch, {lib1: 10, lib2: 100})

    plan = sr.plan_rebalance([lib1, lib2], "even_fill")
    targets = [m["to"] for m in plan.get("moves") or []]
    assert len(targets) == len(set(targets)), (
        f"plan sends two files to one target path: {targets}"
    )
    assert "error" in plan and "scene.mp4" in plan["error"], (
        f"a target collision must be a plan error naming the path: {plan}"
    )

    sr.execute_plan(plan, dry_run=False)
    assert _read(a) == b"FIRST" and _read(b) == b"SECOND", (
        "a colliding plan destroyed a file"
    )


def test_client_plan_with_one_target_twice_keeps_both_files(clean_workdir, lib_roots, capsys):
    """The execute-time guard must be per move: a pre-scan of existing files
    misses two moves to the same new path, because the first one creates it."""
    db.db_init()
    a = _write(str(clean_workdir / "lib1" / "x" / "scene.mp4"), b"FIRST")
    b = _write(str(clean_workdir / "lib1" / "y" / "scene.mp4"), b"SECOND")
    dst = str(clean_workdir / "lib2" / "scene.mp4")
    out = sr.execute_plan(
        {
            "moves": [
                {"from": a, "to": dst, "history_id": _row(a, 5), "size_bytes": 5},
                {"from": b, "to": dst, "history_id": _row(b, 6), "size_bytes": 6},
            ]
        },
        dry_run=False,
    )
    assert _read(dst) == b"FIRST" and _read(b) == b"SECOND", (
        f"second move overwrote the first: {out}"
    )
    assert out["moved"] == 1 and out["errors"] == 1, out
    assert "destination exists" in capsys.readouterr().err


# ── RED: no overwrite at execute ─────────────────────────────────────────────


def test_execute_never_overwrites_an_existing_destination(clean_workdir, lib_roots, capsys):
    """B2v: plan {a(10 B) -> existing b(20 B)} replaced b with a's bytes."""
    db.db_init()
    a = _write(str(clean_workdir / "lib1" / "a.mp4"), b"A" * 10)
    b = _write(str(clean_workdir / "lib2" / "b.mp4"), b"B" * 20)
    rid = _row(a, 10)
    out = sr.execute_plan(
        {"moves": [{"from": a, "to": b, "history_id": rid, "size_bytes": 10}]},
        dry_run=False,
    )
    assert _read(b) == b"B" * 20, f"existing destination was overwritten: {out}"
    assert _read(a) == b"A" * 10, "source must stay in place when refused"
    assert out["moved"] == 0 and out["errors"] == 1, out
    assert _filename(rid) == a, "history must not point at a refused target"
    assert "destination exists" in capsys.readouterr().err


# ── RED: exact root, not LIKE prefix ─────────────────────────────────────────


@pytest.mark.parametrize("root,sibling", [("lib1", "lib10"), ("lib_1", "libx1")])
@pytest.mark.parametrize("strategy", ["even_fill", "tier_by_age"])
def test_planner_root_does_not_select_a_sibling_root(
    clean_workdir, monkeypatch, root, sibling, strategy
):
    """B5: root lib1 selected lib10/x/sibling.mp4; '_' in a root is a LIKE wildcard."""
    db.db_init()
    full, empty = str(clean_workdir / root), str(clean_workdir / "lib2")
    own = _write(os.path.join(full, "own.mp4"), b"OWN")
    other = _write(str(clean_workdir / sibling / "x" / "sibling.mp4"), b"SIB")
    os.makedirs(empty)
    _row(own, 3)
    _row(other, 3)
    _stub_disks(monkeypatch, {full: 10, empty: 100})

    plan = sr.plan_rebalance([full, empty], strategy)
    froms = [m["from"] for m in plan.get("moves") or []]
    assert own in froms, f"control: the root's own file must be planned: {plan}"
    assert other not in froms, (
        f"root {root} selected a file under sibling root {sibling}: {froms}"
    )


# ── RED: history UPDATE by exact path ────────────────────────────────────────


def test_update_does_not_rewrite_an_unrelated_row(clean_workdir, lib_roots):
    """B1/B4: the UPDATE keyed on the plan's history_id rewrote a foreign row."""
    db.db_init()
    src = _write(str(clean_workdir / "lib1" / "mine.mp4"), b"M")
    dst = str(clean_workdir / "lib2" / "mine.mp4")
    mine = _row(src, 1)
    foreign_name = str(clean_workdir / "lib1" / "theirs.mp4")
    foreign = _row(foreign_name, 1)
    out = sr.execute_plan(
        {"moves": [{"from": src, "to": dst, "history_id": foreign, "size_bytes": 1}]},
        dry_run=False,
    )
    assert _filename(foreign) == foreign_name, (
        f"an unrelated history row was rewritten to {dst}: {out}"
    )
    assert _filename(mine) == dst, "the moved file's own row must follow it"


def test_null_history_id_move_is_reported_moved(clean_workdir, lib_roots):
    """B2: hid null -> the rename ran, then the DB write raised -> 'errors 1'."""
    db.db_init()
    src = _write(str(clean_workdir / "lib1" / "n.mp4"), b"N")
    dst = str(clean_workdir / "lib2" / "n.mp4")
    rid = _row(src, 1)
    out = sr.execute_plan(
        {"moves": [{"from": src, "to": dst, "history_id": None, "size_bytes": 1}]},
        dry_run=False,
    )
    assert os.path.isfile(dst) and not os.path.exists(src), "fixture: move ran"
    assert out["moved"] == 1 and out["errors"] == 0, (
        f"a completed move was reported as an error: {out}"
    )
    assert _filename(rid) == dst


# ── RED: a client plan is re-validated against the download roots ────────────


def test_execute_route_refuses_a_plan_outside_the_download_roots(
    fresh_app, clean_workdir, monkeypatch
):
    """B1: a client plan moved a file from outside every root to outside every root."""
    import bulk_downloader.app as bd_app

    lib1 = str(clean_workdir / "lib1")
    lib2 = str(clean_workdir / "lib2")
    os.makedirs(lib2)
    monkeypatch.setitem(bd_app.s_cfg, "rbA", {"name": "A", "download_dir": lib1})
    monkeypatch.setitem(bd_app.s_cfg, "rbB", {"name": "B", "download_dir": lib2})
    outside = _write(str(clean_workdir / "outside" / "keep.txt"), b"KEEP")
    inside = _write(os.path.join(lib1, "in.mp4"), b"IN")
    cases = [
        (outside, os.path.join(lib2, "keep.txt")),  # src outside
        (inside, str(clean_workdir / "elsewhere" / "in.mp4")),  # dst outside
        (inside, lib1 + "0" + os.sep + "in.mp4"),  # sibling root lib10
    ]
    for src, dst in cases:
        r = fresh_app.post(
            "/api/rebalance/execute",
            json={
                "plan": {"moves": [{"from": src, "to": dst, "history_id": None}]},
                "dry_run": False,
            },
        )
        assert r.status_code == 400, (
            f"out-of-root plan {src} -> {dst} was not refused: "
            f"{r.status_code} {r.get_json()}"
        )
        assert not os.path.exists(dst), f"out-of-root move ran: {dst}"
    assert _read(outside) == b"KEEP" and _read(inside) == b"IN"

    # Control: an in-root plan still moves.
    dst = os.path.join(lib2, "in.mp4")
    r = fresh_app.post(
        "/api/rebalance/execute",
        json={
            "plan": {"moves": [{"from": inside, "to": dst, "history_id": None}]},
            "dry_run": False,
        },
    )
    assert r.status_code == 200 and r.get_json()["moved"] == 1, r.get_json()
    assert _read(dst) == b"IN"


# ── RED (amend, R271-B F1): the sink refuses an out-of-root plan, whole ───────


def _plan(*pairs) -> dict:
    return {"moves": [{"from": a, "to": b, "history_id": None, "size_bytes": 1}
                      for a, b in pairs]}


def test_storage_execute_route_refuses_a_plan_outside_the_download_roots(
    fresh_app, clean_workdir, monkeypatch
):
    """R271-B F1: {<wd>/outside/secret.txt -> <wd>/elsewhere/secret.txt} on
    /api/storage_rebalance/execute -> 200 {moved: 1}; the file left every root."""
    import bulk_downloader.app as bd_app

    lib1 = str(clean_workdir / "lib1")
    lib2 = str(clean_workdir / "lib2")
    os.makedirs(lib2)
    monkeypatch.setitem(bd_app.s_cfg, "rbA", {"name": "A", "download_dir": lib1})
    monkeypatch.setitem(bd_app.s_cfg, "rbB", {"name": "B", "download_dir": lib2})
    secret = _write(str(clean_workdir / "outside" / "secret.txt"), b"SECRET")
    outside = _write(str(clean_workdir / "outside" / "keep.txt"), b"KEEP")
    inside = _write(os.path.join(lib1, "in.mp4"), b"IN")
    cases = [
        (secret, str(clean_workdir / "elsewhere" / "secret.txt")),  # R271-B repro
        (outside, os.path.join(lib2, "keep.txt")),  # src outside
        (inside, str(clean_workdir / "elsewhere" / "in.mp4")),  # dst outside
        (inside, lib1 + "0" + os.sep + "in.mp4"),  # sibling root lib10
    ]
    for src, dst in cases:
        r = fresh_app.post(
            "/api/storage_rebalance/execute",
            json={"plan": _plan((src, dst)), "dry_run": False},
        )
        assert r.status_code == 400, (
            f"out-of-root plan {src} -> {dst} was not refused: "
            f"{r.status_code} {r.get_json()}"
        )
        assert r.get_json()["moved"] == 0, r.get_json()
        assert not os.path.exists(dst), f"out-of-root move ran: {dst}"
    assert _read(secret) == b"SECRET"
    assert _read(outside) == b"KEEP" and _read(inside) == b"IN"

    # Control: an in-root plan still moves on this route.
    dst = os.path.join(lib2, "in.mp4")
    r = fresh_app.post(
        "/api/storage_rebalance/execute",
        json={"plan": _plan((inside, dst)), "dry_run": False},
    )
    assert r.status_code == 200 and r.get_json()["moved"] == 1, r.get_json()
    assert _read(dst) == b"IN" and not os.path.exists(inside)


@pytest.mark.parametrize(
    "route", ["/api/storage_rebalance/execute", "/api/rebalance/execute"]
)
def test_mixed_plan_moves_nothing_on_either_route(
    fresh_app, clean_workdir, monkeypatch, route
):
    """One in-root move first, one out-of-root move second: refused WHOLE, so
    the good entry does not run ahead of the bad one."""
    import bulk_downloader.app as bd_app

    lib1 = str(clean_workdir / "lib1")
    lib2 = str(clean_workdir / "lib2")
    os.makedirs(lib2)
    monkeypatch.setitem(bd_app.s_cfg, "rbA", {"name": "A", "download_dir": lib1})
    monkeypatch.setitem(bd_app.s_cfg, "rbB", {"name": "B", "download_dir": lib2})
    good = _write(os.path.join(lib1, "good.mp4"), b"GOOD")
    bad = _write(str(clean_workdir / "outside" / "bad.txt"), b"BAD")
    good_dst = os.path.join(lib2, "good.mp4")
    bad_dst = os.path.join(lib2, "bad.txt")
    r = fresh_app.post(
        route,
        json={"plan": _plan((good, good_dst), (bad, bad_dst)), "dry_run": False},
    )
    assert r.status_code == 400, f"{route}: mixed plan not refused: {r.get_json()}"
    assert _read(good) == b"GOOD" and not os.path.exists(good_dst), (
        f"{route}: the in-root entry moved although the plan was refused"
    )
    assert _read(bad) == b"BAD" and not os.path.exists(bad_dst)


def test_execute_plan_refuses_the_whole_plan_at_the_sink(clean_workdir, lib_roots):
    """The sink, driven directly (no route): a mixed plan and a malformed entry
    are refused before the first move, with the offending entries listed."""
    db.db_init()
    lib1, lib2 = lib_roots
    good = _write(os.path.join(lib1, "good.mp4"), b"GOOD")
    bad = _write(str(clean_workdir / "outside" / "bad.txt"), b"BAD")
    good_dst = os.path.join(lib2, "good.mp4")
    rid = _row(good, 4)

    out = sr.execute_plan(
        _plan((good, good_dst), (bad, os.path.join(lib2, "bad.txt"))),
        dry_run=False,
    )
    assert out["ok"] is False and out["moved"] == 0, out
    assert [m["from"] for m in out["refused"]] == [bad], out
    assert _read(good) == b"GOOD" and not os.path.exists(good_dst), (
        f"the in-root entry moved although the plan was refused: {out}"
    )
    assert _filename(rid) == good

    out = sr.execute_plan(
        {"moves": [{"from": good, "to": good_dst}, "not-a-move"]}, dry_run=False
    )
    assert out["ok"] is False and out["refused"] == ["not-a-move"], out
    assert _read(good) == b"GOOD" and not os.path.exists(good_dst)

    # Control: the same in-root move alone runs.
    out = sr.execute_plan(_plan((good, good_dst)), dry_run=False)
    assert out["ok"] is True and out["moved"] == 1, out
    assert _read(good_dst) == b"GOOD" and _filename(rid) == good_dst


def test_spillover_and_tier_roots_admit_a_move_and_nothing_else(
    fresh_app, clean_workdir, monkeypatch
):
    """The shared roots (paths.download_roots) are download_dir + spillover_dirs
    + storage_tier_dir: a rebalance between them moves, and a move to a
    directory outside every one of them is still refused whole."""
    import bulk_downloader.app as bd_app

    lib1 = str(clean_workdir / "lib1")
    spill = str(clean_workdir / "spill")
    tier = str(clean_workdir / "tier")
    monkeypatch.setitem(
        bd_app.s_cfg,
        "rbA",
        {
            "name": "A",
            "download_dir": lib1,
            "spillover_dirs": "\n" + spill + "\n",
            "storage_tier_dir": tier,
        },
    )
    a = _write(os.path.join(lib1, "a.mp4"), b"A")
    b = _write(os.path.join(spill, "b.mp4"), b"B")
    outside = str(clean_workdir / "elsewhere" / "b.mp4")

    r = fresh_app.post(
        "/api/storage_rebalance/execute",
        json={"plan": _plan((b, outside)), "dry_run": False},
    )
    assert r.status_code == 400 and r.get_json()["moved"] == 0, r.get_json()
    assert _read(b) == b"B" and not os.path.exists(outside)

    r = fresh_app.post(
        "/api/storage_rebalance/execute",
        json={
            "plan": _plan((a, os.path.join(spill, "a.mp4")),
                          (b, os.path.join(tier, "b.mp4"))),
            "dry_run": False,
        },
    )
    assert r.status_code == 200 and r.get_json()["moved"] == 2, r.get_json()
    assert _read(os.path.join(spill, "a.mp4")) == b"A"
    assert _read(os.path.join(tier, "b.mp4")) == b"B"
