"""v3.66.754c -- the dark singletons must gain a real SPA caller (spa_wired=True).

OPERATOR DECISION @754: WIRE (option 1). Three long-dark controls -- dropped from a prose
handoff twice -- get a genuine frontend path. The dead-letter REQUEUE (a mutating POST) is
wired together with its companion LIST (GET) so the button never posts into a view the
operator cannot see.

Targets (all spa_wired=False on the pristine tree -> RED here):
  POST   /api/live/parse_url                       -> LiveSection "check URL" affordance
  GET    /api/queue/dead_letter                    -> QueueOpsDialog dead-letter list
  POST   /api/queue/dead_letter/requeue            -> QueueOpsDialog per-row Requeue
  GET|DELETE /api/sites/<sid>/heuristic/fingerprint -> site settings inspect + reset

The scanner (gui_parity_inventory) credits spa_wired to any endpoint whose rule appears as a
FULL /api/... literal in the frontend source. So each wiring must use the literal path, never
a concatenated base var (durable rule).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import gui_parity_inventory as P  # noqa: E402

_WIRE = [
    "/api/live/parse_url",
    "/api/queue/dead_letter",
    "/api/queue/dead_letter/requeue",
    "/api/sites/<sid>/heuristic/fingerprint",
]


def _by_rule():
    d = P.build(ROOT)
    out = {}
    for i in d["items"]:
        ep = i.get("command_or_endpoint", "")
        # strip the leading "METHOD " to get the rule
        rule = ep.split(" ", 1)[-1] if " " in ep else ep
        out[rule] = i
    return out


def test_all_four_singleton_endpoints_are_spa_wired():
    idx = _by_rule()
    unwired = []
    for rule in _WIRE:
        it = idx.get(rule)
        assert it is not None, "endpoint vanished from the inventory: %s" % rule
        if it.get("spa_wired") is not True:
            unwired.append(rule)
    assert not unwired, (
        "these singletons still have NO frontend caller (spa_wired != True): %s. "
        "Each must appear as a full /api/... literal in an apiGet/apiPost/apiDelete call."
        % unwired)


def test_requeue_is_wired_together_with_its_list():
    """The mutating requeue must not be wired without its read: a requeue button with no
    dead-letter list is a control posting into a view the operator cannot see."""
    idx = _by_rule()
    requeue = idx.get("/api/queue/dead_letter/requeue")
    listing = idx.get("/api/queue/dead_letter")
    assert requeue and requeue.get("spa_wired") is True, "requeue not wired"
    assert listing and listing.get("spa_wired") is True, (
        "the dead-letter LIST is not wired -- the requeue action has no visible surface")
