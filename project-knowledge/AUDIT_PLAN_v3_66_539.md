<!-- verified-against: v3.66.539 -->
<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
# AUDIT PLAN — 100% line-by-line (from v3.66.539)

The execution spec the audit sessions run against. Implements the method in
`CODE_INTELLIGENCE_PROGRAM.md`; this doc is the **partition + per-session
procedure**. Pairs with `audit_manifests/` (the file assignment) and
`audit_partition.py` (regenerates it deterministically).

**State at authoring (re-derive):** live = built = deployed = v3.66.539, on-stash
GREEN (0 FAIL), fix chain complete, **no sandbox-buildable forward-code backlog
remains** — the audit is the natural next major effort. Production surface (539,
re-derived via `audit_partition.py 5500`): **277,105 SLOC across 1,028 files**
(bulk_downloader 525 · tools 207 · frontend/src 296 [pure TS, 0 .js/.jsx]).
**vs 531 (1,004 files / 272,537 LOC): +24 files, all in `tools/` — the source of
the 57→58 batch change below.** bulk_downloader (525) and frontend/src (296) are
unchanged in file count; the 24 new `tools/` files are the audit-program tooling,
report generators, and inventory scanners added across 532–539.

---

## 1. Prerequisite — build the tooling FIRST (one session)

Do **not** start audit waves cold. Per `CODE_INTELLIGENCE_PROGRAM.md` §9 steps 1–3,
one tooling session builds the multiplier so every later session arrives
pre-digested:
1. `l0_extract` + `graph_build` + `risk_score` → `KNOWLEDGE_GRAPH.db` populated.
2. `defect_patterns.py` seeded from `DEFECT_PATTERN_CATALOG.md` (18 classes; grows as new bug-classes are confirmed) + its gate.
3. `REVIEW_STATE.json` ledger + `INVARIANTS.json` + their `--check` gates.

Install the battery first: `bd_review_tools_FULL_kit` (offline; see
`CODE_INTELLIGENCE_TOOLING.md` §1 — the `--ignore-installed PyJWT` + real-bin +
throwaway-venv gotchas). Skipping this turns a ~58-session read into ~58
sessions of mostly-wasted manual extraction.

**Also install the 540 audit-tools pack** (`audit_tools_offline_pack_v3_66_540.zip`,
offline): `bash install_audit_tools_offline.sh` → adds **OpenGrep 1.25.0**
(cross-function taint via `--taint-intrafile` — the SSRF/injection dataflow the
Semgrep-CE battery can't reach), **ast-grep 0.44.0** (structural search/rewrite,
reaches the FE/TS surface), **mutmut 3.6.0** (mutation testing, scoped per batch).
Installs into `~/.audit_tools/venv` + `~/.audit_tools/bin` — **off the service
venv** (same isolation rule as semgrep). Starter taint rules for the SSRF + argv
classes ship in the pack (`rules/ssrf_cmdi_starters.yaml`). See §4a for the taint
workflow. (CodeQL is deliberately NOT used — its license forbids private-repo use
without paid GitHub Advanced Security.)

---

## 2. The partition (subsystem-coherent, SLOC-balanced, 100%)

`audit_partition.py` assigns every production file to exactly one batch by
subsystem precedence, then SLOC-bin-packs each subsystem to ~5,500 LOC/batch (a
monolith >5,500 is its own batch — an `app.py` *is* a full session). Proven
disjoint + complete on the 539 tree (**1,028/1,028, 277,105 SLOC**). **58 batches**
(was 57 at 531; the delta is `TOOLS_OTHER` 7→8, below):

| Subsystem | Batches | Files | SLOC | vs 531 | What |
|---|--:|--:|--:|:--|---|
| **FE** | 10 | 296 | 47,488 | = | `frontend/src` — React/TS (distinct skill surface; pure TS) |
| **CAP** | 1 | 9 | 4,998 | = | capture → WACZ → redact intake (5 guard files; SHA-verify, never edit) |
| **REC** | 4 | 27 | 16,447 | = | recognizer / extraction_core (guard) / deep_detect / template / provider_resolve |
| **RUN** | 3 | 23 | 15,639 | = | runner kernel + 13 mixins / queue / transport / batch_ops |
| **AUTH** | 2 | 21 | 8,251 | = | accounts / auth / cookies / secrets / db |
| **APP** | 6 | 172 | 32,119 | = | the `app_*` blueprints |
| **CORE_BD** | 19 | 278 | 95,624 | = | the rest of the backend core (infra, integrations, vpn, stats, notify, …) |
| **COCKPIT** | 3 | 4 | 11,059 | = | `tools/cockpit_*` monoliths |
| **TOOLS_BUILD** | 2 | 22 | 6,115 | = | build/governance generators + AST audits |
| **TOOLS_OTHER** | **8** | **176** | **39,365** | **+1 batch, +24 files** | the rest of `tools/` (incl. the 532–539 tooling + report/inventory scanners) |
| **Total** | **58** | **1,028** | **277,105** | **+1 batch** | |

