"""v3.66.484 R5 (plugin-v3): api_version supported-range + payload-schema split.

Replaces the exact-match api gate (``int(api) != PLUGIN_API_VERSION -> skip``,
replicated across ``_validate_manifest`` + the node/.py/exec bridges) with a
**supported range** [``PLUGIN_API_MIN``, ``PLUGIN_API_MAX``] so a plugin that
declares a scalar ``api_version`` -- or a ``min_api``/``max_api`` range --
loads iff it OVERLAPS the host's range. Lands BEFORE the kinds (each kind raises
the API) so the version model is right once and an older plugin keeps loading
when the host max advances.

Also splits a **payload-schema version** (``PLUGIN_PAYLOAD_SCHEMA``) off from the
API major: additive hook-payload changes bump the payload schema, NOT the API,
so they don't force an api bump or break older plugins. ``known_events()`` now
reports BOTH versions.

At ship the host range is [2, 2] (== the old exact-match for api_version==2), so
every existing plugin loads byte-behaviour-identically; K1 later raises the max.

Runner-safe: zero-arg fns, no pytest builtins, paths from __file__, tempfile.
"""
import json
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_plugin_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _bridge(manifest_extra):
    """A bridge .py processor whose manifest carries `manifest_extra`."""
    man = {"kind": "processor", "name": "ranged", "priority": 50}
    man.update(manifest_extra)
    return (
        "# bd:bridge\n"
        "import json, sys\n"
        f"PLUGIN = {man!r}\n"
        "def handle(event, payload, ctx):\n"
        "    return {'ran': True}\n"
        "if __name__ == '__main__':\n"
        "    arg = sys.argv[1] if len(sys.argv) > 1 else ''\n"
        "    if arg == '--manifest':\n"
        "        sys.stdout.write(json.dumps(PLUGIN)); sys.exit(0)\n"
        "    raw = sys.stdin.read()\n"
        "    req = json.loads(raw) if raw.strip() else {}\n"
        "    sys.stdout.write(json.dumps({'ok': True, 'result': handle('', {}, {})}))\n"
    )


def _write(tmp, name, body):
    fp = Path(tmp) / name
    fp.write_text(body, "utf-8")
    return fp


def test_api_range_constants_exist():
    assert isinstance(P.PLUGIN_API_MIN, int)
    assert isinstance(P.PLUGIN_API_MAX, int)
    assert isinstance(P.PLUGIN_PAYLOAD_SCHEMA, int)
    assert P.PLUGIN_API_MIN <= P.PLUGIN_API_MAX
    # the host's "current" major must sit inside its own supported range
    assert P.PLUGIN_API_MIN <= P.PLUGIN_API_VERSION <= P.PLUGIN_API_MAX


def test_api_compatible_scalar_backcompat():
    ok, _ = P.api_compatible({"api_version": P.PLUGIN_API_VERSION})
    assert ok
    ok, why = P.api_compatible({"api_version": P.PLUGIN_API_MIN - 1})
    assert not ok and why
    ok, why = P.api_compatible({"api_version": P.PLUGIN_API_MAX + 1})
    assert not ok and why
    # absent manifest api is permissive (manifest optional)
    assert P.api_compatible({})[0]
    # non-int is rejected with a reason
    ok, why = P.api_compatible({"api_version": "two"})
    assert not ok and why


def test_api_compatible_range_overlap():
    lo, hi = P.PLUGIN_API_MIN, P.PLUGIN_API_MAX
    # exact host range
    assert P.api_compatible({"min_api": lo, "max_api": hi})[0]
    # a plugin that supports an older..current span overlaps
    assert P.api_compatible({"min_api": lo - 1, "max_api": hi})[0]
    # a plugin that supports current..future span overlaps
    assert P.api_compatible({"min_api": lo, "max_api": hi + 1})[0]
    # entirely below the host range -> skip
    assert not P.api_compatible({"max_api": lo - 1})[0]
    # entirely above the host range -> skip
    assert not P.api_compatible({"min_api": hi + 1})[0]
    # inverted range -> skip
    assert not P.api_compatible({"min_api": hi, "max_api": lo - 1})[0]


def test_range_plugin_loads_end_to_end():
    """(a) A plugin declaring min_api/max_api overlapping the host loads + fires."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "r.py", _bridge({"min_api": P.PLUGIN_API_MIN,
                                      "max_api": P.PLUGIN_API_MAX + 5}))
        res = P.load_all()
        assert res["loaded"] == 1, res
        assert "ranged" in [p["name"] for p in P.list_processors()]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_below_range_plugin_skips_end_to_end():
    """(b) A plugin whose whole range is below the host max is SKIPPED (not loaded)."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "old.py", _bridge({"min_api": P.PLUGIN_API_MIN - 2,
                                       "max_api": P.PLUGIN_API_MIN - 1}))
        res = P.load_all()
        assert res["loaded"] == 0, res
        assert res["skipped"] >= 1, res
        assert "ranged" not in [p["name"] for p in P.list_processors()]
    finally:
        P._plugin_dir = orig
        P.reset()


def test_above_range_plugin_skips_end_to_end():
    """(c) A plugin requiring a newer API than the host provides is SKIPPED."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        _write(tmp, "future.py", _bridge({"min_api": P.PLUGIN_API_MAX + 1}))
        res = P.load_all()
        assert res["loaded"] == 0, res
        assert res["skipped"] >= 1, res
    finally:
        P._plugin_dir = orig
        P.reset()


def test_payload_schema_independent_and_reported():
    """(e) payload-schema version is reported and is INDEPENDENT of the API major."""
    ev = P.known_events()
    assert ev.get("api_version") == P.PLUGIN_API_VERSION
    assert ev.get("payload_schema_version") == P.PLUGIN_PAYLOAD_SCHEMA
    # compatibility keys off the API range ONLY -- a payload_schema field in a
    # manifest must not affect whether the plugin is api-compatible.
    base = P.api_compatible({"api_version": P.PLUGIN_API_VERSION})[0]
    with_ps = P.api_compatible({"api_version": P.PLUGIN_API_VERSION,
                                "payload_schema": 999})[0]
    assert base is True and with_ps is True
