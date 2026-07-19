#!/usr/bin/env python3
"""endpoint_reachability -- which mutating endpoints can an OPERATOR actually reach?

A route existing is not reachability. A CONTROL that calls it is. Route counts have
been used as a parity proxy for the whole life of this project and they answer a
different question: /api/vpn/system_killswitch/<id>/apply exists, is counted, and no
human can invoke it from the GUI.

Matching is the whole difficulty, and the naive version is worse than useless:

  * callers build URLs from FRAGMENTS -- cockpit_console does `/cockpit/api/shell/` +
    verb -- so the full rule string never appears in source;
  * callers use TEMPLATE LITERALS -- `/api/sites/${id}/settings` -- so the rule with
    its <converter> placeholders never appears either;
  * a type declaration is not a call. `system_killswitch_active?: boolean` in a .tsx
    file means the GUI RENDERS the state, not that it can change it.

Matching full rule strings reported 117 dark endpoints. 40 of those were false: real
callers that build the URL in pieces. The honest number is 77. A gate that cries wolf
on 40 endpoints gets switched off, so the matcher tries the static prefix AND the
distinctive tail segments.

Classification (reports/endpoint_reachability.json) says WHY a dark endpoint is dark:
dev-only, internal/machine, or an operator gap. Dark is allowed; dark and unexplained
is not.
"""
import argparse
import json
import re
import os
import sys

DEFAULT_ROOT = "/home/claude/work"
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _blob(root, rel, exts):
    src = ""
    base = os.path.join(root, rel)
    if not os.path.isdir(base):
        return src
    for dp, dn, fns in os.walk(base):
        dn[:] = [d for d in dn if d not in ("node_modules", "__pycache__", "dist")]
        for fn in sorted(fns):
            if fn.endswith(exts):
                try:
                    src += open(os.path.join(dp, fn), encoding="utf-8",
                                errors="replace").read()
                except OSError:
                    continue
    return src


# The server-rendered consoles -- and ONLY these. tools/ also holds ~200 CLI scripts,
# and a CLI script calling an endpoint does NOT make it operator-reachable from a GUI.
# Blobbing all of tools/ certified /api/vpn/system_killswitch/<id>/apply as "wired"
# because a command-line tool posts to it. That is precisely the mistake this whole
# ledger exists to stop: confusing "something calls it" with "an operator can reach it".
_CONSOLES = (
    "tools/cockpit_console.py",
    "tools/cockpit_core.py",
    "tools/framework_dashboard.py",
    "tools/framework_fleet.py",
)


def _files(root, rels):
    src = ""
    for rel in rels:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            try:
                src += open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
    return src


def _callers(root):
    """Every surface an operator can drive: the SPA, the server-rendered consoles,
    the browser extension. NOT the CLI."""
    return {
        "spa": _blob(root, "frontend/src", (".ts", ".tsx")),
        "console": _files(root, _CONSOLES),
        "extension": _blob(root, "extension", (".js", ".html")),
    }


