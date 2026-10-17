from __future__ import annotations

import pytest
from bulk_downloader.routes.api import (
    BulkEnqueueRequest,
    BulkEnqueueResponse,
    DiskUsage,
    HealthStatusResponse,
    HealthV2Response,
    HistoryEnvelopeResponse,
    HistoryRow,
    LibraryBrowseResponse,
    LibraryItem,
    LibraryStatsResponse,
    LibraryTagsResponse,
    OllamaStatus,
    app,
    safe_float,
    safe_int,
)
from fastapi.testclient import TestClient

BD_GATE_SCOPE = "module"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)

def test_schema_harmonization_health_status() -> None:
    model = HealthStatusResponse(ok=True, version="3.66.1901", degraded=None, db_ok=True, uptime_s=12.5)
    dumped = model.model_dump()
    assert dumped["ok"] is True
    assert dumped["version"] == "3.66.1901"
    assert dumped["degraded"] is None
    assert dumped["db_ok"] is True
    assert dumped["uptime_s"] == 12.5

def test_schema_harmonization_health_v2() -> None:
    disk = DiskUsage(path="/data", free_gb=128.5, total_gb=500.0, free_pct=25.7)
    ollama = OllamaStatus(reachable=True, model="qwen2.5-coder:7b", error=None)
    model = HealthV2Response(ok=True, version="3.66.1901", degraded=None, db_ok=True, ollama=ollama, disks=[disk], uptime_s=3600.0)
    dumped = model.model_dump()
    assert dumped["ok"] is True
    assert dumped["db_ok"] is True
    assert dumped["disks"][0]["path"] == "/data"
    assert dumped["disks"][0]["free_gb"] == 128.5
    assert dumped["ollama"]["reachable"] is True

def test_schema_harmonization_history() -> None:
    row = HistoryRow(id=101, site_id="site_alpha", url="https://example.com/item/101", status="failed", title="Example Title", site_name="Site Alpha", filename="v101.mp4", file_size=1048576, message="boom", ts="2026-10-15T12:00:00")
    envelope = HistoryEnvelopeResponse(rows=[row], next_cursor=101)
    dumped = envelope.model_dump()
    assert len(dumped["rows"]) == 1
    assert dumped["rows"][0]["site_id"] == "site_alpha"
    assert dumped["rows"][0]["file_size"] == 1048576
    assert dumped["next_cursor"] == 101

def test_schema_harmonization_library() -> None:
    item = LibraryItem(id=42, title="Video 42", file_path="/media/video42.mp4", site_id="site_alpha", file_size=2048000, duration_s=120.5, watched=0, rating=4, notes="Important reference", tags=[{"id": 1, "name": "hd", "color": ""}], added_at=1792215679.2)
    browse = LibraryBrowseResponse(ok=True, rows=[item], count=1, next_cursor=None)
    dumped = browse.model_dump()
    assert dumped["ok"] is True
    assert dumped["count"] == 1
    assert dumped["rows"][0]["id"] == 42
    assert dumped["rows"][0]["watched"] is False
    assert dumped["rows"][0]["tags"] == [{"id": 1, "name": "hd", "color": ""}]
    stats = LibraryStatsResponse(ok=True, stats={"total_files": 100, "total_size": 10737418240, "watched_files": 35, "missing_files": 0, "unrated_files": 7})
    assert stats.stats.total_files == 100
    assert stats.stats.watched_files == 35
    tags = LibraryTagsResponse(ok=True, tags=[{"id": 1, "name": "hd", "color": "", "usage_count": 3}])
    assert [t.name for t in tags.tags] == ["hd"]

def test_schema_harmonization_bulk_enqueue() -> None:
    req = BulkEnqueueRequest(site_id="site_test", urls=["https://example.com/1", "https://example.com/2"])
    assert req.site_id == "site_test"
    assert len(req.urls) == 2
    res = BulkEnqueueResponse(ok=True, enqueued=2, skipped=0, job_ids=["job-1", "job-2"])
    assert res.ok is True
    assert res.enqueued == 2

