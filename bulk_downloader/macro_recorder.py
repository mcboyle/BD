"""Macro recorder — storage layer (Phase 186, Block C, partial).

The full design: record Playwright actions (click, scroll, wait, type)
and replay them deterministically. Use cases: multi-step login flows,
age-gate clickthroughs, "click play then click download".

This module is the **storage primitive** only. It stores and retrieves
named action sequences as JSON. The actual record-from-browser and
replay-into-browser parts are gated behind operator config and need
careful integration with `learn.py`'s sync_playwright context (which
is why they're deferred — touching the same Playwright session as the
main worker is fraught).

Schema (file-backed under ~/BulkDownloader/macros/):
  {site_id}_{name}.json  →  {
    site_id, name, created_at, updated_at,
    actions: [
      {kind: 'click', selector: '...', timeout_ms: 5000},
      {kind: 'wait', for: '...', timeout_ms: 10000},
      {kind: 'scroll', to: 'bottom'},
      {kind: 'type', selector: '...', text: '...'},
      {kind: 'sleep', ms: 1000},
    ],
    metadata: {description, tags, last_replay_ok, last_replay_ts}
  }

Public surface:
  • record_macro(site_id, name, actions, *, description)
  • get_macro(site_id, name)
  • list_macros(*, site_id=None)
  • delete_macro(site_id, name)
  • mark_replay_result(site_id, name, ok, message='')

The replay-integration layer (not in this module): runner consults
the per-site `pre_download_macro` config field; if set, looks up
that macro and replays its actions on each fresh page context before
the normal teach/extract path.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_VALID_KINDS = {"click", "wait", "scroll", "type", "sleep", "press", "await_url"}

# NEW-1: a recorded "type" action that targets a password field stores
# this sentinel as its `text` instead of the real password. At replay,
# the password is resolved from the vault (secrets_store) keyed by the
# site — so the macro file NEVER contains a plaintext password. This is
# the same literal macro_replay.py documented; both modules import it
# from here so the marker has a single source of truth.
VAULT_MARKER = "(set in vault)"

# Tight password-field detection: only true password inputs, so we never
# false-positive and resolve a vault password into a non-password field
# (which would itself leak the credential). The recorder's own selector
# synthesis emits input[type='password'] for password inputs, so this
# catches real recorder output; non-standard builders should additionally
# set an explicit "secret": true flag on the action.
_PW_SELECTOR_RE = re.compile(
    r"""type\s*=\s*['"]?password"""            # input[type='password']
    r"""|autocomplete\s*=\s*['"]?(?:current-|new-)?password""",  # autocomplete pw
    re.IGNORECASE,
)


def _looks_like_password_field(action: dict) -> bool:
    """True if a 'type' action targets a password field. Keyed on an
    explicit ``secret`` flag (authoritative, set by an updated recorder)
    or a precise password-selector match (catches real recorder output)."""
    if not isinstance(action, dict) or action.get("kind") != "type":
        return False
    if action.get("secret"):
        return True
    return bool(_PW_SELECTOR_RE.search(action.get("selector") or ""))


def _scrub_secret_actions(actions: list) -> tuple[list, int]:
    """Return (new_actions, n_scrubbed). Any 'type' action that targets a
    password field has its real ``text`` replaced with VAULT_MARKER and is
    flagged ``secret: True`` — so a plaintext password is never persisted.
    Idempotent: an action already carrying the marker is normalised (flag
    set) but not counted as a fresh scrub. Never mutates the input."""
    out = []
    n = 0
    for a in actions:
        if isinstance(a, dict) and _looks_like_password_field(a):
            had_real = a.get("text") not in (VAULT_MARKER, None, "")
            copy = dict(a)
            copy["text"] = VAULT_MARKER
            copy["secret"] = True
            out.append(copy)
            if had_real:
                n += 1
        else:
            out.append(a)
    return out, n


def _resolve_secret_for(site_id: Optional[str]) -> Optional[str]:
    """Resolve the site's password from the vault. Returns None if there
    is no site_id or no stored password (caller then fails loudly rather
    than typing a bogus value). Fail-soft on any backend error."""
    if not site_id:
        return None
    try:
        from . import secrets_store as _ss
        return _ss.resolve_password(_ss.make_password_reference(site_id))
    except Exception:
        return None


def _substitute_secret(action: dict, site_id: Optional[str]) -> dict:
    """If `action` is a password 'type' action (marker or secret flag),
    return a COPY with `text` set to the real vault password. Otherwise
    return the action unchanged. Raises ValueError (with no secret in the
    message) if a password marker is present but the vault has none."""
    if not isinstance(action, dict) or action.get("kind") != "type":
        return action
    if not (action.get("secret") or action.get("text") == VAULT_MARKER):
        return action
    secret = _resolve_secret_for(site_id)
    if not secret:
        raise ValueError(
            "password marker present but no vault password for site "
            f"{site_id!r}")
    return {**action, "text": secret}


