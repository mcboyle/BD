<!-- verified-against: v3.66.593 -->
# PLUGIN-V3 — SCOPING + PLAN

<!-- authored against live baseline v3.66.593; grounds current state in bulk_downloader/plugins.py -->
<!-- STATUS: SCOPING. The v3 feature SET is an operator decision not yet made. This doc captures the -->
<!-- current v2 system precisely, the enabler that makes a v3 bump cheap, the candidate v3 tracks, and -->
<!-- a phased execution plan template. No cut/build authorized by this doc. -->

Pairs with: `plugins.py` (the system), `PROJECT_GOALS.md` / `AUTOMATION_POLICY.md` (charter/ToS
frame), `RELEASE_DISCIPLINE_TIERS.md` (gating), the `TASK_TRACKER` (rows `PLUGIN-V3-*`).

---

## 0. Where the plugin system stands @593 (the v2 baseline)

The plugin/hooks system (Phase 116, Block Q; expanded @465) is **shipped and stable at
`PLUGIN_API_VERSION = 2`**. What exists today:

| Facet | Current state @593 |
|---|---|
| Extension kinds (5) | `@extractor("site")`, `@hook("event")`, `@processor(priority=)`, `@config_provider()`, `@lifecycle("event")` (GATED) |
| API contract | Manifest `PLUGIN = {..., "api_version": 2, "capabilities": [...]}`; loads iff the plugin's declared API range OVERLAPS `[PLUGIN_API_MIN, PLUGIN_API_MAX]`, ships `[2, 2]` |
| Enable / order | Optional `plugins.json`: `enabled` (allowlist+order), `disabled`, `order`, `allow_full_access`; no file → every non-`_` `*.py` loads (historical behavior) |
| Robustness | Every callback wrapped (exception caught, never propagates); `_FAIL_BUDGET` failures → **quarantine** (surfaced on status page until `clear_quarantine`); optional per-registration timeout on processor/lifecycle |
| Full-access gate | Lifecycle hooks get the live Playwright context/page + raw launch kwargs; **default OFF**; opt in via `allow_full_access` or `BD_PLUGINS_ALLOW_FULL_ACCESS=1` |
| Security model | **NO sandbox.** A loaded plugin runs in-process with full FS/network/config/secrets/session/browser access. Trust = whoever can drop a file in the plugin dir. BD ships no evasion/DRM/challenge-solving plugins. |
| Supporting modules | `plugin_exec.py`, `plugin_kv.py`, `plugin_node.py`, `plugin_py_bridge.py`, `plugins.py` |

**The single most important pre-built enabler:** the R5 API-**range** mechanism. Because a plugin
loads on range *overlap* (not exact major match), `PLUGIN_API_MAX` can be raised to `3` **without
breaking any plugin pinned to `[2,2]`** — the v2→v3 transition is additive by construction, not a
breaking migration. This is why v3 is "a forward feature, not mid-flight": the plumbing to carry it
already exists; only the v3 *feature set* is undefined.

---

## 1. What "v3" is NOT

To keep scope honest:
- v3 is **not** a rewrite. The registry, loader, quarantine, and gate stay.
- v3 is **not** required for correctness — v2 is complete and green. v3 is capability expansion.
- v3 does **not** change the charter frame: no evasion/DRM/challenge-solving; operator owns ToS/legal.

---

## 2. Candidate v3 tracks (OPERATOR PICKS THE SET)

These are grounded in v2's stated limitations and the natural next capabilities. Each is
independently landable behind the range bump. **Pick some subset; this is a menu, not a spec.**

### V3-A — Capability/permission model (addresses the "NO sandbox / trust = file-drop" gap)
The largest posture gap today is all-or-nothing trust. A declarative permission model lets a plugin
request only what it needs and lets the operator grant per-capability.
- Manifest gains a `permissions: ["net", "fs:read", "fs:write:<dir>", "secrets", "browser"]` list.
- Loader enforces at the wrapper boundary (deny-by-default; grant via `plugins.json`
  `granted_permissions`). A plugin using an ungranted capability is quarantined with a clear reason.
- **Not** a true OS sandbox (still in-process) — it is a *declared-intent + operator-consent* layer,
  the same trust model BD uses elsewhere. Document the residual honestly (a malicious plugin can
  still bypass a pure-Python gate); the value is honest defaults + accident containment, not
  adversarial isolation.
- *Optional stretch:* a real isolation backend (subprocess via `plugin_py_bridge`/`plugin_node`,
  or a restricted interpreter) for the `net`/`fs` kinds — high effort, separate wave.

### V3-B — New extension kinds
Add kinds the current 5 don't cover, each a new `capability` + decorator + wrapper:
- `@notifier` — a first-class notification sink (today notifications are core-only; a plugin sink
  would let operators route to arbitrary services without touching apprise).
