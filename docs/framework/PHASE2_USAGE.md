# Phase 2 — selector learning (DOM/selector recognition layer)

`tools/selector_learning.py` reads captured rrweb DOM logs and produces REVIEWABLE
selector candidates with confidence scores. Recognition only — it emits descriptive data,
never an executable flow. Goes in `tools/`.

## Run
    python3 tools/selector_learning.py \
        --captures ./captures/bros_run1.wacz ./captures/bros_run2.wacz \
        --site bros \
        --learned-selectors ./site_templates/bros.selectors.json \   # optional
        --out-dir ./selector_learning/bros

## Outputs
- `selector_inventory.json` — per-capture candidate action/download/login elements, each
  with derived selector candidates (id / attribute / class / href-pattern / text-assisted)
  and role/signal hints. Data only.
- `selector_confidence.json` — every candidate selector scored by stability across captures,
  specificity, churn resistance, proximity to download/rendition signals, learned-selector
  overlap, and historical live status (from selector_drift, if available). Ranked.
- `selector_drift_report.md` — stable-across-all vs partial/drifting selectors; flags
  low-stability selectors near the download signal.
- `selector_learning_report.md` — top candidates by confidence + the enable/approve statement.
- `selector_profile_update_candidate.json` — SUGGESTED selectors a maintainer might promote.
  Never auto-applied.

## Reuse
Reuses `cross_site_selectors` (class-churn / specificity-shape primitives) and
`selector_drift.status_for` (historical live success/zero-match). The Phase-2 volatile-class
check is a stricter local supplement layered on the shared one; the shared primitive is
unchanged.

## Boundaries (enforced)
- DOM read from captured artifacts only — nothing fetched or driven.
- No signing value in any artifact: redacted nodes contribute no text/value, hrefs are
  query-stripped, and a `posture_scan` over all output fails closed before writing.
- A replay-guard additionally refuses to write if any artifact contains script-like content
  (page.goto/click/fill, await, playwright, new_page).
- Empty/absent DOM logs → a "blocked / not available" result (exit 0), not an error, and
  live operation continues on existing learned-selector behavior.
- Selector confidence does NOT auto-update live templates, learned-selector storage, or the
  corpus, and cannot retire debt. A maintainer promotes selectors manually.

## Tests
`test_selector_learning.py` proves: no replay/executable content, no signing values, empty
DOM → blocked, candidates are reviewable data, volatile classes filtered, download role
recognized, and profile update is suggested-only. Run: `python3 test_selector_learning.py`.
