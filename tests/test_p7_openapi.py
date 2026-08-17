"""Behavioral validation for the canonical live OpenAPI producer."""

import json
import os

os.environ["BD_DISABLE_KEEPALIVE"] = "1"

from bulk_downloader import __version__, openapi_spec  # noqa: E402
from bulk_downloader.app import app as APP  # noqa: E402

_SPEC = None


def _spec():
    global _SPEC
    if _SPEC is None:
        _SPEC = openapi_spec.generate(APP)
    return _SPEC


def _expected_paths():
    out = set()
    for rule in APP.url_map.iter_rules():
        if rule.endpoint == "static" or rule.rule.startswith("/static"):
            continue
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        if methods:
            path, _ = openapi_spec._path_to_openapi(rule.rule)
            out.add(path)
    return out


def test_spec_is_openapi_31_and_uses_package_version():
    spec = _spec()
    assert spec["openapi"] == "3.1.0"
    assert spec["info"]["version"] == __version__


def test_spec_has_many_paths_and_is_json_serializable():
    spec = _spec()
    assert len(spec["paths"]) > 900
    json.dumps(spec)


def test_every_non_static_url_map_route_is_documented():
    documented = set(_spec()["paths"])
    expected = _expected_paths()
    assert documented == expected, {
        "missing": sorted(expected - documented)[:5],
        "unexpected": sorted(documented - expected)[:5],
    }


def test_csrf_post_route_is_marked_from_the_production_hook():
    promote = _spec()["paths"]["/api/template_manager/promote"]["post"]
    assert promote["x-csrf-required"] is True
    assert "X-CSRF-Token" in {
        parameter["name"] for parameter in promote.get("parameters", [])}
    assert "403" in promote["responses"]


def test_plain_get_is_not_csrf_marked():
    health = _spec()["paths"]["/api/health"]["get"]
    assert health["x-csrf-required"] is False
    assert "security" not in health


def test_path_params_use_openapi_braces():
    assert not any("<" in path or ">" in path for path in _spec()["paths"])


def test_live_route_returns_the_canonical_document():
    response = APP.test_client().get("/api/openapi.json")
    assert response.status_code == 200
    assert response.get_json() == _spec()
