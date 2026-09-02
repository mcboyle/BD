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

# C. Verify, ship, land

**SAFETY, FOR THIS WHOLE SECTION -- `bd-verify-cut.sh` AND `bd-land` ARE
LOAD-BEARING AND OFTEN RUNNING.** Never edit either in place. `write_text()` and
an in-place shell redirect both TRUNCATE AND REWRITE THE SAME INODE, and a bash
script that is currently executing keeps reading that inode at its saved byte
offset -- so the running lane resumes in the middle of different text. That is
how one of these reported `line 138: THIS: command not found` while `bash -n` on
the same file passed cleanly. Develop against a copy and apply with
`/home/mboyle/bd-edit.py`, which renames over the target atomically so the
running process keeps the old inode and the change takes effect on the next
invocation. `scripts/deploy.sh` has the same boundary and A6 documents it there.

## bd-verify-when-ready -- do not launch an expensive lane against a tree the cheap checks refuse

* **Location** `/home/mboyle/bd-verify-when-ready` (96 lines). Status: live.
* **Answers** "is this candidate actually ready for a ten-minute verify?"
* **Invoke** `bash /home/mboyle/bd-verify-when-ready /home/mboyle/bd-cuts/cut/1479-the-toolchain-has-an-index v1479`.
* **Exit codes** 0 launched -- it prints `LAUNCHED <tag> -> <log path>` and the
  verify runs detached under `setsid nohup`; 2 usage; 3 NOT READY.
* **The five refusals, in order** the worktree is MID-REBASE, where a commit
  lands on a detached HEAD and the branch ref still points at pre-rebase work;
  the worktree is on a detached HEAD, so `bd-land` would have no branch to
  publish; there is unstaged tracked drift (the `venv` and `frontend/node_modules`
  symlinks are excluded); `bd-denom-preflight` refuses; or the tracked generated
  artifacts are out of sync.
* **On that last one** it RUNS `bd-regen-order` and then diffs the chain's own
  tracked outputs, because the regen is idempotent -- measured, a full run on an
  in-sync worktree left all ten tracked outputs clean. If it is not a no-op the
  tree needed it anyway. A zero-length tracked-output list is UNKNOWN and
  refuses, not agreement.
* **Why it exists** twice in one day an expensive verify was launched against a
  tree the cheap checks had already refused, because the chain
  `rebase; trio; commit; preflight; launch` was run and the LAUNCH line was read
  rather than the preflight line. **`bd-freshcheck --repo-only` does not answer
  the regen question** -- a register-only edit still moves
  `project-knowledge/STATIC_KB_MANIFEST.json`, and reading freshcheck's OK as if
  it had answered cost a full lane.

## bd-verify-cut.sh -- is this immutable candidate shippable?

* **Location** `/home/mboyle/bd-verify-cut.sh` (1286 lines). Status: live.
  **Load-bearing and frequently running -- see the section warning above.**
* **Answers** "over the complete net cut, does everything pass?" The denominator
  is `merge-base(fresh origin/main, candidate)..candidate`, not the final commit.
* **Invoke** `bash /home/mboyle/bd-verify-cut.sh /home/mboyle/bd-cuts/cut/1479-the-toolchain-has-an-index v1479`.
  Two arguments, both required; the tag must match `[A-Za-z0-9._-]+`.
* **Outputs** a `VERDICT <tag>: precut= prepush= derive= audit= band= over N file(s)`
  line, a `DOCS-ONLY <tag>:` line, and then one of
  `ALL GREEN -- shippable`, `SHIPPABLE WITH INHERITED FAILURES -- not green`
  (an attributed band, never reported as green, with the inherited failures and
  the base SHA printed), or `NOT SHIPPABLE` with every RED or UNKNOWN reason
  named.
* **Exit codes** 0 shippable (either of the two green-ish verdicts); 1 RED; 2
  UNKNOWN, usage error, a bad tag, or an incomplete terminal state; 130 on INT;
  143 on TERM or HUP. **A band size of zero is not green** -- `ALL GREEN`
  requires `BAND_N > 0`.
* **Order** all potentially mutating regeneration runs first and alone in a
  disposable exact-SHA worktree; every later reader runs only after that tree is
  re-proved.
* **Knobs, all environment** `BD_VERIFY_CUT_ARTIFACT_DIR`, `BD_VERIFY_CUT_PREPUSH`,
  `BD_VERIFY_CUT_BAND_REMOTE`, `BD_VERIFY_CUT_PREFLIGHT`, `BD_VERIFY_CUT_VCACHE`,
  `BD_VERIFY_CUT_BASELINE`, `BD_VERIFY_CUT_BAND_TRANSFER`, the five timeouts, and
  `BAND_WORKERS` (default 24). A non-positive integer in any of them is UNKNOWN,
  not a default.
* **Composes** `bd-denom-preflight`, `bd-prepush.sh`, `bd-precut`,
  `bd-band-derive`, `bd-band-remote.sh`, `bd-docs-only` and `bd-verdict-cache`.

## bd-verdict-cache -- a PASS-only cache for the verify gates

* **Location** `/home/mboyle/bd-verdict-cache` (75 lines). Status: live.
* **Answers** "has this exact gate already passed on this exact tree?"
* **Invoke** `bash /home/mboyle/bd-verdict-cache key <gate> <tree-sha> <base-sha> [material...]`,
  then `get <key> --expect-tree <tree-sha>`, `put <key> PASS <tag> <log-path>
  <tree-sha> <base-sha> <gate>`, or `purge [--all]`.
* **Exit codes** `get` exits 0 on a hit; 1 on a miss, a non-PASS record, or a
  tree mismatch; 2 on a usage error. Material given as a file path is digested by
  CONTENT; anything else by its literal text.
* **The safety argument, entire** FAIL and UNKNOWN are never written, so no
  lookup can manufacture a green -- the worst a corrupt or stale entry can do is
  skip a gate that already passed on this exact tree. The key covers the
  candidate tree SHA, the merge-base SHA, the gate name, and a digest of the
  gate's own scripts and anything that changes the EXPERIMENT, so a rerun after a
  rebase, a tool edit or a worker-count change MUST miss.
* **What the key does NOT cover** an ad-hoc change to the venv or an installed
  system tool. Edits to requirements files are tree-covered; a hand-run
  `pip install` is not. **Purge the cache after one.**

## bd-band-remote.sh -- run the band on a capacity box, not on the tree being edited

* **Location** `/home/mboyle/bd-band-remote.sh` (350 lines). Status: live.
* **Answers** "how do I run a 600-file band without the tree changing underneath
  it?" A6 forbids running capture on a host whose tree is being edited; this
  makes it impossible rather than merely forbidden by running at a FROZEN SHA on
  another machine.
