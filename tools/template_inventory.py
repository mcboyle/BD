#!/usr/bin/env python3
"""
template_inventory.py — read-only inventory + scoring of the template tree.

Scans templates/{reviewed,enabled,drafts,review_candidates} and reports, per
template: host, status, schema, which selector groups are present, whether the
download trigger / modal row_selectors / api base exist, the resolution ladder,
network-pattern count, a 0-100 completeness score, blocked-term warnings, and a
promotion-readiness flag that mirrors the ACTUAL promote gate
(tools/promote_template.py) so the numbers can't diverge from reality.

STRICTLY READ-ONLY. It never writes, promotes, enables, or swaps anything.
Shared library: verify_release.py (P1) and the cockpit Template Manager (P3)
both import `scan()`.

stdlib-only; plain `python3` runs it.

CLI:
    python3 tools/template_inventory.py [--root .] [--json]
Exit 0 always (it is a report, not a gate) unless --strict is given, in which
case exit 1 if any dir-level sanity violation is found (e.g. a draft marked
enabled, or a reviewed template with an unsupported status).
"""
import argparse
import json
import os
import sys

# Reuse the SAME blocked-term list the promote gate uses — single source of truth
# in bulk_downloader.bad_terms. (This is a read-only diagnostic; if the shared
# module is somehow unimportable, fall back to an empty list — the promote gate
# remains the authoritative safety check — rather than duplicate the definition.)
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from bulk_downloader.bad_terms import BAD_TERMS  # type: ignore
except Exception:  # pragma: no cover - defensive; gate is authoritative
    BAD_TERMS = []  # type: ignore

# Directory → the set of statuses its templates may legitimately carry.
# NOTE: the codebase uses BOTH "draft_requires_review" (builder's template_status,
# line 292) and "draft_review_required" (the draft's top-level status + not-ready
# candidates) — a real status-string inconsistency, logged as a conflict. We accept
# both for drafts rather than emit noise. The safety-critical rules are enforced
# separately below: nothing outside reviewed/enabled may be `enabled`, the
# enabled directory is enabled-only, and reviewed templates may be intentionally
# disabled while their evidence remains available to the Template Manager.
DIR_ALLOWED_STATUS = {
    "reviewed": {"enabled", "disabled"},
    "enabled": {"enabled"},
    "drafts": {"draft_requires_review", "draft_review_required"},
    "review_candidates": {"review_ready", "draft_review_required"},
}

# Completeness scoring weights (sum 100). A video-download template wants all of
# these; missing pieces lower the score but only the gate decides promotability.
_WEIGHTS = {
    "api_base": 15,
    "download_trigger": 15,
    "row_selectors": 15,
    "resolutions": 15,
    "login": 10,
    "player": 10,
    "quality": 10,
    "network_patterns": 10,
}


def _host_of(tpl, fallback):
    return tpl.get("host") or tpl.get("hostname") or tpl.get("site") or fallback


def _blocked_terms(tpl):
    """Scan api values + network_patterns for blocked terms (same surface the
    promote gate checks). Returns the sorted list of terms found."""
    hay = []
    for v in (tpl.get("api") or {}).values():
        hay.append(str(v))
    for p in (tpl.get("network_patterns") or []):
        hay.append(json.dumps(p) if isinstance(p, (dict, list)) else str(p))
    blob = " ".join(hay).lower()
    return sorted({t for t in BAD_TERMS if t.lower() in blob})


