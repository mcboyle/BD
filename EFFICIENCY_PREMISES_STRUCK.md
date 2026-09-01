# The efficiency queue, measured -- 2026-09-01

Produced by the `check-efficiency-premises` workflow at origin/main 5291de20
(v3.66.1388). Six queued items; every premise was MEASURED rather than argued.

FIVE OF SIX WERE STRUCK. Do not re-queue them. Each entry below carries the
number that killed it, so nobody re-derives the item from the same wrong figure.

Two more had already died the same way earlier the same day and are recorded in
HANDOFF.md section 6: "CI shard rebalancing roughly halves the critical path"
(measured 17s) and "bd-band-derive maps a docs/register change to 245 test
files" (measured 19, and 57 with the release trio).

That is SEVEN items in one day whose stated benefit did not survive contact with
a measurement. The lesson is not that a better item exists.

---

# BUILD — 1 item

**1. Harness fast lane (~10 min of work) — saves 317s (5.3 min) per harness edit.**
Make the default harness lane the whole suite minus the 4 preflight tests: `pytest /home/mboyle/bd-persist/harness/tests -k "not preflight and not new_document"` = 94/98 in **53.58s** vs **370.73s** for the full suite. The 4 preflight tests are not deleted — they move to a pre-freeze-only lane, where their ~5 minutes is already justified. Bonus safety: `scratch_wt` is used only by those 4, so the fast lane never runs `git worktree add` against the live integrator repo.

That is the entire build list. Nothing else in the queue survived measurement.

# STRUCK — do not re-queue

**remote-precut — STRUCK. True number: 11.8s mean critical-path exposure (median 0s, max 65s), not ~5 min.**
The ~5 minutes was already removed by a different fix: `bd-verify-cut.sh:752` backgrounds precut concurrent with the band and only collects it after. The claim quotes the pre-fix number and mistook precut's *runtime* (256.89s measured) for its *cost*. Dispatch cannot capture even the 11.8s — capacity hosts have the same 48 cores, so it relocates rather than shrinks, plus ssh/mirror/worktree overhead. **And built as specified it breaks every verify:** `gh` is absent on both capacity hosts (10.0.70.52, 10.0.70.54), so the CI shard-headroom check raises → UNKNOWN → `record_unknown` → NOT SHIPPABLE on every cut, trading rows 463/464's unsatisfiable-UNKNOWN shape for a 2.30s advisory check.

**precut-internal parallelisation (the 187.27s pytest step) — STRUCK BEFORE IT IS QUEUED.** The measurement proposed this as its own follow-on item. It is not one. Total precut critical-path exposure across 20 real verifies is 237s, so the **ceiling on any precut-internal speedup is ~12s/verify mean, 0s median**. The 187.27s is real runtime and almost entirely invisible. Do not let that figure seed a new item.

**stamp-trio-at-merge — STRUCK. True number: 18 of 24 sibling pairs still same-line collide after the trio is removed; only 6/24 go clean, and 6 is an upper bound.**
The trio is three of seven colliding artifacts. PIN_INDEX's counter, STATIC_KB's digests, the backlog header line and import_graph_baseline collide on lines the trio never touches. It also cannot be built: the trio is atomic (`bd-precut` refuses unless version == slice4 pin == CHANGELOG top), and `tests/test_register_closed_versions_exist.py` is repo-wide and demands each `CLOSED @N` resolve to a `## v*.*.N` heading in the same tree — blocking the mechanism for 12/25 cuts. Real cost of the thing being fixed: **1 of 24 verify attempts**, already patched in `bd-rebase-cut.py`.

**local-band-minus-ci — STRUCK. True number: 33 of 236 files (14.0%), 16.3% wall clock (~55s on a real 289-file band), not "roughly halving."**
CI and a derived band are near-disjoint by design; `ci.yml` says so in its own comment. Two further blockers: the subtraction is only knowable *after* the freeze, so banking it means betting speculatively and paying a refreeze when the bet loses; and the 33 removed are exactly the box-only gates (operator-database gate whose subject is test5's deployed-tree-equals-work-tree, install-dir isolation, deploy script, fresh-host, Chromium download chain). Saves most where the band is already cheapest.

**skip-regen-when-inputs-unchanged — STRUCK. True number: ~0.5s expected saving per verify, not ~90s. A 180x overstatement.**
The step costs 63.3s not 90s, runs once per verify, and only 54.67s of it is skippable generators. The skip fires on **1 of 120 whole-cut deltas (0.8%, an upper bound)** because the measured input union is 2,836 tracked files = 75.1% of the repo. Decisive: the obvious hand-derived digest was built and it **shipped a stale artifact 3 times in the last 300 commits** (e54cb9e0c, 2ba52840b, fcabc6f02 — all changed STATIC_KB_MANIFEST.json, because STATIC_KB reads 93 files under `project-knowledge/` including IMPROVEMENT_BACKLOG.md). That is @944's recurrence verbatim. No cheap staleness oracle exists: every `--check` costs what regenerating costs.

**reuse-checkout-and-band-cache — STRUCK. True numbers: ~2s worktree round trip and ~5s band derivation. ~7s of a ~790s attempt (0.9%), not ~180s.**
Overstated 45x and 12-24x respectively. The rerun case this targets is already solved by ruling 41's `bd-verdict-cache`, which turned v1388-b's prepush from 313.8s into 1.6s. Same-SHA checkout reuse already exists on the remote lane (`bd-band-remote.sh:185-193`). And reuse destroys a provability property for 2s: `prove_checkout` runs `--untracked-files=no`, so disposability is the *only* thing guaranteeing attempt 2 does not inherit attempt 1's residue, including a `frontend/dist` whose absence several gates exist to judge.

**per-file harness selection (the original harness-suite-selection mechanism) — STRUCK, even though its parent item survives.**
Editing the heavy file and running only it saves **52.8s, not 4 minutes** — 15% of the claimed benefit for the file carrying 85% of the cost. It also silently drops coverage: 5 subjects span 2-3 test files (`bd-anchorcheck.py` 3; `bd-verify-cut.sh`, `bd-denom-preflight`, `bd-band-remote.sh`, `bd-rebase-cut.py` 2 each), and real edits are to scripts, not test files, so "the changed test file" is not even well-defined. **Also struck: parallelising the harness suite** — `-n 12 --dist loadfile` measured 372.68s vs 370.73s serial, i.e. worse than nothing, because the 4 expensive tests share a module-scoped fixture. `--dist load` would fire concurrent `git worktree add` against the live repo; do not adopt it.

# Is any of this worth your last hours

**One item, yes, conditionally. The rest, no — and the honest advice is the lane is fast enough.**

Build the harness fast lane if and only if you are still editing harness scripts in the time remaining: ~10 minutes of work, 5.3 minutes back per edit, break-even at edit two. If harness editing is done for the session, skip it too.

Everything else: no. The queue claimed roughly 16 minutes per verify. Measured, the entire remaining pool is ~62s, and every second of it is either already banked, unsafe, or unbankable. A verify attempt's time is band 370s + prepush 314s, and prepush is already cached to 1.6s on rerun. **Nothing in this queue touched either.** Six items were queued off numbers that were assumed rather than measured; the correct read of that is not "find a seventh item," it is that the assumed-number well is dry.

Pointer, not a build item: if the real goal was ever fewer discarded verdicts rather than raw seconds, the target is `bd-verify-cut.sh:1032`'s bare SHA-equality guard on `origin/main` and `bd-land:121-154`'s unconditional sibling rebase — 2 of 24 attempts died there, versus 1 on the trio. Research it when there is a session for it, not in the last hours.
