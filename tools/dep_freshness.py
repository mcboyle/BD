"""Dependency-freshness advisory scanner (POS-2) -- ADVISORY ONLY, never auto-bumps.

Reads the repo's requirements*.txt files and the currently-installed package
versions, and reports pins whose installed version has DRIFTED outside the declared
specifier, packages that are UNPINNED (no specifier), and pinned packages that are
NOT INSTALLED. Read-only: it produces a report and a non-zero exit under --check when
drift is found; it never edits requirements, never installs, never upgrades.

Usage:
    python tools/dep_freshness.py                # print the advisory report
    python tools/dep_freshness.py --check        # exit 1 if any pin has drifted
    python tools/dep_freshness.py --json         # machine-readable report

CLI-only by design: dependency changes are an operator decision, so this surfaces
advice and stops there (mirrors the ytdlp_updater staleness posture -- report, don't
act).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

# A requirement line: name + optional extras + optional specifier. We only need the
# distribution name and the version specifier; markers/URLs are ignored (reported
# unpinned if no specifier).
_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*([<>=!~][^;#]*)?")


def parse_requirement_line(line: str) -> Optional[Dict[str, str]]:
    """Parse one requirements line -> {name, specifier, raw} or None (comment/blank/
    non-pin such as a bare URL or -r include)."""
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("-"):
        return None
    if "://" in s or s.startswith("git+"):
        return None
    m = _LINE.match(s)
    if not m or not m.group(1):
        return None
    name = m.group(1)
    spec = (m.group(3) or "").split("#")[0].strip()
    return {"name": name, "specifier": spec, "raw": s}


def parse_requirements(text: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for line in (text or "").splitlines():
        parsed = parse_requirement_line(line)
        if parsed:
            out.append(parsed)
    return out


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# Self-contained PEP 440-numeric comparator. Deliberately NO dependency on
# `packaging` -- the deploy (service) venv does not ship it, and this is an
# advisory dev tool that must run there. Handles the numeric release segment and
# the standard comparison operators, which is what the repo's range pins use
# (e.g. ">=3.0,<4.0"); pre/post/dev/epoch suffixes are ignored for the compare.
def _ver_tuple(v: str) -> tuple:
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(v))
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def _cmp(a: tuple, b: tuple) -> int:
    la, lb = list(a), list(b)
    n = max(len(la), len(lb))
    la += [0] * (n - len(la))
    lb += [0] * (n - len(lb))
    return (la > lb) - (la < lb)


def _satisfies(installed: str, specifier: str) -> bool:
    iv = _ver_tuple(installed)
    for clause in specifier.split(","):
        clause = clause.strip()
        if not clause:
            continue
        m = re.match(r"(~=|===|==|!=|>=|<=|>|<)\s*(.+)", clause)
        if not m:
            continue
        op, ver = m.group(1), m.group(2).strip().rstrip("*").rstrip(".")
        c = _cmp(iv, _ver_tuple(ver))
        if op in (">=",) and not c >= 0:
            return False
        if op in ("<=",) and not c <= 0:
            return False
        if op == ">" and not c > 0:
            return False
        if op == "<" and not c < 0:
            return False
        if op in ("==", "===", "~=") and op != "~=" and not c == 0:
            return False
        if op == "~=" and not c >= 0:   # compatible-release: at-least (lower bound)
            return False
        if op == "!=" and not c != 0:
            return False
    return True


def check_freshness(req_texts: Dict[str, str],
                    installed: Dict[str, str]) -> Dict[str, Any]:
    """Compare every pinned requirement against installed versions.

    req_texts: {filename: file-contents}. installed: {canonical-name: version}.
    Returns {checked, ok, drifted[], unpinned[], missing[], errors[]}.
    Drift = installed version does NOT satisfy the declared specifier.
    """
    inst = {_canon(k): v for k, v in (installed or {}).items()}
    report: Dict[str, Any] = {
        "checked": 0, "ok": 0,
        "drifted": [], "unpinned": [], "missing": [], "errors": [],
    }
    seen = set()
    for fname, text in (req_texts or {}).items():
        for req in parse_requirements(text):
            name = req["name"]
            key = (fname, _canon(name), req["specifier"])
            if key in seen:
                continue
            seen.add(key)
            report["checked"] += 1
            spec = req["specifier"]
            if not spec:
                report["unpinned"].append({"file": fname, "name": name})
                continue
            cur = inst.get(_canon(name))
            if cur is None:
                report["missing"].append(
                    {"file": fname, "name": name, "specifier": spec})
                continue
            try:
                satisfied = _satisfies(cur, spec)
            except Exception as e:  # noqa: BLE001
                report["errors"].append(
                    {"file": fname, "name": name, "error": str(e)[:120]})
                continue
            if satisfied:
                report["ok"] += 1
            else:
                report["drifted"].append({
                    "file": fname, "name": name,
                    "specifier": spec, "installed": cur})
    return report


def _installed_versions() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        from importlib import metadata as im
        for dist in im.distributions():
            try:
                nm = dist.metadata["Name"]
                if nm:
                    out[_canon(nm)] = dist.version
            except Exception:
                continue
    except Exception:
        pass
    return out


def _repo_req_texts(root: str) -> Dict[str, str]:
    texts: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(root, "requirements*.txt"))):
        try:
            with open(path, encoding="utf-8") as f:
                texts[os.path.basename(path)] = f.read()
        except OSError:
            continue
    return texts


def build_report(root: Optional[str] = None) -> Dict[str, Any]:
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return check_freshness(_repo_req_texts(root), _installed_versions())


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    as_json = "--json" in argv
    check = "--check" in argv
    rep = build_report()
    if as_json:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(f"dependency freshness: {rep['ok']}/{rep['checked']} pins satisfied")
        for d in rep["drifted"]:
            print(f"  DRIFT   {d['name']} {d['specifier']} but installed {d['installed']} "
                  f"({d['file']})")
        for m in rep["missing"]:
            print(f"  MISSING {m['name']} {m['specifier']} not installed ({m['file']})")
        for u in rep["unpinned"]:
            print(f"  UNPINNED {u['name']} ({u['file']})")
    if check and rep["drifted"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
