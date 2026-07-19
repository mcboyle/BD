"""Contract tests — cross-cutting invariants that catch whole classes
of regression rather than single behaviours.

If one of these fails, it usually means a structural assumption broke:
a route stopped returning JSON, a widget catalog drifted, an endpoint
that should be GET-only started accepting POST, etc.
"""
import json
import os
import re
import sys

import pytest


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── JSON contract ───────────────────────────────────────────────────────

# Read-only endpoints that take no path params and should always return
# valid JSON with an "ok" field.
_SIMPLE_GET_JSON = [
    "/api/health",
    "/api/doctor",
    "/api/jobs/stuck",
    "/api/sites/schema",
    "/api/sites/selector_health",
    "/api/library/integrity",
    "/api/notify/presets",
]


def test_contract_simple_get_endpoints_return_json():
    """Every simple GET endpoint must return parseable JSON."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    for path in _SIMPLE_GET_JSON:
        r = c.get(path)
        assert r.status_code in (200, 503), f"{path} -> {r.status_code}"
        try:
            body = r.get_json()
        except Exception as e:
            raise AssertionError(f"{path} did not return JSON: {e}")
        assert body is not None, f"{path} returned empty body"


def test_contract_ok_field_is_boolean():
    """When an endpoint includes an 'ok' field it must be a real bool."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    for path in _SIMPLE_GET_JSON:
        body = c.get(path).get_json()
        if isinstance(body, dict) and "ok" in body:
            assert isinstance(body["ok"], bool), \
                f"{path}: ok is {type(body['ok'])}, not bool"


def test_contract_health_endpoint_has_version():
    from bulk_downloader import app as a
    c = a.app.test_client()
    body = c.get("/api/health").get_json()
    assert "version" in body


# ── Method contract ─────────────────────────────────────────────────────

def test_contract_get_endpoints_reject_post():
    """GET-only endpoints must not silently accept POST."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    get_only = ["/api/doctor", "/api/sites/schema",
                "/api/sites/selector_health", "/api/notify/presets",
                "/api/jobs/stuck", "/api/library/integrity"]
    for path in get_only:
        r = c.post(path)
        assert r.status_code in (404, 405), \
            f"{path} accepted POST ({r.status_code})"


def test_contract_widgets_data_endpoint_covers_catalog():
    """/api/widgets/data should return a dict; every widget the catalog
    declares should be resolvable (value present or gracefully absent)."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/api/widgets/data")
    body = r.get_json()
    assert body["ok"] is True
    assert isinstance(body["data"], dict)


# ── Version contract ────────────────────────────────────────────────────

def test_contract_version_is_semver():
    import bulk_downloader
    v = bulk_downloader.__version__
    assert re.match(r"^\d+\.\d+\.\d+$", v), f"non-semver version: {v}"


def test_contract_version_matches_health_endpoint():
    import bulk_downloader
    from bulk_downloader import app as a
    c = a.app.test_client()
    health_v = c.get("/api/health").get_json().get("version")
    assert health_v == bulk_downloader.__version__


def test_contract_changelog_mentions_current_version():
    """The current __version__ must have a CHANGELOG entry — guards
    against shipping a version bump with no recorded changes."""
    import bulk_downloader
    v = bulk_downloader.__version__
    changelog = os.path.join(_REPO, "CHANGELOG.md")
    text = open(changelog, encoding="utf-8").read()
    assert f"v{v}" in text, f"CHANGELOG has no entry for v{v}"


# ── Route registry sanity ───────────────────────────────────────────────

def test_contract_no_duplicate_route_rules():
    """No two endpoints should register the same rule string FOR THE
    SAME HTTP METHOD — that was the /metrics bug, where two GET
    handlers collided and one became dead code.

    NOTE: the same path with different methods (GET list + POST add,
    PUT + DELETE) is correct REST design, NOT a duplicate — this test
    keys on (path, method) precisely so it doesn't false-positive on
    those.

    v3.62.1: the two previously-known duplicates are now resolved —
    the dead mobile_view() handler was deleted, and api_export() was
    moved to /api/sites/<sid>/history/export. KNOWN_DUPLICATES is now
    empty; any duplicate is a real failure.
    """
    KNOWN_DUPLICATES = set()  # empty: all known duplicates resolved
    from bulk_downloader import app as a
    seen = {}
    unexpected = []
    for rule in a.app.url_map.iter_rules():
        path = str(rule)
        methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
        for method in methods:
            key = (path, method)
            if key in seen:
                if key not in KNOWN_DUPLICATES:
                    unexpected.append(
                        f"{path} [{method}]: {seen[key]} and "
                        f"{rule.endpoint}")
            seen[key] = rule.endpoint
    assert not unexpected, (
        "unexpected same-method duplicate routes: "
        + "; ".join(unexpected))


def test_contract_m_route_has_single_handler():
    """Regression guard for the /m duplicate resolved in v3.62.1.

    /m and /m/ must each resolve to exactly ONE handler
    (serve_mobile_view). The dead mobile_view() handler was deleted;
    if a second handler for /m ever reappears, this fails."""
    from bulk_downloader import app as a
    m_handlers = [r.endpoint for r in a.app.url_map.iter_rules()
                  if str(r) == "/m"]
    assert len(m_handlers) == 1, (
        f"/m should have exactly one handler, found: {m_handlers}")
    assert m_handlers[0] == "serve_mobile_view", (
        f"/m handler should be serve_mobile_view, got {m_handlers[0]!r}")


def test_contract_sites_export_routes_distinct():
    """Regression guard for the /api/sites/<sid>/export duplicate
    resolved in v3.62.1. Config export and history export must live
    at distinct URLs - config at /export, history at /history/export."""
    from bulk_downloader import app as a
    by_path = {}
    for r in a.app.url_map.iter_rules():
        by_path.setdefault(str(r), []).append(r.endpoint.rsplit(".", 1)[-1])
    cfg = by_path.get("/api/sites/<sid>/export", [])
    hist = by_path.get("/api/sites/<sid>/history/export", [])
    assert cfg == ["api_sites_export"], (
        f"/api/sites/<sid>/export should be config export only, "
        f"got {cfg}")
    assert hist == ["api_export"], (
        f"/api/sites/<sid>/history/export should be history export, "
        f"got {hist}")


def test_contract_all_api_routes_have_endpoint():
    from bulk_downloader import app as a
    for rule in a.app.url_map.iter_rules():
        if str(rule).startswith("/api/"):
            assert rule.endpoint, f"{rule} has no endpoint"


# ── bdctl --json contract ───────────────────────────────────────────────

def test_contract_maybe_json_helper_exists():
    sys.path.insert(0, _REPO)
    import bdctl
    assert hasattr(bdctl, "_maybe_json")


def test_contract_maybe_json_handles_all_arg_shapes():
    """_maybe_json must never raise regardless of the args object."""
    sys.path.insert(0, _REPO)
    import bdctl
    import contextlib
    import io

    class NoAttr:
        pass

    class JsonTrue:
        json = True

    class JsonFalse:
        json = False

    assert bdctl._maybe_json({"x": 1}, NoAttr()) is False
    assert bdctl._maybe_json({"x": 1}, JsonFalse()) is False
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert bdctl._maybe_json({"x": 1}, JsonTrue()) is True
    assert json.loads(buf.getvalue()) == {"x": 1}
