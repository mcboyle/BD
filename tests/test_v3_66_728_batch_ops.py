"""v3.66.728 -- the /api/batch/* CONTROL cluster: retry, move, dedup_scan.

Three of the 38 genuinely-dark CONTROL endpoints, all on one page. `BatchOps.tsx` already
wires `/api/batch/delete` with a dry-run PREVIEW then a typed-confirm live apply. The other
three share the same `{filter, dry_run}` contract and were reachable from nothing.

THE TRAPS, and both are the shapes this program keeps finding:

  1. `/api/batch/move` REQUIRES `target_dir` and answers 400 "target_dir required" without
     it. A button that posts `{filter, dry_run}` and no target is a DEAD CONTROL -- the
     exact 724/726 bug (a control calling the right route with a body it refuses). The
     type-aware gate at 727 would catch it, but the test below states it directly.

  2. Asserting a route LITERAL proves nothing about a working control. These tests assert
     the route path AND the request BODY, because body shape is precisely what the
     reachability ledger is blind to.

`dedup_scan` is a POST but is READ-ONLY (it scans and reports duplicates). It gets no
confirm gate and no dry_run -- inventing one would be cargo-culting a safety ritual onto a
call that changes nothing, and safety theatre teaches operators to click through.

RED-first: all of it fails on pristine v3.66.727.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FE = os.path.join(ROOT, "frontend", "src", "routes", "BatchOps.tsx")


def _code():
    """Source with COMMENTS STRIPPED -- a comment naming a route is not a call to it."""
    src = open(FE, encoding="utf-8").read()
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    return src


def _windows(src, route, size=320):
    out, i = [], 0
    while True:
        ix = src.find(route, i)
        if ix == -1:
            break
        out.append(src[ix:ix + size])
        i = ix + 1
    assert out, f"{route} is not called from BatchOps"
    return out


def _body_has(route, key):
    return any(key in w for w in _windows(_code(), route))


# ── the three routes are actually called ───────────────────────────────────
def test_batch_retry_is_wired():
    assert "/api/batch/retry" in _code()


def test_batch_move_is_wired():
    assert "/api/batch/move" in _code()


def test_batch_dedup_scan_is_wired():
    assert "/api/batch/dedup_scan" in _code()


# ── and they send bodies the endpoints ACCEPT ──────────────────────────────
def test_retry_sends_a_filter():
    assert _body_has("/api/batch/retry", "filter")


def test_retry_previews_before_it_applies():
    """`bulk_retry` defaults dry_run=True. The control must be explicit about which it is
    doing rather than relying on a default it cannot see."""
    assert _body_has("/api/batch/retry", "dry_run")


def test_move_sends_target_dir():
    """THE DEAD-CONTROL TRAP. /api/batch/move answers 400 "target_dir required" without it.
    A move button that posts only {filter, dry_run} can never succeed -- and every ledger
    we own would score it WIRED."""
    assert _body_has("/api/batch/move", "target_dir"), (
        "the move control sends no target_dir -- the endpoint answers 400 'target_dir "
        "required', so the button can never work")


def test_move_previews_before_it_applies():
    assert _body_has("/api/batch/move", "dry_run")


def test_dedup_scan_sends_its_params():
    assert _body_has("/api/batch/dedup_scan", "min_file_size_mb")


# ── destructive ops stay gated; the read-only one does not get fake gating ──
def test_move_is_confirm_gated():
    """Moving files on disk is destructive-adjacent and irreversible in practice."""
    src = _code()
    assert "moveConfirm" in src, "the live move is not confirm-gated"


def test_dedup_scan_is_not_confirm_gated():
    """It CHANGES NOTHING. A confirm dialog in front of a read-only scan is safety theatre,
    and theatre is how operators learn to click through the real ones."""
    src = _code()
    assert "dedupConfirm" not in src, (
        "a read-only scan has been given a confirm gate -- that trains click-through")


# ── the endpoints themselves still enforce their contract ──────────────────
def _client():
    from bulk_downloader.app import app

    return app.test_client()


def _csrf(c):
    c.get("/")
    t = (c.get("/api/csrf").get_json() or {}).get("csrf_token")
    return {"X-CSRFToken": t, "X-CSRF-Token": t, "Content-Type": "application/json"}


def test_move_still_rejects_a_missing_target_dir():
    """Pin the contract the control must satisfy. If this ever starts returning 200 on a
    missing target_dir, a bodyless mover becomes silently 'fine'."""
    c = _client()
    r = c.post("/api/batch/move", json={"filter": {}, "dry_run": True}, headers=_csrf(c))
    assert r.status_code == 400
    assert "target_dir" in (r.get_json() or {}).get("error", "")


def test_batch_routes_are_csrf_gated():
    c = _client()
    c.get("/")  # a browser session with no token
    for r_ in ("retry", "move", "dedup_scan"):
        resp = c.post(f"/api/batch/{r_}", json={})
        assert resp.status_code == 403, f"/api/batch/{r_} is not CSRF-gated"
