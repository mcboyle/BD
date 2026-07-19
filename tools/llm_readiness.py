#!/usr/bin/env python3
"""Phase 9.6 -- local LLM readiness check (CLI).

    python tools/llm_readiness.py [--json]

Prints a green/amber/red readiness report for the local LLM, using the live AI
config + provider. Exits 0 when green, 1 otherwise. Uses only the shared contract
and registry; sends only fixed benign probe prompts to the model.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import llm_readiness  # noqa: E402

_COLOR = {"green": "GREEN", "amber": "AMBER", "red": "RED"}
_MARK = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL", "skipped": "skip", "info": "info"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Local LLM readiness check")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    rep = llm_readiness.check()

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"LLM readiness: {_COLOR.get(rep['status'], rep['status'].upper())}")
        print(f"  provider={rep['provider']!r} endpoint={rep['endpoint']!r} "
              f"text_model={rep['model_text']!r} latency={rep['latency_ms']}ms")
        for c in rep["checks"]:
            print(f"  [{_MARK.get(c['status'], c['status'])}] {c['name']}: {c['detail']}")
        print(f"  -> {rep['suggested_action']}")

    return 0 if rep["status"] == "green" else 1


if __name__ == "__main__":
    raise SystemExit(main())
