"""PIN_INDEX in-sync + invariant gate (KB Tier-A / A3).

PIN_INDEX.json is an AST scan of tests/*.py that surfaces the drift-prone, hand-typed
pin forms so `what-pins X` is a query, not a grep — and so the count-dict class behind the
v3.66.302 miss is impossible to ship stale undetected.

v1 scope (deliberately honest, non-over-capturing, non-forking):
  - DIRECTLY indexed: `version` (assert __version__ == "X") and `count_dict`
    (assert <expr> == {<str>: <int>, ...}, the 302 class).
  - HANDLED ELSEWHERE (coverage-mapped, not re-indexed): guard SHAs live in guards.json
    (gated by bd-guardcheck and verify_release); route-count pins are
    gated by tools/check_route_counts.py (G12). PIN_INDEX points at them rather than
    forking those gates.

AST is the engine because the version-pin *fixtures* (test_release_hygiene_gates,
test_scan_version_pins_fixture) hold their pins inside string literals — an AST walk sees
those as Constants, never as Assert nodes, so the bump()-footgun fixture-vs-pin confusion
cannot happen. A fixture-file allowlist is belt-and-suspenders on top.

Gating choice mirrors ROUTE_INDEX: the in-sync diff compares the STABLE projection
(form, file, value, gates_what) — `line` is regenerated but NOT equality-gated, so the gate
fires on a real pin change (added / removed / value drifted), not on an unrelated line shift.
"""
import os
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN_INDEX = ROOT / "PIN_INDEX.json"

_FIXTURE_FILES = {"test_release_hygiene_gates.py", "test_scan_version_pins_fixture.py"}
_STABLE = ("form", "file", "value", "gates_what")


def _proj(pins):
    return sorted([{k: p.get(k) for k in _STABLE} for p in pins],
                  key=lambda d: (d["file"], str(d["form"]), str(d["value"]), str(d["gates_what"])))


def _load():
    assert PIN_INDEX.exists(), "PIN_INDEX.json missing — run `python tools/build_pin_index.py`"
    return json.loads(PIN_INDEX.read_text(encoding="utf-8"))


def _regen():
    import importlib
    import tools.build_pin_index as bpi
    importlib.reload(bpi)
    return bpi.build_index()


def test_pin_index_exists_and_parses():
    d = _load()
    assert isinstance(d, dict) and "pins" in d and isinstance(d["pins"], list)
    assert d.get("schema_version")
    assert "coverage" in d and "covers" in d["coverage"], "must ship a coverage map"


def test_pin_index_in_sync():
    committed = _load()
    regen = _regen()
    assert _proj(regen["pins"]) == _proj(committed["pins"]), (
        "PIN_INDEX.json is stale — regenerate with `python tools/build_pin_index.py` "
        "(a pin was added/removed or its value drifted)"
    )


def test_every_pin_has_required_fields():
    d = _load()
    for p in d["pins"]:
        for k in ("form", "file", "line", "value", "gates_what"):
            assert k in p, f"pin missing '{k}': {p}"
        assert p["form"] in ("version", "count_dict"), p["form"]


def test_pin_paths_are_platform_independent_posix_paths():
    d = _load()
    assert all("\\" not in p["file"] for p in d["pins"]), d["pins"]


def test_no_pins_from_fixture_files():
    """The bump()-footgun guard: synthetic pin strings in fixture files must never
    be indexed as real pins (AST already excludes them; this is belt-and-suspenders)."""
    d = _load()
    bad = [p for p in d["pins"] if Path(p["file"]).name in _FIXTURE_FILES]
    assert not bad, f"fixture-file pins leaked into the index: {bad}"


def test_canonical_302_count_dict_is_captured():
    """Positive control: the exact count-dict pin behind the v3.66.302 miss must be
    in the index — proof PIN_INDEX covers the class it was built for."""
    d = _load()
    hits = [p for p in d["pins"]
            if p["form"] == "count_dict" and "test_v3_66_302" in p["file"]]
    assert hits, "the v3.66.302 count-dict pin is not captured"
    # its value must carry the three gated-blueprint keys
    val = str(hits[0]["value"])
    assert "data_layer." in val and "report_center." in val and "actions_center." in val, val


def test_version_pins_match_live_version():
    """Bonus gate: every indexed version pin must equal the live __version__.
    Catches a forgotten 3-part bump (e.g. __init__ bumped, slice4 pin not)."""
    from bulk_downloader import __version__
    d = _load()
    vpins = [p for p in d["pins"] if p["form"] == "version"]
    assert vpins, "no version pin indexed (expected at least the slice4 pin)"
    for p in vpins:
        assert p["value"] == __version__, (
            f"version pin {p['file']}:{p['line']} == {p['value']!r} but live "
            f"__version__ == {__version__!r} — a 3-part bump is incomplete"
        )
