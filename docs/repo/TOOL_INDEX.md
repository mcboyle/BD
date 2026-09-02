# TOOL_INDEX -- the tools that are the daily workflow

## What this document is

BD's tool denominator is over six hundred files across four populations. A
document that lists all of them is a document nobody reads, and an index nobody
reads is the same thing as no index: the recorded cost of rediscovering existing
tooling in this repository is ninety minutes and eight defects re-found that the
existing tool had already fixed.

So this indexes the tools that carry the lane -- start a cut, gate it, verify
it, land it, prove it shipped, deploy it -- and points mechanically at the rest.
It extends the A8 routing table in `CLAUDE.md`, which is deliberately small; A8
routes a question to an owner, this says how the owner is invoked, what it
returns, and what it will do to you if you invoke it wrong.

It is a starting point, not a complete denominator. A8's rule still binds:
before creating a file at any path, prove that exact name is unused in BOTH the
repository and the operator harness, and read an existing caller before
assuming an interface.

## How the documented set was chosen

Three sources, unioned:

1. **Reference count.** The operator harness map at
   `/home/mboyle/bd-persist/workers/1457/HARNESS_MAP.txt` records, per harness
   file, how many other files name it. Everything at or above roughly fifteen
   inbound references is in the lane by construction: it is called by the lane.
2. **Named in the contract.** Every tool `CLAUDE.md` names by role -- the gates
   in A3 and A5, the routing table in A8, the deploy path in A6.
3. **Safety boundaries.** A tool whose whole job is to refuse something
   dangerous is in the lane even with one caller, because its value is realised
   at the moment it is invoked by hand.

Everything else is reachable through the mechanical enumeration below.

## Populations, measured

Measured on host `test5`, in the worktree for `cut/1479-the-toolchain-has-an-index`,
at base commit `0b150ad1d6b48e3006227defbe3738ccc9126a72`,
tree `905492772198f6d230fb9261595903c601fc9ff1`, 2026-09-02T14:41Z.
These are volatile. Re-derive rather than quote.

| Population | Count | Re-derive with |
| --- | --- | --- |
| `toolchain/bin` operational tools (tracked, extensionless) | 256 | `git ls-files toolchain/bin \| wc -l` |
| `tools/*.py` build/analysis scripts (top level) | 228 | `ls tools/*.py \| wc -l` |
| `tools/**/*.py` in subdirectories | 20 | `git ls-files 'tools/*.py' \| wc -l` minus the above |
| `scripts/` install/deploy/service entry points | 20 files, 9 of them `.sh` | `ls scripts/` |
| Operator harness `/home/mboyle/bd-*` (depth 1) | 222 entries: 177 files, 45 directories | `find /home/mboyle -maxdepth 1 -name 'bd-*' \| wc -l` |
| ...of those files, executable | 142 | `find /home/mboyle -maxdepth 1 -name 'bd-*' -type f -executable \| wc -l` |

Note the two denominators that disagree on purpose: `ls toolchain/bin` returns
257 in the integrator's own checkout because `__pycache__` is present and
untracked. A glob is a denominator choice; say which one you used.

## The lane at a glance

| Question | Tool |
| --- | --- |
| Where do I do this work? | `/home/mboyle/bd-cut.sh` |
| Will an expensive gate reject this tree anyway? (26s) | `toolchain/bin/bd-denom-preflight` via the harness copy |
| Will CI reject it? (~2 min, local) | `/home/mboyle/bd-prepush.sh` |
| Which tree-wide gates can the band never derive? | `toolchain/bin/bd-precut --gate` |
| Which tests does this diff require? | `toolchain/bin/bd-band-derive` |
| Are the generated artifacts in sync? | `toolchain/bin/bd-regen-order` |
| Are the doc citations still real? | `toolchain/bin/bd-freshcheck --repo-only` |
| Did an edit touch a guarded file? | `toolchain/bin/bd-guardcheck` |
| Does the band actually catch a defect? | `toolchain/bin/bd-mutate` |
| Is this candidate ready for a ten-minute verify? | `/home/mboyle/bd-verify-when-ready` |
| Is this candidate shippable? | `/home/mboyle/bd-verify-cut.sh` |
| Land it without wrecking its siblings | `/home/mboyle/bd-land` |
| Has this work actually reached main? | `/home/mboyle/bd-shipped` |
| Is that worker really finished? | `/home/mboyle/bd-worker-terminal` |
| Is X running? | `/home/mboyle/bd-running` |
| Kill it without killing myself | `/home/mboyle/bd-kill-mine.sh` |
| Edit a script that is currently executing | `/home/mboyle/bd-edit.py` |
| Deploy one host / the fleet | `scripts/deploy.sh`, `/home/mboyle/bd-fleet-deploy.sh` |
| What does the browser actually see? | `/home/mboyle/bd-shoot.py` |

