# Phases 13-18 + cross-cutting views — final deliverables

Trust-improvement layers over Phases 1-12, all in one tool (`tools/trust_intelligence.py`)
with nine subcommands. The objective was not more automation — it was making the framework
better at answering how sure it is, why, when to stop being sure, what evidence matters,
what to review, and what will fail next. Every subcommand consumes existing artifacts,
produces reviewable data, fails closed on a posture scan, and changes nothing.

## 1. Implementation summary

- **Phase 13 `calibrate`** — compares predicted confidence to observed success (from an
  outcomes file and/or the live `selector_drift` signal). Reports calibration error, Brier
  score, over/under-confidence rates, and reliability by subsystem. Answers "when confidence
  says 0.80, how often is it actually right?" Outputs: `confidence_calibration.json`,
  `confidence_accuracy_report.md`, `overconfidence_report.md`, `underconfidence_report.md`.
- **Phase 14 `regress`** — compares two snapshots of a site's verdict-bearing artifacts and
  flags every changed conclusion (maturity, selector trust, drift classification) for a human
  to judge expected-vs-regression. Regression testing for conclusions, not code. Outputs:
  `verdict_regression_report.md`, `verdict_drift_history.json`, `verdict_change_queue.json`.
- **Phase 15 `capquality`** — scores a capture's evidence quality (DOM completeness, workflow
  completeness, selector coverage, rendition coverage, goal quality) into Excellent / Good /
  Usable / Weak / Discard, BEFORE it feeds learning. A no-goal capture is Discard. Outputs:
  `capture_quality.json`, `capture_quality_report.md`.
- **Phase 16 `forecast`** — estimates probability of future drift, of entering Fragile/Broken,
  and time-to-fragile, from the drift/confidence/health trajectory. Forecast only. Outputs:
  `drift_forecast.json`, `site_risk_forecast.md`.
- **Phase 17 `simulate`** — previews what approving the queue WOULD change (selectors promoted/
  distrusted, profile fields, projected maturity) so a maintainer can decide before approving.
  Applies nothing. Outputs: `policy_impact_report.md`, `approval_simulation.json`.
- **Phase 18 `freshness`** — ages each evidence type (Fresh / Aging / Stale / Expired) and
  flags stale evidence for review priority — never changes trust automatically. Outputs:
  `evidence_freshness.json`, `evidence_aging_report.md`.
- **Cross-cut `failures`** — classifies observed drift into failure types (Selector / Workflow
  / Login / Session / Rendition / Infrastructure / Unknown). Output:
  `failure_intelligence_report.md`.
- **Cross-cut `impact`** — rates each observation Low / Medium / High / Critical by downstream
  effect (structural drift = Critical), focusing operator attention. Outputs:
  `evidence_impact.json`, `evidence_impact_report.md`.
- **Cross-cut `family`** — extends Phase 12: per-family drift rate, avg health, maturity
  distribution. For confidence/analysis only. Outputs: `family_health_report.md`,
  `family_drift_report.md`.

Reuse: `selector_drift.status_for` (live outcome signal for calibration/failures), Phase
12 `site_family_membership.json` (family aggregation), all Phase 1-12 histories and profiles.
No parallel systems.

## 2. Validation and testing summary

`test_phases1318.py` runs all nine end-to-end and proves the substantive behaviors:
calibration detects both overconfidence (0.9 predicted / 50% observed) and underconfidence
(0.5 / 100%); regression flags maturity, selector, and drift changes; capquality scores a
good capture Good and a no-goal capture Discard; forecast yields high break-probability with
declining confidence; simulate previews promotions and a Watch→Stable projection without
applying it; 120-day-old evidence ages to Expired; failures classify selector+workflow;
impact rates structural drift Critical; family aggregation groups the JWPlayer family. One
real bug was caught and fixed (the maturity-projection condition was too strict). All pass.
The 254 existing engine tests still pass — the reuse broke nothing.

## 3. Posture verification summary

Every subcommand runs the shared fail-closed check (signing-value `posture_scan` + executable/
replay regex guard) before writing. The global test confirms no signing value and no replay
content anywhere across all nine outputs. The corpus is read-only (debt read via `debt_report`,
never written) and remains at 34 entries. Nothing promotes a selector, updates a profile,
writes the corpus, retires debt, drives a browser, replays, or reuses signing material.

## 4. What each phase enables

Calibration tells you whether your confidence scores are honest. Regression catches when your
conclusions silently change. Capquality stops weak evidence from polluting confidence at the
front door. Forecast warns of instability before failure. Simulate lets a human see the
consequence of a batch approval before approving. Freshness flags evidence that has aged out
of trust-worthiness. The cross-cuts classify failures by type, rank observations by downstream
impact, and aggregate health at the family level.

## 5. How confidence quality improved

Before Phase 13, the framework produced confidence scores with no way to know if they meant
anything. Now calibration measures predicted-vs-observed and reports over/under-confidence by
subsystem; regression detects when a verdict changes across versions; and forecast turns
confidence trajectory into a forward risk estimate. Confidence is no longer just asserted — it
is measured, tracked, and projected, and the framework can say where its own scores are
unreliable.

## 6. How evidence quality improved

Before Phase 15/18, all evidence was treated as equally good and equally current. Now capquality
gates evidence at intake (Discard before it pollutes anything), impact analysis ranks which
observations actually matter downstream, and freshness flags evidence that has aged past
trust-worthiness. Evidence is now scored for quality, ranked for impact, and aged for currency —
the framework knows which of its inputs to trust and which to refresh.

## 7. What still requires human approval

Promoting any selector; applying any profile/workflow/login update; acting on any forecast,
calibration finding, or freshness flag; resolving any flagged verdict regression; and writing
any corpus entry or retiring any debt. Everything here informs; nothing acts.

## 8. What still requires new captures

The open validation-debt items (real perturbation captures) remain the top framework need.
Per-site: anything Phase 16 forecasts as high-risk, anything Phase 18 ages to Stale/Expired,
any capture Phase 15 scores Weak/Discard (re-capture), and any site whose calibration shows
unreliable confidence for want of outcome data.

## 9. Highest-ROI next phase after Phase 18

The framework can now describe, score, explain, forecast, and simulate — but it is still a
file-based suite a human runs tool by tool. The highest-ROI next step is a **unified operator
cockpit**: surface the dashboards (Phase 6), health/maturity (Phase 7), capture priority
(Phase 9), forecasts (Phase 16), and review/approval queues inside the existing operator UI,
with the calibration and freshness signals attached, so the operator sees "what to review, what
will fail next, how sure we are, and how stale it is" in one place — and approves through the
existing human gates in-app rather than by editing files. That turns eighteen phases of
recognition intelligence into a usable daily workflow, still recognition-only, still
human-gated, with no new detector behavior.
