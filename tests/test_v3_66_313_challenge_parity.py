"""v3.66.313 — CLI->GUI parity Phase 4.2: Challenge-handling group (NON-guard subset).

Read-site grep classified the 5 Challenge env vars heterogeneously:
  - BD_HONEYPOT_SCORE_THRESHOLD (provider_resolve.py, call-time)   -> FULL
  - BD_HONEYPOT_PER_SITE        (honeypot_threshold.enabled(), call-time) -> FULL
  - BD_CAPTCHA_PENDING_TIMEOUT_S / BD_CAPTCHA_PUSH_DEDUPE_S (captcha_relay.py
        MODULE-LEVEL constants, import-bound)                      -> DISPLAY-ONLY (4.6)
  - BD_CHALLENGE_WAIT_S  (tools/capture_session.py == a GUARD file) -> DEFERRED to a
        separate, explicitly-authorized guard cut (NOT promoted here).

Full promotion uses the canonical pattern: a global_config key read store > env > default
at call time (GUI write authoritative; env is the seed when the store key is unset).
honeypot_score_threshold is stored as a STR (the read site float()-parses it; matches env
semantics and avoids the int-vs-float type_mismatch edge).

RED-first: every promote/classify assertion fails on pristine v3.66.312. Zero-arg tests;
env + store restored in try/finally.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_FULL = ("BD_HONEYPOT_SCORE_THRESHOLD", "BD_HONEYPOT_PER_SITE",
         # v3.66.503 (Bucket 1): captcha-relay timeouts promoted display-only -> full
         "BD_CAPTCHA_PENDING_TIMEOUT_S", "BD_CAPTCHA_PUSH_DEDUPE_S")
_DISPLAY = ()
_GUARD_DEFERRED = "BD_CHALLENGE_WAIT_S"


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_challenge_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert s["honeypot_score_threshold"]["type"] is str
    assert s["honeypot_per_site"]["type"] is bool


# ── full: provider_resolve honors store over env (store > env > default) ─────
def test_honeypot_threshold_store_over_env():
    import bulk_downloader.provider_resolve as PR
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_HONEYPOT_SCORE_THRESHOLD")
    try:
        os.environ.pop("BD_HONEYPOT_SCORE_THRESHOLD", None)
        _fresh_store({"honeypot_score_threshold": "0.8"})
        assert PR._honeypot_score_threshold() == 0.8           # store honored, env unset
        os.environ["BD_HONEYPOT_SCORE_THRESHOLD"] = "0.6"
        _fresh_store({"honeypot_score_threshold": "0.8"})
        assert PR._honeypot_score_threshold() == 0.8           # store wins over env
        _fresh_store({})
        assert PR._honeypot_score_threshold() == 0.6           # env is the seed when store unset
    finally:
        if saved is None:
            os.environ.pop("BD_HONEYPOT_SCORE_THRESHOLD", None)
        else:
            os.environ["BD_HONEYPOT_SCORE_THRESHOLD"] = saved
        os.chdir(cwd)


def test_honeypot_per_site_store_over_env():
    import bulk_downloader.honeypot_threshold as HT
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_HONEYPOT_PER_SITE")
    try:
        os.environ.pop("BD_HONEYPOT_PER_SITE", None)
        _fresh_store({"honeypot_per_site": True})
        assert HT.enabled() is True                            # store honored, env unset
        _fresh_store({"honeypot_per_site": False})
        os.environ["BD_HONEYPOT_PER_SITE"] = "1"
        assert HT.enabled() is False                           # store wins over env=1
        _fresh_store({})
        assert HT.enabled() is True                            # env seed when store unset
    finally:
        if saved is None:
            os.environ.pop("BD_HONEYPOT_PER_SITE", None)
        else:
            os.environ["BD_HONEYPOT_PER_SITE"] = saved
        os.chdir(cwd)


# ── inventory + manifest ─────────────────────────────────────────────────────
def test_inventory_full_and_display():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _FULL:
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])
    for k in _DISPLAY:
        assert items[k]["runtime_tunable"] is False, k
        assert items[k]["gui_exposure"] == "display-only", (k, items[k]["gui_exposure"])


def test_guard_var_deferred_resolved_at_314():
    """BD_CHALLENGE_WAIT_S backs a GUARD file (capture_session.py) and was DEFERRED
    out of the 313 non-guard cut. The deferral was resolved by the explicitly-
    authorized guard cut v3.66.314, which promoted it to full. This test now
    confirms the var carries through as promoted (the 313 deferral is retired)."""
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    assert items[_GUARD_DEFERRED]["gui_exposure"] == "full", "promoted at the 314 guard cut"
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    assert m.get(_GUARD_DEFERRED) == "full", "on the manifest as of the 314 guard cut"


def test_env_effective_no_longer_lists_promoted_captcha():
    # v3.66.503: captcha timeouts moved out of the read-only env-lock panel into
    # full editable controls, so they must NOT appear in _env_effective() anymore.
    from bulk_downloader import app_settings_center as sc
    names = {r["name"] for r in sc._env_effective().get("env", [])}
    for k in ("BD_CAPTCHA_PENDING_TIMEOUT_S", "BD_CAPTCHA_PUSH_DEDUPE_S"):
        assert k not in names, k


def test_manifest_ledgers_challenge():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    for k in _FULL:
        assert m.get(k) == "full", k
    for k in _DISPLAY:
        assert m.get(k) == "display-only", k


def test_ratchet_open_dropped():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    assert base["open_count"] <= 76, base["open_count"]   # 80 - 2 full - 2 display; ceiling
    for k in _FULL + _DISPLAY:
        assert k not in base["open"], k


def test_spa_challenge_controls_present():
    blob = ""
    for p in (_REPO / "frontend" / "src").rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    assert "honeypot_score_threshold" in blob
    assert "honeypot_per_site" in blob


def test_landed_at_or_after_313():
    from bulk_downloader import __version__
    parts = tuple(int(x) for x in __version__.split(".")[:3])
    assert parts >= (3, 66, 313), __version__