def test_null_safety_guards() -> None:
    row = HistoryRow(id="guid-abc-123", site_id="site_pending", url="https://example.com/pending", status="queued")
    dumped = row.model_dump()
    assert dumped["title"] is None
    assert dumped["message"] is None
    assert dumped["file_size"] is None
    item = LibraryItem(id=99, title="Bare Item")
    dumped_item = item.model_dump()
    assert dumped_item["file_path"] is None
    assert dumped_item["rating"] is None
    assert dumped_item["notes"] is None
    assert dumped_item["tags"] == []
    assert dumped_item["watched"] is False

def test_bulk_enqueue_url_validation() -> None:
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        BulkEnqueueRequest(site_id="site_test", urls=["ftp://invalid-scheme.org/file"])
    with pytest.raises(ValueError, match="urls list must contain at least one valid URL"):
        BulkEnqueueRequest(site_id="site_test", urls=["   ", ""])

def test_error_uniformity_404(client: TestClient) -> None:
    res = client.get("/api/library/999999")
    assert res.status_code == 404
    body = res.json()
    assert body["ok"] is False
    assert "error" in body and isinstance(body["error"], str)
    assert body["status"] == 404
    assert body["code"] == "NOT_FOUND"

def test_error_uniformity_422(client: TestClient) -> None:
    res = client.get("/api/history?limit=not_an_integer")
    assert res.status_code == 422
    body = res.json()
    assert body["ok"] is False
    assert "error" in body and isinstance(body["error"], str)
    assert body["status"] == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert "detail" in body

def test_error_uniformity_500() -> None:
    import bulk_downloader.routes.api as api

    probe = api.create_api_app()

    @probe.get("/__probe_500")
    async def _boom() -> dict:
        raise RuntimeError("probe")

    res = TestClient(probe, raise_server_exceptions=False).get("/__probe_500")
    assert res.status_code == 500
    body = res.json()
    assert body["ok"] is False
    assert body["error"] == "Internal server error"
    assert body["status"] == 500
    assert body["code"] == "INTERNAL_ERROR"

def test_safe_int_helper() -> None:
    from fastapi import HTTPException
    assert safe_int("42", 10) == 42
    assert safe_int(None, 10) == 10
    assert safe_int("", 10) == 10
    with pytest.raises(HTTPException) as exc_info:
        safe_int("invalid_number", 10, param_name="limit")
    assert exc_info.value.status_code == 422
    with pytest.raises(HTTPException) as exc_info:
        safe_int("0", 10, min_val=1, param_name="limit")
    assert exc_info.value.status_code == 422
    with pytest.raises(HTTPException) as exc_info:
        safe_int("1001", 10, max_val=1000, param_name="limit")
    assert exc_info.value.status_code == 422

def test_safe_float_helper() -> None:
    from fastapi import HTTPException
    assert safe_float("0.75", 0.5) == 0.75
    assert safe_float(None, 0.5) == 0.5
    with pytest.raises(HTTPException) as exc_info:
        safe_float("not_a_float", 0.5, param_name="ratio")
    assert exc_info.value.status_code == 422
    with pytest.raises(HTTPException) as exc_info:
        safe_float("-0.1", 0.5, min_val=0.0, param_name="ratio")
    assert exc_info.value.status_code == 422

