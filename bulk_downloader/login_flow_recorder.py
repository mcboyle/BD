"""Login-flow recorder — T45 / D-27.

A thin registry layer over the existing macro_recorder + learn.py
infrastructure. The browser-side recorder (learn.py RECORDER_JS) and
the macro storage (macro_recorder.py) already exist; this module ties
them together at the operator-facing layer:

  • record_login_flow(site_id, name, actions, ...) — save a captured
    action sequence as a named login flow under the macro store, with
    a "login_flow" tag.

  • list_login_flows(site_id=None) — read-only list of every saved
    login flow, optionally filtered by site.

  • get_login_flow(site_id, name) — fetch one flow's bundle.

  • delete_login_flow(site_id, name) — remove one flow.

  • mark_replay_result(site_id, name, ok, message) — record the
    last replay outcome (delegates to macro_recorder).

Why this exists at all (given macro_recorder.record_macro is already
public). Login flows are a specific KIND of macro: the operator
wants to know "which macros are login flows" without searching by
tag in every read site. This module is the canonical surface for
that question. It also means the browser-recording UI can save
under a known-stable tag without each call site having to remember
to add it.

This module does NO module-level work at import — no DB queries, no
threads. macro_recorder honours the same constraint.
"""
from __future__ import annotations

from typing import Optional


_LOGIN_FLOW_TAG = "login_flow"


def record_login_flow(site_id: str, name: str, actions: list, *,
                       description: str = "") -> dict:
    """Save a captured login-flow action sequence. Returns
    {ok, path?, error?}.

    Delegates to macro_recorder.record_macro under the hood, adding
    the canonical "login_flow" tag so list_login_flows can find it.
    """
    try:
        from . import macro_recorder as _mr
    except Exception as e:
        return {"ok": False,
                "error": f"macro_recorder import failed: {e}"}
    return _mr.record_macro(
        site_id, name, actions,
        description=description,
        tags=[_LOGIN_FLOW_TAG])


def get_login_flow(site_id: str, name: str) -> Optional[dict]:
    """Fetch one saved login flow. Returns the macro bundle (with
    actions[], metadata{}, etc.) or None if not found OR if the
    stored macro doesn't carry the login_flow tag."""
    try:
        from . import macro_recorder as _mr
    except Exception:
        return None
    bundle = _mr.get_macro(site_id, name)
    if not bundle:
        return None
    tags = (bundle.get("metadata") or {}).get("tags") or []
    if _LOGIN_FLOW_TAG not in tags:
        # Macro exists but isn't a login flow — don't return it under
        # this surface
        return None
    return bundle


def list_login_flows(*,
                      site_id: Optional[str] = None) -> list:
    """All saved login flows. Returns a list of bundles (same shape
    as macro_recorder.list_macros) filtered to those carrying the
    login_flow tag."""
    try:
        from . import macro_recorder as _mr
    except Exception:
        return []
    all_macros = _mr.list_macros(site_id=site_id) or []
    out = []
    for m in all_macros:
        tags = (m.get("metadata") or {}).get("tags") or []
        if _LOGIN_FLOW_TAG in tags:
            out.append(m)
    return out


def delete_login_flow(site_id: str, name: str) -> dict:
    """Remove a saved login flow. Refuses to delete if the macro
    exists but doesn't carry the login_flow tag — preventing an
    accidental delete of an unrelated macro that happens to share
    the name."""
    try:
        from . import macro_recorder as _mr
    except Exception as e:
        return {"ok": False,
                "error": f"macro_recorder import failed: {e}"}
    bundle = _mr.get_macro(site_id, name)
    if bundle is None:
        return {"ok": True, "removed": False,
                "note": "macro not found"}
    tags = (bundle.get("metadata") or {}).get("tags") or []
    if _LOGIN_FLOW_TAG not in tags:
        return {"ok": False,
                "error": ("macro exists but is not a login flow "
                          "(missing login_flow tag); refusing to "
                          "delete via the login-flow surface")}
    ok = _mr.delete_macro(site_id, name)
    return {"ok": bool(ok), "removed": bool(ok)}


def mark_replay_result(site_id: str, name: str, ok: bool,
                         message: str = "") -> bool:
    """Record the last replay outcome for a login flow. Delegates
    to macro_recorder.mark_replay_result after verifying the macro
    is in fact a login flow."""
    bundle = get_login_flow(site_id, name)
    if bundle is None:
        return False
    try:
        from . import macro_recorder as _mr
    except Exception:
        return False
    try:
        _mr.mark_replay_result(site_id, name, ok=ok, message=message)
        return True
    except Exception:
        return False


def login_flow_tag() -> str:
    """Public accessor for the canonical tag — used by tests and by
    the dev_suite inspector to keep the tag string in one place."""
    return _LOGIN_FLOW_TAG


