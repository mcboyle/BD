# HANDOFF — v3.66.133 (Autonomy H: live config apply)

## What shipped
The first **production write**. `tools/autonomy_live.py` can autonomously apply a
`learned`+`scoring` change to a granted, tier-3-corroborated site's live `sites_config.json`,
under a fail-closed window + a pure post-apply validation, fully reversible. Built **dark**:
no grant or no tier-3 evidence ⇒ nothing applies.

## Files
- **NEW `tools/autonomy_live.py`** — apply loop + reverser + pure validation + redacted
  backup + read-only views. Reverser registered at import (`agr.register_reverser(
  "live_site_config", _restore_live_block)`), which is what makes `apply_path_exists()` True.
- **NEW `tools/autonomy_grant.py`** — human-only `grant_site`/`revoke_site`/`unsuspend_site`
  (CLI) + host-only `reconcile_grants` (auto-suspend only). Writes
  `governance/oracle/site_auto_grants.json` + `grants_log.jsonl`.
- **`tools/autonomy_eligibility.py`** — `apply_path_exists()` → `agr.has_reverser(
  "live_site_config")` (was hardcoded `False`). This is the single change that lets
  `participation_eligible` become True once H is loaded (still requires grant + tier-3 + …).
- **`tools/autonomy_guardrails.py`** — `has_reverser(kind)`.
- **`tools/cockpit_console.py`** — +4 GET (`/api/live/status|grants|pending|change`) + nav +
  `PAGES.liveconfig`/`liveconfigchange`. No new POST.
- **`tests/test_v3_66_133_live_config_apply.py`** — full proof set.
- Edited `tests/test_v3_66_126_eligibility_governance.py` — 3 assertions that depended on the
  pre-H "no apply path" invariant now pop/restore the live reverser so they stay
  deterministic regardless of phase import order.
- Tripwire `len(rules) == 146` → `150` (27 files). CHANGELOG entry. ENDPOINT_CATALOG 885.

## Authority model (the invariant, in code)
`participation_eligible` (the live gate) is True only when ALL hold: active grant, Class C
allowed, apply-path (reverser loaded), tier-3 corroborated evidence, trust ≥ floor, not
frozen, blast OK. The system may AUTO-SUSPEND a grant (`reconcile_grants`: trust↓ / tier<3 /
freeze / expiry) — a contraction. It may NEVER auto-grant or auto-un-suspend — expansion is
human-only (CLI). Proven by `TestAuthorityInvariant`.

## Scope (proven byte-for-byte)
The live write rewrites ONLY the target site's `learned` + `scoring`. Every other key
(credentials, login selectors, url, domain, output, wait) and every OTHER site stay
byte-identical. Credentials never enter change records (`before`/`after` keys are exactly
`["learned","scoring"]`) or backups (redacted). `TestScopeAndSecrets`.

## Validation (pure)
`_post_apply_validation` derives the proposed descriptor from the block's `_expectations` and
requires oracle identity/rendition/template-shape agreement with held-out. NO network /
browser / re-download / byte comparison. Fail-closed (no held-out or no identity ⇒ revert).

## How to operate (post-telemetry)
1. Run the telemetry pause (host jobs in cron) ~1 week; confirm a site reaches real tier-3.
2. `python -m tools.autonomy_grant grant <site> --by you --reason "…"`.
3. The host `maintain_all_live` applies corroborated learned+scoring under the fail-closed
   window. Review in cockpit → Review (accept keeps; reject/silence revert).
4. `reconcile_grants` (host) auto-suspends on any safety signal; re-grant/un-suspend is yours.

## Build / gate (verified this session)
- `python3 run_tests.py tests/test_v3_66_133_live_config_apply.py tests/test_v3_66_126_eligibility_governance.py tests/test_v3_66_132_staged_candidate_autonomy.py tests/test_v3_66_130_impact_analysis.py tests/test_v3_66_131_promotion_activity.py tests/test_v3_66_124_oracle_eligibility.py tests/test_v3_66_98_cockpit_console.py` → 157/157.
- Contract/drift/posture + cockpit + H → 83/83.
- Release: `python3 tools/build_release.py --skip-tests --out /mnt/user-data/outputs`.
- Zero-regression gate: pre-existing sandbox-unrunnable failures = **53** (network/SSRF/
  provider/session-keeper). Phased run from the built zip must show total == 53 and
  `test_v3_66_133` not failing.

## Next (deferred, re-recommend after this)
- **Telemetry pause** is the recommended next operational step (above).
- **I** — operational-data housekeeping (history/queue/push_subs row-snapshot reversers).
- **J** — file organization/metadata (manifest reverser; never content).
- **K** — held-out-designation assist + coordinated multi-store (assist-only).
- Rule: more autonomy = register more reversers + grant more sites; never remove a gate; the
  ten-action human-only set is fixed.
- **Override-gated KB merge** (separate session): fold v1 + H deltas into the consolidated KB.
