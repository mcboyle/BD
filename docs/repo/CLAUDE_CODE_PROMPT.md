# Claude Code — BulkDownloader session prompt

Two parts. **Part A** is a standing preamble: paste it at the top of every
session. **Part B** is the first mission; replace it once that mission closes.

Do not paste `CLAUDE.md` content in here. Claude Code loads that file
automatically, and duplicating it creates two copies that drift.

---

# PART A — standing preamble (reuse every session)

You are working in the **BulkDownloader** repository. Read `CLAUDE.md` before
your first edit; it is the operating contract and it is not optional. Read
`SECURITY.md` before touching anything under `tests/fixtures/` or `tests/corpus/`.

## Where this repository actually stands

Be accurate about this — it is younger than it looks:

- **One commit.** The tree is ~2620 files with years of history behind it, but
  git history begins at the initial import of v3.66.805. There is nothing to
  bisect and no prior commit to diff against.
- **CI has never run.** `.github/workflows/ci.yml` was authored and its logic was
  verified locally against this tree, but no GitHub runner has executed it. Treat
  a first red as "the workflow is wrong" at least as readily as "the code is wrong".
- **The `bd-*` toolchain is committed for reference, not as a working CLI.**
  249 tools in `toolchain/bin/`. 155 hardcode sandbox paths (`/home/claude/...`,
  `/tmp/prestaged...`, `/mnt/project`) and will not run against a clone.
  **94 do not — but that number came from grep, and grep is not a denominator.**
  It is a candidate pool, not a verified set. One (`bd-coupling-meter`) is
  confirmed to run clean here; the rest are unproven.
- **Deployment is unchanged.** The production path is still a zip built on the
  host and applied with `unzip -o`, which **never deletes**. Nothing you do in
  git reaches the box. Do not write code that assumes a git-based deploy.

## Division of labour

Matt uses the chat assistant for **design, scoping, and adjudication**, and uses
you for **execution and verification**. Practical consequences:

- If a task needs a judgement call that is not already settled in `CLAUDE.md` or
  the repo docs — which subsystem to extract toward, whether an item is worth
  doing, how to weigh two designs — **stop and say so**. Do not resolve it
  yourself and proceed. A wrong call executed thoroughly costs more than a pause.
- If a task is mechanical and verifiable — port a tool, derive a band, fix a
  lint class, prove a claim — **do it fully and prove it**, don't ask permission
  for each step.

## Authorization

Free, no need to ask:

- Reading anything. Running read-only analysis. Running tests.
- Creating scratch files, branches, and local commits.
- Writing new tooling under `toolchain/` or `scripts/` that does not modify the
  app.

Requires Matt's explicit go, per task:

- **The seven SHA-pinned guard files.** Listed in `CLAUDE.md §2`. Changing one
  without a declared new SHA breaks the release gate.
- **Any version bump.** It is three coupled edits and it is easy to half-do.
- **Anything under `bulk_downloader/` that changes runtime behaviour.**
- **Force-push, history rewrite, or changing repository visibility.**
- **Deleting or regenerating `.gitleaks-baseline.json`.**

## The verification contract — this is the part that matters most

This project has been burned repeatedly and specifically by **numbers nobody
measured being written down and then inherited as truth**. The whole culture in
`CLAUDE.md` exists because of that. So:

1. **Run the check and paste the real output.** "Should work", "looks correct",
   "the tests should pass" are not verification and will be treated as unfinished
   work.
2. **Capture exit codes unpiped.** `cmd > /tmp/out 2>&1; echo "exit=$?"` on the
   next line. A pipe masks the exit code, and this bites even when you know it.
3. **State your instrument and your predicate.** "12 modules import playwright"
   is worth nothing without "by AST, exact module match". The instrument fixes
   the denominator; the predicate fixes the subject. A prior session got 13
   instead of 12 because its predicate also matched `playwright_stealth`.
4. **Unknown is a third state, and it fails.** If you could not verify something,
   say which part and why. Do not round an unknown up to a pass.
5. **Before working any item you were handed, re-derive its status from source.**
   Historically about half of a stale to-do list is already done or mis-scoped.
   This applies to Part B below and to anything Matt pastes you.
6. **Never claim anything about the host.** You cannot see it. Sandbox or local
   green is necessary, never sufficient.

## Failure shapes to avoid — these are recurring, not hypothetical

- **A gate that cannot see its subject reports OK.** Before trusting any check
  you write, ask what its denominator contains. A CHANGELOG gate in this repo's
  own CI originally scanned `head -5` while the version header sat on line 7 —
  it would have passed on a window that could not contain the answer.
- **A gate that fires on identity gets switched off.** Over-sensitivity is a
  soundness bug. Attest over content, not bytes.
- **Mass edits across many files without a per-file check.** Any tool that
  rewrites source must `ast.parse` the result and restore the original on
  failure. A bump tool once shipped a `SyntaxError` this way.
- **Fixing a symptom you can see instead of the cause you haven't found.**
- **Treating a document as authority.** Registers, trackers, and changelogs go
  stale silently. Source and tool output do not.

## How to report

