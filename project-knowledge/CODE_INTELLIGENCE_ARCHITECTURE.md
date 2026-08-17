<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
<!-- verified-against: v3.66.805 -->
# Code-Intelligence Architecture

How the machine layers (L0/L1) are built and how every analysis artifact is a
**projection of one graph**, not a hand-maintained file. Companion to
`CODE_INTELLIGENCE_PROGRAM.md` (the method) and `CODE_INTELLIGENCE_SCHEMAS.md`
(the exact schemas + gates).

---

## 1. The shape: one graph, many projections

The mistake to avoid is a binder of parallel JSONs that drift apart. Instead there
is **one canonical store** — `KNOWLEDGE_GRAPH.db` (SQLite, stdlib `sqlite3`,
offline) — and every other artifact (`MODULE_CATALOG`, `CALL_GRAPH`, `TAINT_MAP`,
`SECURITY_SURFACE`, `INVARIANTS`, `CONTRACTS`, `ERROR_CATALOG`, `CONFIG_LINEAGE`,
`CONCURRENCY_MAP`, `DEAD_CODE`) is a **query/projection** of it, regenerated, never
edited by hand. Queryability is the point: "every taint path from a request body to
a SQL sink", "which invariants are UNGUARDED", "what breaks if I change function X"
are single queries, not greps across files.

```
            ┌──────────────── L2 (sessions write) ────────────────┐
            │  judgment: intent · invariants · contracts · risk   │
            └─────────────────────────┬───────────────────────────┘
                                      │ upsert
   L0 extract ──► KNOWLEDGE_GRAPH.db ◄┴─ L1 graph_build (calls/imports/taint/guards)
   (battery)         │   ▲
                     │   └── bd-audit-gate queries it on every cut
                     ▼
   projections: MODULE_CATALOG · CALL_GRAPH · TAINT_MAP · SECURITY_SURFACE
                INVARIANTS · CONTRACTS · ERROR_CATALOG · CONFIG_LINEAGE
                CONCURRENCY_MAP · DEAD_CODE  → rendered docs (advanced PK)
```

---

## 2. L0 — deterministic extraction (zero reading budget)

One AST pass (stdlib `ast`; `libcst` `[LIVE]` where position-preserving CST is
needed for `semantic_diff`) over every production file, plus the battery output,
populates the graph with facts a script derives exhaustively and a human should
never hand-transcribe:

- **per function:** module, qualname, line span, signature (params + defaults +
  kw-only), `raises` set, return-contract sketch, decorators, is-route + method,
  cyclomatic complexity (radon), churn (CHANGELOG/version tags).
- **sinks:** SQL string construction, `subprocess`/`os.system`/`exec`,
  `send_file`/path joins, `request.get_json`, redaction-floor entry, template/format
  interpolation, URL fetch.
- **auth gates:** every `_check_csrf`/`_check_token`/decorator gate and the routes it
  guards.
- **secrets:** every read/write/mask/log of a secret-named field.
- **edges (L1):** call edges (jedi/`bd-lsp` refs + AST), import edges (the existing
  `import_graph`), and **taint edges** (source→sink propagation, §4).

L0 is **better** as machine work because it is exhaustive and never drifts — the
opposite of a session transcribing signatures by hand.

---

## 3. L1 — the graph + its projections

`graph_build` assembles nodes and edges into `KNOWLEDGE_GRAPH.db`. Node kinds:
`module · function · route · secret · sink · invariant · contract · finding`.
Edge kinds: `calls · imports · taints · guards · reads_secret · writes_secret ·
validates`. Projections are SQL views materialized to JSON on demand:

- **MODULE_CATALOG** — per module: purpose (L2), public API, key invariants (L2),
  dependencies, risk score. The spine of the advanced PK.
