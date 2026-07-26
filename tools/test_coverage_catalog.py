#!/usr/bin/env python3
"""test_coverage_catalog.py — heuristic map of source modules -> test files (G).
Read-only. Matches by shared name tokens (e.g. queue_intelligence -> test_p8_queue_intelligence).
A module with no matching test file is flagged as a coverage gap candidate. --json
"""
import argparse, json, os, re, sys


def _tokens(name):
    return set(t for t in re.split(r"[_\.]", name.lower()) if len(t) > 3)


def catalog(root="."):
    src_base = os.path.join(root, "bulk_downloader")
    test_base = os.path.join(root, "tests")
    tests = [n for n in (os.listdir(test_base) if os.path.isdir(test_base) else [])
             if n.startswith("test_") and n.endswith(".py")]
    test_tokens = {t: _tokens(t[5:-3]) for t in tests}
    mapped, gaps = {}, []
    for n in sorted(os.listdir(src_base)) if os.path.isdir(src_base) else []:
        if not n.endswith(".py") or n == "__init__.py":
            continue
        mt = _tokens(n[:-3])
        hits = [t for t, tk in test_tokens.items() if mt & tk]
        if hits:
            module_path = os.path.relpath(
                os.path.join(src_base, n),
                root,
            ).replace(os.sep, "/")
            mapped[module_path] = hits
        else:
            gaps.append(
                os.path.relpath(
                    os.path.join(src_base, n),
                    root,
                ).replace(os.sep, "/")
            )
    return {"modules": len(mapped) + len(gaps), "with_test_match": len(mapped),
            "gap_candidates": gaps, "mapped": mapped}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = catalog(a.root)
    print(json.dumps(d, indent=2) if a.json else
          f"modules {d['modules']} | with test match {d['with_test_match']} | "
          f"gap candidates {len(d['gap_candidates'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
