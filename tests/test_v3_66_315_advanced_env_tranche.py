"""v3.66.315 — CLI->GUI parity Phase 4.2: the Advanced env tranche (non-guard).

Promotes 15 runtime tunables to FULL (call-time read sites, store > env seed >
default) and classifies 5 deploy/import-bound vars DISPLAY-ONLY. None touch a
guard file (BD_HUD_OVERLAY / BD_LINT_KB_ALLOW are deferred to a follow-on guard
cut; BD_COCKPIT_SHELL + the two path roots are deferred per operator decision).

FULL (15): auth-throttle (enable + free/base/max), redaction greys
(emails/extra_headers/network_urls — danger_note: weakens retention),
secrets-audit (mode/sink/file/max_bytes), autonomy day-windows
(held_out_stale_days / lib_reconcile_missing_days), fleet_nodes, youtube_cipher.

DISPLAY-ONLY (5): BD_COCKPIT_PORT / BD_FLEET_PORT / BD_FRAMEWORK_PORT (bind ports,
deploy-time) + BD_DISABLE_VPN_RUNTIME / BD_VPN_LEAK_INTERVAL_S (module-level
constants, import-bound).

Numerics are stored as STR with ""=unset (read site casts; matches the
honeypot_score_threshold / challenge_wait_s precedent). auth_throttle is bool.

RED-first: every promote/classify assertion fails on pristine v3.66.314. Zero-arg
tests; env + store restored in try/finally; cwd restored.
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_FULL = (
    "BD_AUTH_THROTTLE", "BD_AUTH_THROTTLE_FREE", "BD_AUTH_THROTTLE_BASE", "BD_AUTH_THROTTLE_MAX",
    "BD_REDACT_EMAILS", "BD_REDACT_EXTRA_HEADERS", "BD_REDACT_NETWORK_URLS",
    "BD_SECRETS_AUDIT", "BD_SECRETS_AUDIT_SINK", "BD_SECRETS_AUDIT_FILE", "BD_SECRETS_AUDIT_MAX_BYTES",
    "BD_HELD_OUT_STALE_DAYS", "BD_LIB_RECONCILE_MISSING_DAYS",
    "BD_FLEET_NODES", "BD_YOUTUBE_CIPHER",
)
_DISPLAY = (
    "BD_COCKPIT_PORT", "BD_FLEET_PORT", "BD_FRAMEWORK_PORT",
    "BD_DISABLE_VPN_RUNTIME", "BD_VPN_LEAK_INTERVAL_S",
)
_STORE_KEYS = (
    "auth_throttle", "auth_throttle_free", "auth_throttle_base", "auth_throttle_max",
    "redact_emails", "redact_extra_headers", "redact_network_urls",
    "secrets_audit", "secrets_audit_sink", "secrets_audit_file", "secrets_audit_max_bytes",
    "held_out_stale_days", "lib_reconcile_missing_days", "fleet_nodes", "youtube_cipher",
)


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


class _Env:
    """Save/restore a set of env vars around a block."""
    def __init__(self, names):
        self.names = names
    def __enter__(self):
        self.saved = {n: os.environ.get(n) for n in self.names}
        for n in self.names:
            os.environ.pop(n, None)
        return self
    def __exit__(self, *a):
        for n, v in self.saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


# ── schema ───────────────────────────────────────────────────────────────────
def test_schema_has_all_advanced_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    for k in _STORE_KEYS:
        assert k in s, f"missing schema key {k}"
    assert s["auth_throttle"]["type"] is bool
    # numerics stored as str (""=unset)
    for k in ("auth_throttle_free", "auth_throttle_base", "auth_throttle_max",
              "secrets_audit_max_bytes", "held_out_stale_days", "lib_reconcile_missing_days"):
        assert s[k]["type"] is str, k


# ── full read sites: store > env seed > default ──────────────────────────────
def test_auth_throttle_store_over_env():
    import bulk_downloader.auth_throttle as AT
    importlib.reload(AT)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_AUTH_THROTTLE", "BD_AUTH_THROTTLE_FREE",
                   "BD_AUTH_THROTTLE_BASE", "BD_AUTH_THROTTLE_MAX"]):
            _fresh_store({"auth_throttle": True})
            assert AT.is_enabled() is True            # store honored, env unset
            os.environ["BD_AUTH_THROTTLE"] = "0"
            _fresh_store({"auth_throttle": True})
            assert AT.is_enabled() is True            # store wins over env
            _fresh_store({})
            os.environ["BD_AUTH_THROTTLE"] = "1"
            assert AT.is_enabled() is True            # env seed when store unset
            # numerics
            os.environ.pop("BD_AUTH_THROTTLE", None)
            _fresh_store({"auth_throttle": True, "auth_throttle_free": "9",
                          "auth_throttle_base": "3.5", "auth_throttle_max": "120"})
            free, base, mx = AT._cfg()
            assert (free, base, mx) == (9, 3.5, 120.0), (free, base, mx)
    finally:
        os.chdir(cwd)


def test_redaction_store_over_env():
    import bulk_downloader.redaction_profile as RP
    importlib.reload(RP)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_REDACT_EMAILS", "BD_REDACT_EXTRA_HEADERS", "BD_REDACT_NETWORK_URLS"]):
            _fresh_store({"redact_emails": "keep"})
            assert RP._emails_mode() == "keep"
            os.environ["BD_REDACT_EMAILS"] = "redact"
            _fresh_store({"redact_emails": "keep"})
            assert RP._emails_mode() == "keep"        # store wins
            _fresh_store({"redact_extra_headers": "x-foo, x-bar"})
            assert "x-foo" in RP._custom_headers() and "x-bar" in RP._custom_headers()
            _fresh_store({"redact_network_urls": "keep_full"})
            prof = RP.current_profile()
            assert prof["network_signed_urls"] == "keep_full"
    finally:
        os.chdir(cwd)


def test_secrets_audit_store_over_env():
    import bulk_downloader.secrets_audit as SA
    importlib.reload(SA)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_SECRETS_AUDIT", "BD_SECRETS_AUDIT_SINK",
                   "BD_SECRETS_AUDIT_FILE", "BD_SECRETS_AUDIT_MAX_BYTES"]):
            _fresh_store({"secrets_audit": "all"})
            assert SA.mode() == "all"
            os.environ["BD_SECRETS_AUDIT"] = "off"
            _fresh_store({"secrets_audit": "mutations"})
            assert SA.mode() == "mutations"           # store wins
            _fresh_store({"secrets_audit_sink": "stderr"})
            assert SA._sink() == "stderr"
            _fresh_store({"secrets_audit_max_bytes": "4096"})
            assert SA._max_bytes() == 4096
    finally:
        os.chdir(cwd)


def test_autonomy_and_misc_store_over_env():
    import autonomy_held_out_assist as HO
    import autonomy_library_reconcile as LR
    import framework_fleet as FF
    import bulk_downloader.provider_resolve as PR
    for m in (HO, LR, FF):
        importlib.reload(m)
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        with _Env(["BD_HELD_OUT_STALE_DAYS", "BD_LIB_RECONCILE_MISSING_DAYS",
                   "BD_FLEET_NODES", "BD_YOUTUBE_CIPHER"]):
            _fresh_store({"held_out_stale_days": "45"})
            assert HO._stale_days() == 45
            _fresh_store({"lib_reconcile_missing_days": "7"})
            assert LR._missing_days() == 7
            # youtube cipher
            _fresh_store({"youtube_cipher": "yt-dlp"})
            assert PR._yt_cipher_backend() == "yt-dlp"
            os.environ["BD_YOUTUBE_CIPHER"] = "off"
            _fresh_store({"youtube_cipher": "player-js"})
            assert PR._yt_cipher_backend() == "player-js"   # store wins
            # fleet_nodes path: store points at a json file
            nf = Path(tmp) / "nodes.json"; nf.write_text("[]", encoding="utf-8")
            _fresh_store({"fleet_nodes": str(nf)})
            assert FF._nodes() == []                   # store path read, valid empty list
    finally:
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


def test_redaction_carries_danger_note():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    assert items["BD_REDACT_NETWORK_URLS"]["danger"] is True


def test_manifest_ledgers_tranche():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    for k in _FULL:
        assert m.get(k) == "full", k
    for k in _DISPLAY:
        assert m.get(k) == "display-only", k


def test_ratchet_open_dropped():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    assert base["open_count"] <= 55, base["open_count"]   # 75 - 15 full - 5 display
    for k in _FULL + _DISPLAY:
        assert k not in base["open"], k


# ── SPA controls present ─────────────────────────────────────────────────────
def test_spa_controls_present():
    blob = ""
    for p in (_REPO / "frontend" / "src").rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    for k in _STORE_KEYS:
        assert k in blob, f"SPA control missing for {k}"


# ── version floor ────────────────────────────────────────────────────────────
def test_landed_at_or_after_315():
    from bulk_downloader import __version__
    parts = tuple(int(x) for x in __version__.split(".")[:3])
    assert parts >= (3, 66, 315), __version__
