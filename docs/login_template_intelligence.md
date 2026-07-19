# Login Template Intelligence

*v3.66.111 (Phase 2). Read-only, recognition-only. Part of the cockpit Template
Intelligence area. See `template_health_cockpit.md` and
`video_template_intelligence.md`.*

## What it is

Three cockpit surfaces that explain the login template (the per-site
`learned.login` block) and login outcomes, answering: **why did login fail? which
selector broke? did the form change? was MFA/captcha added? was the session
expired? should I update the template?**

- **Login Templates** (`/cockpit/api/template/login-health`) — per-site health +
  session freshness + recent login history.
- **Login Drift** (`/cockpit/api/template/login-drift`) — drift signals + a safe
  login dry-run.
- **Login Review Queue** (`/cockpit/api/template/login-review`) — templates needing
  attention, with data-only suggestions.

## Login Template Health

For each site: whether the login template exists (`learned.login` with
`user_field` + `pass_field`); whether `login_url` is set; the selector counts
(user/pass/submit) and a **defined** selector-confidence signal; **session
freshness** from `cookie_quality` (score, band, expired); the **recent login
success rate**; and whether **MFA/captcha** has been observed (the known invisible-
captcha markers: cf-turnstile / h-captcha / g-recaptcha). Recent login outcomes
come from the relogin log — timestamps and outcomes only, never credentials.

## Login Drift

Drift is classified from **observable state**: cookie expired, recent success rate
low, MFA/captcha present. Changes that genuinely require a live DOM diff — username
/ password / submit field changed, form moved, success-marker changed — are
**honestly marked `needs_dry_run`** rather than guessed from state.

## Safe Login Dry Run

Given a login page's HTML, it identifies which fields and buttons are present
(username / password / submit / form) and whether a captcha is on the page, and
reports a confidence. It **never submits credentials** and **never reads or echoes
any credential value** (a prefilled `value=` is never surfaced — verified by test).
The live page-open is only ever performed via the existing approved login path on
the deployment; this surface is the recognition step.

## Suggested Updates & Review Queue

When a login looks drifted, a **data-only** suggested template update (candidate
selectors from the login banks) is available — never auto-applied, never promoted.
The review queue lists templates needing attention with their reasons. The
**approve/reject workbench with side-by-side diffs is Phase 4** — Phase 2 surfaces
the queue; it does not mutate templates.

## Boundaries (enforced, tested)

- authorized sessions only; no login submitted anywhere in this surface
- no credential **values** read or echoed — only field names, selectors, markers
- no request replay, no captured-token reuse, no signed-URL reconstruction, no
  generated browser scripts
- no config writes, no corpus writes, no debt retirement
- suggestions are data-only; approve/reject is Phase 4
- the `do_login` `allow_manual_takeover` gate is untouched (this module does not
  call `do_login`)

## Endpoints (v3.66.111)

`GET /cockpit/api/template/login-health`, `…/login-drift`, `…/login-review` — all
GET, all read-only. Cockpit route count 86; POST surface unchanged.