def _macro_dir() -> Path:
    """Path to the macros storage directory.
    ~/BulkDownloader/macros (cross-platform via env var)."""
    base = os.environ.get("BD_INSTALL_DIR") or os.path.expanduser(
        "~/BulkDownloader")
    path = Path(base) / "macros"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_name(name: str) -> str:
    n = (name or "").strip().lower().replace(" ", "-")
    if not _NAME_PATTERN.match(n):
        return ""
    return n


def _path_for(site_id: str, name: str) -> Optional[Path]:
    sid = (site_id or "").strip().lower()
    n = _normalize_name(name)
    if not sid or not n:
        return None
    return _macro_dir() / f"{sid}_{n}.json"


def _validate_actions(actions: list) -> tuple[bool, str]:
    """Light schema validation. Returns (ok, error_msg)."""
    if not isinstance(actions, list):
        return False, "actions must be a list"
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            return False, f"actions[{i}] not a dict"
        kind = a.get("kind")
        if kind not in _VALID_KINDS:
            return False, (f"actions[{i}].kind '{kind}' "
                           f"not in {sorted(_VALID_KINDS)}")
    return True, ""


def record_macro(site_id: str, name: str, actions: list, *,
                description: str = "",
                tags: Optional[list] = None) -> dict:
    """Store a macro. Overwrites any existing macro with the same
    name. Returns {ok, path, error?}."""
    path = _path_for(site_id, name)
    if not path:
        return {"ok": False,
                "error": "invalid site_id or name (lowercase, "
                         "letters/digits/_-, max 64 chars)"}
    ok, err = _validate_actions(actions)
    if not ok:
        return {"ok": False, "error": err}
    # NEW-1: never persist a plaintext password — replace password-field
    # text with the vault marker before writing. Replay resolves the real
    # value from secrets_store keyed by site_id.
    actions, _ = _scrub_secret_actions(actions)
    now = time.time()
    existing = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
    bundle = {
        "site_id": site_id, "name": _normalize_name(name),
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
        "actions": actions,
        "metadata": {
            "description": description,
            "tags": tags or [],
            "last_replay_ok": existing.get("metadata", {}).get(
                "last_replay_ok"),
            "last_replay_ts": existing.get("metadata", {}).get(
                "last_replay_ts"),
            "last_replay_message": existing.get("metadata", {}).get(
                "last_replay_message", ""),
        },
    }
    try:
        # v3.47.8 (#43): atomic write — tmp-then-replace so crash mid-write
        # leaves the previous bundle intact instead of a truncated file.
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        tmp.replace(path)
        return {"ok": True, "path": str(path)}
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}"}


def get_macro(site_id: str, name: str) -> Optional[dict]:
    """Return the stored macro bundle, or None if not found."""
    path = _path_for(site_id, name)
    if not path or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_macros(*, site_id: Optional[str] = None) -> list:
    """List all stored macros. Filter by site_id if given.
    Returns sorted list of macro bundles (without `actions` for size)."""
    out = []
    try:
        for p in _macro_dir().glob("*.json"):
            try:
                bundle = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if site_id and bundle.get("site_id") != site_id:
                continue
            # Trim `actions` to a count to keep list responses small
            summary = {k: v for k, v in bundle.items() if k != "actions"}
            summary["action_count"] = len(bundle.get("actions") or [])
            out.append(summary)
    except Exception:
        pass
    out.sort(key=lambda b: (b.get("site_id", ""), b.get("name", "")))
    return out


def delete_macro(site_id: str, name: str) -> bool:
    """Delete a macro. Returns True if it existed and was removed."""
    path = _path_for(site_id, name)
    if not path or not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def mark_replay_result(site_id: str, name: str,
                       ok: bool, message: str = "") -> bool:
    """Update the metadata block with the latest replay outcome.
    Called by the replay engine (not part of this storage module's
    direct surface). Returns True on success."""
    path = _path_for(site_id, name)
    if not path or not path.is_file():
        return False
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        meta = bundle.setdefault("metadata", {})
        meta["last_replay_ok"] = bool(ok)
        meta["last_replay_ts"] = time.time()
        meta["last_replay_message"] = (message or "")[:200]
        # v3.47.8 (#43): atomic write
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except (json.JSONDecodeError, OSError):
        return False