* **Invoke** `bash /home/mboyle/bd-band-remote.sh <40-hex-sha> tests/test_a.py tests/test_b.py`.
  `BD_REMOTE_MODE=precut` or `prepush` judges the whole tree and takes no
  selectors.
* **Exit codes** pytest's own rc when it ran; 2 for band mode with no tests
  given; **64 = REMOTE-UNAVAILABLE** -- an invalid SHA, a SHA that is not a local
  commit, no capacity host in the roles file, or no free slot. It is opt-in and
  fail-soft by design: the caller then runs locally exactly as before, because a
  verification lane that can refuse a good cut is worse than a slow one.
* **The mechanism** capacity boxes clone from a LOCAL bare repo on that host, not
  from GitHub, so `git fetch origin` there can exit 0 and still leave the box
  many commits behind -- the same shape A6 records as "A FETCH EXITING 0 IS NOT
  DELIVERY". This pushes the exact object over SSH first. The band runs in its
  own per-SHA worktree and never touches the host's own checkout, so a host that
  serves BD can lend cores without lending its tree.
* **Population** hosts with role `capacity` or `runner` in
  `/home/mboyle/.config/bd/roles`. `deploy` hosts are excluded: they are
  user-facing.

## The push-and-merge step -- name withheld, see finding 5

**This tool cannot be named in this document.** Its filename shares a stem with
one of the twelve tool names a tree-wide gate has physically retired, and that
gate scans the whole tracked Markdown corpus for those tokens. Naming it here
turns a green tree red. Finding 5 below gives the one-line command that derives
the name; it is the harness's push-and-merge script, 425 lines, and it sits
between a frozen candidate and `bd-land`.

* **Location** the operator harness, `/home/mboyle/`. Status: live, 27 inbound
  references -- one of the most-called scripts in the lane.
* **Invoke** `bash <that script> <branch> <title> <pr-body-file>`.
* **Exit codes** 1 no PR body, or the integrator repo is not reachable; 2 no such
  branch; 3 a phase failure -- no open PR, the detached push copy could not be
  built or could not reach the candidate SHA, or its origin is not the expected
  upstream; 0 on a completed ship. The gh required-check filter it writes exits
  125 when it cannot capture evidence.
* **The two defects it used to have** an unbounded `until <checks done>` wait
  that never ends when a check is never created (now bounded at 40 polls of 45s);
  and a ZERO DENOMINATOR read as green, where the first poll after a push returns
  an empty rollup -- as do `gh` errors -- so the script could merge unattended
  having observed NO CHECKS AT ALL. Green now requires every check NAME derived
  from the frozen candidate's own `.github/workflows/ci.yml` to be present and
  passing. An empty or incomplete board is UNKNOWN, never permission.
* **It resolves the candidate from the BRANCH argument, not from HEAD**, so
  shipping cut N while cut N+1 is checked out needs no `git checkout`.
* **Test seam** `BD_SHIP_SOURCE_ONLY=1` lets a focused test source the real gate
  functions without ever entering push or merge.

## bd-land -- land one cut, then report every sibling without mutating it

* **Location** `/home/mboyle/bd-land` (436 lines). Status: live.
  **Load-bearing and frequently running -- see the section warning above.**
* **Answers** "how do I merge this cut without silently invalidating or
  rewriting the other cuts in flight?"
* **Invoke** `bash /home/mboyle/bd-land /home/mboyle/bd-cuts/cut/1479-the-toolchain-has-an-index --pr 737`;
  add `--dry-run` to analyse and merge nothing.
* **Exit codes** 0 landed, or a completed `--dry-run` which prints
  `DRY RUN -- nothing merged`; 2 REFUSED, for every precondition -- no such
  worktree, a detached HEAD with no branch to land, an unknown argument, or
  another `bd-land` already holding `/tmp/bd-land.lock` under `flock -n`.
* **What it requires before merging** a local `ALL GREEN -- shippable` or
  `SHIPPABLE WITH INHERITED FAILURES` verdict for **this exact SHA** -- not for
  the branch -- exact-head CI success read from NAMED fields, and a clean tree.
* **Containment is proven by BLOB EQUALITY per changed path, deletions
  included**, and it prints the count it checked. It once printed
  `containment LANDED` over ZERO files because the missing-file counter stayed at
  zero over an empty denominator, and a deletion-only cut checked zero paths and
  still printed LANDED. Both were found by audit.
* **Siblings are observed, pinned and reported -- never rewritten.** Automatic
  sibling rebases produced conflicted worktrees left mid-rebase, detached
  unreferenced commits, silently replaced release-trio blobs and colliding
  version assignments. It pins each sibling head before analysis, performs a
  read-only virtual merge, proves the sibling's branch, ref and authored blobs
  are unchanged afterward, and names the paths needing the integrator's manual
  merge. A landed cut may invalidate a sibling's evidence; it must not rewrite
  that sibling.
* **Why the problem exists at all** a cut's version and base are chosen when the
  cut is created and only validated when it merges, so every merge is a silent
  invalidation of every sibling. Three cuts in flight, all green, and merging the
  middle one refused the other two with "origin/main moved during verification"
  -- twenty-five minutes of verified evidence discarded by a four-second merge,
  twice.

## bd-rebase -- resolve the conflicts that are not judgement calls

* **Location** `/home/mboyle/bd-rebase` (133 lines). Status: live.
* **Invoke** `bash /home/mboyle/bd-rebase <row> [row...]` or `bash /home/mboyle/bd-rebase --all`.
* **Exit codes** 2 with no arguments; otherwise the rebase result.
* **What it resolves automatically** three GENERATED files -- `PIN_INDEX.json`,
  `project-knowledge/STATIC_KB_MANIFEST.json` and
  `tools/decomp/import_graph_baseline.json` -- because `bd-regen-order` rewrites
  them from source during integrate, so keeping a side is not a merge decision at
  all. Two more, the CI workflow and a gate declaration block, are append-only
  registries union-resolved elsewhere.
* **What it will NOT do** resolve a conflict in source, tests or the register by
  picking a side. A row's own content is the thing under review and a tool that
  guessed there would silently drop work; five rows lost their register row to a
  rebuild once already.
* **Overrides, and why they exist** `BD_REBASE_REPO`, `BD_REBASE_ARTIFACTS`,
  `BD_REBASE_WT_ROOT` and `BD_REBASE_ALL` exist ONLY so the harness tests can run
  against a scratch repo. `bd-rebase-all.sh` REWRITES worktrees, and a test
  falling through to the real one would rebase every live tree.

# D. Containment and evidence

## bd-shipped -- has THIS candidate's work actually reached main?

* **Location** `/home/mboyle/bd-shipped` (167 lines). Status: **new**, added
  2026-09-02.
