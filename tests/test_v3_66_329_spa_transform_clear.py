"""P4-A.0 Cut 1 (v3.66.329) -- SPA Transform-URLs + Clear-done surface.

Two command-palette actions previously lived ONLY in /legacy (app.js
`bulkTransformPrompt` and `act('clear')`), so their per-site endpoints read
spa_wired:false in the gui_parity inventory. Deleting /legacy (Phase C) before
wiring an SPA surface would be a capability regression (operators lose bulk
URL find/replace and finished-URL housekeeping from the UI). This cut wires:

  - Transform URLs -> POST /api/sites/<sid>/bulk_url_transform
        body {pattern, replacement, dry_run}  (dry-run preview then commit)
  - Clear done     -> POST /api/sites/<sid>/clear   (remove finished URLs)

RED-first: on pristine 328 source both endpoints are spa_wired:false (no SPA
reference). After QueueOpsDialog.tsx references the FULL /api/... literals and
Queue.tsx mounts it, a LIVE inventory build credits both as spa_wired:true.

Builds the inventory against the live tree (like test_v3_66_328_spa_add_url /
test_parity_method_aware) so this asserts the real SPA wiring, not a frozen
shipped artifact.
"""
from pathlib import Path

import tools.gui_parity_inventory as g

_REPO = Path(__file__).resolve().parent.parent


def _wired(items, ce):
    for it in items:
        if it.get("command_or_endpoint") == ce:
            return it.get("spa_wired")
    raise AssertionError("inventory item not found: " + ce)


def test_transform_urls_is_spa_wired():
    """POST /api/sites/<sid>/bulk_url_transform must be SPA-wired."""
    inv = g.build(str(_REPO))
    assert _wired(inv["items"], "POST /api/sites/<sid>/bulk_url_transform") is True


def test_clear_done_is_spa_wired():
    """POST /api/sites/<sid>/clear must be SPA-wired (clear finished URLs)."""
    inv = g.build(str(_REPO))
    assert _wired(inv["items"], "POST /api/sites/<sid>/clear") is True


def test_queue_ops_dialog_source_references_full_literals():
    """The dialog must use FULL /api/... literals (templated by <sid>), not a
    concatenated base var, or the parity scanner won't credit them spa_wired."""
    src = (_REPO / "frontend" / "src" / "components" / "QueueOpsDialog.tsx").read_text()
    assert "/bulk_url_transform" in src and "/api/sites/${" in src
    assert "/clear" in src


def test_queue_route_mounts_queue_ops_dialog():
    """The Transform/Clear surface is reachable from the Queue page."""
    src = (_REPO / "frontend" / "src" / "routes" / "Queue.tsx").read_text()
    assert "QueueOpsDialog" in src
