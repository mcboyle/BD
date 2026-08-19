<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
<!-- verified-against: v3.66.805 -->
# Code-Intelligence Tooling

The **custom** tools the program builds, and the **verified offline install** of the
third-party battery they sit on. Complements `CODE_REVIEW_TOOLCHAIN.md` (which
documents the battery itself, per-tool invocation, and gotchas). Status:
`[LIVE]` verified · `[PLANNED]` to build (all stdlib-first, offline).

---

## 1. The battery — one merged kit, verified offline

`bd_review_tools_FULL_kit.zip` merges the four kits (base review wheels + eslint +
adv + semgrep + jscpd) into one upload. Layout: `wheels/` · `eslint/` (node_modules
+ `eslint.config.mjs`) · `jscpd/` · `INSTALL.sh` · `MANIFEST.txt`.

**Verified install (sandbox AND stash, offline):**
```sh
unzip -oq bd_review_tools_FULL_kit.zip -d "${BD_REVIEW_KIT:?set BD_REVIEW_KIT}"
bash "$BD_REVIEW_KIT/INSTALL.sh"
# == what INSTALL.sh runs ==
pip install --break-system-packages --no-index --find-links=<kit>/wheels/ \
  vulture radon detect-secrets coverage hypothesis bandit pip-audit libcst
pip install --break-system-packages --no-index --find-links=<kit>/wheels/ \
  --ignore-installed PyJWT semgrep            # PyJWT workaround (see gotchas)
```
ruff/black ship via `precommit_kit`; pyright/jedi via `lsp_kit` (`bd-lsp`).

**Tool verdicts (`[LIVE]`, smoke-tested):** vulture · radon · detect-secrets ·
coverage · ruff · black · jedi/pyright · eslint(+react-hooks) · shellcheck (existing
battery) — plus the advanced adds: **hypothesis** 6.x (property fuzzing),
**libcst** 1.8.x (CST for `semantic_diff`), **bandit** 1.9.x (B-rule security),
**semgrep** 1.168.x (bundled core scans offline), **jscpd** 4.x (clone detection),
**pip-audit** (binary works; CVE DB needs network → **stash-only**).

**Gotchas (hard-won — each otherwise wastes a session):**
- **semgrep needs `--ignore-installed PyJWT`.** Debian's PyJWT has no RECORD file →
  pip can't uninstall to upgrade → the whole install aborts without the flag.
- **semgrep is heavy** — pulls ~50 deps (pydantic/starlette/cryptography/urllib3/
  otel…) and prints a benign `mitmproxy ↔ typing-extensions` warning (a pre-existing
  package complaining, not a failure). Its core is `manylinux_2_34` (loads on
  glibc≥2.34).
- **ON STASH: keep review deps OFF the service venv.** semgrep's cryptography/
  urllib3/requests land in user site-packages; do the installs in a throwaway venv
  (`python3 -m venv ~/rev && ~/rev/bin/pip install --no-index --find-links=<kit>/wheels/ …`)
  so they can't shadow `venv/`'s Flask/Playwright.
- **eslint + jscpd: invoke the REAL bin, never the `.bin` shim** (zip flattens
  symlinks → broken relative requires):
  `node <kit>/eslint/node_modules/eslint/bin/eslint.js --config <kit>/eslint/eslint.config.mjs 'src/**/*.{ts,tsx}' --format json`
  and `node <kit>/jscpd/node_modules/jscpd/bin/jscpd <path>`.
- **coverage hangs on the full suite in-sandbox** → run on stash, ingest `coverage.json`.
- **PATH:** stash installs land in `~/.local/bin` (off PATH); invoke via
  `python3 -m bandit` / `python3 -m semgrep` or the venv path.

### 1a. The 540 audit-tools pack (cross-function taint + structure + mutation)

`audit_tools_offline_pack_v3_66_540.zip` — a second offline pack that closes the
biggest gaps in the CE battery. `bash install_audit_tools_offline.sh` → installs
into `~/.audit_tools/venv` + `~/.audit_tools/bin`, **off the service venv** (same
isolation rule as semgrep). Three tools:
- **OpenGrep 1.25.0** (`bin/opengrep`, standalone glibc binary) — LGPL fork of
  Semgrep CE that restores **cross-function taint** the battery's Semgrep CE lacks.
  Run WITH `--taint-intrafile`. Byte-compatible with Semgrep rules/JSON/SARIF.
  Wrapper: **`bd-audit-taint <BATCH>`** runs it over a batch's manifest against
  `$BD_WORK` with `rules/ssrf_cmdi_starters.yaml` (tune per batch).
  **Boundary:** cross-*function within a file*, NOT cross-*file* — cross-file flows
  stay the manual read + `reachability_ledger`. Tool hits are LEADS, not verdicts;
  a finding still needs a RED repro, tagged `source:"opengrep:<rule-id>"`.
- **ast-grep 0.44.0** — structural search/rewrite; reaches the FE/TS surface the
  Python AST tools don't. Full function-def match needs the body in the pattern
  (`def $F($$$ARGS):\n    $$$BODY`), not a bare `def $F(...)`.
- **mutmut 3.6.0** — mutation testing (does the suite kill a regression?). Run
  **scoped to one batch**, never the whole `tests/` dir. 3.6.0 needs a source target.

**CodeQL is deliberately excluded** — its license forbids private-repo use without
paid GitHub Advanced Security. Only viable if BD is OSI-licensed or GHAS is bought.

---

## 2. Custom review tooling (extends the existing `tools/*.py` audits)