---

# A. Starting a cut

## bd-cut.sh -- start an integrator cut in its own worktree

* **Location** `/home/mboyle/bd-cut.sh` (operator harness, 26 lines). Status: live.
* **Answers** "where do I do this work without colliding with the other cut?"
* **Invoke** `bash /home/mboyle/bd-cut.sh cut/1479-the-toolchain-has-an-index`
  -> prints `/home/mboyle/bd-cuts/cut/1479-the-toolchain-has-an-index`.
* **Inputs** one branch name. **Outputs** the worktree path on stdout.
* **Exit codes** 0 created; 2 the worktree directory already exists (it refuses
  rather than reusing); 1 `git worktree add` failed.
* **Dependencies** the integrator repo at `/home/mboyle/BulkDownloader`; it
  fetches `origin` and prunes stale worktrees first, then branches from
  `origin/main` -- never from the integrator's working HEAD.
* **Why it exists** overlapping two cuts on one working tree put an edit on the
  wrong branch: a gate's own `_DECLARED` entry was written while the tree had
  been switched to the next cut, so CI refused a candidate whose fix existed on
  a neighbouring branch.

**SAFETY -- NEVER RUN `npm ci` IN A CUT WORKTREE.** The script ends with two
symlinks: `venv` and `frontend/node_modules` both point at the integrator's
copies. `npm ci` deletes `node_modules` before installing, and through a symlink
it deletes the *integrator's* shared tree. This happened on 2026-09-02: 253
packages to 0, taking down every other worktree at the same time. For lockfile
work use `npm install --package-lock-only`, which does not touch the tree.

## bd-next-row -- what is the next free register row id?

* **Location** `/home/mboyle/bd-next-row` (39 lines). Status: live.
* **Answers** "what id do I file this row under, and what do I name the test?"
* **Invoke** `bash /home/mboyle/bd-next-row` or `bash /home/mboyle/bd-next-row --json`.
  Optional first argument is an alternative backlog path.
* **Outputs** the next free id; `--json` also reports the row count, the maximum
  id, and the gaps.
* **Exit codes** 0 answered; 2 UNKNOWN -- no register at the path, zero rows
  parsed, or duplicate ids (it refuses to guess an id over a register that is
  not a population).
* **Why it exists** a test file was named from the row COUNT: the register held
  474 rows but its ids ran to 530, because ids are never reused and rows have
  been retired. Count and max are different questions and the `--json` output
  says both.

# B. The cheap gates, run before an expensive one

The ordering here is the whole point. Every gate in this section costs seconds;
`bd-verify-cut.sh` costs ten to fifteen minutes and a re-freeze. A RED here
aborts the expensive lane, and that is a deliberate exception to A5's "do not
cancel independent lanes merely because one fails" -- these gates judge the TREE,
so their failure says nothing about the band's and vice versa.

## bd-denom-preflight -- the 26-second answer to "will an expensive gate reject this anyway"

* **Location** `/home/mboyle/bd-denom-preflight` (136 lines). Status: live.
* **Answers** "does anything this cut added break a pinned denominator somewhere
  else in the tree?"
* **Invoke** `bash /home/mboyle/bd-denom-preflight /home/mboyle/bd-cuts/cut/1479-the-toolchain-has-an-index`.
  The argument defaults to `/home/mboyle/BulkDownloader`, which is almost never
  what you want from inside a cut.
