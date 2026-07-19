# Template Drift Intelligence

*v3.66.112 (Phase 3). Read-only, recognition-only. The unified layer over video
(Phase 1) and login (Phase 2) template intelligence. See `template_health_cockpit.md`.*

## What it is

Two cockpit surfaces that unify both template types and add drift classification +
scoring, answering: **what changed? how often? which sites are becoming unstable?
which templates can I trust?**

- **Unified Template Health** (`/cockpit/api/template/unified-health`) — one row per
  site: video + login template presence, stability, maturity + trust, and a drift
  summary. Sorted least-stable first.
- **Drift Intelligence** (`/cockpit/api/template/drift-intel`) — drift timeline,
  frequency, severity summary, and likely root causes.

## Drift timeline & frequency (factual, not forecast)

The timeline is a dated log of real drift events drawn from the available sources:
corpus drift verdicts, login relogin-log failures, and stale download selectors,
newest first. Frequency reports per-site counts over 7- and 30-day windows.

These are **factual records, not forecasts**. With few events the timeline is
flagged `sparse` and frequency is flagged `trend_reliable=false` (threshold: 8
events) — counts are shown but never extrapolated into a trend. This is the same
honesty discipline as the gated forecasting work: show what's real, flag what's too
thin to call.

## Drift severity classification

Each drift is classified by a pure rule: **critical** (template missing / identity
change), **high** (selector zero-match or stale, login failing), **medium** (cookie
expired, captcha added, rendition drift, corpus drift verdict), **low** (otherwise).
No guessing beyond the recorded signal.

## Drift root cause analysis

For each site showing drift, the likely cause is inferred from recorded signals
(download selector stale → re-teach; cookie expired → re-login; low success rate →
Safe Dry Run; captcha present → manual-takeover path), each with a suggested next
step. Field/form pinpointing still needs a Safe Dry Run or fresh capture — stated,
not guessed.

## Stability & maturity scores (DEFINED composites)

Both are **transparent, defined composites** (components, weights, and raw inputs
all shown — not objective measures), 0–100:

- **Stability** = mean of download-clean + login-clean + drift-quiet. Login defaults
  to neutral (0.5) when there's no history. Bands: stable / watch / unstable.
- **Maturity** = mean of template-coverage + selector-breadth + proven-use +
  low-drift. Proven-use is 0 without login history. Bands: mature / developing /
  nascent, with a trust note (trusted / use-with-review). Distinct from the
  framework-wide maturity score in `cockpit_core`.

Both are weak on little history and sharpen as captures and logins accrue — stated
in the output.

## Boundaries (enforced, tested)

- read-only: no writes to `sites_config.json`, no corpus writes, no debt retirement
- recognition-only: no live page fetch, no model/network call, no request replay,
  no captured-token reuse, no generated browser scripts
- no auto-application or promotion of anything
- timelines/frequencies are factual logs with honest sparse/trend flags — no
  fabricated trends

## Endpoints (v3.66.112)

`GET /cockpit/api/template/unified-health`, `…/drift-intel` — both GET, both
read-only. Cockpit route count 88; POST surface unchanged.
