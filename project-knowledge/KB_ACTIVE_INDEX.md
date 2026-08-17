<!-- verified-against: v3.66.464 -->
# BulkDownloader -- Active KB Index

The single active index for the project-knowledge set. This file is **static**
(version-agnostic) and lives in project knowledge.

## Where the current state lives

The distilled current state (newest `KB_HANDOFF_v3_66_<n>.md`) and the live
planning docs are **not** in static project knowledge -- they arrive in the
per-session **`version.zip`**. This is historical workflow context; the current
checkout and `CLAUDE.md` are authoritative.

## Reading order

1. `CLAUDE.md` -- the sole agent-facing contract: working style, release
   checklist, verification, and footguns.
1. `PROJECT_CHARTER.md` -- purpose, scope, ethics, in-code guardrails.
1. `PROJECT_GOALS.md` -- durable direction.
1. `AUTOMATION_POLICY.md` -- canonical automation doc (what may be automated, where
   approval is required, IMPLEMENTED / PARTIAL / PLANNED state).
1. `KB_JUDGMENT.md` -- the durable why/how layer: named failure shapes, system
   mental-models, decision criteria, and how Matt works (judgment, not facts;
   changes only when judgment changes, never on a routine release).
1. `ADVANCED_PROJECT_KNOWLEDGE.md` -- the consolidated, deduplicated, @464-validated
   reference: sandbox/shell footguns, build/release discipline, the failure taxonomy,
   secrets/vault invariants, bulk-ops, automation posture + declined scope, the tracker
   method, capture/redaction footguns, and the runner/db/blueprint load-bearing invariants
   (section I; full registry in `DANGER_MAPv2.md`). The working "what bites and why" layer.
1. `DANGER_MAPv2.md` -- the full numbered runner/db/session load-bearing-invariant registry
   (deep backing for ADVANCED section I; use `tools/explain_invariant.py INV-NNN`).
1. `GLOSSARY.md` -- the project's jargon decode ring (capture/redaction, recognizer,
   release, decomposition, tracker/process terms). Read when a handoff/tracker term is opaque.
1. `ARCHITECTURE_MAP.md` -- source topology (the capture->template->download pipeline, the
   169 blueprints by domain, the 13 runner mixins, deep_detect, the guard boundary). Navigation.
1. `KB_SYNC_WORKFLOW.md` -- how the bd-* scripts keep this static set from going stale
   (the `bd-kb-sync` manifest loop: static is a cache, the pack is the live edge). Read if
   you are about to change a durable static doc or run a session close.
1. `SANDBOX.md` -- environment and footguns.
1. `SCHEMAS.md` -- the three template-pipeline schemas (draft / review-candidate /
   reviewed), from source.
1. **From the version.zip:** `STATE.json` first (the machine-readable pin —
   validate with `bd-state`), then newest `KB_HANDOFF_v3_66_<n>.md`, `Backlog.md`,
   `Roadmap.md`, and `KNOWN_FLAKES.md`.
1. `REPTYLE_CAPTURE_RUNBOOK.md` -- live capture / regeneration.
1. `reference/` -- build/audit/sandbox cards (durable: 2,3,4,6,7,8,9,10 here;
   version-specific 1 + 5 arrive in the version.zip).

## Static project knowledge (this set -- change only when a static doc changes)