* **Inputs** a work tree containing `venv/bin/python`.
* **Exit codes** 0 the cheap denominators agree; 1 RED; 2 UNKNOWN (unreadable
  directory, no venv interpreter, or a pinned member absent from the tree).
  **A missing pinned member is UNKNOWN, never a smaller lane that passes.**
* **What it runs** a fixed list of twelve pinned test files -- the CI gate-shard
  declaration gate, the gate-scope debt gate, the one-task-authority gate, the
  machine-visible-backlog and backlog-truth gates, versync, the version pin, the
  pin index, the nested-freshness gate, and the three cheap tree-wide gates
  lifted out of `bd-precut` (mutant anchors, ambient locale into a subprocess,
  guards surviving a module wipe). The other three `bd-precut` gates cost 104s,
  61s and 21s and stay there.
* **Dependencies** the work tree's own `venv/bin/python`. It shares one
  interpreter start across the whole lane; that is where the 26 seconds comes
  from.
* **Its own tests** `bd-persist/harness/tests/test_denom_preflight_sees_its_incidents.py`
  holds negative controls proving the lane can see its two motivating incidents.

## bd-prepush.sh -- everything CI would tell me, told locally in two minutes

* **Location** `/home/mboyle/bd-prepush.sh` (147 lines). Status: live.
* **Answers** "will exact-head CI reject this, before I pay for a CI round trip?"
* **Invoke** `bash /home/mboyle/bd-prepush.sh /home/mboyle/bd-cuts/cut/1479-the-toolchain-has-an-index`.
* **Inputs** a work tree path. **It defaults to `/home/mboyle/BulkDownloader`**,
  and the hardcoded `cd` it replaced once ran every gate against main instead of
  the candidate -- a green answer about the wrong tree. Always pass the tree.
* **Outputs** one aligned OK/FAIL line per gate, plus up to three grep-extracted
  failure lines for each FAIL. Exit 1 only for an unreadable work tree; the
  aggregate verdict is read from the printed lines, so read them.
* **Dependencies** a pinned Gitleaks 8.24.3 at
  `/home/mboyle/.cache/bd-tools/gitleaks/8.24.3/gitleaks`. An absent binary is a
  FAIL, not a skip -- a PATH fallback would turn a release gate into a different
  scanner.

**SAFETY -- gitleaks scans branch HISTORY, not the checkout.** Fixing the working
tree does not clear a finding: the secret is still in an earlier commit on the
branch, and the scan walks the PR range. A touched baseline line is a newly
evaluated line, and moving a realistic-looking secret into a fixture hides it
from the scan without removing it from the history.

## bd-precut --gate -- the tree-wide gates the band can never derive

* **Location** `toolchain/bin/bd-precut` (627 lines). Status: live.
* **Answers** "is anything true of the whole TREE that would refuse this cut?"
  `bd-band-derive` derives from changed paths, so a gate whose subject is
  everything can never be selected by it. Between v3.66.1223 and v3.66.1238 four
  defects shipped that a gate already in this tree would have caught; none was a
  missing gate, each was a gate that did not run.
* **Invoke** `venv/bin/python toolchain/bin/bd-precut --gate` from the work-tree
  root. Advisory mode (no `--gate`) is `venv/bin/python toolchain/bin/bd-precut`.
* **Flags** `--gate` makes it blocking; `--baseline ZIP`, `--root DIR`, `--json`,
  `--selftest`, and the opt-outs `--no-insync`, `--no-envscan`, `--no-coretest`.
* **Outputs** three delegated reports: the advisory version/pin/surface/guard
  prediction from `tools/precut_check.py`; the in-sync generator gate tests, run
  for real so a stale generated document is caught rather than predicted; and
  `bd-envscan`, which names the exact env vars that would fail the env tranche.
* **Exit codes** advisory mode exits 0 regardless. `--gate` exits non-zero on any
  real problem. **The exit code is the point** -- run it before freezing a
  candidate and read the status, not the output.

## bd-band-derive -- which tests does this diff require?