* **Answers** the question A7 calls a denominator choice. The three available
  containment tests are not equivalent, and the weak ones fail in one direction
  only: **SHA ancestry decided ZERO of 24 tagged candidates**, because every cut
  is rebased before it lands, so the candidate SHA is never an ancestor;
  **patch-id reported three fully-landed tags as unmerged**, because resolving a
  release-trio collision changes the patch; and **comparing THIS BLOB for every
  file the candidate touched** is the only test that answered correctly.
* **Why it exists** the absence of exactly this instrument let four register rows
  be reported merged when they were only tagged, and a fifth release be reported
  shipped when its version number never reached main. `bd-land` already carried
  this logic for the cut it is landing; nothing carried it for an ARBITRARY ref,
  which is what closing a register row needs.
* **Invoke** `bash /home/mboyle/bd-shipped v3.66.1478 --authored`. The candidate
  ref may be a tag, a branch, a SHA or a worktree path. Also `--base <ref>` to
  set the merge base explicitly, `--against <ref>` to change what "shipped" means
  (default `origin/main`), `-q`, and `-h`.
* **`--authored`, and why you usually want it** a landed cut's release trio and
  generated artifacts are legitimately superseded by the NEXT release, so a
  whole-diff comparison reports a fully-shipped fix as NOT SHIPPED once one more
  version lands. `--authored` applies the four-disposition split -- TRIO, DERIVED,
  DECLARED, AUTHORED -- and answers about the authored files alone. **Every
  excluded path is PRINTED**; a silent exclusion would be the fail-open the tool
  exists to refuse.
* **Exit codes** 0 SHIPPED; 3 NOT SHIPPED; 4 UNKNOWN (unmeasurable); 2 usage.
  UNKNOWN is a failing third state and is never printed as OK.
* **When you report a containment answer, say which test you used.**

## bd-band-transfer-key -- may a band verdict transfer across a rebase?

* **Location** `toolchain/bin/bd-band-transfer-key` (451 lines). Status: live.
* **Answers** "this candidate was rebased; is its local band still evidence about
  the same experiment?"
* **Invoke** `venv/bin/python toolchain/bin/bd-band-transfer-key key --repo "$PWD" --base BASE --head HEAD [--json]`
  or `... compare --repo "$PWD" --old-base A --old-head B --new-base C --new-head D [--json]`.
* **Outputs** a fail-closed CONTENT identity for the candidate's local test band.
  It is deliberately NOT a tree identity: callers pass the digest to
  `bd-verdict-cache` as the tree argument, which preserves that cache's PASS-only
  `get --expect-tree` re-proof while letting an equivalent local band hit.
* **The premise it enforces** the identity survives an ordinary release rebase
  only when all four path dispositions remain provable -- release trio,
  generated, reviewed declaration, and authored. A differing authored path set,
  or a differing authored blob, raises `TransferRefused` naming the exact paths.
* **Reused by** `bd-docs-only`, for the byte-exact trio proof, rather than
  reimplemented there.

## bd-worker-terminal -- is that worker actually finished?

* **Location** `/home/mboyle/bd-worker-terminal` (112 lines). Status: live.
* **Answers** "may I harvest this worktree?" **A worker that commits looks
  EXACTLY like a worker that finished.** One cut was imported into a release
  batch while its agent was still running; it later AMENDED, adding a twelve-line
  branch its own self-audit had found. The batch was frozen, verified for nine
  minutes and killed. Shipping it would have released an incomplete fix under a
  changelog line claiming the row was closed.
* **Invoke** `bash /home/mboyle/bd-worker-terminal /home/mboyle/bd-codex-wt/row-1479 /path/to/dispatch.log`.
  The dispatch log is optional but is the second half of the evidence.
* **Exit codes** 0 terminal, and it prints the HEAD and tree to pin; 3 NOT
  TERMINAL -- a live process holds the worktree, the tree is dirty, or it is
  mid-rebase; 4 UNKNOWN -- not a git worktree, ancestry unwalkable, zero
  processes inspected, an unreadable dispatch log, or a dispatch log recording no
  `rc=` lines at all.
* **It answers from process state and the dispatch record, never from the
  presence of a commit.**
* **Two parsing traps it encodes** `ppid` is field 4 of `/proc/<pid>/stat`, but
  field 2 is `comm` IN PARENTHESES and `comm` may itself contain spaces and
  parens, so a naive `awk '{print $4}'` returns the STATE letter for any process
  whose name has a space -- which is how the first version walked zero ancestors
  and reported TERMINAL over a live worker. Parse after the LAST `)`. And it
  excludes the whole PROCESS GROUP rather than only the ancestor chain, because a
  pipeline's members are SIBLINGS of the calling shell and each carries the
  worktree path in its own argv: `bd-worker-terminal $W | grep ...` once reported
  its own `grep` and `cat` as processes still holding the tree.

## bd-row-audit.py -- does this worktree contain the work its row claims?

* **Location** `/home/mboyle/bd-row-audit.py` (332 lines). Status: live.
* **Answers** "did the worker implement its own row, or somebody else's?" An
  audit of nine live worktrees found FOUR that did not implement their own row --
  one carried 45 KB of login code under a row about a download menu, two carried
  ten insertions of backlog and gate-count edits and no implementation at all,
  and one was missing its core deliverable entirely.
* **Invoke** `venv/bin/python /home/mboyle/bd-row-audit.py <worktree> [...]`.
* **Exit code** the worst per-check result across every audited row.
* **Why it is mechanical** two of those empty rows were worse than empty: each
  RAISED the expected declared-gate count by one for a gate file that does not
  exist, and each added six duplicate module-level assignments of that constant,
  so Python took the last one and the tree claimed 177 of 178 declared gates
  against main's 228. A gate that does not exist would be counted -- the exact
  inverse of "a gate CI does not run does not exist". All of that falls to
  mechanical checks. "Does this diff implement this subject?" is the one
  genuinely model-shaped question and is deliberately NOT attempted here.

# E. Turning a worker's row into a cut

## bd-qa-row.sh -- integrator QA before anything touches main

* **Location** `/home/mboyle/bd-qa-row.sh` (152 lines). Status: live.
* **Answers** "does this returned worker row hold up when I re-run it myself?"
  This is A3 step 9 made mechanical: a worker's claim that its battery passed is
  data, not evidence, until it is re-run here.
* **Invoke** `bash /home/mboyle/bd-qa-row.sh 1479`. It operates on
  `/home/mboyle/bd-codex-wt/row<N>` and writes
  `.../codex-cuts/row<N>.qa.log`, whose `QA_RC=0` line is what the integrate step
  reads.
* **Exit codes** 1 no such worktree; 0 when there is nothing to judge (a doc or
  backlog-only row) or when the row's own tests pass; otherwise pytest's result.
