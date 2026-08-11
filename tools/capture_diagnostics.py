#!/usr/bin/env python3
"""capture_diagnostics.py — capture readiness diagnostics (A4).

ONE verdict for a single capture: "if I derived a template from this capture,
how complete would it be, how does it drift from the gold, and did the capture
actually exercise the workflow?" — composed entirely from existing engines.

This module invents NO new detection, scoring, or diff logic. It composes:

  * tools/build_template_from_wacz.build_template  — capture(.wacz) -> candidate
        template dict. The candidate is already redaction-scrubbed
        (redact_artifact) before it leaves that builder.
  * tools/template_inventory.assess                — the single 0-100 completeness
        scorer + `promotion_ready` (mirrors the real promote gate). Applied to the
        candidate, this is capture-YIELD completeness.
  * tools/template_drift_report.diff_*             — the section-drift engine the
        A5 keystone reuses (diff candidate vs gold). Shared, not re-implemented.
  * tools/workflow_diagnostic.analyze              — runtime readiness: did the
        capture reach the api host / fetch a manifest / stream segments, plus the
        explicit blind-spot labels. Observation-side, not template-side.
  * tools/replay_validator.validate_replay         — the rrweb replayability
        PRECONDITION (A8): is the capture's dom_log a faithful, replayable
        session at all? Reused, not re-implemented. ids/counts/timestamps only.

The four axes answer four different questions and are reported separately:
  - REPLAY   : is the capture's dom_log even replayable?                 (replay_validator)
  - YIELD    : is the derivable template good enough to promote?         (assess)
  - DRIFT    : does the derivable template differ from the proven gold?  (drift_report)
  - RUNTIME  : did this capture actually exercise the workflow?          (workflow_diagnostic)
REPLAY is a PRECONDITION: a capture whose dom_log cannot be replayed is an
INSUFFICIENT_CAPTURE (recapture) — its yield/drift/runtime cannot be trusted.
The combined `verdict` is an ADVISORY summary over the axes — it never overrides
the real promote gate; `yield.promotion_ready` is the gate mirror.

POSTURE: read-only. Consumes only the already-redacted candidate for YIELD/DRIFT.
For RUNTIME it reports counts + hosts only — never raw manifest/segment URLs,
which can carry path-embedded signing (the Phase-C-pending gap). Nothing here
fetches, replays, signs, writes a template, or surfaces a signing value.

stdlib + project modules only; plain `python3` runs it on stash.

CLI:
    python3 tools/capture_diagnostics.py <capture.wacz> [--gold PATH] [--json]
Exit code: 0 = PROMOTABLE, 1 = REVIEW, 2 = INSUFFICIENT_CAPTURE, 3 = usage/IO error.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
for _p in (str(_ROOT), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_template_from_wacz as _BTW   # type: ignore  # noqa: E402
import template_inventory as _TI          # type: ignore  # noqa: E402
import template_drift_report as _TDR      # type: ignore  # noqa: E402
import workflow_diagnostic as _WD          # type: ignore  # noqa: E402
import replay_validator as _RV            # type: ignore  # noqa: E402

VERDICT_PROMOTABLE = "PROMOTABLE"
VERDICT_REVIEW = "REVIEW"
VERDICT_INSUFFICIENT = "INSUFFICIENT_CAPTURE"
_EXIT = {VERDICT_PROMOTABLE: 0, VERDICT_REVIEW: 1, VERDICT_INSUFFICIENT: 2}


def _host_of(tpl):
    return tpl.get("host") or tpl.get("hostname") or tpl.get("site") or "?"


# ── YIELD: capture-derived template completeness (assess) ────────────────────

def _yield(candidate):
    facts = _TI.assess(candidate, source="capture")
    return {
        "host": facts["host"],
        "completeness_score": facts["completeness_score"],
        "promotion_ready": facts["promotion_ready"],
        "missing": facts["missing"],
        "blocked_terms": facts["blocked_terms"],
        "row_selectors_count": facts["row_selectors_count"],
        "resolutions_count": facts["resolutions_count"],
        "network_patterns_count": facts["network_patterns_count"],
    }


# ── DRIFT: candidate vs gold, via the keystone's shared diff engine ──────────

def _resolve_gold(candidate, gold_path):
    if gold_path:
        return gold_path
    return _TDR._default_gold(candidate)   # host-derived: .bak else live reviewed


def _drift(candidate, gold_path):
    base = _resolve_gold(candidate, gold_path)
    if not base or not Path(base).is_file():
        return {"baseline": None, "total": 0, "lines": ["(no gold baseline — first version)"],
                "have_baseline": False}
    try:
        gold = json.loads(Path(base).read_text("utf-8"))
    except (OSError, ValueError) as e:
        return {"baseline": base, "total": 0, "lines": [f"(gold parse failed: {e})"[:120]],
                "have_baseline": False, "error": str(e)[:120]}
    out: list = []
    total = 0
    # SAME five section diffs the A5 keystone runs (template_drift_report).
    for fn in (_TDR.diff_selectors, _TDR.diff_row_selectors, _TDR.diff_resolutions,
               _TDR.diff_api, _TDR.diff_network_patterns):
        try:
            total += fn(candidate, gold, out)
        except Exception as e:   # a missing section must not crash the diagnostic
            out.append(f"  (diff section {fn.__name__} errored: {e})")
    return {"baseline": base, "total": total, "lines": out, "have_baseline": True}


# ── RUNTIME: did the capture exercise the workflow? (workflow_diagnostic) ─────

def _runtime(cap, gold_dict):
    a = _WD.analyze(cap, gold_dict)
    td = a.get("template_diff", {}) or {}
    obs = dict(td.get("observed_independent", {}) or {})
    # POSTURE: never surface raw manifest URLs (path-signing can survive
    # redaction). Reduce to counts + the distinct host list already in obs.
    manifests = obs.pop("manifests", []) or []
    obs["manifest_count"] = len(manifests)
    return {
        "readiness": td.get("readiness", "unknown"),
        "readiness_reason": td.get("readiness_reason", ""),
        "template_provided": td.get("template_provided", False),
        "missing_steps": td.get("missing_steps", []),
        "observed": obs,                       # counts + hosts only
        "blind_spots": a.get("missing_signals", []),
    }


# ── combined advisory verdict ────────────────────────────────────────────────

def _verdict(y, d, r, rp=None):
    """Advisory summary over the axes. Conservative and transparent — it does
    not override the real promote gate (that is y['promotion_ready']).

    ``rp`` (replay axis, optional) is a PRECONDITION: a capture whose dom_log
    carries replay ERRORS cannot be faithfully replayed, so its derived
    template and observed runtime are not trustworthy → INSUFFICIENT_CAPTURE
    (recapture). Replay WARNINGS do not gate (surfaced on the axis only).
    Passing rp=None preserves the pre-replay three-axis behaviour."""
    # PRECONDITION: un-replayable dom_log → recapture, before judging anything else.
    if rp is not None and rp.get("errors"):
        n = len(rp["errors"])
        return (VERDICT_INSUFFICIENT,
                f"capture dom_log is not replayable ({n} replay error(s): "
                f"{rp['errors'][0]}) — recapture; yield/drift/runtime are untrustworthy")

    yield_ok = bool(y["promotion_ready"])
    runtime_ok = r["readiness"] == "ready"
    drift_clean = (not d["have_baseline"]) or d["total"] == 0

    # Capture itself is deficient → recapture, not review.
    obs = r["observed"]
    exercised = bool(obs.get("manifest_count", 0)) or obs.get("segments", 0) >= 2
    if r["template_provided"] and not runtime_ok and not exercised:
        return (VERDICT_INSUFFICIENT,
                "capture did not exercise the workflow (no manifest / insufficient "
                "segment stream) — recapture before judging the template")

    if yield_ok and runtime_ok and drift_clean:
        reason = ("derivable template passes the promote-gate mirror, capture exercised "
                  "the workflow, and " +
                  ("no gold baseline to drift against" if not d["have_baseline"]
                   else "no drift vs gold"))
        return (VERDICT_PROMOTABLE, reason)

    bits = []
    if not yield_ok:
        bits.append(f"yield not promotable (missing: {', '.join(y['missing']) or 'gate selectors/resolutions'})")
    if d["have_baseline"] and d["total"]:
        bits.append(f"{d['total']} drift point(s) vs gold")
    if r["template_provided"] and not runtime_ok:
        bits.append(f"runtime {r['readiness']} ({', '.join(r['missing_steps']) or 'see steps'})")
    return (VERDICT_REVIEW, "; ".join(bits) or "needs human review")


def diagnose(wacz_path, *, gold_path=None):
    """Compose the three-axis verdict for one capture (.wacz)."""
    p = Path(wacz_path)
    candidate = _BTW.build_template(p)          # already redacted
    cap = _WD.load_capture(p)                    # handles .wacz and .json
    y = _yield(candidate)

    # Load gold once for both drift (path) and runtime (dict) so they agree.
    base = _resolve_gold(candidate, gold_path)
    gold_dict = None
    if base and Path(base).is_file():
        try:
            gold_dict = json.loads(Path(base).read_text("utf-8"))
        except (OSError, ValueError):
            gold_dict = None

    d = _drift(candidate, gold_path)
    r = _runtime(cap, gold_dict)
    rp = _RV.validate_replay(cap)        # ids/counts/timestamps only — posture-safe
    verdict, reason = _verdict(y, d, r, rp)
    return {
        "capture": {
            "host": cap.get("host"), "url_host": _WD._host(cap.get("url") or ""),
            "title": cap.get("title"),
            "network_events": len(cap.get("network_log") or []),
            "dom_events": len(cap.get("dom_log") or []),
            "ws_connections": len(cap.get("websocket_log") or []),
        },
        "replay": rp,
        "yield": y,
        "drift": d,
        "runtime": r,
        "verdict": verdict,
        "verdict_reason": reason,
    }


# ── aggregate over a capture tree (data-layer + CLI --root) ──────────────────

# The bound on how far past its deadline an ISOLATED collect() can answer:
# child kill + reap + result parse, plus discovery and sorting outside the
# loop. Measured well under 1s on test5/test4; 1.5 is the conservative pin.
# tests/test_v3_66_1026_heavy_collectors_bounded_for_real.py asserts
# _HEAVY_BUDGET_S + _KILL_GRACE_S + 1.0 <= _L34_ROUTE_BUDGET_S, the same
# relationship @1023 pinned for the parse-bound collector.
_KILL_GRACE_S = 1.5

_CHILD_SRC = """\
import json, os, sys
for p in sys.argv[2].split(os.pathsep):
    if p not in sys.path:
        sys.path.insert(0, p)
