"""Cut 4 — GET /api/queue/preflight and GET /api/sites/<id>/readiness.

Both read-only operator-intelligence composites.

queue/preflight aggregates existing signals (auth_health, daily_budget,
selector_drift, runner status, review backlog) plus two new checks (download-dir
writable, dupe estimate) into a go/no-go strip. On the empty sandbox instance
every check resolves clean.

sites/<id>/readiness rolls the per-site signals into a green/amber/red level
with "fix this" hints; unknown site -> 404.

RED on pristine 373: both routes 404.
"""

_PREFLIGHT_KEYS = (
    "auth_health", "daily_budget", "selector_drift",
    "runners", "review_backlog", "download_dir", "dupe_estimate",
)


def test_queue_preflight_contract():
    from bulk_downloader import app as A
    c = A.app.test_client()
    r = c.get("/api/queue/preflight")
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert d["ok"] is True
    assert isinstance(d["ready"], bool)
    keys = {ch["key"] for ch in d["checks"]}
    for k in _PREFLIGHT_KEYS:
        assert k in keys, f"preflight missing check {k!r}"
    for ch in d["checks"]:
        assert ch["status"] in ("ok", "warn", "fail"), ch
        assert ch.get("label")


def test_readiness_unknown_site_404():
    from bulk_downloader import app as A
    c = A.app.test_client()
    r = c.get("/api/sites/__nope__/readiness")
    assert r.status_code == 404, r.get_json()


def test_readiness_known_site_shape():
    from bulk_downloader import app as A
    orig = dict(A.s_cfg)
    try:
        A.s_cfg["t_ready"] = {
            "name": "T", "login_url": "https://t.test/login",
            "download_dir": "/tmp",
        }
        c = A.app.test_client()
        r = c.get("/api/sites/t_ready/readiness")
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["ok"] is True
        assert d["site_id"] == "t_ready"
        assert d["level"] in ("green", "amber", "red")
        assert isinstance(d["checks"], list) and d["checks"]
        assert isinstance(d["fixes"], list)
        for ch in d["checks"]:
            assert ch["status"] in ("ok", "warn", "fail")
    finally:
        A.s_cfg.clear()
        A.s_cfg.update(orig)
