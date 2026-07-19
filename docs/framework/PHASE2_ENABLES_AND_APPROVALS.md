# Phase 2 — what it enables, and what still requires human approval

## What Phase 2 enables (automatic, recognition-only)
- Reading captured DOM/rrweb logs into a candidate-element inventory.
- Deriving multiple selector candidates per element (id, attribute, class, href-pattern,
  text-assisted), with volatile/hashy class tokens filtered out.
- Scoring selectors by stability across same-title and different-title captures,
  specificity, uniqueness, churn resistance, proximity to download/rendition signals, and
  historical live success where the selector_drift table has it.
- Reporting selector drift (which selectors are stable vs. appearing only in some captures).
- Flagging where derived high-confidence selectors overlap the existing learned set.

This improves recognition and resilience: the framework can now say which selectors LOOK
stable and trustworthy, and surface when a site's DOM has drifted — all from captured
evidence, without touching the live site.

## What still requires human approval (never automatic)
- Promoting any candidate selector into the live learned-selector set. The
  `selector_profile_update_candidate.json` is a SUGGESTION; a maintainer decides.
- Writing anything to the validation corpus. Phase 2 produces no corpus entry at all; the
  Phase 1 draft entry remains the (human-finalized) corpus path.
- Changing live download/login behavior. Live operation is unaffected by Phase 2 and
  continues on existing learned-selector behavior unless a human promotes a selector.

## What Phase 2 deliberately does NOT do
- It does not generate Playwright scripts, click sequences, or any executable flow.
- It does not reconstruct a user session or drive a browser.
- It does not bypass site UI or use captured tokens.
- It does not auto-update templates, storage, or the corpus, or retire debt.

Selectors describe what looks stable and trustworthy. They are reviewable data for a human,
not executable session replays. The recognition-only posture is unchanged.