def _reaches(rule, src, shared_tails=frozenset()):
    """Fragment- and template-literal-aware. See the module docstring for why the
    obvious full-string match is wrong -- and why the obvious FIX is also wrong.

    PARAMETERIZED rules (`/api/sites/<sid>/purge`) are the trap. Matching their static
    stem (`/api/sites`) means one apiPost to /api/sites certifies EVERY /api/sites/...
    endpoint as wired -- including the dangerous ones. So for a parameterized rule,
    require every static PART to appear: the prefix `/api/sites/` AND the suffix
    `/purge`. A template literal `/api/sites/${id}/purge` contains both; a sibling
    caller contains only the first.

    STATIC rules are matched whole, then by parent path (a caller may build the URL:
    `"/cockpit/api/shell/" + verb`), then by a distinctive tail.
    """
    if not src:
        return False
    parts = [p for p in re.split(r"<[^>]+>", rule) if p]
    if len(parts) > 1:
        sig = [p for p in parts if len(p) >= 4]
        return bool(sig) and all(p in src for p in sig)

    stem = rule.rstrip("/")
    if len(stem) > 6 and stem in src:
        return True
    parent = stem.rsplit("/", 1)[0] if "/" in stem else ""
    if (parent and len(parent) > 14
            and len([s for s in parent.split("/") if s]) >= 3
            and parent in src):
        return True
    # Tail matching requires a LEADING SLASH. Without it, any identifier that happens
    # to share the name counts as a call site -- react-query does
    # `queryKey: ["sched_exports", "list"]`, and that bare string certified
    # /api/sched_exports as wired when nothing calls it. A cache key is not a call.
    segs = [s for s in stem.split("/") if s]
    if not segs:
        return False
    if len(segs) >= 2:
        tail = "/" + "/".join(segs[-2:])
        if len(tail) > 7 and tail in src:
            return True
    # v3.66.752 -- two guards on the 1-segment tail, both from live phantoms:
    # SHARED tails are not distinctive (wiring /api/ai/classify certified
    # /api/retry_policy/classify; five GET */diagnose callers were certifying
    # POST /api/doctor/diagnose -- so build() computes shared_tails over ALL
    # url_map rules, not just mutating ones). And the occurrence must be
    # TERMINAL: the real thumbnail_sheets caller's /contact_sheet/ substring
    # certified the DARK raw-path /api/thumbs/contact_sheet family as
    # operator-reachable; a tail followed by more path is a DIFFERENT
    # endpoint's URL. Both guards fail toward DARK: a wired endpoint misfiled
    # dark is a visible, self-correcting nuisance; a dark endpoint misfiled
    # wired is never adjudicated.
    tail = "/" + segs[-1]
    if len(tail) > 7 and tail not in shared_tails:
        if re.search(re.escape(tail) + r"(?![\w/-])", src):
            return True
    return False


def _load_classes(root):
    p = os.path.join(root, "reports", "endpoint_reachability.json")
    try:
        return json.load(open(p, encoding="utf-8")).get("classified", {})
    except Exception:  # noqa: BLE001
        return {}


def build(root=DEFAULT_ROOT):
    root = os.path.abspath(root)
    sys.path.insert(0, root)
    from bulk_downloader.app import app

    callers = _callers(root)
    classes = _load_classes(root)

    # v3.66.752 -- final segments appearing on MORE THAN ONE rule (any method)
    # are non-distinctive; the 1-segment tail rule must never certify by one.
    from collections import Counter
    _tails = Counter()
    for _r in app.url_map.iter_rules():
        _segs = [s for s in str(_r.rule).rstrip("/").split("/") if s]
        if _segs:
            _tails["/" + _segs[-1]] += 1
    shared_tails = frozenset(t for t, c in _tails.items() if c > 1)

    seen, endpoints = set(), []
    for r in app.url_map.iter_rules():
        if not (r.methods & MUTATING):
            continue
        rule = str(r.rule)
        if rule in seen:
            continue
        seen.add(rule)
        by = [k for k, src in callers.items()
              if _reaches(rule, src, shared_tails=shared_tails)]
        reach = by[0] if by else "dark"
        e = {"rule": rule, "reach": reach,
             "methods": sorted(r.methods & MUTATING)}
        if reach == "dark":
            e["why"] = classes.get(rule, "")
        endpoints.append(e)

    endpoints.sort(key=lambda e: e["rule"])
    dark = [e for e in endpoints if e["reach"] == "dark"]
    return {
        "root": root,
        "counts": {
            "mutating": len(endpoints),
            "wired": len(endpoints) - len(dark),
            "dark": len(dark),
            "dark_unexplained": len([e for e in dark if not e.get("why")]),
        },
        "endpoints": endpoints,
    }