* **Two harness traps it encodes.** New files must be INDEX-VISIBLE before the
  gates run: agents never commit, so a brand-new test file is untracked in its
  worktree and the tracked-shard gate correctly refuses a shard entry `git
  ls-files` cannot see. That is a true statement about the worktree and a false
  one about the cut, so it runs `git add -N` to make the file index-visible
  without staging content -- the exact state the gate will judge. Without it the
  QA lane fails every row that adds a test: the harness manufacturing its own
  failure. And a row that adds a test file is also run through the declaration
  gate here, because one row declared a repo-wide gate in neither the declared
  set nor any CI shard, passed QA, and was caught eleven minutes later inside
  verify by a check that costs about seven seconds here.

## bd-integrate-row.sh -- turn a QA'd worktree into a proper cut

* **Location** `/home/mboyle/bd-integrate-row.sh` (350 lines, the
  most-referenced script in the harness). Status: live.
* **Invoke** `bash /home/mboyle/bd-integrate-row.sh <row> <version> <slug> <changelog-title>`.
  The row argument may name SEVERAL rows landing as one cut; the FIRST row names
  the artifacts and the PR body.
* **What it does** a fresh worktree off current main, the worker's diff applied,
  **the release trio written by the integrator** (workers are forbidden to touch
  it), the register row closed with the header recomputed by the gate's own
  function, regeneration LAST, then verify. **It does not ship** -- shipping is a
  separate, serialized step.
* **Exit codes** 2 a named row's QA is not green; 3 an unresolvable merge base,
  or a row that changed nothing against its base; 4 an empty patch; 5 a branch
  that already exists on the remote, or a failed worktree creation; 6 a register
  merge failure; **13 the version is ALREADY CLAIMED**, locally or on the remote,
  or the remote branch list could not be read so the claim is UNMEASURED.
* **The version is derived from CURRENT MAIN, not from the queue plan.** Cuts
  fail and retry out of order, so a retried cut could be handed a version lower
  than main already carries; prepending a lower version makes the changelog
  descend and `bd-precut` refuses. Asking main is the only number true at the
  moment the cut is built.

**A NOTE ON ROW IDS UNDER CONCURRENCY.** Parallel workers file the same next-free
id. `bd-next-row` answers correctly at the moment it is asked and gives no lock;
renumber a colliding row rather than overwriting one.

# F. The canonical register

The register is `project-knowledge/IMPROVEMENT_BACKLOG.md`, and A1 makes it the
only place current product work lives. Its header carries `rows=`, `open=` and an
`ids-sha256=` over the id list, so an edit that does not recompute the header is
detected. **Use these tools rather than editing the table by hand**; each takes
the same file lock, recomputes the same header, and writes atomically.

## bd-register-append -- add validated OPEN rows

* **Location** `toolchain/bin/bd-register-append` (256 lines). Status: live.
* **Invoke** `venv/bin/python toolchain/bin/bd-register-append --repo "$PWD" --request request.json`.
* **Inputs** a JSON request with exactly the keys `schema`, `expected_ids_sha256`
  and `rows`. The expected digest is the guard: if the register moved under you,
  the append is refused rather than applied to a register you did not read.
* **Exit codes** 0 appended; 2 and 3 for distinct refusal classes -- read the
  message, do not retry blind.
* **Mechanism** `fcntl` file locking, a temp file, and a rename; the finalization
  errors are named individually rather than collapsed.

## bd-register-close -- stamp a row with its release version

* **Location** `toolchain/bin/bd-register-close` (217 lines). Status: live.
* **Invoke** `venv/bin/python toolchain/bin/bd-register-close --repo "$PWD" --row 1479 --version 3.66.1479`.
* **Exit codes** 0 closed; 3 refused.
* **Position in the lane** it is the repository-owned close step for
  `bd-integrate-row.sh`, and it runs AFTER the release trio is written and AFTER
  worker rows are merged. Closing earlier stamps a version the cut may not get.

## bd-register-amend -- change one row under a guard

* **Location** `toolchain/bin/bd-register-amend` (299 lines). Status: live.
* **Invoke** `venv/bin/python toolchain/bin/bd-register-amend --repo "$PWD" --request request.json`.
* **Inputs** a JSON request with `schema`, `row`, `expected_status`,
  `expected_row_sha256`, `find` and `replace`. Both expectations are checked
  before the write, so an amendment cannot land on a row that changed since you
  read it.
* **Exit codes** 0 amended; 2 and 3 for distinct refusals.

## build_current_overlay.py -- derive the register's true header

* **Location** `project-knowledge/build_current_overlay.py` (162 lines). Status:
  live.
* **Answers** "what do `rows=`, `open=` and `ids-sha256=` actually evaluate to
  for this file?" `derive_backlog(text)` is the one function that answers it and
  it is what the gates use.
* **Invoke** `venv/bin/python project-knowledge/build_current_overlay.py --repo "$PWD" --out OUT.json`;
  `--check` compares rather than writes.
* **What `derive_backlog` counts** every `| id | status |` row; OPEN is `OPEN` or
  a status STARTING with `OPEN `; the digest is sha256 over the comma-joined id
  list in file order; and completed rows are those whose status starts with
  `CLOSED`, `MOOT` or `FIXED`. Duplicate ids raise -- the register is a
  population or it is nothing.
* **Refusals** a dirty repository is `UNKNOWN`, and every `git` observation it
  takes runs with `GIT_OPTIONAL_LOCKS=0` under a 15s timeout so a concurrent
  writer cannot make it hang or mutate an index.

**A REGISTER CAVEAT WORTH KNOWING.** The dangling-reference gate resolves a row
citation against **main's** row population, so prose in a cut cannot cite a row
that has not merged yet. Cite it after it lands, or the gate calls a true
reference dangling.

# G. Fleet and deployment

## scripts/deploy.sh -- the git deploy path for one host

* **Location** `scripts/deploy.sh` (1035 lines). Status: live. **A6 forbids
  hand-recreating this sequence.**
* **Invoke** `bash scripts/deploy.sh --expect-commit <40-hex>`; `-h`/`--help`
  prints usage and exits 0 without touching anything.
* **Exit codes** 0 deployed-and-verified, or already-current-and-verified; 1 a
  step or a verification FAILED and the state is NOT known good; 2 refusal or a
  failed precondition, with NOTHING mutated. Everything checkable is checked
  before the first side effect, so a typo costs exit 2 and no side effect.
* **Why it is not three git commands.** A deploy moves files; it does not make
  the running system match them. `requirements.txt` can gain an entry and nothing
  on the deploy path runs pip. `frontend/dist/` holds zero tracked files and is
  gitignored, so git never delivers it and a missing bundle is a silent 503.
  `__pycache__` is not cleared by `git reset --hard`. A gitignored build-time
  report cannot be evicted by `git clean -fd` -- that needs `-x`. The graph
  content pin lives outside the repo under `/var/lib/`. And the service is not
  restarted, so the process keeps running the old tree.
