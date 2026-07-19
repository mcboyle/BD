"""v3.66.486 R3 (plugin-v3): hook-payload golden (lock the contract).

Pins every event's payload key-set with a golden so a producer-side
rename/removal trips a CI gate instead of silently quarantining live plugins.
Locks the contract BEFORE E1 triples the event surface.

The declared contract is the ``HOOK_EVENTS`` doc registry: each entry documents
``Payload: {k1, k2, ...}``. ``tools/hook_payload_golden.py`` derives that key-set
per event and pins it; the check is **subset** semantics -- a removed/renamed key
(a golden key no longer present) FAILS, but an ADDITIVE key does NOT (forward-
compat, pairs with R5's payload-schema split).

Runner-safe: zero-arg fns, no pytest builtins, paths from __file__.
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from bulk_downloader import plugins as P  # noqa: E402

_GOLDEN = _REPO / "tests" / "golden" / "hook_payloads.golden.json"


def test_tool_importable_and_golden_present():
    import hook_payload_golden as G  # noqa: F401
    assert hasattr(G, "derive_payload_keys")
    assert hasattr(G, "check")
    assert _GOLDEN.is_file(), f"golden missing: {_GOLDEN}"


def test_golden_matches_current_contract():
    """(a) The committed golden has NO violation against the current HOOK_EVENTS."""
    import hook_payload_golden as G
    golden = json.load(open(_GOLDEN))
    current = G.derive_payload_keys(P.HOOK_EVENTS)
    violations = G.check(golden, current)
    assert violations == [], violations


def test_producer_rename_trips():
    """(b) A renamed payload key (a golden key no longer present) FAILS the gate."""
    import hook_payload_golden as G
    golden = json.load(open(_GOLDEN))
    current = G.derive_payload_keys(P.HOOK_EVENTS)
    # pick any event that has at least one pinned key
    ev = next(e for e, ks in golden.items() if ks)
    renamed = {e: list(ks) for e, ks in current.items()}
    old = golden[ev][0]
    renamed[ev] = [("RENAMED_" + k if k == old else k) for k in renamed[ev]]
    violations = G.check(golden, renamed)
    assert any(ev in v for v in violations), (ev, violations)


def test_additive_field_does_not_trip():
    """(c) Adding a NEW payload key does NOT trip (subset semantics)."""
    import hook_payload_golden as G
    golden = json.load(open(_GOLDEN))
    current = G.derive_payload_keys(P.HOOK_EVENTS)
    ev = next(iter(golden))
    augmented = {e: list(ks) for e, ks in current.items()}
    augmented[ev] = sorted(set(augmented[ev]) | {"brand_new_additive_key"})
    assert G.check(golden, augmented) == []


def test_event_removal_trips():
    """A whole event vanishing from the producer side FAILS the gate."""
    import hook_payload_golden as G
    golden = json.load(open(_GOLDEN))
    current = G.derive_payload_keys(P.HOOK_EVENTS)
    ev = next(iter(golden))
    pruned = {e: ks for e, ks in current.items() if e != ev}
    violations = G.check(golden, pruned)
    assert any(ev in v for v in violations), (ev, violations)
