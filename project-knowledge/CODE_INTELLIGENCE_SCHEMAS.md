<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
<!-- verified-against: v3.66.805 -->
# Code-Intelligence Schemas

Every artifact's schema and the gate it carries. All are projections of
`KNOWLEDGE_GRAPH.db` (see `CODE_INTELLIGENCE_ARCHITECTURE.md`) except
`REVIEW_STATE.json` (the ledger, authored by the review flow). JSON unless noted;
all generators stdlib-first, deterministic (sorted keys), offline.

> Convention: every artifact has a `generated_against` version, a `schema` int, and
> a regenerator with `--check` (drift gate, rc≠0 on mismatch) and `--update`.
>
> **CONVENTION NOT MET BY THE SHIPPED ARTIFACTS — measured v3.66.805.** Of the three
> artifacts that exist in the static PK today: `INVARIANTS.json` has `schema: 1` but
> **no** `generated_against`; `CONTRACTS.json` and `COVERAGE_GAPS.json` have **neither**.
> All three instead carry a `_meta` block, and `CONTRACTS.json` nests under a
> `contracts` key rather than the flat `{"schema":1, "C0003": {...}}` shape shown in §8.
> So this line states an intended convention, not the observed one. Treat the schemas
> below as the DESIGN; read the artifact before assuming its shape, and do not write a
> `--check` that assumes `generated_against` exists — it does not.

---

## 1. `REVIEW_STATE.json` — the ledger (volatile, in `version.zip`)
The resumable, compaction-survivable spine. STATE.json-analog. (Full design in
`CODE_REVIEW_METHODOLOGY.md` §4 — restated here for one-stop reference.)
```json
{ "schema":1, "generated_against":"vX.Y.Z",
  "files": { "<path>": {
     "sha256":"<hash>", "lines":0,
     "status":"unreviewed|in_progress|reviewed",
     "reviewed_at_sha":"<hash>", "rubric":{ "auth":"ok|finding|na", "...":"" },
     "finding_ids":["F0001"], "invariant_ids":["I0007"], "catalog":true } },
  "findings": { "F0001": {
     "file":"", "line_range":[0,0],
     "category":"crash|security|resource-leak|logic|dead-code|type|concurrency",
     "severity":"low|medium|high|critical",
     "confidence":"confirmed|probable|triage",
     "title":"", "detail":"", "fix":"", "repro_test":"tests/...::test_...",
     "status":"open|fixed|wontfix|duplicate",
     "source":"manual|ruff:S608|radon|semgrep:<id>|eslint:react-hooks/exhaustive-deps" } } }
```
**Gate:** a file whose `sha256 != tree` auto-flips to `unreviewed`. Renders
`REVIEW_DASHBOARD.md` for humans.

## 2. `COVERAGE_LEDGER.json` — audit-coverage attestation
Either folded into `REVIEW_STATE.files` or standalone. Asserts each production file
appears exactly once, `lines` matches the live file, and flags any file whose SHA
changed after `reviewed_at_sha` (audit staleness).
```json
{ "schema":1, "files":{ "<path>":{ "audited_by":"sessionX", "audited_at_sha":"",
  "lines":0, "note_ref":"AUDIT_<batch>.md#<path>" } },
  "totals":{ "production_files":0, "audited":0, "stale":0 } }
```
**Gate `--check`:** `audited == production_files && stale == 0`.

## 3. `MODULE_CATALOG.json` — knowledge-as-data (renders the advanced PK)
```json
{ "schema":1, "modules": { "<path>": {
   "purpose":"<L2: one-paragraph intent>",
   "public_api":[{"name":"","signature":"","raises":[],"returns":"","contract_id":"C0003"}],
   "invariants":["I0007"], "depends_on":[], "depended_by":[],
   "sinks":["S..."], "secrets":["password","cookie_file"],
   "risk_score":0.0, "complexity_max":0, "coverage":0.0,
   "data_flow":"<L2: what flows in/through/out>" } } }
```
**Gate:** mechanical fields (signature/raises/depends_on/sinks/complexity) regen
from source and must match; `purpose`/`invariants`/`data_flow` are L2 (human),
checked only for presence.

## 4. `CALL_GRAPH.json` — function-level edges
```json
{ "schema":1, "nodes":["mod:qualname"], "edges":[{"from":"","to":"","kind":"call"}],
  "unresolved":[{"from":"","name":"","reason":"dynamic|missing"}] }
```
**Gate:** regen must equal committed (proves no untracked call edge).