# ─── v3.46.0 Phase 186 part 2: replay engine ──────────────────────────
#
# Takes a stored macro + a Playwright `page` object and executes each
# action in order. Surfaces per-step failures to the caller and updates
# `metadata.last_replay_*` via mark_replay_result().
#
# This module imports nothing from playwright; it expects the caller
# to pass in a page that quacks like a Playwright Page (locator, wait_for,
# evaluate, keyboard, etc). That keeps macro_recorder.py importable in
# every BD context (tests, CLI tools, lightweight handlers) regardless
# of whether Playwright is installed.
#
# Action shapes:
#   click   {selector, timeout_ms?: 5000, force?: false}
#   wait    {for: <selector>, timeout_ms?: 10000, state?: 'visible'}
#   scroll  {to: 'bottom'|'top'|<px-int>}
#   type    {selector, text, timeout_ms?: 5000, clear?: false}
#   sleep   {ms: <int>}
#   press   {keys: 'Control+a'|<single key>, selector?: <focus target>}

DEFAULT_TIMEOUT_MS = 5000


def replay_macro(page, macro: dict, *,
                site_id: Optional[str] = None,
                name: Optional[str] = None,
                strict: bool = False) -> dict:
    """Replay every action in the macro against `page`. Returns a
    summary dict:

        {
          ok: bool,
          executed: int,        # how many actions ran successfully
          failed_at: int|None,  # index of first failed action, if any
          error: str,           # short message if failed_at is not None
          steps: [              # per-action result
            {idx, kind, ok, took_ms, error?},
            ...
          ],
        }

    If `strict=True`, the replay stops on the first failure. Otherwise
    it logs the failure and continues — useful for "best-effort"
    pre-download macros where one missing element shouldn't block the
    rest. Default is False (continue on failure).

    Caller is responsible for managing the page lifecycle (open before,
    close after); replay does NOT navigate or close.

    Updates `metadata.last_replay_*` if `site_id` and `name` are given.
    """
    if not isinstance(macro, dict):
        return {"ok": False, "executed": 0, "failed_at": -1,
                "error": "macro must be a dict",
                "steps": []}
    actions = macro.get("actions") or []
    if not isinstance(actions, list):
        return {"ok": False, "executed": 0, "failed_at": -1,
                "error": "macro.actions must be a list",
                "steps": []}

    results = []
    executed = 0
    failed_at = None
    err_msg = ""

    for idx, action in enumerate(actions):
        step_start = time.time()
        kind = (action or {}).get("kind", "")
        step_result = {"idx": idx, "kind": kind, "ok": False,
                       "took_ms": 0}
        try:
            # NEW-1: resolve a password marker to the real vault value at
            # the last moment, on a copy — the stored macro never holds it.
            action = _substitute_secret(action, site_id)
            _execute_action(page, action)
            step_result["ok"] = True
            executed += 1
        except Exception as e:
            step_result["error"] = (f"{type(e).__name__}: {e}")[:200]
            if failed_at is None:
                failed_at = idx
                err_msg = step_result["error"]
            if strict:
                step_result["took_ms"] = int(
                    (time.time() - step_start) * 1000)
                results.append(step_result)
                break
        step_result["took_ms"] = int((time.time() - step_start) * 1000)
        results.append(step_result)

    ok = (failed_at is None)
    summary = {
        "ok": ok, "executed": executed,
        "failed_at": failed_at,
        "error": err_msg,
        "steps": results,
    }

    # Persist replay outcome to the macro's metadata block
    if site_id and name:
        try:
            mark_replay_result(
                site_id, name, ok,
                message=err_msg or f"{executed}/{len(actions)} steps ok")
        except Exception:
            pass

    return summary


