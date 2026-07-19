# HANDOFF — v3.66.134 (Autonomy: consolidation — kind-aware authority + Class-C harness)

## What shipped
Consolidated all Class-C apply autonomy behind one harness and one authority model, and
**activated the per-site gate computation** (formerly a hardcoded `False` stub) — dark by
default. H and v1 were refactored onto the harness with **no behavior change** (95→ all green).

### New
- **`tools/autonomy_apply.py`** — the generic harness.
  - `register_apply_kind(kind, *, gate, current, proposer, applier, reverser, corroborate=None,
    validator=None, backup=None, unchanged=None, transition=None, target_ref=None,
    action_class="C", transition_field=None)`. Requires a reverser; rejects pinned/unsafe kind
    names (belt-and-suspenders); registers the reverser with the guardrail engine.
  - `apply_for_kind(site, kind, *, by)` — the single orchestration (gate → propose →
    corroborate → before/after → idempotency → backup → record_change → apply →
    register_pending → validate→revert-on-miss → transition).
  - `apply_all(kind, *, by, sites=None)`.
  - **Single authority read surface** (lazy-imports oracle/eligibility): `registered_kinds`,
    `authority_kinds`, `authority_grants(kind=None)`, `authority_pending(kind=None)`,
    `authority_change(id, kind=None)`, `authority_status(kind=None)`. The cockpit Authority
    routes and the `/api/live/*` compat aliases both read from these.
- **`tests/test_v3_66_134_authority_consolidation.py`** — migration, per-(site,kind) isolation,
  reverser-required, unsafe-kind rejection, gate activation + dark-by-default (all three
  conditions), suspend/expire blocks, reconcile contraction-only (never create/un-suspend), H
  unchanged via harness (apply + scope + fail-closed), v1 separate-gate unchanged,
  Authority read-only + no new POST, **/api/live ≡ /api/authority for live_site_config**,
  harness-has-no-grant-writer.

### Changed
- **`tools/autonomy_oracle.py`** — `_now_iso`, `_normalize_grants` (old→nested), `_load_grants`;
  `class_c_site_eligible(site, kind="live_site_config")` is now **computed** (grant ∧ Class C
  auto ∧ tier ≥ 3), dark by default.
- **`tools/autonomy_eligibility.py`** — `evaluate_site(site, *, kind="live_site_config", …)`,
  `apply_path_exists(kind="live_site_config")` (→ `agr.has_reverser(kind)`),
  `can_participate(site, …, *, kind="live_site_config")`.
- **`tools/autonomy_grant.py`** — per-(site, kind): `grant_site/revoke_site/unsuspend_site
  (site, *, kind=…, by, …)` human-only; `reconcile_grants` sweeps all (site, kind), auto-suspend
  only; `is_active(site, kind=…)`; `grant_overview`; normalize-on-read; nested atomic write;
  `--kind` CLI flag. Store: `governance/oracle/site_auto_grants.json` (+ `grants_log.jsonl`).
- **`tools/autonomy_live.py`** (H) — registers `live_site_config` via `register_apply_kind`
  (late-binding wrappers); `maintain_live_config`/`maintain_all_live` delegate to the harness;
  `live_status/grants/pending/change` are now thin **compat aliases** over
  `aap.authority_*(kind="live_site_config")`. Kept `LIVE_TARGET_KIND`, `_restore_live_block`,
  `_atomic_write_config`, `_backup_sites_config`, the four hook functions.
- **`tools/autonomy_staging.py`** (v1) — registers `staging_json` via the harness; keeps the
  **weaker** `staging_eligible` gate (does NOT join the grant model / does NOT consult
  `participation_eligible`); custom `unchanged` (behavioral + evidence) and
  `transition_field="staged_candidate"` preserve v1 exactly.
- **`tools/cockpit_console.py`** — Authority view replaces the Live Config page; nav
  `Live Config` → `Authority`; +5 read-only `/api/authority/*`; kept 4 `/api/live/*` as compat
  aliases. Cockpit **150 → 155 unique paths**, POST **21** (unchanged).
- Version `3.66.134`; `ENDPOINT_CATALOG.md` 885 → **890**; `FUNCTION_INDEX.md` **1057**
  (harness not indexed — only app/runner/db/login/extractors).

## Invariants preserved (verify on any change here)
- **Authority can auto-suspend (contraction) but never auto-create or auto-unsuspend.** Grants
  are human-only (CLI).
- **No kind applies without a registered reverser** (`apply_path_exists(kind)` =
  `has_reverser(kind)`).
- **Dark by default**: no grant / Class C not at auto / no tier-3 ⇒ `eligible False`.
- **Granting one kind never grants another.**
- **No new POST**; Authority view read-only; accept/reject reuse `/api/review/decide`.
- **`/api/live/*` ≡ `/api/authority/*` for `live_site_config`** (one reader).
- The 10 permanently-human actions are never a `target_kind` and never registrable.

## Test / build / gate (this sandbox)
- Env every shell: `export BD_DISABLE_KEEPALIVE=1 BD_ROOT=/home/claude/work
  PYTHONPATH=/home/claude/work BD_HOME=/home/claude/bd_home`; clear pycache.
- Affected sweep (30 files): **540/540**. Contract/drift/posture + cockpit + 134: **82/82**.
- Build: `python3 tools/build_release.py --skip-tests --out /mnt/user-data/outputs`.
- Pre-existing sandbox-unrunnable failures = **53** (network/SSRF/provider/DB/keyring +
  1 runner canary) — unchanged; none touch autonomy/eligibility/cockpit.

## Next
- I/J/K become thin `register_apply_kind(…)` registrations (operational_rows / library_layout /
  held-out-assist). Granting each kind per site is a separate human decision.
- Recommended operational step before granting anything: deploy to `stash`, wire host cron
  (`decay_all_trust`, `scan_and_record`, staging `maintain_all`, `sweep_review_windows`, H's
  `maintain_all_live`, `reconcile_grants`), collect ~1 week of real held-out/decay/rollback
  signal. **Do not grant any (site, kind) before real tier-3 accrues.**
