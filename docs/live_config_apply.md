# Live Config Apply (H) — operator guide

**v3.66.133.** H is the first time BulkDownloader writes its **production**
`sites_config.json` on its own. It is deliberately narrow and heavily gated.

## What H enables
For a site you have **explicitly granted**, when human-designated held-out evidence
corroborates the change (oracle tier 3), the system can autonomously apply a `learned`
(extraction) + `scoring` change to that site's live config — **and only those two blocks** —
under a fail-closed window. A bad change is auto-reverted by either of two controls: a
**pure post-apply validation** (reverts in seconds on a descriptor mismatch) or the
**24-hour fail-closed window** (reverts on silence).

## What H deliberately does NOT do
- No login-field change, **no credentials** read or written.
- No corpus write, no debt retirement, no finding confirm/falsify.
- No automation-policy or posture change, no release approval.
- **No capture execution, no third-party interaction.**
- **Never authors selectors** — it applies the synthesis subsystem's proposal verbatim.
- Never two sites in one in-flight window (blast radius = one site).

## How grants work
Granting is the operator's act of expanding authority for one site. **It is human-only and
done from the CLI/host — there is no grant button in the cockpit.**

```
python -m tools.autonomy_grant grant   <site> --by you --reason "…" [--expires-at ISO8601]
python -m tools.autonomy_grant revoke  <site> --by you --reason "…"
python -m tools.autonomy_grant unsuspend <site> --by you            # human lifts a suspension
python -m tools.autonomy_grant list                                  # read-only
```

A grant is **necessary but not sufficient**. A site is `participation_eligible` (the live
gate) only when ALL hold:
- an active (non-suspended, unexpired) grant,
- Class C allowed by policy,
- the live apply path exists (the reverser is loaded),
- tier-3 corroborated evidence,
- trust above the floor,
- not frozen,
- blast radius OK.

**The system can take authority away on its own, but never give it.** The host job
`reconcile_grants` AUTO-SUSPENDS a grant when trust drops below the floor, oracle tier falls
below 3, automation is frozen, or the grant has expired. It never creates a grant and never
un-suspends one — only you can (via the CLI).

## How rollback works
Every live apply records an immutable `before`/`after` of just `{learned, scoring}` and
registers a fail-closed pending entry. A revert restores the prior block exactly:
- **reject** (cockpit Review → decide) → revert now,
- **silence** → the host sweep reverts at the deadline,
- **validation failure** → revert immediately,
- a reverser error trips the automation **freeze** (fail-closed).
A timestamped, **credential-redacted** snapshot of the config is written before each apply
(last 10 kept) as defense in depth — the reverser's source of truth is the change record.

## How validation works
The post-apply validation is **pure** — it does **no** network fetch, **no** browser action,
**no** re-download, and **no** byte comparison. It derives the proposed media descriptor from
the block's `_expectations` and requires the oracle's identity / rendition / template-shape
agreement with the human-designated held-out captures. No held-out, or no identity
expectation, or any disagreement ⇒ fail ⇒ immediate revert.

## How H differs from v1 (staged candidates)
v1 maintained an **annotation-only staged candidate** — it wrote no production config,
promoted nothing to live, and proposed no behavioral change; its gate was the weaker
`staging_eligible`. H **writes live** `learned`/`scoring` for granted, tier-3 sites under a
fail-closed window; its gate is the stronger `participation_eligible`. v1 is unchanged and
remains the weaker authority; H stacks the live authority on top, behind your grant.

## Cockpit
**Live Config** (Governance nav) is read-only: grant status (active/suspended, tier, trust,
eligibility), pending live changes (with the learned/scoring change flags, deadline, and a
rollback preview). Accept/reject happen through the existing Review path.

## Telemetry required before trusting it operationally
Build H dark and run the telemetry pause first. Wire the host jobs (`decay_all_trust`,
`scan_and_record`, staging `maintain_all`, `sweep_review_windows`, plus H's
`maintain_all_live` and `reconcile_grants`) into cron and collect about a week of real
held-out / decay / rollback / transition signal. **Do not grant any site until tier-3
evidence has actually accrued for it from real held-out captures** — a grant without tier-3
evidence still applies nothing, but the point of the pause is to confirm tier-3 means
something real before you expand authority to any site.