def test_boundary_validation_endpoints(client: TestClient) -> None:
    vectors = [("POST", "/api/concurrent/site1", {"n": "two"}), ("GET", "/api/stats/bandwidth?seconds=invalid", None), ("GET", "/api/stats/timeline?days=30days", None), ("GET", "/api/hourly_stats?since_days=week", None), ("POST", "/api/sites/site1/prune_selectors", {"min_attempts": "none"}), ("POST", "/api/sites/site1/prune_selectors", {"max_miss_ratio": "high"}), ("GET", "/api/sites/site1/events?after=cursor_123", None), ("GET", "/api/sites/site1/events?limit=unlimited", None), ("GET", "/api/sites/site1/queue?offset=ten", None), ("GET", "/api/sites/site1/queue?limit=all", None), ("POST", "/api/dedup/find", {"distance": "close"}), ("GET", "/api/dedup/groups?distance=far", None), ("GET", "/api/import/recent?limit=max", None), ("GET", "/api/shares/access_log?limit=hundred", None), ("GET", "/api/events_all?limit=auto", None)]
    for method, path, payload in vectors:
        if method == "GET":
            res = client.get(path)
        else:
            res = client.post(path, json=payload)
        assert res.status_code == 422, f"Expected 422 for {method} {path}, got {res.status_code}"
        data = res.json()
        assert data["ok"] is False
        assert data["code"] == "VALIDATION_ERROR"

def test_health_v2_degraded_contract(client: TestClient) -> None:
    res = client.get("/api/health/v2?simulate_degraded=1")
    assert res.status_code == 503
    data = res.json()
    assert data["ok"] is False
    assert data["degraded"] == "Disk space low on /data"
    assert isinstance(data["disks"], list)
    assert len(data["disks"]) > 0


def test_routes_package_import_does_not_pull_in_fastapi() -> None:
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import sys, bulk_downloader.routes as r\n"
        "assert 'fastapi' not in sys.modules, 'ROUTES-EAGER-FASTAPI'\n"
        "assert r.export_import_bp is not None\n"
        "from bulk_downloader.routes import app, router\n"
        "from bulk_downloader.routes.api import app as api_app\n"
        "assert app is api_app and router.routes and all(rt.path.startswith('/api/') for rt in router.routes)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


# ── PM 05:07Z: the stubs are NOT wired and must mirror the live Flask /api/* contracts ──
# The comparison is against the LIVE Flask app (bulk_downloader.app test client, hermetic: the
# conftest autouse fixture isolates BD_HOME), never against hand-written expectations. Each
# covered stub route's response_model -- read from the router itself -- must name only fields the
# live Flask body carries (nested models included), and the live body must validate against it.

# GET routes compared. path -> (live Flask request, keys Flask sets only on some paths)
_LIVE_COMPARED = {
    "/api/health": ("/api/health", {"degraded"}),
    "/api/health/v2": ("/api/health/v2", {"degraded"}),
    "/api/history": ("/api/history?paginate=1", set()),  # the {rows, next_cursor} envelope form
    "/api/library/browse": ("/api/library/browse", set()),
    "/api/library/{lid}": ("/api/library/{lid}", set()),
    "/api/library/stats": ("/api/library/stats", set()),
    "/api/library/tags": ("/api/library/tags", set()),
}


def _stub_route(path: str, method: str = "GET"):
    import bulk_downloader.routes.api as api

    for r in api.router.routes:
        if r.path == path and method in r.methods:
            return r
    raise AssertionError(f"STUB-ROUTE-MISSING {method} {path}")


def _models_in(annotation):
    import typing

    from pydantic import BaseModel

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
    for arg in typing.get_args(annotation):
        yield from _models_in(arg)


def _assert_mirrors(model, body, where: str, conditional=frozenset()) -> None:
    assert isinstance(body, dict), f"STUB-SCHEMA-DRIFT {where}: live Flask body is {type(body).__name__}, not an object"
    missing = set(model.model_fields) - set(body) - set(conditional)
    assert not missing, f"STUB-SCHEMA-DRIFT {where}: {model.__name__} fields absent from live Flask: {sorted(missing)} (live keys {sorted(body)})"
    for name, field in model.model_fields.items():
        nested = next(_models_in(field.annotation), None)
        value = body.get(name)
        if nested is None or value is None:
            continue
        for i, item in enumerate(value if isinstance(value, list) else [value]):
            _assert_mirrors(nested, item, f"{where}.{name}[{i}]")


