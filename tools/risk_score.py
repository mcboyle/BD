#!/usr/bin/env python3
"""risk_score -- composite per-file risk + batch re-ranking.

Composite (SCHEMAS §3, extended): for each production file,
  risk = w_cc*norm(max_CC) + w_sink*sink_weight + w_secret*secret_density
       + w_taint*taint_proxy + w_prior*prior_defect_proximity
radon supplies cyclomatic complexity (invoked from the ~/rev venv, offline).
prior_defect_proximity boosts files that carried a confirmed VR-P/F0001 defect
(recently-buggy code is higher prior risk -- the catalog's own signal).

Emits RISK_SCORES.json (per-file) and re-ranks the audit partition batches by
measured risk instead of SLOC proxy -> BATCH_ORDER.json + a readable summary.

Usage: python3 risk_score.py [--db DB] [--root TREE] [--manifests DIR]
                              [--radon ~/rev/bin/radon] [--outdir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TypeAlias


JsonValue: TypeAlias = (
    bool | int | float | str | None | list["JsonValue"] | dict[str, "JsonValue"]
)

SCHEMA = 1
DEFAULT_ROOT = os.environ.get("BD_WORK", os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
REVIEW = os.environ.get("BD_REVIEW_ROOT", os.path.join(DEFAULT_ROOT, "review"))

# Files that carried a confirmed defect (VERIFY_MATRIX 520 + F0001) -> prior risk.
PRIOR_DEFECT_SUBSTR = [
    "notify_apprise", "capture_artifact_redact", "app_jsonapi", "app_library",
    "library_final", "runner.py", "site_editor", "app.py", "batch_ops",
    "extraction_core", "build_template_from_wacz", "provider_resolve",
    "cockpit_console", "watch_folder", "scrub",
]

# sink kinds weighted by exploitability
SINK_W = {"sql_fstring": 5, "subprocess": 4, "sql": 2, "path": 2,
          "fetch": 2, "redaction": 3}
WEIGHTS = {"cc": 0.30, "sink": 0.25, "secret": 0.15, "taint": 0.15, "prior": 0.15}


def radon_cc(radon, root, files):
    """Return {relpath: max_cc} via `radon cc -j`. Batched to keep argv sane."""
    out = {}
    abs_py = [os.path.join(root, f) for f in files if f.endswith(".py")]
    for i in range(0, len(abs_py), 200):
        chunk = abs_py[i:i + 200]
        try:
            r = subprocess.run([radon, "cc", "-j", *chunk],
                               capture_output=True, text=True, timeout=300)
            data = json.loads(r.stdout or "{}")
        except Exception as e:
            print(f"  radon batch error: {e}", file=sys.stderr)
            continue
        for ap, blocks in data.items():
            rel = os.path.relpath(ap, root)
            mx = 0
            if isinstance(blocks, list):
                for b in blocks:
                    if isinstance(b, dict) and isinstance(b.get("complexity"), int):
                        mx = max(mx, b["complexity"])
            out[rel] = mx
    return out


def load_facts(db):
    c = sqlite3.connect(db)
    sink_w = defaultdict(int)
    secret_n = defaultdict(int)
    lines = {}
    for path, mj, ln in c.execute(
            "SELECT path,meta_json,lines FROM nodes WHERE kind='module'"):
        lines[path] = ln or 0
    for path, mj in c.execute(
            "SELECT path,meta_json FROM nodes WHERE kind='function'"):
        meta = json.loads(mj)
        for s in meta.get("sinks", []):
            sink_w[path] += SINK_W.get(s["kind"], 1)
        secret_n[path] += len(meta.get("secrets", []))
    c.close()
    return sink_w, secret_n, lines


def prior(path):
    return 1.0 if any(s in path for s in PRIOR_DEFECT_SUBSTR) else 0.0


def norm(v, lo, hi):
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def score_from_reports(
    *,
    graph_path: Path,
    radon_report: Mapping[str, JsonValue] | None,
) -> dict[str, dict[str, JsonValue]]:
    """Return deterministic per-module risk from already-loaded reports."""
    sink_weight, secret_count, lines = load_facts(str(graph_path))
    complexity = {}
    for path, blocks in (radon_report or {}).items():
        complexity[path] = max(
            (
                int(block.get("complexity", 0))
                for block in blocks
                if isinstance(block, dict)
            ),
            default=0,
        )
    max_cc = max(complexity.values(), default=1)
    max_sink = max(sink_weight.values(), default=1)
    result = {}
    for path in sorted(lines):
        line_count = max(1, int(lines[path]))
        parts = {
            "complexity": norm(complexity.get(path, 0), 0, max_cc),
            "sink": norm(sink_weight.get(path, 0), 0, max_sink),
            "secret": norm(
                secret_count.get(path, 0) / line_count * 100, 0, 5
            ),
            "taint_proxy": norm(
                sink_weight.get(path, 0) / line_count * 100, 0, 10
            ),
            "prior_defect": prior(path),
        }
        value = round(
            WEIGHTS["cc"] * parts["complexity"]
            + WEIGHTS["sink"] * parts["sink"]
            + WEIGHTS["secret"] * parts["secret"]
            + WEIGHTS["taint"] * parts["taint_proxy"]
            + WEIGHTS["prior"] * parts["prior_defect"],
            4,
        )
        result[path] = {
            "score": value,
            "complexity_max": complexity.get(path, 0),
            "sink_weight": sink_weight.get(path, 0),
            "secret_count": secret_count.get(path, 0),
            "components": parts,
        }
    return result


def score(db, root, radon):
    sink_w, secret_n, lines = load_facts(db)
    files = sorted(lines.keys())
    cc = radon_cc(radon, root, files)
    max_cc = max(cc.values()) if cc else 1
    max_sink = max(sink_w.values()) if sink_w else 1
    scores = {}
    for f in files:
        ln = max(1, lines.get(f, 1))
        s_cc = norm(cc.get(f, 0), 0, max_cc)
        s_sink = norm(sink_w.get(f, 0), 0, max_sink)
        s_secret = norm(secret_n.get(f, 0) / ln * 100, 0, 5)  # secret density / 100 LOC
        s_taint = norm(sink_w.get(f, 0) / ln * 100, 0, 10)    # sink density proxy
        s_prior = prior(f)
        risk = round(WEIGHTS["cc"] * s_cc + WEIGHTS["sink"] * s_sink
                     + WEIGHTS["secret"] * s_secret + WEIGHTS["taint"] * s_taint
                     + WEIGHTS["prior"] * s_prior, 4)
        scores[f] = {"risk": risk, "max_cc": cc.get(f, 0), "sink_weight": sink_w.get(f, 0),
                     "secrets": secret_n.get(f, 0), "lines": ln, "prior_defect": s_prior}
    return scores


def rerank(scores, manifests_dir):
    """Re-rank batches by mean file risk; re-order files within each batch."""
    batches = {}
    for fn in sorted(os.listdir(manifests_dir)):
        if not fn.endswith(".txt"):
            continue
        batch = fn[:-4]
        files = [l.strip() for l in open(os.path.join(manifests_dir, fn))
                 if l.strip()]
        ranked = sorted(files, key=lambda f: scores.get(f, {}).get("risk", 0),
                        reverse=True)
        risks = [scores.get(f, {}).get("risk", 0) for f in files]
        mean = round(sum(risks) / len(risks), 4) if risks else 0
        mx = round(max(risks), 4) if risks else 0
        batches[batch] = {"mean_risk": mean, "max_risk": mx, "files": ranked,
                          "n": len(files)}
    order = sorted(batches.keys(),
                   key=lambda b: (batches[b]["mean_risk"], batches[b]["max_risk"]),
                   reverse=True)
    return order, batches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(REVIEW, "artifacts", "KNOWLEDGE_GRAPH.db"))
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--manifests", default=os.path.join(REVIEW, "audit_manifests"))
    ap.add_argument("--radon", default=os.path.expanduser("~/rev/bin/radon"))
    ap.add_argument("--outdir", default=os.path.join(REVIEW, "artifacts"))
    a = ap.parse_args()
    scores = score(a.db, a.root, a.radon)
    with open(os.path.join(a.outdir, "RISK_SCORES.json"), "w") as f:
        json.dump({"schema": SCHEMA, "weights": WEIGHTS, "scores": scores},
                  f, indent=2, sort_keys=True)
    order, batches = rerank(scores, a.manifests)
    with open(os.path.join(a.outdir, "BATCH_ORDER.json"), "w") as f:
        json.dump({"schema": SCHEMA, "order": order, "batches": batches},
                  f, indent=2, sort_keys=True)
    print("risk_score:", json.dumps({"files_scored": len(scores),
          "batches": len(order),
          "top10_files": [k for k, _ in sorted(
              scores.items(), key=lambda kv: kv[1]["risk"], reverse=True)[:10]]}))
    print("\nTOP 12 BATCHES BY MEASURED RISK:")
    for b in order[:12]:
        print(f"  {b:16s} mean={batches[b]['mean_risk']:.3f} "
              f"max={batches[b]['max_risk']:.3f} n={batches[b]['n']}")


if __name__ == "__main__":
    main()