def _execute_action(page, action: dict):
    """Dispatch one action against the page. Raises on failure with
    a descriptive message. Pure dispatch — no result tracking."""
    if not isinstance(action, dict):
        raise ValueError(f"action must be a dict, got {type(action).__name__}")
    kind = action.get("kind", "")
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown action kind {kind!r}; "
                         f"valid: {sorted(_VALID_KINDS)}")

    if kind == "click":
        sel = action.get("selector", "")
        if not sel:
            raise ValueError("click action requires 'selector'")
        timeout = int(action.get("timeout_ms", DEFAULT_TIMEOUT_MS))
        force = bool(action.get("force", False))
        page.locator(sel).first.click(timeout=timeout, force=force)

    elif kind == "wait":
        sel = action.get("for", "")
        if not sel:
            raise ValueError("wait action requires 'for' (selector)")
        timeout = int(action.get("timeout_ms", DEFAULT_TIMEOUT_MS * 2))
        state = action.get("state", "visible")
        page.locator(sel).first.wait_for(state=state, timeout=timeout)

    elif kind == "scroll":
        to = action.get("to", "bottom")
        if to == "bottom":
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        elif to == "top":
            page.evaluate("() => window.scrollTo(0, 0)")
        elif isinstance(to, (int, float)):
            page.evaluate(f"() => window.scrollTo(0, {int(to)})")
        else:
            raise ValueError(f"scroll 'to' must be 'top'/'bottom'/int, got {to!r}")

    elif kind == "type":
        sel = action.get("selector", "")
        if not sel:
            raise ValueError("type action requires 'selector'")
        text = action.get("text", "")
        timeout = int(action.get("timeout_ms", DEFAULT_TIMEOUT_MS))
        locator = page.locator(sel).first
        if action.get("clear"):
            locator.fill("", timeout=timeout)
        # `fill` is fast but doesn't simulate key events. `type` is
        # slower but more realistic — pick based on action.method.
        # Default: fill (faster, more reliable for plain text inputs).
        if action.get("method") == "type":
            locator.type(text, timeout=timeout,
                         delay=int(action.get("delay_ms", 30)))
        else:
            locator.fill(text, timeout=timeout)

    elif kind == "sleep":
        ms = int(action.get("ms", 1000))
        # Cap absurd values to prevent macros from hanging
        ms = max(0, min(ms, 60_000))
        time.sleep(ms / 1000.0)

    elif kind == "press":
        keys = action.get("keys", "")
        if not keys:
            raise ValueError("press action requires 'keys'")
        sel = action.get("selector", "")
        if sel:
            # Focus the target first
            page.locator(sel).first.focus(
                timeout=int(action.get("timeout_ms",
                                       DEFAULT_TIMEOUT_MS)))
        page.keyboard.press(keys)

    elif kind == "await_url":
        # Navigation/origin-aware step: wait for the page URL to match a glob or
        # substring before the next step runs. This is what lets an N-step login
        # flow express the cross-origin hop between steps (enter username → Next
        # → await the IdP origin → enter password → submit). The url shape is a
        # structural origin/path pattern, never a credential. wait_until="load"
        # mirrors do_login's page-load semantics so a slow SSO redirect settles
        # before the next field is typed.
        url = action.get("url", "")
        if not url:
            raise ValueError("await_url action requires 'url'")
        timeout = int(action.get("timeout_ms", DEFAULT_TIMEOUT_MS * 2))
        try:
            page.wait_for_url(url, timeout=timeout,
                              wait_until=action.get("wait_until", "load"))
        except TypeError:
            # A page double / older Playwright signature without wait_until.
            page.wait_for_url(url, timeout=timeout)


def validate_macro(macro: dict) -> tuple[bool, str]:
    """Static validation: check structure + each action's required
    fields. Doesn't execute. Useful before storing a hand-authored
    macro to fail fast on typos."""
    if not isinstance(macro, dict):
        return False, "macro must be a dict"
    actions = macro.get("actions")
    if not isinstance(actions, list):
        return False, "macro.actions must be a list"
    for i, a in enumerate(actions):
        if not isinstance(a, dict):
            return False, f"actions[{i}] not a dict"
        kind = a.get("kind", "")
        if kind not in _VALID_KINDS:
            return False, (f"actions[{i}].kind={kind!r} "
                           f"not in {sorted(_VALID_KINDS)}")
        # Per-kind requirements
        if kind == "click" and not a.get("selector"):
            return False, f"actions[{i}] (click) missing selector"
        if kind == "wait" and not a.get("for"):
            return False, f"actions[{i}] (wait) missing 'for'"
        if kind == "type" and not a.get("selector"):
            return False, f"actions[{i}] (type) missing selector"
        if kind == "press" and not a.get("keys"):
            return False, f"actions[{i}] (press) missing keys"
        if kind == "await_url" and not a.get("url"):
            return False, f"actions[{i}] (await_url) missing 'url'"
        if kind == "scroll":
            to = a.get("to", "bottom")
            if to not in ("top", "bottom") and not isinstance(to, (int, float)):
                return False, (f"actions[{i}] (scroll) 'to' must be "
                               f"'top'/'bottom'/int")
    return True, ""


def scrub_stored_passwords(site_id: Optional[str] = None) -> dict:
    """Migration (NEW-1): rewrite any macro already on disk that stored a
    plaintext password so the password field carries VAULT_MARKER instead.
    Optionally limited to one site. Real passwords are resolved from the
    vault at replay, so removing the plaintext is non-destructive.

    Returns {scanned, modified, files: [paths]}. Run once after upgrading.
    """
    scanned = modified = 0
    files: list[str] = []
    try:
        macro_dir = _macro_dir()
    except OSError:
        return {"scanned": 0, "modified": 0, "files": []}
    for path in sorted(macro_dir.glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if site_id and bundle.get("site_id") != site_id:
            continue
        scanned += 1
        actions = bundle.get("actions") or []
        new_actions, n = _scrub_secret_actions(actions)
        if n == 0 and new_actions == actions:
            continue
        bundle["actions"] = new_actions
        bundle["updated_at"] = time.time()
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
            tmp.replace(path)
            modified += 1
            files.append(str(path))
        except OSError:
            continue
    return {"scanned": scanned, "modified": modified, "files": files}