def _seed_live_flask_data(tmp_path) -> int:
    from bulk_downloader import db, library

    library._SCHEMA_READY = False  # BD_HOME is per-test: the cached flag names the previous test's DB
    db.db_init()
    db.db_log("site_a", "Site A", "https://example.com/v/1", "failed", filename="v1.mp4", file_size=123, message="boom")
    media = tmp_path / "v1.mp4"
    media.write_bytes(b"x" * 10)
    library.library_record(str(media), site_id="site_a")
    rows = library.library_browse(limit=500)[0]
    lid = next(r["id"] for r in rows if r["file_path"] == str(media))
    assert library.library_add_tag(lid, "hd")
    return lid


def test_stub_schemas_mirror_live_flask_responses(tmp_path) -> None:
    from bulk_downloader import app as a

    lid = _seed_live_flask_data(tmp_path)
    live = a.app.test_client()
    stub = TestClient(app, raise_server_exceptions=False)
    for path, (live_path, conditional) in _LIVE_COMPARED.items():
        model = _stub_route(path).response_model
        res = live.get(live_path.format(lid=lid))
        assert res.status_code in (200, 503), f"{live_path} -> {res.status_code}"
        body = res.get_json()
        _assert_mirrors(model, body, path, conditional)
        model.model_validate(body)
        for field in ("rows", "tags"):  # seeded: the item schemas were compared, not vacuous
            if field in model.model_fields:
                assert body[field], f"STUB-SCHEMA-VACUOUS {path}: live {field} empty after seeding"
        stub_res = stub.get(path.format(lid=lid))
        assert stub_res.status_code in (200, 503), f"STUB-ROUTE-SHADOWED {path}: stub app answered {stub_res.status_code} {stub_res.text[:200]}"
        model.model_validate(stub_res.json())


def test_stub_error_shape_extends_live_flask_error() -> None:
    from bulk_downloader import app as a

    live = a.app.test_client().get("/api/library/999999")
    stub = TestClient(app, raise_server_exceptions=False).get("/api/library/999999")
    assert live.status_code == stub.status_code == 404
    live_body, stub_body = live.get_json(), stub.json()
    drift = {k: (v, stub_body.get(k)) for k, v in live_body.items() if type(stub_body.get(k)) is not type(v)}
    assert not drift, f"STUB-ERROR-SHAPE-DRIFT live Flask 404 keys/types not carried by the stub: {drift}"


def test_stub_routes_exist_in_live_flask() -> None:
    import re

    import bulk_downloader.routes.api as api
    from bulk_downloader import app as a

    live = set()
    for rule in a.app.url_map.iter_rules():
        path = re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", r"{\1}", rule.rule)
        live |= {(m, path) for m in rule.methods - {"HEAD", "OPTIONS"}}
    stub = {(m, r.path_format) for r in api.router.routes for m in r.methods - {"HEAD", "OPTIONS"}}  # {tag_name:path} -> {tag_name}
    assert stub, "no stub routes read"
    assert ("GET", "/api/health") in live, "url_map probe cannot see a known Flask route"
    assert not stub - live, f"STUB-ROUTE-NOT-IN-FLASK {sorted(stub - live)}"


