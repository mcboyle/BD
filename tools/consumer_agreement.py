#!/usr/bin/env python3
"""consumer_agreement -- the tractable differential oracle.

The general differential_oracle is hard. This is the buildable subset that targets
the exact bug-class the verify pass AND this pilot hit: "two things that should
agree didn't." The SSRF finding was two siblings consuming rec.url that disagreed
on validation, because not every PRODUCER of rec.url enforced the shape invariant
the CONSUMERS rely on.

A CONTRACT declares, for a shared symbol X:
  - producers[]          functions that SET X and MUST apply the guard
  - consumers_relying[]  functions that USE X and are ALLOWED to skip the guard
                         BECAUSE they rely on the producers having applied it
  - guard_signature      a regex the guard leaves in a producer's source

GATE: every producer's source must contain the guard. The day someone adds a 3rd
producer that sets X without the guard, the consumers' assumption silently breaks
-- and this gate catches it. (It also records that consumers_relying SKIP the guard
by design, so a future reader knows the asymmetry is intentional, not a miss.)

Usage:  consumer_agreement.py [--contracts CONTRACTS.json] [--gate]
AST-based source extraction; regex guard match. Stdlib only.
"""
import argparse
import ast
import json
import os
import re

ROOT = os.environ.get(
    "BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
)
REVIEW_ROOT = os.path.join(ROOT, "review")
CONTRACTS = os.path.join(REVIEW_ROOT, "artifacts", "CONTRACTS.json")


def _fn_sources(path):
    """name -> source slice for every top-level + nested function in a file."""
    src = open(os.path.join(ROOT, path)).read()
    lines = src.splitlines()
    out = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a, b = node.lineno - 1, (node.end_lineno or node.lineno)
            out[node.name] = "\n".join(lines[a:b])
    return out


def check(contracts_path, gate, root=None):
    global ROOT
    ROOT = root or ROOT
    contracts = json.load(open(contracts_path))["contracts"]
    failures = []
    print("CONSUMER-AGREEMENT (shared-symbol guard contracts)")
    print("=" * 70)
    for c in contracts:
        fns = _fn_sources(c["file"])
        guard = re.compile(c["guard_signature"])
        print(f"\n{c['id']} — symbol `{c['symbol']}` in {c['file']}")
        print(f"  guard: /{c['guard_signature']}/")
        miss = []
        for prod in c["producers"]:
            body = fns.get(prod)
            if body is None:
                miss.append((prod, "function not found"))
                continue
            if not guard.search(body):
                miss.append((prod, "PRODUCER does not apply the guard"))
            else:
                print(f"    [ok]   producer {prod}() applies the guard")
        for m, why in miss:
            print(f"    [FAIL] producer {m}(): {why}")
        for cons in c.get("consumers_relying", []):
            body = fns.get(cons, "")
            re_guards = bool(guard.search(body))
            print(f"    [rely] consumer {cons}() relies on the invariant "
                  f"({'also re-guards' if re_guards else 'does NOT re-guard — by design'})")
        if miss:
            failures.append((c["id"], miss))
    print("\n" + "=" * 70)
    if gate:
        if failures:
            print(f"GATE FAIL: producers break their contract: "
                  f"{[f[0] for f in failures]}")
            return 1
        print("GATE PASS: every producer applies its symbol's guard "
              "(consumer assumptions hold)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--contracts", default=CONTRACTS)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--gate", action="store_true")
    a = ap.parse_args()
    raise SystemExit(check(a.contracts, a.gate, a.root))


if __name__ == "__main__":
    main()