### Existing-plan tools (from `CODE_REVIEW_METHODOLOGY.md` §5) -- status measured PER TOOL, not blanket `[PLANNED]`

Markers below were measured against the tree; re-derive with `ls tools/ toolchain/bin/`
before trusting them. A blanket heading marker was previously wrong for three of seven.

- **`bd-scan`** `[BUILT: tools/bd-scan.py]` — runs the whole L0 battery, normalizes
  every tool's output into ledger findings; diff-aware (only re-emits for changed files).
- **`bd-review-next`** `[PLANNED]` — emits the next risk-ordered slice: module + import
  neighborhood + open findings + DANGER_MAP invariants + its test file.
- **`bd-finding`** `[PLANNED]` — scaffolds a finding into a RED test stub.
- **`bd-triage`** `[BUILT: tools/bd-triage.py, toolchain/bin/bd-triage.py]` — encodes the
  FP-suppression rules (the `F821` 97%-FP class etc.) so the ledger isn't flooded.
- **`bd-invariant`** `[PLANNED under this name; the closest BUILT equivalent is
  toolchain/bin/bd-invariant-engine, a declarative invariant gate]` — promotes a confirmed
  bug-class into a permanent AST gate.
- **`bd-dup`** `[PLANNED]` (wraps jscpd) · **`bd-coverage-map`**
  `[BUILT: toolchain/bin/bd-coverage-map, a launcher for tools/coverage_map.py]`
  (ingests `coverage.json` + radon → the risk score).

### Code-intelligence additions — **status measured per tool (re-measured at v3.66.818), not blanket `[PLANNED]`**

> **BUILT** (present in `tools/`, some also in the static PK): `bd-scan.py` (tools/ -- the PK copy was a byte-identical duplicate, retired at v3.66.954),
> `l0_extract.py` (PK+tools), `graph_build.py`, `defect_patterns.py`, `risk_score.py`,
> `bd-audit-gate.py` (PK+tools), `bd-triage.py` (PK+tools) -- and, **built since @805**,
> `semantic_diff.py`, `differential_oracle.py`, `fuzz_harness.py`, `reachability.py`,
> all four in `tools/` and each backed by a service in `tools/code_intelligence/`
> (`semantic_service.py`, `oracle_service.py`, `fuzz_service.py`, `reachability_service.py`).
> **STILL UNBUILT** (verified absent from the whole tree): `invariant_probe.py`.
> The blanket `[PLANNED]` heading this section previously carried was stale for most of
> the tools listed, and this block itself then went stale in the other direction --
> it called four already-built tools missing. Re-derive before trusting any marker below.
- **`l0_extract`** — the AST pass: per-function facts + sinks + auth gates + secrets
  → upsert into `KNOWLEDGE_GRAPH.db`. stdlib `ast` (+ `libcst` for CST needs).
- **`graph_build`** — assembles nodes/edges incl. **taint edges**; materializes the
  §3–§12 projections (`CODE_INTELLIGENCE_SCHEMAS.md`).
- **`risk_score`** — computes the §3 composite (adds `taint_reach` +
  `prior_defect_proximity` to the existing four factors).
- **`defect_patterns.py`** — AST/grep checker seeded from `DEFECT_PATTERN_CATALOG.md`
  (the verify-pass 16 + F0001). The highest-ROI net-new tool: a project-native
  linter built from your own bugs. Every new confirmed bug-class adds a pattern.
- **`semantic_diff.py`** — `libcst`-based; flags signature/raises/return-contract/
  call-edge changes across "pure refactors" (the caller/callee drift class).
- **`differential_oracle.py`** — cross-checks redundant implementations of one
  contract (two IP classifiers, two path resolvers, `_mask` vs `_is_secret`);
  divergence = latent bug. Uses jscpd + the graph.
- **`fuzz_harness.py`** — `hypothesis` property fuzzing on parse/redaction/validation
  boundaries (NaN/inf/fragments/malformed captures); replays `regression_corpus/`.
- **`reachability.py`** — pre-auth/post-auth/internal classification per route
  (the BUG-5 blast-radius distinction, computed not judged).
- **`invariant_probe.py`** — runs each `INVARIANTS[].probe` against the live app
  (in-process Flask test client).
- **`bd-audit-gate`** — **the multiplier.** Wires `defect_patterns` + `semantic_diff`
  + `invariant_probe` + `reachability` + contract-check + ledger-staleness into one
  gate that **blocks the build** like the in-sync gates. Without it the analysis is
  a library nobody runs.

---

## 3. Use the repo's own audits first
`tools/` already ships ~30 offline stdlib AST checkers (`audit_atomic.py`,
`decomp_lint.py`, `cross_monolith_graph.py --check`, `dependency_graph.py`,
`route_map_snapshot.py`, `build_function_index.py`, the `import_graph_gate`, …) —
the project-native semgrep-ruleset. **Always check whether an invariant already has
a checker here before writing a new one;** `bd-invariant` extends this pattern, it
does not replace it.

---

## 4. What runs where
| Tool | Sandbox | Stash |
|---|:--:|:--:|
| L0 battery (ruff/vulture/radon/detect-secrets/bandit/semgrep/libcst/eslint/jscpd) | ✅ | ✅ (throwaway venv) |
| coverage (full suite) | ✗ hangs | ✅ → ingest `coverage.json` |
| pip-audit (CVE DB) | ✗ network | ✅ |
| the custom L0/L1/graph/audit-gate tools | ✅ stdlib | ✅ |
| invariant_probe (live app) | ✅ test client | ✅ live |
