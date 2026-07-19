"""API validation tests for the OpenAPI export (#20 / P7).

Asserts the generated spec stays in sync with app.url_map (every non-boring route
is documented), carries the correct version, and marks CSRF-required operations.

Imports the full app once (like tests/test_endpoint_catalog_in_sync.py). Runs
under run_tests.py: zero-arg functions, repo root from __file__.
"""
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import build_endpoint_catalog as BEC  # noqa: E402
import build_openapi as BO  # noqa: E402

_APP = None
_SPEC = None


def _spec():
    global _APP, _SPEC
    if _SPEC is None:
        _APP = BEC._import_app()
        _SPEC = BO.build_openapi_dict(_APP, version=BO._version(str(_REPO)))
    return _SPEC


def _expected_paths():
    app = _APP or BEC._import_app()
    out = set()
    for rule in app.url_map.iter_rules():
        methods = [m for m in (rule.methods or set()) if m not in BEC._BORING_METHODS]
        if not methods:
            continue
        oas_path, _ = BO._flask_path_to_openapi(rule.rule)
        out.add(oas_path)
    return out


def test_spec_is_openapi_31_and_versioned():
    s = _spec()
    assert s["openapi"] == "3.1.0"
    # version matches __init__.py
    with open(_REPO / "bulk_downloader" / "__init__.py") as fh:
        src = fh.read()
    assert s["info"]["version"] in src
    assert s["info"]["version"] != "0.0.0"


def test_spec_has_many_paths_and_is_json_serializable():
    s = _spec()
    assert len(s["paths"]) > 500, len(s["paths"])
    json.dumps(s)  # must not raise


def test_every_url_map_route_is_documented():
    s = _spec()
    documented = set(s["paths"])
    expected = _expected_paths()
    missing = expected - documented
    assert not missing, f"{len(missing)} routes missing from spec, e.g. {sorted(missing)[:5]}"


def test_csrf_post_route_is_marked():
    s = _spec()
    # the existing template_manager promote endpoint is a CSRF-tripping POST
    promote = s["paths"].get("/api/template_manager/promote", {})
    assert "post" in promote, list(promote)
    assert promote["post"]["x-csrf-required"] is True
    # and a header parameter was added for it
    names = [p["name"] for p in promote["post"].get("parameters", [])]
    assert "X-CSRF-Token" in names, names


def test_plain_get_not_csrf_marked():
    s = _spec()
    # /api/health is a plain GET, never CSRF-gated
    health = s["paths"].get("/api/health", {})
    if "get" in health:
        assert health["get"]["x-csrf-required"] is False


def test_path_params_converted_to_braces():
    s = _spec()
    # no Flask-style <...> should survive in any documented path
    assert not any("<" in p or ">" in p for p in s["paths"]), \
        [p for p in s["paths"] if "<" in p][:3]
