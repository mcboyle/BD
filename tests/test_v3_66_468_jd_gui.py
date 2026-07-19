"""v3.66.468 WS4b: JD GUI gap -- backend selector + typed jd_host/jd_port.

The per-site `backend` key (teach/jd/qb) was config-file-only: it rendered in
the gui-safe site editor as a FREE-TEXT field, so a user couldn't discover the
valid values or actually switch a site to JD from the UI. WS4b types it as an
ENUM with explicit choices and gives jd_host/jd_port proper string/integer
types + descriptions, so SiteSettings renders a dropdown + labelled fields.
All three are already in CFG_FIELDS + the gui-safe editable set (integration /
general category), so no new route and no editable-set change -- only typing.

Runner-safe: zero-arg test fns, paths from __file__.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import site_editor as SE  # noqa: E402
from bulk_downloader import app_settings_center as SC  # noqa: E402


def test_backend_is_enum_with_choices():
    enums = getattr(SE, "_FIELD_ENUMS", {})
    assert "backend" in enums, enums
    assert enums["backend"] == ["teach", "jd", "qb"], enums["backend"]


def test_jd_fields_typed():
    assert SE._FIELD_TYPES.get("jd_host", (None,))[0] == "string", SE._FIELD_TYPES.get("jd_host")
    assert SE._FIELD_TYPES.get("jd_port", (None,))[0] == "integer", SE._FIELD_TYPES.get("jd_port")
    # backend keeps a valid JSON-Schema type (string); its enum-ness lives in
    # _FIELD_ENUMS, and generate_json_schema emits string+enum (never type=enum).
    assert SE._FIELD_TYPES.get("backend", (None,))[0] == "string", SE._FIELD_TYPES.get("backend")


def test_backend_schema_is_string_with_enum():
    sch = SE.generate_json_schema(["backend", "jd_host", "jd_port"])
    prop = sch["additionalProperties"]["properties"]["backend"]
    assert prop["type"] == "string", prop
    assert prop.get("enum") == ["teach", "jd", "qb"], prop


def test_descriptor_carries_enum():
    d = SC._field_descriptor("backend", current="teach")
    assert d["type"] == "enum", d
    assert d.get("enum") == ["teach", "jd", "qb"], d


def test_non_enum_field_has_no_enum_key_value():
    d = SC._field_descriptor("jd_host", current="127.0.0.1")
    assert d.get("enum") in (None, []), d
