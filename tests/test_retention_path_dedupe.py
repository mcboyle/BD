"""Retention binds deletion to the FILE, not the history id (F3).

``apply_retention`` used to unlink a candidate row's ``filename`` whatever
else pointed at it: a re-download at the same path (a newer row), or a row
the operator excluded or protect-tagged, lost its file through an old
expired row's id. And nothing kept the unlink inside the download roots.

Contract pinned here: apply unlinks a candidate's file only when no newer
history row (any site, any status) and no excluded or keep-tagged row
references the same realpath, and the file and its directory entry lie
inside a download root of the walked config. Real files on disk are the
assertion; every diagnostic carries ``RPD-F3``.

Module shares one DB under ``bd_module_wipe``; each test uses disjoint site
ids and its own temp root.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.bd_module_wipe

BD_GATE_SCOPE = "module"

_OLD = "2020-01-01T00:00:00"
_OLDER = "2019-01-01T00:00:00"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _cfg(root, **kw) -> dict:
    return dict({"retention_days": 30, "download_dir": str(root)}, **kw)


def _mk(path, data: bytes = b"x" * 16) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _hist(site: str, path: str, ts: str, status: str = "done") -> int:
    from bulk_downloader import db as _db, retention as _rt

    _db.db_init()
    _rt._ensure_tables()
    with _db.db_conn() as cx:
        return cx.execute(
            """INSERT INTO history(site_id, site_name, url, status,
                                   filename, file_size, ts)
               VALUES (?,?,?,?,?,?,?)""",
            (site, site, f"https://x/{site}/{time.time_ns()}", status, path, 16, ts),
        ).lastrowid


def _apply(site: str, cfg: dict, ids: list) -> dict:
    from bulk_downloader import retention as r

    return r.apply_retention({site: cfg}, dry_run=False, confirm_ids=ids, site_id=site)


def _audited(ids: list) -> list:
    from bulk_downloader import db as _db

    with _db.db_conn() as cx:
        return sorted(
            row[0]
            for row in cx.execute(
                "SELECT history_id FROM retention_audit WHERE dry_run = 0 "
                f"AND history_id IN ({','.join('?' * len(ids))})",
                ids,
            )
        )


def test_newer_row_at_same_path_keeps_file(tmp_path):
    f = _mk(tmp_path / "lib" / "clip.mp4")
    old = _hist("rpd-newer", f, _OLD)
    _hist("rpd-newer", f, _now())
    res = _apply("rpd-newer", _cfg(tmp_path / "lib"), [old])
    assert os.path.exists(f), (
        f"RPD-F3 R2: file of the newer row deleted via old id {old}: {res}"
    )
    assert res["total_deleted"] == 0, f"RPD-F3 R2: {res}"
    assert res["sites"]["rpd-newer"]["kept_shared_file"] == 1, res


def test_excluded_row_at_same_path_keeps_file(tmp_path):
    f = _mk(tmp_path / "lib" / "keep.mp4")
    from bulk_downloader import retention as r

    excl = _hist("rpd-excl", f, _OLDER)  # older: only exclusion saves it
    cand = _hist("rpd-excl", f, _OLD)
    assert r.mark_excluded(excl, True) is True
    res = _apply("rpd-excl", _cfg(tmp_path / "lib"), [cand])
    assert os.path.exists(f), (
        f"RPD-F3 R4: file of excluded row {excl} deleted via {cand}: {res}"
    )
    assert res["total_deleted"] == 0, f"RPD-F3 R4: {res}"


def test_keep_tagged_row_at_same_path_keeps_file(tmp_path):
    from bulk_downloader import tags

    f = _mk(tmp_path / "lib" / "tagged.mp4")
    pinned = _hist("rpd-tag", f, _OLDER)
    cand = _hist("rpd-tag", f, _OLD)
    tags.add_tag([pinned], "keep")
    res = _apply(
        "rpd-tag", _cfg(tmp_path / "lib", retention_keep_tagged_with=["keep"]), [cand]
    )
    assert os.path.exists(f), (
        f"RPD-F3 pinned: file of keep-tagged row {pinned} deleted: {res}"
    )


def test_sole_expired_row_is_deleted(tmp_path):
    """Control: nothing else references the file, so it goes."""
    f = _mk(tmp_path / "lib" / "alone.mp4")
    cand = _hist("rpd-sole", f, _OLD)
    res = _apply("rpd-sole", _cfg(tmp_path / "lib"), [cand])
    assert not os.path.exists(f), f"RPD-F3 control: sole row kept: {res}"
    assert res["total_deleted"] == 1, res
    assert _audited([cand]) == [cand]


def test_newer_row_unconfirmed_still_keeps_file(tmp_path):
    """The newer row is expired too but the operator confirmed only the old
    one: the newer row still owns the file."""
    f = _mk(tmp_path / "lib" / "both.mp4")
    old = _hist("rpd-unconf", f, _OLDER)
    _hist("rpd-unconf", f, _OLD)
    res = _apply("rpd-unconf", _cfg(tmp_path / "lib"), [old])
    assert os.path.exists(f), f"RPD-F3 unconfirmed newer: {res}"


def test_both_rows_confirmed_unlinks_once_under_newest(tmp_path):
    f = _mk(tmp_path / "lib" / "pair.mp4")
    old = _hist("rpd-pair", f, _OLDER)
    new = _hist("rpd-pair", f, _OLD)
    res = _apply("rpd-pair", _cfg(tmp_path / "lib"), [old, new])
    assert not os.path.exists(f), f"RPD-F3 pair: file kept: {res}"
    assert res["total_deleted"] == 1, f"RPD-F3 pair: {res}"
    assert _audited([old, new]) == [new], "RPD-F3 pair: audit not on newest"


def test_newer_row_of_another_site_keeps_file(tmp_path):
    f = _mk(tmp_path / "lib" / "cross.mp4")
    old = _hist("rpd-xa", f, _OLD)
    _hist("rpd-xb", f, _now(), status="failed")
    res = _apply("rpd-xa", _cfg(tmp_path / "lib"), [old])
    assert os.path.exists(f), f"RPD-F3 cross-site newer row: {res}"


def test_newer_row_via_symlink_alias_keeps_file(tmp_path):
    f = _mk(tmp_path / "lib" / "a" / "alias.mp4")
    link = tmp_path / "lib" / "b" / "alias.mp4"
    os.makedirs(link.parent)
    os.symlink(f, link)
    old = _hist("rpd-alias", f, _OLD)
    _hist("rpd-alias", str(link), _now())
    res = _apply("rpd-alias", _cfg(tmp_path / "lib"), [old])
    assert os.path.exists(f), f"RPD-F3 alias: realpath owner missed: {res}"


def test_file_outside_roots_is_not_deleted(tmp_path):
    f = _mk(tmp_path / "outside" / "loose.mp4")
    os.makedirs(tmp_path / "lib")
    cand = _hist("rpd-out", f, _OLD)
    res = _apply("rpd-out", _cfg(tmp_path / "lib"), [cand])
    assert os.path.exists(f), f"RPD-F3 R3: outside-root file deleted: {res}"
    assert any(
        "outside the download roots" in e for e in res["sites"]["rpd-out"]["errors"]
    ), res


def test_symlink_escape_is_not_followed(tmp_path):
    target = _mk(tmp_path / "outside" / "secret.mp4")
    link = tmp_path / "lib" / "secret.mp4"
    os.makedirs(link.parent)
    os.symlink(target, link)
    cand = _hist("rpd-esc", str(link), _OLD)
    res = _apply("rpd-esc", _cfg(tmp_path / "lib"), [cand])
    assert os.path.lexists(link) and os.path.exists(target), (
        f"RPD-F3 R3: symlink out of the root unlinked: {res}"
    )


def test_entry_outside_roots_resolving_inside_is_not_unlinked(tmp_path):
    inside = _mk(tmp_path / "lib" / "real.mp4")
    link = tmp_path / "outside" / "real.mp4"
    os.makedirs(link.parent)
    os.symlink(inside, link)
    cand = _hist("rpd-ent", str(link), _OLD)
    res = _apply("rpd-ent", _cfg(tmp_path / "lib"), [cand])
    assert os.path.lexists(link), (
        f"RPD-F3 R3: directory entry outside the root unlinked: {res}"
    )


def test_route_apply_keeps_file_of_newer_row(tmp_path, monkeypatch):
    """The SPA path: preview, then POST /api/retention/apply with the old
    id confirmed (probe R2)."""
    from bulk_downloader import app as a, app_state

    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    lib = tmp_path / "lib"
    f = _mk(lib / "route.mp4")
    old = _hist("rpd-route", f, _OLD)
    _hist("rpd-route", f, _now())
    monkeypatch.setattr(app_state, "s_cfg", {"rpd-route": _cfg(lib)})
    c = a.app.test_client()
    h = {"Host": "localhost"}
    pv = c.get("/api/retention/preview/rpd-route", headers=h).get_json()
    assert [x["id"] for x in pv["candidates"]] == [old], pv
    r = c.post(
        "/api/retention/apply",
        headers=h,
        json={"dry_run": False, "confirm_ids": [old], "site_id": "rpd-route"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert os.path.exists(f), f"RPD-F3 route: newer row's file deleted: {r.get_json()}"


# r2: the roots are every directory a site's files land in (paths.py), and a
# row sharing the file is protected by ITS OWN site's keep-tags.


def test_spillover_file_sole_expired_row_deleted(tmp_path):
    """The runner lands downloads in spillover_dirs when the primary is full
    (lens R272-B probe P1): retention must still free them."""
    os.makedirs(tmp_path / "lib")
    f = _mk(tmp_path / "spill" / "s.mp4")
    cand = _hist("rpd-spill", f, _OLD)
    cfg = _cfg(tmp_path / "lib", spillover_dirs=f"\n{tmp_path / 'spill'}\n")
    res = _apply("rpd-spill", cfg, [cand])
    assert not os.path.exists(f), f"RPD-ROOTS spillover file kept: {res}"
    assert res["total_deleted"] == 1, res


def test_storage_tier_file_sole_expired_row_deleted(tmp_path):
    os.makedirs(tmp_path / "lib")
    f = _mk(tmp_path / "cold" / "t.mp4")
    cand = _hist("rpd-tier", f, _OLD)
    cfg = _cfg(tmp_path / "lib", storage_tier_dir=str(tmp_path / "cold"))
    res = _apply("rpd-tier", cfg, [cand])
    assert not os.path.exists(f), f"RPD-ROOTS storage-tier file kept: {res}"


def test_outside_every_root_refused_blank_and_relative_not_widened(tmp_path):
    """Negative control: download_dir, spillover and tier all set, plus a
    blank and a relative spillover line -- a file outside all of them stays."""
    for d in ("lib", "spill", "cold"):
        os.makedirs(tmp_path / d)
    f = _mk(tmp_path / "other" / "o.mp4")
    cand = _hist("rpd-none", f, _OLD)
    cfg = _cfg(
        tmp_path / "lib",
        spillover_dirs=f"  \nrelative/dir\n{tmp_path / 'spill'}",
        storage_tier_dir=str(tmp_path / "cold"),
    )
    res = _apply("rpd-none", cfg, [cand])
    assert os.path.exists(f), f"RPD-ROOTS outside-root file deleted: {res}"
    assert any(
        "outside the download roots" in e for e in res["sites"]["rpd-none"]["errors"]
    ), res


def test_default_roots_include_spillover_and_tier(tmp_path, monkeypatch):
    """The one-argument form the sibling routes call reads the live config."""
    from bulk_downloader import app_state, paths

    cfg = _cfg(
        tmp_path / "lib",
        spillover_dirs=str(tmp_path / "spill"),
        storage_tier_dir=str(tmp_path / "cold"),
    )
    monkeypatch.setattr(app_state, "s_cfg", {"rpd-dflt": cfg})
    for d in ("lib", "spill", "cold"):
        assert paths.within_download_roots(str(tmp_path / d / "f.mp4")), (
            f"RPD-ROOTS default roots miss {d}: {paths._configured_roots()}"
        )
    assert not paths.within_download_roots(str(tmp_path / "other" / "f.mp4"))


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_default_dir_site_without_download_dir_deleted(tmp_path, monkeypatch, blank):
    """A site with no download_dir of its own downloads into the deployment
    default (runner._resolve_write_dir -> app._oi_default_download_dir, here
    BD_DOWNLOAD_DIR); retention must still free it (lens R289-B probe q1)."""
    d = tmp_path / "dflt"
    monkeypatch.setenv("BD_DOWNLOAD_DIR", str(d))
    f = _mk(d / "q1.mp4")
    cand = _hist("rpd-dflt-blank", f, _OLD)
    cfg = {"retention_days": 30}
    if blank is not None:
        cfg["download_dir"] = blank
    res = _apply("rpd-dflt-blank", cfg, [cand])
    assert not os.path.exists(f), f"RPD-ROOTS default-dir file kept: {res}"


def test_default_dir_is_no_root_when_every_site_names_one(tmp_path, monkeypatch):
    """Control: the default joins the roots only for a blank site; with every
    download_dir set, a file in BD_DOWNLOAD_DIR stays outside."""
    os.makedirs(tmp_path / "lib")
    d = tmp_path / "dflt"
    monkeypatch.setenv("BD_DOWNLOAD_DIR", str(d))
    f = _mk(d / "n.mp4")
    cand = _hist("rpd-dflt-set", f, _OLD)
    res = _apply("rpd-dflt-set", _cfg(tmp_path / "lib"), [cand])
    assert os.path.exists(f), f"RPD-ROOTS default dir widened a named site: {res}"


@pytest.mark.parametrize("dflt", ["", "   ", None])
def test_blank_default_dir_is_skipped_never_root(tmp_path, monkeypatch, dflt):
    """A blank deployment default adds no root -- never "" or "/"."""
    from bulk_downloader import app, paths

    monkeypatch.setattr(app, "_oi_default_download_dir", lambda: dflt)
    f = _mk(tmp_path / "any" / "b.mp4")
    cand = _hist("rpd-dflt-none", f, _OLD)
    res = _apply("rpd-dflt-none", {"retention_days": 30, "download_dir": ""}, [cand])
    assert os.path.exists(f), f"RPD-ROOTS blank default widened the roots: {res}"
    roots = paths.download_roots({"s": {"download_dir": ""}})
    assert all(r.strip() and r.strip() != "/" for r in roots), roots


def test_other_site_row_pinned_by_its_own_keep_tag_keeps_file(tmp_path):
    """Lens R272-B probe P3: site B's row, tagged with B's keep tag, shares
    the file; applying site A must not delete it."""
    from bulk_downloader import retention as r, tags

    f = _mk(tmp_path / "lib" / "x.mp4")
    pinned = _hist("rpd-pb", f, _OLDER)
    tags.add_tag([pinned], "keepb")
    cand = _hist("rpd-pa", f, _OLD)
    s_cfg = {
        "rpd-pa": _cfg(tmp_path / "lib", retention_keep_tagged_with="keepa"),
        "rpd-pb": _cfg(tmp_path / "lib", retention_keep_tagged_with="keepb"),
    }
    res = r.apply_retention(s_cfg, dry_run=False, confirm_ids=[cand], site_id="rpd-pa")
    assert os.path.exists(f), (
        f"RPD-F3 cross-site pin: site B row {pinned} lost its file via A: {res}"
    )


def test_other_site_row_tagged_outside_its_policy_does_not_keep_file(tmp_path):
    """Control: B's row carries A's tag, not B's -- B's policy does not pin
    it, and no other row protects the file, so A's expired row deletes it."""
    from bulk_downloader import retention as r, tags

    f = _mk(tmp_path / "lib" / "z.mp4")
    other = _hist("rpd-qb", f, _OLDER)
    tags.add_tag([other], "keepa")
    cand = _hist("rpd-qa", f, _OLD)
    s_cfg = {
        "rpd-qa": _cfg(tmp_path / "lib", retention_keep_tagged_with="keepa"),
        "rpd-qb": _cfg(tmp_path / "lib", retention_keep_tagged_with="keepb"),
    }
    res = r.apply_retention(s_cfg, dry_run=False, confirm_ids=[cand], site_id="rpd-qa")
    assert not os.path.exists(f), f"RPD-F3 own-policy control: file kept: {res}"