- `@template_provider` / `@template_transform` — supply or post-process a site template at
  build/onboard time (ties into the automation program's onboarding, roadmap A4).
- `@storage_backend` — pluggable destination for finished media (S3/rclone/etc.), behind
  `fs:write` permission.
- `@ui_panel` — register a read-only cockpit panel (data + render hook), no route authority.

### V3-C — Manifest + lifecycle richness
- Structured `requires` (min BD version, other-plugin deps), surfaced at load with clear skip
  reasons.
- Config schema declaration (`config_schema`) so `@config_provider` plugins validate their inputs.
- A `on_enable`/`on_disable` lifecycle pair (distinct from browser lifecycle) for setup/teardown.

### V3-D — Distribution/integrity (optional, charter-aligned)
- Optional signature verification (`PLUGIN.signature` + an operator-trusted key) so an operator can
  pin "only signed plugins load" — mirrors the cloakbrowser verified-download posture. Off by
  default; does not change the file-drop trust model unless the operator opts in.

### V3-E — Observability
- A `plugin_metrics` surface (per-plugin invocation count, failure count, p50/p95 latency,
  quarantine state) on the status page + `/api/plugins/metrics`, complementing the existing
  quarantine display.

**Recommended minimal v3 (highest value / lowest risk):** V3-A (permission model, declared-intent
tier only) + V3-C (manifest richness) + V3-E (metrics). V3-B and V3-D are additive later waves.

---

## 3. Execution plan (template — applies once the set is chosen)

Every wave follows the standing operating contract: **RED-first TDD, guards byte-identical, band from
the extracted zip, version bump = 3-part edit, ASCII changelog.** `plugins.py` is **not** one of the
7 SHA-pinned guard files, so plugin waves don't need guard-declare — but confirm that against
`STATE.guards` each cut (a new import edge still needs the import-graph baseline re-freeze).

### Wave 0 — Version-carry proof (do first, tiny)
- RED: a test that a plugin declaring `api_version: 3` loads once `PLUGIN_API_MAX` is raised to 3,
  AND a `[2,2]` plugin still loads unchanged (no regression).
- GREEN: raise `PLUGIN_API_MAX = 3`; no feature yet. Proves the carrier end-to-end and locks the
  no-break invariant before any capability lands.

### Wave 1..N — One track per wave (recommended order below)
For each chosen track:
1. **RED-first**: tests for the new capability's happy path + the deny/skip/quarantine path +
   backward-compat (an old plugin unaffected).
2. Implement the decorator/wrapper/manifest field; enforce at the single wrapper boundary.
3. Update the `plugins.py` module docstring (it is the canonical contract) + `plugins.json` schema.
4. Band the plugin test family from the extracted zip; on-stash GREEN before ledger flip.

Recommended sequence (risk-ascending):
- **W1 = V3-C** manifest richness (`requires`, `config_schema`, `on_enable/on_disable`) — additive,
  low risk, unblocks clean validation for later kinds.
- **W2 = V3-E** metrics surface — read-only, no authority change.
- **W3 = V3-A** permission model (declared-intent + operator-consent tier) — the posture headline;
  deny-by-default, quarantine-on-violation.
- **W4 = V3-B** new extension kinds, one kind per sub-cut (`@notifier`, then `@storage_backend`
  behind `fs:write`, then `@template_provider` coordinating with automation A4, then `@ui_panel`).
- **W5 = V3-D** optional signature verification (off by default).
- **(stretch) W6 = V3-A isolation backend** — subprocess/restricted-interp for `net`/`fs`; separate,
  high-effort, only if the declared-intent tier proves insufficient in practice.

### Cross-wave invariants (assert every cut)
- A plugin with **no** manifest still loads (historical behavior) and gets the safe default
  permission set (nothing privileged) under any new gate.
- Full-access gate semantics unchanged unless a wave explicitly revises them (and then RED-first).
- No new default-ON privilege: every new capability is **deny/off by default**, operator opt-in.
- The charter line holds: no shipped evasion/DRM/challenge plugin; new kinds can't smuggle one.

---

## 4. Open questions for the operator (resolve before Wave 1)
1. **Which tracks are in v3?** (Recommend V3-A+C+E minimal; B/D later.)
2. **Permission enforcement depth:** declared-intent + consent only, or invest in a real isolation
   backend? (Recommend start declared-intent; treat isolation as a stretch, evidence-gated.)
3. **Distribution:** is signature verification wanted at all, or does file-drop trust remain the
   model? (Recommend optional/off-by-default if built.)
4. **`@ui_panel`** — does the cockpit/SPA surface want third-party panels, given the GUI/CLI parity
   posture? (Coordinate with the parity ratchet; may be deferred.)

---

## 5. Definition of done (per the chosen set)
- `PLUGIN_API_MAX = 3`, every chosen track landed RED-first, each behind a default-off/deny-default
  gate, `plugins.py` docstring + `plugins.json` schema updated, plugin test family GREEN on-stash,
  7 guards byte-identical throughout, import-graph baseline re-frozen for any new edge, changelog
  ASCII. A `[2,2]` plugin from before v3 still loads and behaves identically (the load-bearing
  backward-compat invariant).

---

## 6. Expansion tracks — beyond the core v3 (candidate, unprioritized)

Additive behind the range bump; each is independently landable. Grouped by the three axes, with
honest risk flags. IDs are for the `TASK_TRACKER` (`PLUGIN-V3-<id>`).

### More ROBUST
- **P-R1 — Self-test contract (`@selftest`).** A plugin declares a self-test BD runs at load AND on
  a cadence; failure → **auto-quarantine before it touches real data**. Turns "fails in production"
  into "never enabled." Cheap, high robustness ROI. Ships with the conformance kit (P-X1).
- **P-R2 — Circuit-breaker with auto-recovery.** Today quarantine is terminal until manual
  `clear_quarantine`. Add a **half-open retry after a cooldown** so a transient failure self-heals;
  notify on state change. (Robustness + a step toward autonomy.)
- **P-R3 — Real resource budgets.** CPU / memory / wall-clock ceilings per plugin (beyond the
  existing per-registration timeout), enforced via the subprocess backend (`plugin_py_bridge` /
  `plugin_node`) with rlimits; breach → quarantine. This is the only *real* containment for the
  `net`/`fs` kinds (pure-Python gates can't isolate an in-process plugin).
- **P-R4 — Per-plugin dependency isolation.** A plugin ships its own `requirements.txt` installed
  into a per-plugin venv via the subprocess backend, so plugin deps can't collide with BD's or each
  other's. Pairs with the V3-A isolation stretch.
- **P-R5 — Dry-run / simulation.** Run a plugin's processors/hooks against a captured payload with
  side effects suppressed, for pre-enable validation.
- **P-R6 — Per-plugin audit trail.** Every privileged action (fs write / net call / secret access)
  logged to a per-plugin audit log. Ties to the V3-A permission model and the operator-responsibility
  charter (findings as kinds/counts, never secret values).

### More FEATURE-RICH
- **P-F1 — Inter-plugin event bus.** Plugins emit custom events other plugins subscribe to (beyond
  BD's core lifecycle events) → composition without core changes.
- **P-F2 — Config-schema → auto-rendered cockpit panel.** A plugin declares `config_schema` (V3-C);
  BD auto-renders a validated config panel (V3-B `@ui_panel`). No hand-written UI per plugin.
- **P-F3 — Plugin-provided recognizers/detectors.** A plugin contributes site-recognition patterns
  into BD's recognizer system; extends detection coverage without touching core. **Charter guard:**
  recognizers describe *observed* structure only — never invent hosts/selectors/URLs.
- **P-F4 — Scaffolding generator (`bd plugin new <kind>`).** Generates a skeleton + manifest + a
  passing self-test per extension kind. DX.
- **P-F5 — Capability negotiation.** A plugin queries host feature flags / versions and adapts
  (graceful degradation across BD versions).
- **P-F6 — State persistence + migrations.** Namespaced, migratable `plugin_kv` state so a plugin can
  evolve its stored schema safely.

### More AUTONOMOUS
- **P-A1 — `@scheduled(cron)` kind.** A plugin registers a recurring task. **This is the bridge to
  the automation program:** an operator can drop in a plugin that runs a drift check, a backup-verify,
  or a digest on a cadence — extending autonomy with no core change.
- **P-A2 — `@automation_policy` kind.** A plugin registers a policy the supervised-autonomy
  controller (automation **A9**) executes. Lets operators extend the autonomy loop itself. **High
  power → must run inside the SAME A0-gated, reversible, approval-bounded envelope as core
  automation; never a bypass.**
- **P-A3 — Operator-gated self-update.** A plugin declares an update source; BD checks + stages a
  **signature-verified, reversible** update for operator confirm (mirrors the automation
  reversibility model). **RISK: supply-chain — only with V3-D signing + an operator-pinned key;
  never auto-apply.**

### Cross-cutting
- **P-X1 — Conformance/certification kit.** A harness a plugin author runs to prove the contract
  (permissions honored, no unhandled exceptions, processors idempotent, self-test passes). Raises the
  ecosystem floor; BD can require "conformant" before enable.

### Honest risk flags (the line that must hold)
- A **marketplace/registry index** (V3-D-adjacent) and **self-update** (P-A3) introduce
  **supply-chain risk** — gate behind signing + operator-pinned trust, never auto-apply, and the
  index must **not** become a distribution channel for evasion/DRM/challenge plugins (the charter
  still forbids BD shipping or endorsing them).
- **`@automation_policy` (P-A2)** hands autonomy *authorship* to plugins — powerful, but bounded by
  the same A0/reversibility/approval gates as core automation.
- The **"no sandbox" reality** persists for pure-Python gates; **P-R3/P-R4** (subprocess isolation)
  are the only real containment — document the residual honestly rather than implying isolation the
  in-process model can't give.

### Suggested priority for the expansion set
Robustness first (they protect everything else): **P-R1 self-test → P-R2 circuit-breaker →
P-R6 audit trail**, then the bridge **P-A1 `@scheduled`** (unlocks plugin-driven automation cheaply),
then feature richness (**P-F2 config panel, P-F4 scaffolding**), then the higher-risk **P-R3/P-R4
isolation**, **P-A2 policy kind**, and **P-A3 self-update** last (evidence + signing gated).