def test_asgi_gateway_does_not_register_stub_routes() -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    src = (root / "bulk_downloader" / "asgi_gateway.py").read_text()
    assert "from bulk_downloader.app import app as flask_app" in src, "gateway source probe cannot see its Flask reference"
    for needle in ("routes.api", "bulk_downloader.routes", "from .routes", "create_api_app", "include_router", "APIRouter"):
        assert needle not in src, f"GATEWAY-WIRES-STUB-ROUTES asgi_gateway.py references {needle!r}"
    code = (
        "import sys, flask\n"
        "import bulk_downloader.asgi_gateway as g\n"
        "g.get_asgi_app(flask.Flask('probe'))\n"
        "assert 'bulk_downloader.routes.api' not in sys.modules, 'GATEWAY-IMPORTS-STUB-ROUTES'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]


def test_unhandled_exception_payload_does_not_leak_message() -> None:
    import bulk_downloader.routes.api as api

    probe = api.create_api_app()

    @probe.get("/__probe_500")
    async def _boom() -> dict:
        raise RuntimeError("LEAK-PROBE-7f3a /etc/bd/secret")

    res = TestClient(probe, raise_server_exceptions=False).get("/__probe_500")
    assert res.status_code == 500
    assert res.json()["code"] == "INTERNAL_ERROR"
    assert "LEAK-PROBE-7f3a" not in res.text, f"EXC-MESSAGE-LEAK 500 payload carries str(exc): {res.text[:300]}"
    assert not [r.path for r in api.router.routes if "simulated_error" in r.path], "TEST-ROUTE-SHIPPED /api/test/simulated_error"


# ── r3 (cx150 F1-F3): the mutating library stubs must accept/answer what live Flask accepts/answers ──
def _live_and_stub(tmp_path):
    from bulk_downloader import app as a

    lid = _seed_live_flask_data(tmp_path)
    return lid, a.app.test_client(), TestClient(app, raise_server_exceptions=False)


def test_stub_rating_accepts_null_like_live_flask(tmp_path) -> None:
    import bulk_downloader.routes.api as api

    lid, live, stub = _live_and_stub(tmp_path)
    path = f"/api/library/{lid}/rating"
    control_live, control_stub = live.post(path, json={"rating": 4}), stub.post(path, json={"rating": 4})
    assert control_live.status_code == control_stub.status_code == 200, "rating=4 control must pass in both"
    assert control_live.get_json()["rating"] == control_stub.json()["rating"] == 4
    cleared_live, cleared_stub = live.post(path, json={"rating": None}), stub.post(path, json={"rating": None})
    assert cleared_live.status_code == 200 and cleared_live.get_json()["rating"] is None, "live probe cannot see a cleared rating"
    assert cleared_stub.status_code == 200, f"MIRROR-RATING-NULL stub refused live-valid rating=null: {cleared_stub.status_code} {cleared_stub.text[:200]}"
    api.LibraryRatingResponse.model_validate(cleared_live.get_json())
    assert cleared_stub.json()["rating"] is None, f"MIRROR-RATING-NULL stub did not answer rating=null: {cleared_stub.json()}"


def test_stub_notes_response_matches_live_flask_body(tmp_path) -> None:
    import bulk_downloader.routes.api as api

    lid, live, stub = _live_and_stub(tmp_path)
    path = f"/api/library/{lid}/notes"
    live_res, stub_res = live.post(path, json={"notes": "synthetic note"}), stub.post(path, json={"notes": "synthetic note"})
    assert live_res.status_code == stub_res.status_code == 200
    live_body = live_res.get_json()
    _assert_mirrors(api.LibraryNotesResponse, live_body, "POST /api/library/{lid}/notes")
    api.LibraryNotesResponse.model_validate(live_body)
    assert set(stub_res.json()) <= set(live_body), f"MIRROR-NOTES-BODY stub answers keys live Flask does not: {stub_res.json()} vs {live_body}"


def test_stub_tag_remove_keeps_flask_path_semantics(tmp_path) -> None:
    lid, live, stub = _live_and_stub(tmp_path)
    control = f"/api/library/{lid}/tags/hd"
    assert live.delete(control).status_code == stub.delete(control).status_code == 200, "single-segment control must pass in both"
    slashed = f"/api/library/{lid}/tags/genre/drama"
    live_res, stub_res = live.delete(slashed), stub.delete(slashed)
    assert live_res.status_code == 200, f"live probe cannot see the <path:tag_name> route: {live_res.status_code}"
    assert stub_res.status_code == live_res.status_code, f"MIRROR-TAG-PATH stub lost Flask <path:tag_name>: live {live_res.status_code} stub {stub_res.status_code}"
