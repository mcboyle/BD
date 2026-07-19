"""PHA-ADDURL (v3.66.328) -- SPA Add-URL / Add-URL-list surface.

The Add-URL affordance previously lived ONLY in /legacy ("Load URLs" panel),
so the two enqueue endpoints read spa_wired:false in the gui_parity inventory.
Deleting /legacy (Phase C) before wiring an SPA surface would be a capability
regression (operators lose manual URL queueing from the UI). This cut wires:

  - single URL  -> POST /api/queue/v2/add_url        (body {site_id, url})
  - URL list    -> POST /api/sites/<sid>/load_urls   (body {text})

RED-first: on pristine 327 source both endpoints are spa_wired:false (no SPA
reference). After AddUrlDialog.tsx references the FULL /api/... literals and
Queue.tsx mounts it, a LIVE inventory build credits both as spa_wired:true.

Builds the inventory against the live tree (like test_parity_method_aware) so
this asserts the real SPA wiring, not a frozen shipped artifact.
"""
from pathlib import Path

import tools.gui_parity_inventory as g

_REPO = Path(__file__).resolve().parent.parent


def _wired(items, ce):
    for it in items:
        if it.get("command_or_endpoint") == ce:
            return it.get("spa_wired")
    raise AssertionError("inventory item not found: " + ce)


def test_add_url_single_is_spa_wired():
    """POST /api/queue/v2/add_url must be SPA-wired (single-URL add)."""
    inv = g.build(str(_REPO))
    assert _wired(inv["items"], "POST /api/queue/v2/add_url") is True


def test_add_url_list_is_spa_wired():
    """POST /api/sites/<sid>/load_urls must be SPA-wired (paste/upload list)."""
    inv = g.build(str(_REPO))
    assert _wired(inv["items"], "POST /api/sites/<sid>/load_urls") is True


def test_add_url_dialog_source_references_full_literals():
    """The dialog must use FULL /api/... literals (not a concatenated base var),
    or the parity scanner won't credit them spa_wired."""
    src = (_REPO / "frontend" / "src" / "components" / "AddUrlDialog.tsx").read_text()
    assert "/api/queue/v2/add_url" in src
    # the list path is a templated literal -> the <sid> segment is interpolated
    assert "/load_urls" in src and "/api/sites/${" in src


def test_queue_route_mounts_add_url_dialog():
    """The Add-URL surface is reachable from the Queue page."""
    src = (_REPO / "frontend" / "src" / "routes" / "Queue.tsx").read_text()
    assert "AddUrlDialog" in src