* **`--expect-commit` is not optional in practice.** Several hosts clone from a
  per-host bare MIRROR rather than the official origin, and this script refuses a
  non-official origin outright unless told which object to land. Without it those
  hosts fail rc=2 on every pass. A fetch exiting 0 is not delivery: hosts have sat
  eighteen to twenty-nine releases behind while every fetch exited 0. **Prove the
  intended commit is PRESENT on the host before deploying it**, and read
  `docs/repo/FLEET_TOPOLOGY.md` for which hosts fetch from where.
* **The inode boundary.** After the script's `git reset --hard`, the running
  shell continues executing the PRE-RESET script inode while the path names the
  new file. A change to a later deploy step therefore takes effect on the
  FOLLOWING invocation unless an explicit handoff is designed.
* **A failed deploy is not a no-op.** It can leave the service down after the
  stop and cache steps. Preserve the failing step, inspect system state, and
  remediate before claiming health. Verify with `/api/health`; there is no
  general `/api/version`.

## bd-fleet-deploy.sh -- deploy origin/main to every non-integrator host

* **Location** `/home/mboyle/bd-fleet-deploy.sh` (331 lines). Status: live, but
  see the HOLD below.
* **Composition, not re-implementation** it only invokes `scripts/deploy.sh` on
  each host and reads its result. Every deploy decision stays inside that script.
* **Invoke** `bash /home/mboyle/bd-fleet-deploy.sh`. Overrides:
  `BD_FLEET_HOSTS`, `BD_FLEET_ARTIFACTS` (so a test run cannot append into the
  operator's real deploy log directory -- per-second stamps once let a test
  interleave into the same file as a concurrent real pass),
  `BD_DEPLOY_CONCURRENCY`, `BD_FLEET_HOLD`.
* **Exit codes** 0 the pass completed (individual host failures are recorded, not
  fatal); 3 a refusal for an UNMEASURABLE precondition -- origin/main
  unresolvable, no version readable, no health URL derivable from `deploy.sh`,
  zero targets parsed, an unreadable roles file, zero roles parsed, or a roles
  file with more non-comment lines than parsed host rows (a malformed row would
  shrink the population silently); **4 the operator HOLD file exists**, at
  `/home/mboyle/.config/bd/DEPLOY_HOLD`.
* **test5 is excluded STRUCTURALLY**, by a `local` marker in the hosts file
  rather than a hardcoded IP: the integrator's tree must never be deployed onto
  while it is the writer.
* **Continue-on-failure by operator ruling** -- one bad host must not strand five.
  A failed host gets a self-heal attempt (restart the unit, re-check health, one
  deploy retry) and an incident record. Health is re-read after every path, and
  an unreachable host is UNKNOWN, never OK.

## scripts/provision_test_host.sh -- fresh Ubuntu to a green capture

* **Location** `scripts/provision_test_host.sh` (777 lines). Status: live.
* **Invoke** `./scripts/provision_test_host.sh` (the repo is found from the
  script) or `./scripts/provision_test_host.sh /path/to/repo`.
* **Why it exists** provisioning was split across two scripts that never met:
  the repo-half installer deliberately never touches the root tier and merely
  PRINTS advice when a system package is missing, and the root-tier script is the
  cloud session-start script that the operator's boxes never run. So nobody ran
  the root tier, nobody started a display, and nobody regenerated the gui-parity
  inventory -- the last of which is why a capture failed on a parity item-set
  mismatch.
* **Design** `set -uo pipefail`, deliberately NOT `set -e`: a failed step must be
  RECORDED and the run must continue, because aborting at the first failure
  produces a host in an unknown state and a console that explains nothing. The
  verdict comes at the end.
* **For a genuinely new host** follow `docs/repo/FRESH_HOST_BRINGUP.md`; do not
  hand-recreate the sequence.

# H. Not killing yourself, your shell, or a live lane

## pkill -- a SHIM that refuses the self-match

* **Location** `/home/mboyle/.local/bin/pkill` (61 lines), shadowing
  `/usr/bin/pkill` on PATH. Status: live.
* **Answers** nothing new -- it exists purely to REFUSE. `pkill -f <pattern>`
  matches every process whose full command line contains the pattern, **including
  the shell running the pkill**, because the pattern sits in that shell's own
  argv. Twice in one session it killed the calling shell (exit 144) and took a
  live background task with it. It is the same family as the `[b]racket` trick
  failing when the pattern is DATA rather than argv.
* **Behaviour** anything that is not `-f` execs straight through to the real
  binary. A `-f` whose pattern does not match this shim's own ancestry execs
  through too. **Only the fatal case is refused, with exit 9.**
* **Override** `BD_PKILL_ALLOW_SELF=1` for a genuinely intended self-match.
* **The lesson it encodes** knowledge did not stop this: the hazard was already
  written down, a memory existed, and a purpose-built tool existed. **Only a
  nonzero exit has ever intercepted this class.**

## bd-kill-mine.sh -- kill by argv pattern and PROVE what died

* **Location** `/home/mboyle/bd-kill-mine.sh` (268 lines). Status: live.
* **Invoke** `bash /home/mboyle/bd-kill-mine.sh 'bd-verify-cut.sh' TERM`.
* **Exit codes** 0 every matched pid is confirmed gone, or nothing matched at
  all; 1 usage error -- no pattern, or a signal name the shell rejects; 2 at
  least one target is STILL ALIVE after the full escalation; 3 UNKNOWN, at least
  one pid could not be resolved to died-or-alive (a zombie, an uninterruptible
  D-state sleeper, or a match it was not permitted to signal). **Exit 2 and exit
  3 are both refusals; neither means the competing writer has stopped.**
* **Escalation** the given signal, then a bounded wait polled every 100ms with
  early exit, then SIGKILL to whatever is left, then a second bounded wait.
  `BD_KILL_WAIT` and `BD_KILL_WAIT_KILL` tune the budgets; `BD_KILL_NO_ESCALATE=1`
  stops before SIGKILL and survivors then exit 2.
* **Why it re-probes** it once printed `1 process(es) signalled` and exited 0
  while the target was still there in state S. **The counter counted SIGNALS
  SENT; nothing ever re-read `/proc`.** It now prints the matching lines, signals,
  waits, escalates, waits again, and re-probes every named PID, reading the
  verdict from `/proc/<pid>/stat` and never from `cmdline` -- a zombie's
  `cmdline` is EMPTY, so "the pattern no longer matches" would score a zombie as
  dead.

