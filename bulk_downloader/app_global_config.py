"""global_config API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/global_config views moved onto a Flask Blueprint.
Endpoint labels gain a "global_config." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (_APP_CFG_DEFAULTS, _ORIGINS_APPLY_RESTART, _ORIGINS_SECRET_FIELDS, _app_cfg) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import sys
from flask import Blueprint, jsonify, request

global_config_bp = Blueprint("global_config", __name__)

def _origins_env_locked(*_a, **_k):
    """Delegate to app._origins_env_locked at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_origins_env_locked")(*_a, **_k)

def _save_app_config(*_a, **_k):
    """Delegate to app._save_app_config at call time (lazy; avoids an import cycle)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_save_app_config")(*_a, **_k)

def _app__APP_CFG_DEFAULTS():
    """The live shared _APP_CFG_DEFAULTS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_APP_CFG_DEFAULTS")

def _app__ORIGINS_APPLY_RESTART():
    """The live shared _ORIGINS_APPLY_RESTART from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_ORIGINS_APPLY_RESTART")

def _app__ORIGINS_SECRET_FIELDS():
    """The live shared _ORIGINS_SECRET_FIELDS from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app"), "_ORIGINS_SECRET_FIELDS")

def _app__app_cfg():
    """The live shared _app_cfg from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_kernel"), "_app_cfg")


# v3.66.709: keys owned by the explicit branches below (bespoke validation or
# side-effects), which are NOT necessarily in GLOBAL_CONFIG_SCHEMA. The unknown-key
# 400 must accept these too. Pinned by test_explicit_branch_keys_match_source so a
# new branch cannot drift out of this set and start 400ing a legitimate write.
_EXPLICIT_BRANCH_KEYS = frozenset({
    "ai_api_key",
    "ai_enabled",
    "ai_endpoint",
    # v3.66.780 (CFG-PARITY-WRITE): the model-name pair is read+written by the
    # explicit ai_* branch (L251-252, mapped to aiassist at L268-269) but was
    # never in this accepted set -> every Settings save 400'd. Sibling of
    # ai_endpoint/ai_provider; belongs here, not in GLOBAL_CONFIG_SCHEMA.
    "ai_model_text",
    "ai_model_vision",
    "ai_provider",
    # v3.66.781 (CFG-PARITY-WRITE read-side): read+written by the same explicit
    # loop branch as watch_folder (`for k in ("watch_folder",
    # "default_quick_add_site")` at L178-179), but its tuple-sibling was missed
    # when watch_folder was added at 780 -> a POST for it 400'd (unwritable). The
    # widened read-side scanner (test_explicit_branch_keys_match_source) now sees
    # loop reads and caught it.
    "default_quick_add_site",
    "global_daily_byte_budget",
    "global_max_concurrent",
    "log_level",
    "path_allowlist",
    "rate_limit_domain_overrides",
    "rate_limit_global_concurrent",
    "rate_limit_global_per_sec",
    # v3.66.780 (CFG-PARITY-WRITE): the keep-alive timing knobs are read+written
    # by the explicit branch at L176-186 (int, clamped [1,360]) but were absent
    # from the accepted set -> every keep-alive save 400'd.
    "session_keep_alive_fetch_interval_min",
    "session_keep_alive_lead_time_min",
    "session_keep_alive_navigate_interval_min",
    "session_keeper_use_cloakbrowser",
    "sound_on_complete",
    "sound_sync_enabled",
    "template_auto_detect_mode",
    "ui_logging_level",
    "watch_archive",
    # v3.66.780 (CFG-PARITY-WRITE): read+written by the explicit branch at
    # L163-164 and consumed by app.py's folder watcher; sibling of watch_archive.
    "watch_folder",
    "watch_interval_sec",
})


@global_config_bp.route("/api/global_config/defaults", methods=["GET"])


def api_global_config_defaults():
    """P6-8: the SHIPPED global-config defaults (frozen at module load).

    Read-only. The Settings page diffs the live /api/global_config values
    against this baseline to badge settings the operator has changed from
    their default. Returns the _app_cfg seed snapshot, never the live (and
    possibly operator-modified) config."""
    _APP_CFG_DEFAULTS = _app__APP_CFG_DEFAULTS()
    return jsonify(dict(_APP_CFG_DEFAULTS))
@global_config_bp.route("/api/global_config/origins", methods=["GET"])
def api_global_config_origins():
    """Per-field origin + apply-timing classification (read-only).

    origin        : "env" if env-locked, else "global" if the live value differs
                    from the shipped default, else "default".
    apply_timing  : "restart" for the static restart set, else "immediate".
    env_locked    : bool — pinned by an environment variable.
    is_secret     : bool — secret-bearing field (value is never emitted).
    """
    _APP_CFG_DEFAULTS = _app__APP_CFG_DEFAULTS()
    _ORIGINS_APPLY_RESTART = _app__ORIGINS_APPLY_RESTART()
    _ORIGINS_SECRET_FIELDS = _app__ORIGINS_SECRET_FIELDS()
    _app_cfg = _app__app_cfg()
    fields = {}
    for key, default_val in _APP_CFG_DEFAULTS.items():
        is_secret = key in _ORIGINS_SECRET_FIELDS
        env_locked = _origins_env_locked(key)
        live_val = _app_cfg.get(key, default_val)
        if env_locked:
            origin = "env"
        elif live_val != default_val:
            origin = "global"
        else:
            origin = "default"
        desc = {
            "origin": origin,
            "apply_timing": "restart" if key in _ORIGINS_APPLY_RESTART else "immediate",
            "env_locked": env_locked,
            "is_secret": is_secret,
        }
        # Refs only for secrets — never serialize the value itself. For
        # non-secret fields we still omit the value here (this endpoint is
        # about provenance, not values; /api/global_config carries values).
        fields[key] = desc
    return jsonify({"ok": True, "fields": fields})
@global_config_bp.route("/api/global_config",methods=["GET","POST"])
def api_global_config():
    """Read/write global app settings. Currently:
      global_max_concurrent (int)  — total concurrent URLs across all sites
        combined. 0 = uncapped (per-site limits apply only).
      watch_folder (str)           — Phase 18.22 folder watcher. Empty = off.
      watch_interval_sec (int)     — poll interval, default 30
      watch_archive (bool)         — move processed files to /processed
      default_quick_add_site (str) — fallback site_id for /api/quick_add
      ai_enabled (bool)            — Phase 27: turn on AI-assist (default off)
      ai_endpoint (str)            — Phase 27: Ollama-style URL
      ai_model_vision (str)        — Phase 27: model for screenshot prompts
      ai_model_text   (str)        — Phase 27: model for text-only prompts
      sound_sync_enabled (bool)    — v3.64.3: opt-in cross-device sync of
                                     the completion-sound toggle (default
                                     False; localStorage path otherwise).
      sound_on_complete (bool)     — v3.64.3: server-side completion-sound
                                     value, read only when sync is on."""
    _app_cfg = _app__app_cfg()
    from . import aiassist
    if request.method=="POST":
        data=request.json or {}
        if "global_max_concurrent" in data:
            n=max(0,min(64,int(data["global_max_concurrent"])))
            _app_cfg["global_max_concurrent"]=n
            from .runner import set_global_concurrent_cap
            set_global_concurrent_cap(n)
        # Cut 8: global (cross-site) daily byte budget. 0 = uncapped. Mirrors
        # the concurrent-cap side-effect: propagate to daily_budget module state
        # so the worker seam enforces it. Non-int -> 400 (no silent coerce).
        if "global_daily_byte_budget" in data:
            try:
                gb = int(data["global_daily_byte_budget"])
            except (TypeError, ValueError):
                return jsonify({"error": "global_daily_byte_budget must be "
                                         "an integer (bytes)"}), 400
            gb = max(0, gb)
            _app_cfg["global_daily_byte_budget"] = gb
            from . import daily_budget as _dbud
            _dbud.set_global_budget(gb)
        # Phase 18.22 + 16.41: pass-through string/int/bool fields
        for k in ("watch_folder", "default_quick_add_site"):
            if k in data: _app_cfg[k] = str(data[k] or "")
        if "watch_interval_sec" in data:
            _app_cfg["watch_interval_sec"] = max(5, int(data["watch_interval_sec"] or 30))
        if "watch_archive" in data:
            _app_cfg["watch_archive"] = bool(data["watch_archive"])
        # v3.64.2: Session keep-alive timing knobs. All three are
        # minute-valued ints, validated to [1, 360]. They round-trip
        # to session_keeper.py via global_config; setting them here
        # takes effect on the next keeper scheduler iteration (no
        # restart required). Out-of-range values clamp to the band
        # rather than rejecting the POST — same pattern as
        # watch_interval_sec above.
        for k in ("session_keep_alive_lead_time_min",
                  "session_keep_alive_fetch_interval_min",
                  "session_keep_alive_navigate_interval_min"):
            if k in data:
                try:
                    minutes = int(data[k])
                except (TypeError, ValueError):
                    return jsonify({"error":
                        f"{k} must be an integer (minutes)"}), 400
                minutes = max(1, min(360, minutes))
                _app_cfg[k] = minutes
        # v3.66.139: session-keeper CloakBrowser toggle. Default on
        # (CloakBrowser is the canonical browser backend); set false to
        # use vanilla Playwright for keep-alive launches. Read by
        # cloak.use_cloak() as the global default — per-site config and
        # the BD_*_CLOAK env vars still override it.
        if "session_keeper_use_cloakbrowser" in data:
            _app_cfg["session_keeper_use_cloakbrowser"] = bool(
                data["session_keeper_use_cloakbrowser"])
        # v3.64.3: opt-in sound-preference sync. Two paired keys:
        # `sound_sync_enabled` (bool, default false) controls whether
        # devices read the server value or localStorage; `sound_on_complete`
        # (bool) holds the server-side value when sync is on. Both are
        # accepted independently so a device can flip sync on without
        # immediately clobbering the server value, and so a sound-OFF
        # device that has never opted into sync still POSTs nothing
        # by default. Default-off behavior keeps the per-device design
        # from v3.64.2 intact for anyone who doesn't toggle the new
        # setting — see the relaxed DANGER_MAP entry on sound sync.
        if "sound_sync_enabled" in data:
            _app_cfg["sound_sync_enabled"] = bool(data["sound_sync_enabled"])
        if "sound_on_complete" in data:
            _app_cfg["sound_on_complete"] = bool(data["sound_on_complete"])
        # Phase 26.6: path allowlist. Array of absolute paths; when set,
        # every site's download_dir / cookie_file / spillover_dirs must
        # be a descendant of one of these. Empty (default) = legacy
        # behavior: any absolute non-traversing path accepted.
        if "path_allowlist" in data:
            v = data["path_allowlist"]
            if v is None: v = []
            if not isinstance(v, list):
                return jsonify({"error": "path_allowlist must be an array"}), 400
            _app_cfg["path_allowlist"] = [str(x) for x in v if x]
        # Phase 34: log level. Accepts DEBUG/INFO/WARNING/ERROR/CRITICAL.
        # Applied immediately via log.set_level() — no restart needed.
        if "log_level" in data:
            from . import log as _log
            level = str(data["log_level"] or "INFO").upper()
            if not _log.set_level(level):
                return jsonify({"error": f"invalid log_level: {level} (use DEBUG/INFO/WARNING/ERROR/CRITICAL)"}), 400
            _app_cfg["log_level"] = level
            _log.get_logger(__name__).info("log level changed to %s", level)
        # Phase 27 / v3.43.43: AI assist fields. Endpoint validation
        # is now provider-aware — only ollama gets the LAN-only check.
        # Cloud providers (claude/openai/gemini) accept https public
        # endpoints.
        if "ai_provider" in data:
            new_p = str(data["ai_provider"] or "ollama").strip().lower()
            if new_p not in ("ollama", "claude", "openai", "gemini"):
                return jsonify({"error":
                    f"unknown ai_provider: {new_p} "
                    "(use ollama/claude/openai/gemini)"}), 400
            _app_cfg["ai_provider"] = new_p
        if "ai_endpoint" in data:
            new_ep = str(data["ai_endpoint"] or "").strip()
            if new_ep:
                # Validate against the SOON-TO-BE provider, not the
                # currently-configured one (the user is editing both
                # in one save).
                effective_provider = _app_cfg.get("ai_provider", "ollama")
                ok, msg = aiassist.validate_endpoint(new_ep,
                                                       provider=effective_provider)
                if not ok:
                    return jsonify({"error": f"endpoint refused: {msg}"}), 400
            _app_cfg["ai_endpoint"] = new_ep
        for k in ("ai_model_vision", "ai_model_text"):
            if k in data: _app_cfg[k] = str(data[k] or "").strip()
        if "ai_enabled" in data:
            _app_cfg["ai_enabled"] = bool(data["ai_enabled"])
        if "ai_api_key" in data:
            new_key = str(data["ai_api_key"] or "")
            # v3.43.43: if the UI didn't touch the field, it sends
            # back the masking sentinel "<configured>". Treat that
            # as "no change" — keep whatever's on disk.
            if new_key == "<configured>":
                pass  # keep existing
            else:
                _app_cfg["ai_api_key"] = new_key
        # Push config through to the module
        aiassist.configure(
            provider=_app_cfg.get("ai_provider") or None,
            endpoint=_app_cfg.get("ai_endpoint") or None,
            model_vision=_app_cfg.get("ai_model_vision") or None,
            model_text=_app_cfg.get("ai_model_text") or None,
            api_key=_app_cfg.get("ai_api_key") or "",
            enabled=_app_cfg.get("ai_enabled", False),
        )
        # v3.43.16: UI event logging tier. Accepts "basic", "verbose",
        # "extreme". Persisted globally (not per-site) and read by the
        # frontend on page load via /api/global_config.
        if "ui_logging_level" in data:
            from . import ui_events as _uie
            tier = str(data["ui_logging_level"] or "basic").lower().strip()
            if tier not in _uie.VALID_TIERS:
                return jsonify({"error": f"invalid ui_logging_level: {tier} (use basic/verbose/extreme)"}), 400
            _app_cfg["ui_logging_level"] = tier
        # v3.66.5: template auto-detect mode. The site-creation pipeline
        # reads this flag and picks one of four strategies for proposing
        # login + download selectors. Validated server-side because the
        # dispatch in _auto_pick_templates coerces unknown values to
        # "static" silently and we'd rather catch typos at save time.
        if "template_auto_detect_mode" in data:
            v = str(data["template_auto_detect_mode"]
                    or "static").lower().strip()
            valid = ("static", "detect", "detect_then_static", "deep")
            if v not in valid:
                return jsonify({
                    "error": (f"invalid template_auto_detect_mode: "
                              f"{v} (use one of {', '.join(valid)})")
                }), 400
            _app_cfg["template_auto_detect_mode"] = v
        # v3.43.31: per-domain rate-limit config. Two fields for the
        # global cap + a nested dict for per-domain overrides. Live
        # changes take effect immediately because we re-apply the
        # limiter config after save.
        rate_limit_dirty = False
        if "rate_limit_global_concurrent" in data:
            try:
                _app_cfg["rate_limit_global_concurrent"] = max(0, int(
                    data["rate_limit_global_concurrent"]))
                rate_limit_dirty = True
            except (TypeError, ValueError):
                return jsonify({"error": "rate_limit_global_concurrent must be a non-negative int"}), 400
        if "rate_limit_global_per_sec" in data:
            try:
                _app_cfg["rate_limit_global_per_sec"] = max(0.0, float(
                    data["rate_limit_global_per_sec"]))
                rate_limit_dirty = True
            except (TypeError, ValueError):
                return jsonify({"error": "rate_limit_global_per_sec must be a non-negative number"}), 400
        if "rate_limit_domain_overrides" in data:
            v = data["rate_limit_domain_overrides"]
            if v is None: v = {}
            if not isinstance(v, dict):
                return jsonify({"error": "rate_limit_domain_overrides must be an object"}), 400
            # Validate each entry's shape so a typo doesn't silently
            # disable rate limits for the wrong domain
            cleaned = {}
            for dom, limits in v.items():
                if not isinstance(dom, str) or not dom.strip():
                    continue
                if not isinstance(limits, dict):
                    continue
                try:
                    mc = max(0, int(limits.get("max_concurrent", 0)))
                    mps = max(0.0, float(limits.get("max_per_sec", 0.0)))
                    cleaned[dom.strip().lower()] = {
                        "max_concurrent": mc, "max_per_sec": mps,
                    }
                except (TypeError, ValueError):
                    return jsonify({"error":
                        f"invalid limits for domain {dom!r}"}), 400
            _app_cfg["rate_limit_domain_overrides"] = cleaned
            rate_limit_dirty = True
        if rate_limit_dirty:
            # Apply live — the next acquire() call sees the new caps
            try:
                from . import rate_limit as _rl_module
                _rl_module.configure_from_app_config(_app_cfg)
            except Exception as e:
                sys.stderr.write(f"rate_limit reload failed: {e}\n")
        # v3.66.308: generic global_config schema write path. The explicit
        # branches above own keys needing bespoke validation/side-effects; every
        # other GLOBAL_CONFIG_SCHEMA key (queue_hk_*, capture_*, automation
        # flags) is accepted here through ONE validated path so a SPA save
        # actually persists — closing the 306 latent bug where schema keys with
        # no explicit branch were silently dropped (POST 200, nothing written).
        # Type backstop (§3.2): a wrong-typed value is rejected 400, not coerced.
        from .global_config import GLOBAL_CONFIG_SCHEMA as _GCS
        _gc_updates = {}
        for _k, _spec in _GCS.items():
            if _k not in data:
                continue
            _v = data[_k]
            # v3.66.317: auth_token / bd_token are masked on GET (presence-only).
            # Preserve-on-blank — a blank field or the "<configured>" mask sentinel
            # means "leave the stored token unchanged" so a Settings save can't
            # silently clear the credential (or write the mask back as the token).
            # To actually clear a stored token, edit app_config.json / the env.
            if _k in ("auth_token", "bd_token") and (
                    not str(_v).strip() or _v == "<configured>"):
                continue
            _exp = _spec.get("type")
            _bad = (_exp is not None) and (
                not isinstance(_v, _exp)
                or (_exp is int and isinstance(_v, bool)))
            if _bad:
                return jsonify({"error":
                    f"{_k} must be {getattr(_exp, '__name__', _exp)}"}), 400
            _gc_updates[_k] = _v
        # v3.66.709 (A-GUI Cut 1): THE CONTRACT. Until now a key that matched no
        # explicit branch and was absent from GLOBAL_CONFIG_SCHEMA was simply never
        # visited by the loop above -- the POST returned 200 and wrote NOTHING. That
        # silent-drop is why automation.master_off_switch (the emergency stop) sat
        # unwritable while every parity gate read clean: a discarded write reported
        # success, so nothing could detect it. An unrecognised key is now a 400.
        #
        # Declaring the missing keys alone would NOT be enough -- the next undeclared
        # key would recreate the bug just as silently. Fix the contract, not the symptom.
        _known = set(_GCS) | _EXPLICIT_BRANCH_KEYS
        _unknown = sorted(k for k in data if k not in _known)
        if _unknown:
            return jsonify({
                "error": "unknown config key(s): %s" % ", ".join(_unknown),
                "unknown_keys": _unknown,
            }), 400
        if _gc_updates:
            _app_cfg.update(_gc_updates)
        _save_app_config()
    # Inject default for ui_logging_level on read so the frontend
    # doesn't have to handle undefined vs "basic".
    out = dict(_app_cfg)
    out.setdefault("ui_logging_level", "basic")
    out.setdefault("template_auto_detect_mode", "static")
    # v3.43.43: mask the API key in GET response. The actual value
    # stays on disk; the UI only sees whether it's set. Empty/missing
    # → empty; "@cred:..." token → shown as the token (it's a
    # reference, not a secret). Plaintext → "<configured>".
    raw_key = out.get("ai_api_key") or ""
    if raw_key.startswith("@cred:"):
        # @cred refs aren't sensitive on their own (without vault unlocked)
        pass
    elif raw_key:
        out["ai_api_key"] = "<configured>"
    # v3.66.317: never echo the auth tokens to the client — presence-only, like
    # ai_api_key. The raw value stays on disk; the UI shows "<configured>" or "".
    for _sk in ("auth_token", "bd_token"):
        if str(out.get(_sk) or "").strip():
            out[_sk] = "<configured>"
    return jsonify(out)

def register_routes(app) -> int:
    app.register_blueprint(global_config_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("global_config."))

