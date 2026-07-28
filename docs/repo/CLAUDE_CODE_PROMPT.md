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

Be accurate about this. Every figure below is a measurement with a date on it,
not a standing fact -- **re-derive before you quote any of them**:

- **The repository has real history.** As of 2026-07-27 (commit `f28c32f`) there
  are 210 commits on `main` and 3358 tracked files, behind 36 merged PRs. Bisect,
  `git diff` against a prior commit, and branch checkpoints all work. Re-derive
  with `git rev-list --count HEAD` and `git ls-files | wc -l`.
- **CI runs on every push.** `.github/workflows/ci.yml` had executed 132 times as
  of 2026-07-27, most recently green on both `main` and the active branch. A red
  is now a real signal about the tree first and the workflow second. Re-derive
  with `gh run list --workflow ci.yml` before quoting any count.
- **The `bd-*` toolchain has been measured and partly ported.** `toolchain/bin/`
  holds 246 `bd-*` tools (plus the `bd` launcher and four `bdtools_*.py` helper
  libs). `docs/repo/TOOLCHAIN_PORTABILITY.md` is the ledger: a per-tool class
  (`RUNS` / `RUNS-DEGRADED` / `SANDBOX-BOUND` / `UNKNOWN`), derived by running
  each tool rather than by grep. Read that ledger instead of re-deriving from
  scratch -- but re-run any tool whose verdict you are about to rely on, because
  the ledger is stamped 2026-07-20 and numbers that move must be measured at
  decision time.
- **Deployment is git.** The box runs `git fetch origin main` + `git reset --hard
  origin/main` + a service restart. Operator-confirmed 2026-07-27; there is no
  zip overlay and no zip fallback. Deletions propagate natively, so the old
  `unzip -o` overlay-orphan class can no longer occur. Merged `main` IS what runs
  on the box -- but you still cannot see the box, so never claim a state there.
- **A git deploy moves files; it does not make the running system match them.**
  None of the following were ever properties of the overlay, so none of them went
  away with it. This is a **condition to satisfy, not a fixed-length list** --
  treat anything else in this class the same way:
  - `__pycache__/*.pyc` are **not** cleared. `git reset --hard` leaves stale
    bytecode exactly as `unzip -o` did; the v3.66.161 footgun is unchanged.
  - Gitignored generated artifacts are **not** refreshed, and `git clean -fd`
    will not remove them either (that needs `-x`). A stale
    `reports/gui_parity_inventory.json` fails the **entire** suite.
  - The service is **not** restarted.
  - `frontend/dist/` is **not delivered at all** -- it is gitignored
    (`frontend/.gitignore:3`) and `git ls-files frontend/dist` returns nothing.
    `bulk_downloader/app.py` serves a uniform 503 when the bundle is missing, so
    a missing or stale bundle is a silent 503 on the SPA. Rebuild with
    `cd frontend && npm ci && npm run build` whenever SPA source changed.

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
toolchain's portable-candidate pool into a measured ledger of what actually runs.
Nothing here touches `bulk_downloader/` or the host.

Work in this order. Each phase has acceptance criteria; do not advance until the
prior phase's criteria are met and shown.

## Phase 1 — reproduce the environment

```bash
python3.12 -m venv venv && ./venv/bin/pip install -r requirements.txt
# The venv MUST be 3.12 -- that is the box/CI interpreter. Bare `python3` in this
# container is 3.11 WITHOUT the project dependencies, and a band measured on it
# once reported seven failures that did not exist. There is no `.venv`; a command
# naming one exits 127. Verify: ./venv/bin/python --version
cd frontend && npm ci && cd ..
```

**Acceptance:**
- Both complete, exit 0, output pasted.
- `./venv/bin/python -c "import bulk_downloader; print(bulk_downloader.__version__)"`
  prints the version in `bulk_downloader/__init__.py` (3.66.818 as of 2026-07-27
  -- read it from the source, do not pin this line).
- `./venv/bin/python --version` reports 3.12.x.
- A fast suite runs green:
  `./venv/bin/python -m pytest tests/test_settings_center_slice4.py -q`

**Correction to an earlier warning:** it was previously suggested that
`requirements.txt` might not resolve against a clean index because it was built
for an offline wheelhouse. **That was measured and is wrong** — both
`requirements.txt` and `requirements-dev.txt` resolve cleanly (pip dry-run,
exit 0, 20 and 27 packages respectively), and `scripts/cloud-setup.sh` installs
them successfully end to end. If a pin *does* fail, report the specific conflict;
do not silently relax it or add `--no-deps` to quiet it. A pin that cannot
resolve is information about the dependency set, not an obstacle to route around.

**Faster path:** `bash scripts/cloud-setup.sh` performs this whole phase and
writes `.claude-env-report.md`. Read that report first — a `WARN` row is an
ABSENT capability, not a passing one.

**Date the report before believing it.** It is gitignored, survives
`git clean -fd`, and is written once per provisioning run, so it outlives the
tree it describes: one was found seven days old asserting v3.66.811 against a
v3.66.818 tree while a session read its rows as current. Run
`venv/bin/python toolchain/bin/bd-env-report-check` — `FRESH` (0), `STALE` (1),
`UNKNOWN` (2). UNKNOWN is not a soft pass: a report that cannot be dated is
indistinguishable from one written against a different tree.

