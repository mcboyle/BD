# Staged Config Candidates — v1 Autonomy Wire

*Cockpit area: **Governance → Staged Candidates**. Module: `tools/autonomy_staging.py`.
Shipped v3.66.132.*

## What v1 does

It is the **first real autonomy wire**. When a site's evidence qualifies (oracle tier 3 +
trust above the floor + a registered reverser + within blast radius + not pinned), the
system autonomously maintains a **confined, reversible staged config candidate** for that
site, and registers it for review with a fail-closed 24-hour window.

It exercises the whole chain end to end on the lowest-risk write target:

```
oracle / trust gates  →  staged candidate apply  →  record_change("staging_json", …)
  →  register_pending(…, "C")  →  fail-closed 24h window  →  auto-revert if not accepted
  →  transition / audit log
```

The maintenance loop (`maintain_all`) and the fail-closed sweep (`sweep_review_windows`) are
**host-scheduled** — run from cron/CLI alongside `decay_all_trust` and `scan_and_record`.
There is no button for them in the cockpit.

## What v1 deliberately does NOT do

- No production `sites_config.json` write.
- No promotion of a candidate to live config (that stays manual).
- No login / template / live-extraction change.
- No behavioral mutation — the candidate's behavioral fields are a **credential-redacted,
  byte-identical copy** of live; the system authors only an `evidence` annotation.
- No corpus writes, no debt retirement, no finding confirmation/falsification, no automation
  policy change, no credential handling, no captures, no third-party interaction.

`participation_eligible` (the authority to auto-apply to **live**) stays `False` and is not
consulted. v1 uses a separate, strictly weaker authority, `staging_eligible` — sound because
a staged write auto-reverts and never affects production.

## Credential safety

The live site block contains secrets (`username`, `password`, cookie paths). The staged
candidate embeds only a **credential-redacted projection** of the behavioral fields —
secrets and PII are dropped and never written to the staging file or shown in the cockpit.
The detail view lists exactly which keys were embedded and which were redacted.

## Reviewing in the cockpit

- **Staged Candidates** (`/api/staging/candidates`, `/api/staging/status`) — pending
  candidates with oracle tier, trust, the **behavioral-unchanged** check, and the revert
  deadline.
- **Staged Candidate** detail (`/api/staging/candidate?site=…`) — the system-authored
  evidence, a byte-identical-vs-live confirmation, the embedded vs redacted keys, a
  rollback-preview, the `change_id`, and the deadline.

All three are read-only GET — there is no new POST.

### Accepting / rejecting

Acceptance reuses the existing audited review path (`/api/review/decide`, which now
delegates a pending `change_id` to `agr.mark_reviewed`):

- **Reject** → reverts the staged candidate immediately.
- **Accept** → **blesses** the candidate and stops the fail-closed clock. It does **not**
  promote to live — the candidate simply persists as a staged, accepted artifact. Promoting
  it to `sites_config.json` is a separate manual step.
- **Do nothing** → the fail-closed sweep auto-reverts the candidate at the 24-hour deadline.
  Silence means revert.

## In the sandbox

Against empty oracle/trust stores, nothing qualifies, so `maintain_all` stages nothing and
the views are empty — by design. The wire is **built dark**: it becomes active only once
real held-out captures exist and a week of `decay_all_trust` / `scan_and_record` has
accrued, so tier-3 means something true.

## How v2 would differ

v2 adds a **live-config apply path**: register one new reverser
(`register_reverser("live_site_config", …)` — restore the prior live block atomically with a
backup) and a **per-site operator grant** that flips `participation_eligible` to `True` only
for explicitly granted sites. Promotion then becomes a gated-apply with its own 24-hour
fail-closed window. Behavioral deltas (actual template changes) enter scope only there, with
their own evidence basis — still never self-confirming a finding. **Higher autonomy comes
from registering more reversers and granting more sites, never from removing a gate.**
