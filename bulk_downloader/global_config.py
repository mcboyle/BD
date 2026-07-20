"""Thin getter/setter for the global app config (app_config.json).

This module exists so other subsystems can read/write user-tunable
settings without importing app.py directly (which would create
circular imports — many modules are loaded BY app.py at startup).

The canonical store remains app.py's `_app_cfg` dict + `app_config.json`
on disk. This module:
  • get_config() — returns a copy of the live config dict
  • set_config(new) — merges into the live config + persists to JSON
  • get(key, default) — convenience reader for one key

Concurrency: reads are dict-copies (snapshot). Writes go through a
file lock so concurrent set_config calls don't corrupt the JSON.

This module was added in v3.43.80 to back the references introduced
by Phase 120 (federation), 132 (stream_relay), 138 (shares),
134 (smart_wakeup), and 151 (shortcuts). Before this, those modules
referenced `from .global_config import get_config` which didn't
resolve — a bug found in integration testing.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

# Same file path as app.py uses. Resolved relative to the working
# directory at runtime (BD's launch script cwds into INSTALL_DIR).
_CONFIG_FILE = Path("app_config.json")
_lock = threading.Lock()
_cached: Optional[dict] = None
_cached_mtime: float = 0.0


def _file_mtime() -> float:
    try:
        return _CONFIG_FILE.stat().st_mtime
    except OSError:
        return 0.0


def get_config() -> dict:
    """Return a snapshot of the global config dict. Reads from disk if
    the file has changed; otherwise returns the cached copy.

    Empty dict if the file doesn't exist (first-run state)."""
    global _cached, _cached_mtime
    with _lock:
        mtime = _file_mtime()
        if _cached is not None and mtime == _cached_mtime:
            return dict(_cached)  # caller-safe copy
        if not _CONFIG_FILE.exists():
            _cached = {}
            _cached_mtime = 0.0
            return {}
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            sys.stderr.write(f"[global_config] read failed: {e}\n")
            return dict(_cached) if _cached is not None else {}
        # BP-CFG (v3.66.285): validate on every fresh load. Findings are logged
        # LOUDLY (a silently-disabled feature must not stay silent) and
        # safety-bearing flags with a bad type are fail-CLOSED to their safe
        # default. We still load (fail-open into use) — validation is advisory,
        # never a hard refusal.
        try:
            findings = validate_config(data)
            for f in findings:
                sys.stderr.write(
                    f"[global_config] config warning: key '{f['key']}' "
                    f"({f['kind']}): {f['detail']}\n")
            if findings:
                data = apply_fail_closed(data, findings)
        except Exception as e:  # validation must never break config loading
            sys.stderr.write(f"[global_config] validation skipped: {e}\n")
        _cached = data
        _cached_mtime = mtime
        return dict(data)


def set_config(updates: dict) -> bool:
    """Merge `updates` into the global config and persist. Returns
    True on success."""
    global _cached, _cached_mtime
    if not isinstance(updates, dict):
        return False
    with _lock:
        # Read fresh (someone else may have written since our cache)
        current: dict = {}
        if _CONFIG_FILE.exists():
            try:
                current = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(current, dict):
                    current = {}
            except Exception:
                current = {}
        current.update(updates)
        try:
            # Atomic-ish write: temp file + rename
            tmp = _CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(current, indent=2,
                                      ensure_ascii=False),
                           encoding="utf-8")
            # Restrict the secret-bearing config to owner-only BEFORE the rename
            # (rename preserves the mode onto the final path), so the persisted
            # app_config.json — which may hold tokens/credentials — is never
            # group/world-readable under the process umask (F-COREBD11-01).
            os.chmod(tmp, 0o600)
            tmp.replace(_CONFIG_FILE)
            _cached = current
            _cached_mtime = _file_mtime()
            return True
        except Exception as e:
            sys.stderr.write(f"[global_config] write failed: {e}\n")
            return False


