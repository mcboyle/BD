"""Redaction-dial report (X-RED-1) -- a dev-loop gate over the redaction posture.

Reports how the ACTIVE redaction profile (redaction_profile.current_profile, env/
config-driven) deviates from the safe DEFAULTS: which tunable "grey" categories are
dialed down, and whether the profile is `reduced_redaction` (retains more than the
defaults -- captures made under it must NEVER be circulated/fixtured/committed, per
RETENTION/CAPTURE_SHARING policy). The unconditional secret-redaction FLOOR is not a
dial and is never reported as reducible.

Read-only + advisory: it surfaces the delta and, under --check, FAILS (exit 1) when
redaction is reduced below safe defaults -- a standing dev-loop signal that the current
dial would produce non-circulatable captures. It never changes redaction behavior.

Usage:
    python tools/redaction_dial_report.py            # print the dial delta
    python tools/redaction_dial_report.py --check     # exit 1 if reduced below safe
    python tools/redaction_dial_report.py --json
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional


def dial_delta(profile: Dict[str, Any], defaults: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-category delta between an active profile and the safe defaults. Only
    categories that differ are returned. Pure."""
    out: List[Dict[str, Any]] = []
    for key in sorted(defaults):
        active = profile.get(key)
        default = defaults.get(key)
        if active != default:
            out.append({"category": key, "active": active, "default": default})
    return out


def build_report(profile: Optional[Dict[str, Any]] = None,
                 defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the live profile + defaults from redaction_profile and report the
    dial delta + the reduced-redaction verdict."""
    from bulk_downloader import redaction_profile as rp
    prof = profile if profile is not None else rp.current_profile()
    defs = defaults if defaults is not None else dict(rp._DEFAULTS)
    reduced = rp.reduced_redaction(prof)
    return {
        "profile": prof,
        "defaults": defs,
        "deltas": dial_delta(prof, defs),
        "reduced_redaction": bool(reduced),
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    rep = build_report()
    if "--json" in argv:
        print(json.dumps(rep, indent=2, sort_keys=True, default=str))
    else:
        verdict = ("REDUCED below safe defaults -- captures are LOCAL-ONLY, "
                   "never circulate") if rep["reduced_redaction"] else "at safe defaults"
        print(f"redaction dial: {verdict}")
        for d in rep["deltas"]:
            print(f"  {d['category']}: active={d['active']!r} default={d['default']!r}")
        if not rep["deltas"]:
            print("  (no dial deviates from defaults)")
    if "--check" in argv and rep["reduced_redaction"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