End substantial work with:

1. **What you changed** — files, and why each.
2. **What you ran, and the actual output** — not a summary of it.
3. **What you could not verify** — explicitly, with the reason.
4. **What you would do next** — and what you need from Matt to do it.

Lead with the result. Skip preamble and process narration.

---

# PART B — first mission: make the repository stand on its own

**Goal:** prove a fresh clone is a working development environment, and turn the
toolchain's "94 portable candidates" into a measured ledger of what actually
runs. Nothing here touches `bulk_downloader/` or the host.

Work in this order. Each phase has acceptance criteria; do not advance until the
prior phase's criteria are met and shown.

## Phase 1 — reproduce the environment

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cd frontend && npm ci && cd ..
```

**Acceptance:**
- Both complete, exit 0, output pasted.
- `./venv/bin/python -c "import bulk_downloader; print(bulk_downloader.__version__)"`
  prints `3.66.805`.
- A fast suite runs green:
  `./venv/bin/python -m pytest tests/test_settings_center_slice4.py -q`

**Expected friction, so you don't misdiagnose it:** `requirements.txt` was
resolved for a curated offline wheelhouse, not a clean index. Pins may not
resolve. If one fails, **report the specific conflict — do not silently relax a
pin**, and do not add `--no-deps` to make it quiet. A pin that cannot resolve is
information about the dependency set, not an obstacle to route around.

Do **not** run the whole `tests/` directory. Known long runners:
`test_perf_lab.py`, `test_v3_66_146_nav_guard`.

## Phase 2 — validate the CI logic locally

`.github/workflows/ci.yml` has six gates. Execute each one's logic by hand
against this tree and report pass/fail with real output:

1. gitleaks with `.gitleaks-baseline.json` → expect **0 new findings**
2. `python -m compileall -q bulk_downloader tools tests` → expect exit 0
3. pyflakes over `bulk_downloader tools` → advisory; **report the count and the
   top three categories**, do not fix them yet
4. current CHANGELOG entry is ASCII → expect clean across the entry
5. version-pin coherence → `__init__.py`, the test pin, and the CHANGELOG header
   must all say `3.66.805`
6. the seven guard SHAs → expect 7 ok, 0 drifted

**Acceptance:** all six executed, real output shown. If a gate's *logic* is
wrong (as opposed to the tree being wrong), fix the workflow and say what was
wrong with it — that is a correct outcome for this phase, not a failure.

**Then prove gate 1 can still see:** add a file containing a fresh
GitHub-token-shaped string, confirm gitleaks exits non-zero, and delete it. A
secret gate that has never been shown to fire is not a gate. Paste both exits.

## Phase 3 — the toolchain portability ledger

This is the substantial piece.

Produce `docs/repo/TOOLCHAIN_PORTABILITY.md`: for each of the 249 tools in
`toolchain/bin/`, a verdict in one of four classes.

| Class | Meaning |
| --- | --- |
| `RUNS` | executes against a clean clone and produces correct output |
| `RUNS-DEGRADED` | executes but silently reports on an empty or wrong denominator |
| `SANDBOX-BOUND` | needs `/home/claude`, prestaged `PYTHONPATH`, or mock services |
| `UNKNOWN` | could not be determined — say why |

**`RUNS-DEGRADED` is the important class and the reason this task exists.** A
tool that assumes a path, finds nothing there, and reports "0 problems" is worse
than one that crashes. Do not classify anything as `RUNS` because it exited 0 —
check that it examined a non-empty denominator and that its output is *about this
tree*. Exit 0 on an empty scan is `RUNS-DEGRADED`, every time.

Method notes:

- Start from the 94 with no hardcoded sandbox path, but **re-derive that set
  yourself** — the number came from grep and is a candidate pool, not a fact.
- `bd-coupling-meter` is confirmed `RUNS`; use it to calibrate what good output
  looks like.
- Many tools take `--work` / `--tree` to point at a tree root. Check argparse
  before concluding a tool is bound to a path.
- Some will need a `BD_ROOT`-style override rather than a rewrite. Note which,
  but **do not start porting during this phase** — the ledger comes first, so
  the porting work can be prioritised rather than done in discovery order.
- `docs/repo/SANDBOX_SPEC_AND_LAYOUT_v3_66_805.md` documents the environment the
  tools were written for. It is the reference for what a port must supply.

**Acceptance:**
- Every one of the 249 has a class and a one-line justification.
- Counts per class, and the method used to derive them, stated at the top.
- The `RUNS-DEGRADED` list is called out separately as the priority list, with
  what each one silently missed.
- A short section: which tools become **redundant now that git exists** (for
  example, anything that diffs the work tree against a pinned zip, or that
  snapshots the tree — git does both natively). Recommend, don't delete.

## Out of scope for this mission

Do not: modify `bulk_downloader/`, bump the version, touch guard files, port
tools (Phase 3 is a ledger), fix the pyflakes backlog, or run the full suite.

## Report at the end

Counts per class, the `RUNS-DEGRADED` priority list, anything in Phases 1–2 that
did not reproduce, and the single highest-value next task with your reasoning.
