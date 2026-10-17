"""FastAPI/pydantic contract stubs for the BulkDownloader /api/* surface.

Status: not wired; mirrors Flask /api/* contracts. Nothing serves these routes:
bulk_downloader/asgi_gateway.py mounts only the Flask app (bulk_downloader.app), and these
paths shadow the live Flask /api/* routes of the same name. The response models are minimal
subsets of the live Flask bodies, checked against the Flask test client by
tests/test_api_contracts.py (GET health, health/v2, history?paginate=1, library browse/item/
stats/tags). POST/DELETE bodies and the DiskUsage item are not checked against Flask.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator


class ProblemDetails(BaseModel):
    ok: bool = False
    error: str = Field(description="Human-readable error description")
    detail: str | list[dict[str, Any]] | None = Field(default=None, description="Detailed diagnostic or validation error list")
    code: str | None = Field(default=None, description="Machine-readable error identifier token")
    status: int = Field(description="HTTP status code")
    title: str | None = Field(default=None, description="RFC 7807 error title")
    instance: str | None = Field(default=None, description="Request URI path")

def safe_int(val: Any, default: int, min_val: int | None=None, max_val: int | None=None, param_name: str="parameter") -> int:
    if val is None or val == "":
        return default
    try:
        res = int(val)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid integer value for {param_name}: {val!r}")
    if min_val is not None and res < min_val:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{param_name} must be >= {min_val}, got {res}")
    if max_val is not None and res > max_val:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{param_name} must be <= {max_val}, got {res}")
    return res

def safe_float(val: Any, default: float, min_val: float | None=None, max_val: float | None=None, param_name: str="parameter") -> float:
    if val is None or val == "":
        return default
    try:
        res = float(val)
    except (ValueError, TypeError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid float value for {param_name}: {val!r}")
    if min_val is not None and res < min_val:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{param_name} must be >= {min_val}, got {res}")
    if max_val is not None and res > max_val:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{param_name} must be <= {max_val}, got {res}")
    return res

class DiskUsage(BaseModel):
    path: str
    free_gb: float
    total_gb: float
    free_pct: float

class OllamaStatus(BaseModel):
    reachable: bool | None = None
    model: str | None = None
    error: str | None = None

class HealthStatusResponse(BaseModel):
    ok: bool
    version: str | None = None
    degraded: str | None = None  # Flask sets it only when a check fails
    db_ok: bool | None = None
    uptime_s: float | None = None

class HealthV2Response(BaseModel):
    ok: bool
    version: str | None = None
    degraded: str | None = None  # Flask sets it only when a check fails
    db_ok: bool | None = None
    ollama: OllamaStatus | None = None
    disks: list[DiskUsage] | None = None
    uptime_s: float | None = None

class HistoryRow(BaseModel):
    id: int | str
    site_id: str
    url: str
    status: str
    title: str | None = None
    site_name: str | None = None
    filename: str | None = None
    file_size: int | None = None
    message: str | None = None
    ts: str | None = None

class HistoryEnvelopeResponse(BaseModel):
    rows: list[HistoryRow] = Field(default_factory=list)
    next_cursor: int | str | None = None

class HistoryQueryParams(BaseModel):
    site_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    status: str | None = None
    limit: int = Field(default=200, ge=1, le=10000)
    offset: int = Field(default=0, ge=0)
    cursor: str | None = None
    paginate: int | None = None

class LibraryTagRef(BaseModel):
    id: int
    name: str
    color: str | None = None

class LibraryItem(BaseModel):
    id: int
    title: str
    file_path: str | None = None
    site_id: str | None = None
    file_size: int | None = None
    duration_s: float | None = None
    watched: bool | None = False
    rating: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = None
    tags: list[LibraryTagRef] = Field(default_factory=list)
    added_at: float | None = None

class LibraryBrowseResponse(BaseModel):
    ok: bool = True
    rows: list[LibraryItem] = Field(default_factory=list)
    count: int = 0
    next_cursor: int | str | None = None

class LibraryItemResponse(BaseModel):
    ok: bool = True
    row: LibraryItem | None = None

class LibraryDeleteResponse(BaseModel):
    ok: bool = True
    deleted: int = 1

class LibraryWatchedRequest(BaseModel):
    watched: bool

class LibraryWatchedResponse(BaseModel):
    ok: bool = True
    watched: bool

class LibraryRatingRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)  # null clears, as in live Flask

class LibraryRatingResponse(BaseModel):
    ok: bool = True
    rating: int | None = None

class LibraryNotesRequest(BaseModel):
    notes: str

class LibraryNotesResponse(BaseModel):
    ok: bool = True  # live Flask answers {"ok": true} only

class LibraryTagRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=128)

class LibraryTagResponse(BaseModel):
    ok: bool = True
    tags: list[str] = Field(default_factory=list)

class LibraryStats(BaseModel):
    total_files: int = 0
    total_size: int = 0
    watched_files: int = 0
    missing_files: int = 0
    unrated_files: int = 0

class LibraryStatsResponse(BaseModel):
    ok: bool = True
    stats: LibraryStats = Field(default_factory=LibraryStats)

class LibraryTagEntry(BaseModel):
    id: int
    name: str
    color: str | None = None
    usage_count: int = 0

class LibraryTagsResponse(BaseModel):
    ok: bool = True
    tags: list[LibraryTagEntry] = Field(default_factory=list)

class BulkEnqueueRequest(BaseModel):
    site_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    urls: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, v: list[str]) -> list[str]:
        valid_urls: list[str] = []
        for raw in v:
            s = str(raw).strip()
            if not s:
                continue
            if not s.startswith(("http://", "https://")):
                raise ValueError(f"Invalid URL scheme in '{s}', must be http:// or https://")
            valid_urls.append(s)
        if not valid_urls:
            raise ValueError("urls list must contain at least one valid URL")
        return valid_urls

class BulkEnqueueResponse(BaseModel):
    ok: bool = True
    enqueued: int
    skipped: int | None = 0
    job_ids: list[str] = Field(default_factory=list)

class ConcurrentUpdateRequest(BaseModel):
    n: int = Field(default=2, ge=1, le=64)

class BandwidthQueryParams(BaseModel):
    seconds: int = Field(default=3600, ge=1, le=31536000)

class TimelineQueryParams(BaseModel):
    days: int = Field(default=30, ge=1, le=365)

class HourlyStatsQueryParams(BaseModel):
    since_days: int = Field(default=30, ge=1, le=365)

class PruneSelectorsRequest(BaseModel):
    min_attempts: int = Field(default=5, ge=1, le=1000)
    max_miss_ratio: float = Field(default=0.8, ge=0.0, le=1.0)

class SiteEventsQueryParams(BaseModel):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1, le=1000)

class SiteQueueQueryParams(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=1000)

class DedupFindRequest(BaseModel):
    distance: int = Field(default=4, ge=0, le=64)
    path: str | None = None

class DedupGroupsQueryParams(BaseModel):
    distance: int = Field(default=4, ge=0, le=64)

class ImportRecentQueryParams(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)

class SharesAccessLogQueryParams(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)

class EventsAllQueryParams(BaseModel):
    limit: int = Field(default=200, ge=1, le=1000)
    after: str | None = None
router = APIRouter()  # full /api/... literals: the served URL is what endpoint_reachability keys on

@router.get("/api/health", response_model=HealthStatusResponse)
async def get_health() -> HealthStatusResponse:
    return HealthStatusResponse(ok=True, version="3.66.1901", degraded=None, db_ok=True, uptime_s=3600.0)

@router.get("/api/health/v2", response_model=HealthV2Response, responses={200: {"model": HealthV2Response}, 503: {"model": HealthV2Response}})
async def get_health_v2(simulate_degraded: int | None=Query(default=0)) -> JSONResponse:
    disks = [DiskUsage(path="/data", free_gb=45.2, total_gb=500.0, free_pct=9.04)]
    ollama = OllamaStatus(reachable=True, model="qwen2.5-coder:7b", error=None)
    if simulate_degraded:
        payload = HealthV2Response(ok=False, version="3.66.1901", degraded="Disk space low on /data", db_ok=True, ollama=ollama, disks=disks, uptime_s=1200.0)
        return JSONResponse(status_code=503, content=payload.model_dump())
    payload = HealthV2Response(ok=True, version="3.66.1901", degraded=None, db_ok=True, ollama=ollama, disks=disks, uptime_s=3600.0)
    return JSONResponse(status_code=200, content=payload.model_dump())

@router.get("/api/history", response_model=HistoryEnvelopeResponse)
async def get_history(site_id: str | None=Query(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]{1,64}$"), status: str | None=Query(default=None), limit: int=Query(default=200, ge=1, le=10000), offset: int=Query(default=0, ge=0), cursor: str | None=Query(default=None), paginate: int | None=Query(default=None)) -> HistoryEnvelopeResponse:
    return HistoryEnvelopeResponse(rows=[], next_cursor=None)

@router.get("/api/library/browse", response_model=LibraryBrowseResponse)
async def browse_library(limit: int=Query(default=100, ge=1, le=500), cursor: str | None=Query(default=None)) -> LibraryBrowseResponse:
    return LibraryBrowseResponse(ok=True, rows=[], count=0, next_cursor=None)

# Static /library/* paths are declared before /library/{lid}: Starlette matches in order.
@router.get("/api/library/stats", response_model=LibraryStatsResponse)
async def get_library_stats() -> LibraryStatsResponse:
    return LibraryStatsResponse(ok=True, stats=LibraryStats())

@router.get("/api/library/tags", response_model=LibraryTagsResponse)
async def get_library_tags() -> LibraryTagsResponse:
    return LibraryTagsResponse(ok=True, tags=[])

@router.get("/api/library/{lid}", response_model=LibraryItemResponse)
async def get_library_item(lid: int) -> LibraryItemResponse:
    if lid == 999999:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Library item {lid} not found")
    return LibraryItemResponse(ok=True, row=LibraryItem(id=lid, title=f"Media Item {lid}"))

@router.delete("/api/library/{lid}", response_model=LibraryDeleteResponse)
async def delete_library_item(lid: int) -> LibraryDeleteResponse:
    if lid == 999999:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Library item {lid} not found")
    return LibraryDeleteResponse(ok=True, deleted=1)

@router.post("/api/library/{lid}/watched", response_model=LibraryWatchedResponse)
async def update_library_watched(lid: int, req: LibraryWatchedRequest) -> LibraryWatchedResponse:
    return LibraryWatchedResponse(ok=True, watched=req.watched)

@router.post("/api/library/{lid}/rating", response_model=LibraryRatingResponse)
async def update_library_rating(lid: int, req: LibraryRatingRequest) -> LibraryRatingResponse:
    return LibraryRatingResponse(ok=True, rating=req.rating)

@router.post("/api/library/{lid}/notes", response_model=LibraryNotesResponse)
async def update_library_notes(lid: int, req: LibraryNotesRequest) -> LibraryNotesResponse:
    return LibraryNotesResponse(ok=True)

@router.post("/api/library/{lid}/tags", response_model=LibraryTagResponse)
async def add_library_tag(lid: int, req: LibraryTagRequest) -> LibraryTagResponse:
    return LibraryTagResponse(ok=True, tags=[req.tag])

@router.delete("/api/library/{lid}/tags/{tag_name:path}", response_model=LibraryTagResponse)
async def remove_library_tag(lid: int, tag_name: str) -> LibraryTagResponse:
    return LibraryTagResponse(ok=True, tags=[])

@router.post("/api/bulk/enqueue", response_model=BulkEnqueueResponse)
async def bulk_enqueue(payload: BulkEnqueueRequest) -> BulkEnqueueResponse:
    return BulkEnqueueResponse(ok=True, enqueued=len(payload.urls), skipped=0, job_ids=[f"job-{i}" for i in range(len(payload.urls))])

@router.post("/api/concurrent/{sid}")
async def update_concurrent(sid: str, req: ConcurrentUpdateRequest) -> dict[str, Any]:
    return {"ok": True, "sid": sid, "n": req.n}

@router.get("/api/stats/bandwidth")
async def get_bandwidth(seconds: int=Query(default=3600, ge=1, le=31536000)) -> dict[str, Any]:
    return {"ok": True, "seconds": seconds}

@router.get("/api/stats/timeline")
async def get_timeline(days: int=Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    return {"ok": True, "days": days}

@router.get("/api/hourly_stats")
async def get_hourly_stats(since_days: int=Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    return {"ok": True, "since_days": since_days}

@router.post("/api/sites/{sid}/prune_selectors")
async def prune_selectors(sid: str, req: PruneSelectorsRequest) -> dict[str, Any]:
    return {"ok": True, "sid": sid, "min_attempts": req.min_attempts}

@router.get("/api/sites/{sid}/events")
async def get_site_events(sid: str, after: int=Query(default=0, ge=0), limit: int=Query(default=200, ge=1, le=1000)) -> dict[str, Any]:
    return {"ok": True, "sid": sid, "after": after, "limit": limit}

@router.get("/api/sites/{sid}/queue")
async def get_site_queue(sid: str, offset: int=Query(default=0, ge=0), limit: int=Query(default=500, ge=1, le=1000)) -> dict[str, Any]:
    return {"ok": True, "sid": sid, "offset": offset, "limit": limit}

@router.post("/api/dedup/find")
async def find_dedup(req: DedupFindRequest) -> dict[str, Any]:
    return {"ok": True, "distance": req.distance, "matches": []}

@router.get("/api/dedup/groups")
async def get_dedup_groups(distance: int=Query(default=4, ge=0, le=64)) -> dict[str, Any]:
    return {"ok": True, "distance": distance, "groups": []}

@router.get("/api/import/recent")
async def get_import_recent(limit: int=Query(default=20, ge=1, le=500)) -> dict[str, Any]:
    return {"ok": True, "limit": limit, "items": []}

@router.get("/api/shares/access_log")
async def get_shares_access_log(limit: int=Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
    return {"ok": True, "limit": limit, "log": []}

@router.get("/api/events_all")
async def get_events_all(limit: int=Query(default=200, ge=1, le=1000), after: str | None=Query(default=None)) -> dict[str, Any]:
    return {"ok": True, "limit": limit, "events": []}

def create_api_app() -> FastAPI:
    fastapi_app = FastAPI(title="BulkDownloader API Contract", version="3.66.1901", description="FastAPI endpoints and synchronized Pydantic schema contracts for JarvisOS Cockpit")

    @fastapi_app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        error_msg = str(exc.detail) if isinstance(exc.detail, str) else "HTTP error"
        code_map = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 422: "VALIDATION_ERROR", 500: "INTERNAL_ERROR", 503: "SERVICE_UNAVAILABLE"}
        code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")
        payload = ProblemDetails(ok=False, error=error_msg, detail=exc.detail if not isinstance(exc.detail, str) else None, code=code, status=exc.status_code, title=error_msg, instance=str(request.url.path))
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(exclude_none=True))

    @fastapi_app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        first_error = exc.errors()[0] if exc.errors() else {}
        loc_parts = first_error.get("loc", [])
        loc = " -> ".join(str(p) for p in loc_parts)
        msg = first_error.get("msg", "Invalid request payload")
        error_msg = f"Validation failed at '{loc}': {msg}" if loc else msg
        safe_errors: list[dict[str, Any]] = []
        for err in exc.errors():
            safe_err = {"loc": list(err.get("loc", [])), "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))}
            safe_errors.append(safe_err)
        payload = ProblemDetails(ok=False, error=error_msg, detail=safe_errors, code="VALIDATION_ERROR", status=status.HTTP_422_UNPROCESSABLE_ENTITY, title="Request validation failed", instance=str(request.url.path))
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload.model_dump(exclude_none=True))

    @fastapi_app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        payload = ProblemDetails(ok=False, error="Internal server error", detail=None, code="INTERNAL_ERROR", status=status.HTTP_500_INTERNAL_SERVER_ERROR, title="Internal server error", instance=str(request.url.path))
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump(exclude_none=True))
    fastapi_app.include_router(router)
    return fastapi_app
app = create_api_app()
