<!-- verified-against: v3.66.185 -->
# BulkDownloader — Project Goals

## Mission

Build a dependable, self-hosted automation tool that helps an authenticated
operator archive video they can already access, using approved templates,
site-provided controls, and low-touch headless operation.

## Durable goals

1. **Automation-first site onboarding.** Adding or refreshing a site should be as
   mechanical as possible: capture preparation, WACZ capture, build, normalize,
   lint, blocked-term scan, drift report, candidate review bundle, staging
   promotion, backup preparation, test, evidence, and rollback notes should be
   automated wherever possible.

2. **Explicit approval checkpoint.** Unknown hosts and first-time templates may be
   prepared automatically as drafts or review candidates. Runtime enablement for a
   new host still requires explicit approval. Previously approved hosts may be
   auto-applied today; auto-refresh / auto-repair are roadmap (see
   `AUTOMATION_POLICY.md`) and gated on automated gold-backup landing first.

3. **Faithful extraction.** Templates must reflect observed, reusable structure.
   Do not invent API hosts, selectors, resolution ladders, URLs, or credentials.
   Observed-but-unconfirmed details are review hints, not authoritative config.

4. **No secret/template contamination.** Templates store selectors, reviewed URL
   patterns, approved API shapes, and resolution preferences only. They never
   store cookies, passwords, Authorization headers, tokens, signed URLs, expiring
   query strings, or challenge artifacts.

5. **Site-provided, authenticated flows.** Runtime uses the operator's
   authenticated browser state and site-provided playback/download controls or
   approved site API endpoints available to that session.

6. **Headless operability.** The workflow should run on the stash host with
   noVNC/cockpit support, sentinel-finish capture, template manager visibility,
   and clear logs.

7. **Drift-resistant runtime.** On DOM/API drift, route to review instead of
   guessing. Reject navigation-looking candidates and generic selectors such as
   `a[href]` unless they resolve to strong media/download evidence.

8. **Release discipline.** Every production patch should have targeted tests, a
   version bump when releasing, changelog entry, extracted-zip verification, and
   rollback instructions.

## Automation preference

Prefer automation over manual steps when the automated path:

- uses an authenticated user session,
- uses site-provided playback/download controls or approved API endpoints,
- does not persist secrets or signed URLs in templates,
- passes lint/blocklist/drift checks,
- has logs, tests, evidence, and rollback,
- and routes uncertainty to review.

Automation should include:

- approved-template detection on site create/update,
- automatic runtime application of reviewed templates,
- review-bundle generation for unknown hosts,
- template manager listing/promotion/disable workflows,
- offline dependency packaging,
- manual-login profile handoff into worker/keeper profiles,
- selector lint and blocked-term enforcement,
- navigation URL rejection,
- drift reports and quarantine recommendations,
- backup/stage/diff preparation,
- and release/test snapshot generation.

## Non-goals

BulkDownloader is not a DRM tool, paywall tool, CAPTCHA solver, challenge-system
bypass tool, unauthorized crawler, or redistribution system.