# ─── BP-CFG (v3.66.285): global-config schema validation ─────────────────────
# get()/get_config() do a FLAT lookup. A nested block in app_config.json (e.g.
# {"automation": {"auto_refresh": true}}) means code reading get("auto_refresh")
# silently gets the default — the feature is silently OFF (the 266 footgun). A
# typo'd flat key is the same silent-OFF. This validator makes both loud at load
# and fail-closes safety-bearing flags. It is advisory at load (we fail OPEN into
# use — better to run than refuse) but NEVER silent.
#
# Schema: key -> {"type": <type|tuple>, "safety": bool, "safe_default": <value>}.
# Safety-bearing automation flags fail CLOSED (default OFF / manual) on a bad
# type, matching AUTOMATION_POLICY's manual-by-default posture for gated actions.
GLOBAL_CONFIG_SCHEMA: dict = {
    # v3.66.716: auto_refresh / auto_repair / auto_quarantine / auto_promote were DELETED.
    # They were declared here with safety=True -- so a POST was accepted and PERSISTED --
    # and NOTHING read them. lifecycle_automation.is_enabled() maps the short toggle NAME
    # through AUTOMATION_TOGGLES to the DOTTED key ("automation.auto_refresh_enabled") and
    # _read_toggle reads only that. An operator setting `auto_refresh: true` -- the obvious
    # name, declared, safety-flagged, 200 OK -- got NOTHING.
    #
    # That is exactly the failure the comment above warns about: "the feature is silently
    # OFF (the 266 footgun)". The schema was carrying the footgun it exists to prevent.
    # With the 709 write contract, a POST to them now 400s LOUDLY instead of lying with 200.
    # v3.66.681 (B2/P6): OIDC / SSO login. Managed by the dedicated SSO settings
    # panel (Sso.tsx), like the VPN keys are managed by Vpn.tsx. oidc_client_secret
    # is a secret (redacted on read-back). Non-safety (auth config, reversible).
    "oidc_enabled":       {"type": bool, "safety": False, "safe_default": False},
    "oidc_issuer":        {"type": str,  "safety": False, "safe_default": ""},
    "oidc_client_id":     {"type": str,  "safety": False, "safe_default": ""},
    "oidc_client_secret": {"type": str,  "safety": False, "safe_default": ""},
    "oidc_redirect_uri":  {"type": str,  "safety": False, "safe_default": ""},
    "oidc_scopes":        {"type": str,  "safety": False, "safe_default": "openid email profile"},
    # Phase 4.2a (CLI->GUI parity): queue-housekeeping tunables, promoted from
    # the matching queue-housekeeping env vars so a Settings write takes effect on
    # the next run. Non-safety (operational, reversible via the apply harness); the
    # getters in tools/autonomy_queue_hk.py clamp to sane ranges (>=0 / >=1).
    "queue_hk_gc_age_days": {"type": int,  "safety": False, "safe_default": 7},
    "queue_hk_abandon":     {"type": bool, "safety": False, "safe_default": False},
    "queue_hk_max_retries": {"type": int,  "safety": False, "safe_default": 10},
    "queue_hk_stale_hours": {"type": int,  "safety": False, "safe_default": 24},
    # Phase 4.2 (CLI->GUI parity, v3.66.308): guard-backed Capture tunables,
    # promoted from their env vars so a Settings write takes effect on the next
    # capture (read at call time, store > env seed > default). Two back guard
    # files (capture_bodies.py, tools/capture_session.py) — their GUI controls
    # surface danger_note. capture_raw is the redaction-disable override: it is
    # value-honored, NOT fail-closed-coerced (operator directive) — so safety
    # is False; default OFF (redaction on) preserved by the env/default seed.
    "capture_bodies":      {"type": bool, "safety": False, "safe_default": False},
    # CLI->GUI parity 4.3a: cross-site selector reuse (opt-in). Store > env
    # (BD_CROSS_SITE_SELECTORS) seed > default. Non-safety feature flag.
    "cross_site_selectors": {"type": bool, "safety": False, "safe_default": False},
    # v3.66.336: operator switch for the AI-6 struct_embed tie-breaker (v3.66.321).
    # Opt-in, default OFF -> byte-identical recognizer behaviour. When ON, the
    # WACZ->template build (build_template_from_wacz) passes struct_tiebreak=True to
    # player_recognition.detect(), which may re-rank ONLY a genuine 2-way tie toward
    # the high-confidence structural verdict; it never invents a family, never
    # reaches past the top-2, and never overrides a storage tell. Review-only
    # builder metadata; non-safety. Store > default (no env seed).
    "player_struct_tiebreak": {"type": bool, "safety": False, "safe_default": False},
    # CLI->GUI parity 4.3b: the autonomy final-apply switch (DANGER — arms
    # autonomous Class-B state changes). Tri-state str: ""=default(off via env),
    # "1"=on, "0"=off. Store > env (BD_AUTONOMY_ENABLED) > default OFF.
    "autonomy_enabled": {"type": str, "safety": True, "safe_default": ""},
    "capture_wait_until":  {"type": str,  "safety": False, "safe_default": ""},
    "dom_honeypot_filter": {"type": str,  "safety": False, "safe_default": "off"},
    "redact_dom_urls":     {"type": str,  "safety": False, "safe_default": "keep_structure"},
    "capture_raw":         {"type": bool, "safety": False, "safe_default": False},
    # Phase 4.2 (v3.66.309): slow-query observability tunables, promoted from
    # their env vars (call-time getters in db.py) so a Settings write takes
    # effect on the next DB connection's tracer — no restart.
    "slow_query_log":      {"type": bool, "safety": False, "safe_default": True},
    "slow_query_ms":       {"type": int,  "safety": False, "safe_default": 100},
    # Phase 4.2 (v3.66.312): Browser-backend group. browser_backend covers the
    # three legacy env vars (BD_BROWSER_BACKEND / BD_USE_CLOAK / BD_USE_CLOAKBROWSER)
    # — cloak.resolve_backend() already reads this store key (precedence per-call >
    # env > store > default), so a Settings write is honored when the env is unset;
    # the env remains a deliberate deploy override. novnc_url backs BD_NOVNC_URL
    # (read store > env > default in tools/cockpit_core.py). Both non-safety strings.
    "browser_backend":     {"type": str, "safety": False, "safe_default": ""},
    "novnc_url":           {"type": str, "safety": False, "safe_default": ""},
    # v3.66.506 (Bucket 3a): the third leg of cloak.resolve_backend()'s backend
    # triple, promoted from a display-only alias to a first-class declared key.
    # cloak._CFG_KEYS already reads it (store > env > default) and
    # app_global_config.py already coerces it on write; declaring it here just
    # makes it validate/round-trip like browser_backend. It is a REDUNDANT alias
    # of browser_backend (the canonical control) — a write to either moves the
    # identical resolution — so the GUI labels it as such. Non-safety bool.
    "session_keeper_use_cloakbrowser": {"type": bool, "safety": False, "safe_default": False},
    # Phase 4.2 (v3.66.313): Challenge-handling honeypot tunables (NON-guard subset).
    # honeypot_score_threshold is stored as a STR (read site float()-parses it; matches
    # env semantics, dodges the int-vs-float type_mismatch edge). Both read store > env
    # seed > default at call time (provider_resolve._honeypot_score_threshold /
    # honeypot_threshold.enabled), so a Settings write takes effect on the next candidate
    # resolution / per-site learn. BD_CHALLENGE_WAIT_S is NOT here — it backs the guard
    # file capture_session.py and is deferred to an explicitly-authorized guard cut.
    "honeypot_score_threshold": {"type": str,  "safety": False, "safe_default": ""},
    "honeypot_per_site":        {"type": bool, "safety": False, "safe_default": False},
    # Phase 4.2 (v3.66.314): the deferred Challenge GUARD var. challenge_wait_s
    # backs BD_CHALLENGE_WAIT_S, whose read site lives in the guard file
    # tools/capture_session.py (_challenge_wait_seconds). Promoted in its own
    # explicitly-authorized guard cut. Stored as a STR (read site float()-parses;
    # matches env semantics, dodges the int-vs-float type_mismatch edge). Read
    # store > env seed > default at call time, so a Settings write takes effect
    # on the next capture; default "20" preserved by the env/default seed.
    "challenge_wait_s":         {"type": str,  "safety": False, "safe_default": "20"},
    # v3.66.703 (MOD-4): pin the ffmpeg BUILD BD runs. A directory holding
    # ffmpeg+ffprobe; EMPTY (default) = use PATH, i.e. behaviour unchanged.
    # Exists because presence != capability: the static johnvansickle 7.0.2 build
    # SEGFAULTS on HLS+HTTPS, and healthcheck._ffmpeg_capability could only TELL
    # you that -- it gave no way to point BD at the good build. This is that way.
    "ffmpeg_path":              {"type": str,  "safety": False, "safe_default": ""},
    # v3.66.706 (X-AUTO-1): run the restore REHEARSAL on the daily-digest schedule --
    # prove the backups on disk still RESTORE instead of assuming it. Default OFF
    # (opt-in, same shape as automation.daily_digest_enabled). A FAILED rehearsal
    # breaks the digest's zero-delta silence: a broken backup on a quiet day is
    # exactly what must not be silent.
    "automation.restore_rehearsal_enabled": {"type": bool, "safety": False, "safe_default": False},
    # v3.66.707 (X-AUTO-2): the ceiling on ONE autonomous cycle
    # (automation_controller.run_host_cycle). 0 = uncapped = pre-707 behaviour.
    # cycle_max_errors is the important one: before 707 a FAILING autonomous loop kept
    # executing steps ("a throwing step is isolated + audited, the loop continues") --
    # a broken pipeline burning actions. An operator running L2 autonomy should set it.
    "automation.cycle_max_steps":  {"type": int,   "safety": False, "safe_default": 0},
    "automation.cycle_wall_s":     {"type": int,   "safety": False, "safe_default": 0},
    "automation.cycle_max_errors": {"type": int,   "safety": False, "safe_default": 0},
    # v3.66.708 (A-PIPE / A9): run the checkpointed autonomous chain on a schedule.
    # DEFAULT OFF. Turn this on only AFTER the parts have been exercised: the restore
    # rehearsal (706) proving the backups restore, and the cycle ceiling (707) proving
    # a failing loop halts. That sequence IS the "proven full reversibility" A-PIPE is
    # gated on -- it is not a formality.
    "automation.pipeline_enabled": {"type": bool, "safety": False, "safe_default": False},
    # v3.66.709 (A-GUI Cut 1): the 21 automation keys the runtime has READ since
    # they were introduced but that were never DECLARED here. The generic write path
    # (app_global_config) iterates THIS schema and skips anything absent -- so a
    # POST for an undeclared key returned 200 and wrote nothing. That is the same
    # "306 latent bug" the write path was built to fix, surviving in the one place
    # it matters most: automation.master_off_switch is the EMERGENCY STOP for all
    # autonomous action, and until this cut it could not be turned on.
    #
    # THE KILL SWITCH. safety-bearing, and its fail-closed value is ENGAGED (True):
    # a malformed config must STOP autonomy, never leave it running. This is the one
    # key in the schema whose safe_default is True for that reason.
    "automation.master_off_switch": {"type": bool, "safety": True, "safe_default": True},
    # L1/L2/L3 -- observe-and-flag only (not download-affecting).
    "automation.drift_sweep_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.validation_gate_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.auto_flag_enabled": {"type": bool, "safety": False, "safe_default": False},
    # L4/L5 -- DOWNLOAD-AFFECTING. safety-bearing: a bad type fails CLOSED (disabled).
    "automation.auto_quarantine_enabled": {"type": bool, "safety": True, "safe_default": False},
    "automation.auto_repair_enabled": {"type": bool, "safety": True, "safe_default": False},
    "automation.auto_refresh_enabled": {"type": bool, "safety": True, "safe_default": False},
    "automation.auto_promote_enabled": {"type": bool, "safety": True, "safe_default": False},
    # A9 -- the supervised-autonomy orchestrator. Download-affecting by delegation.
    "automation.controller_enabled": {"type": bool, "safety": True, "safe_default": False},
    # A-DISCO (v3.66.788) -- level-4 enumerate -> triage -> auto-queue. Autonomous
    # network enumeration + capture queueing; safety-bearing so a malformed value
    # fails CLOSED (disabled), never leaving autonomous discovery running.
    "automation.disco_enabled": {"type": bool, "safety": True, "safe_default": False},
    # A4/A6/A7/A8 -- prep/restore/self-management; reversible, not keystone-required.
    "automation.auto_onboard_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.auto_ci_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.auto_recover_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.auto_queue_enabled": {"type": bool, "safety": False, "safe_default": False},
    # auto_refresh trigger-mode selectors + its drift ceiling.
    "automation.auto_refresh_on_capture_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.auto_refresh_confirm_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.auto_refresh_max_drift": {"type": int, "safety": False, "safe_default": 0},
    # Capture-time scrub (redaction). SHIPS ON -- so its fail-closed value is ON:
    # a malformed config must not silently stop redacting captures.
    "automation.scrub_on_capture_enabled": {"type": bool, "safety": True, "safe_default": True},
    "automation.scrub_on_capture_tool": {"type": str, "safety": False, "safe_default": ""},
    # Reporting / canary -- read-only side effects.
    "automation.daily_digest_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.drift_repair_enabled": {"type": bool, "safety": False, "safe_default": False},
    "automation.template_canary_enabled": {"type": bool, "safety": False, "safe_default": False},
    # Phase 4.2 (v3.66.315): Advanced env tranche (non-guard). 15 runtime tunables
    # promoted to full; read store > env seed > default at call time. Numerics are
    # stored as STR ("" = unset -> fall back to env/default; read site casts) to
    # mirror challenge_wait_s/honeypot_score_threshold and dodge type_mismatch.
    # auth_throttle is the enable bool. The redaction greys (redact_emails/
    # redact_extra_headers/redact_network_urls) carry a danger_note: loosening
    # them RETAINS more sensitive data above the unconditional floor.
    "auth_throttle":              {"type": bool, "safety": False, "safe_default": False},
    "auth_throttle_free":         {"type": str,  "safety": False, "safe_default": ""},
    "auth_throttle_base":         {"type": str,  "safety": False, "safe_default": ""},
    "auth_throttle_max":          {"type": str,  "safety": False, "safe_default": ""},
    "redact_emails":              {"type": str,  "safety": False, "safe_default": ""},
    "redact_extra_headers":       {"type": str,  "safety": False, "safe_default": ""},
    "redact_network_urls":        {"type": str,  "safety": False, "safe_default": ""},
    "secrets_audit":              {"type": str,  "safety": False, "safe_default": ""},
    "secrets_audit_sink":         {"type": str,  "safety": False, "safe_default": ""},
    "secrets_audit_file":         {"type": str,  "safety": False, "safe_default": ""},
    "secrets_audit_max_bytes":    {"type": str,  "safety": False, "safe_default": ""},
    "held_out_stale_days":        {"type": str,  "safety": False, "safe_default": ""},
    "lib_reconcile_missing_days": {"type": str,  "safety": False, "safe_default": ""},
    "fleet_nodes":                {"type": str,  "safety": False, "safe_default": ""},
    "youtube_cipher":             {"type": str,  "safety": False, "safe_default": ""},
    # Phase 4.2 (v3.66.316): bucket-3 GUARD vars (single follow-on guard cut).
    # hud_overlay backs BD_HUD_OVERLAY (capture_session guard #3, _hud_enabled) —
    # the global decorative-HUD enable, default ON; the per-capture --no-hud flag
    # still wins. lint_kb_allow backs BD_LINT_KB_ALLOW (build_release guard #7) —
    # a comma-list of KB-lint --allow-missing-ref names, read when the KB-lint gate
    # runs at build time. Both read store > env seed > default; both carry the
    # guard danger_note in the GUI.
    "hud_overlay":                {"type": bool, "safety": False, "safe_default": True},
    "lint_kb_allow":              {"type": str,  "safety": False, "safe_default": ""},
    # Phase 4.2 (v3.66.317): the FINAL env tranche — the 4 operator-deferred vars
    # + the 3 historically-EXCLUDED vars, promoted to full live-writable controls
    # per explicit operator directive (single-operator LAN; risk acknowledged).
    # All read store > env seed > default at CALL TIME (the GUI write wins). Three
    # of the seven had NO functional read site before 317; this cut WIRES them
    # (option 2 — build the gate):
    #   auth_token  -> app._expected_token(): the API bearer. Blank store = unset
    #       (defers to env, then the app_config file) so a blank GUI field can't
    #       lock you out; SETTING it overrides the env token.
    #   bd_token    -> app._accepted_tokens(): a SECOND accepted server-side
    #       bearer / X-BD-Token (BD_TOKEN had no server read before 317).
    #   dev_mode    -> dev_tools.is_dev_mode(): BD_DEV_MODE_DISABLE=1 still hard-
    #       kills (unchanged); else store > env > default-ON. "0"/off disables the
    #       in-GUI dev/test-runner surface — re-gates the v3.47.7 always-on default.
    #   test_mode   -> app.app_test_mode(): advisory flag surfaced in /api/health;
    #       NO security/behavior effect — a live indicator only.
    #   cockpit_shell -> cockpit_shell._shell_pref()/shell_enabled(): the arbitrary-
    #       command PTY enable. store > env > "1", then != "0" and _PTY_OK.
    #   cockpit_tasks / framework_reports -> cockpit_core.tasks_root()/reports_root():
    #       value honored as-written, NO BD_HOME jail (operator directive).
    # auth/shell/path/dev controls carry a danger_note in the GUI; test_mode does not.
    "auth_token":        {"type": str,  "safety": False, "safe_default": ""},
    "bd_token":          {"type": str,  "safety": False, "safe_default": ""},
    "dev_mode":          {"type": str,  "safety": False, "safe_default": ""},
    "test_mode":         {"type": bool, "safety": False, "safe_default": False},
    "cockpit_shell":     {"type": str,  "safety": False, "safe_default": ""},
    "cockpit_tasks":     {"type": str,  "safety": False, "safe_default": ""},
    "framework_reports": {"type": str,  "safety": False, "safe_default": ""},
    # v3.66.503 (Bucket 1): HLS / Live-recorder / Captcha-relay tunables, promoted
    # from import-time module constants to call-time getters (runtime_flags.num,
    # store > env seed > default) so a Settings write takes effect on the next
    # download / poll / launch without a restart. Stored as STR ("" = unset ->
    # fall through to env/default; the getter casts) to mirror challenge_wait_s
    # and dodge the int-vs-float type_mismatch edge. Non-safety operational knobs;
    # no danger_note. live_launch_timeout_s has a getter but no live consumer yet.
    "hls_input_timeout_us":        {"type": str, "safety": False, "safe_default": "10000000"},
    "hls_max_runtime_s":           {"type": str, "safety": False, "safe_default": "3600"},
    "hls_progress_poll_s":         {"type": str, "safety": False, "safe_default": "1.0"},
    "live_poll_interval_s":        {"type": str, "safety": False, "safe_default": "60"},
    "live_disconnect_tolerance_s": {"type": str, "safety": False, "safe_default": "180"},
    "live_max_active_recordings":  {"type": str, "safety": False, "safe_default": "32"},
    "live_launch_timeout_s":       {"type": str, "safety": False, "safe_default": "45"},
    "captcha_pending_timeout_s":   {"type": str, "safety": False, "safe_default": "3600"},
    "captcha_push_dedupe_s":       {"type": str, "safety": False, "safe_default": "300"},
    # MOD-1 A-4: how a captcha solve session presents. "visible" opens the solve
    # browser on the server display (today's fallback); "remote" opens it headless
    # + screencast-enabled so the operator solves it through the cockpit takeover
    # viewer. Non-safety string; default preserves the pre-A-4 behavior exactly.
    "captcha_takeover_mode":       {"type": str, "safety": False, "safe_default": "visible"},
    # MOD-1 A-5a: remote-takeover admission controls. captcha_takeover_enabled is
    # the KILL-SWITCH -- safety-bearing, fail-closed to OFF: remote takeover is
    # disabled unless explicitly enabled, and any config-integrity finding resets
    # it to OFF. captcha_takeover_max_concurrent caps simultaneous remote sessions.
    "captcha_takeover_enabled":    {"type": bool, "safety": True, "safe_default": False},
    "captcha_takeover_max_concurrent": {"type": str, "safety": False, "safe_default": "2"},
    # MOD-1 A-5b: a `solving` takeover session with no operator input for this
    # long is finalized by the sweep (dismissed + ender + channel closed); the
    # SSE viewer stream honors the same bound. Reset on each accepted input.
    "captcha_takeover_idle_timeout_s": {"type": str, "safety": False, "safe_default": "300"},
    # MOD-1 Arch-B (remote_vnc, v3.66.808): the KasmVNC display the takeover
    # browser renders on and the KasmVNC websocket port the probe + default
    # viewer target. Read in takeover_vnc.py via config.get(); declared here so a
    # SPA save persists them (str, int()-coerced at the read site -- mirrors
    # captcha_takeover_max_concurrent). Non-safety; defaults match takeover_vnc's
    # _DEFAULT_DISPLAY / _DEFAULT_WS_PORT so behavior is byte-identical when unset.
    "captcha_vnc_display":         {"type": str, "safety": False, "safe_default": ":5"},
    "captcha_vnc_websocket_port":  {"type": str, "safety": False, "safe_default": "8444"},
}

