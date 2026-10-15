"""crash-recovery-delete-bound r1 (O1789 recovery-retention HIGH-F1).

POST /api/crash_recovery/delete unlinked any path the body named, plus its
``.meta``: no ``.part`` check, no download-root bound, no tie to what /scan
listed. The path a request names is now only a lookup key: it is deleted only
when it is a ``.part`` inside a download root AND is in the current server-side
scan set. Everything else is refused with 400 and nothing on disk changes.

paths.within_download_roots is the shared bound (rows 2 and 3 stack on it).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bulk_downloader import crash_recovery as cr
from bulk_downloader import paths
from bulk_downloader import staging_claim as sc

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------- helper ----

def test_helper_root_and_children_inside(tmp_path):
    root = tmp_path / "lib1"
    (root / "a" / "b").mkdir(parents=True)
    f = root / "a" / "b" / "x.part"
    f.write_bytes(b"x")
    assert paths.within_download_roots(str(root), [str(root)])
    assert paths.within_download_roots(str(f), [str(root)])
    assert paths.within_download_roots(str(root / "a" / "missing.part"), [str(root)])


def test_helper_sibling_prefix_is_outside(tmp_path):
    (tmp_path / "lib1").mkdir()
    (tmp_path / "lib10").mkdir()
    sib = tmp_path / "lib10" / "x.part"
    sib.write_bytes(b"x")
    assert not paths.within_download_roots(str(sib), [str(tmp_path / "lib1")]), (
        "BOUND-SIBLING: /lib10 accepted under root /lib1 (bare prefix match)")
    assert not paths.within_download_roots(str(tmp_path / "lib1x"), [str(tmp_path / "lib1")])


def test_helper_symlink_escape_refused(tmp_path):
    root = tmp_path / "lib1"
    out = tmp_path / "outside"
    root.mkdir()
    out.mkdir()
    (out / "secret.txt").write_bytes(b"s")
    (root / "link.part").symlink_to(out / "secret.txt")
    (root / "dirlink").symlink_to(out, target_is_directory=True)
    assert not paths.within_download_roots(str(root / "link.part"), [str(root)]), (
        "BOUND-SYMLINK: file symlink out of the root accepted")
    assert not paths.within_download_roots(str(root / "dirlink" / "secret.txt"), [str(root)]), (
        "BOUND-SYMLINK: directory symlink out of the root accepted")
    assert not paths.within_download_roots(str(root / ".." / "outside" / "secret.txt"), [str(root)]), (
        "BOUND-DOTDOT: .. escape accepted")


def test_helper_symlinked_root_judged_by_target(tmp_path):
    disk = tmp_path / "disk1"
    disk.mkdir()
    (disk / "x.part").write_bytes(b"x")
    (tmp_path / "data").symlink_to(disk, target_is_directory=True)
    assert paths.within_download_roots(str(tmp_path / "data" / "x.part"), [str(tmp_path / "data")])
    assert paths.within_download_roots(str(disk / "x.part"), [str(tmp_path / "data")])


def test_helper_unusable_inputs_are_outside(tmp_path):
    root = str(tmp_path)
    for bad in ("", "relative/x.part", "x.part", "a\x00b", None, 7, b"/x"):
        assert not paths.within_download_roots(bad, [root]), (
            f"BOUND-INPUT: {bad!r} accepted")
    assert not paths.within_download_roots(os.path.join(root, "x.part"), []), (
        "BOUND-EMPTY: no roots must mean nothing is inside")
    assert not paths.within_download_roots(os.path.join(root, "x.part"), ["", "rel"]), (
        "BOUND-EMPTY: blank / relative roots must not match")


def test_helper_filesystem_root():
    assert paths.within_download_roots("/etc/passwd", ["/"])
    assert not paths.within_download_roots("/etc/passwd", ["/e"])


def test_helper_default_roots_from_site_config(tmp_path, monkeypatch):
    from bulk_downloader import app_state
    lib = tmp_path / "lib1"
    lib.mkdir()
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {"download_dir": str(lib)}, "s2": {}})
    assert paths.within_download_roots(str(lib / "x.part"))
    assert not paths.within_download_roots(str(tmp_path / "other" / "x.part"))
    monkeypatch.setattr(app_state, "s_cfg", {})
    assert not paths.within_download_roots(str(lib / "x.part"))


# ----------------------------------------------------------------- route ----

_OLD = 3 * 86400
H = {"Host": "localhost"}


def _put(path, data=b"x" * 64, age=_OLD, meta=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if meta:
        path.with_name(path.name + ".meta").write_text('{"url": "https://example.test/v"}')
    _age(path, age)
    return path


def _age(path, age=_OLD):
    t = time.time() - age
    os.utime(path, (t, t), follow_symlinks=True)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from bulk_downloader import app as bd_app
    from bulk_downloader import app_state
    lib = tmp_path / "lib1"
    lib.mkdir()
    (tmp_path / "lib10").mkdir()
    (tmp_path / "outside").mkdir()
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {"name": "s1", "download_dir": str(lib)}})
    monkeypatch.setattr(app_state, "runners", {})
    client = bd_app.app.test_client()

    def delete(p):
        return client.post("/api/crash_recovery/delete", json={"path": str(p)}, headers=H)

    def listed():
        r = client.get("/api/crash_recovery/scan", headers=H)
        assert r.status_code == 200, r.get_data()[:200]
        return {o["path"]: o for o in r.get_json()["orphans"]}

    return tmp_path, lib, delete, listed


def _debris(p):
    p = Path(p)
    return [str(x) for x in (cr._delete_marker_for(p), sc.owner_path_for(p)) if os.path.lexists(x)]


def test_listed_orphan_is_deleted_with_its_meta(rig):
    _tmp, lib, delete, listed = rig
    part = _put(lib / "sub" / "clip.mp4.part")
    meta = part.with_name(part.name + ".meta")
    assert str(part) in listed(), "control: aged unclaimed .part must be listed by /scan"
    r = delete(part)
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_json()
    assert not part.exists() and not meta.exists(), "control: listed orphan + .meta must be gone"
    assert _debris(part) == []


def _refusal_cases(tmp, lib):
    out = tmp / "outside"
    cases = {}
    cases["etc-style"] = _put(out / "precious.txt", b"KEEP" * 16)
    cases["outside-part"] = _put(out / "stray.mp4.part")
    cases["sibling-root"] = _put(tmp / "lib10" / "x" / "sib.mp4.part")
    secret = _put(out / "secret.txt", b"SECRET" * 8, meta=False)
    link = lib / "evil.mp4.part"
    link.symlink_to(secret)
    cases["symlink-out"] = link
    cases["non-part"] = _put(lib / "done" / "movie.mp4", b"M" * 128)
    cases["part-not-in-scan"] = _put(lib / "young.mp4.part", age=60)
    cases["absent-part"] = lib / "never.mp4.part"
    cases["dotdot-escape"] = _put(out / "dd.mp4.part")
    return cases, secret


_SUFFIX = "refused: only a .part orphan can be deleted"
_OUTSIDE = "refused: path is outside every download root"
_UNLISTED = "refused: path is not an orphan listed by the current scan"
_WHY = {"etc-style": _SUFFIX, "outside-part": _OUTSIDE, "sibling-root": _OUTSIDE,
        "symlink-out": _OUTSIDE, "non-part": _SUFFIX, "part-not-in-scan": _UNLISTED,
        "absent-part": _UNLISTED, "dotdot-escape": _OUTSIDE}


@pytest.mark.parametrize("case", sorted(_WHY))
def test_unlisted_or_unbounded_path_refused_400_nothing_touched(rig, case):
    tmp, lib, delete, listed = rig
    cases, secret = _refusal_cases(tmp, lib)
    target = cases[case]
    sent = str(lib / ".." / "outside" / "dd.mp4.part") if case == "dotdot-escape" else str(target)
    before = {str(p): (os.path.lexists(p), p.with_name(p.name + ".meta").exists())
              for p in cases.values()}
    if case == "symlink-out":
        assert str(target) in listed(), (
            "precondition: /scan lists the symlinked .part, so only the root bound refuses it")
    r = delete(sent)
    after = {str(p): (os.path.lexists(p), p.with_name(p.name + ".meta").exists())
             for p in cases.values()}
    assert after == before, (
        f"DEL-{case.upper()}: delete of {case} changed files on disk: {before!r} -> {after!r}")
    assert secret.read_bytes() == b"SECRET" * 8
    assert r.status_code == 400, (
        f"DEL-{case.upper()}: expected 400, got {r.status_code} {r.get_json()!r}")
    body = r.get_json()
    assert body["ok"] is False and body["error"] == _WHY[case], (
        f"DEL-{case.upper()}-WHY: refused by the wrong check: {body!r}")
    assert _debris(target) == [], f"DEL-{case.upper()}: refusal left delete debris {_debris(target)!r}"


def test_delete_orphan_itself_refuses_non_part(tmp_path):
    f = _put(tmp_path / "keep.txt")
    r = cr.delete_orphan(str(f))
    assert r["ok"] is False and f.exists() and f.with_name("keep.txt.meta").exists(), (
        "DEL-PRIMITIVE: delete_orphan unlinked a non-.part path")


def test_interrupted_delete_with_own_claim_is_listed_and_finishable(rig, monkeypatch):
    _tmp, lib, delete, listed = rig
    part = _put(lib / "crash.mp4.part")
    real_unlink = Path.unlink
    fired = {"n": 0}

    def crash_once(self, *a, **k):
        if str(self) == str(part) and not fired["n"]:
            fired["n"] += 1
            raise OSError("simulated crash before unlink")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", crash_once)
    r = delete(part)
    assert fired["n"] == 1 and r.get_json()["ok"] is False and part.exists()
    assert sc.owner_path_for(part).exists(), "precondition: the operator claim survived the crash"
    monkeypatch.setattr(Path, "unlink", real_unlink)
    entry = listed().get(str(part))
    assert entry is not None, "DEL-RETRY: own interrupted delete not listed, so it can never be finished"
    r = delete(part)
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_json()
    assert not part.exists() and _debris(part) == []


def test_interrupted_delete_after_unlink_is_listed_and_finishable(rig, monkeypatch):
    _tmp, lib, delete, listed = rig
    part = _put(lib / "half.mp4.part")
    real_release = sc.release
    monkeypatch.setattr(sc, "release", lambda *a, **k: False)
    r = delete(part)
    assert r.get_json()["ok"] is False and not part.exists()
    assert cr._delete_marker_for(part).exists(), "precondition: marker retained for retry"
    monkeypatch.setattr(sc, "release", real_release)
    entry = listed().get(str(part))
    assert entry and entry.get("interrupted_delete") is True, (
        "DEL-RETRY: absent part with its own marker not listed")
    r = delete(part)
    assert r.status_code == 200 and r.get_json()["ok"] is True, r.get_json()
    assert "interrupted delete" in r.get_json().get("note", "")
    assert _debris(part) == []


def test_foreign_claim_still_not_listed(rig):
    _tmp, lib, delete, listed = rig
    part = _put(lib / "worker.mp4.part")
    assert sc._create_owner(sc.owner_path_for(part), "a" * 64)
    assert str(part) not in listed(), "a download's claim must keep the part out of /scan"
    r = delete(part)
    assert r.status_code == 400 and part.exists(), (
        f"DEL-FOREIGN-CLAIM: a claimed part was not refused: {r.status_code} {r.get_json()!r}")


def test_spillover_orphan_is_listed_and_deletable(rig, monkeypatch):
    """r1-repin: a configured spillover_dirs entry is a root (paths.download_roots).

    Under the narrow roots (download_dir only) an orphan .part on the
    spillover disk was neither listed nor deletable; the shared roots make it
    both, and a sibling outside every root stays refused."""
    from bulk_downloader import app_state
    tmp, lib, delete, listed = rig
    spill = tmp / "spill1"
    spill.mkdir()
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {
        "name": "s1", "download_dir": str(lib), "spillover_dirs": str(spill)}})
    part = _put(spill / "sub" / "clip.mp4.part")
    assert str(part) in listed(), "CRD-REPIN-SPILLOVER-NOT-LISTED"
    r = delete(part)
    assert r.status_code == 200 and r.get_json()["ok"] is True, (
        "CRD-REPIN-SPILLOVER-REFUSED", r.get_json())
    assert not part.exists() and _debris(part) == []
    out = _put(tmp / "outside" / "clip.mp4.part")
    assert delete(out).status_code == 400 and out.exists(), (
        "CRD-REPIN-OUT-OF-ROOTS-DELETED")
    young = _put(spill / "sub" / "live.mp4.part", age=0)
    assert str(young) not in listed(), "control: a young .part is not an orphan"
    assert delete(young).status_code == 400 and young.exists(), (
        "CRD-REPIN-UNLISTED-SPILLOVER-DELETED")


def test_nested_spillover_root_lists_an_orphan_once(rig, monkeypatch):
    """A spillover dir inside download_dir is walked twice; /scan lists once."""
    from bulk_downloader import app_state
    _tmp, lib, _delete, _listed = rig
    nested = lib / "spill"
    nested.mkdir()
    monkeypatch.setattr(app_state, "s_cfg", {"s1": {
        "name": "s1", "download_dir": str(lib), "spillover_dirs": str(nested)}})
    part = _put(nested / "clip.mp4.part")
    orphans = cr.scan_for_orphans(s_cfg=app_state.s_cfg, runners={})
    assert [o["path"] for o in orphans].count(str(part)) == 1, (
        "CRD-REPIN-ORPHAN-LISTED-TWICE", orphans)
