"""Phase K — held-out designation CLI. HUMAN-ONLY (mirrors the grant CLI). Not autonomous, not
grantable, not wired into the Class-C cycle. It performs the `corpus_writes` action of designating
a held-out capture — which is permanently human — and only after the Phase-K assist verdict passes
(defense in depth: it refuses a candidate the assist REJECTs).

Usage (run from the repo root with PYTHONPATH=.):
  designate <site> <capture_name> --by NAME --reason TEXT
  undesignate <site> <capture_name> --by NAME --reason TEXT
  list [site]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from tools import autonomy_oracle as ao
from tools import autonomy_held_out_assist as ka


def designate(site: str, capture: str, *, by: str, reason: str) -> Dict[str, Any]:
    v = ka.evaluate_candidate(site, capture)
    if not v.get("eligible"):
        return {"ok": False, "error": "candidate not eligible — refusing to designate", "verdict": v}
    prov = ao._provenance()
    s = prov.setdefault(site, {})
    held = s.setdefault("held_out", [])
    if capture not in held:
        held.append(capture)
    s["held_out_designated_at"] = ao._now()
    s.setdefault("designation_log", []).append(
        {"op": "designate", "capture": capture, "by": by, "reason": reason, "at": ao._now()})
    ao._atomic_write(ao._provenance_path(), prov)
    return {"ok": True, "site": site, "capture": capture,
            "tier_now": ao.oracle_verdict(site)["tier"]}


def undesignate(site: str, capture: str, *, by: str, reason: str) -> Dict[str, Any]:
    prov = ao._provenance()
    s = prov.get(site, {})
    held = s.get("held_out", [])
    if capture not in held:
        return {"ok": False, "error": "capture not currently designated held-out"}
    held.remove(capture)
    s["held_out_designated_at"] = ao._now()
    s.setdefault("designation_log", []).append(
        {"op": "undesignate", "capture": capture, "by": by, "reason": reason, "at": ao._now()})
    ao._atomic_write(ao._provenance_path(), prov)
    return {"ok": True, "site": site, "capture": capture,
            "tier_now": ao.oracle_verdict(site)["tier"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autonomy_designate",
                                 description="Human-only held-out evidence designation (Phase K).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("designate", help="designate a capture as held-out for a site")
    pd.add_argument("site")
    pd.add_argument("capture")
    pd.add_argument("--by", required=True)
    pd.add_argument("--reason", required=True)

    pu = sub.add_parser("undesignate", help="remove a held-out designation")
    pu.add_argument("site")
    pu.add_argument("capture")
    pu.add_argument("--by", required=True)
    pu.add_argument("--reason", required=True)

    pl = sub.add_parser("list", help="show the designation assist report")
    pl.add_argument("site", nargs="?", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "designate":
        res = designate(args.site, args.capture, by=args.by, reason=args.reason)
    elif args.cmd == "undesignate":
        res = undesignate(args.site, args.capture, by=args.by, reason=args.reason)
    else:
        res = (ka.site_designation(args.site) if args.site else ka.designation_report())
    print(json.dumps(res, indent=2, default=str))
    return 0 if (args.cmd == "list" or res.get("ok")) else 2


if __name__ == "__main__":
    sys.exit(main())