# ── cross-origin N-step login derivation + replay plan (v3.66.302) ─────────
from urllib.parse import urlsplit  # noqa: E402


def _origin(url):
    """scheme://host of a URL, or '' — the structural origin (no path/query)."""
    if not isinstance(url, str) or not url:
        return ""
    p = urlsplit(url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


def _nav_origins(network_log):
    """Ordered (ts, origin) of navigation/document responses in the network
    log — used to locate cross-origin hops when the picks don't carry a
    page_url (e.g. captures that predate per-pick origin stamping)."""
    out = []
    for e in network_log or []:
        if not isinstance(e, dict):
            continue
        rt = str(e.get("resource_type") or e.get("resourceType")
                 or e.get("type") or "").lower()
        ct = str(e.get("content_type") or e.get("contentType") or "").lower()
        is_doc = rt in ("document", "navigation") or ct.startswith("text/html")
        org = _origin(e.get("url"))
        ts = e.get("timestamp")
        if is_doc and org and isinstance(ts, int):
            out.append((ts, org))
    out.sort(key=lambda x: x[0])
    return out


def _origin_for(entry, nav_origins):
    """Best-effort origin for one timeline entry: its own page_url when the
    capture stamped one, else the most-recent navigation origin at/before the
    click ts."""
    org = _origin(entry.get("page_url"))
    if org:
        return org
    ts = entry.get("ts")
    if isinstance(ts, int):
        cur = ""
        for nts, norg in nav_origins:
            if nts <= ts:
                cur = norg
            else:
                break
        return cur
    return ""


def derive_login_flow(action_timeline, *, network_log=None, name="login"):
    """Turn an operator-recorded ``action_timeline`` into a general N-step
    login flow (a macro_recorder action list). Structure-only; F2.

    Rules:
      * a credential/email/username field → ``type`` (text filled at plan time
        from the configured username);
      * a password field → ``type`` routed through the vault marker
        (``secret: True``) so the flow file never holds a plaintext password;
      * a submit/next control → ``click``;
      * when the origin changes between consecutive steps (a cross-origin hop —
        e.g. to an SSO/IdP), an ``await_url`` is inserted before the first step
        of the new origin so replay waits for that origin to load. The origin is
        read from each pick's ``page_url`` when present, else inferred from the
        network log's navigation responses.

    NOTE on 2FA/OTP: a non-password text field on a later step (e.g. an OTP) is
    emitted as a ``type`` with empty text — it cannot be replayed unattended and
    is left for manual challenge handoff. Returns a macro bundle dict.
    """
    try:
        from .macro_recorder import VAULT_MARKER as _VM
    except Exception:
        _VM = "(set in vault)"
    entries = [e for e in (action_timeline or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: e.get("ts") if isinstance(e.get("ts"), int) else 0)
    navs = _nav_origins(network_log)

    actions = []
    prev_origin = None
    cred_filled = False  # only the FIRST credential field is the username slot
    for e in entries:
        sel = e.get("selector")
        if not isinstance(sel, str) or not sel:
            continue
        org = _origin_for(e, navs)
        if prev_origin and org and org != prev_origin:
            actions.append({"kind": "await_url", "url": org + "/*"})
        role = str(e.get("role") or "").lower()
        if "password" in role:
            actions.append({"kind": "type", "selector": sel,
                            "text": _VM, "secret": True})
        elif "submit" in role or "next" in role:
            actions.append({"kind": "click", "selector": sel})
        elif ("username" in role or "email" in role or "user" in role
              or "login" in role):
            actions.append({"kind": "type", "selector": sel,
                            "text": "", "credential": not cred_filled})
            cred_filled = True
        if org:
            prev_origin = org

    return {
        "actions": actions,
        "metadata": {"tags": [_LOGIN_FLOW_TAG], "name": name,
                     "derived": True, "steps": len(actions),
                     "origins": sorted({a["url"][:-2] for a in actions
                                        if a["kind"] == "await_url"})},
    }


def plan_login_flow(flow, *, username=""):
    """The ordered action list ``do_login`` would drive for a site that has a
    saved login flow. Fills the credential field with the configured
    ``username``; the password action keeps the vault marker (resolved at the
    last moment inside replay, never in the plan). Returns a NEW list — the
    stored flow is not mutated.

    Live browser drive happens in ``login.replay_saved_login_flow``; this pure
    planner is what the offline tests exercise.
    """
    if isinstance(flow, dict):
        acts = flow.get("actions") or []
    elif isinstance(flow, list):
        acts = flow
    else:
        acts = []
    plan = []
    cred_done = False
    for a in acts:
        if not isinstance(a, dict):
            continue
        b = dict(a)
        if b.get("kind") == "type" and not b.get("secret"):
            if b.get("credential") or (not cred_done):
                b["text"] = username
                cred_done = True
        plan.append(b)
    return plan