* **Location** `toolchain/bin/bd-band-derive` (1613 lines). Status: live.
* **Answers** "what is the FLOOR of tests this change must run?" A8 routes here;
  A5 says the output is a floor and never a ceiling.
* **Invoke** `venv/bin/python toolchain/bin/bd-band-derive --files bulk_downloader/detect.py toolchain/bin/bd-precut`
  for an explicit changed set; `--file PATH` for one file's full radius; bare for
  the work tree's own diff; `--emit` prints only the resulting band line;
  `--json`; `--selftest`.
* **How it derives** five unioned signals: filename-stem glob; the curated map in
  `project-knowledge/TOUCHED_FILE_TO_TEST.md`; declared count-coupling; every
  test that imports or names the changed module; and the import-contract edge,
  where a changed writer importing a declared shared provider selects that
  provider's contract gate.
* **Exit codes** 0 derived; 2 for an unusable request, and unresolved import-graph
  state is reported UNKNOWN and exits nonzero rather than emitting a short band.
* **Safety** only suites that EXIST on disk are emitted -- it never invents a
  band. An earlier build used a substring test, so one map row matched
  everything and the tool returned a near-constant band regardless of input. A
  wrong band is worse than no band: the cut bands the wrong suites and goes GREEN
  on a regression.

## bd-regen-order -- regenerate the derived artifacts in the one correct order

* **Location** `toolchain/bin/bd-regen-order` (526 lines). Status: live.
* **Answers** "which generator has to run before which?" -- an order that used to
  live only in someone's head and cost two stash REDs and an aborted cut.
* **Invoke** `venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"` from the
  work-tree root, **after the last source edit**. `--dry-run` prints the chain and
  runs nothing; `--tracked-outputs` lists the paths it may write; `--selftest`.
* **The order** gui parity inventory, then route index (which joins against it),
  endpoint catalog, dependency graph, function index, invariant tags, source
  window hashes, pin index, and finally a route-count check that verifies rather
  than generates.
* **Exit codes** 0 the chain ran; 2 on a bad request. Read the resulting `git
  diff` and explain it; regeneration can invalidate gates you already ran.
* **What it deliberately will NOT do** re-freeze the frozen baselines --
  `tests/route_map_baseline.txt` with its SHA pin, and
  `tools/decomp/import_graph_baseline.json`. Those are declarations of intent,
  not derived documents. Declaring one is `--declare-surface`, `--declare-edges`
  or `--declare-reach`, in the same cut, deliberately.

## bd-freshcheck -- have the documents gone stale?

* **Location** `toolchain/bin/bd-freshcheck`. Status: live.
* **Answers** the mechanically checkable half of staleness: a `file.py` line
  citation whose file is gone or whose line is past the end, and a doc claim
  about a source path that no longer resolves. It cannot check whether the line
  still SAYS what the document claims.
* **Invoke** `venv/bin/python toolchain/bin/bd-freshcheck --repo-only` for any
  documentation or canonical-backlog edit. Also `--json`, `--root DIR`,
  `--selftest`.
* **Exit codes** 0 every check ran and passed; 1 STALE; 2 UNKNOWN -- a check could
  not RUN. **2 is not a softer 1.**
* **The two populations, and why `--repo-only` exists** repo-derivable checks read
  only tracked content and mean the same thing in a container, in CI and on the
  box. The environment-local check reads a gitignored provisioning artifact that
  does not exist in CI, so including it would return UNKNOWN forever and the gate
  would be switched off within a week.
* **THE TRAP THAT MATTERS FOR THIS DOCUMENT** the anchor corpus comes from `git
  ls-files`. An UNTRACKED new document is outside the denominator, so the gate
  goes green over a corpus that excludes its own subject. `git add` the file
  first, then run the gate from the work-tree root -- `--root` defaults to the
  current directory, so running it in the integrator's tree gates the wrong tree.
* **Corpus** root-level `*.md` plus everything under `project-knowledge/` and
  `docs/`, with `CHANGELOG.md` and `docs/archive/` classified historical and
  excluded. The rule lives once, in `toolchain/bin/bdtools_sec.py`, because two
  callers reimplementing one membership rule is exactly the drift A8 warns about.