def selftest():
    ok = True
    G, R, RST = "\033[32m", "\033[31m", "\033[0m"

    # POS: a caller that builds the URL from FRAGMENTS is reachable. This is the case
    # that made the naive matcher report 40 live endpoints as dark.
    p1 = _reaches("/cockpit/api/shell/open", 'fetch("/cockpit/api/shell/" + verb)')
    print(("%sPASS%s" if p1 else "%sFAIL%s") % ((G, RST) if p1 else (R, RST)) +
          "  POS: fragment-built URL counts as a caller")
    ok &= p1

    # POS: a template literal with a param
    p2 = _reaches("/api/sites/<sid>/settings", "apiPost(`/api/sites/${id}/settings`)")
    print(("%sPASS%s" if p2 else "%sFAIL%s") % ((G, RST) if p2 else (R, RST)) +
          "  POS: template-literal URL counts as a caller")
    ok &= p2

    # NEG: an unrelated endpoint is NOT reachable just because a prefix appears
    n1 = not _reaches("/api/vpn/system_killswitch/<t>/apply", 'const x = "/api/vpn";')
    print(("%sPASS%s" if n1 else "%sFAIL%s") % ((G, RST) if n1 else (R, RST)) +
          "  NEG: a bare prefix does not make a sub-route reachable")
    ok &= n1

    # NEG: short tails must not match promiscuously
    n2 = not _reaches("/api/x/run", "run()")
    print(("%sPASS%s" if n2 else "%sFAIL%s") % ((G, RST) if n2 else (R, RST)) +
          "  NEG: a short tail ('run') does not match any word in the source")
    ok &= n2

    # NEG (the one that keeps the parent-prefix rule honest): a caller of a SIBLING
    # route must not make a dangerous sub-route look reachable. "/api/sites" is 2
    # segments, so it can never satisfy the parent rule -- otherwise one apiPost to
    # /api/sites would certify every /api/sites/... endpoint as wired.
    n3 = not _reaches("/api/sites/<sid>/purge", 'apiPost("/api/sites", body)')
    print(("%sPASS%s" if n3 else "%sFAIL%s") % ((G, RST) if n3 else (R, RST)) +
          "  NEG: a sibling caller does not certify a dangerous sub-route")
    ok &= n3

    # NEG: a cache key sharing the endpoint name is not a caller. `queryKey: ["sched_exports"]`
    n4 = not _reaches("/api/sched_exports", 'queryKey: ["sched_exports", "list"]')
    print(("%sPASS%s" if n4 else "%sFAIL%s") % ((G, RST) if n4 else (R, RST)) +
          "  NEG: a cache key sharing the endpoint name is not a caller")
    ok &= n4

    # NEG (v3.66.752, found live): a SHARED final segment is not a distinctive tail.
    # Wiring /api/ai/classify certified /api/retry_policy/classify as reachable --
    # the 1-segment tail rule must refuse when another route ends in the same segment.
    n5 = not _reaches("/api/retry_policy/classify",
                      'apiPost("/api/ai/classify", { element_desc })',
                      shared_tails=frozenset({"/classify"}))
    print(("%sPASS%s" if n5 else "%sFAIL%s") % ((G, RST) if n5 else (R, RST)) +
          "  NEG: a final segment shared by another route is not distinctive")
    ok &= n5

    # NEG (v3.66.752, found live): a tail occurrence INSIDE a longer path is someone
    # else's URL. The real thumbnail_sheets caller's '/contact_sheet/' substring
    # certified the DARK raw-path thumbs family as operator-reachable.
    n6 = not _reaches("/api/thumbs/contact_sheet",
                      "apiPost(`/api/thumbnail_sheets/contact_sheet/${hid}`, {})")
    print(("%sPASS%s" if n6 else "%sFAIL%s") % ((G, RST) if n6 else (R, RST)) +
          "  NEG: a tail followed by more path is a different endpoint's URL")
    ok &= n6

    print("SELFTEST PASS" if ok else "SELFTEST FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="endpoint_reachability")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--update", action="store_true",
                    help="re-pin the ledger (dark_count ratchet)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    d = build(a.root)
    if a.update:
        p = os.path.join(a.root, "reports", "endpoint_reachability.json")
        prev = _load_classes(a.root)
        json.dump({"dark_count": d["counts"]["dark"],
                   "dark": sorted(e["rule"] for e in d["endpoints"]
                                  if e["reach"] == "dark"),
                   "classified": prev},
                  open(p, "w", encoding="utf-8"), indent=2, sort_keys=True)
        print("pinned dark_count=%d -> %s" % (d["counts"]["dark"], p))
        return 0
    if a.json:
        print(json.dumps(d, indent=2, sort_keys=True))
    else:
        c = d["counts"]
        print("mutating endpoints : %d" % c["mutating"])
        print("  wired to a control: %d" % c["wired"])
        print("  DARK              : %d  (unexplained: %d)"
              % (c["dark"], c["dark_unexplained"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
