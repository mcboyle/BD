# Site Readiness Score

*v3.66.118 (Phase 9). Read-only composite. Rolls up Phases 1–8 into one number per
site. See `template_health_cockpit.md`.*

## What it is

One cockpit page — **Site Readiness** — that answers, per site, **"can I trust this
site today?"** with a single 0–100 score (banded ready / caution / not-ready) plus
the full component breakdown.

## The composite

Seven components, each normalised 0–1, combined with explicit weights:

| Component | Weight | From |
|---|---|---|
| Login health | 0.20 | login template presence + selector confidence + recent success rate |
| Video health | 0.20 | video template presence + selector confidence − stale penalty |
| Drift (inverted) | 0.15 | recent drift events (fewer = higher) |
| Evidence freshness | 0.10 | age of the site's newest corpus evidence |
| Capture quality | 0.10 | avg quality of captures matched to the site (Phase 8) |
| Template maturity | 0.15 | the Phase 3 maturity score |
| Review debt (inverted) | 0.10 | pending template-review items (fewer = higher) |

`readiness = Σ(component × weight) × 100`. It is a **DEFINED, transparent**
composite — every component, weight, and raw input is shown — not an objective
measure. **Thin signals use a neutral 0.5 and are flagged** (`thin_signals`), so a
fresh deployment with little capture/evidence/login history scores honestly rather
than being penalised or inflated.

## Boundaries (enforced, tested)

- read-only: no writes (no `write_text`, no `json.dump(`, no `_store_save`),
  no live fetch, no replay, no `do_login`, no apply
- transparent: weights sum to 1.0; components + inputs shown; bands labelled
- honest: thin signals neutral-and-flagged; capture quality only counts captures
  matched to the site by name (else neutral)

## Endpoint (v3.66.118)

`GET /cockpit/api/template/site-readiness` — GET, read-only. Cockpit route count 96;
POST surface unchanged.
