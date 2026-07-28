# Codex handoff — record of the 2026-07-25 session

**This is a RECORD, not a set of live instructions.** It describes work done by
a Codex agent in a different checkout, on a different machine, under a policy
that no longer applies. Its task ledger is still the only map of the wider
program, which is why it survives; everything environment-bound has been cut.

**Re-derive every status below from source before acting on it.** Figures marked
*(re-derived 2026-07-28)* were checked against this tree on that date. Everything
else is as the original author left it and has not been re-measured — historically
about half of a stale register is already done or mis-scoped, and three figures in
this very file had moved by the time anyone looked.

The environment sections of the original were removed on 2026-07-28: they named a
WSL checkout at `/root/BulkDownloader-main`, a HEAD ~50 commits behind, a
`.venv/bin/python` that does not exist here (a command naming it exits 127), and a
standing instruction **not to reset, checkout, or stage** — written to protect a
git index that is long gone, in a repository whose deploy is now
`git reset --hard`. Recover them from history if ever needed:
`git log --diff-filter=M -- CODEX_HANDOFF.md`.

The interpreter in this tree is `venv/bin/python`.

---

## The program

Complete the BulkDownloader code-intelligence / audit program in the ordered SDD
plans under `docs/superpowers/plans/` *(11 plan files present, re-derived
2026-07-28)*.

| Group | State as recorded |
| --- | --- |
| Foundation / graph, Tasks 1-8 | complete |
| Analysis frontend, Tasks 1-3 | complete |
| Analysis Task 4 (`reachability.py`) | paused at a safe checkpoint — one reviewed P2 remains |
| Analysis Tasks 5-7 | pending |
| Governance / gate, Tasks 1-8 | pending |
| Audit / knowledge / hygiene / static-KB, Tasks 1-11 | pending |

Eleven of 34 task groups complete. Task 4 is implemented but **not** complete.

## Files the completed tasks touched

Repo-relative. All present *(re-derived 2026-07-28)*.

**Analysis Task 3** — complete, independently approved:
`tools/code_intelligence/semantic_service.py`, `tools/semantic_diff.py`,
`tests/test_semantic_diff_frontend.py`, `DEPENDENCY_GRAPH.json`,
`DEPENDENCY_GRAPH.md`

**Analysis Task 4** — paused:
`tools/code_intelligence/reachability_service.py`, `tools/reachability.py`,
`tests/test_reachability_frontend.py`, `DEPENDENCY_GRAPH.json`,
`DEPENDENCY_GRAPH.md`

Evidence lives under `.superpowers/sdd/` (briefs, reports, checkpoint,
`progress.md`).

> **The frozen Task 4 review packages no longer exist** *(re-derived
> 2026-07-28)*: `.superpowers/sdd/review-analysis-task-4.diff` and
> `-derived.diff` are both absent, so the original "verify these hashes before
> resuming" step cannot be run. Resuming Task 4 now means **re-freezing** from
> the current tree, not verifying against those hashes. The recorded hashes are
> kept below only to identify what the old packages were.
>
> Task 3 core `969224f2b7bb0da7cc94e6a1887b0b563ee543c719dd3b4bb53ecbb02405fdf1`,
> derived `4f95909b48e0963b785d5d5f4c7e3643f86e996ba5db20e7337fc73d5c315695`.
> Task 4 core `6b3d11475a9ccc1f6b00cb13cd2d85f73e40c448d25cab435a9eb5af92d04fc4`,
> derived `3b2c2b7783072ee13dacd3293903c0019b707c228a8a2b8a23f8c702bfe97813`.

## Design decisions worth keeping

These are about the analysis itself and do not depend on the retired
environment. The original list's first two items — preserve dirty state, never
commit or stage — were policy for that session and are dropped.

1. Treat unknown or ambiguous semantic facts as unknown / fail-closed rather
   than inventing confidence.
2. Task 3 models ordered Python scope execution, `global`/`nonlocal`,
   comprehension walruses, CPython 3.12 header/decorator order, descriptor
   composition, receiver provenance, and post-decoration callability.
3. Task 3 uses explicit file/tree/AST/artifact/semantic-work bounds, strict
   artifact validation, secret redaction, tracked-tree fail-closed behaviour,
   and atomic path-identity-checked output.
