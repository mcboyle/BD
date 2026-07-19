#!/usr/bin/env python3
"""Cut 7 (7.2) — site onboarding automation, automated UP TO the enable.

Drives the existing detect -> dry-run -> promote_check -> promote sequence for a
new host and STOPS at the enable checkpoint. It stages the host as
``reviewed_not_enabled`` with a plain-English review bundle so the operator's
approval is a quick read — and it NEVER enables a first-time host. Enabling an
unapproved host is a mandatory human approval checkpoint (AUTOMATION_POLICY);
this orchestrator structurally cannot cross it.

Guardrails (policy): no credential/token persistence in templates; site-provided
login flows only; no challenge/anti-bot bypass; any selector/path/API
uncertainty or a failed dry-run/promote_check routes to review. The detection
upstream may be LLM-assisted, but its output is a *candidate*, never a fact.

The orchestrator talks to an injected ``client`` whose methods mirror the
existing endpoints (``detect``, ``dry_run``, ``promote_check``, ``promote``,
``status``), so it is fully testable without network. CLI wiring lives at the
bottom; importing the module has no side effects.
"""
from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional


def _ok(result: Optional[Dict[str, Any]]) -> bool:
    return bool(result) and bool(result.get("ok", False))


def build_summary(host: str, status_obj: Dict[str, Any], *, enabled: bool) -> str:
    """A plain-English description of the staged candidate so the human approval
    is a 20-second read instead of decoding raw JSON. Advisory; approves
    nothing."""
    login = (status_obj or {}).get("login_flow", "unknown")
    captures = (status_obj or {}).get("captures") or []
    cap = ", ".join(str(c) for c in captures) if captures else "none detected"
    state = "ENABLED" if enabled else "staged for review and NOT enabled"
    return (
        f"Host {host}: {state}. "
        f"Login flow: {login}. Captures: {cap}. "
        "Review the selectors and login flow before enabling."
    )


def onboard(host: str, *, client,
            approved_hosts: FrozenSet[str] = frozenset(),
            enable: bool = False) -> Dict[str, Any]:
    """Run the onboarding sequence for ``host`` and stop at the enable gate.

    Returns a dict with ``result`` in {"review", "reviewed_not_enabled",
    "enabled"}, an evidence ``bundle``, a plain-English ``summary``, and
    ``refused_enable`` (True when an enable was requested for a non-approved
    host and therefore declined).
    """
    bundle: Dict[str, Any] = {}

    detect = client.detect(host)
    bundle["detect"] = detect
    if not _ok(detect):
        return {"result": "review", "enabled": False, "bundle": bundle,
                "summary": f"Host {host}: detection inconclusive — routed to review.",
                "refused_enable": False}

    dry = client.dry_run(host)
    bundle["dry_run"] = dry
    if not _ok(dry):
        return {"result": "review", "enabled": False, "bundle": bundle,
                "summary": f"Host {host}: dry-run failed — routed to review.",
                "refused_enable": False}

    check = client.promote_check(host)
    bundle["promote_check"] = check
    if not _ok(check):
        return {"result": "review", "enabled": False, "bundle": bundle,
                "summary": f"Host {host}: promote_check failed — routed to review.",
                "refused_enable": False}

    # The hard stop. An enable is permitted ONLY for an already-approved host;
    # a first-time host is always staged reviewed_not_enabled. We never call
    # promote(enable=True) for a host outside the approved set.
    refused_enable = bool(enable) and host not in approved_hosts
    do_enable = bool(enable) and host in approved_hosts

    promoted = client.promote(host, enable=do_enable)
    bundle["promote"] = promoted

    status_obj = client.status(host)
    bundle["status"] = status_obj

    enabled = bool(promoted.get("enabled"))
    result = "enabled" if enabled else "reviewed_not_enabled"
    return {
        "result": result,
        "enabled": enabled,
        "bundle": bundle,
        "summary": build_summary(host, status_obj, enabled=enabled),
        "refused_enable": refused_enable,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def _build_default_client():  # pragma: no cover - real wiring, exercised on host
    """The production client maps each method onto the existing HTTP endpoints
    (bearer + CSRF prelude). Kept out of the import path so tests stay
    network-free; only constructed when run as a CLI."""
    raise NotImplementedError(
        "wire the HTTP client (bd-curl/session) before running on a live host")


def main(argv=None):  # pragma: no cover - CLI shim
    import argparse
    p = argparse.ArgumentParser(description="Stage a new host up to the enable gate.")
    p.add_argument("host")
    p.add_argument("--enable", action="store_true",
                   help="only honored for an already-approved host")
    p.add_argument("--approved", default="",
                   help="comma-separated approved hosts")
    args = p.parse_args(argv)
    approved = frozenset(h.strip() for h in args.approved.split(",") if h.strip())
    client = _build_default_client()
    out = onboard(args.host, client=client, approved_hosts=approved, enable=args.enable)
    print(out["summary"])
    return 0 if out["result"] != "review" else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
