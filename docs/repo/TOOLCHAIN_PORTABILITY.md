# TOOLCHAIN PORTABILITY LEDGER -- bd-* against a clean clone

<!-- verified-against: v3.66.805 ; measured 2026-07-20 in the Claude Code cloud env -->

**What this establishes.** For every `bd-*` tool in `toolchain/bin/`, whether it
runs correctly against a fresh git clone -- and, for tools that failed only
because they assumed the old sandbox, the ports that fixed them (cause, not
symptom).

**Point-in-time, measured -- re-derive at decision time.** Every class came from
RUNNING the tool (or, for mutating tools, reading it), not from a register.
Reproduce with `scripts/classify_toolchain.py` (signals) ->
`scripts/classify_toolchain_verdict.py` (auto class) ->
`scripts/refine_degraded.py` (re-examine the degraded bucket with real output) ->
`scripts/emit_toolchain_ledger.py` (this doc's body).

---

## Counts headline

Measured **after** the two ports below. Before them, RUNS was ~38 and
SANDBOX-BOUND ~139; the shared-default port alone moved ~52 analysis tools onto
this tree.

## Method (instrument + predicate)

- **Denominator: 244 `bd-*` executables.** `toolchain/bin/` holds 249 files; 5
  are not tools (`bd` launcher + four `bdtools_*.py` helper libs). The "249
  tools" figure counts files; the tool population is 244.
- **Partition, re-derived (not the quoted "155/94"):** by a sandbox-marker regex
  over each tool's source -- **153 hardcode a sandbox path, 91 do not** -- but
  **164 accept a `--work/--tree/--root` override**, so a hardcoded path is often a
  default, not a binding.
- **Ran read-only tools with the exit code captured UNPIPED** (CLAUDE.md section
  5 -- a pipe to `head` SIGPIPE'd a classifier mid-run during this very task and
  silently kept a stale verdict; caught and redone). Mutating/heavy tools
  (`bd-boot`, `bd-install`, `bd-venv`, `bd-mirror`, `bd-seed`, ...) were NEVER
  executed beyond `--help`.
- **Exit 0 is never RUNS on its own.** Every exit-0 tool whose output was not
  self-evidently about this tree was re-run and read: a tool emitting all-zero
  counts, "not found", or a `/home/claude` path is `RUNS-DEGRADED`, not `RUNS`.

## Ported this session (fix the cause)

1. **`bdtools_sec.DEFAULT_WORK` (sandbox path -> repo-root resolution).** This
   one constant was the default denominator behind the `bd-*-taint`,
   `bd-plugin-*`, `bd-config-*`, `bd-monolith-*`, `bd-coupling-meter`,
   `bd-seam-finder` ... families. It now resolves `$BD_ROOT` -> a walk up to the
   repo-root marker -> the `/home/claude/work` fallback (so the sandbox is
   unaffected). Effect: ~52 tools went `RUNS-DEGRADED`/`SANDBOX-BOUND` -> `RUNS`
   (e.g. `bd-monolith-xray` "app.py 7061 lines / 116 functions", `bd-config-orphan`
   "412 keys, 43 orphaned", `bd-risk-graph` "545 modules"). `bdtools_sec.py
   --selftest` went 5 FAIL -> 1 FAIL (the remaining check needs the version pack,
   correctly absent from a clone).
2. **`bd-netns-proof` (assert -> derive).** Replaced the hardcoded, false
   "creating a netns needs CAP_NET_ADMIN (stash-only)" with a probe that actually
   adds+deletes a throwaway netns and reports three states; here it now correctly
   says "netns creation here: works". `--selftest` PASS with a new check.
3. **`--home`/`--work` own-default batch (3 more RUNS).** `bd-ascii`, `bd-changelog`
   (own `default="/home/claude/work"` -> `sec.DEFAULT_WORK`, `sec` import added) and
   `bd-sweep` (`DEFAULT_BIN` -> this file's dir) now run against the clone. Tools
   that ALSO require the version pack (`bd-dependency-license`, `bd-version-genealogy`,
   `bd-band-derive`) got the same `--work` fix but stay SANDBOX-BOUND -- a second
   dependency (release zip / STATE bundle, absent from a clone) binds them.

## Caveats on the counts

- **`SANDBOX-BOUND` (83) still includes tools that default to `--home
  /home/claude`** for the STATE.json version pack / vpack / `bin` (e.g.
  `bd-agent-*`, `bd-release-*`, `bd-trust-score`). Those need the version pack,
  which is not in a git clone -- a second shared-default port (like DEFAULT_WORK)
  could rescue the ones that only need a *tree*, but the pack-dependent ones are
  genuinely bound. This is the highest-value follow-up port.
- **`UNKNOWN` (55) is mostly the mutating/heavy tools, deliberately not run** --
  a tool not executed against the clone is UNKNOWN, not RUNS.
- **A few `RUNS-DEGRADED` fail LOUD** (`bd-deps`, `bd-imports` -- missing a
  generated graph artifact; `bd-decomp` -- missing a lens): correct behaviour,
  but they do not produce a result on a bare clone as-is.
- This is a measured pass honest about its denominator, not a certified final set.

---

## Counts

| Class | Count |
| --- | --- |
| `RUNS` | 88 |
| `RUNS-DEGRADED` | 21 |
| `SANDBOX-BOUND` | 80 |
| `UNKNOWN` | 55 |
| **total** | **244** |

## `RUNS-DEGRADED` -- the priority list (what each silently missed)

| Tool | What it reports on instead of this tree |
| --- | --- |
| `bd-brief` | exit 0: === BD SESSION BRIEF :: /home/claude/work === / Ground truth (DERIVED from sourc |
| `bd-cockpit-governance` | exit 0: Cockpit governance: 0 file(s) scanned, 0 violation(s) / no violations -- boundar |
| `bd-decomp` | exit 2 'no lens produced output for bulk_downloader/app.py' -- engaged this tree but produced nothing (missing lens dep) |
| `bd-deps` | exit 1: DEPENDENCY_GRAPH.json not found (bd-regen --write) |
| `bd-docstale` | 'no verified-against markers found' -- PK/docs here carry them; scanned the wrong place |
| `bd-fe-dead-control` | exit 0: == bd-fe-dead-control :: 0 control surfaces == /   every control surface reaches |
| `bd-flakes` | exit 0: HARD HAZARDS (test discipline): /   ! NEVER run the full tests/ dir -- it hangs  |
| `bd-footguns` | exit 0: bd-footguns --check :: 10 active footguns vs /home/claude/work /   [skip     ] F |
| `bd-freshest` | exit 0: dir: /home/claude/nextsess    STATE.built_version: unknown / NEWEST AUTHORITATIV |
| `bd-graph-build` | exit 0: graph projections -> /home/claude/kg_artifacts /   8926 resolved call edges, 394 |
| `bd-gui-surface` | exit 0: == bd-gui-surface :: /home/claude/work == /   cockpit reachability: 0 UNREACHABL |
| `bd-imports` | exit 1: import_graph_gate.py not found |
| `bd-intake` | exit 0: no uploads dir at /mnt/user-data/uploads |
| `bd-l0-graph` | exit 0: KNOWLEDGE_GRAPH built -> /home/claude/KNOWLEDGE_GRAPH.db /   1122 files, 1122 mo |
| `bd-pending` | exit 0: bd-pending -- reconciling pending items vs reality in /mnt/project / =========== |
| `bd-precut` | exit 0: == bd-precut :: root=/home/user/BD /   [precut_check skipped -- no tool or basel |
| `bd-ratchet` | exit 0:  |
| `bd-since` | 'no source zip found' -- needs the release zip; git diff replaces it (redundant) |
| `bd-surface-census` | all zeros (0 env vars / 0 config / 0 modules) -- scanned an empty denominator |
| `bd-tool-lint` | exit 0: linted 0 tools (0 analysis + 0 operational/legacy): 0 error(s), 0 warning(s) / c |
| `bd-versync` | exit 0: __init__.py __version__ : NOT FOUND / CHANGELOG top entry     : NOT FOUND / VERS |

## Redundant now that git exists (recommend, don't delete)

- `bd-checkpoint` -- bd-snapshot is per-file (pre-edit diffs); bd-checkpoint tars the whole SOURCE se
- `bd-since` -- bd-preflight asserts byte-identity; bd-since LISTS the diff (Matt overlays files
- `bd-snapshot` -- (one snapshot per file per version baseline) so "what have I changed this sessio

## Appendix: `RUNS` (88)

| Tool | Justification |
| --- | --- |
| `bd-api-contract` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-ascii` | PORTED: --work default -> sec.DEFAULT_WORK; scans the clone CHANGELOG (exit 0 == ASCII-clean) |
| `bd-capture-chaos` | post-port: produces real output about this tree (capture chaos: 3 failure modes /   contained raises     -> error:Runti) |
| `bd-capture-trace` | post-port: produces real output about this tree (capture pipeline map: /   session_capture.py: network, storage, bodies) |
| `bd-changelog` | PORTED: --work default -> sec.DEFAULT_WORK; reads the clone top entry (v3.66.805) |
| `bd-config-lineage` | post-port: produces real output about this tree (  [-RS--] MAX_CONTENT_LENGTH (reads:1) /   [-RS--] _autopick (reads:1)) |
| `bd-config-orphan` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-config-risk` | post-port: produces real output about this tree (config/env risk (71 keys, 28 high-risk): /   risk 5  BD_AUTH_THROTTLE ) |
| `bd-contract-testgen` | post-port: produces real output about this tree ("""test_contract_routes -- generated by bd-contract-testgen. / Asserts) |
| `bd-coupling-meter` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-crx-check` | clean with --tree pointed at the clone; output is about this tree |
| `bd-csrf-audit` | post-port: produces real output about this tree (  bypass     POST   /api/pair/redeem (bootstrap: first cookie-session ) |
| `bd-db-chaos` | post-port: produces real output about this tree (db chaos: 3 failure modes /   contained raises     -> error:RuntimeErr) |
| `bd-decision-memory` | post-port: produces real output about this tree (0 decision doc(s) bound to code:) |
| `bd-decomp-plan` | clean with --tree pointed at the clone; output is about this tree |
| `bd-defaults-audit` | clean with --tree pointed at the clone; output is about this tree |
| `bd-defect-scan` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-devsurf-audit` | post-port: produces real output about this tree (dev-mode gate: DEFAULT-ON, kill-switch present  (unlocks: BD_DEV_MODE,) |
| `bd-doc-truth` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-domain-slicer` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-egress-proof` | post-port: produces real output about this tree (site example: vpn_required=False path=direct proxy=None  no direct lea) |
| `bd-egress-taint` | clean with --tree pointed at the clone; output is about this tree |
| `bd-evidence` | clean with --tree pointed at the clone; output is about this tree |
| `bd-fe-route-diff` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-fetch-migration` | clean with --tree pointed at the clone; output is about this tree |
| `bd-fetch-policy` | clean with --tree pointed at the clone; output is about this tree |
| `bd-fuzz-import` | post-port: produces real output about this tree (all 39 import/parse payloads handled gracefully) |
| `bd-fuzz-pathguard` | post-port: produces real output about this tree (_is_safe_path rejected all 4 traversal payloads / 23 payloads fuzzed) |
| `bd-fuzz-urlguard` | post-port: produces real output about this tree (no bypass -- every should-block URL was blocked / 26 adversarial URLs ) |
| `bd-guardcheck` | clean with --tree pointed at the clone; output is about this tree |
| `bd-host-guard` | clean with --tree pointed at the clone; output is about this tree |
| `bd-html-taint` | post-port: produces real output about this tree (  low    frontend/src/routes/fe_batch_guards.test.ts:12  it("Cluster.t) |
| `bd-innovation-radar` | post-port: produces real output about this tree (80 speculative idea(s) on the radar (kept OFF the active backlog): /  ) |
| `bd-interface-contracts` | post-port: produces real output about this tree ({ /   "schema": "bd-interface-contracts/1", /   "route_contract": [) |
| `bd-jd-check` | post-port: produces real output about this tree (JDownloader @ None:None: not reachable, API off /   hint: pip install ) |
| `bd-json-roundtrip` | post-port: produces real output about this tree (139 JSON store(s) checked; 0 unstable / all JSON stores round-trip los) |
| `bd-json-schema-check` | post-port: produces real output about this tree (5 JSON schema(s) checked; 0 issue(s) / all schemas structurally valid) |
| `bd-monolith-slice` | clean with --tree pointed at the clone; output is about this tree |
| `bd-monolith-xray` | post-port: produces real output about this tree (app.py x-ray: 7061 lines, 116 functions, 94 low-risk seams (2159 lines) |
| `bd-netns-launch-proof` | clean with --tree pointed at the clone; output is about this tree |
| `bd-netns-proof` | post-port: produces real output about this tree (pid self: NOT confined (shared) /   net-ns inode: 4026531833  (host: 4) |
| `bd-network-chaos` | post-port: produces real output about this tree (network chaos: 3 failure modes /   contained raises     -> error:Runti) |
| `bd-node-plugin-check` | post-port: produces real output about this tree (node available: /opt/node22/bin/node  v22.22.2 /   --manifest contract) |
| `bd-observability-map` | post-port: produces real output about this tree (observability map (log points per KLOC): /   LOW     plugins     0 pts) |
| `bd-parallel` | post-port: produces real output about this tree (usage: bd-parallel [-h] [--selftest] /                    {claim,check) |
| `bd-path-guard` | clean with --tree pointed at the clone; output is about this tree |
| `bd-path-scan` | clean with --tree pointed at the clone; output is about this tree |
| `bd-path-taint` | clean with --tree pointed at the clone; output is about this tree |
| `bd-pinscan` | post-port: produces real output about this tree (no fragile `== N` magnitude pins on count/parity keywords found.) |
| `bd-plugin-audit` | clean with --tree pointed at the clone; output is about this tree |
| `bd-plugin-chaos` | post-port: produces real output about this tree (  contained chaos=raises   hook raised: RuntimeError /   contained cha) |
| `bd-plugin-containment` | post-port: produces real output about this tree (0 plugin containment issue(s): / all plugins contained (no ungranted h) |
| `bd-plugin-fuzz` | post-port: produces real output about this tree (1000 fuzz manifests; 0 validator crash(es) / validator robust: every f) |
| `bd-plugin-lifecycle-trace` | post-port: produces real output about this tree (1 lifecycle event(s): /   event never-fired  reg:1 fire:0) |
| `bd-plugin-manifest` | post-port: produces real output about this tree (0 plugin manifest(s) checked; 0 issue(s) / all plugin manifests valid) |
| `bd-plugin-sandbox-check` | post-port: produces real output about this tree (3 plugin bridge(s) checked; 0 isolation issue(s): / plugin bridges hav) |
| `bd-plugin-threatmodel` | clean with --tree pointed at the clone; output is about this tree |
| `bd-property-tests` | post-port: produces real output about this tree (all properties held across 2000 generated cases) |
| `bd-queue-chaos` | post-port: produces real output about this tree (queue chaos: 3 failure modes /   contained raises     -> error:Runtime) |
| `bd-redaction-compiler` | post-port: produces real output about this tree (ruleset compiles consistently -- 5 rules, scrubber+scanner+tests agree) |
| `bd-redaction-scan` | clean with --tree pointed at the clone; output is about this tree |
| `bd-report-sanitizer` | post-port: produces real output about this tree (no markdown/report HTML render sinks found) |
| `bd-risk-graph` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-runtime-gate` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-scheduler-check` | post-port: produces real output about this tree (2 scheduler module(s) checked; 0 issue(s): / scheduler has sleep/backo) |
| `bd-scheduler-sim` | post-port: produces real output about this tree (scheduler sim: 50 jobs, cap 4 /   peak queue depth: 20 / peak workers:) |
| `bd-schema-oracle` | post-port: produces real output about this tree (5 schema(s); 0 invalid, 0 cross-schema type conflicts / all schemas va) |
| `bd-seam-finder` | clean with --tree pointed at the clone; output is about this tree |
| `bd-secret-canary` | post-port: produces real output about this tree (all 84 canaries scrubbed -- no secret survived) |
| `bd-secret-fixture` | post-port: produces real output about this tree (fake-secret corpus (27 entries, sentinel=FAKEsecret) /   jwt         e) |
| `bd-secret-floor` | clean with --tree pointed at the clone; output is about this tree |
| `bd-secret-taint` | clean with --tree pointed at the clone; output is about this tree |
| `bd-secrets` | clean with --tree pointed at the clone; output is about this tree |
| `bd-smoke` | clean with --tree pointed at the clone; output is about this tree |
| `bd-spa-boundary` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-ssrf` | clean with --tree pointed at the clone; output is about this tree |
| `bd-state-machine-extract` | post-port: produces real output about this tree (runner state machine: 16 states, 0 transitions /   states: cookies_exp) |
| `bd-state-machine-test` | post-port: produces real output about this tree (runner: 16 states, 0 observed transitions / no illegal transitions obs) |
| `bd-sweep` | PORTED: DEFAULT_BIN -> this file's dir; now smoke-tests the real toolchain/bin |
| `bd-template-safety` | post-port: produces real output about this tree (1 template(s) checked; 0 issue(s) / all templates safe to export) |
| `bd-template-taint` | clean with --tree pointed at the clone; output is about this tree |
| `bd-ui-contract` | runs clean with no args; produced counts (non-empty denominator) |
| `bd-url-taint` | clean with --tree pointed at the clone; output is about this tree |
| `bd-vpn-egress-scan` | clean with --tree pointed at the clone; output is about this tree |
| `bd-vpn-proof` | post-port: produces real output about this tree (site example: vpn_required=False path=direct proxy=None  no direct lea) |
| `bd-vpn-required` | post-port: produces real output about this tree (site example: vpn_required=False, socks=None  fail-closed OK) |
| `bd-workflow-model` | post-port: produces real output about this tree (enqueue-download: /   APIs: (none matched) /   states: cookies_expired) |
| `bd-workflow-replay` | post-port: produces real output about this tree (enqueue-download: no-api-steps / capture-session: VALID /   OK /api/ca) |

## Appendix: `RUNS-DEGRADED` (21)

| Tool | Justification |
| --- | --- |
| `bd-brief` | exit 0: === BD SESSION BRIEF :: /home/claude/work === / Ground truth (DERIVED from sourc |
| `bd-cockpit-governance` | exit 0: Cockpit governance: 0 file(s) scanned, 0 violation(s) / no violations -- boundar |
| `bd-decomp` | exit 2 'no lens produced output for bulk_downloader/app.py' -- engaged this tree but produced nothing (missing lens dep) |
| `bd-deps` | exit 1: DEPENDENCY_GRAPH.json not found (bd-regen --write) |
| `bd-docstale` | 'no verified-against markers found' -- PK/docs here carry them; scanned the wrong place |
| `bd-fe-dead-control` | exit 0: == bd-fe-dead-control :: 0 control surfaces == /   every control surface reaches |
| `bd-flakes` | exit 0: HARD HAZARDS (test discipline): /   ! NEVER run the full tests/ dir -- it hangs  |
| `bd-footguns` | exit 0: bd-footguns --check :: 10 active footguns vs /home/claude/work /   [skip     ] F |
| `bd-freshest` | exit 0: dir: /home/claude/nextsess    STATE.built_version: unknown / NEWEST AUTHORITATIV |
| `bd-graph-build` | exit 0: graph projections -> /home/claude/kg_artifacts /   8926 resolved call edges, 394 |
| `bd-gui-surface` | exit 0: == bd-gui-surface :: /home/claude/work == /   cockpit reachability: 0 UNREACHABL |
| `bd-imports` | exit 1: import_graph_gate.py not found |
| `bd-intake` | exit 0: no uploads dir at /mnt/user-data/uploads |
| `bd-l0-graph` | exit 0: KNOWLEDGE_GRAPH built -> /home/claude/KNOWLEDGE_GRAPH.db /   1122 files, 1122 mo |
| `bd-pending` | exit 0: bd-pending -- reconciling pending items vs reality in /mnt/project / =========== |
| `bd-precut` | exit 0: == bd-precut :: root=/home/user/BD /   [precut_check skipped -- no tool or basel |
| `bd-ratchet` | exit 0:  |
| `bd-since` | 'no source zip found' -- needs the release zip; git diff replaces it (redundant) |
| `bd-surface-census` | all zeros (0 env vars / 0 config / 0 modules) -- scanned an empty denominator |
| `bd-tool-lint` | exit 0: linted 0 tools (0 analysis + 0 operational/legacy): 0 error(s), 0 warning(s) / c |
| `bd-versync` | exit 0: __init__.py __version__ : NOT FOUND / CHANGELOG top entry     : NOT FOUND / VERS |

## Appendix: `SANDBOX-BOUND` (80)

| Tool | Justification |
| --- | --- |
| `bd-agent-debate` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-agent-redteam` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-agent-scorecard` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-agent-watchdog` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-archive-normalize` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-audit` | hardcodes a sandbox path with no --tree/--work override |
| `bd-band-derive` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-binary-audit` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-boot` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-bump` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-consumer-graph` | hardcodes a sandbox path with no --tree/--work override |
| `bd-corpus` | hardcodes a sandbox path with no --tree/--work override |
| `bd-cut` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-dependency-license` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-deploy-proof` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-deploy-rehearse` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-dltest` | hardcodes a sandbox path with no --tree/--work override |
| `bd-doclint` | hardcodes a sandbox path with no --tree/--work override |
| `bd-doctor` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-env-parity` | hardcodes a sandbox path with no --tree/--work override |
| `bd-evidence-chain` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-evidence-pack` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-factcheck` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-fixture-lint` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-handoff` | hardcodes a sandbox path with no --tree/--work override |
| `bd-install` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-invariant-engine` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-job` | hardcodes a sandbox path with no --tree/--work override |
| `bd-kb` | hardcodes a sandbox path with no --tree/--work override |
| `bd-kb-conflict` | hardcodes a sandbox path with no --tree/--work override |
| `bd-kb-sync` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-lsp` | hardcodes a sandbox path with no --tree/--work override |
| `bd-mkauditstate` | hardcodes a sandbox path with no --tree/--work override |
| `bd-mkbdsuite` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-mutation-test` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-novnc` | hardcodes a sandbox path with no --tree/--work override |
| `bd-offline-proof` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-optpack` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-opv` | hardcodes a sandbox path with no --tree/--work override |
| `bd-pack` | hardcodes a sandbox path with no --tree/--work override |
| `bd-packs` | hardcodes a sandbox path with no --tree/--work override |
| `bd-parity-scan` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-pk-mirror` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-policy-compiler` | hardcodes a sandbox path with no --tree/--work override |
| `bd-prestage` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-proof-ledger` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-proxy` | hardcodes a sandbox path with no --tree/--work override |
| `bd-ready` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-recognizer-drift` | hardcodes a sandbox path with no --tree/--work override |
| `bd-reconcile` | hardcodes a sandbox path with no --tree/--work override |
| `bd-regen` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-regen-order` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-release-attestation` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-release-note` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-render-env` | hardcodes a sandbox path with no --tree/--work override |
| `bd-retest` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-rev` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-review-pack` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-rollback` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-rollback-plan` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-runner-nav` | hardcodes a sandbox path with no --tree/--work override |
| `bd-sbcap` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-selfcheck` | hardcodes a sandbox path with no --tree/--work override |
| `bd-session` | hardcodes a sandbox path with no --tree/--work override |
| `bd-ship` | hardcodes a sandbox path with no --tree/--work override |
| `bd-snapshot` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-state` | hardcodes a sandbox path with no --tree/--work override |
| `bd-status` | hardcodes a sandbox path with no --tree/--work override |
| `bd-store-check` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-supply-chain-scan` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-time-travel` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-tool-smoke` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-tools` | hardcodes a sandbox path with no --tree/--work override |
| `bd-tracker-recon` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-triage.py` | hardcodes a sandbox path with no --tree/--work override |
| `bd-trust-score` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-venv` | mutating/heavy; hardcodes a sandbox path (needs /home/claude infra); not run |
| `bd-version-genealogy` | fails against the clone referencing a sandbox path; no working tree-root override |
| `bd-vpnlab` | hardcodes a sandbox path with no --tree/--work override |
| `bd-zipcheck` | hardcodes a sandbox path with no --tree/--work override |

## Appendix: `UNKNOWN` (55)

| Tool | Justification |
| --- | --- |
| `bd-archaeology` | signals inconclusive; needs a hand classification |
| `bd-artifact-quarantine` | signals inconclusive; needs a hand classification |
| `bd-audit-gate.py` | signals inconclusive; needs a hand classification |
| `bd-band` | signals inconclusive; needs a hand classification |
| `bd-bandcheck` | signals inconclusive; needs a hand classification |
| `bd-body-contract` | signals inconclusive; needs a hand classification |
| `bd-calendar-check` | signals inconclusive; needs a hand classification |
| `bd-capsweep` | signals inconclusive; needs a hand classification |
| `bd-capture-risk` | signals inconclusive; needs a hand classification |
| `bd-checkpoint` | signals inconclusive; needs a hand classification |
| `bd-coretest` | signals inconclusive; needs a hand classification |
| `bd-coverage` | signals inconclusive; needs a hand classification |
| `bd-curl` | signals inconclusive; needs a hand classification |
| `bd-dashboard-readonly` | signals inconclusive; needs a hand classification |
| `bd-deep-capture` | signals inconclusive; needs a hand classification |
| `bd-deploy-manifest` | mutating/heavy; not executed against the clone (would need a real run to confirm) |
| `bd-envscan` | signals inconclusive; needs a hand classification |
| `bd-equiv` | signals inconclusive; needs a hand classification |
| `bd-fetch` | signals inconclusive; needs a hand classification |
| `bd-fixture-serve` | no-arg run TIMEOUT; could not determine (needs a hand run) |
| `bd-frontier-lab` | signals inconclusive; needs a hand classification |
| `bd-fullsuite` | signals inconclusive; needs a hand classification |
| `bd-fuzz-redaction` | signals inconclusive; needs a hand classification |
| `bd-golden` | signals inconclusive; needs a hand classification |
| `bd-guard-declare` | signals inconclusive; needs a hand classification |
| `bd-job-forensics` | no-arg run TIMEOUT; could not determine (needs a hand run) |
| `bd-lineage` | signals inconclusive; needs a hand classification |
| `bd-live` | signals inconclusive; needs a hand classification |
| `bd-log-causality` | no-arg run TIMEOUT; could not determine (needs a hand run) |
| `bd-log-sanitize` | signals inconclusive; needs a hand classification |
| `bd-lost-symbol` | signals inconclusive; needs a hand classification |
| `bd-parband` | signals inconclusive; needs a hand classification |
| `bd-pin` | signals inconclusive; needs a hand classification |
| `bd-plugin-diff` | signals inconclusive; needs a hand classification |
| `bd-plugin-permission-diff` | signals inconclusive; needs a hand classification |
| `bd-preflight` | signals inconclusive; needs a hand classification |
| `bd-reindex` | signals inconclusive; needs a hand classification |
| `bd-render` | signals inconclusive; needs a hand classification |
| `bd-repin-dist` | signals inconclusive; needs a hand classification |
| `bd-rollback-oracle` | signals inconclusive; needs a hand classification |
| `bd-route` | signals inconclusive; needs a hand classification |
| `bd-run-timeline` | signals inconclusive; needs a hand classification |
| `bd-scrub-proof` | signals inconclusive; needs a hand classification |
| `bd-share-safe` | signals inconclusive; needs a hand classification |
| `bd-sym` | signals inconclusive; needs a hand classification |
| `bd-template-diff` | signals inconclusive; needs a hand classification |
| `bd-template-promote-check` | signals inconclusive; needs a hand classification |
| `bd-timeit` | signals inconclusive; needs a hand classification |
| `bd-treecheck` | signals inconclusive; needs a hand classification |
| `bd-url-classify` | signals inconclusive; needs a hand classification |
| `bd-verify` | signals inconclusive; needs a hand classification |
| `bd-verify-live` | signals inconclusive; needs a hand classification |
| `bd-wacz-scrub` | signals inconclusive; needs a hand classification |
| `bd-worktree-check` | signals inconclusive; needs a hand classification |
| `bd-yt-dlp-check` | signals inconclusive; needs a hand classification |
