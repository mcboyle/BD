# Phase 3 — live-template integration (advisory layer)

`tools/live_template_integration.py` uses the Phase-1 site profile and Phase-2 selector
confidence to GUIDE the existing live authorized downloader — recognition, selector
fallback order, confidence, and drift detection only. It does not drive a browser, click,
fetch, or download, and it cannot replay anything. Goes in `tools/`.

## Two phases per run
**Pre-flight** — build the enriched `learned` dict the existing `find_best_download` already
consumes, with row_selectors ORDERED by Phase-2 confidence:

    python3 tools/live_template_integration.py --site bros \
        --site-profile ./site_learning/bros/site_profile.json \
        --selector-confidence ./selector_learning/bros/selector_confidence.json \
        --emit-learned --out-dir ./live_runs/bros
    # -> guidance_learned.json : feed this as `learned` to find_best_download.

**Post-flight** — given what the existing live workflow OBSERVED on the page, classify the
match/drift and explain the decision:

    python3 tools/live_template_integration.py --site bros \
        --site-profile ... --selector-confidence ... \
        --live-observation ./live_obs.json --out-dir ./live_runs/bros

`live_obs.json` is what the existing workflow saw: `{identity, renditions[],
signing_markers[] (names only), goal_url_shape (query-stripped), selector_hits{}, via_learned,
structural_ok}`.

## Outputs
- `guidance_learned.json` — confidence-ordered `learned` dict for find_best_download
  (row_selectors / trigger_selectors + advisory `_expectations`). Falls through to the live
  sweep on a miss — never exclusive.
- `live_template_run_report.md` — match verdict, drift, the live download decision + why,
  and the selector guidance applied.
- `template_match_report.json` — verdict (template_matched / partially_matched /
  selector_drift / rendition_drift / signing_pattern_drift / structural_drift /
  unknown_layout) + drift flags.
- `download_decision_report.json` — highest currently-available rendition selected from the
  LIVE page (via the existing `detect.res_score`), scored options, and the basis.
- `live_drift_observation.json` — compact drift record.
- `suggested_profile_update.json` — advisory only.

## How it integrates without replacing live observation
The download decision is computed over the renditions the LIVE page presented, scored by the
existing `detect.res_score` — not from the template. Expectations from the profile are
advisory metadata that never override live state. Selector guidance only reorders what the
existing fast-path tries first; a miss falls through to the existing full live sweep. If no
profile/selectors exist, the live workflow runs exactly as before.

## Reuse
`detect.res_score` (resolution scoring — NOT reimplemented), the `find_best_download`
`learned` schema (row_selectors/trigger_selectors), `selector_drift.status_for` (live
history). 198 existing live-path tests still pass.

## Boundaries (enforced)
No request replay, no captured-token reuse, no signed-URL reconstruction, no generated
replay script, no UI bypass, no automatic corpus write, no automatic debt retirement. A
posture scan + a replay-content guard fail closed before writing. Profile/selector/corpus
updates are SUGGESTED only.

## Tests
`test_live_template_integration.py`: no replay content, confidence-ordered fallback, highest
LIVE rendition selected (incl. options not in template), clean match vs drift detection,
suggested-only updates, signing-names-only, pre-flight-without-live works.
