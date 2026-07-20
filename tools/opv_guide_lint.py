#!/usr/bin/env python3
"""Validate an OPV guide's route + source-ref claims against the ACTUAL tree.

Meant to run ON THE BOX whose tree is the real test case (CLAUDE.md 1: verify by
running, never inherit a number from a doc). It trusts NO markdown:

  ROUTE claims   -- every `METHOD /api|/cockpit/...` (markdown or a curl
                    `-X METHOD "$BASE/..."`) is checked against the LIVE Flask
                    url_map (imported from the app), not a catalog file that can
                    drift. Param names + shell-var interpolations are normalized
                    (`<int:site_id>` and `$TUN` both -> `<P>`).
  SOURCE refs    -- each `file.py:NNN` must actually contain, within a few lines of
                    NNN, a SYMBOL the guide names right next to it (a backtick
                    identifier or a route path). When the line has DRIFTED the tool
                    SELF-CORRECTS: it finds where the symbol really is and prints the
                    true line, so you can fix the guide (this is the db.py:1382 ->
                    :1578 class of bug -- a real ref pointing at the wrong line).

Exit 0 iff every claim resolves. Usage:
  ./venv/bin/python tools/opv_guide_lint.py <guide.md>
    [--catalog ENDPOINT_CATALOG.md]   # offline fallback if the app won't import
    [--window N]                      # symbol must be within +/-N lines (default 4)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")
_PATH = r"/[A-Za-z0-9_./<>:${}\-]*"
_SRC_DIRS = ("", "bulk_downloader", "tools", "scripts", "frontend/src")


def _norm(path: str) -> str:
    path = path.split("?", 1)[0].split("#", 1)[0]
    path = re.sub(r"<[^>]+>", "<P>", path)
    path = re.sub(r"\$\{?\w+\}?", "<P>", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def _resolve_src(root: Path, rel: str):
    for d in _SRC_DIRS:
        cand = root / d / rel if d else root / rel
        if cand.is_file():
            return cand
    return None


def load_routes_from_app(root: Path) -> set[tuple[str, str]]:
    """The LIVE route table -- what the running service actually serves."""
    sys.path.insert(0, str(root))
    from bulk_downloader import app as A  # noqa: E402
    pairs = set()
    for r in A.app.url_map.iter_rules():
        for m in r.methods:
            if m in _METHODS:
                pairs.add((m, _norm(str(r.rule))))
    return pairs


def load_routes_from_catalog(cat_path: Path) -> set[tuple[str, str]]:
    pairs = set()
    row = re.compile(r"^(%s)\s+(%s)" % ("|".join(_METHODS), _PATH))
    for line in cat_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = row.match(line.strip())
        if m:
            pairs.add((m.group(1), _norm(m.group(2))))
    return pairs


def extract_route_claims(text: str) -> list[tuple[str, str, int]]:
    claims = []
    lines = text.splitlines()
    explicit = re.compile(r"\b(%s)\s+(%s)" % ("|".join(_METHODS), _PATH))
    for i, ln in enumerate(lines, 1):
        for m in explicit.finditer(ln):
            claims.append((m.group(1), _norm(m.group(2)), i))
    # curl blocks (may span lines via trailing backslash)
    joined, starts, buf, buf_start = [], [], "", 0
    for i, ln in enumerate(lines, 1):
        if buf:
            buf += " " + ln.strip()
        elif "curl" in ln:
            buf, buf_start = ln.strip(), i
        if buf and not buf.rstrip().endswith("\\"):
            joined.append(buf.replace("\\", " ")); starts.append(buf_start); buf = ""
    curl_x = re.compile(r"-X\s+(%s)" % "|".join(_METHODS))
    # NB: keep the base-url var names here free of a literal BD_ token -- the
    # config-surface scanner greps every file for BD_[A-Z0-9_]+ and would ledger
    # such a token as a phantom env var (open parity debt). $BASE / $B suffice.
    base_path = re.compile(r'\$(?:BASE|B)"?(%s)' % _PATH)
    for cmd, ln in zip(joined, starts):
        mm = curl_x.search(cmd)
        meth = mm.group(1) if mm else "GET"
        for pm in base_path.finditer(cmd):
            claims.append((meth, _norm(pm.group(1)), ln))
    return claims


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_ROUTEISH = re.compile(r"/[A-Za-z0-9_./<>:\-]{3,}")


def _candidates_for(lines: list[str], gi: int) -> list[str]:
    """Symbols the guide names next to a ref on guide-line gi (1-based): backtick
    identifiers (last dotted segment too) + route paths, from this line + the prior."""
    ctx = lines[gi - 1]
    if gi >= 2:
        ctx = lines[gi - 2] + " " + ctx
    cands: list[str] = []
    for span in re.findall(r"`([^`]+)`", ctx):
        for tok in _IDENT.findall(span):
            cands.append(tok)
        for rt in _ROUTEISH.findall(span):
            cands.append(rt)
    # de-dup, keep order, drop the ubiquitous module-name noise
    seen, out = set(), []
    for c in cands:
        if c in seen or c in ("python", "json", "curl", "true", "false", "None"):
            continue
        seen.add(c); out.append(c)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("guide")
    ap.add_argument("--catalog", default="")
    ap.add_argument("--root", default=str(_ROOT))
    ap.add_argument("--window", type=int, default=4)
    args = ap.parse_args(argv)
    root = Path(args.root)
    W = args.window

    route_src = "live url_map"
    try:
        if args.catalog:
            raise RuntimeError("catalog requested")
        routes_truth = load_routes_from_app(root)
    except Exception as e:  # noqa: BLE001
        cat = Path(args.catalog) if args.catalog else root / "ENDPOINT_CATALOG.md"
        route_src = f"catalog {cat.name} (app import failed: {str(e)[:60]})" if not args.catalog else f"catalog {cat.name}"
        routes_truth = load_routes_from_catalog(cat)
    truth_paths = {p for _, p in routes_truth}

    text = Path(args.guide).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    stale_routes, stale_refs = [], []

    routes = [c for c in extract_route_claims(text)
              if c[1].startswith(("/api", "/cockpit", "/dashboard", "/metrics"))]
    for meth, path, ln in routes:
        if (meth, path) in routes_truth:
            continue
        why = ("path exists but NOT for %s" % meth) if path in truth_paths else "no such route"
        stale_routes.append((ln, f"{meth} {path}", why))

    refs = []
    pat = re.compile(r"\b([A-Za-z0-9_./]+\.py):(\d+)\b")
    for i, ln in enumerate(lines, 1):
        for m in pat.finditer(ln):
            refs.append((m.group(1), int(m.group(2)), i))
    for rel, claimed, gi in refs:
        fp = _resolve_src(root, rel)
        if fp is None:
            stale_refs.append((gi, f"{rel}:{claimed}", "file not found in repo")); continue
        flines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
        if claimed > len(flines):
            stale_refs.append((gi, f"{rel}:{claimed}", f"{fp.relative_to(root)} has only {len(flines)} lines")); continue
        cands = _candidates_for(lines, gi)
        # Drop the file's OWN module stem -- `app_report_center.py:484` puts
        # `app_report_center` in a backtick, and that token appears once (the file
        # header) so it would wrongly win "rarest" and mask the real symbol.
        _stem = fp.stem
        cands = [c for c in cands if c != _stem and not c.endswith("/" + _stem)]
        # ANCHOR on the most SPECIFIC candidate (rarest in the file), not a common
        # param name like `site_id` that appears everywhere and would mask drift.
        occ = {}  # candidate -> sorted list of line numbers it appears on
        for c in cands:
            if c.startswith("/"):
                ls = [j for j, fl in enumerate(flines, 1) if c in fl]
            else:
                rx = re.compile(r"\b%s\b" % re.escape(c))
                ls = [j for j, fl in enumerate(flines, 1) if rx.search(fl)]
            if ls:
                occ[c] = ls
        if not occ:
            continue  # nothing nameable to adjudicate -> not a checkable claim
        # rarest first; the function/route name wins over ubiquitous params
        anchor = min(occ, key=lambda c: (len(occ[c]), -len(c)))
        near = [j for j in occ[anchor] if abs(j - claimed) <= W]
        if near:
            continue
        real = min(occ[anchor], key=lambda j: abs(j - claimed))
        stale_refs.append((gi, f"{rel}:{claimed}",
                           f"line drifted -- {anchor!r} is at :{real} (not within +/-{W} of :{claimed})"))

    print(f"== opv_guide_lint: {Path(args.guide).name} ==")
    print(f"route truth: {route_src} ({len(routes_truth)} method-routes) | "
          f"guide routes: {len(routes)} | source-refs: {len(refs)}")
    if stale_routes:
        print(f"\nSTALE ROUTES ({len(stale_routes)}):")
        for ln, claim, why in stale_routes:
            print(f"  L{ln:<4} {claim:48s} -- {why}")
    if stale_refs:
        print(f"\nSTALE SOURCE REFS ({len(stale_refs)}):")
        for ln, claim, why in stale_refs:
            print(f"  L{ln:<4} {claim:28s}{why}")
    if not stale_routes and not stale_refs:
        print("\nOK -- every route + symbol-anchored source-ref resolves against THIS tree.")
        return 0
    print(f"\nFAIL -- {len(stale_routes)} stale route(s), {len(stale_refs)} stale ref(s). "
          f"(Line numbers are THIS tree's; fix the guide to match.)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
