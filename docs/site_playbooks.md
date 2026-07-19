# Site Playbooks

*v3.66.114 (Phase 5). Read-only living dossier per site. Aggregates Phases 1–4 plus
operator notes and inferred family membership. See `template_health_cockpit.md`.*

## What it is

One cockpit page — **Site Playbooks** — with a directory of every site and a
drill-in dossier. Pure aggregation of existing data: no live fetch, no model, no
replay, no writes.

The index (`/cockpit/api/template/playbook-index`) lists each site with its inferred
family, login/video template presence, stability and maturity bands, open-concern
count, and note count. Click a site to open its dossier
(`/cockpit/api/template/playbook?site=…`).

## The dossier

A living dossier for one site:

- **Login model** — template presence, login_url, selector counts, session
  freshness, recent success rate, MFA/captcha observed (from Phase 2).
- **Download model** — template presence, selector count, url_attribute, two-step
  flow, highest rendition seen (from Phase 1).
- **Selector model** — the actual login + download selectors, with confidence.
- **Drift history** — recorded drift events (Phase 3) plus the site profile's drift
  history.
- **Known failure modes** — the site's open corpus concerns plus rule-based drift
  causes, each with a next step.
- **Known workarounds** — captured as operator notes.
- **Operator notes** — the site's notes (read-only).
- **Family membership** — inferred from the stored template/config against the
  project's own `PROVIDERS` classification plus player-library markers (JWPlayer,
  Video.js, React, Plyr, Flowplayer, hls.js, dash.js). Honest: "inferred; confirm
  with a capture."
- **Confidence** — stability and maturity bands/scores (Phase 3), plus the site
  profile's confidence history when present (point-in-time scores otherwise — stated).

Any signing material is surfaced as **marker names only** (`known_signing_markers`),
never values, consistent with the project posture.

## Boundaries (enforced, tested)

- read-only: no writes (no `write_text`, no `json.dump(`, no `_store_save`)
- recognition-only: no live page fetch, no model/network call, no replay,
  no `do_login`/fill/click
- no auto-application or promotion
- family membership is inferred from stored markers (the project's PROVIDERS table),
  not from a live page — and may be empty (honest, not invented)
- signing surfaced as names, never values

## Endpoints (v3.66.114)

`GET /cockpit/api/template/playbook-index`, `GET /cockpit/api/template/playbook?site=…`
— both GET, both read-only. Cockpit route count 91; POST surface unchanged.
