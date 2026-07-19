"""v3.66.308 — CLI->GUI parity: guard-backed Capture promotion.

Promotes 5 Capture env vars into global_config so a Settings write takes effect
without a restart, and FIXES the 306 latent bug where POST /api/global_config
silently dropped schema keys it had no explicit branch for (queue_hk_* never
persisted). RED-first: every assertion below fails on pristine v3.66.307 source.

Harness notes: custom runner (no pytest fixtures); zero-arg tests; derive repo
root from __file__; restore module globals in try/finally; prestaged has Flask.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_CAPTURE_KEYS = (
    "BD_CAPTURE_BODIES", "BD_CAPTURE_WAIT_UNTIL", "BD_CAPTURE_RAW",
    "BD_DOM_HONEYPOT_FILTER", "BD_REDACT_DOM_URLS",
)


def _fresh_store(d: dict) -> None:
    """Point global_config at a fresh app_config.json holding `d` and clear its
    mtime cache so the next get_config() reads our write."""
    from bulk_downloader import global_config as GC
    p = Path("app_config.json")
    p.write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_five_capture_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert s["capture_bodies"]["type"] is bool
    assert s["capture_wait_until"]["type"] is str
    assert s["dom_honeypot_filter"]["type"] is str
    assert s["redact_dom_urls"]["type"] is str
    assert s["capture_raw"]["type"] is bool
    # operator directive: capture_raw is value-honored, NOT fail-closed-coerced
    assert s["capture_raw"]["safety"] is False


# ── the 306 bug: generic schema-driven write path must persist ────────────────
def test_post_persists_schema_key_queue_hk():
    """The 306 regression: a SPA save of a global_config schema key must reach
    disk + global_config.get(). On 307 this drops silently (POST 200, no write)."""
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    try:
        from bulk_downloader import app as A
        from bulk_downloader import global_config as GC
        GC._cached = None
        GC._cached_mtime = 0.0
        c = A.app.test_client()
        r = c.post("/api/global_config", json={"queue_hk_gc_age_days": 14})
        assert r.status_code == 200, r.status_code
        assert GC.get("queue_hk_gc_age_days", "<unset>") == 14
        disk = json.loads(Path(tmp, "app_config.json").read_text())
        assert disk.get("queue_hk_gc_age_days") == 14
    finally:
        os.chdir(cwd)


def test_post_persists_capture_keys():
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    try:
        from bulk_downloader import app as A
        from bulk_downloader import global_config as GC
        GC._cached = None
        GC._cached_mtime = 0.0
        c = A.app.test_client()
        body = {"capture_bodies": True, "capture_wait_until": "domcontentloaded",
                "dom_honeypot_filter": "strict", "redact_dom_urls": "keep_full",
                "capture_raw": True}
        r = c.post("/api/global_config", json=body)
        assert r.status_code == 200, r.status_code
        for k, v in body.items():
            assert GC.get(k, "<unset>") == v, (k, GC.get(k, "<unset>"))
    finally:
        os.chdir(cwd)


def test_post_rejects_bad_typed_schema_key():
    """Type backstop (§3.2): a wrong-typed schema value is rejected 400, not
    coerced/persisted."""
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    try:
        from bulk_downloader import app as A
        from bulk_downloader import global_config as GC
        GC._cached = None
        GC._cached_mtime = 0.0
        c = A.app.test_client()
        r = c.post("/api/global_config", json={"queue_hk_gc_age_days": "not-an-int"})
        assert r.status_code == 400, r.status_code
        assert GC.get("queue_hk_gc_age_days", "<unset>") == "<unset>"
    finally:
        os.chdir(cwd)


# ── read sites honor the store over the env seed ──────────────────────────────
def test_bodies_enabled_store_over_env():
    from bulk_downloader import capture_bodies as CB
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_CAPTURE_BODIES")
    try:
        os.environ.pop("BD_CAPTURE_BODIES", None)
        _fresh_store({"capture_bodies": True})
        assert CB.bodies_enabled() is True            # store on, env unset
        os.environ["BD_CAPTURE_BODIES"] = "1"
        _fresh_store({"capture_bodies": False})
        assert CB.bodies_enabled() is False           # store wins over env=1
    finally:
        if saved is None:
            os.environ.pop("BD_CAPTURE_BODIES", None)
        else:
            os.environ["BD_CAPTURE_BODIES"] = saved
        os.chdir(cwd)


def test_wait_until_store_over_env():
    sys.path.insert(0, str(_REPO / "tools"))
    import capture_session as CS
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_CAPTURE_WAIT_UNTIL")
    try:
        os.environ.pop("BD_CAPTURE_WAIT_UNTIL", None)
        _fresh_store({"capture_wait_until": "domcontentloaded"})
        assert CS._resolve_capture_wait_until() == "domcontentloaded"
        _fresh_store({"capture_wait_until": "bogus"})
        assert CS._resolve_capture_wait_until() is None   # invalid -> unchanged
    finally:
        if saved is None:
            os.environ.pop("BD_CAPTURE_WAIT_UNTIL", None)
        else:
            os.environ["BD_CAPTURE_WAIT_UNTIL"] = saved
        os.chdir(cwd)


def test_honeypot_mode_store_over_env():
    from bulk_downloader import detect as D
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_DOM_HONEYPOT_FILTER")
    try:
        os.environ.pop("BD_DOM_HONEYPOT_FILTER", None)
        _fresh_store({"dom_honeypot_filter": "strict"})
        assert D._dom_honeypot_mode() == "strict"
        _fresh_store({"dom_honeypot_filter": "nonsense"})
        assert D._dom_honeypot_mode() == "off"
    finally:
        if saved is None:
            os.environ.pop("BD_DOM_HONEYPOT_FILTER", None)
        else:
            os.environ["BD_DOM_HONEYPOT_FILTER"] = saved
        os.chdir(cwd)


def test_redact_dom_urls_store_over_env():
    from bulk_downloader import redaction_profile as RP
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_REDACT_DOM_URLS")
    try:
        os.environ.pop("BD_REDACT_DOM_URLS", None)
        _fresh_store({"redact_dom_urls": "keep_full"})
        assert RP.current_profile()["dom_embedded_urls"] == "keep_full"
    finally:
        if saved is None:
            os.environ.pop("BD_REDACT_DOM_URLS", None)
        else:
            os.environ["BD_REDACT_DOM_URLS"] = saved
        os.chdir(cwd)


# ── capture_raw passthrough seam (operator override; no stamp, no fail-closed) ─
def test_capture_raw_installs_passthrough_redactor():
    from bulk_downloader import capture_redactor as CR
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_CAPTURE_RAW")
    try:
        os.environ.pop("BD_CAPTURE_RAW", None)
        _fresh_store({})
        assert CR.active_redactor() is CR._REAL          # default: real redactor
        _fresh_store({"capture_raw": True})
        r = CR.active_redactor()
        assert r is not CR._REAL                          # raw -> passthrough
        assert r.unredacted is True                       # self-identifying flag
        # passthrough returns body + url unchanged (no redaction)
        sample = '{"signed":"https://cdn.example/x?sig=SECRET"}'
        assert r.response_body(sample, "application/json") == sample
        url = "https://cdn.example/x?sig=SECRET"
        assert r.query(url) == url
    finally:
        if saved is None:
            os.environ.pop("BD_CAPTURE_RAW", None)
        else:
            os.environ["BD_CAPTURE_RAW"] = saved
        os.chdir(cwd)


# ── inventory: 5 keys promoted to full, ratchet 155 -> 150, danger surfaced ────
def test_inventory_capture_keys_full_and_danger():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _CAPTURE_KEYS:
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])
    # Irrecoverable controls carry a disclaimer: the two guard-backed keys +
    # the redaction-weakening keys. The honeypot filter is detection-quality,
    # not a leak/integrity control, so it is not flagged danger.
    for k in ("BD_CAPTURE_BODIES", "BD_CAPTURE_WAIT_UNTIL",
              "BD_CAPTURE_RAW", "BD_REDACT_DOM_URLS"):
        assert items[k]["danger"] is True, k
        assert items[k]["danger_note"], k


def test_ratchet_baseline_dropped_to_150():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    # Ceiling, not equality: the 308 cut burned the ratchet to AT MOST 150.
    # Later parity cuts only lower it further — a `== 150` pin re-breaks on each
    # (309 dropped it to 141). The durable per-cut check is key membership below.
    assert base["open_count"] <= 150, base["open_count"]
    for k in _CAPTURE_KEYS:
        assert k not in base["open"], k


def test_manifest_ledgers_five_capture_keys():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text())
    exposed = m.get("exposed", m)
    for k in _CAPTURE_KEYS:
        assert exposed.get(k) == "full", k
