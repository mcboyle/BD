#!/usr/bin/env python3
"""
capture_analytics.py — analytics over capture artifacts + their yield (#13 / P9).

Read-only. Captures themselves run on the operator host (noVNC / sentinel), so in
a clean sandbox there are usually no artifacts — this tool degrades gracefully and
still reports the *yield* (the drafts and review candidates a capture produces).
On the operator host, point it at the capture output dirs to get real numbers.

What it reports:
  * artifact inventory — *.wacz and capture_*.json under the configured dirs:
    count, total bytes, and the host each is associated with (filename or content)
  * capture yield — per-host draft + review-candidate counts (templates a capture
    flowed into), and how many of those are gate-ready (via template_inventory)

It never fetches, replays, or reconstructs anything; it only stats files already
on disk. URLs, if surfaced, are query-stripped (reuses offline_capture_analyze's
helper when available).

CLI:
    python3 tools/capture_analytics.py [--root .] [--captures-dir DIR ...] [--json] [--md OUT]
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

_DEFAULT_DIRS = ["captures", "offline_out", "offline_captures",
                 "templates/drafts", "templates/review_candidates"]
_HOST_RE = re.compile(r"([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)


def _path_only(url):
    try:
        import offline_capture_analyze as OCA  # type: ignore
        return OCA._path_only(url)
    except Exception:  # noqa: BLE001
        return re.sub(r"\?.*$", "", str(url))


def _host_from(name, data=None):
    if isinstance(data, dict):
        for k in ("host", "hostname", "site"):
            if data.get(k):
                return data[k]
    base = os.path.basename(name)
    base = re.sub(r"\.(wacz|json)$", "", base, flags=re.I)
    # drop trailing capture/template descriptors so we keep just the domain
    base = re.sub(r"\.(session|capture|template|template-draft|candidate|draft)\b.*$",
                  "", base, flags=re.I)
    m = _HOST_RE.search(base)
    return m.group(1) if m else None


def _artifacts(root, dirs, limit=None):
    # Collect all paths first so we can check wacz↔json siblings
    wacz_paths: set = set()
    json_paths: list = []
    for d in dirs:
        base = os.path.join(root, d)
        if not os.path.isdir(base):
            continue
        for p in glob.glob(os.path.join(base, "*.wacz")):
            if os.path.isfile(p):
                wacz_paths.add(p)
        for p in glob.glob(os.path.join(base, "capture_*.json")):
            if os.path.isfile(p):
                json_paths.append(p)
        # legacy: bare *.wacz already captured above
    skipped = 0
    if limit is not None:
        # Newest-first across ALL artifacts; process at most `limit`. Mirrors the
        # 596 capture_diagnostics/replay bound: on a large store the per-json
        # json.load walk is a multi-minute single-core hang. Unbounded
        # (limit=None) is preserved for the CLI + summaries.
        allp = [(p, "w") for p in wacz_paths] + [(p, "j") for p in json_paths]
        allp.sort(key=lambda t: os.path.getmtime(t[0]), reverse=True)
        kept = allp[:limit]
        skipped = len(allp) - len(kept)
        wacz_paths = {p for p, k in kept if k == "w"}
        json_paths = [p for p, k in kept if k == "j"]
    arts = []
    # ── .wacz entries ──────────────────────────────────────────────
    for p in sorted(wacz_paths):
        size = os.path.getsize(p)
        arts.append({
            "path": os.path.relpath(p, root),
            "bytes": size,
            "host": _host_from(p, None),
            "type": "wacz",
            "capture_kind": None,
            "network_log_count": None,
            "dom_log_count": None,
            "websocket_log_count": None,
            "has_dom": None,
            "has_ws": None,
            "backend_inferred": None,
        })
    # ── capture_*.json entries ──────────────────────────────────────
    for p in sorted(json_paths):
        size = os.path.getsize(p)
        data = None
        try:
            with open(p) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
        if data is None:
            arts.append({"path": os.path.relpath(p, root), "bytes": size,
                         "host": _host_from(p, None), "type": "capture_json",
                         "capture_kind": None, "network_log_count": None,
                         "dom_log_count": None, "websocket_log_count": None,
                         "has_dom": None, "has_ws": None,
                         "backend_inferred": None})
            continue
        # G3: extract the full capture-result field set
        nlc = data.get("network_log_count")
        if nlc is None:
            nlc = len(data.get("network_log") or [])
        dlc = data.get("dom_log_count")
        if dlc is None:
            dl = data.get("dom_log")
            dlc = len(dl) if dl is not None else None
        wslc = data.get("websocket_log_count")
        if wslc is None:
            wsl = data.get("websocket_log")
            wslc = len(wsl) if wsl is not None else None
        ck = data.get("capture_kind")
        has_dom = bool(dlc) if dlc is not None else False
        has_ws = bool(wslc) if wslc is not None else False
        # Infer browser backend from capture_kind and dom presence:
        # "dom+network" → DOM recording was active → CloakBrowser/Playwright
        #   (required for rrweb inject); "network" alone could be either.
        if ck == "dom+network":
            backend = "playwright/cloak (dom+network)"
        elif ck:
            backend = f"unknown ({ck})"
        else:
            backend = "unknown"
        # Check for a sibling .wacz export
        stem = os.path.splitext(p)[0]
        sibling_wacz = stem + ".wacz"
        has_wacz = os.path.isfile(sibling_wacz) or sibling_wacz in wacz_paths
        arts.append({
            "path": os.path.relpath(p, root),
            "bytes": size,
            "host": _host_from(p, data),
            "type": "capture_json",
            "capture_kind": ck,
            "network_log_count": nlc,
            "dom_log_count": dlc,
            "websocket_log_count": wslc,
            "has_dom": has_dom,
            "has_ws": has_ws,
            "backend_inferred": backend,
            "has_wacz_sibling": has_wacz,
        })
    return arts, skipped


def _yield(root):
    """Per-host draft + candidate counts (capture yield), with gate-ready tally."""
    try:
        import template_inventory as TI  # type: ignore
        scan = TI.scan(root)
    except Exception:  # noqa: BLE001
        return {"available": False}
    per_host = {}
    for sub in ("drafts", "review_candidates"):
        for a in scan["dirs"].get(sub, []):
            h = a["host"] or "?"
            ph = per_host.setdefault(h, {"drafts": 0, "candidates": 0, "gate_ready": 0})
            ph["drafts" if sub == "drafts" else "candidates"] += 1
            if a["promotion_ready"]:
                ph["gate_ready"] += 1
    return {"available": True, "per_host": per_host,
            "hosts": sorted(per_host.keys())}


def analyze(root=".", dirs=None, limit=None):
    dirs = dirs or _DEFAULT_DIRS
    arts, skipped = _artifacts(root, dirs, limit=limit)
    by_host = Counter(a["host"] for a in arts if a["host"])
    json_arts = [a for a in arts if a.get("type") == "capture_json"]
    result = {
        "root": os.path.abspath(root),
        "searched_dirs": dirs,
        "artifacts": {
            "count": len(arts),
            "total_bytes": sum(a["bytes"] for a in arts),
            "by_host": dict(by_host.most_common()),
            "items": arts,
        },
        # G3: aggregate capture-result field set
        "capture_summary": {
            "json_count": len(json_arts),
            "wacz_count": sum(1 for a in arts if a.get("type") == "wacz"),
            "with_dom": sum(1 for a in json_arts if a.get("has_dom")),
            "with_ws": sum(1 for a in json_arts if a.get("has_ws")),
            "with_wacz_sibling": sum(1 for a in json_arts if a.get("has_wacz_sibling")),
            "capture_kinds": dict(Counter(
                a.get("capture_kind") or "unknown" for a in json_arts
            ).most_common()),
        },
        "yield": _yield(root),
    }
    if limit is not None:
        result["bounded"] = True
        result["limit"] = limit
        result["skipped_artifacts"] = skipped
    return result


def _md(a):
    art = a["artifacts"]
    L = ["# Capture analytics", "",
         f"- root: `{a['root']}`",
         f"- searched: {', '.join('`%s`' % d for d in a['searched_dirs'])}",
         f"- artifacts found: **{art['count']}** ({art['total_bytes']} bytes)", ""]
    if art["count"]:
        L += ["## Artifacts by host", ""]
        for h, n in art["by_host"].items():
            L.append(f"- {h}: {n}")
        L.append("")
    else:
        L += ["_No capture artifacts found — expected in a clean sandbox; "
              "captures live on the operator host._", ""]
    y = a["yield"]
    L += ["## Capture yield (drafts + review candidates)", ""]
    if y.get("available") and y["per_host"]:
        for h, c in y["per_host"].items():
            L.append(f"- {h}: drafts={c['drafts']} candidates={c['candidates']} "
                     f"gate_ready={c['gate_ready']}")
    else:
        L.append("- none")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture analytics (read-only).")
    ap.add_argument("--root", default=".")
    ap.add_argument("--captures-dir", action="append", dest="dirs",
                    help="additional dir to search (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--md", metavar="OUT")
    args = ap.parse_args(argv)
    dirs = _DEFAULT_DIRS + (args.dirs or [])
    a = analyze(args.root, dirs)
    if args.md:
        with open(args.md, "w") as fh:
            fh.write(_md(a))
        print(f"wrote {args.md}")
    if args.json:
        print(json.dumps(a, indent=2, default=str))
    elif not args.md:
        sys.stdout.write(_md(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