def assess(tpl, source="?"):
    """Return a per-template facts dict (pure; no IO)."""
    sel = tpl.get("selectors") or {}
    dl = sel.get("download") or {}
    login = sel.get("login") or {}
    player = sel.get("player") or {}
    quality = sel.get("quality") or {}
    api = tpl.get("api") or {}
    res = tpl.get("resolutions") or []
    nps = tpl.get("network_patterns") or []
    rows = dl.get("row_selectors") or []

    present = {
        "api_base": bool(api.get("base")),
        "download_trigger": bool(dl.get("trigger")),
        "row_selectors": bool(rows),
        "resolutions": bool(res),
        "login": all(login.get(k) for k in ("email", "password", "submit")),
        "player": bool(player.get("container") or player.get("play_button")),
        "quality": bool(quality.get("open_menu") or quality.get("resolution_option")),
        "network_patterns": bool(nps),
    }
    score = sum(_WEIGHTS[k] for k, v in present.items() if v)
    blocked = _blocked_terms(tpl)

    # Mirror the real promote gate: (trigger|rows|button) + non-empty resolutions
    # + no blocked terms. api.base is recommended, not gated.
    gate_selector = bool(dl.get("trigger") or rows or dl.get("button"))
    promotion_ready = gate_selector and bool(res) and not blocked

    missing_recommended = [k for k, v in present.items() if not v]
    return {
        "source": source,
        "host": _host_of(tpl, "?"),
        "status": tpl.get("status"),
        "schema": tpl.get("schema"),
        "selector_groups": sorted(sel.keys()),
        "download_trigger": bool(dl.get("trigger")),
        "row_selectors_count": len(rows),
        "api_base": api.get("base"),
        "resolutions": res,
        "resolutions_count": len(res),
        "network_patterns_count": len(nps),
        "completeness_score": score,
        "blocked_terms": blocked,
        "promotion_ready": promotion_ready,
        "missing": missing_recommended,
        # Wave 168 review-only recognition metadata (None on pre-168 drafts).
        "recognition": tpl.get("recognition"),
    }


def scan(root="."):
    """Walk the four template dirs. Returns
    {"dirs": {name: [assessment,...]}, "sanity": [violation,...], "counts": {...}}.
    Read-only."""
    out = {"dirs": {}, "sanity": [], "counts": {}}
    for name, allowed in DIR_ALLOWED_STATUS.items():
        d = os.path.join(root, "templates", name)
        items = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".json"):
                    continue
                path = os.path.join(d, fn)
                try:
                    with open(path) as fh:
                        tpl = json.load(fh)
                except (OSError, ValueError) as e:
                    out["sanity"].append(f"{name}/{fn}: unreadable ({e})")
                    continue
                a = assess(tpl, source=f"{name}/{fn}")
                items.append(a)
                st = a["status"]
                # HARD: never-enable outside reviewed/enabled
                if name in ("drafts", "review_candidates") and st == "enabled":
                    out["sanity"].append(
                        f"{name}/{fn}: status=enabled in a non-reviewed dir "
                        "(drafts/candidates must NEVER be enabled)")
                # HARD: enabled/ is enabled-only; reviewed/ may retain an
                # intentionally disabled template for later OPV completion.
                elif name == "enabled" and st != "enabled":
                    out["sanity"].append(
                        f"{name}/{fn}: status={st!r} but {name}/ templates must be "
                        "'enabled' (registry loads only enabled)")
                elif name == "reviewed" and st not in allowed:
                    out["sanity"].append(
                        f"{name}/{fn}: status={st!r} but reviewed/ templates must be "
                        "'enabled' or 'disabled'")
                # SOFT: unexpected status string (informational, not a violation)
                elif st not in allowed:
                    out["sanity"].append(
                        f"{name}/{fn}: unexpected status {st!r} "
                        f"(expected one of {sorted(allowed)})")
        out["dirs"][name] = items
        out["counts"][name] = len(items)
    return out


def _print_report(data):
    print("=" * 70)
    print("  Template inventory")
    print("=" * 70)
    for name, items in data["dirs"].items():
        print(f"\n-- templates/{name}/  ({len(items)}) --")
        for a in items:
            flags = []
            if a["promotion_ready"]:
                flags.append("gate-ready")
            if a["blocked_terms"]:
                flags.append(f"BLOCKED:{','.join(a['blocked_terms'])}")
            if not a["download_trigger"]:
                flags.append("no-trigger")
            if not a["row_selectors_count"]:
                flags.append("no-rows")
            if not a["api_base"]:
                flags.append("no-api-base")
            print(f"  {a['host']:<24} status={a['status']!r:<24} "
                  f"score={a['completeness_score']:>3}/100 "
                  f"res={a['resolutions_count']} rows={a['row_selectors_count']} "
                  + (" ".join(flags)))
    if data["sanity"]:
        print("\n-- SANITY VIOLATIONS --")
        for v in data["sanity"]:
            print(f"  ! {v}")
    else:
        print("\n-- sanity: OK (statuses consistent with their dirs) --")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only template inventory + scoring.")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any dir-level sanity violation is found")
    args = ap.parse_args(argv)
    data = scan(args.root)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _print_report(data)
    return 1 if (args.strict and data["sanity"]) else 0


if __name__ == "__main__":
    sys.exit(main())
