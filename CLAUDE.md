# CLAUDE.md — operating contract for BulkDownloader

Read this file fully before editing. It is the sole agent-facing contract for
BulkDownloader. Historical incidents remain in Git and the changelog; this file
contains standing authority, safety boundaries, required commands, and links to
focused owners.

## A1 | Authority and scope

BulkDownloader (BD) is a self-hosted Flask, Playwright, and React/TypeScript
batch downloader operated by Matthew. The authoritative repository is
`/home/mboyle/BulkDownloader`, with official origin `mcboyle/BD`. The deployed
tree and the working tree may be the same directory, especially on `test5`.

This is the sole agent-facing contract. Do not create or revive a second
bootstrap prompt, handoff prompt, session contract, promise ledger, task
register, casebook, or authority. Current product work lives only in
`project-knowledge/IMPROVEMENT_BACKLOG.md`. Git history and `CHANGELOG.md` are
evidence, not current task queues.

Treat every reading as a claim about both a commit and a host. Before relying
on a result, record or verify:

- hostname and machine identity;
- repository path and official origin;
- branch, HEAD commit, tree SHA, parent/base, and ahead/behind state;
- clean tracked/untracked/index state;
- interpreter, environment, and exact command;
- service or fleet boundary touched by the work.

A finding without host, commit, and tree identity is not transferable evidence.
An observation from a stale checkout is about that checkout, even when its
tests pass. Fetch normally, resolve the exact object, and prove ancestry and
containment before comparing results.

Measure volatile facts from the current tree. Do not copy counts from prose.
Use `git ls-files`, parsers, tool output, CI APIs, service probes, or generated
manifests appropriate to the question. A glob is a denominator choice: include
extensionless scripts, frontend source, fixtures, generated inputs, deleted
paths, and other populations when the claim covers them. Verify what a tool
executes, not only what its docstring says.

Documents go stale silently. Re-derive backlog status and source facts before
acting; preserve explicit uncertainty when current evidence cannot decide.

## A2 | Authorization and state

Matthew's terse directives such as `go`, `continue`, `cut`, or a bare artifact
authorize routine work within the already established scope. They do not grant
authority over unrelated repositories, hosts, data, people, costs, credentials,
or destructive targets.

`hold`, `wait`, `pause`, or `stop` means stop immediately at the next safe
point. Read-only inspection may continue only when it cannot affect active
evidence or external state. `resume` restores the previously authorized scope;
it does not silently broaden it.

PASS and FAIL are not exhaustive. UNKNOWN is a failing third state whenever a
required claim cannot be measured. Missing, malformed, truncated, stale,
wrong-SHA, wrong-tree, wrong-host, zero-denominator, digest-mismatched,
unobserved, or transport-failed evidence is UNKNOWN/HOLD, never permission.

Keep task, merge, and deployment authority separate:

- implementation authority permits scoped source/test/doc changes;
- merge requires the reviewed exact head, terminal required tests, exact-head
  CI, current PR metadata, and clean state;
- deployment requires the exact merged tree and the sanctioned deploy path;
- infrastructure, package, fleet, or external-service actions remain bounded
  by the operator's explicit scope and cost/license limits.

If work is intentionally deferred, record it as a machine-visible row in the canonical
backlog with a unique row, status, evidence, acceptance criteria, and dependency.
Prose such as "later" or an unchecked historical checklist is not a deferral.
Do not invent a row for work that is completed, obsolete, or already represented.

When a meaningful choice would change the product or expand authority, present
the evidence and ask. Routine, reversible, in-scope implementation and
verification should continue without confirmation. Never convert ambiguity
into a narrower or easier objective merely to obtain a green result.

Know which host you are changing. Never edit a checkout while an authoritative
capture or formal timing run uses it. Never test against the live service or an
authenticated site unless that exact contact is authorized and isolated.

## A3 | Change lifecycle

Carry one coherent feature per cut, or one coherent safety contract. Do not fold unrelated
cleanup or a second backlog item into it. If the work cannot fit one coherent
cut, split it or ask; do not weaken acceptance criteria.

Use this lifecycle in order:

1. Read this contract and the exact canonical backlog row/roadmap section.
2. Record starting identity, clean state, locks/processes, PR state, service
   boundary, scope, permitted paths, rollback, and evidence destination.