4. Task 4 keeps these evidence categories separate: `auth_probe`,
   `auth_gate_facts`, `operator_wiring`, `navigation`, `call_paths`,
   `deferrals`.
5. Existing endpoint and navigation tools are adapters and evidence sources,
   not proof of a route's privilege class.
6. Do not probe unsafe mutating or unresolved parameterised Flask routes merely
   because probing happens in a child process. **A child is not a sandbox.**
7. Authenticated classification requires a real unauthenticated denial/delta.
   Dual denial does not prove `internal`; dual 404 does not prove `unreachable`.
8. Redirect evidence is retained only for safe fixed auth landing paths;
   dynamic, signed, or credential-bearing paths are redacted.
9. Task 4 stopped at a requested safe point. No product changes beyond the
   checkpoint were authorised.
10. CodeRabbit CLI 0.7.0 emitted no output and timed out on the final Task 3
    reviews. **Do not claim a CodeRabbit issue count from those attempts.**
    Task 3 received a successful independent manual approval instead.
11. The extracted-ZIP test-suite release gate remains waived unless explicitly
    requested. *(Note: the zip deploy path itself was retired 2026-07-27; the
    box now deploys via git.)*

## Verification, as recorded — and as it stands now

| Check | Recorded | Re-derived 2026-07-28 |
| --- | --- | --- |
| Focused semantic frontend | 90 passed | **90 passed** |
| Focused reachability frontend | 26 passed | **32 passed** — the suite grew |
| Standing compatibility controller (T3) | 521 passed | not re-measured |
| Standing compatibility controller (T4) | 547 passed | not re-measured |
| Dependency graph / import graph | 1,366 edges | not re-measured |
| Real self-diff | 24,480 function locations/side, zero semantic changes | not re-measured |
| CPython descriptor probe | all 127 stacks to depth six matched | not re-measured |

Reproduce the focused suites with:

```bash
venv/bin/python -m pytest tests/test_semantic_diff_frontend.py -q
venv/bin/python -m pytest tests/test_reachability_frontend.py -q
```

## Baseline debt recorded at the time

Three failures in the legacy four-suite reachability selection, all reproducing
on a pristine HEAD, so **not** attributable to Task 4. Status has moved:

- `test_every_dark_endpoint_is_classified` — 101 unexplained dark endpoints.
  *(re-derived 2026-07-28: `bd-regen-order` reports `dark=101, in sync`, so the
  count holds but the ledger now exists to explain it.)*
- `test_dark_count_is_ratcheted` — missing `reports/endpoint_reachability.json`.
  *(re-derived 2026-07-28: the file is **present**. This item looks resolved;
  confirm by running the suite before treating it as debt.)*
- `test_dark_ratchet_fell` — same missing ledger, same correction.

These were never accepted as passes.

## Remaining work

### Resume and finish Analysis Task 4

1. Add a RED regression using a custom sensitive exception class name such as
   `Bearer_ultra_private_value`.
2. Route exception types through a small bounded allowlist / sanitiser.
3. Emit a generic `ProbeError` for unknown, invalid, or sensitive exception
   class names in artifacts and `CheckResult` evidence.
4. Rerun the focused suite, controller, compilation, CLI help, graph checks,
   secret/path/resource probes, and scoped whitespace checks.
5. **Re-freeze** both Task 4 packages — the originals are gone, so this is a
   fresh freeze, not a hash comparison.
6. Obtain an independent review of the exact final packages.
7. Write the final report; mark Task 4 complete only after approval.

### Continue the wider program

- Analysis Tasks 5-7.
- Governance / gate Tasks 1-8.
- Audit / knowledge / hygiene / static-KB Tasks 1-11, including the risk-routed
  L2/L3 review and the 445-row corpus disposition.
- Static-KB replacement and external re-paste remain operator-gated.

## Where to start

The original "exact next command" was a PowerShell/WSL invocation against the
retired checkout, verifying package hashes that no longer exist. It is removed.
Start instead by establishing where Task 4 actually stands:

```bash
venv/bin/python -m pytest tests/test_reachability_frontend.py -q
venv/bin/python tools/dependency_graph.py --check
venv/bin/python tools/decomp/import_graph_gate.py --check
```
