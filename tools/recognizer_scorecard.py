#!/usr/bin/env python3
"""recognizer_scorecard.py — player-family recognition scorecard (A2).

The recognizer (`player_recognition.detect` + the 169+ brand families in
`player_families`) emits a `player_family` for any page. Its 36 candidate
families carry CANDIDATE-grade signatures — "verify against the capture corpus"
is written on the box. This tool is the measurement half: run the recognizer
over a LABELLED corpus of scrubbed captures and report per-family precision /
recall / F1 + a confusion matrix, so signature drift and family collisions are
quantified instead of guessed.

It is DATA-driven, not code-blocked: the harness + math are complete and proven
on synthetic fixtures here; the numbers it reports are only as meaningful as the
corpus it is pointed at. Today that corpus is ~1 real candidate-grade capture —
so this exists to make measurement a single command as the corpus accretes, not
to claim a precision/recall figure off n=1.

CORPUS UNIT (one labelled entry — already redaction-scrubbed):
    {
      "id": "rep171_theoplayer",        # stable label for the entry
      "expected_family": "theoplayer",  # ground truth (or "unknown")
      "inputs": {                       # the recognizer's signal inputs ONLY
        "html": "...scrubbed class/id/custom-element markup...",
        "script_srcs": ["...", ...],    # optional
        "iframe_hosts": ["...", ...],   # optional
        "network": [ {"url": "...", "type": "...", ...}, ... ],  # optional, scrubbed
        "storage_keys": ["THEOplayer.x", ...]                    # NAMES only, optional
      },
      "expected_delivery": "hls",       # optional aux label
      "expected_drm": false,            # optional aux label
      "expected_ad": false,             # optional aux label
      "source_note": "..."              # optional provenance prose
    }

A corpus is either a single JSON file ({"entries":[...]} or a bare list) or a
directory of per-entry *.json files. Loose nesting under the dir is walked.

POSTURE: recognition-only / read-only. The corpus entries are SCRUBBED signal
sets (the A2/A8 rule: scrubbed captures only). The scorecard reports labels,
predicted-vs-expected, and counts — it NEVER echoes entry `inputs` (no html,
script srcs, urls, or storage values) into its output. Synthetic fixtures only
in-tree; real scrubbed captures stay on the operator host.

stdlib + project modules; browser-free; plain `python3`.

CLI:
    python3 tools/recognizer_scorecard.py --corpus PATH [--json]
Exit: 0 = scored (or empty corpus), 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import player_recognition as _PR   # type: ignore  # noqa: E402


_INPUT_KEYS = ("html", "script_srcs", "iframe_hosts", "network", "storage_keys")


# ── corpus loading ───────────────────────────────────────────────────────────

def _coerce_entries(obj):
    if isinstance(obj, dict) and isinstance(obj.get("entries"), list):
        return obj["entries"]
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and "expected_family" in obj:
        return [obj]            # a single bare entry
    return []


def load_corpus(path):
    """Load labelled entries from a JSON file or a directory of *.json files.
    Returns a list of entry dicts. Missing path → empty list (caller decides)."""
    p = Path(path)
    if not p.exists():
        return []
    if p.is_file():
        return _coerce_entries(json.loads(p.read_text("utf-8")))
    entries = []
    for f in sorted(p.rglob("*.json")):
        try:
            entries.extend(_coerce_entries(json.loads(f.read_text("utf-8"))))
        except (OSError, ValueError):
            continue
    return entries


# ── scoring one entry ────────────────────────────────────────────────────────

def predict(entry):
    """Run the recognizer on one entry's scrubbed inputs. Returns the detect()
    result dict. Only the documented signal keys are forwarded."""
    raw = entry.get("inputs") or {}
    kw = {k: raw[k] for k in _INPUT_KEYS if k in raw and k != "html"}
    return _PR.detect(raw.get("html", "") or "", **kw)


def score_entry(entry):
    """Compare the prediction to the ground-truth label(s). Returns a compact
    record carrying labels + booleans ONLY — never the entry inputs."""
    res = predict(entry)
    pred = res.get("player_family", "unknown")
    exp = entry.get("expected_family", "unknown")
    rec = {
        "id": entry.get("id", "?"),
        "expected": exp,
        "predicted": pred,
        "correct": pred == exp,
        "top_score": (res.get("candidates") or [{}])[0].get("score"),
    }
    # optional aux labels — only scored when present on the entry
    if "expected_delivery" in entry:
        rec["delivery_expected"] = entry["expected_delivery"]
        rec["delivery_predicted"] = res.get("delivery")
        rec["delivery_correct"] = res.get("delivery") == entry["expected_delivery"]
    if "expected_drm" in entry:
        rec["drm_correct"] = bool(res.get("flags", {}).get("drm")) == bool(entry["expected_drm"])
    if "expected_ad" in entry:
        rec["ad_correct"] = bool(res.get("flags", {}).get("ad_overlay")) == bool(entry["expected_ad"])
    return rec


# ── corpus-level metrics ──────────────────────────────────────────────────────

def _prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else None
    r = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * p * r / (p + r)) if (p and r) else (0.0 if (p == 0 or r == 0) else None)
    rnd = lambda x: round(x, 3) if isinstance(x, float) else x
    return {"precision": rnd(p), "recall": rnd(r), "f1": rnd(f1), "tp": tp, "fp": fp, "fn": fn}


def score_corpus(entries):
    """Score every entry and aggregate per-family P/R/F1 + a confusion matrix.
    Pure; posture-safe (labels/counts only)."""
    rows = [score_entry(e) for e in entries]
    n = len(rows)
    labels = sorted({r["expected"] for r in rows} | {r["predicted"] for r in rows})

    # confusion[expected][predicted] = count
    confusion = {e: {} for e in sorted({r["expected"] for r in rows})}
    for r in rows:
        d = confusion.setdefault(r["expected"], {})
        d[r["predicted"]] = d.get(r["predicted"], 0) + 1

    per_family = {}
    for fam in labels:
        tp = sum(1 for r in rows if r["expected"] == fam and r["predicted"] == fam)
        fp = sum(1 for r in rows if r["predicted"] == fam and r["expected"] != fam)
        fn = sum(1 for r in rows if r["expected"] == fam and r["predicted"] != fam)
        support = sum(1 for r in rows if r["expected"] == fam)
        if support == 0 and tp == 0 and fp == 0:
            continue
        per_family[fam] = {**_prf(tp, fp, fn), "support": support}

    correct = sum(1 for r in rows if r["correct"])
    aux = {}
    for key in ("delivery_correct", "drm_correct", "ad_correct"):
        scored = [r for r in rows if key in r]
        if scored:
            aux[key.replace("_correct", "")] = {
                "scored": len(scored),
                "accuracy": round(sum(1 for r in scored if r[key]) / len(scored), 3),
            }
    return {
        "n": n,
        "accuracy": round(correct / n, 3) if n else None,
        "correct": correct,
        "unknown_predicted": sum(1 for r in rows if r["predicted"] == "unknown"),
        "registered_families": len(_PR.FAMILIES),
        "per_family": per_family,
        "aux": aux,
        "confusion": confusion,
        "rows": rows,
    }


def report(corpus_path):
    """Load + score a corpus path; attach a note when the corpus is too thin
    to mean anything (the honest n=1 caveat)."""
    entries = load_corpus(corpus_path)
    # ensure the brand pack is registered before reporting registered_families
    try:
        import player_families
        player_families.ensure_registered()
    except Exception:
        pass
    sc = score_corpus(entries)
    if sc["n"] == 0:
        sc["note"] = (f"empty/absent corpus at {corpus_path} — nothing to score; "
                      f"{sc['registered_families']} families registered, measurement pending")
    elif sc["n"] < 5:
        sc["note"] = (f"corpus is n={sc['n']} — candidate-grade only; per-family "
                      "precision/recall is not statistically meaningful yet")
    else:
        sc["note"] = ""
    return sc


# ── render ─────────────────────────────────────────────────────────────────--

def render_markdown(sc):
    L = ["=" * 70, "  Recognizer scorecard (A2)", "=" * 70]
    if sc.get("note"):
        L.append(f"  note: {sc['note']}")
    L.append(f"  n={sc['n']}  accuracy={sc['accuracy']}  correct={sc['correct']}  "
             f"unknown_predicted={sc['unknown_predicted']}  "
             f"families_registered={sc['registered_families']}")
    if sc.get("aux"):
        L.append(f"  aux: {json.dumps(sc['aux'], sort_keys=True)}")
    if sc["per_family"]:
        L.append("")
        L.append(f"  {'family':<22}{'P':>7}{'R':>7}{'F1':>7}{'sup':>5}")
        for fam, m in sorted(sc["per_family"].items(),
                             key=lambda kv: (-(kv[1]["support"]), kv[0])):
            L.append(f"  {fam:<22}{str(m['precision']):>7}{str(m['recall']):>7}"
                     f"{str(m['f1']):>7}{m['support']:>5}")
    # confusion: only print rows that actually have a mislabel, to stay terse
    mis = {e: d for e, d in sc["confusion"].items()
           if any(p != e for p in d)}
    if mis:
        L.append("")
        L.append("  confusion (expected -> predicted, mislabels only):")
        for e, d in sorted(mis.items()):
            for p, c in sorted(d.items(), key=lambda kv: -kv[1]):
                if p != e:
                    L.append(f"    {e} -> {p}: {c}")
    L.append("=" * 70)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Player-family recognition scorecard (A2).")
    ap.add_argument("--corpus", required=True,
                    help="path to a labelled corpus (JSON file or directory of *.json entries)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the report")
    args = ap.parse_args(argv)
    try:
        sc = report(args.corpus)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    # attach aux into the dict for the renderer (kept out of score_corpus core)
    if args.json:
        print(json.dumps(sc, indent=2, sort_keys=True))
    else:
        print(render_markdown(sc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
