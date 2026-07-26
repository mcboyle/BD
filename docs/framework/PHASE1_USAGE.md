# Phase 1 — capture→template continuous-learning loop

`tools/site_learning.py` orchestrates the EXISTING recognition engines (capture_ingest,
capture_template, temporal_harness, goal_skeleton) over a site's captures and emits the
descriptive artifacts. It adds no analysis of its own — every verdict comes from a module
that is already tested and posture-gated. Goes in `tools/`.

## Run
    python3 tools/site_learning.py \
        --captures ./captures/bros_run1.wacz ./captures/bros_run2.wacz \
        --site bros \
        --template ./site_templates/bros.template.json \   # omit on first run
        --prior-profile ./site_learning/bros/site_profile.json \  # to append history
        --out-dir ./site_learning/bros

## Outputs (all to --out-dir)
- `template_validation_report.md` — each capture diffed against the stored template
  (HELD / DRIFTED / NEW per prediction), via `diff_template`.
- `rendition_profile.json` — rendition/identity descriptors from goal_skeleton's slots,
  plus the temporal rendition axis. Path identifiers only, never signing.
- `site_drift_report.md` — drift on the supported axes (goal-URL-shape/structural,
  identity, rendition, signing-marker presence), classified cosmetic/moderate/breaking.
- `site_profile.json` — accumulating KB: known goal shapes, rendition/identity
  descriptors, signing markers (names only), with confidence_history and drift_history
  appended across runs. URL/rendition/signing slice only (Phase 1).
- `site_health_report.md` — confidence scores. Login/selector/download are reported as
  `not_measured_phase1_dom_required` (DOM-dependent → Phase 2), plus a DOM-presence
  readiness check.
- `next_capture_recommendation.md` — what remains unknown, what would raise confidence,
  highest information gain, fragility.
- `suggested_corpus_entry.json` — a REVIEWABLE DRAFT (no id, outcome `untested`). Cannot
  write the corpus or retire debt; a human finalizes it via the corpus-entry templates.

## Posture
Recognition-only, enforced. Before writing anything, the tool runs `posture_scan` over the
combined output and FAILS CLOSED if any signing value would appear. No replay, no fetching,
no downloading, no signed-URL reconstruction, no token reuse, no generated Playwright
script, no automatic corpus writes, no automatic debt retirement. Signing is reported by
marker name/type only; every echoed URL is query-stripped. Verified: synthetic captures
with `token=`/`expires=`/`sig=` values produce artifacts containing none of those values.

## Selectors are Phase 2
This pass does NOT extract selectors. It only REPORTS whether the captures carry a usable
DOM/rrweb log (the Phase-2 gate). See the archived
`docs/archive/2026-07-22-doc-hygiene/docs/framework/PHASE2_SELECTOR_DESIGN.md` for the scoped design of
the DOM-log-to-selector extractor and what must be enabled if DOM logs are absent.

## Tying into the capture tooling
Feed it the run1/run2 pairs from `capture_batch.py --pairs`: capture a site twice, point
`site_learning.py` at both WACZs, and the temporal/diff axes light up (stable-vs-per-session).
The loop: capture → diff against template → descriptive profile + drift + recommendation →
(human) corpus entry → informs the next capture. Nothing reproduces a session.
