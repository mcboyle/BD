#!/usr/bin/env python3
"""P5-2b accuracy watch — does per-site honeypot-threshold learning actually
beat the deterministic global rule, on REAL stash data?

This is a *watch*, not a build: it changes nothing. It is read-only against
the live ``history`` table and reuses the SHIPPED learner functions
(``bulk_downloader.honeypot_threshold``) rather than reimplementing the
quantile/clamp/sample-gate logic, so it can never drift from what the runner
actually does.

Run on stash (the box with the populated DB)::

    cd ~/BulkDownloader
    BD_HONEYPOT_SCORE_THRESHOLD=0.60 python tools/p5_2b_accuracy_watch.py

Set BD_HONEYPOT_SCORE_THRESHOLD to the global drop threshold you are actually
comparing against (the value you'd run in production). If it is unset the
script still runs and reports against a stated assumed default, but the
"would it change decisions" column is only meaningful relative to a real
global number, so pass the real one.

WHAT IT PRINTS, AND WHY EACH PART MATTERS
-----------------------------------------
The learner only produces a site-specific threshold once a site has at least
``DEFAULT_MIN_SAMPLES`` (5) confirmed traps; below that it *abstains* and
returns the global default unchanged. So a site whose learned value equals the
global value may be agreeing OR abstaining, and those are completely different
facts. The report therefore splits every site into one of three buckets:

  FIRED      site cleared the sample gate; learner produced its own number.
             Only these sites can tell you anything about learning quality.
  ABSTAINED  site has 1..4 confirmed traps; learner deliberately fell back to
             the global default. "learned == global" here means "not enough
             evidence yet", NOT "learning agrees".
  NO-DATA    site has 0 confirmed traps; nothing to learn from.

For FIRED sites it additionally answers the only question that actually
matters operationally: *would the learned threshold have changed any real drop
decisions?* A learned 0.62 vs a global 0.60 is behaviourally identical unless
real candidates on that site scored in the (0.60, 0.62] gap. So for each fired
site it counts, over that site's scored history, how many candidates fall in
the band between the global and learned thresholds — i.e. the candidates the
two rules would classify DIFFERENTLY. Zero such candidates = the learner is
numerically different but operationally a no-op on the data so far.

A "confirmed trap" (matching the learner's own definition) is a finished
download (status='done') whose file came back suspiciously small
(0 < file_size < tiny_mb) AND that carries a stamped honeypot_score. Those are
candidates that SLIPPED PAST the scorer and proved to be junk — the right
training signal, but it only exists where the global rule already failed.
"""
from __future__ import annotations

import os
import sys

# Repo-root bootstrap so `bulk_downloader` imports whether run from the repo
# root or from tools/ (mirrors tools/capture_session.py's fix, .52 lesson).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bulk_downloader import honeypot_threshold as ht  # noqa: E402
from bulk_downloader import db as _db  # noqa: E402


def _assumed_global_default() -> tuple[float, bool]:
    """Return (global_threshold, was_explicit). Honours the real env var the
    runner uses; falls back to a stated assumption if unset."""
    raw = os.environ.get("BD_HONEYPOT_SCORE_THRESHOLD", "").strip()
    if raw:
        try:
            v = float(raw)
            if 0.0 < v <= 1.0:
                return v, True
        except ValueError:
            pass
    # Unset: assume a representative mid value purely so the script runs.
    # The decision-change column is only trustworthy with an explicit value.
    return 0.60, False