3. Enumerate readers, writers, tests, CI, generated artifacts, packaging,
   deployment consumers, documentation, and external/operator dependencies.
4. Write a meaningful RED-first test against the defective base. Prove its
   preconditions and nonzero seam; record the precise expected failure.
5. Implement the smallest coherent correction. Do not weaken assertions,
   suppress failures, add arbitrary sleeps, or retry a mandatory failure away.
6. Run focused GREEN, negative/adversarial controls, and the complete affected
   floor with real pytest.
7. Regenerate tracked artifacts after the last source edit, inspect every diff,
   and rerun gates invalidated by regeneration.
8. Freeze an immutable candidate, push it normally, and run all final lanes
   against that exact SHA/tree. Pre-freeze evidence never substitutes.
9. Obtain independent implementation/scope, test-integrity/denominator, and
   evidence reviews. Reviewer output is data until its cited facts are checked.
10. Require exact-head GitHub CI and a current PR body before merge.
11. Merge only the reviewed head, prove merged-tree identity, deploy when the
    change affects runtime/deployment state, and verify health/version.
12. Update durable roadmap evidence, prune only exact disposable artifacts,
    reconcile branches safely, and report terminal state.

RED-first means the new test fails for the intended defect on the correct base,
not because of a typo, missing dependency, empty fixture, wrong environment, or
untracked CI path. GREEN means the same test reaches the intended production
path and passes after the fix. A test written after implementation has no RED
provenance unless the defective parent is replayed explicitly.

Some gates judge the TREE rather than a diff, so `bd-band-derive` can never
select them: it derives from changed paths and their subject is everything.
Between v3.66.1223 and v3.66.1238 four defects shipped that such a gate already
in this tree would have caught -- a mutation anchor resolving zero or two times
(four times over), a subprocess budget above the bound governing its item, and a
subprocess inheriting the ambient locale. None was a missing gate; each was a
gate that did not run. Run `toolchain/bin/bd-precut --gate` before freezing a
candidate; it executes that undertow explicitly and refuses on failure.

The affected band is necessary but not sufficient. A green band is not proof
that no regression exists; adversarial review, schedule interactions, generated
state, packaging, and full-suite behavior are separate questions.

Before packaging or final review, regenerate deterministically from the root:

```bash
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

Run regeneration after the last relevant edit. Never re-freeze an intent
baseline merely to make a gate green. Read generated diffs and explain them.

## A4 | Writer and Git safety

There is one authoritative integrator and sole writer for the candidate. Other
workers may inspect immutable checkouts and return proposals or evidence; they
do not push, merge, deploy, or silently edit the integrator's tree.

Every worker result must identify its exact base/candidate/tree/host. A worker
checkout may start at the session base rather than the branch tip; fetch the
named branch or commit, detach at the exact object, then assert a change-specific
symbol exists before measuring. A green old test on old source is not evidence.

Declare path ownership before concurrent work. Never use `git add -A`, `git add
.`, broad globs, or regeneration staging while another writer can modify the
tree. Stage only inspected paths. Recheck `git status`, staged names, and staged
diff immediately before every commit.

Do not use `git reset --hard`, `git checkout --`, broad recursive deletion, or
other destructive recovery unless the exact target and authority are proven.
The sanctioned `scripts/deploy.sh` reset and an explicitly authorized recovery
are narrow exceptions. Prefer additive fixes or exact `apply_patch` restoration.

Preserve the user's unrelated dirty work. Do not amend or rewrite an already
merged commit. GitHub-generated merge commits are GitHub's records; never
re-author them to change attribution or signature state.

After merge:

- fetch and prune the official origin;
- prove the candidate is contained in `origin/main` and the merged tree is the
  reviewed candidate tree;
- fast-forward the authoritative `main` checkout;
- delete a local topic branch only after proving no unique content remains;
- verify zero unpushed commits and clean tracked state.

Remote branch replacement is exceptional. First fetch `main`, prove a two-dot
diff from the remote branch to `origin/main` is empty, and stop on any nonempty
result. If replacement is still needed, use force-with-lease, never bare force.

Secret scanning is a release boundary. Run gitleaks/CI on the exact candidate;
do not hide a new finding by editing a baseline or moving a realistic-looking
secret into a fixture. Security fixtures use explicitly documented zero-entropy
values. Treat a touched baseline line as newly evaluated.

Evidence and commits use exact immutable identity. A result from an earlier
candidate becomes stale when applicable source, tests, workflow, generated
artifacts, or review premises change. Documentation-only changes may transfer
only when they provably cannot affect the claimed behavior or denominator.

## A5 | Verification

Use real pytest through the repository interpreter. Derive affected tests with
`toolchain/bin/bd-band-derive`; its output is a floor, never a ceiling. Add
tree-wide denominators, deleted-file consumers, docs/freshness gates, generated
gates, release gates, and adversarial tests that the changed subject requires.
For a documentation or canonical-backlog edit, run the repository freshness
gate directly as well:

```bash
venv/bin/python toolchain/bin/bd-freshcheck --repo-only
```

For ordinary focused/affected pytest, remove ambient install-directory state:

```bash
env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest tests/test_target.py -q'
```

The only sanctioned canonical local full-suite command is:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ -n 24 --dist loadfile --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly
```