import capture_diagnostics as _CD
out = _CD.diagnose(sys.argv[1])
sys.stdout.write(json.dumps(out))
"""


def _diagnose_isolated(abs_path, timeout_s, work_root):
    """One diagnose() in a child process, killed at timeout_s.

    Returns the diagnose dict; {"error": ...} when the child failed; None
    when it was killed at the deadline. A kill is the point: the in-process
    deadline can only skip BETWEEN files, and a single diagnose is
    uninterruptible regex work that measured 16-38s on real captures.

    The child's cwd is work_root -- collect()'s own root -- because GOLD
    RESOLUTION IS CWD-RELATIVE (template_drift_report probes
    templates/reviewed/{host}.template.json with os.path.exists): a scratch
    cwd here silently blanked drift/runtime for every reviewed host,
    reporting "first version" against golds that exist (review catch,
    pre-merge). What protects the operator's database is BD_INSTALL_DIR
    pointed at a scratch dir -- db._resolve_db_path prefers it over the
    working directory, so the cwd fallback never fires (CLAUDE.md
    section 5). The scratch dir also swallows PYTHONDONTWRITEBYTECODE-
    exempt residue and is removed either way.
    """
    import subprocess
    import tempfile
    here = Path(__file__).resolve().parent          # tools/
    roots = os.pathsep.join([str(here.parent), str(here)])
    scratch = tempfile.mkdtemp(prefix="capdiag_iso_")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)                     # no ambient shadowing
    env["BD_INSTALL_DIR"] = scratch
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        r = subprocess.run(
            [sys.executable, "-c", _CHILD_SRC, str(abs_path), roots],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=str(Path(work_root).resolve()), env=env)
    except subprocess.TimeoutExpired:
        return None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if r.returncode != 0:
        return {"error": (r.stderr or "").strip()[-200:] or
                         f"child exit {r.returncode}"}
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"error": f"unparseable child output ({len(r.stdout)} bytes)"}


def _empty_agg():
    return {"n": 0, "promotable": 0, "review": 0, "insufficient": 0, "errors": 0,
            "replay_failed": 0, "mean_completeness": None}


def _row(rel, dgn):
    y, d, r = dgn["yield"], dgn["drift"], dgn["runtime"]
    rp = dgn.get("replay") or {}
    return {
        "path": rel, "host": y["host"], "verdict": dgn["verdict"],
        "completeness_score": y["completeness_score"],
        "promotion_ready": y["promotion_ready"],
        "drift_total": d["total"] if d["have_baseline"] else None,
        "runtime_readiness": r["readiness"] if r["template_provided"] else "n/a",
        "replay_ok": rp.get("ok"),
        "replay_errors": len(rp.get("errors") or []),
        "missing": y["missing"],
    }


def _aggregate(rows):
    scored = [r for r in rows if r.get("verdict") != "ERROR"]
    scores = [r["completeness_score"] for r in scored
              if isinstance(r.get("completeness_score"), int)]
    cnt = lambda v: sum(1 for r in rows if r.get("verdict") == v)
    replay_failed = sum(1 for r in scored if r.get("replay_ok") is False)
    return {"n": len(scored), "promotable": cnt(VERDICT_PROMOTABLE),
            "review": cnt(VERDICT_REVIEW), "insufficient": cnt(VERDICT_INSUFFICIENT),
            "errors": cnt("ERROR"), "replay_failed": replay_failed,
            "mean_completeness": round(sum(scores) / len(scores), 1) if scores else None}


def collect(root=".", dirs=None, limit=None, budget_s=None, max_bytes=None,
            isolate=False):
    """Run diagnose() over every .wacz under the standard capture dirs and
    summarize. Reuses capture_analytics' discovery so the diagnosed set matches
    the Capture Reports view. Loose capture_*.json artifacts are COUNTED but not
    diagnosed (the template builder reads .wacz only). Read-only; rows carry no
    raw media URLs (the per-capture posture applies). On the operator host this
    returns real rows; in a clean checkout it returns an empty set + a note.

    v3.66.1026: `isolate=True` runs each diagnose in a CHILD PROCESS killed at
    the deadline. The in-process deadline can only skip BETWEEN files, and one
    diagnose of the operator's newest <=25MB .wacz measured 16.1s (the
    3rd-newest: 37.9s at 2.0MB -- size does not predict regex cost), so with
    budget_s=5 the route answered in 17.4s against L34's 8s serial gate. A
    kill bounds the overrun by _KILL_GRACE_S instead of by whatever one file
    costs. Opt-in, because the in-process path is the CLI contract and the
    stub-based batteries monkeypatch diagnose() in THIS interpreter -- a child
    would silently run the real thing (the v3.66.926 harness lesson). The
    data-layer route passes it; nothing else should need to."""
    try:
        import capture_analytics as _CA  # type: ignore
        dirs = dirs or _CA._DEFAULT_DIRS
        arts, _skipped = _CA._artifacts(root, dirs)
    except Exception as e:
        return {"rows": [], "json_captures": 0, "errors": 0, "aggregate": _empty_agg(),
                "dirs": list(dirs or []), "note": f"capture discovery unavailable: {e}"[:160]}
    rows, json_count = [], 0
    # PERF: each diagnose() opens the wacz + builds a full template + runs player
    # recognition -- an unbounded pass over a large capture store is a multi-minute
    # walk (and leaks a temp dir per capture). When `limit` is set, diagnose at
    # most `limit` .wacz NEWEST-FIRST; loose json captures are counted (cheap),
    # never diagnosed. skipped_wacz reports how many were not diagnosed.
    skipped_wacz = 0
    skipped_oversize = 0
    killed_in_flight = 0
    budget_exhausted = False
    # `is not None`, NOT truthiness: budget_s=0 means "no time at all", the
    # semantics capture_analytics._artifacts has had since @1015. The falsy
    # form shipped here meant 0 = UNBOUNDED -- measured >10min on the real
    # store before the probe was killed (v3.66.1026).
    _deadline = (time.monotonic() + budget_s) if budget_s is not None else None
    if limit is not None or _deadline is not None:
        def _mt(rel):
            try:
                return (Path(root) / rel).stat().st_mtime
            except OSError:
                return 0.0
        arts = sorted(arts, key=lambda a: _mt(a.get("path", "")), reverse=True)
    diagnosed = 0
    for a in arts:
        rel = a.get("path", "")
        if not rel.lower().endswith(".wacz"):
            json_count += 1
            continue
        if limit is not None and diagnosed >= limit:
            skipped_wacz += 1
            continue
        if _deadline is not None and time.monotonic() >= _deadline:
            # Time budget spent: count the remainder, never start another
            # (a single diagnose is uninterruptible regex work).
            budget_exhausted = True
            skipped_wacz += 1
            continue
        ap = str(Path(root) / rel)
        if max_bytes is not None:
            try:
                if (Path(root) / rel).stat().st_size > max_bytes:
                    skipped_oversize += 1
                    continue
            except OSError:
                pass
        if isolate and _deadline is not None:
            remaining = _deadline - time.monotonic()
            dgn = _diagnose_isolated(ap, max(remaining, 0.05), root)
            if dgn is None:
                # Killed at the deadline mid-diagnose. Counted in BOTH
                # buckets so the arithmetic reconciles (skipped = not
                # diagnosed) and the kill is separately visible.
                killed_in_flight += 1
                skipped_wacz += 1
                budget_exhausted = True
                continue
            if "error" in dgn and "verdict" not in dgn:
                rows.append({"path": rel, "verdict": "ERROR",
                             "error": str(dgn["error"])[:120]})
            else:
                rows.append(_row(rel, dgn))
        else:
            try:
                rows.append(_row(rel, diagnose(ap)))
            except Exception as e:
                rows.append({"path": rel, "verdict": "ERROR", "error": str(e)[:120]})
        diagnosed += 1
    diagnosed = [r for r in rows if r.get("verdict") != "ERROR"]
    note = ("" if diagnosed else
            "no .wacz captures found under the standard capture dirs "
            "(captures live on the operator host; expected in a clean checkout)")
    return {"rows": rows, "json_captures": json_count,
            "errors": sum(1 for r in rows if r.get("verdict") == "ERROR"),
            "aggregate": _aggregate(rows), "dirs": list(dirs), "note": note,
            "skipped_wacz": skipped_wacz, "skipped_oversize": skipped_oversize,
            "killed_in_flight": killed_in_flight,
            "budget_exhausted": budget_exhausted}


# ── render ───────────────────────────────────────────────────────────────────

def _render_tree(d):
    a = d["aggregate"]
    L = ["=" * 70, "  Capture diagnostics — tree", "=" * 70]
    if d.get("note"):
        L.append(f"  note: {d['note']}")
    for r in d["rows"]:
        if r.get("verdict") == "ERROR":
            L.append(f"  ERROR  {r['path']}  {r.get('error','')}")
        else:
            rp = "" if r.get("replay_ok") is None else (
                " replay=ok" if r["replay_ok"] else f" replay=FAIL({r.get('replay_errors',0)})")
            L.append(f"  {r['verdict']:<20} {r['completeness_score']:>3}/100  "
                     f"drift={r['drift_total']}  runtime={r['runtime_readiness']}"
                     f"{rp}  {r['host']}")
    L.append(f"\n  n={a['n']} promotable={a['promotable']} review={a['review']} "
             f"insufficient={a['insufficient']} replay_failed={a.get('replay_failed',0)} "
             f"errors={a['errors']} mean={a['mean_completeness']}  "
             f"json_captures={d.get('json_captures',0)}")
    L.append("=" * 70)
    return "\n".join(L)


def render_markdown(dgn):
    y, d, r = dgn["yield"], dgn["drift"], dgn["runtime"]
    rp = dgn.get("replay") or {}
    L = ["=" * 70,
         f"  Capture diagnostics — host {y['host']}   VERDICT: {dgn['verdict']}",
         f"  {dgn['verdict_reason']}",
         "=" * 70,
         "",
         f"-- REPLAY (is the dom_log replayable? precondition) --",
         f"  replayable: {rp.get('ok')}   stats: {json.dumps(rp.get('stats', {}), sort_keys=True)}"]
    for e in (rp.get("errors") or []):
        L.append(f"    ! {e}")
    for w in (rp.get("warnings") or []):
        L.append(f"    - {w}")
    L += ["",
          f"-- YIELD (derivable-template completeness) --",
         f"  score: {y['completeness_score']}/100   promotion_ready: {y['promotion_ready']}",
         f"  missing: {y['missing'] or 'none'}",
         f"  rows={y['row_selectors_count']} resolutions={y['resolutions_count']} "
         f"network_patterns={y['network_patterns_count']}"
         + (f"   BLOCKED TERMS: {y['blocked_terms']}" if y['blocked_terms'] else ""),
         "",
         f"-- DRIFT (candidate vs gold) --"]
    if not d["have_baseline"]:
        L.append(f"  {d['lines'][0]}")
    else:
        L.append(f"  baseline: {d['baseline']}   drift points: {d['total']}")
        L.extend(d["lines"])
    L += ["",
          f"-- RUNTIME (did the capture exercise the workflow?) --",
          f"  readiness: {r['readiness']}  ({r['readiness_reason']})"
          if r["template_provided"] else
          f"  readiness: n/a — no gold template to compare runtime against",
          f"  observed: {json.dumps(r['observed'], sort_keys=True)}",
          f"  blind spots ({len(r['blind_spots'])}):"]
    L.extend(f"    - {b}" for b in r["blind_spots"])
    L.append("=" * 70)
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capture readiness diagnostics (A4).")
    ap.add_argument("capture", nargs="?", help="path to a capture .wacz (or capture .json)")
    ap.add_argument("--root", help="aggregate: diagnose every .wacz under ROOT's standard capture dirs")
    ap.add_argument("--gold", help="gold template path (default: host-derived .bak/live reviewed)")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of the report")
    args = ap.parse_args(argv)
    if args.root:
        tree = collect(args.root)
        print(json.dumps(tree, indent=2) if args.json else _render_tree(tree))
        return 0
    if not args.capture:
        ap.error("give a capture path or --root")
    try:
        dgn = diagnose(args.capture, gold_path=args.gold)
    except (OSError, ValueError, SystemExit) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(dgn, indent=2))
    else:
        print(render_markdown(dgn))
    return _EXIT.get(dgn["verdict"], 1)


if __name__ == "__main__":
    sys.exit(main())
