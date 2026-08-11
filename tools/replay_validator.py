#!/usr/bin/env python3
"""replay_validator.py — headless rrweb replayability validation (A8).

Answers a question the rest of the capture ecosystem ASSUMES but never checks:
"is this capture's DOM event log a faithful, replayable rrweb session?" A
capture can ingest, score, and correlate against a template (A4 / diff_template
/ workflow_diagnostic) while its dom_log is silently un-replayable — the exact
failure class as the rrweb-bundle ASI bug, just on the recorded side. This is
the precondition validator for the replay ecosystem.

It validates the rrweb playback invariants WITHOUT a browser and WITHOUT
rendering anything:
  * a Meta event is present (replayer needs viewport/url first)         [warn]
  * a FullSnapshot exists and precedes every IncrementalSnapshot        [error]
  * the FullSnapshot carries a non-empty serialized node tree           [error]
  * timestamps (and dom_seq) are monotonic non-decreasing               [error]
  * node-id integrity, walked in order against a live id-set:
      - incremental `adds` reference a parentId that is live            [warn]
      - `removes` / text / attribute mutations reference a live id      [warn]

POSTURE: redaction-safe by construction. It inspects only numeric node ids,
event types, sources, and timestamps — never node text, attribute values, or
URLs. It therefore runs over scrubbed captures (the A8 rule: viewer/validation
over scrubbed captures only), fetches nothing, and surfaces no values.

stdlib-only; browser-free; plain `python3`.

CLI:
    python3 tools/replay_validator.py <capture.wacz|.json> [--json]
    python3 tools/replay_validator.py --root . [--json]
Exit: 0 = replayable (no errors), 1 = errors found, 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import workflow_diagnostic as _WD   # type: ignore  # noqa: E402  (load_capture: .wacz/.json)


def _collect_ids(node, out):
    """Recursively collect rrweb node ids from a serialized node subtree."""
    if not isinstance(node, dict):
        return
    nid = node.get("id")
    if isinstance(nid, int):
        out.add(nid)
    for child in (node.get("childNodes") or []):
        _collect_ids(child, out)


def validate_replay(cap):
    """Validate the rrweb replayability invariants of a capture's dom_log.
    Returns {ok, errors[], warnings[], stats{}}. Pure; no IO; no rendering."""
    errors, warnings = [], []
    log = [e for e in (cap.get("dom_log") or []) if isinstance(e, dict)]
    stats = {"events": len(log), "meta": 0, "full_snapshots": 0,
             "incrementals": 0, "dangling_parent_adds": 0, "dangling_ref_ops": 0,
             "max_node_id": None}

    if not log:
        return {"ok": False, "errors": ["empty dom_log — nothing to replay"],
                "warnings": [], "stats": stats}

    # monotonic timestamps / dom_seq
    last_ts, last_seq = None, None
    for i, ev in enumerate(log):
        ts = ev.get("timestamp")
        if isinstance(ts, (int, float)):
            if last_ts is not None and ts < last_ts:
                errors.append(f"timestamp regresses at event {i} ({ts} < {last_ts})")
            last_ts = ts
        seq = ev.get("dom_seq")
        if isinstance(seq, int):
            if last_seq is not None and seq < last_seq:
                errors.append(f"dom_seq regresses at event {i} ({seq} < {last_seq})")
            last_seq = seq

    # snapshot ordering + node-id integrity, walked in order
    live = set()
    seen_full = False
    for i, ev in enumerate(log):
        etype = ev.get("type")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if etype == "meta":
            stats["meta"] += 1
            continue
        if etype == "full_snapshot":
            stats["full_snapshots"] += 1
            node = data.get("node")
            if not isinstance(node, dict) or not node:
                errors.append(f"full_snapshot at event {i} has no serialized node tree")
            else:
                ids = set()
                _collect_ids(node, ids)
                if not ids:
                    errors.append(f"full_snapshot at event {i} has a node tree with no ids")
                live |= ids
            seen_full = True
            continue
        if etype == "incremental":
            stats["incrementals"] += 1
            if not seen_full:
                errors.append(f"incremental at event {i} precedes any full_snapshot "
                              "(replayer has no base DOM)")
            # adds: parentId must be live; the added subtree's ids become live
            for a in (data.get("adds") or []):
                if not isinstance(a, dict):
                    continue
                pid = a.get("parentId")
                if isinstance(pid, int) and pid not in live:
                    stats["dangling_parent_adds"] += 1
                add_ids = set()
                _collect_ids(a.get("node"), add_ids)
                live |= add_ids
            # removes / text / attribute mutations: id must be live
            for key in ("removes", "texts", "attributes"):
                for op in (data.get(key) or []):
                    if isinstance(op, dict):
                        rid = op.get("id")
                        if isinstance(rid, int) and rid not in live:
                            stats["dangling_ref_ops"] += 1
                        elif key == "removes" and isinstance(rid, int):
                            live.discard(rid)

    if not seen_full:
        errors.append("no full_snapshot in dom_log — capture cannot be replayed")
    if stats["meta"] == 0:
        warnings.append("no Meta event — viewport/navigation unknown; replayer may "
                        "mis-size or miss SPA route changes")
    if stats["dangling_parent_adds"]:
        warnings.append(f"{stats['dangling_parent_adds']} incremental add(s) reference a "
                        "parent id not present at that point (partial/truncated capture)")
    if stats["dangling_ref_ops"]:
        warnings.append(f"{stats['dangling_ref_ops']} mutation op(s) reference an id not "
                        "live at that point (out-of-order or dropped events)")
    stats["max_node_id"] = max(live) if live else None

    return {"ok": not errors, "errors": errors, "warnings": warnings, "stats": stats}


# ── aggregate over a capture tree ────────────────────────────────────────────

def collect(root=".", dirs=None, limit=None, budget_s=None, max_bytes=None):
    """Validate replayability of every .wacz under the standard capture dirs.
    Reuses capture_analytics' discovery (matches the Capture Reports set).
    Read-only; carries ids/counts only (posture-safe)."""
    try:
        import capture_analytics as _CA  # type: ignore
        dirs = dirs or _CA._DEFAULT_DIRS
        arts, _skipped = _CA._artifacts(root, dirs)
    except Exception as e:
        return {"rows": [], "note": f"capture discovery unavailable: {e}"[:160],
                "dirs": list(dirs or [])}
    rows = []
    # PERF: load_capture opens each wacz -- an unbounded pass over a large capture
    # store is a multi-minute walk. When `limit` is set, validate at most `limit`
    # .wacz NEWEST-FIRST; skipped_wacz reports how many were not validated.
    skipped_wacz = 0
    skipped_oversize = 0
    budget_exhausted = False
    # `is not None`, NOT truthiness: budget_s=0 means "no time at all"
    # (capture_analytics' semantics since @1015). The falsy form here meant
    # 0 = UNBOUNDED (v3.66.1026, measured on the real store).
    _deadline = (time.monotonic() + budget_s) if budget_s is not None else None
    if limit is not None or _deadline is not None:
        def _mt(rel):
            try:
                return (Path(root) / rel).stat().st_mtime
            except OSError:
                return 0.0
        arts = sorted(arts, key=lambda a: _mt(a.get("path", "")), reverse=True)
    validated = 0
    for a in arts:
        rel = a.get("path", "")
        if not rel.lower().endswith(".wacz"):
            continue
        if limit is not None and validated >= limit:
            skipped_wacz += 1
            continue
        if _deadline is not None and time.monotonic() >= _deadline:
            budget_exhausted = True
            skipped_wacz += 1
            continue
        if max_bytes is not None:
            try:
                if (Path(root) / rel).stat().st_size > max_bytes:
                    skipped_oversize += 1
                    continue
            except OSError:
                pass
        try:
            cap = _WD.load_capture(Path(root) / rel)
            v = validate_replay(cap)
            rows.append({"path": rel, "ok": v["ok"], "errors": len(v["errors"]),
                         "warnings": len(v["warnings"]), "events": v["stats"]["events"]})
        except Exception as e:
            rows.append({"path": rel, "ok": False, "error": str(e)[:120]})
        validated += 1
    note = "" if rows else ("no .wacz captures under the standard dirs "
                            "(captures live on the operator host)")
    return {"rows": rows, "dirs": list(dirs), "note": note,
            "skipped_wacz": skipped_wacz, "skipped_oversize": skipped_oversize,
            "budget_exhausted": budget_exhausted}


def render_markdown(v):
    L = ["=" * 64, f"  rrweb replay validation — {'REPLAYABLE' if v['ok'] else 'NOT REPLAYABLE'}",
         "=" * 64, f"  stats: {json.dumps(v['stats'], sort_keys=True)}"]
    if v["errors"]:
        L.append(f"  errors ({len(v['errors'])}):")
        L += [f"    ! {e}" for e in v["errors"]]
    if v["warnings"]:
        L.append(f"  warnings ({len(v['warnings'])}):")
        L += [f"    - {w}" for w in v["warnings"]]
    if not v["errors"] and not v["warnings"]:
        L.append("  clean — no errors or warnings")
    L.append("=" * 64)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Headless rrweb replayability validator (A8).")
    ap.add_argument("capture", nargs="?", help="path to a capture .wacz (or .json)")
    ap.add_argument("--root", help="aggregate: validate every .wacz under ROOT's capture dirs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.root:
        tree = collect(args.root)
        print(json.dumps(tree, indent=2) if args.json else
              "\n".join(f"{'ok ' if r.get('ok') else 'ERR'} {r['path']} "
                        f"(err={r.get('errors','?')} warn={r.get('warnings','?')})"
                        for r in tree["rows"]) or (tree.get("note") or "(no captures)"))
        return 0
    if not args.capture:
        ap.error("give a capture path or --root")
    try:
        cap = _WD.load_capture(Path(args.capture))
    except (OSError, ValueError, SystemExit) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    v = validate_replay(cap)
    print(json.dumps(v, indent=2) if args.json else render_markdown(v))
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
