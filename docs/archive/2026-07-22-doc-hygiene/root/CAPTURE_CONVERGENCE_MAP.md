# Capture-model convergence map (B0)

Read-only deliverable for **Phase B — schema / normalizer / capture-model convergence**.
Re-derived from source at v3.66.174 (+ this session's staged work). Pairs with the
characterization golden `tools/capture_model_golden.py` / `tests/golden/capture_model.golden.json`,
which pins the current derived output of every reader so a B1/B2 reroute is provably
behaviour-preserving.

> Scope note: this is the **structural** convergence (one model, one reader path). The
> redaction-*depth* work (collapsing/strengthening the redaction layers) is **Phase C**,
> not B — see "What is NOT B" below.

## The divergence in one picture

```
                         raw capture (.wacz / .json)
                                    │
        ┌───────────────────────────┼────────────────────────────┐
        │                            │                            │
  capture_ingest.load_capture   workflow_diagnostic.load_capture   build_template_from_wacz
        │                            │                            │  (inline extraction;
  normalize_capture()           (load + capture_health +           │   no shared loader)
        │                         redaction_profile)               │
        ▼                            ▼                            ▼
  COMMON CAPTURE MODEL          DIAGNOSTIC VIEW               TEMPLATE DRAFT (v1)
  {host,title,captured_at,      {host,url,title,captured_at,  {schema_version, selectors,
   n_requests,goal_url,          capture_health,              network_discovery, match,
   capabilities,requests[],      redaction_profile,           resolution_priority,
   _raw}                         dom_log,network_log}         workflow, recognition, ...}
        │                            │                            │
   redact via                   relies on upstream            redact via
   capture_redact.redact_query  redaction (reads redacted)    capture_artifact_redact.redact_artifact
   + local _signing_markers_in                                (single derivation chokepoint)
                                                                   │
                                                          template_normalize.normalize_draft
                                                          (re-scrubs via redact_artifact)
                                                                   ▼
                                                          REVIEW CANDIDATE (v1)
```

## Readers — three of them, of the same bytes

| # | Reader | Output | Used by |
|---|--------|--------|---------|
| 1 | `bulk_downloader/capture_ingest.py :: load_capture` + `normalize_capture` | common internal capture model (`requests[]`, `capabilities`, `goal_url`, `_raw`) | `analyze_captures`, perturbation/temporal harness |
| 2 | `tools/workflow_diagnostic.py :: load_capture` | diagnostic view (`capture_health`, `redaction_profile`, echoed `dom_log`/`network_log`) | A4 capture diagnostics, A8 replay validator (and the 4a fold) |
| 3 | `tools/build_template_from_wacz.py :: build_template` | template DRAFT (v1) via its **own inline** dom/network extraction | the template pipeline (→ `normalize_draft` → promote) |

Reader **3 does not use 1 or 2** — it re-derives host, url-masking, signing detection, and
DOM serialization independently. Readers **1 and 2** are separate `load_capture`s with separate
derived fields. This is the duplication B converges.

## Normalizers — two models from the same source

- **capture model** (`normalize_capture`): request-centric, capability-flagged, query-stripped URLs,
  signing recorded by marker name/type. Built for *analysis*.
- **template draft/candidate** (`build_template` → `normalize_draft`): selector/network-pattern/resolution
  centric, schema-versioned (`bulk_downloader.template_draft.v1` → `…review_candidate.v1`). Built for
  *templatization*.

They overlap on host, URL redaction, and signing-marker derivation — computed by different code in each.

## Redaction — layered, NOT six duplicates (be accurate)

| Module | Role |
|--------|------|
| `capture_redact` | redaction **primitives**, "single source of truth" (`redact_query`, `SENSITIVE_QS_KEY`) |
| `capture_artifact_redact` | derivation-**boundary** layer for capture-DERIVED artifacts (`redact_artifact`), built on the primitives |
| `redaction_profile` | the tunable, env/config-driven redaction profile |
| `netlog_classify` | network-log classifier incl. signing markers (`_SIGN_MARKER`) |
| `capture_redactor` | redactor seam for session capture |
| `capture_bodies` | body capture/redaction (**hard guard**) |

The convergence-relevant fact is narrower than "6 redactors": the two **normalizers** apply *different
boundary functions* — `capture_ingest` uses `redact_query` (query-only); `build_template` / `normalize_draft`
use `redact_artifact` (value-content) — and **signing-marker detection lives in ≥2 places**
(`capture_ingest._signing_markers_in` and `netlog_classify._SIGN_MARKER`). Converging the *reader* should
route signing/marker derivation through one path; converging the *redaction depth* is Phase C.

## Constraints (bind every B slice)

- `extraction_core.py` is a **hard guard** AND already the template-side primitives anchor
  (`IDENTITY`, `RENDITION`, `DraftPattern`). Any reroute that touches it needs sha-declared guard approval.
- `decision_confidence` frozen @161 (`capture_workbench.py` L66); CP_* move declined — **B must not touch either**.
- The 3-stage template schema (DRAFT→CANDIDATE→REVIEWED) is documented (SCHEMAS.md) and test-asserted —
  `schema_version` strings and the contract must be preserved.
- Output stays redaction-clean; do not collapse the two redaction primitives in B (Phase C).

## Plan + how the golden guards it

| Slice | Move | Guarded by |
|-------|------|-----------|
| **B0** ✅ (this) | convergence map + characterization golden (`capture_model_golden.py` `--check`) | — |
| **B1** | route `workflow_diagnostic.load_capture` (cheapest; already an A4/A8 dep) through the canonical model | golden `--check` must stay green; wire `--check` into the build before landing |
| **B2** | route `build_template` extraction through the canonical model — likely touches `extraction_core` (guard) | golden + guard sha declaration |

`--check` runs the three readers on the fixed synthetic capture and diffs against the committed golden;
any change in a derived field fails the build. Same shape as the A3 dependency-graph gate and the
extraction_core step-5 consolidation golden.

## What is NOT B (explicit)

- Redaction-primitive unification / depth (the 6-module question) → **Phase C** (B3 in the Roadmap maps here).
- `decision_confidence` relocation, CP_* move → parked.
- The deferred GUI/control basket (Settings-Center writes, VPN control, AI editor, secrets lifecycle,
  template live-enablement) → independent features, not this convergence.
> [!WARNING]
> **Historical archive - do not execute.** This file is a point-in-time record. Its commands, procedures, paths, versions, and acceptance criteria may be obsolete; use active documentation and current release gates instead.