**Batch-change decision (539): only `TOOLS_OTHER` changed — 7→8 batches.** The 24
files added across 532–539 all land in `tools/` under the `TOOLS_OTHER` precedence
bucket; bin-packing pushed the overflow into a new `TOOLS_OTHER-08` (33 files)
rather than reshuffling the existing 7. Every other subsystem's batch structure and
file membership is **byte-for-byte stable** — a session that already read, say,
`RUN-01` or `CAP-01` at 531 does not need to re-read it (the files are unchanged;
LOC drift shown is from small in-batch edits, not new files). Net new audit work vs
a 531 run: **`TOOLS_OTHER-08` only.**

Manifests are `audit_manifests/<SUBSYS>-NN.txt` (58 files, regenerated at 539 and
bundled with this plan). Re-derive identically with `python3 audit_partition.py 5500`
run **from the work root** (any target re-balances; same source ⇒ same partition).
NOTE: `audit_partition.py` is a **review tool**, not in the source tree — it ships in
the audit kit / this bundle, and is run against `/home/claude/work`. Within a batch,
files are listed SLOC-desc as the risk proxy until
`risk_score` (radon + taint + churn) re-orders them.

**Sequencing:** risk-first, not numeric. Run the highest-risk subsystems early —
**RUN** (the CC-163 `_process_one` family), **CAP**/**REC** (the guard + redaction +
SSRF surfaces), **AUTH** (secrets), then **APP**/**CORE_BD**, with **FE** and
**TOOLS_OTHER** as their own track. The monolith batches (`app.py`,
`cockpit_console`, `cockpit_core`, `runner`) are single-file sessions — budget
accordingly.

---

## 3. Session model — parallel reads, serial fixes

Audit reads are **read-only**, so batches parallelize with zero collision (like the
verify pass). 57 batches at 5-wide ≈ 12 waves; 10-wide ≈ 6. Fixes are a separate
serial cut chain *after* a slice (never mid-scan).

**Per audit session:**
1. Bootstrap (`bd-install` → `bd-state` = 539) + install `bd_review_tools_FULL_kit`.
2. `bd-review-next` (or read your `audit_manifests/<batch>.txt`) → your files +
   their call-neighborhood + open findings + DANGER_MAP invariants + test files,
   pre-digested from the graph.
3. Read **every line** of each file to the **rubric** (§4). For each unit emit
   either a finding (RED repro) or a positive assurance.
4. Emit `AUDIT_<batch>_v3_66_539.{md,json}` (§5) — findings + knowledge + new
   invariants + new defect-patterns.
5. **Attest** `guard_touch=false` / `tracker_write=false`; re-verify the tree
   byte-identical (`bd-preflight`). Write ONLY your own `AUDIT_<batch>.*`.

**Anti-collision (identical to the verify pass):** read-only tree, no
bump/cut/guard-edit/baseline-`--update`/tracker-write/stash-touch; own-file-only
output; identical 539 bootstrap; batched runs, never the whole `tests/` dir.

---

## 4. Per-file rubric (every session, same depth → "100%" means one thing)

For each unit, read every line and check + record: **auth/authz** gate · **injection**
(SQL/command/path) · **SSRF / URL-trust** · **secret** handling (read/write/mask/log)
· **error contract** (raise→status, swallow) · **type/None** (NaN/inf/bare-name) ·
**concurrency/shared state** · **resource lifecycle** · **input validation** ·
**dead code/unreachable**. The 18 `DEFECT_PATTERN_CATALOG.md` classes are the
checklist; a new class found here is added to the catalog (the flywheel).

---

## 4a. Taint pass (per batch, mechanical — feeds the rubric's injection/SSRF rows)