Every token is load-bearing. `-n 24` is fixed; host capacity does not rewrite
the experiment. `--dist loadfile` preserves the qualified scheduling contract.
The timeout names hangs. `signal` mode raises inside the test's own thread so
the offending test is REPORTED BY NAME; `thread` mode called `os._exit(1)` and
killed the worker instead, and wrote its stack dump to a worker stdout that
xdist points at /dev/null -- measured 0 dumps under xdist against 2 serially, so
it named nothing in the only shape this command uses.
`--max-worker-restart=0` turns any surviving worker death into an immediate
abort rather than the drain livelock that once span 11.6 hours. No `-q` keeps
xdist worker crash narration visible, and `PYTHONUNBUFFERED=1` preserves output
from a run that never exits. A different worker count, scheduler, plugin,
interpreter, or environment is a different experiment and cannot authorize
merge.

Do not export `BD_INSTALL_DIR` into pytest. `BD_HOME` does not govern the same
resources. Pop inherited values with `env -u BD_INSTALL_DIR`; merely omitting an
assignment still inherits the caller's shell state.

A wait or monitor that greps a log for a completion marker must gate on a line
that exists ONLY when the current run succeeded. Logs are appended across
attempts, so an earlier failed run's completion line satisfies the wait and the
caller proceeds against stale evidence. Gate on the verdict itself, seed the
seen-set before arming, or record the line count before the run and read only
past it.

Every selected test lane records a nonzero expected, collected, and executed
denominator; pass/fail/error/skip/xfail/xpass/deselected identities; raw process
status; timeout state; exact command and digest; environment identity; start/end
UTC; pre/post repository state; complete persistent log; result hashes; and an
atomic completion marker. A partial JUnit file must not define its own expected
denominator.

Test the seam, not only components. Assert fixtures actually built the intended
shape, callbacks/branches fired exact nonzero counts, and negative controls fail
for the intended reason. When several refusals share an exit code, assert the
distinctive diagnostic and make later refusal conditions pass so they cannot
launder the result.

When a function has several outcomes, prove each is reachable. A green battery
is not coverage evidence until the exercised path and mutation catcher are
identified. A schedule-sensitive failure is not retired by one green sample;
preserve failures, establish causation, and compare matched environments.

CI is a tree-wide denominator independent of the diff. A gate CI does not run
does not exist. Every new `tests/test*.py` file declares `BD_GATE_SCOPE` or is
explicitly classified by the frozen legacy mechanism; repo-wide/safety gates
must be directly present in a shard and `_DECLARED`. Read CI status from named
status/conclusion fields, not positional CLI columns.

Never trim a slow CI shard or omit a required test to regain green. Preserve the
denominator and split or ask when a shard exceeds its budget. Do not cancel
independent lanes merely because one fails; complete failure information reduces
refreeze cycles.

Report completed measurements, not estimated percentages. Verify summary and
verdict lines against raw evidence. Say what was not run and why. Claims of
READY, merged, deployed, clean, or complete require current exact SHA/tree/host
evidence; otherwise report UNKNOWN/HOLD.

## A6 | Release and deployment

A version bump is three source edits together:

1. `bulk_downloader/__init__.py` sets `__version__`;
2. `tests/test_settings_center_slice4.py` pins that exact value;
3. an ASCII-only `CHANGELOG.md` entry is prepended and anchored on the previous
   release header.

Then regenerate and inspect `PIN_INDEX.json`; do not assume the number or
location of version pins. Run version, changelog, generated, release, frontend,
and packaging gates against the final candidate.

The repository environment is `venv`, not the dot-prefixed `.venv`; use
`venv/bin/python` and do not fall through to a different system interpreter.

Release packaging must include required gitignored generated artifacts and
`frontend/dist`, prove the archive member set matches the intended source tree,
exclude runtime/private/retired residue, and retain raw verifier status. Missing
generated or frontend artifacts are failures, not permission to omit them.

Git moves tracked files; it does not update a running process, clear bytecode,
regenerate gitignored artifacts, rebuild the SPA, or restart the service. Use
`scripts/deploy.sh` for an existing host and
`docs/repo/FRESH_HOST_BRINGUP.md` for a new host. Do not hand-recreate either
sequence.

A failed deploy is not a no-op. It can leave the service down after stop/cache
steps, so preserve the failing step, inspect system state, and remediate before
claiming health. The deploy script itself has a special inode boundary: after
`git reset --hard`, the running shell continues executing the pre-reset script
inode while the path names the new file. Changes to later deploy steps therefore
take effect on the following invocation unless an explicit handoff is designed.

After merge, deploy the exact merged main tree when runtime, source delivery,
generated artifacts, or deployment state changed. Verify the script reports the
merged SHA, health endpoint version, and `GET / = 200`. There is no general
`/api/version`; use `/api/health` for deployment verification.

Do not run formal tests against the live service or authenticated sites. Test
lanes use isolated HOME/TMPDIR/cache/state/ports/databases and persistent
filesystem semantics appropriate to the feature. Do not run capture on a host
whose tree is being edited, and do not edit it during capture.

Host timezone and load are part of evidence. A UTC host cannot prove local-time
behavior; force `TZ` in the relevant test. Formal timing runs need recorded load
and no competing local-model/full-suite work.

## A7 | Engineering invariants

A gate must see the subject it claims to judge. Define the complete denominator,
assert it is nonzero, reconcile collection to execution, and make unavailable
measurement return UNKNOWN rather than OK. Do not derive an expected set solely
from the artifact under test; retain an independent exact denominator or
mechanical completeness proof.

The inverse matters too: do not let identity, timestamps, mutable paths, comments,
or unrelated text make an unchanged subject fail. If a gate scans source text,
remember its comments and examples are inside that denominator. Strip comments
or parse structure when prose must not count.

Every fix tends to reproduce the defect's shape. Audit the new implementation,
test harness, generated artifact, cleanup, and evidence recorder for the same
missing denominator, stale identity, ordering, path, environment, or fail-open
condition it is intended to prevent.

Tests prove preconditions before verdicts: assert the precondition explicitly.
Assert the fixture created the file,
process, identity, callback, row, link, collision, or race; assert exact fired
counts; include a negative control; and assert the distinctive outcome. Empty
iterables, unrelated early refusal, or teardown restoration must not manufacture
green.

A process probe matches every command line containing its pattern, including the
shell that WROTE the script being searched for -- which the `[b]racket` trick
does not hide, because there the pattern is data rather than argv. Anchor on the
invocation (`^bash /path/to/script`) or match a known PID. A count that is
suspiciously uniform across hosts is the tell that the probe is counting itself;
print the matching lines once and read them before trusting any such number.

Environment-changing tests remove inherited values rather than merely declining
to set them. To ask whether importing code touches a resource, instrument the
resource boundary and exercise relevant flag states; source reading is not
runtime evidence. Isolate HOME, TMPDIR, cache, database, ports, current directory,
module globals, logging, subprocesses, and services where applicable.

For SQLite evidence, `immutable=1` is for surveying a file that must not be
touched; use a normal open to assert what was written, and preserve WAL/SHM
companions during recovery. Package-name resolution is not version satisfaction;
the requirements gate must evaluate specifiers or return UNKNOWN.