**SAFETY -- A KILL PATTERN MATCHES BRIEF TEXT.** An agent's brief is one argv
argument, so a bare tool name in a kill pattern matches every agent whose brief
mentions that tool. Anchor on the invocation, and read the printed matching lines
before signalling anything.

## bd-running -- is X running?

* **Location** `/home/mboyle/bd-running` (59 lines). Status: live.
* **Invoke** `bash /home/mboyle/bd-running bd-verify-cut.sh` or with `--quiet`.
* **Exit codes** 0 at least one live process; 1 none.
* **Both failure modes it closes** an anchored pattern like
  `^bash /home/mboyle/bd-verify-cut.sh` matched NOTHING while four runs were in
  flight, because their real argv was the relative `bash bd-verify-cut.sh` -- so
  a fifth was started and all five fought over one worktree. And a bare substring
  matches the shell that WROTE the script and every ancestor of the search
  itself, so a count comes back uniformly 1 on every host and means nothing.
* **How** it reduces a path argument to its BASENAME always -- an earlier version
  reduced it only when the path existed on disk, so a query about a moved or
  renamed script answered a confident zero -- excludes self and the WHOLE ancestor
  chain by PID, and **always prints the matching lines. A count you have not read
  is a claim.**

## bd-ps.sh -- find live processes by argv

* **Location** `/home/mboyle/bd-ps.sh` (18 lines). Status: live; prefer
  `bd-running` when the question is a verdict.
* **Invoke** `bash /home/mboyle/bd-ps.sh <argv-substring>`; prints `pid  argv`.
* **Limits, stated plainly** it excludes only SELF and PARENT, not the whole
  ancestor chain, which is why `bd-running` exists. It reads `cmdline` through
  `cat` inside the pipeline on purpose: `tr ... < /proc/$p/cmdline 2>/dev/null`
  does NOT silence the race, because the failure comes from the SHELL opening the
  redirect and `tr`'s own stderr redirection never applies.

## bd-edit.py -- atomically replace a running script

* **Location** `/home/mboyle/bd-edit.py` (41 lines). Status: live.
* **Answers** "how do I change a script that something is executing right now?"
* **Invoke** `printf '%s' "$NEW" | venv/bin/python /home/mboyle/bd-edit.py /home/mboyle/bd-land`
  -- new content on stdin, target path as the only argument.
* **Outputs** `<name>: replaced atomically (<n> bytes)`.
* **Exit codes** 0 replaced; nonzero with a message on either refusal.
* **The mechanism** `write_text()` truncates and rewrites THE SAME INODE, and a
  running bash keeps reading that inode at its saved byte offset -- so an
  in-place rewrite makes it resume in the middle of different text. `rename()`
  swaps the directory entry and leaves the old inode intact for the running
  process, so the edit takes effect on the NEXT invocation and never corrupts the
  current one.
* **Two refusals, both earned.** Empty or whitespace-only stdin is REFUSED: two
  agents chained a patch builder into this tool, each builder aborted on a bad
  anchor and printed nothing, and this tool faithfully replaced a live script
  with 0 bytes -- twice in one day, both recovered only from backups the contract
  demands. `BD_EDIT_ALLOW_EMPTY=1` exists for the one caller that means it. And a
  target that does not exist is refused: this tool REPLACES a script, it does not
  create one.
* **Use `&&`, not `;`, between the producer and this tool.** The refusal message
  says so because that is the actual defect.

# I. Operator instruments

These drive real hosts, real browsers or real credentials. A6 forbids a
repository test from doing any of that, which is precisely why they live in the
operator harness and are **never gates**.

## bd-shoot.py -- what does the BROWSER actually see?

* **Location** `/home/mboyle/bd-shoot.py` (85 lines). Status: live. Harness, never
  a gate: it drives a real browser against an authenticated site.
* **Answers** A7's rule -- **a rendered page is evidence; a candidate list is a
  claim about it.** BD once chose a download link, saved 5,102,802,950 bytes and
  filed it under the requested scene's title, and the file was a different scene.
  The history row, the library title and the candidate ranking all looked right.
  One screenshot answered it: below the scene's own six download tiers sat a
  related-videos grid of about 25 scenes, each card exposing its own direct media
  links -- 159 media links on one page, six of them the requested work. Within the
  hour the same instrument CLEARED three rows a filename audit had called
  mis-filed, because the page showed exactly the performer and studio the
  filenames encode.
* **Invoke** `venv/bin/python /home/mboyle/bd-shoot.py <site_id> <url> /tmp/shot.png`.
  It reads `~/BulkDownloader/cookies/<site_id>.json`, loads the cookies into a
  cloaked page, prints the page title and every media link it can see, and writes
  a full-page screenshot.
* **Read-only** it loads cookies; it never queues, downloads, or writes to the
  app's state.
* **FINDING -- it has the executable bit but NO shebang line.** Byte 0 of the file
  is `"`, the start of its docstring. Running it as `./bd-shoot.py` gets ENOEXEC
  and falls back to `/bin/sh`, which will fail on the docstring; its own usage
  line reads `bd-shoot.py <site_id> <url> <out.png>` and implies otherwise. It
  also imports `bulk_downloader.cloak` and Playwright, so it must run under the
  repository venv interpreter regardless.
* **Capture BEFORE theorising**, whenever a result looks wrong, or looks right for
  a reason you cannot name.

## bd-vault-unlock.sh -- re-arm a host's secrets vault after a restart

* **Location** `/home/mboyle/bd-vault-unlock.sh` (177 lines). Status: live.
* **Answers** "why does every configured login report a missing password after a
  deploy?" The master-password backend keeps the derived key in process memory
  only, so **every service restart, including every deploy, starts LOCKED** -- and
  the locked-vault message even advises re-entering credentials, which would
  overwrite intact ones.
* **Invoke** `bash /home/mboyle/bd-vault-unlock.sh <host|local> [...]`; with no
  arguments it targets the two site hosts.
* **Exit codes** 0 every named host ended UNLOCKED; 1 at least one did not; the
  remote payload itself returns 2 when the password directory is absent or does
  not hold exactly one entry.
* **The credential convention** the password is the **FILENAME**, not the file's
  contents, in the directory `BD_VAULT_PWDIR` names (default
  `$HOME/.bd-import/vault-master`). It is read on the REMOTE host, posted only to
  that host's own loopback API, never echoed and never logged.
* **It reports the STATE, not the call's exit code** -- a 200 that left the vault
  locked is a failure, and that distinction is the whole point.
* **The A7 diagnostic lesson, and its current status.** This tool is A7's example
  of a diagnostic that collapses distinct failures: a four-request flow inside one
  `except Exception` printing "pairing fallback failed" made a 401 incorrect
  password indistinguishable from a broken pairing endpoint, and those two
  diagnoses lead to opposite actions. **The current source has the remedy**: a
  `STEP` variable is advanced across `GET /api/pair`, `POST /api/pair/redeem` and
  the unlock, and the handler prints `<STEP> FAILED: HTTP <code> -- <server's own
  error text>`.

