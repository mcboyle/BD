#!/usr/bin/env python3
"""Convert a rich WACZ-builder draft into a runtime reviewed-shape review
candidate. Never enables anything. Pipeline:

  build_template_from_wacz.py  (rich draft)
    -> normalize_template_draft.py  (runtime-shape review candidate)  <-- here
    -> lint/safety review
    -> promote_template.py  (manual promote to reviewed/)
    -> runtime consumes reviewed template
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bulk_downloader.template_normalize import normalize_draft


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Normalize a rich WACZ-builder draft into a runtime "
                    "reviewed-shape review candidate (never enabled).")
    ap.add_argument("draft", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    draft = json.loads(args.draft.read_text(encoding="utf-8"))
    cand = normalize_draft(draft)

    out = args.out
    if out is None:
        host = (cand.get("host") or "capture").replace(":", "_").replace("/", "_")
        out = Path("templates/review_candidates") / f"{host}.candidate.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cand, indent=2, sort_keys=False), encoding="utf-8")

    print(f"wrote {out}")
    print(f"status: {cand['status']}")
    print(f"host: {cand['host']}")
    print("selectors:", ", ".join(cand.get("selectors", {}).keys()) or "none")
    print(f"network_patterns: {len(cand['network_patterns'])} "
          f"(rejected {len(cand['rejected_patterns'])})")
    print(f"resolutions: {cand['resolutions']}")
    print(f"warnings: {len(cand['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
