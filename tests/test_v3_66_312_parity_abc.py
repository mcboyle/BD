"""v3.66.312 — CLI->GUI parity, three groups in one cut (A + B + C).

A — Browser backend group (4 env vars -> full). BD_BROWSER_BACKEND / BD_USE_CLOAK /
    BD_USE_CLOAKBROWSER all funnel through cloak.resolve_backend(), which ALREADY reads
    the global_config store (precedence: per-call config > env > store > default), so a
    Settings write to `browser_backend` is honored when the env var is unset (env stays a
    deliberate deploy override). BD_NOVNC_URL is read call-time in tools/cockpit_core.py;
    rewired to store > env > default via a new `novnc_url` global_config key.

B — Widgets (6 widgets_config keys cleared). The dashboard ALREADY wires the widgets store
    (/api/widgets/all GET + /api/widgets/<scope> GET/POST/DELETE), so the 4 substantive keys
    (global/per_site/size + the source-declared per-site entry) are gui_exposure=full; the 2
    store-metadata keys (_saved_at, schema_version) are not user-tunable -> display-only.

C — Phase 4.6 read panel (19 deploy/path/import-time keys -> display-only). The backend
    /api/settings/env/effective is extended to surface the display-only set (effective value +
    default + how-to-override); a read-only SPA panel renders it. No write surface.

RED-first: every assertion fails on pristine v3.66.311. Custom-runner safe; zero-arg tests;
env restored in try/finally.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_BROWSER_ENV = ("BD_BROWSER_BACKEND", "BD_USE_CLOAK", "BD_USE_CLOAKBROWSER", "BD_NOVNC_URL")
_WIDGETS_FULL = ("widgets.global", "widgets.per_site", "widgets.size", "widgets.wowgirls")
_WIDGETS_DISPLAY = ("widgets._saved_at",)
# v3.66.507 (Bucket 3b): widgets.schema_version was promoted display-only -> full
# via the raw store-metadata editor. Only widgets._saved_at stays display-only.
_WIDGETS_PROMOTED_FULL_507 = ("widgets.schema_version",)
# v3.66.503 (Bucket 1): the 7 HLS/Live vars were removed from the display-only set
# (promoted to full live controls). The remaining 12 are the genuine deploy/path
# env vars that stay display-only (set before the process starts).
_DISPLAY_DEPLOY = (
    "BD_CAPTURES_ROOT", "BD_DEV_MODE_DISABLE", "BD_DISABLE_KEEPALIVE",
    "BD_HOME", "BD_INSTALL_DIR", "BD_KB_DIR",
    "BD_LOG_FILE", "BD_REPO", "BD_ROOT", "BD_SITES_CONFIG_PATH", "BD_VPN_CONFIG_PATH",
    "BD_WIDGETS_CONFIG_PATH",
)


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


# ── A: schema + store-over-env wiring ────────────────────────────────────────
def test_a_schema_has_browser_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert s["browser_backend"]["type"] is str
    assert s["novnc_url"]["type"] is str


def test_a_resolve_backend_honors_store_when_env_unset():
    from bulk_downloader import cloak
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = {k: os.environ.get(k) for k in cloak._ENV_KEYS}
    try:
        for k in cloak._ENV_KEYS:
            os.environ.pop(k, None)
        _fresh_store({"browser_backend": "playwright"})
        assert cloak.resolve_backend() == "playwright"   # store honored (env unset)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.chdir(cwd)


def test_a_novnc_url_store_over_env():
    import importlib
    cc = importlib.import_module("cockpit_core")
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_NOVNC_URL")
    try:
        os.environ.pop("BD_NOVNC_URL", None)
        _fresh_store({"novnc_url": "https://store.example/vnc"})
        assert cc._resolve_novnc_url() == "https://store.example/vnc"   # store wins when env unset
        os.environ["BD_NOVNC_URL"] = "https://env.example/vnc"
        _fresh_store({"novnc_url": "https://store.example/vnc"})
        assert cc._resolve_novnc_url() == "https://env.example/vnc"     # env override preserved
    finally:
        if saved is None:
            os.environ.pop("BD_NOVNC_URL", None)
        else:
            os.environ["BD_NOVNC_URL"] = saved
        os.chdir(cwd)


def test_a_browser_env_full_in_inventory_and_manifest():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _BROWSER_ENV:
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    for k in _BROWSER_ENV:
        assert m.get(k) == "full", k


# ── B: widgets full + metadata display-only ──────────────────────────────────
def test_b_widgets_classification():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "widgets_config"}
    for k in _WIDGETS_FULL:
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])
    for k in _WIDGETS_DISPLAY:
        assert items[k]["runtime_tunable"] is False, k
        assert items[k]["gui_exposure"] == "display-only", (k, items[k]["gui_exposure"])
    # v3.66.507: widgets.schema_version promoted to full (raw store editor).
    for k in _WIDGETS_PROMOTED_FULL_507:
        assert items[k]["runtime_tunable"] is True, k
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])


def test_b_spa_wires_widgets_store():
    blob = ""
    for p in (_REPO / "frontend" / "src").rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    assert "/api/widgets/all" in blob
    assert "/api/widgets/" in blob   # /<scope> GET/POST/DELETE


# ── C: 4.6 read panel ────────────────────────────────────────────────────────
def test_c_env_effective_surfaces_display_only_set():
    from bulk_downloader import app_settings_center as sc
    body = sc._env_effective()
    names = {r["name"] for r in body.get("env", [])}
    missing = [k for k in _DISPLAY_DEPLOY if k not in names]
    assert not missing, f"env/effective missing display-only keys: {missing}"


def test_c_display_only_keys_satisfied_in_inventory():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in _DISPLAY_DEPLOY:
        assert items[k]["gui_exposure"] == "display-only", (k, items[k]["gui_exposure"])
    # display_open tally fully cleared (env display-only set delivered)
    assert d["counts"]["display_open"] == 0, d["counts"]["display_open"]


def test_c_spa_read_panel_wires_env_effective():
    blob = ""
    for p in (_REPO / "frontend" / "src").rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    assert "/api/settings/env/effective" in blob


# ── ratchet: open drops by A(4)+B(6); manifest ledgers everything ────────────
def test_ratchet_open_dropped():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    assert base["open_count"] <= 80, base["open_count"]   # 90 - 4 (A) - 6 (B); ceiling
    for k in _BROWSER_ENV + _WIDGETS_FULL + _WIDGETS_DISPLAY:
        assert k not in base["open"], k
    assert base.get("display_open", ["x"]) == [] or len(base.get("display_open", [])) == 0


# ── version landed at/after 312 (stable, not a live pin) ─────────────────────
def test_landed_at_or_after_312():
    from bulk_downloader import __version__
    parts = tuple(int(x) for x in __version__.split(".")[:3])
    assert parts >= (3, 66, 312), __version__
