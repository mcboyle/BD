# HANDOFF — v1 Autonomy Wire (v3.66.132)

**One line:** the system can now autonomously maintain a confined, reversible, per-site
**staged config candidate** — and nothing more. First real autonomy wire; built dark.

## What v1 does
Exercises the full autonomy chain on the lowest-risk write target:
`oracle/trust gate → staged candidate apply → record_change("staging_json",…) →
register_pending(…,"C") → fail-closed 24h window → auto-revert if not accepted →
transition/audit log`. The maintenance loop and the sweep are **host-scheduled**
(cron/CLI beside `decay_all_trust` + `scan_and_record`), never cockpit buttons.

## What it deliberately does NOT do
No production `sites_config.json` write · no live promotion · no behavioral mutation
(behavioral fields are a **credential-redacted, byte-identical copy** of live; the system
authors only an `evidence` annotation) · no login/template/extraction change · no corpus
writes · no debt retirement · no finding confirm/falsify · no policy change · no credential
handling · no captures · no third-party interaction. `participation_eligible` stays `False`
and is not consulted; v1 uses a separate, strictly weaker gate, `staging_eligible`.

## How to review it (Governance → Staged Candidates)
Read-only views (`/api/staging/status|candidates|candidate`) show pending candidates with
oracle tier, trust, the behavioral-unchanged check, embedded-vs-redacted keys, a
rollback-preview, and the revert deadline. Accept/reject reuses the existing audited
`/api/review/decide` (no new POST): **reject** reverts immediately; **accept** blesses and
stops the clock **without promoting**; **silence** auto-reverts at the deadline.

## Built dark
Empty oracle/trust stores ⇒ nothing qualifies ⇒ no candidate. Becomes meaningful only after
real held-out captures + ~a week of trust/promotion host jobs. Recommended next step before
v2: the telemetry pause (deploy F+G1–G6+v1 to `stash`, wire the four host jobs, collect a
week of signal).

## How v2 differs
v2 adds a **live-config apply path**: `register_reverser("live_site_config", …)` + a per-site
operator **grant** that flips `participation_eligible` True only for granted sites;
promotion becomes a gated-apply with its own 24h fail-closed window; behavioral/template
deltas enter scope there, with their own evidence basis, still never self-confirming
findings. **Higher autonomy = register more reversers + grant more sites, never remove a
gate.** The fixed human-only set is unchanged.

## Release facts
Version 3.66.132 · cockpit 143→146 (+3 GET, POST 21) · ENDPOINT_CATALOG 878→881 ·
FUNCTION_INDEX 1057 · new files: `tools/autonomy_staging.py`,
`tests/test_v3_66_132_staged_candidate_autonomy.py`, `docs/staged_candidate_autonomy.md` ·
edited: `cockpit_console.py` (routes/pages/nav), `cockpit_core.py` (`review_decide`
delegation). Tests 121/121; zero-regression gate clean (53 pre-existing sandbox-unrunnable
failures, unchanged).