## bd-harness-test -- the harness's own test lane

* **Location** `/home/mboyle/bd-harness-test` (46 lines). Status: live.
* **Answers** "did I just break the harness?" The harness scripts are not covered
  by the repository suite, so this is their only lane.
* **Invoke** `bash /home/mboyle/bd-harness-test` for the fast lane, or
  `bash /home/mboyle/bd-harness-test --full` before archiving, freezing or handing
  off. `-h` prints the header and exits 0.
* **Measured 2026-09-01** fast is 94 of 98 in about 54s; full is 98 of 98 in about
  371s. Four tests carry 317s of that: they build a real git worktree at
  origin/main and run `bd-denom-preflight` against it three times.
* **Exit codes** 2 when the harness test directory or the interpreter is absent;
  otherwise pytest's result.
* **The split is a SAFETY boundary, not just a speed one.** Those four tests are
  the only users of the scratch-worktree fixture, so the fast lane never runs
  `git worktree add` against the live integrator repo at all.
* **DO NOT PARALLELISE IT.** Measured: `-n 12 --dist loadfile` is 372.68s against
  370.73s serial -- worse than nothing, because the four expensive tests share a
  module-scoped fixture and serialise on it anyway. `--dist load` would fire
  concurrent `git worktree add` against the live repo, which is worse than slow.
* **Overrides** `BD_HARNESS_DIR` (default `/home/mboyle/bd-persist/harness`) and
  `BD_HARNESS_PYTHON` (default the repository venv).

---

# Shell hazards none of these tools can protect you from

Two of them are measured here, in a scratch directory, because both cost real
work before they were written down.

## Never pipe a MUTATING command into `head`

`head` exits after N lines and closes the pipe; the writer's next `write()` gets
SIGPIPE and dies where it stands. A tool that mutates then restores never reaches
its restore step, and the pipeline reports 141.

Measured 2026-09-02, with a stand-in that writes a marker, prints 5000 lines,
then restores:

```
$ bash mut.sh state | head -3
line 1
line 2
line 3
pipeline rc=141
state after: MUTATED
```

The restore never ran. This applies to `bd-mutate`, to anything driving it, to
the register writers, to `bd-regen-order`, and to any harness script that
prints progress while holding a partially applied change. Redirect to a file
and read a slice of the file -- A8's "CAPTURE WHOLE TO DISK, READ A SLICE" is
the same rule for the same reason.

## `git add` with a pathspec that no longer exists stages NOTHING

Not "stages the rest"; nothing. Measured 2026-09-02:

```
$ git add a.txt missing.txt b.txt
fatal: pathspec 'missing.txt' did not match any files
add rc=128
$ git diff --cached --name-only     # empty
$ git diff --name-only
a.txt
b.txt
```

Both real files remained unstaged. A4 already forbids `git add -A` and broad
globs while another writer can touch the tree, so exact-path staging is the rule
-- and the corollary is that **you must check the exit status of `git add`**. A
`git add ... ; git commit` chain after a renamed or regenerated-away path commits
the previous state silently. Use `&&`, and re-read `git status`, the staged
names and the staged diff immediately before every commit.

---

# The other five hundred and something

This index covers the lane. The rest of the denominator is enumerated
mechanically, never from memory, and never from this document.

**Repository populations** (run from a work-tree root):

```bash
git ls-files toolchain/bin                 # operational tools, extensionless
git ls-files 'tools/*.py'                  # build and analysis code
ls scripts/                                # install / deploy / service
git ls-files 'project-knowledge/*'         # generated and current knowledge
```

**One line per tool, with its own header line** -- the same shape as the harness
map, and the fastest way to find whether something already exists:

```bash
for f in $(git ls-files toolchain/bin); do
  printf '%-30s %s\n' "$(basename "$f")" \
    "$(sed -n '2,8p' "$f" | grep -m1 -E '^(#[^!]|""")' \
       | sed -E 's/^(#|""")[[:space:]]*//; s/"""$//' | cut -c1-70)"
done
```

**The operator harness** -- outside the repository, and equally load-bearing:

```bash
find /home/mboyle -maxdepth 1 -name 'bd-*' -type f | sort
for f in /home/mboyle/bd-*; do [ -f "$f" ] || continue
  printf '%-30s %s\n' "$(basename "$f")" \
    "$(sed -n '2,8p' "$f" | grep -m1 -E '^(#[^!]|""")' \
       | sed -E 's/^(#|""")[[:space:]]*//; s/"""$//' | cut -c1-70)"
done
```

A prebuilt map with inbound-reference counts already exists at
`/home/mboyle/bd-persist/workers/1457/HARNESS_MAP.txt` (derived 2026-09-02).
Re-derive rather than trusting its counts; files are added daily.

**Before creating any new tool**, A8 binds: prove that exact name is unused in
BOTH populations, and if it exists, read its existing CALLER to learn the real
interface. Writing new logic under an existing tool's name once changed that
tool's argument contract silently, and every integrate refused until the
original was reconstructed from its caller.

# Unclassified, not dead: the 61 unreferenced harness files

`HARNESS_MAP.txt` records 61 live files under `/home/mboyle/bd-*` that **nothing
else names**. That is a statement about inbound references, not about value. An
unreferenced script is very often a hand-invoked instrument, and the moment it
matters is exactly the moment nobody has time to rewrite it: the fleet
provisioning fan-out, the WAN saturation measurement, the matched-control
experiments for a specific incident, the full-suite-on-a-remote-host runner.
Several are also genuine one-shots whose subject has passed, and a few are
tarballs and logs. **Do not treat this list as a deletion queue.**

The list, exactly as derived:

