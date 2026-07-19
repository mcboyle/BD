"""P4-A.0 Cut 2 (v3.66.330) -- SPA per-site integration tests + storage-tier
+ spillover-check surface.

These per-site editor actions previously lived ONLY in /legacy (app.js
`/stash/diagnose` 7047, `/plex/sections` 7112, `/jellyfin/libraries` 7188,
`/hooks/spillover_check` 7584, `/storage_tier/status` 9839, `/storage_tier/run_now`
9887, plus the plex/jellyfin/qb/jd diagnose buttons), so their endpoints read
spa_wired:false in the gui_parity inventory. Deleting /legacy (Phase C) before
wiring an SPA surface would be a capability regression (operators lose the
integration connection tests, the storage-tier manual sweep + live status, and
the spillover pick check). This cut wires all of them onto the SiteActions page:

  Integration tests (GET):
    /plex/diagnose  /plex/sections  /jellyfin/diagnose  /jellyfin/libraries
    /stash/diagnose  /qb/diagnose  /jd/diagnose
  Storage tier:
    POST /storage_tier/run_now   GET /storage_tier/status
  Spillover:
    GET /hooks/spillover_check

NOT in scope (waived, both confirmed NOT legacy-reachable -> not deletion
blockers): #4 HereSphere/DeoVR probe (already wired in Integrations.tsx via
/api/jsonapi/probe) and #7 queue/save_template + queue_templates family (0
references in the legacy shell -- a backend/API-only endpoint surfaced by
neither shell).

RED-first: on pristine 329 source all 10 endpoints are spa_wired:false. The
SiteActions generic `${suffix}` POST does NOT credit them (two interpolations ->
the parity scanner can't resolve the trailing segment), so each must be wired
with a FULL /api/sites/${...}/<literal-segment> call. Builds the inventory
against the live tree.
"""
from pathlib import Path

import tools.gui_parity_inventory as g

_REPO = Path(__file__).resolve().parent.parent

_GET = [
    "GET /api/sites/<sid>/plex/diagnose",
    "GET /api/sites/<sid>/plex/sections",
    "GET /api/sites/<sid>/jellyfin/diagnose",
    "GET /api/sites/<sid>/jellyfin/libraries",
    "GET /api/sites/<sid>/stash/diagnose",
    "GET /api/sites/<sid>/qb/diagnose",
    "GET /api/sites/<sid>/jd/diagnose",
    "GET /api/sites/<sid>/storage_tier/status",
    "GET /api/sites/<sid>/hooks/spillover_check",
]
_POST = [
    "POST /api/sites/<sid>/storage_tier/run_now",
]


def _wired(items, ce):
    for it in items:
        if it.get("command_or_endpoint") == ce:
            return it.get("spa_wired")
    raise AssertionError("inventory item not found: " + ce)


def test_all_cut2_endpoints_are_spa_wired():
    """Every legacy-reachable per-site integration/storage/spillover endpoint
    must be SPA-wired after this cut."""
    inv = g.build(str(_REPO))
    items = inv["items"]
    missing = [ce for ce in (_GET + _POST) if _wired(items, ce) is not True]
    assert not missing, "still spa_wired:false -> " + repr(missing)


def test_site_actions_source_references_full_literals():
    """SiteActions must use FULL /api/sites/${...}/<segment> literals (not the
    generic `${suffix}` POST), or the parity scanner won't credit them."""
    src = (_REPO / "frontend" / "src" / "routes" / "SiteActions.tsx").read_text()
    assert "/api/sites/${" in src
    for seg in (
        "/plex/diagnose",
        "/plex/sections",
        "/jellyfin/diagnose",
        "/jellyfin/libraries",
        "/stash/diagnose",
        "/qb/diagnose",
        "/jd/diagnose",
        "/storage_tier/run_now",
        "/storage_tier/status",
        "/hooks/spillover_check",
    ):
        assert seg in src, "missing full-literal segment: " + seg
