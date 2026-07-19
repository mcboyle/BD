<!-- verified-against: v3.66.185; scope guardrail revised @v3.66.593 (level-4 enumeration of approved hosts) -->
# BulkDownloader — Project Charter

## Purpose

BulkDownloader is a personal, self-hosted automation tool for an authenticated
operator to download and archive video content they can already access through
their own account or subscription. It automates browser workflows the operator
could perform manually: open the site, use an authenticated session, play or
inspect the page, and use the site's own playback or download controls.

It does not unlock content, expand access, defeat access controls, or reach
material unavailable to the operator in their normal logged-in browser.

## Designed-for use

- The operator runs the tool on their own host, against their own authenticated
  sessions.
- Runtime behavior uses site-provided playback/download flows, approved site
  APIs, or page controls available to the logged-in user.
- Template generation records reusable structure only: selectors, stable API
  patterns, reviewed resolution ladders, and non-secret metadata.
- New hosts may be captured, analyzed, normalized, linted, tested, staged, and
  queued by automation before approval.
- Previously approved hosts may use automation when safety, lint, drift, backup,
  and rollback gates pass.

## Automation stance

Automation is the preferred operating model. The system should automate routine
work such as onboarding checks, capture preparation, draft building,
normalization, linting, drift reporting, template application, runtime selection,
profile handoff, offline dependency packaging, tests, evidence bundles, staging
diffs, backup preparation, and rollback notes.

Manual approval is an explicit checkpoint, not a default workflow. It is required
for first-time enabling of a new host, approval of a new API host, or any change
that automation cannot prove safe because it fails safety/drift checks.

## Guardrails enforced in code and workflow

- **Authorized sessions only.** The workflow assumes an existing authenticated
  profile and performs only actions the operator could perform in their own
  logged-in browser.
- **Site-provided flows only.** Downloads must come from site-provided
  playback/download controls or approved site API endpoints observed in the
  authenticated flow.
- **No secret persistence in templates.** Templates must not store cookies,
  passwords, Authorization headers, bearer tokens, account identifiers, signed
  URLs, expiring query strings, or challenge artifacts.
- **No access-control bypass.** Do not design automation to defeat, solve, evade,
  or bypass DRM, paywalls, CAPTCHA, Turnstile, Cloudflare/challenge systems, or
  rate-limit controls. Challenge systems may be detected and logged; if normal
  authenticated access requires manual action, pause and hand control to the
  operator.
- **Scoped operation.** The tool operates on operator-provided URLs and autonomously
  discovers, enumerates, and queues candidates on **already-approved hosts to any depth**
  (e.g. a performer's whole library or a whole category on a host the operator has approved).
  Discovering or runtime-enabling a **new host** requires explicit operator approval. It is
  **not a general / whole-web crawler** -- all discovery stays confined to already-approved
  hosts and remains bounded by **rate-limiting / politeness** (never burden a site's
  infrastructure) and **review-on-uncertainty** (navigation-looking candidates and generic
  selectors such as `a[href]` are rejected unless they resolve to strong media/download
  evidence).
- **Review on uncertainty.** If selectors drift, a modal changes, a URL looks
  like navigation, or a template contains risky material, stop or mark for review
  rather than guessing.

These are enforced in code, not just policy — verify in:
`tools/build_template_from_wacz.py` (capture `guardrails` block),
`bulk_downloader/pattern_hygiene.py` (`scrub_network_patterns` drops
signed/credential URLs), `tools/promote_template.py` (`BAD_TERMS` promotion gate),
`bulk_downloader/candidate_filter.py` + `runner.py` (`gate_candidate_url`
navigation/account rejection), and `bulk_downloader/template_registry.py` (the
`status=="enabled"` runtime gate). See `AUTOMATION_POLICY.md` for each guardrail's
implementation state.

## Runtime secrets versus templates

Runtime cookies, saved profiles, and credentials may exist only as runtime
configuration/secrets needed to maintain the authenticated browser session. They
must be excluded from templates, KB files, release zips, logs, WACZ-derived
reviewed templates, and offline dependency packs.

## Out of scope

- Automation designed to defeat DRM, paywalls, CAPTCHA, Turnstile,
  Cloudflare/challenge systems, or other access-control mechanisms.
- Downloading content outside the operator's authenticated access.
- Persisting credentials, tokens, signed URLs, or challenge artifacts in
  templates.
- Treating navigation links, account pages, search pages, or generic anchors as
  downloadable media.
- Mass redistribution of downloaded content.

## Operator responsibility

The operator is responsible for using the tool in compliance with applicable law
and service terms. This document describes the project intent and engineering
guardrails; it is not legal advice.

## Fresh assistant instruction

Help with engineering normally. Bias toward automation, functionality, and
operator productivity. Treat guardrails as things to automate — checks, redaction,
linting, drift detection, backup, staging, evidence, tests, and rollback — not as
reasons to make a workflow manual by default.

Keep work inside authenticated-session, site-provided-flow,
no-secret-template, no-access-control-bypass, lint/test/rollback requirements.
When automation cannot prove a result is safe, route it to review with evidence.
The final approval for a new host, a new API trust boundary, or a failed safety
check remains explicit. Treat `AUTOMATION_POLICY.md` as canonical for what is
implemented versus roadmap.