- **CALL_GRAPH** — function-level callers/callees; answers blast-radius.
- **TAINT_MAP** — every source→sink path (§4).
- **SECURITY_SURFACE** — auth gates × secret sites × sinks, each with guard status.
- **INVARIANTS** — load-bearing rules, each `GUARDED`/`UNGUARDED` (§5).
- **CONTRACTS** — pre/postconditions per public function (L2).
- **ERROR_CATALOG** — raise/except → HTTP status, gated for consistency.
- **CONFIG_LINEAGE** — setting → readers → writers → effect.
- **CONCURRENCY_MAP** — shared state, locks, WAL/isolation hacks, keeper/pause rules.
- **DEAD_CODE** — defined-but-uncalled + route-registered-but-unreachable.

---

## 4. Taint propagation (the "advanced" that finds bugs by construction)

Sources (user-controlled): request bodies/args, captured page content + WS frames +
URLs, provider IDs, cookie/secret stores. Sinks: SQL, path ops, subprocess,
redaction floor, template interpolation, URL fetch. `taint_trace` walks the call
graph from each source, propagating taint through assignments/returns/params, and
records a `taints` edge wherever tainted data reaches a sink **without passing a
recognized sanitizer**. The verify pass's OAuth-fragment leak, the CGNAT SSRF gap,
and the provider-ID traversal were all source→sink flows — a taint projection
surfaces that class *by construction* rather than by someone thinking to look.

Sanitizer recognition is itself an L2 judgment (which functions actually neutralize
taint), recorded as `validates` edges — so the map sharpens as the audit proceeds.

---

## 5. Invariants — prose → gated, executable data

The audit's most durable output. Each load-bearing rule (e.g. `resume_site_keepers
must never exist`; fail-closed redaction floors; `_process_one` dispatch ordering;
keeper nested-playwright pause) is a row: `{id, statement, file:line,
why_load_bearing, guard_test | UNGUARDED, probe}`. Two mechanisms make it more than
documentation:
- **`UNGUARDED` is visible, not auto-fixed** — `tools/invariants.py --check`
  reports the state and fails under the current root authority. The historical
  `bd-finding` and `bd-invariant` command names are absent; no stub emitter is implied.
- **`invariant_probe` is absent**. A future executable live-app probe would require
  its own backlog decision and direct test contract.

This converts safety claims from prose into gated data in root `INVARIANTS.json`.

---

## 6. Drift gates (why the knowledge can't rot)

Every artifact carries its own `--check`, mirroring the in-sync gates:
- **Coverage ledger** (`REVIEW_STATE.json`): a file whose `sha256 != tree` flips to
  `unreviewed` — audit-staleness can't hide. This is `bd-state` for the audit.
- **`semantic_diff`** `[LIVE-capable via libcst]`: flags when a function's
  signature/raises/return-contract/call-edges change even across a "pure refactor" —
  catches the caller/callee signature-drift class the moment it lands.
- **`differential_oracle`** `[LIVE as tools/differential_oracle.py]`: cross-checks redundant implementations of
  one contract (the two IP classifiers, the two `sites_config` resolvers, `_mask`
  vs `_is_secret` pairs); divergence = latent bug.
- **`bd-audit-gate`** `[BUILT, STANDALONE]`: the two tracked copies run the
  available defect-pattern, invariant, review-state, witness, emit, topology, and
  consumer-agreement checks with three-state failure semantics. It is not wired
  into CI or the canonical cut gate, and it does not run `semantic_diff`,
  `differential_oracle`, `fuzz_harness`, or an absent `invariant_probe`. Those
  frontends remain standalone until a separate product decision wires them.

---

## 7. Determinism + offline constraints
- All generators are stdlib-first (`ast`, `sqlite3`, `json`); `libcst`, `radon`,
  `jedi`/`pyright` (via `bd-lsp`), `semgrep` come from the staged kits
  (`bd_review_tools_FULL_kit`). No network at runtime.
- `coverage.json` is produced **on stash** (the full suite hangs in-sandbox) and
  ingested; `pip-audit` advisory DB is stash-only. Everything else runs in-sandbox.
- Generators must be deterministic (sorted keys, stable ordering) so artifact
  diffs are reviewable and the gates are reproducible — same source ⇒ same graph.