def test_other_site_pin_keeps_file_when_walked_site_has_no_keep_tags(tmp_path):
    """The walked site has no tag policy at all; B's pin still counts."""
    from bulk_downloader import retention as r, tags

    f = _mk(tmp_path / "lib" / "w.mp4")
    pinned = _hist("rpd-nb", f, _OLDER)
    tags.add_tag([pinned], "keepb")
    cand = _hist("rpd-na", f, _OLD)
    s_cfg = {
        "rpd-na": _cfg(tmp_path / "lib"),
        "rpd-nb": _cfg(tmp_path / "lib", retention_keep_tagged_with="keepb"),
    }
    res = r.apply_retention(s_cfg, dry_run=False, confirm_ids=[cand], site_id="rpd-na")
    assert os.path.exists(f), (
        f"RPD-F3 cross-site pin, untagged walked site: row {pinned} lost its file: {res}"
    )


def test_route_apply_keeps_file_pinned_by_other_site_keep_tag(tmp_path, monkeypatch):
    """P3 on the SPA path: the route hands apply_retention the whole live
    config, so site B's keep-tag policy is visible when site A is applied."""
    from bulk_downloader import app as a, app_state, tags

    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    lib = tmp_path / "lib"
    f = _mk(lib / "rp.mp4")
    pinned = _hist("rpd-rb", f, _OLDER)
    tags.add_tag([pinned], "keeprb")
    cand = _hist("rpd-ra", f, _OLD)
    monkeypatch.setattr(app_state, "s_cfg", {
        "rpd-ra": _cfg(lib),
        "rpd-rb": _cfg(lib, retention_keep_tagged_with="keeprb"),
    })
    r = a.app.test_client().post(
        "/api/retention/apply",
        headers={"Host": "localhost"},
        json={"dry_run": False, "confirm_ids": [cand], "site_id": "rpd-ra"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert os.path.exists(f), (
        f"RPD-F3 route cross-site pin: row {pinned} lost its file: {r.get_json()}"
    )