Do **not** run the whole `tests/` directory. Known long runner:
`test_perf_lab.py`. (An earlier version of this line also named
`test_v3_66_146_nav_guard`; verified 2026-07-27 that no such file exists in any
variant -- the only 146 files are `test_v3_66_146_runtime_gate.py` and
`test_v3_66_146_detection_safety.py`. If a second hanger exists it is currently
unnamed, so treat the list as incomplete rather than exhaustive.)

## Phase 2 — validate the CI logic locally

`.github/workflows/ci.yml` has six gates. Execute each one's logic by hand
against this tree and report pass/fail with real output:

1. gitleaks with `.gitleaks-baseline.json` → expect **0 new findings**
2. `python -m compileall -q bulk_downloader tools tests` → expect exit 0
3. pyflakes over `bulk_downloader tools` → advisory; **report the count and the
   top three categories**, do not fix them yet
4. current CHANGELOG entry is ASCII → expect clean across the entry
5. version-pin coherence -> `bulk_downloader/__init__.py`, the pin in
   `tests/test_settings_center_slice4.py`, and the top CHANGELOG header must all
   agree. Do not hardcode the number here -- read it from `__init__.py`. Note the
   predicate trap: `grep -rnE '__version__ *== *"3\.66\.' tests/` returns 5 hits
   and only ONE is a real pin; the rest are fixture string literals.
6. the seven guard SHAs → expect 7 ok, 0 drifted

**Acceptance:** all six executed, real output shown. If a gate's *logic* is
wrong (as opposed to the tree being wrong), fix the workflow and say what was
wrong with it — that is a correct outcome for this phase, not a failure.

**Then prove gate 1 can still see:** add a file containing a fresh
GitHub-token-shaped string, confirm gitleaks exits non-zero, and delete it. A
secret gate that has never been shown to fire is not a gate. Paste both exits.

## Phase 3 — the toolchain portability ledger

This is the substantial piece.

Produce `docs/repo/TOOLCHAIN_PORTABILITY.md`: for **every** `bd-*` tool in
`toolchain/bin/`, a verdict in one of four classes. Derive the denominator at
run time (`ls toolchain/bin/bd-* | wc -l` was 246 on 2026-07-27) rather than
working to a quoted count -- a ledger that covers fewer tools than exist is the
empty-denominator failure this document keeps warning about.

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

- Start from the tools with no hardcoded sandbox path, but **re-derive that set
  yourself** -- the split came from grep and is a candidate pool, not a fact. The
  sandbox-marker grep (`/home/claude`, `/tmp/prestaged`, `/mnt/project`) matched
  151 of 246 on 2026-07-27; both halves move, so measure, do not quote.
- `bd-coupling-meter` is confirmed `RUNS`; use it to calibrate what good output
  looks like.
- Many tools take `--work` / `--tree` to point at a tree root. Check argparse
  before concluding a tool is bound to a path.
- Some will need a `BD_ROOT`-style override rather than a rewrite.

**Port as you go — fix what you find, do not defer it to a later pass.** An
earlier draft of this mission made Phase 3 a ledger only; that was overruled.
When a tool fails only because it hardcodes a sandbox path, fix it in place and
prove the fix by running it. Keep the ledger updated as the record of what you
did, not as a substitute for doing it. Two constraints on that:

- **Fix the cause, not the symptom.** A tool that assumes `/home/claude/work`
  wants a resolved tree root (argument, then `BD_ROOT`, then a repo-root walk),
  not a second hardcoded path.
- **Every port needs a run.** A ported tool that has not been executed against
  this tree is `UNKNOWN`, not `RUNS`.
- `docs/repo/SANDBOX_SPEC_AND_LAYOUT_v3_66_805.md` documents the environment the
  tools were written for. It is the reference for what a port must supply.

**Acceptance:**
- Every `bd-*` tool present in `toolchain/bin/` at the time you run has a class
  and a one-line justification -- state the denominator you enumerated and how.
- Counts per class, and the method used to derive them, stated at the top.
- The `RUNS-DEGRADED` list is called out separately as the priority list, with
  what each one silently missed.
- A short section: which tools become **redundant now that git exists** (for
  example, anything that diffs the work tree against a pinned zip, or that
  snapshots the tree — git does both natively). Recommend, don't delete.

## Environment

You have **full network access**. Install from upstream — **do not use the
offline packs (A-H, cloak, nuitka, test-tools)**; they exist to provision an
air-gapped sandbox and are 2.5 GB of redundant transfer here.
`docs/repo/ENVIRONMENT_PROVISIONING.md` gives the upstream equivalent of every
pack, tiered so you install only what the task needs, plus a capability probe.
Run the probe before scoping anything that needs `CAP_NET_ADMIN` — that
capability is unverified in this environment.

## Out of scope for this mission

Do not: modify `bulk_downloader/`, bump the version, touch guard files, fix the
pyflakes backlog, or run the full suite.

## Report at the end

Counts per class, the `RUNS-DEGRADED` priority list, anything in Phases 1–2 that
did not reproduce, and the single highest-value next task with your reasoning.
