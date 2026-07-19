"""F0.3-B tripwire — capture-dict SCHEMA-SHAPE guard (no scrub logic).

The redaction floor (`capture_artifact_redact.redact_capture` /
`scan_floor_secrets`) is the F2 secret floor for an assembled capture dict. It
walks the dict generically but STRUCTURALLY special-cases one key — `dom_log`
(routed through `_walk_dom`; everything else through the credential floor) — and
the secret-bearing surfaces it scans are the list fields `network_log` and
`action_timeline` plus the scalar page fields.

This test pins the SHAPE of the committed canonical capture model
(`tools/capture_model_golden.fixed_capture()` — the same fixture the convergence
golden uses). If a producer adds, renames, or drops a top-level capture field,
this trips RED, forcing a human to confirm the floor still covers the new/renamed
surface BEFORE it can carry a secret undetected.

It is a TRIPWIRE, not a redactor test: it contains NO redaction/scan logic by
design — shape only. The floor's behaviour is covered by the floor regression
suite (test_secret_display_never / _166 / _171 / _245). This guards the *input
shape* those rely on.

Synthetic only; browser-free; zero-arg test functions (custom-runner safe).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import capture_model_golden as G

# The committed top-level shape of an assembled capture dict. Hardcoded on
# purpose: comparing the fixture against ITSELF would never trip. A diff here
# means the capture model changed — re-confirm the floor covers the change,
# then update this set in the SAME cut.
EXPECTED_TOPLEVEL = frozenset({
    "host", "url", "title", "captured_at",
    "network_log", "dom_log",
    "action_timeline", "action_timeline_count",
})

# Keys the floor STRUCTURALLY depends on (independent of the full set, so a
# future fixture refactor can't silently drop them).
FLOOR_LOAD_BEARING = ("dom_log", "network_log", "action_timeline")


def test_toplevel_shape_pinned():
    """The capture model's top-level key set is exactly the committed shape."""
    got = set(G.fixed_capture().keys())
    missing = EXPECTED_TOPLEVEL - got
    added = got - EXPECTED_TOPLEVEL
    assert not (missing or added), (
        "capture-dict SHAPE drift — re-confirm the redaction floor covers the "
        "change, then update EXPECTED_TOPLEVEL in the same cut.\n"
        f"  dropped/renamed: {sorted(missing)}\n"
        f"  new (floor-uncovered until reviewed): {sorted(added)}"
    )


def test_floor_load_bearing_keys_present():
    """dom_log/network_log/action_timeline exist and are lists."""
    cap = G.fixed_capture()
    for k in FLOOR_LOAD_BEARING:
        assert k in cap, f"floor-load-bearing key '{k}' absent from capture model"
        assert isinstance(cap[k], list), f"'{k}' must be a list, got {type(cap[k]).__name__}"


def test_dom_log_event_shape():
    """Each dom_log event is the {type, data} shape `_walk_dom` recurses."""
    for ev in G.fixed_capture()["dom_log"]:
        assert isinstance(ev, dict), "dom_log entries must be dicts"
        assert "type" in ev and "data" in ev, "dom_log event missing type/data"


def test_network_log_entry_shape():
    """Each network_log entry carries the url/method surface the floor scans."""
    for req in G.fixed_capture()["network_log"]:
        assert isinstance(req, dict), "network_log entries must be dicts"
        assert "url" in req and "method" in req, "network_log entry missing url/method"


def test_action_timeline_effect_is_counts_only():
    """action_timeline `effect` stays F2-safe: numeric/bool counts, no URLs/values.

    The fixture comment guarantees 'structural + kinds/counts only; no URLs/values'.
    A producer that started stuffing a URL/value into effect would both break F2
    posture and trip this.
    """
    for entry in G.fixed_capture()["action_timeline"]:
        assert isinstance(entry, dict)
        eff = entry.get("effect", {})
        assert isinstance(eff, dict) and eff, "action_timeline entry missing effect counts"
        for k, v in eff.items():
            assert isinstance(v, (int, bool)), (
                f"action_timeline.effect['{k}'] must be a count/flag (int/bool), "
                f"got {type(v).__name__} — possible URL/value leak into a counts-only field"
            )
