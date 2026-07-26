# Framework operations — workflows

This is the human-facing operating guide for running the recognition-only capture-analysis
framework repeatedly on future captures. It describes process only. It introduces no new
framework features and no new validation claims. The corpus is the source of truth and is
read-only except through the explicit corpus-review workflow below; debt is never retired
except through the explicit debt-retirement procedure. Recognition-only posture is
unchanged: nothing here drives a browser, replays, reconstructs signed URLs, reuses tokens,
or takes an automatic action.

The tools referenced live in `tools/`; their per-tool usage is in `docs/framework/*_USAGE.md`
and the phase deliverable docs in the same directory.

---

## 1. Capture workflow (operator)

Goal: collect authorized-session captures that are good enough to feed analysis.

1. Decide the target site(s) and titles. For drift/diff analysis you want **two** captures
   of the same title (a run1/run2 pair), since the temporal and template-diff axes only
   light up across a same-title series.
2. Run the capture launcher:
   `python tools/capture_batch.py --pairs --job <site> <url> --profile-root <dir>`
   (see `docs/framework/PARALLEL_CAPTURE_USAGE.md` / `PLAN_C_USAGE.md` for profile,
   autofill, and URL-memory options). Capture is human-driven: you log in through the
   site's own UI and press ENTER to stop; the tool passively records and redacts.
3. Confirm the capture carries a usable DOM log if you want selector analysis (capture
   quality, step 4 below, will tell you).
4. Score capture quality before feeding anything downstream:
   `python tools/validation_harness.py capquality --capture <capture> --out-dir <dir>`
   Discard / Weak captures should be re-collected, not analyzed — they pollute confidence.

Hard line: the operator collects evidence by operating the site normally. The framework
never fetches media, reassembles a stream, or replays a request.

---

## 2. Evidence intake (analyst)

Goal: turn raw captures into a structured, analyzed artifact set, without writing the corpus.

1. Run the per-site learning loop on the capture(s):
   `python tools/site_learning.py --captures <c1> <c2> --site <site> [--template <t>] --out-dir <dir>`
   Produces template-validation, rendition profile, drift report, site profile, health,
   next-capture recommendation, and a **draft** corpus entry (no id, outcome untested).
2. If DOM is present, run selector learning:
   `python tools/selector_learning.py --captures <c1> <c2> --site <site> --out-dir <dir>`
3. Accumulate history and policy:
   `python tools/closed_loop_learning.py ...` then `python tools/evidence_policy.py ...`
4. The analyst's output is the artifact set, not a corpus change. The draft corpus entry
   is a proposal for the reviewer (section 5), never an automatic write.

---

## 3. Analyst workflow (trust + intelligence layers)

Goal: assess how trustworthy and stable the analysis is before anything is proposed for the
corpus or a release.

- Health & maturity: `tools/ops_intelligence.py health ...`
- Calibration (are the confidence scores honest?): `tools/trust_intelligence.py calibrate ...`
- Decision consistency / threshold edges: `tools/validation_harness.py consistency ...`
- Benchmark stability vs. baseline: `tools/validation_harness.py benchmark --baseline ...`
- Forecast & freshness: `tools/trust_intelligence.py forecast ... | freshness ...`
- Self-audit (defensibility, assumptions, risk): `tools/meta_intelligence.py audit | assumptions | risk`

The analyst compiles these into the review packet for the reviewer. No score, profile,
selector, or corpus entry is changed by the analyst.

---

## 4. Reviewer workflow (human gate)

Goal: a human decides what, if anything, becomes durable. This is the gate every change
passes through.

For each proposed item the reviewer:
1. Reads the draft corpus entry and the artifact set behind it.
2. Confirms the evidence is real (captures exist, not synthetic), the observation matches
   what the analysis found, and the defensibility (audit-readiness) is adequate.
3. Decides: accept / revise / reject / request more evidence.
4. If accepted, the reviewer **manually** finalizes the corpus entry per the corpus-entry
   template (`docs/framework/templates/corpus_entry_template.md`) and appends it through the
   corpus-review workflow (section 5). The framework never appends on the reviewer's behalf.

The reviewer also owns selector/profile promotion decisions (the tools only *suggest*),
structural-drift responses (always human review), and release readiness (section 7).

---

## 5. Corpus review workflow

Goal: keep the corpus the source of truth, changed only deliberately.

- The corpus (`validation_corpus.jsonl`, 35 entries, measured 2026-07-22) is read-only to every tool. The only
  way an entry changes is a human appending or editing it after review.
- A proposed entry arrives as a draft (from intake) with no id and `outcome: untested`.
- The reviewer assigns the id, sets the date/version, scopes the observation, and records
  the basis_kind and (if applicable) the `resolves` pointer, using the corpus-entry template.
- Appends are append-only; corrections to a prior entry are recorded as new entries that
  resolve the old one, not silent edits, so the history stays auditable.
- After any change, re-run the corpus-consuming checks (audit readiness, knowledge graph,
  assumption intelligence) to confirm the change is coherent.

---

## 6. Validation review & debt-retirement procedure

Goal: retire validation debt only with real evidence, deliberately, by a human.

Current debt (read-only status): correction 0, capability 0, validation 2 —
**VC-0017** (player_config axis) and **VC-0018** (workflow axis). Both are validation debt:
a prediction the framework has made but not yet confirmed against real perturbation captures.

To retire a validation-debt item (human-driven, no synthetic shortcut):
1. Collect the **real** perturbation captures the item requires (e.g. same title captured
   under the varied axis). Synthetic or simulated captures cannot retire debt — this is
   enforced and must not be worked around.
2. Run the perturbation/temporal harness over the real captures and review the verdict.
3. The reviewer judges whether the prediction is confirmed, falsified, or still open.
4. If confirmed/falsified, the reviewer **manually** records the outcome as a corpus entry
   (per the corpus-entry template) that resolves the debt item, through the corpus-review
   workflow. The framework does not retire debt automatically.
5. Re-run the debt report to confirm the new state.

If the captures don't settle it, the item stays open. Debt staying open is an acceptable,
honest state; retiring it without real evidence is not.

---

## 7. Release review procedure

Goal: a defensible go/no-go before shipping a framework change.

1. Run the readiness gate:
   `python tools/validation_harness.py release --confidence-calibration ... --benchmark-scorecard ... --verdict-changes ... --governance-findings ... --risk-register ... --framework-scorecard ...`
2. Blockers: critical risk, governance non-compliance, or verdict regression. Known/accepted
   validation debt is reported but is not by itself a blocker.
3. Run the governance scan over the release artifacts:
   `python tools/operator_layer.py governance --artifacts-root <dir>` — confirms recognition-
   only posture, no replay/executable content, and human-gate language across all artifacts.
4. Run the full unit suite (`run_tests.py`); confirm 0 logic failures. Live-environment
   integrity checks (`L22/L26/L34/L37`) only pass on the deployed host and are expected to
   report absent in an offline build.
5. Build with `tools/build_release.py` — the CHANGELOG/ENDPOINT_CATALOG/FUNCTION_INDEX drift
   gates, the verifier, and the POSTURE GATE (confirms no raw-capture capability in the zip)
   must all pass.
6. The reviewer makes the final call. The gate informs; it does not ship anything itself.

---

## Posture reminder (applies to every workflow above)

Recognition-only, end to end. Describe and surface; never act. No browser driving, no replay,
no signed-URL reconstruction, no token reuse, no automatic selector/profile promotion, no
automatic corpus write, no automatic debt retirement. Every durable change passes through a
human reviewer.