`README_KB.md`, `KB_ACTIVE_INDEX.md`,
`PROJECT_CHARTER.md`, `PROJECT_GOALS.md`, `AUTOMATION_POLICY.md`,
`PROJECT_KNOWLEDGE_IS_STATIC.md`,
`KB_JUDGMENT.md`, `ADVANCED_PROJECT_KNOWLEDGE.md`, `DANGER_MAPv2.md`, `GLOSSARY.md`, `ARCHITECTURE_MAP.md`, `SANDBOX.md`, `SCHEMAS.md`, `REPTYLE_CAPTURE_RUNBOOK.md`, `BDKIT_FIXES.md`,
`bdkit_HANDOFF.md`, `KNOWN_FLAKES.md`, `CONTINUATION_TEMPLATE.md`,
`STATE.schema.json`, `Manifest.md`, the kit scripts (`setup.sh`, `bd-install`,
`bd-status`, `bd`, `bdenv.sh`, `bd-preflight`, `bd-state`, `bd-cut`, `bd-pack`,
`install_bulkdl_kits.sh`), the kit READMEs (`README_KIT_FIXES.md`,
`README_EFFICIENCY_KIT.md`), `bd-kb-sync` + `KB_SYNC_WORKFLOW.md` + `STATIC_KB_MANIFEST.json` (the static-KB sync engine, its doc, and the integrity manifest), and `reference/` cards 2/3/4/6/7/8/9/10.

### Sandbox capability + OPV + harness (added v3.66.539)

The sandbox is far more capable than the old "offline/no-browser" premise. For any
OPV / harness / render / GUI-audit work, read these first:

| Doc | What it covers |
|---|---|
| `SANDBOX_CAPABILITY_LAYER.md` | **Read first.** What the sandbox can do (headed browser, netns, OCR, Prometheus, SMTP/webpush/QR mocks), how to restore it (`bd-sbcap`), what stays operator-gated, and the full footgun encyclopedia. |
| `OPV_CHECK_REFERENCE.md` | What each `bd-opv` check proves (generated from the live registry; live counts in STATE `opv_session`). |
| `TESTING_ETHICS_FRAME.md` | The settled harness ethics — detect→handoff never solve, no solving service, no adult sites, redaction-on. Don't relitigate. |
| `SANCTIONED_TEST_URLS.md` | The harness allowlist (public/non-adult/purpose-built endpoints; off-allowlist = refused). |
| `RENDER_CAPTURE_AUDIT_GUIDE.md` | The definitive GUI render + audit methodology (gui_audit_kit, discoverability, both-theme capture, the squish-regression lesson). |
| `PROJECT_KNOWLEDGE_UPDATE_539_SANDBOX.md` | What the 539 sandbox session changed and why. |

`bd-sbcap` (capability provisioner) and the expanded `bd-opv` are in `BD_TOOLCHAIN_REFERENCE.md`.
The full toolkit (every tool added that session + a README) is `BD_SANDBOX_TOOLKIT_v3_66_539.zip`.


## Per-session version.zip (regenerated at session close)

Newest `STATE.json` (the pin), `KB_HANDOFF_v3_66_<n>.md`, `Backlog.md`, `Roadmap.md`,
`KB_VALIDATION_NOTES.md`, `REFCARD_1_artifact_provenance.md`, `REFCARD_5_delta_spec.md` (version-stamped per release). The pin lives in `STATE.json` + the newest `KB_HANDOFF`; `CONTINUATION_MESSAGE` is retired (`CONTINUATION_TEMPLATE.md` is an optional template).

### Decision records (travel with `Backlog.md` / `Roadmap.md`)

- `EXTRACTION_CORE_DECISION_CONFIDENCE_DECISION.md` -- the leave-in-workbench
  decision for `decision_confidence` / `CP_*` (extraction_core Phase 1 closed; the
  rejected relocate path is not to be pursued). Cross-referenced from
  the `Backlog.md` and `Roadmap.md` extraction_core sections.

## Not in project knowledge (by design)

Source zips (attached per session), transcripts (`/mnt/transcripts/`), the offline
packs (100s of MB -- see `Manifest.md`), and per-release session paper-trail (build
report, MAX audit, slice plans, screenshots -- loose in outputs, reconstructable
from the transcript).

## Folded / merged in prior cleanups (not separate files)

- `FUNCTIONALITY_FIRST_RULES.md` -> folded into `AUTOMATION_POLICY.md`.
- The former operating-instructions addendum was retired with the superseded
  document; current agent rules live only in `CLAUDE.md`.

Ground-truth order: **source code > newest transcript > newest handoff > static
project docs > older docs.**
