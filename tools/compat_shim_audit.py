#!/usr/bin/env python3
"""compat_shim_audit.py — audit compatibility wrappers (M). DOCUMENT ONLY.
Read-only. Locates the named wrappers (_browser_backend, _use_cloakbrowser,
_feature_enabled) and similar, reporting definitions vs call sites so duplicates /
dead wrappers / consolidation opportunities are visible. It NEVER modifies them —
these are runtime-adjacent (browser backend / CloakBrowser) and explicitly off-limits.
Writes reports/compat_shim_audit.md.
CLI: --root, --outdir, --json
"""
import os as _os_rc, sys as _sys_rc
_sys_rc.path.insert(0, _os_rc.path.dirname(_os_rc.path.abspath(__file__)))
import report_core as _RC  # shared write/render helpers

import argparse, ast, json, os, re, sys
from collections import defaultdict

_ROOTS = ["bulk_downloader", "tools"]
_TARGETS = ["_browser_backend", "_use_cloakbrowser", "_feature_enabled"]
_EXTRA = re.compile(r"\bdef (_[a-z][a-z0-9_]*(?:backend|compat|shim|enabled|fallback|legacy))\b")


def audit(root=".", roots=None):
    roots = roots or _ROOTS
    defs = defaultdict(list)      # symbol -> [file:line] where defined
    calls = defaultdict(int)      # symbol -> call/reference count
    extra_wrappers = []
    targets = set(_TARGETS)
    for r in roots:
        for dp, _, names in os.walk(os.path.join(root, r)):
            if "__pycache__" in dp:
                continue
            for n in names:
                if not n.endswith(".py"):
                    continue
                p = os.path.join(dp, n); rel = os.path.relpath(p, root)
                try:
                    text = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    for t in _TARGETS:
                        if re.search(rf"def {t}\b", line):
                            defs[t].append(f"{rel}:{i}")
                        calls[t] += len(re.findall(rf"\b{t}\b", line))
                    for m in _EXTRA.finditer(line):
                        extra_wrappers.append({"symbol": m.group(1), "at": f"{rel}:{i}"})
    report = {}
    for t in _TARGETS:
        report[t] = {"definitions": defs.get(t, []),
                     "reference_count": calls.get(t, 0),
                     "def_count": len(defs.get(t, [])),
                     "flag": ("multiple definitions" if len(defs.get(t, [])) > 1
                              else ("no references" if calls.get(t, 0) <= len(defs.get(t, []))
                                    and defs.get(t) else "ok"))}
    return {"targets": report, "similar_wrappers": extra_wrappers[:60],
            "similar_count": len(extra_wrappers)}


def _md(d):
    L = ["# Compatibility-shim audit (document only)", "",
         "These wrappers are runtime-adjacent and were NOT modified.", "",
         "## Named targets", ""]
    for t, info in d["targets"].items():
        L.append(f"- `{t}`: {info['def_count']} def(s), {info['reference_count']} "
                 f"reference(s) — **{info['flag']}**")
        for loc in info["definitions"]:
            L.append(f"    - def at `{loc}`")
    L += ["", f"## Similar wrappers ({d['similar_count']})", ""]
    for w in d["similar_wrappers"][:40]:
        L.append(f"- `{w['symbol']}` at `{w['at']}`")
    L += ["", "_Consolidation is a runtime change — recommendations only; defer to operator._"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="."); ap.add_argument("--outdir", default="reports")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = audit(a.root)
    if a.json:
        print(json.dumps(d, indent=2)); return 0
    p = _RC.write_report(a.outdir, "compat_shim_audit.md", _md(d))
    print("wrote", p); return 0


if __name__ == "__main__":
    sys.exit(main())
