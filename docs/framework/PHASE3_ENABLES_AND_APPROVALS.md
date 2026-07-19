# Phase 3 — what it enables, and what still requires human approval

## What Phase 3 enables (recognition/guidance, automatic)
- Pre-flight: turns Phase-1/Phase-2 profiles into a confidence-ordered `learned` dict the
  existing live workflow consumes, so the most trustworthy selectors are tried first.
- Post-flight: classifies whether the live page matched the learned template, and reports
  template / partial / selector / rendition / signing / structural drift, or unknown layout.
- Explains which rendition the live workflow would select and why — the highest currently
  available resolution on the live page, scored by the existing detect.res_score.
- Surfaces drift early so a maintainer knows when a site changed.

## What still requires human approval (never automatic)
- Promoting any selector into the live learned set.
- Updating the site profile.
- Writing any corpus entry (Phase 3 writes none; suggestions only).
- Retiring any debt.
- Changing live download behavior beyond the ORDER learned selectors are tried (which always
  falls through to the existing full live sweep on a miss).

## What Phase 3 deliberately does NOT do
- It does not drive the browser, click, navigate, fetch, or download.
- It does not replay captured network requests or reuse captured signing values.
- It does not reconstruct signed URLs or generate any replay/Playwright script.
- It does not bypass site UI or replace live observation with template assumptions.

## The live workflow remains in control
The live authorized session, current page state, visible controls, normal site download UI,
and currently available renditions drive every actual action. The template guides recognition,
confidence, fallback order, and drift detection — and nothing more. The template may guide the
live workflow; it does not become a replay script. Recognition-only posture is unchanged.