def _all_site_ids(conn) -> list[str]:
    # Degrade to [] if the history table is missing/unreadable (e.g. an
    # un-migrated or empty DB) — mirrors trap_scores_for_site's contract so
    # the watch reports "no data" rather than throwing a raw traceback when
    # pointed at a box without history yet.
    try:
        rows = conn.execute(
            "SELECT DISTINCT site_id FROM history "
            "WHERE site_id IS NOT NULL AND site_id <> '' "
            "ORDER BY site_id"
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        v = r[0] if not hasattr(r, "keys") else r["site_id"]
        if v:
            out.append(str(v))
    return out


def _scored_candidates_for_site(conn, site_id: str) -> list[float]:
    """ALL stamped honeypot_score values for a site (not just confirmed
    traps). This is the population a drop threshold actually acts on, so it is
    what tells us whether two thresholds differ in PRACTICE."""
    rows = conn.execute(
        "SELECT honeypot_score FROM history "
        "WHERE site_id = ? AND honeypot_score IS NOT NULL",
        [site_id],
    ).fetchall()
    out: list[float] = []
    for r in rows:
        val = r[0] if not hasattr(r, "keys") else r["honeypot_score"]
        try:
            out.append(float(val))
        except (TypeError, ValueError):
            continue
    return out


def main() -> int:
    global_default, explicit = _assumed_global_default()

    print("=" * 70)
    print("P5-2b accuracy watch — per-site learned threshold vs global rule")
    print("=" * 70)
    print(f"Global drop threshold (comparison baseline): {global_default}"
          f"{'' if explicit else '   <-- ASSUMED (BD_HONEYPOT_SCORE_THRESHOLD unset)'}")
    print(f"Learner config: min_samples={ht.DEFAULT_MIN_SAMPLES}, "
          f"quantile={ht.DEFAULT_QUANTILE}, "
          f"clamp=[{ht.THRESHOLD_FLOOR}, {ht.THRESHOLD_CEIL}], "
          f"tiny<{ht.DEFAULT_TINY_MB}MB")
    print(f"Per-site learning currently {'ENABLED' if ht.enabled() else 'OFF'} "
          f"(BD_HONEYPOT_PER_SITE) — this watch reads regardless.")
    print()

    fired: list[tuple] = []
    abstained: list[tuple] = []
    nodata: list[str] = []

    with _db.db_conn() as conn:
        site_ids = _all_site_ids(conn)
        for sid in site_ids:
            traps = ht.trap_scores_for_site(sid, conn=conn)
            n = len(traps)
            if n == 0:
                nodata.append(sid)
                continue
            learned = ht.learn_threshold(traps, default=global_default)
            if n < ht.DEFAULT_MIN_SAMPLES:
                # Learner abstains; learned will equal global_default.
                abstained.append((sid, n, learned))
                continue
            # Fired: count candidates the two thresholds classify differently.
            cands = _scored_candidates_for_site(conn, sid)
            lo, hi = sorted((global_default, learned))
            # Candidates in the (lo, hi] band are dropped by the stricter rule
            # and kept by the looser one — the only decisions that change.
            changed = [c for c in cands if lo < c <= hi]
            fired.append((sid, n, learned, len(cands), len(changed),
                          "stricter" if learned > global_default
                          else "looser" if learned < global_default
                          else "equal"))

    # --- FIRED ----------------------------------------------------------
    print(f"[FIRED]  {len(fired)} site(s) cleared the {ht.DEFAULT_MIN_SAMPLES}"
          f"-sample gate — these are the only sites that judge learning:")
    if not fired:
        print("    (none — no site yet has >=5 confirmed traps; the learner "
              "has not produced a single site-specific threshold. The watch "
              "cannot conclude anything until traps accumulate.)")
    else:
        print(f"    {'site_id':<28} {'traps':>5} {'learned':>8} {'vs global':>10} "
              f"{'scored':>7} {'decisions changed':>18}")
        for sid, n, learned, ncand, nchanged, rel in fired:
            print(f"    {sid:<28} {n:>5} {learned:>8.4f} {rel:>10} "
                  f"{ncand:>7} {nchanged:>18}")
        print()
        moved = [f for f in fired if f[4] > 0]
        if moved:
            print(f"    => {len(moved)} fired site(s) where the learned "
                  f"threshold WOULD change real decisions. THIS is the "
                  f"evidence learning beats the global rule — inspect whether "
                  f"the changed candidates are traps (win) or good media (loss).")
        else:
            print("    => On every fired site, learned and global classify the "
                  "SAME real candidates (no scores in the gap). Learning is "
                  "numerically different but operationally a no-op so far — "
                  "do NOT switch to per-site on this evidence.")

    # --- ABSTAINED ------------------------------------------------------
    print()
    print(f"[ABSTAINED]  {len(abstained)} site(s) have 1..4 traps — learner "
          f"falls back to global ({global_default}). 'learned==global' here is "
          f"ABSTENTION, not agreement:")
    for sid, n, learned in sorted(abstained, key=lambda x: -x[1]):
        print(f"    {sid:<28} traps={n}  -> needs {ht.DEFAULT_MIN_SAMPLES - n} "
              f"more to fire")

    # --- NO-DATA --------------------------------------------------------
    print()
    print(f"[NO-DATA]  {len(nodata)} site(s) have 0 confirmed traps "
          f"(global rule never visibly failed there). Nothing to learn from.")
    if nodata:
        preview = ", ".join(nodata[:12])
        print(f"    {preview}{' ...' if len(nodata) > 12 else ''}")

    # --- VERDICT --------------------------------------------------------
    print()
    print("-" * 70)
    if not fired:
        print("VERDICT: INCONCLUSIVE — learner has fired on 0 sites. Keep "
              "BD_HONEYPOT_PER_SITE OFF; let traps accumulate and re-run.")
    elif any(f[4] > 0 for f in fired):
        print("VERDICT: ACTIONABLE SIGNAL — learner diverges from global on "
              "real candidates for at least one site. Manually confirm the "
              "changed candidates are traps before trusting per-site learning.")
    else:
        print("VERDICT: NO BENEFIT YET — learner fired but changes no real "
              "decisions. Per-site learning is safe but pointless on current "
              "data; stay on the global rule.")
    print("-" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
