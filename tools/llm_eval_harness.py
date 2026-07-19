#!/usr/bin/env python3
"""Phase 9.7 -- local LLM eval harness (CLI).

    python tools/llm_eval_harness.py [--json] [--verbose]

Runs the fixture-driven contract eval (mocked model outputs; no live Ollama
required). Exits 0 when all fixtures pass, 1 otherwise.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import llm_eval  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Local LLM eval harness")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--verbose", action="store_true", help="print every case")
    args = ap.parse_args()

    res = llm_eval.run()

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"LLM eval: {res['passed']}/{res['total']} passed "
              f"({res['failed']} failed) across {len(res['categories'])} categories")
        for c in res["cases"]:
            if args.verbose or not c["ok"]:
                mark = "PASS" if c["ok"] else "FAIL"
                print(f"  [{mark}] {c['name']} -> status={c['status']} via={c['via']}"
                      + (f"  {c['detail']}" if c["detail"] else ""))

    return 0 if res["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