## bd-guardcheck -- did an edit touch a guarded file?

* **Location** `toolchain/bin/bd-guardcheck` (308 lines). Status: live.
* **Answers** "have the release-guard files changed without a declaration?"
* **Invoke** `venv/bin/python toolchain/bin/bd-guardcheck --tree "$PWD"`. The
  baseline resolves in strict precedence: explicit `--guards PATH` (absent or
  malformed is a HARD ERROR, never a silent fallback), then `--state PATH`, then
  the repo-root `guards.json`, then operator STATE discovery.
* **Exit codes** 0 every guard resolved and matched; 1 at least one DRIFTED,
  declare a new SHA or revert; 2 UNKNOWN -- no baseline at all, a guard file
  missing or unreadable, a guard with no pin, or a summary whose buckets do not
  account for every guard.
* **Why exit 2 exists** it once defaulted `--tree` to an absent sandbox path, so
  from a git checkout all seven guards read as FILE MISSING, and `if drift:
  return 1` was the only nonzero path. It printed "0 ok, 0 drifted, 7 missing."
  and exited 0 -- certifying a tree it had never read.

## bd-mutate -- does the band actually catch anything?

* **Location** `toolchain/bin/bd-mutate` (3169 lines). Status: live.
* **Answers** "is this green band evidence, or is it green because nothing tests
  the changed line?" Mutation testing is the only thing in this project that has
  repeatedly found defects a green band could not.
* **Invoke** `venv/bin/python toolchain/bin/bd-mutate --spec SPEC.json --work "$PWD"`.
  Also `--emit-spec BASENAME`, `--subject`, `--direction regression|overcorrection|all`,
  `--timeout`, `--band` for legacy bare lists, `--json`, `--selftest`, `--recover`.
* **Use it instead of hand-rolling.** Five separate agents rebuilt this harness
  and the harness -- not the subject -- was wrong in four distinguishable ways,
  each producing a confident wrong number: an anchor matching more than one site
  so the verdict describes a different line than the report names; a mutant that
  does not parse, whose collection error reads as an ESCAPE; stale bytecode,
  because a same-length substitution restored inside one wall-clock second
  changes neither mtime nor size; and a baseline that was never green, where
  agents reimplemented the absent feature and scored their own code.
* **A7's contract for any source rewriter** assert the old anchor occurs exactly
  once; mutate in memory and write once; prove bytes changed by exact arithmetic;
  parse the result; restore and abort on malformed output; treat invalid mutants
  separately from caught and escaped; prove RED with the mutant and GREEN
  without; record recovery state before the first irreversible write.
* **Related** `/home/mboyle/bd-anchorcheck.py` proves every tracked mutant anchor
  still occurs exactly once; a broken anchor is what `bd-denom-preflight` now
  catches thirteen minutes and one CI round trip earlier than it used to.

## bd-versync -- do the version claims agree?

* **Location** `toolchain/bin/bd-versync` (134 lines). Status: live.
* **Answers** "does `__version__` agree with the newest `CHANGELOG.md` header and
  with the pin index?"
* **Invoke** `venv/bin/python toolchain/bin/bd-versync --tree "$PWD"`; `--selftest`.
* **Exit codes** 0 in sync; 1 findings; 2 CANNOT EVALUATE -- for example when
  `tools/build_pin_index.py` is absent, so there is no pin index to gate against.
  The three codes come from the shared contract in `toolchain/bin/bdtools_sec.py`.
* **Finding** its entire docstring is `bd-versync fixed.` A gate in the
  `bd-denom-preflight` pinned lane documents nothing about its own subject; read
  the source, not the docstring.

## import_graph_gate.py -- did this cut add an import edge?

* **Location** `tools/decomp/import_graph_gate.py` (368 lines). Status: live.
* **Answers** "did a decomposition cut create accidental coupling?" It is the
  complement to the surface lock: those prove nothing left, this proves nothing
  new crept in.
* **Invoke** `venv/bin/python tools/decomp/import_graph_gate.py --check` (exit 1
  on any NEW edge), `--update` to re-freeze, `--list` for edge counts, and
  `--shrink` alongside `--update` when the baseline is genuinely getting smaller.
