# Phases 7-12 — final deliverables

Six operational-intelligence layers built on top of Phases 1-6, all in one tool
(`tools/ops_intelligence.py`) with six subcommands. Each consumes existing artifacts,
produces reviewable data, fails closed on a posture scan, and changes nothing live.

## 1. Implementation summary

- **Phase 7 `health`** — site health scoring across six axes (selector, rendition,
  profile, drift, validation, operational) → an overall score and a maturity state
  (Unknown / Learning / Stable / Trusted / Watch / Fragile / Broken). Every score carries
  an explanation. Outputs: `site_health_score.json`, `site_health_report.md`.
- **Phase 8 `trace`** — reconstructs why each significant decision occurred (rendition
  selection, template match, selector policy, drift policy) with inputs / evidence /
  confidence / rule path / outcome. Outputs: `decision_trace.json`,
  `decision_trace_report.md`. No hidden decisions.
- **Phase 9 `capqueue`** — ranks sites for fresh capture by uncertainty, repeated drift,
  missing evidence, low health, and information gain, with the validation-debt items called
  out as the top framework-level priority. Every priority is explained. Outputs:
  `capture_priority_queue.json`, `capture_priority_report.md`.
- **Phase 10 `login`** — descriptive login workflow profile: selector roles, step order,
  MFA presence, cookie-persistence and session-longevity observations, success rate. NO
  credentials, NO replay, NO session reconstruction. Outputs: `login_profile.json`,
  `login_health_report.md`, `login_drift_report.md`.
- **Phase 11 `workflow`** — workflow STRUCTURE profile (stages and order), and the key
  distinction: selector drift (same stage, changed locator) is scored separately from
  workflow drift (a stage appeared / vanished / reordered) — a selector change is never
  promoted to a workflow change. Outputs: `workflow_profile.json`,
  `workflow_health_report.md`, `workflow_drift_report.md`.
- **Phase 12 `patterns`** — groups sites into structural families (Kaltura / JWPlayer /
  VideoJS / Brightcove / Wistia / CDN families) by player markers, CDN host, and selector
  shape. Families are for confidence and analysis only — they never change live behavior.
  Outputs: `cross_site_patterns.json`, `pattern_family_report.md`,
  `site_family_membership.json`.

Reuse: `cross_site_selectors.selector_shape`/`form_signature` (family fingerprinting),
the `login` selector-role vocabulary and `login_flow_recorder` shape (Phase 10), the
`selector_chains` step/stage structure (Phase 11). No parallel systems built.

## 2. Tests and validation summary

`test_phases712.py` runs all six end-to-end against synthetic artifacts and proves:
repeated drift → Fragile maturity with every score explained; each decision trace carries
all five fields and the rendition outcome is correct; the capture queue ranks and explains
and notes validation debt; the login profile is descriptive (roles + MFA + steps) with no
credential values; selector drift (2) is held distinct from workflow drift (1); and `bros`
is grouped into `jwplayer_family`. All pass. The 163 existing engine tests (cross-site,
selector, login, capture, ingest) still pass — the reuse broke nothing.

## 3. Posture verification summary

Every subcommand runs a shared fail-closed check before writing: a `posture_scan` for
signing values plus a regex guard for executable/replay content (`page.goto/click/fill`,
`await`, `playwright`, `new_page`, `requests.get/post`). The global test confirms no
signing value and no replay content appears anywhere across all six phases' output. The
corpus is read-only (debt is read via `debt_report`, never written) and remains at 34
entries. No tool promotes a selector, updates a profile, writes the corpus, or retires debt.

## 4. What each phase enables

Health gives a consistent, explainable maturity verdict per site. Trace makes every
decision reconstructable by a human. Capqueue turns evidence into a ranked, explained
capture plan. Login and Workflow extend recognition to the login and download *workflows*
(descriptively), and separate selector churn from real workflow change. Patterns surfaces
reusable structural families for confidence and cross-site analysis. Together they are the
operational-intelligence view over everything Phases 1-6 produce.

## 5. What still requires human approval

Promoting any selector; applying any profile, login, or workflow update; adopting a family
inference into live behavior; writing any corpus entry; retiring any debt; and any response
to structural/workflow drift. Everything these phases emit is a suggestion or an
explanation.

## 6. What still requires new captures

The open validation-debt items (real same-title perturbation captures, not retirable
synthetically) remain the top framework-level need. Per-site: anything Phase 9 ranks high
(low observations, repeated drift, missing evidence), any site without DOM logs before login
or selector learning is possible, and sites whose family is `unknown` for want of evidence.

## 7. Next highest-ROI phase after Phase 12

Capturing the real perturbation pairs for the two open validation-debt items remains the
single highest-value step — it is the only thing that retires standing debt and the
harnesses already exist. After that, the highest-ROI build is surfacing the Phase 6/9
queues and the Phase 7 health/maturity states inside the existing operator UI, so review,
capture, and approval items appear in-app rather than as files — turning this whole stack
from a file-based analysis suite into a live operator cockpit, still recognition-only.
EOF
echo done