# Top-level keys that are LEGITIMATELY dicts — not the flat-lookup footgun.
KNOWN_NESTED: tuple = ("tunnels", "global_settings", "automation_policy")


def _edit_distance(a: str, b: str) -> int:
    """Bounded Levenshtein — used only for did-you-mean on near-miss keys."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def validate_config(data: dict, schema: Optional[dict] = None,
                    known_nested=None) -> list:
    """Validate a config dict against `schema`. Returns a list of finding dicts
    ``{key, kind, detail}`` (empty when clean). Pure — logs nothing, mutates
    nothing. Kinds:
      • nested_wont_resolve — a top-level dict (not known-nested) whose sub-keys
        shadow flat schema keys that get() reads (the 266 footgun)
      • type_mismatch — a flat schema key present with the wrong type
      • typo_near_miss — an unknown scalar key within edit-distance 1 of a
        schema key (likely a typo that silently disables the feature)
    """
    if schema is None:
        schema = GLOBAL_CONFIG_SCHEMA
    if known_nested is None:
        known_nested = KNOWN_NESTED
    findings: list = []
    if not isinstance(data, dict):
        return findings
    for key, val in data.items():
        if isinstance(val, dict):
            if key in known_nested:
                continue
            shadowed = [k for k in val.keys() if k in schema]
            if shadowed:
                findings.append({
                    "key": key, "kind": "nested_wont_resolve",
                    "detail": (f"nested block won't resolve under the flat "
                               f"get() lookup; sub-key(s) {sorted(shadowed)} "
                               f"read as top-level flat keys — flatten them"),
                })
            continue
        spec = schema.get(key)
        if spec is not None:
            exp = spec.get("type")
            # bool is an int subclass — a True must not pass an int type check
            bad = (exp is not None) and (
                not isinstance(val, exp)
                or (exp is int and isinstance(val, bool)))
            if bad:
                findings.append({
                    "key": key, "kind": "type_mismatch",
                    "detail": (f"expected {getattr(exp,'__name__',exp)}, "
                               f"got {type(val).__name__}"),
                })
            continue
        # unknown scalar key — flag only a near-miss of a schema key (low noise)
        for cand in schema:
            if _edit_distance(key, cand) <= 1:
                findings.append({
                    "key": key, "kind": "typo_near_miss",
                    "detail": f"unknown key — did you mean '{cand}'?",
                })
                break
    return findings


def apply_fail_closed(data: dict, findings: list,
                      schema: Optional[dict] = None) -> dict:
    """Return a copy of `data` with safety-bearing flags that failed a type
    check reset to their safe default. Non-safety findings are left as-is
    (warned, not coerced). Does not mutate the input."""
    if schema is None:
        schema = GLOBAL_CONFIG_SCHEMA
    out = dict(data)
    for f in findings:
        if f.get("kind") != "type_mismatch":
            continue
        spec = schema.get(f.get("key")) or {}
        if spec.get("safety"):
            out[f["key"]] = spec.get("safe_default")
    return out


def get(key: str, default: Any = None) -> Any:
    """Convenience reader for one key with a default."""
    return get_config().get(key, default)