In shallow clones, only `git merge-base --is-ancestor` exit 0 proves ancestry.
Nonzero is UNKNOWN until history is deepened. Fetching a commit by SHA may obtain
the object without connecting its history and can manufacture a false negative.

Any source rewriter or mutation harness must:

- assert the old anchor occurs exactly once;
- mutate in memory and write once at the end;
- prove bytes changed with exact length/count arithmetic;
- parse the result and check name/runtime semantics where parsing is insufficient;
- restore the original and abort on malformed output;
- treat invalid mutants separately from caught/escaped mutants;
- prove RED with the mutant and GREEN without it;
- record durable recovery state before the first irreversible write;
- inspect `git status` and diff after interruption before rerunning.

Do not use `sed -i` as an applied check or retype punctuation-sensitive anchors;
locate exact current text with `rg` and patch it explicitly. Use
`toolchain/bin/bd-mutate` for mutation batteries instead of rebuilding its
semantics.

An action with an irreversible side effect must prove its evidence record is
writable before acting. Create conditional artifacts lazily and remove only
targets whose identity and ownership are proven. Missing cleanup evidence is a
failure, not a successful no-op.

## A8 | Focused authorities and commands

Keep this routing table small. It is a starting point, not a complete tool
denominator; inspect `toolchain/bin` and read the nearest tool's implementation
and selftest before hand-writing a replacement.

The tool denominator is not only `toolchain/bin`. Operator harness scripts live
outside the repository and are equally load-bearing. Before creating a file at
any path, prove that exact name is unused: list the target and search the harness
directory as well as the repository. A name that already exists belongs to its
existing caller, so read that caller's invocation to learn the real interface and
choose a different name for new logic. Writing new logic under an existing tool's
name changed its argument contract silently and every integrate refused until the
original was reconstructed from its caller.

| Question | Focused authority |
| --- | --- |
| What work remains? | `project-knowledge/IMPROVEMENT_BACKLOG.md` |
| Which tests does a changed path require? | `project-knowledge/TOUCHED_FILE_TO_TEST.md` and `toolchain/bin/bd-band-derive` |
| How is a cloud/session environment prepared? | `docs/repo/ENVIRONMENT_PROVISIONING.md` |
| How is a test host provisioned? | `scripts/provision_test_host.sh` |
| How is a fresh host or deployment prepared? | `docs/repo/FRESH_HOST_BRINGUP.md` and `scripts/deploy.sh` |
| What are the guarded files? | root `guards.json` and `toolchain/bin/bd-guardcheck` |
| What safety declarations exist? | root `FOOTGUNS.json` and `INVARIANTS.json` |
| How are generated artifacts ordered? | `toolchain/bin/bd-regen-order` |
| How is affected scope derived? | `toolchain/bin/bd-band-derive` |
| How are mutations run safely? | `toolchain/bin/bd-mutate` |
| How is a cut's robustness tier chosen? | `project-knowledge/CUT_TIERING.md` |
| How does CI classify gates? | `.github/workflows/ci.yml` and `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py` |

Repository populations are distinct: `bulk_downloader/` is application Python,
`tests/` contains pytest files plus fixtures/corpus, `tools/` contains Python
build/analysis code, `toolchain/bin` contains extensionless operational tools,
`frontend/` is the React/TypeScript SPA, `scripts/` owns install/deploy/service
operations, and `project-knowledge/` holds generated/current knowledge. Measure
membership at decision time; do not put volatile counts here.

Before a broad scan, identify the exact population and use `rg`/`git ls-files`
or the existing purpose-built tool. Preserve complete logs outside the repository
and summarize results rather than flooding the working context. This does not
permit skimming: read every instruction or source needed for the current claim.

CAPTURE WHOLE TO DISK, READ A SLICE. A SECOND HAND-ROLLED HEREDOC IS A MISSING `bd-*` TOOL.
And measure before optimising: use the complete captured denominator,
then inspect bounded slices and promote repeated logic into the existing toolchain.

Prefer parallel read-only discovery for independent populations when explicitly
authorized, but retain one integrator and one writer. Local-model or worker
classifications are proposals, not tests, reviews, absence proofs, merge approval,
or deployment authority.

Before claiming completion, audit the actual objective requirement by
requirement against current files, tests, CI, PR, merge, deployment, and roadmap
evidence. Do not redefine completion around the work already performed.
