#!/usr/bin/env python3
"""changelog_analyzer.py — parse CHANGELOG.md into release intelligence (E).
Read-only. Per release: date, title, feature vs bug-fix bullet counts, modules
mentioned. Plus trends (releases per day, total features/fixes).
CLI: --root, --json
"""
import argparse, json, re, sys, os
from collections import Counter

_HEAD = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\s+—\s+(\d{4}-\d{2}-\d{2})?\s*(?:\((.*?)\))?",
                   re.M)
_FIX = re.compile(r"\b(fix|bug|regression|crash|broken|incorrect|wrong|leak)\b", re.I)
_MODULE = re.compile(r"\b([a-z_]+\.py|app\.py|runner\.py|db\.py|cockpit_\w+|template_\w+)\b")


def parse(root="."):
    p = os.path.join(root, "CHANGELOG.md")
    text = open(p, encoding="utf-8", errors="replace").read()
    heads = list(_HEAD.finditer(text))
    releases = []
    for i, m in enumerate(heads):
        start = m.end()
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[start:end]
        bullets = [ln for ln in body.splitlines() if ln.strip().startswith(("-", "*"))]
        fixes = sum(1 for b in bullets if _FIX.search(b))
        feats = len(bullets) - fixes
        mods = Counter()
        for b in bullets:
            for mm in _MODULE.findall(b):
                mods[mm] += 1
        releases.append({"version": m.group(1), "date": m.group(2),
                         "title": (m.group(3) or "").strip(),
                         "bullets": len(bullets), "features": feats, "fixes": fixes,
                         "modules": dict(mods.most_common(8))})
    by_date = Counter(r["date"] for r in releases if r["date"])
    mod_total = Counter()
    for r in releases:
        for k, v in r["modules"].items():
            mod_total[k] += v
    return {"releases": releases, "count": len(releases),
            "totals": {"features": sum(r["features"] for r in releases),
                       "fixes": sum(r["fixes"] for r in releases),
                       "bullets": sum(r["bullets"] for r in releases)},
            "releases_per_date": dict(by_date.most_common(10)),
            "module_impact_top": dict(mod_total.most_common(15))}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv); d = parse(a.root)
    if a.json: print(json.dumps(d, indent=2))
    else:
        print(f"releases: {d['count']} | features {d['totals']['features']} "
              f"fixes {d['totals']['fixes']}")
        print("top module impact:", d["module_impact_top"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