* **Exit codes** 0 clean; 1 a new edge, a parse failure anywhere in the walked
  file set, or a refused shrink. A file the parser cannot read contributes no
  edges, so every mode first parses the same file set the graph walks and exits
  nonzero naming the count and the files rather than reporting PASS over a
  silently reduced denominator.
* **Behaviour worth knowing** an edge REMOVED by a cut is reported but does not
  fail `--check`. `--update` refuses to shrink the baseline without `--shrink`,
  because baking a reduced edge set in is how a temporarily blind gate becomes
  permanently blind.

**SAFETY -- `tools/decomp/import_graph_baseline.json` is a WHOLE-TREE
measurement.** It cannot be merged, transplanted between branches, or hand
resolved: it describes the edge set of one tree, so a conflict resolution that
keeps "our side" or unions the two produces a baseline describing no tree that
exists. Re-derive it with `--update` in the cut that changed the edges. And
`--check` PASSES against a baseline that is too LARGE -- it only fails on edges
present in the graph and absent from the baseline -- so a green `--check` is not
a well-formedness test for the baseline.

## bd-docs-only -- can this candidate take the docs-only lane?

* **Location** `toolchain/bin/bd-docs-only` (1121 lines). Status: new (row 530).
* **Answers** "is this candidate provably incapable of affecting runtime, so it
  can skip a version number, a fourteen-minute verify, a six-minute CI and a
  fleet deploy that restarts BD on every serving host?"
* **Invoke** `venv/bin/python toolchain/bin/bd-docs-only classify --repo "$PWD" --base BASE --head HEAD [--json]`;
  a second subcommand takes `--repo` and `--commit`; `--selftest`.
* **Exit codes** 0 DOCS-ONLY; 1 RUNTIME-AFFECTING; 2 UNKNOWN, which is a failing
  third state and never permission.
* **How it is safe** the ALLOW set is small, positively derived and gated; the
  DENY set is everything else. A path is permitted only by proven membership of
  exactly three populations: the tracked Markdown corpus, from the one shared
  rule rather than a second copy of it; the release trio, and only when a
  byte-exact proof shows the head blobs differ from base by the version literal
  alone (that proof is `bd-band-transfer-key`'s, reused); and a tracked output of
  the regeneration chain, and only when re-running THAT generator in a disposable
  exact-head worktree reproduces the candidate's bytes exactly. A path it has
  never heard of, or a new top-level directory, is runtime-affecting.
* **Consumed by** `bd-verify-cut.sh`, which records the verdict as evidence and
  not as authority -- a clean rc 0 still leaves the printed verdict at a
  non-permissive value unless the classifier was actually present in the base.

## bd-shuffle-lane -- does the suite have order dependencies?

* **Location** `toolchain/bin/bd-shuffle-lane` (734 lines). Status: new.
* **Answers** "does any test file pass in isolation and fail beside a particular
  neighbour?" `--dist loadfile` hands one file to whichever worker is free, so a
  cross-file leak surfaces only by luck; three such leaks are already filed.
* **Invoke** `venv/bin/python toolchain/bin/bd-shuffle-lane --install` once per
  host, then `--seed 4126` to replay a finding, `--control` for the positive
  control, `--selftest`, or bare to run the lane.
* **Exit codes** 0 PASS; 2 usage; 3 FINDINGS; 4 UNKNOWN.
* **Status: ADVISORY, and a SECOND lane.** It never replaces or edits the A5
  canonical full-suite command, and a tracked gate pins that command's exact
  bytes so this tool cannot drift it.
* **Containment** pytest auto-loads a plugin from its entry point, so installing
  `pytest-randomly` into the repository venv would turn shuffling on for every
  pytest invocation that does not pass `-p no:randomly` -- the affected band,
  `bd-precut --gate`, every CI shard. The plugin therefore lives in a private
  directory. This is also why the `-p no:randomly` token in the A5 command must
  stay: it guarded a plugin that did not exist until now, and it is what keeps
  the canonical lane the same experiment it has always been.