## 5. `TAINT_MAP.json` — source→sink flows
```json
{ "schema":1, "sources":[{"id":"","kind":"request_body|capture|provider_id|url","at":""}],
  "sinks":[{"id":"","kind":"sql|path|subprocess|redaction|template|fetch","at":""}],
  "paths":[{"source":"","sink":"","via":["mod:qualname"],"sanitized_by":null,"severity":""}] }
```
**Gate:** any `paths[].sanitized_by == null` is a candidate finding; `--check` fails
if a previously-sanitized path lost its sanitizer.

## 6. `SECURITY_SURFACE.json` — auth × secret × sink
```json
{ "schema":1, "auth_gates":[{"name":"_check_csrf","guards":["route"],"reachable":"pre_auth|post_auth|internal"}],
  "secret_sites":[{"field":"","op":"read|write|mask|log","at":"","masked":true}],
  "sql_sites":[{"at":"","parametrized":true,"table_user_controlled":false}],
  "subprocess_sites":[{"at":"","shell":false,"arg_is_list":true}],
  "path_sinks":[{"at":"","allowlisted":true}] }
```
**Gate:** flags `masked:false` secret reads on read-endpoints, `shell:true`,
`table_user_controlled:true`, non-allowlisted path sinks (the verify-pass classes).

## 7. `INVARIANTS.json` — gated, executable rules
```json
{ "schema":1, "invariants":{ "I0007": {
   "statement":"resume_site_keepers must never exist",
   "at":"bulk_downloader/runner.py", "why":"nested-playwright deadlock",
   "guard_test":"tests/...::test_no_resume","status":"GUARDED|UNGUARDED",
   "probe":"<expr asserted against live app>" } } }
```
**Gate:** every `UNGUARDED` emits a RED-test stub via `bd-finding`; `bd-audit-gate`
runs each `probe`.

## 8. `CONTRACTS.json` — pre/postconditions (deepest L2 output)
```json
{ "schema":1, "C0003": { "fn":"mod:qualname",
   "pre":["max_concurrent is finite int in [1,32]"],
   "post":["returns {} on all-valid, else {field:reason}"],
   "raises":["ValueError on non-dict body"], "checked_by":"tests/...::..." } }
```
**Gate:** runtime contract-check harness (`[PLANNED]`) asserts pre/post on the
covered call sites.

## 9. `ERROR_CATALOG.json` — raise→status consistency
```json
{ "schema":1, "handlers":[{"at":"","raises":"AttributeError","maps_to":500,"expected":400,"ok":false}] }
```
**Gate:** flags `ok:false` (the "should be 400, returns 500" class).

## 10. `CONFIG_LINEAGE.json` — setting → effect
```json
{ "schema":1, "settings":{ "<key>":{ "readers":[""],"writers":[""],"effect":"",
   "gui_exposure":"full|display_only","runtime_tunable":true } } }
```
**Gate:** extends the CLI↔GUI ratchet down to runtime; flags a writer with no reader
or a runtime-tunable with no GUI exposure.

## 11. `CONCURRENCY_MAP.json` — shared state + locks
```json
{ "schema":1, "shared_state":[{"name":"","guarded_by":"lock|none","at":""}],
  "locks":[{"name":"","reentrant":false,"protects":[]}],
  "rules":["keeper pauses nested playwright","WAL isolation note"] }
```
**Gate:** flags shared state with `guarded_by:none` mutated from >1 path.

## 12. `DEAD_CODE.json`
```json
{ "schema":1, "uncalled":[{"fn":"","confidence":0}], "unreachable_routes":[{"route":"","reason":""}] }
```
**Gate:** vulture ≥90 + route-registration-vs-reachability cross-check.

## 13. `audit_metrics.json` + `regression_corpus/`
`{ "schema":1, "by_module":{ "<path>":{ "findings":0,"density":0.0,"audited_at":"" } } }`
plus a frozen seed (fuzz/lint input) per confirmed bug so it can never silently
return. **Gate:** the corpus is replayed by `fuzz_harness` + `defect_patterns` each cut.

## 14. `KNOWLEDGE_GRAPH.db` (SQLite) — the canonical store
Tables: `nodes(id, kind, path, qualname, span, meta_json)`,
`edges(src, dst, kind, meta_json)`, plus views materializing §3–§12. All other
artifacts are `SELECT … → json`. **Gate:** rebuilt deterministically from source;
a content hash of the rebuilt DB is pinned and `--check`ed.
