<!-- generated_against: v3.66.532 (recut) -->
# Code-Intelligence Tooling — Build Deliverables (v3.66.532)

Tooling-build session per `AUDIT_PLAN_v3_66_531.md` §1 / `CODE_INTELLIGENCE_PROGRAM.md` §9.
Read-only on the source tree; all artifacts live under `/home/claude/review/` and ride in
the per-session `version.zip` (volatile). **Attestation:** `guard_touch=false`,
`tracker_write=false`, source tree byte-identical to the 532 zip (2207/2207).

The graph is pinned to **recut 532** source (every module sha verified against the live tree).

---

## Tools built (`/home/claude/review/tools/`, stdlib-first, offline)

| Tool | Purpose | Status |
|---|---|---|
| `l0_extract.py` | AST pass → `KNOWLEDGE_GRAPH.db` (nodes/edges/per-fn facts) | LIVE, deterministic |
| `graph_build.py` | resolve call edges + materialize §3–§12 projections | LIVE |
| `risk_score.py` | composite risk (radon CC + sinks + secret/taint density + prior-defect) → batch re-rank | LIVE (radon via `~/rev`) |
| `defect_patterns.py` | the 18 DP detectors + regression-corpus `--check` gate | LIVE, 7 corpus-gated |
| `seed_review_state.py` | seed the ledger (F0001 + 16 VR-P) + `--check` staleness gate | LIVE |
| `invariants.py` | 10 gated invariants + `--check` (phantom-guard catch) | LIVE |
| `bd-scan.py` | L0 battery (defect_patterns + bandit + vulture) → normalized findings | LIVE |
| `bd-audit-gate.py` | composite gate: defect_patterns + invariants + ledger staleness | LIVE, PASS |

## Battery (offline, throwaway venv `~/rev` — kept off the service venv per TOOLING §1)
radon 6.0.1 · vulture 2.16 · bandit 1.9.4 · detect-secrets 1.5.0 · libcst 1.8.6 ·
semgrep 1.168.0 (installed, not run in the default `bd-scan` — heavy; `--semgrep` opt-in).

---

## Artifacts (`/home/claude/review/artifacts/`)

- **`KNOWLEDGE_GRAPH.db`** (canonical, SCHEMAS §14) — 1005 modules · 7183 functions ·
  55054 edges (call 43608 / contains 7183 / imports 4263) · 0 parse errors.
  Deterministic rebuild → identical content hash, pinned in `KNOWLEDGE_GRAPH.db.sha256`
  (`79f2f6090b9a3707…`).
- **Projections:** `CALL_GRAPH.json` (8534 resolved edges) · `MODULE_CATALOG.json` (1005, L2
  fields null for audit) · `SECURITY_SURFACE.json` (177 sql_fstring · 95 subprocess · 1
  shell=True · 1785 secret sites) · `ERROR_CATALOG.json` · `TAINT_MAP.json` (sink inventory) ·
  `DEAD_CODE.json` (3183 uncalled candidates).
- **`REVIEW_STATE.json`** (ledger, SCHEMAS §1) — 1005 files `unreviewed`; 17 seed findings
  (F0001 + VR-P01..P16) `status:fixed`, each linked to its file + repro test + DP class.
- **`INVARIANTS.json`** — 10 GUARDED invariants (I0001..I0010), 0 phantom, 0 unguarded.
- **`RISK_SCORES.json`** + **`BATCH_ORDER.json`** — per-file composite risk; 57 batches
  re-ranked by measured risk (replaces the SLOC proxy).
- **`SCAN_FINDINGS.json`** — `bd-scan` across 1005 files: 3223 findings
  (defect_patterns 2092 · bandit 1089 · vulture 42; 18 high-severity).
- **`DEFECT_FINDINGS.json`** — defect_patterns standalone scan.

---

## Validation — the linter fires on its own known bugs ✅

`defect_patterns --check` = PASS: all 7 corpus-gated detectors fire on the frozen `*_vuln.py`
fixture and stay silent on `*_fixed.py` — **including the four the plan named (DP-07, DP-08,
DP-11, DP-14)** plus DP-01, DP-03, DP-10. The bugs are fixed in 532, so validation runs against
frozen vuln/fixed pairs in `regression_corpus/` rather than live code. `bd-audit-gate` = PASS
(all three sub-gates green).

## Risk-ranked batch order (top of 57)

`APP-01` (app.py monolith, 0.405) · `COCKPIT-01` (cockpit_console, 0.371) · `RUN-01`
(runner kernel, 0.356/max 0.499) · `REC-01` (extraction_core/recognizer, 0.278) · `APP-02`
(0.229) · `CORE_BD-02` (0.227) · `RUN-02` · `AUTH-01` (secrets, 0.201) · `REC-02` · `CAP-01`
(guard surface, 0.172). Top files: runner.py, app.py, capture_artifact_redact.py, batch_ops.py,
build_template_from_wacz.py — the known-hot set surfaces from measured signal.

---

## Honest limitations (for the audit sessions)

- **TS/TSX is grep-level** (no offline TS compiler) — frontend nodes carry export/fetch/secret
  facts only, not a full call graph. FE batches lean on eslint (`~/rev` real-bin) at audit time.
- **`auth_gates`=4** — real auth is `before_request` (`_check_csrf`/`_check_token`), not
  per-route decorators; SECURITY_SURFACE undercounts route-level auth. Reachability
  (pre/post-auth) is `reachability.py` `[PLANNED]`, an audit-time computation.
- **Call resolution is by last-segment name** — 8534 resolved / 34808 unresolved (dynamic
  dispatch, methods, stdlib). DEAD_CODE `uncalled` is therefore a *candidate* list (conf 0.5),
  not a verdict — vulture ≥90 (42 findings) is the higher-precision dead-code signal.
- **High-precision DP counts are candidate surfaces, not confirmed bugs** (DP-01 43, DP-10 165,
  DP-06 480) — the catalog's own calibration (DP-01 "144×/3 guarded", DP-06 "97% FP", DP-10
  "cross-check TAINT_MAP"). **Audit-priority:** DP-08 (1), DP-11 (4), DP-14 (4) fired on *fixed*
  532 source — confirm whether residual or benign before clearing.
- **`semantic_diff` / `differential_oracle` / `fuzz_harness` / `reachability` / `invariant_probe`
  remain `[PLANNED]`** — they build on this graph in later sessions.
