"""v3.66.498 O1 (plugin-v3): config-schema -> auto-GUI.

A plugin may declare a ``config_schema`` in its PLUGIN manifest (a JSON-Schema
object: ``{type:"object", properties:{...}, required:[...]}``). This slice
normalizes that schema into a render-ready FORM MODEL -- a flat list of fields
with a UI control type, label, default, required flag, enum choices, and help
text -- so the GUI can auto-render a config form with zero per-plugin UI code.

ADDITIVE: ``config_schema`` is an optional manifest field (the manifest already
accepts unknown keys), so no api bump and no break for schema-less plugins. The
normalized schemas are surfaced via the EXISTING ``/api/plugins/config`` GET
response (additive ``schemas`` field, NO new route). The React form component is
the tsc+vite half.

Runner-safe; restores module globals in finally.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402

_SCHEMA = {
    "type": "object",
    "properties": {
        "endpoint": {"type": "string", "title": "Endpoint URL",
                     "default": "https://example.test", "description": "Where to POST"},
        "retries": {"type": "integer", "default": 3},
        "verbose": {"type": "boolean", "default": False, "title": "Verbose logging"},
        "mode": {"type": "string", "enum": ["fast", "safe"], "default": "safe"},
    },
    "required": ["endpoint"],
}


def _fields_by_name(fields):
    return {f["name"]: f for f in fields}


def test_normalize_maps_types_to_controls():
    fields = P._normalize_config_schema(_SCHEMA)
    by = _fields_by_name(fields)
    assert by["endpoint"]["type"] == "text"
    assert by["retries"]["type"] == "number"
    assert by["verbose"]["type"] == "checkbox"
    assert by["mode"]["type"] == "select"  # string + enum -> select


def test_normalize_carries_label_default_required_enum_help():
    by = _fields_by_name(P._normalize_config_schema(_SCHEMA))
    assert by["endpoint"]["label"] == "Endpoint URL"
    assert by["endpoint"]["default"] == "https://example.test"
    assert by["endpoint"]["required"] is True
    assert by["endpoint"]["help"] == "Where to POST"
    assert by["retries"]["required"] is False
    # a field with no title falls back to the key name
    assert by["retries"]["label"] == "retries"
    assert by["mode"]["enum"] == ["fast", "safe"]


def test_normalize_empty_or_bad_schema_is_empty_list():
    assert P._normalize_config_schema(None) == []
    assert P._normalize_config_schema({}) == []
    assert P._normalize_config_schema({"type": "object"}) == []  # no properties
    assert P._normalize_config_schema("garbage") == []


def test_plugin_config_schemas_from_manifests():
    P.reset()
    try:
        P._manifests["demo.py"] = {"name": "demo", "api_version": 2,
                                   "config_schema": _SCHEMA}
        P._manifests["noschema.py"] = {"name": "noschema", "api_version": 2}
        schemas = P.plugin_config_schemas()
        assert "demo.py" in schemas
        assert "noschema.py" not in schemas  # only plugins WITH a schema
        assert _fields_by_name(schemas["demo.py"])["mode"]["type"] == "select"
    finally:
        P.reset()


def test_config_endpoint_folds_schemas_in_source():
    src = (_REPO / "bulk_downloader" / "app_plugins.py").read_text(encoding="utf-8")
    assert "plugin_config_schemas" in src
    assert "schemas" in src