Before the manual read, run OpenGrep's cross-function taint over the batch files to
surface source→sink candidates the manual pass then confirms or refutes (a finding
still needs a RED repro; a tool hit is a lead, not a verdict):
```
export PATH="$HOME/.audit_tools/bin:$PATH"
opengrep scan --config <starter_or_tuned>.yaml --taint-intrafile --json \
  $(sed 's|^|/home/claude/work/|' audit_manifests/<BATCH>.txt)
```
- Start from `rules/ssrf_cmdi_starters.yaml`; **tune sources/sinks/sanitizers to the
  batch's real names** (the actual host-allowlist helper, the subprocess wrapper) —
  the starters use generic `validate_host`/`subprocess.*` and will under- or
  over-match untuned.
- `--taint-intrafile` = cross-*function within a file*. Genuinely cross-*file* flows
  (e.g. a source in one module, sink in another) are **out of OpenGrep's reach** —
  those remain the manual read's job, backed by `consumer_agreement` /
  `reachability_ledger`. Record such a flow as a finding with `source:"manual"`.
- `source:` on any resulting finding is `opengrep:<rule-id>` (mechanical origin),
  distinct from `manual`/`radon`/`semgrep` — keeps the flywheel's provenance honest.
- ast-grep is the structural-search complement (find every call-site of a risky sink,
  every `except: pass`, every FE `dangerouslySetInnerHTML`) — same "lead not verdict"
  rule. Deterministic; safe to run read-only over the tree.

---

## 5. Per-batch deliverable schema (so batches merge into the graph)

`AUDIT_<batch>_v3_66_539.md` (human) + `.json`:
```json
{ "batch":"RUN-01", "version":"3.66.539",
  "files":[{"path":"","sha256":"","lines":0,"rubric":{"auth":"ok|finding|na","...":""},
            "purpose":"<L2 intent>","public_api":[],"invariants":["I00xx"],
            "data_flow":"<L2>","risk":0.0}],
  "findings":[{"id":"F00xx","file":"","line_range":[0,0],"category":"","severity":"",
               "confidence":"confirmed|probable|triage","title":"","detail":"","fix":"",
               "repro_test":"tests/...::...","source":"manual|opengrep:..|semgrep:..|radon|.."}],
  "new_invariants":[{"statement":"","at":"","guard_test":null}],
  "new_patterns":[{"id":"DP-xx","rule":"","signature":""}],
  "guard_touch":false, "tracker_write":false }
```
`files[].purpose/invariants/data_flow` and `findings` are the irreducible L2;
the mechanical fields feed `l0_extract`'s graph upsert. These roll into
`REVIEW_STATE.json` + `KNOWLEDGE_GRAPH.db` at consolidation.

---

## 6. Consolidation → advanced PK → static KB (the knowledge tail)

After each wave (or at program end):
1. **Merge** `AUDIT_*.json` into `REVIEW_STATE.json` (ledger) + upsert the graph;
   `COVERAGE_LEDGER` `--check` must show `audited == 1028 && stale == 0`.
2. **Consolidate** — render `ADVANCED_PROJECT_KNOWLEDGE_v2.md` + the grown
   `INVARIANTS`/`MODULE_CATALOG` **from the graph** (knowledge-as-data → prose, like
   TASK_TRACKER renders from DATA.json). These are **volatile** — they ride in
   `version.zip`.
3. **Promote** to static KB at a deliberate `bd-handoff --kb-dir` →
   `bd-kb-sync` step (stages the project-files zip + `PROJECT_KNOWLEDGE_UPDATE.md`,
   reseeds `STATIC_KB_MANIFEST.json`). The code-intelligence docs themselves
   (`CODE_INTELLIGENCE_*`, `DEFECT_PATTERN_CATALOG`, this plan) are version-agnostic
   → promote them into static KB in the same step.

---

## 7. Kickoff template (paste per audit session, swap the batch)

> Bootstrap to **v3.66.539** (`bd-install` → `bd-state` = 539), install
> `bd_review_tools_FULL_kit` (offline, per `CODE_INTELLIGENCE_TOOLING.md` §1). You are
> an **audit session** — read-only, no cut/bump/guard-edit/baseline-`--update`/
> tracker-write/stash. Read **every line** of the files in
> `audit_manifests/<BATCH>.txt` to the §4 rubric; emit
> `AUDIT_<BATCH>_v3_66_539.{md,json}` per §5 (findings RED-first + knowledge +
> new invariants/patterns); attest `guard_touch=false`/`tracker_write=false` and
> re-verify the tree. Reference: `CODE_INTELLIGENCE_PROGRAM.md` + `DEFECT_PATTERN_CATALOG.md`.

Seed `F0001` (api_status NameError) and the v3.66.520 verify register (16 deduped,
in `VERIFY_MATRIX`) into the ledger at instantiation — they predate this audit and
belong in the same register.
