# Phase 2 — selector learning: scoped design (NOT built)

This is a design note, not an implementation. Phase 1 deliberately stopped at what the
network-log model supports. Phase 2 is the selector half of the original objectives
(selector learning, selector confidence, the selector/login/download confidence scores),
and it needs a piece the current stack does not have: a DOM-log-to-selector extractor.

## DOM-log availability (the gate)

Phase 1's `site_health_report.md` reports, per run, whether the captures carry a usable
DOM log. This is determined from `dom_capture`'s output: a capture made through
`capture_session.py` with DOM capture active carries `dom_log` (rrweb-style events) and
`dom_log_count` in the capture dict. So:

- **If `any_dom_log_present` is true** in the health report, Phase 2 is feasible against
  those captures — the raw material exists.
- **If it is false**, the captures are network-log-only and selector learning is not
  possible until captures are re-taken with DOM capture enabled. There is no way to
  recover selectors that were never recorded.

What "enabling DOM capture" means concretely: `capture_session.py` uses `DomCapture`
(which records the rrweb `dom_log`) — so DOM is captured whenever the WACZ path is used.
The constraint is not a flag to flip so much as ensuring the site's DOM is actually being
serialized: rrweb runs browser-side, and the capture records what it emits. A capture
whose `dom_log_count` is 0 or trivially small did not capture usable DOM (e.g. the page
never instrumented, or the interaction was too brief).

## What the extractor would do (recognition-only)

The extractor reads the rrweb `dom_log` and produces a **selector inventory** — it does
not drive, click, or replay anything. Mechanically:

1. **Parse the rrweb event stream.** rrweb full-snapshot + incremental events describe the
   DOM and its mutations. The extractor walks them to reconstruct, for the elements the
   operator interacted with (clicks, inputs), a stable description: tag, id, classes,
   attributes, and structural position.
2. **Derive candidate selectors per interacted element.** For each click/input target,
   produce several candidate selectors at different specificities (id-based, class-based,
   attribute-based, structural/nth-child). This is the same idea as the project's existing
   selector_chains work (P5-1), applied to captured DOM rather than live.
3. **Respect the capture's redaction.** `dom_capture` already masks PII-annotated content
   (`.bd-mask`/`.bd-block`/rrweb-native `.rr-mask`) at capture time. The extractor reads
   the already-redacted log — it never sees masked content and must not try to infer it.
4. **Score selector stability across capture pairs.** Given two+ captures of the same
   workflow, a selector that resolves to the same logical element in all of them is
   stable; one that changes is drift. This yields `selector_confidence.json` ranked by
   stability, specificity, and (once tracked) historical success.

## Outputs Phase 2 would add

- `selector_confidence.json` — selectors ranked by stability / specificity / history.
- The selector slice of `template_validation_report.md` (stable / changed / newly
  discovered selectors) — merged into Phase 1's report or a sibling.
- The login/selector/download confidence scores in `site_health_report.md` that Phase 1
  currently reports as `not_measured_phase1_dom_required`.
- Selector and workflow drift in `site_drift_report.md` (the DOM/workflow axes Phase 1
  explicitly does not cover).
- Selectors and workflow patterns added to `site_profile.json`.

## Boundaries (unchanged from Phase 1)

The extractor is recognition-only and stays on the clean side exactly as Phase 1 does:
it reads a captured DOM log and emits descriptive selectors and confidence scores. It does
NOT generate a script that clicks them, does NOT replay the workflow, does NOT reconstruct
a session. Selector intelligence improves *recognition and resilience* — the tool knows
which selectors to trust and when a site's DOM has drifted — it never becomes an executable
replay. The line is the same one the whole project holds: describe what the site is, do not
reproduce what the operator did.

## Why it is a separate build, not a wiring job

Phase 1 wired existing engines because the URL/rendition/signing/drift logic already
existed and was already tested. No selector-extraction logic exists in `capture_ingest`,
`capture_template`, the harnesses, or `goal_skeleton` — they are all network-log/URL-shape
oriented. The rrweb-log walker, the candidate-selector deriver, and the cross-capture
stability scorer are genuinely new code with their own tests, which is why this is scoped
as Phase 2 rather than folded into the Phase 1 pass. It also overlaps the project's existing
P5-1 selector_chains work, which should be reviewed and reused rather than duplicated when
Phase 2 is built.
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
