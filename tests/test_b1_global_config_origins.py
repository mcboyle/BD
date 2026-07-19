"""B1 — settings source-of-truth resolver.

GET /api/global_config/origins returns, for each global-config field, its true
origin + static apply-timing, extending the existing live-vs-defaults diff
(/api/global_config/defaults). Read-only. Makes the Settings source/apply chips
(Cut 5) honest.

Contract per field:
  origin        : one of "default" | "global" | "env" (static classification:
                  differs-from-default => "global", else "default"; env-locked
                  overrides to "env")
  apply_timing  : "immediate" | "restart" (static map; unknown => "immediate")
  env_locked    : bool  (field is pinned by an environment variable)
  is_secret     : bool  (secret-bearing field)

SECRET DISCIPLINE: secret fields are reported as refs only — the payload must
never carry a secret value (covered explicitly below).

RED-first: the route does not exist yet, so it 404s on pristine source.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_origins_route_exists_and_shape():
    import bulk_downloader.app as a
    c = a.app.test_client()
    r = c.get("/api/global_config/origins")
    assert r.status_code == 200, f"/api/global_config/origins -> {r.status_code}"
    body = r.get_json()
    assert isinstance(body, dict)
    fields = body.get("fields")
    assert isinstance(fields, dict) and fields, "expected a non-empty fields map"
    # A known global key is present and well-formed.
    gmc = fields.get("global_max_concurrent")
    assert gmc is not None, "global_max_concurrent must be classified"
    for key in ("origin", "apply_timing", "env_locked", "is_secret"):
        assert key in gmc, f"missing {key} in field descriptor"
    assert gmc["origin"] in ("default", "global", "env")
    assert gmc["apply_timing"] in ("immediate", "restart")


def test_origins_flags_changed_field_as_global():
    import bulk_downloader.app as a
    c = a.app.test_client()
    before = a._app_cfg.get("global_max_concurrent")
    a._app_cfg["global_max_concurrent"] = (before or 0) + 7
    try:
        body = c.get("/api/global_config/origins").get_json()
        assert body["fields"]["global_max_concurrent"]["origin"] == "global"
    finally:
        a._app_cfg["global_max_concurrent"] = before


def test_origins_never_emits_secret_values():
    import bulk_downloader.app as a
    c = a.app.test_client()
    body = c.get("/api/global_config/origins").get_json()
    fields = body["fields"]
    secret_fields = [k for k, v in fields.items() if v.get("is_secret")]
    assert secret_fields, "at least one secret field expected in the classification"
    for k in secret_fields:
        # A secret descriptor reports it's a secret, but carries no value key.
        assert "value" not in fields[k], f"secret field {k} leaked a value"