```
bd-HOLD-PARKED.txt              bd-NAMED-TEST-evidence.log      bd-RESUME-2026-08-14-late.md
bd-actions-watch.sh             bd-arm-batching.sh              bd-arm-risky-batch.sh
bd-board.sh                     bd-capture-driver.log           bd-capture-on-50.sh
bd-checkpoint30.sh              bd-codex-batch.sh               bd-codex-cut.sh.bak-195319
bd-codex-repair.sh              bd-exit-tracer-sitecustomize.py bd-fanout-lane.sh
bd-finish.sh                    bd-freshhost-50.sh              bd-fullsuite-remote.sh
bd-handoff-2026-08-12.tar.gz    bd-handoff-2026-08-12.txt       bd-handoff-2026-08-13.txt
bd-hunt-snapshot                bd-install-overlap.sh           bd-knowledge-20260901b.tar.gz
bd-knowledge-20260901c.tar.gz   bd-lane-experiment.sh           bd-make-patch.sh
bd-netprobe.sh                  bd-next-agent.txt               bd-ollama-50.sh
bd-pipe-lane.sh                 bd-poll5.sh                     bd-provision-fan.sh
bd-publish.sh                   bd-qa-watch.sh                  bd-queue-final.sh
bd-queue-last.sh                bd-queue2.sh                    bd-queue3.sh
bd-remote-suite.sh              bd-repair-brief.py              bd-resolve-stash-conflict.py
bd-resume-codex.sh              bd-resume-now.sh                bd-resume-queue-batch1.sh
bd-revert-scaffold.sh           bd-revert-scan.sh               bd-run-tail.sh
bd-t6-capture-control.sh        bd-t6-capture-main.sh           bd-t6-matched.sh
bd-trio-resume.sh               bd-trio-resume2.sh              bd-wansat.sh
bd-watch-merge.sh               bd-watch-stalls.sh              bd-wedge-FULL-20260814T120613Z.tar.gz
bd-wedge-evidence-20260814T120558Z.tar.gz                       bd-wedge-evidence-20260814T145128Z.tar.gz
bd-width-restore.sh             bd-worker-dashboard.sh
```

Three further files are recorded as RESIDUE -- backup or superseded copies that
are deliberately kept: `bd-fleet-deploy.sh.bak`, `bd-row-chain.sh.preoverlap` and
one more `.preoverlap` copy whose stem finding 5 forbids this document to spell.
Two of those exist because `bd-edit.py`'s empty-stdin fail-open once emptied a
live script, and the backups were the only recovery.

## The bd-row212-* directories

`/home/mboyle` holds about twenty directories matching `bd-row212-*` -- runner,
work, target, preflight, mutation and clone trees, several of them named for a
specific SHA and a shard (`-s06`), plus two randomly suffixed clone/detached
trees. They have the shape of scaffolding left by a single row's experiment
runs.

**This document does not recommend deleting them, and the recommendation is
withheld deliberately.** A stale queue is a deletion list: pending entries for
finished rows once nearly removed two worktrees a live lane still needed, and a
glob censused against a path that did not exist read as "no work" and nearly
destroyed thousands of lines. Anything under `/home/mboyle` matching a worktree
shape may still be registered with git. Before touching any of them, prove
ownership: `git -C /home/mboyle/BulkDownloader worktree list` names every tree
git still tracks, and `/home/mboyle/bd-worktree-archive.sh` is the
operator-approved archive-then-delete path. Archive first; the archive is what
makes the deletion reversible.

# Findings from writing this index

Recorded here rather than acted on, because each is a separate cut.

1. **`bd-shoot.py` has the executable bit and no shebang.** Its first byte is the
   opening quote of its docstring, so `./bd-shoot.py` gets ENOEXEC and falls back
   to `/bin/sh`. Its own usage line implies direct invocation. It must be run as
   `venv/bin/python /home/mboyle/bd-shoot.py ...`, which it must be anyway --
   it imports `bulk_downloader.cloak` and Playwright. Either add the shebang or
   correct the usage line; the executable bit currently promises something the
   file cannot deliver. This finding and finding 5 are the two that deserve
   register rows.
2. **`bd-versync`'s entire docstring is `bd-versync fixed.`** It is a member of
   the `bd-denom-preflight` pinned lane and gates the release trio against the
   pin index, and its own documentation says nothing about its subject, its
   exit contract, or its arguments. Read the source.
3. **`bd-doc-truth` used to advertise checks it had never implemented.** The
   current docstring says so in its own words -- version-claim, tool-count and
   route-count checks were advertised and never written. It is worth reading as
   the canonical example of the shape: a tool's own docstring is a document and
   goes stale like any other, and claiming coverage that does not exist is how a
   narrow detector gets read as a broad one.
4. **`bd-vault-unlock.sh`'s A7 defect is fixed and A7 still reads as present
   tense.** The collapsing `except Exception` that printed "pairing fallback
   failed" has been replaced by a `STEP` variable naming the failing request and
   a handler that carries the server's own error text. The contract's account is
   correct as history; a reader looking for the defect in the source will not
   find it.
5. **A tree-wide gate forbids the tracked Markdown corpus from naming three
   LIVE operator-harness scripts.** `tests/test_v3_66_1172_nested_freshness_and_legacy_retirement.py`
   holds a set of twelve physically retired in-repo tool names and asserts that
   no current tracked Markdown document contains any of them as a whole token.
   Three live harness scripts under `/home/mboyle/` -- plus one `.preoverlap`
   residue copy -- share a stem with one of those retired names, and the token
   regex's lookahead stops at `-` but not at `.`, so `<retired-stem>.sh` matches.
   One of the three is the push-and-merge step above, with 27 inbound references.
   The gate is right about its own subject and wrong about this one: a retired
   `toolchain/bin` tool and a live `/home/mboyle` script are different files in
   different populations, and the token set cannot tell them apart. **This
   document is materially poorer for it** -- the most-called script in the lane is
   documented anonymously. Derive the exact names with:

   ```bash
   venv/bin/python - <<'PY'
   import re, pathlib
   src = pathlib.Path("tests/test_v3_66_1172_nested_freshness_and_legacy_retirement.py").read_text()
   retired = sorted(set(re.findall(r'"([^"]+)"', src.split("RETIRED = {", 1)[1].split("}", 1)[0])))
   tok = re.compile(r"(?<![A-Za-z0-9_-])(?:" + "|".join(map(re.escape, retired)) + r")(?![A-Za-z0-9_-])")
   print([p.name for p in sorted(pathlib.Path("/home/mboyle").glob("bd-*")) if p.is_file() and tok.search(p.name)])
   PY
   ```

   The fix is a scoped one -- have the gate exclude a `<stem>.sh` form, or have it
   judge repo-relative paths rather than bare tokens -- and it is a register row,
   not a docs cut.
6. **`ls toolchain/bin` and `git ls-files toolchain/bin` disagree by one** in the
   integrator's checkout, because `__pycache__` is present and untracked. Neither
   count is wrong; they answer different questions. Say which one you used.

Nothing in the documented set was found deprecated with a missing replacement.
The one file in the harness carrying an explicit retirement note,
`/home/mboyle/bd-autorebase.sh`, is marked NEUTERED in its own header (lines 2-10)
with both the reason -- it hard-reset queued worktrees and dropped their
implementation commits -- and the condition for re-enabling it, which is that
`bd-rebase` be proven to REPLAY a row commit onto main rather than reset to it.
It now does nothing but sleep, and deliberately does not `exec` that sleep,
because replacing its own argv would erase the string its watchdog matches on.
The capability has a live owner in `bd-rebase` above; the retirement is
documented at the retired file, which is the correct place for it.
