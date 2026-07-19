#!/usr/bin/env python3
"""Cut 7 (7.3) — OPV evidence automation: auto-bundle + auto-triage.

For each OPV item this stages the evidence and attaches a TRIAGE SIGNAL
(green/amber/red) computed from DETERMINISTIC signals only — never a verdict.

THE INVARIANT: no code path emits an OPV PASS/FAIL sign-off. A verdict needs
real-world ground truth (a real login succeeding, a real device render, a secret
actually stripped) that no model or curl can observe; auto-emitting one would
fabricate verification. The triage colour is a *suggestion to a human*, who
remains the only one who can sign off. The output schema deliberately has no
verdict field.

Tiers (by verifiability):
  * A — CLI / read-only  -> fully auto-bundled + triaged
  * B — noVNC / GUI      -> stage evidence + instruct the human
  * C — phone / device   -> instruct only (nothing to triage here)

The orchestrator talks to an injected ``client`` (``probe`` / ``report``) so it
is testable without network. CLI wiring is at the bottom.
"""
from __future__ import annotations

from typing import Any, Dict

# OPV item -> tier. Tier-A items are the deterministic, curl-assertable checks
# (the ones eventually promotable OUT of OPV into the on-stash suite).
OPV_TIERS: Dict[str, str] = {
    "OPV-BASE": "A", "OPV-F4.3": "A", "OPV-F1.3": "A", "OPV-F1.4": "A",
    "OPV-F3.2": "A", "OPV-F3.3": "A", "OPV-F3.1": "A", "OPV-F2a": "A",
    "OPV-PICK": "B", "OPV-F2.6": "B", "OPV-B2": "B",
    "OPV-F4.1": "C", "OPV-F4.5": "C",
}

_TIER_B_INSTRUCTIONS = (
    "Open the staged page in noVNC and visually confirm the expected element / "
    "draft override; the bundle holds the pre-opened context."
)
_TIER_C_INSTRUCTIONS = (
    "Run this check on a real device (PWA share-target / SSE); it cannot be "
    "observed from the host. Confirm the device behaviour, then sign off."
)


def triage_from_signals(report: Dict[str, Any]) -> str:
    """Map deterministic report signals to a colour. NEVER a verdict.

    Red on a hard failure (non-200, missing redaction marker). Amber on a soft
    signal (drift flag, baseline diff). Green only when every signal is clean.
    """
    status = report.get("status")
    if status is not None and status != 200:
        return "red"
    if report.get("redaction_marker") is False:
        return "red"
    if report.get("drift") or report.get("baseline_diff"):
        return "amber"
    return "green"


def assemble(item_id: str, *, client) -> Dict[str, Any]:
    """Stage the evidence + triage signal for one OPV item.

    Returns ``{item, tier, evidence, triage, instructions}``. There is no
    verdict field, by construction (the 7.3 invariant).
    """
    if item_id not in OPV_TIERS:
        raise KeyError(f"unknown OPV item: {item_id!r}")
    tier = OPV_TIERS[item_id]
    out: Dict[str, Any] = {"item": item_id, "tier": tier,
                           "evidence": {}, "instructions": ""}

    if tier == "A":
        probe = client.probe(item_id)
        report = client.report(item_id)
        out["evidence"] = {"probe": probe, "report": report}
        out["triage"] = triage_from_signals(report)
    elif tier == "B":
        # Stage what we can read; the human does the visual confirm.
        out["evidence"] = {"report": client.report(item_id)}
        out["triage"] = "n/a"
        out["instructions"] = _TIER_B_INSTRUCTIONS
    else:  # C
        out["triage"] = "n/a"
        out["instructions"] = _TIER_C_INSTRUCTIONS
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────
def _build_default_client():  # pragma: no cover - real wiring, exercised on host
    raise NotImplementedError(
        "wire the cockpit report client (bearer + CSRF) before running on host")


def main(argv=None):  # pragma: no cover - CLI shim
    import argparse
    import json
    p = argparse.ArgumentParser(description="Stage + triage OPV evidence (no verdict).")
    p.add_argument("item", nargs="?", help="OPV item id; omit to list tiers")
    args = p.parse_args(argv)
    if not args.item:
        for k, v in OPV_TIERS.items():
            print(f"{k}\t{v}")
        return 0
    client = _build_default_client()
    print(json.dumps(assemble(args.item, client=client), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
