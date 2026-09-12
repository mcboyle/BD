# CLAUDE.md — operating contract for BulkDownloader

Read this file fully before editing. It is the sole agent-facing contract for
BulkDownloader: standing authority, safety boundaries, required commands, and
links to focused owners. Incidents live in Git and `CHANGELOG.md`, not here.

## A1 | Authority and scope

BulkDownloader (BD) is a self-hosted Flask, Playwright, and React/TypeScript
batch downloader operated by Matthew. Authoritative repository:
`/home/mboyle/BulkDownloader`, official origin `mcboyle/BD`. Deployed tree and
working tree may be the same directory (`test5`).

This is the sole agent-facing contract. Do not create or revive a second
bootstrap prompt, handoff prompt, session contract, promise ledger, task
register, casebook, or authority. Current product work lives only in
`project-knowledge/IMPROVEMENT_BACKLOG.md`; Git history and `CHANGELOG.md` are
evidence, not task queues.

Every reading is a claim about a commit AND a host. Before relying on a result,
record or verify: IP address and hostname TOGETHER (hostnames are not unique on
this fleet -- the IP is the identity); repository path and origin; branch, HEAD,
tree SHA, base, ahead/behind; clean tracked/untracked/index state; interpreter,
environment and exact command; the service or fleet boundary touched. A finding
without host, commit and tree identity is not transferable. A stale checkout's
green tests are about that checkout. Fetch, resolve the exact object, prove
ancestry and containment before comparing.

Measure volatile facts from the current tree (`git ls-files`, parsers, tool
output, CI APIs, probes, manifests) -- never copy counts from prose. A glob is a
denominator choice: include extensionless scripts, frontend source, fixtures,
generated inputs and deleted paths when the claim covers them. Verify what a
tool executes, not its docstring. Re-derive backlog status before acting;
preserve explicit uncertainty when current evidence cannot decide.

## A2 | Authorization and state

Matthew's terse directives (`go`, `continue`, `cut`, a bare artifact) authorize
routine work within the established scope -- never unrelated repositories,
hosts, data, people, costs, credentials or destructive targets. `hold`, `wait`,
`pause`, `stop` mean stop at the next safe point; read-only inspection may
continue only when it cannot affect active evidence or external state. `resume`
restores the previous scope without broadening it.

PASS and FAIL are not exhaustive. UNKNOWN is a failing third state whenever a
required claim cannot be measured: missing, malformed, truncated, stale,
wrong-SHA/tree/host, zero-denominator, digest-mismatched, unobserved or
transport-failed evidence is UNKNOWN/HOLD, never permission.

Keep authorities separate: implementation permits scoped source/test/doc
changes; merge requires the reviewed exact head, terminal required tests,
exact-head CI, current PR metadata and clean state; deployment requires the
exact merged tree and the sanctioned deploy path; infrastructure, package,
fleet or external-service actions stay inside the operator's explicit scope and
cost/license limits.

Deferred work is a machine-visible row in the canonical backlog (unique id,
status, evidence, acceptance criteria, dependency). Prose such as "later" is not
a deferral. Do not file a row for completed, obsolete or already-represented
work. Any train may carry a register commit (bd-register-append/close/amend);
workers PROPOSE row text and never assign ids -- the integrator assigns them.
Register rows never gate a runtime lane.

When a meaningful choice would change the product or expand authority, present
the evidence and ask; routine, reversible, in-scope work continues without
confirmation. Landing authority sits with the integrator: it lands a train once
the required lenses BOARD, the lane is green and exact-head CI is green. The
adjudicator rules on refusals, lane failures and lens disagreement and verifies
every landing by blob. A routine choice takes the recommended default, is logged
in OPERATOR_DECISIONS.md with the word DEFAULT, and reaches the operator in a
digest; only a blocker interrupts. Every TIMED hold names its EXPIRY ACTION
(resume, escalate or stop) -- a duration alone is not one. A bare `hold` stands
until `resume`. Never narrow an ambiguous objective to obtain a green result.

Know which host you are changing. Never edit a checkout during an authoritative
capture or timing run. Never test against the live service or an authenticated
site unless that exact contact is authorized and isolated.

## A3 | Change lifecycle

Carry one coherent feature per cut, or one coherent safety contract; do not
fold unrelated cleanup or a second backlog item in. If it cannot fit one cut,
split or ask.

A TRAIN is the second sanctioned shape: up to sixteen independently reviewed
patches with DISJOINT authored paths under one version trio, one lane and one
exact-head CI. Width is the throughput lever, because the trio serializes
landings. A lane failure is bisected by patch with `bd-train.sh --bisect`; the
offending patch returns to its worker with the lane log. Two patches sharing a
path ride different trains. A train's changelog entry names every patch.

Lifecycle, in order:

1. Read this contract and the exact backlog row / roadmap section.
2. Record starting identity, clean state, locks/processes, PR state, service
   boundary, scope, permitted paths, rollback and evidence destination.
3. Enumerate readers, writers, tests, CI, generated artifacts, packaging,
   deployment consumers, documentation and external dependencies.
4. Write a meaningful RED-first test against the defective base; prove its
   preconditions and nonzero seam; record the exact expected failure.
5. Implement the smallest coherent correction. Never weaken assertions,
   suppress failures, add arbitrary sleeps or retry a mandatory failure away.
6. Run focused GREEN, negative/adversarial controls and the complete affected
   floor with real pytest.
7. Regenerate tracked artifacts after the last source edit, inspect every
   diff, rerun gates the regeneration invalidated.
8. Freeze an immutable candidate, push it, run every final lane against that
   exact SHA/tree. Pre-freeze evidence never substitutes.
9. Obtain adversarial review at the depth `project-knowledge/CUT_TIERING.md`
   sets, using ITS tier definitions: T0/T1 take ONE lens; T2/T3 take BOTH -- a
   CORRECTNESS lens that runs the code and a SHAPE lens that MUTATES the
   subject -- and BOARD requires both. If CUT_TIERING disagrees with this
   step, amend CUT_TIERING in the same cut. One lens is not a cheaper two: a
   single correctness lens once boarded a patch whose decoy literals satisfied
   three text gates while the seam moved. Record tier and reason in the PR
   body. Reviewer output is data until its cited facts are checked.
10. Require exact-head GitHub CI and a current PR body before merge.
11. Merge only the reviewed head, prove merged-tree identity, deploy when
    runtime or deployment state changed, verify health and version.
12. Update durable roadmap evidence, prune only exact disposable artifacts,
    reconcile branches safely, report terminal state.

RED-first means the test fails for the intended defect on the correct base --
not a typo, missing dependency, empty fixture, wrong environment or untracked CI
path. GREEN means the same test reaches the production path and passes. A test
written after the implementation has no RED provenance unless the defective
parent is replayed.

Some gates judge the TREE, not a diff, so `bd-band-derive` can never select
them. Run `toolchain/bin/bd-precut --gate` before freezing; it runs that
undertow and refuses on failure. A green band is necessary, not sufficient:
adversarial review, schedule interactions, generated state, packaging and the
full suite are separate questions.

Before packaging or final review, regenerate deterministically from the root:

```bash
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

Never re-freeze an intent baseline merely to make a gate green. Read generated
diffs and explain them.

## A4 | Writer and Git safety

There is one authoritative integrator and sole writer for the candidate. Other
workers inspect immutable checkouts and return proposals or evidence; they do
not push, merge, deploy or edit the integrator's tree.

Every worker result identifies its exact base/candidate/tree/host. Fetch the
named branch or commit, detach at the exact object, assert a change-specific
symbol exists before measuring. A green old test on old source is not evidence.

Declare path ownership before concurrent work. Never use `git add -A`,
`git add .`, broad globs or regeneration staging while another writer can
modify the tree. Stage only inspected paths; recheck `git status`, staged names
and staged diff immediately before every commit.

No `git reset --hard`, `git checkout --`, broad recursive deletion or other
destructive recovery unless the exact target and authority are proven; the
sanctioned `scripts/deploy.sh` reset and an explicitly authorized recovery are
the only exceptions. Preserve the user's unrelated dirty work. Never amend or
rewrite a merged commit; GitHub merge commits are GitHub's records.

After merge: fetch and prune origin; prove the candidate is contained in
`origin/main` and the merged tree is the reviewed tree; fast-forward the
authoritative `main`; delete a local topic branch only after proving no unique
content remains; verify zero unpushed commits and clean tracked state. Remote
branch replacement is exceptional: prove the two-dot diff from the remote
branch to `origin/main` is empty, then force-with-lease, never bare force.

Secret scanning is a release boundary: run gitleaks/CI on the exact candidate;
never hide a finding by editing a baseline or moving a realistic secret into a
fixture. Fixtures use documented zero-entropy values; a touched baseline line
is newly evaluated.

Evidence uses exact immutable identity. A result from an earlier candidate is
stale once applicable source, tests, workflow, generated artifacts or review
premises change; documentation-only changes transfer only when they provably
cannot affect the claimed behavior or denominator.

## A5 | Verification

Before reporting `VERDICT: PATCH` on a T2/T3 patch, a worker runs the
correctness lens's checklist on itself: the RED command with its EXACT failure
text, the GREEN command and result, a negative control, an exact-count
assertion, the bd-mutate result, and both tree-gate lines. A DONE.md missing
any of these is bounced by the review dispatcher before a lens is spent.
`bd-review-prep` itself checks only that DONE.md exists with line 1 exactly
`VERDICT: PATCH`; the floor is enforced by the dispatcher, not the tool. A
T0/T1 patch owes only what its tier owes.

Use real pytest through the repository interpreter. Derive affected tests with
`toolchain/bin/bd-band-derive`; its output is a floor, never a ceiling -- add
tree-wide denominators, deleted-file consumers, docs/freshness, generated,
release and adversarial tests the subject requires. For a documentation or
backlog edit also run:

```bash
venv/bin/python toolchain/bin/bd-freshcheck --repo-only
```

For focused/affected pytest, remove ambient install-directory state:

```bash
env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest tests/test_target.py -q'
```

The only sanctioned canonical local full-suite command is:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ -n 24 --dist loadfile --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly
```

Every token is load-bearing: `-n 24` is fixed; `--dist loadfile` preserves the
scheduling contract; `signal` reports a hung test BY NAME where `thread` killed
the worker and dumped nowhere; `--max-worker-restart=0` turns a worker death
into an abort instead of an 11.6-hour drain livelock; no `-q` keeps crash
narration; `PYTHONUNBUFFERED=1` preserves output from a run that never exits.
A different worker count, scheduler, plugin, interpreter or environment is a
different experiment and cannot authorize merge. Never export `BD_INSTALL_DIR`
into pytest (`BD_HOME` is a different resource); pop it with `env -u`.

A wait that greps a log for completion must gate on a line that exists ONLY
when the current run succeeded -- gate on the verdict, seed the seen-set, or
read only past the pre-run line count.

Every selected lane records nonzero expected/collected/executed denominators;
pass/fail/error/skip/xfail/xpass/deselected identities; raw status; timeout
state; exact command and digest; environment identity; start/end UTC; pre/post
repository state; complete log; result hashes; an atomic completion marker. A
partial JUnit file never defines its own denominator.

Test the seam, not only components: assert fixtures built the intended shape,
callbacks fired exact nonzero counts, negative controls fail for the intended
reason; when refusals share an exit code, assert the distinctive diagnostic.
Prove each outcome of a multi-outcome function is reachable; a green battery is
not coverage until the exercised path and mutation catcher are identified. A
schedule-sensitive failure is not retired by one green sample.

CI's pytest denominator is an ENUMERATED LIST OF NAMED FILES: `.github/workflows/ci.yml`
hands pytest explicit paths (gate-suite shard `suites` values plus the
integration job's files) and never a directory, so most tracked `tests/*.py`
are named by nothing. Derive both populations -- parse `suites` from the
workflow (not by scanning it as text; comments name files too) and take the
tree from `git ls-files tests/`. A gate CI does not run does not exist. Whether
that enumeration is sufficient is under review by the operator; nothing here is
relaxed or tightened while he decides. Every new `tests/test*.py` file declares
`BD_GATE_SCOPE` or is explicitly classified by the frozen legacy mechanism;
repo-wide/safety gates must be directly present in a shard and `_DECLARED`.
Read CI status from named status/conclusion fields, not positional CLI columns.
Never trim a slow CI shard or omit a required test to regain green. Split or
ask when a shard exceeds its budget. Do not cancel independent lanes because
one fails.

Report completed measurements, not estimates. Verify summary lines against raw
evidence. Say what was not run and why. READY, merged, deployed, clean or
complete require current exact SHA/tree/host evidence; otherwise UNKNOWN/HOLD.

## A6 | Release and deployment

A version bump is three source edits together: `bulk_downloader/__init__.py`
sets `__version__`; `tests/test_settings_center_slice4.py` pins that exact
value; an ASCII-only `CHANGELOG.md` entry is prepended, anchored on the previous
release header. Then regenerate and inspect `PIN_INDEX.json`; do not assume the
number or location of pins. Run version, changelog, generated, release,
frontend and packaging gates against the final candidate.

The environment is `venv` (not `.venv`); use `venv/bin/python` and never fall
through to a system interpreter.

Release packaging includes required gitignored generated artifacts and
`frontend/dist`, proves the archive member set matches the source tree,
excludes runtime/private/retired residue and retains raw verifier status.
Missing artifacts are failures, not permission to omit them.

A FETCH EXITING 0 IS NOT DELIVERY. Some hosts fetch from a per-host bare mirror
nothing pushes into; deploy.sh refuses with INTENDED-COMMIT-ABSENT and names the
remedy. Prove the intended commit is PRESENT on the host before deploying; read
`docs/repo/FLEET_TOPOLOGY.md` for which hosts fetch from where.

Git moves files; it does not restart a process, clear bytecode, regenerate
artifacts or rebuild the SPA. Use `scripts/deploy.sh` for an existing host and
`docs/repo/FRESH_HOST_BRINGUP.md` for a new one; never hand-recreate either.
A failed deploy is not a no-op -- it can leave the service down; preserve the
failing step, inspect state, remediate before claiming health. deploy.sh runs
the pre-reset script inode after its own `git reset --hard`, so edits to later
steps take effect on the following invocation.

After merge, deploy the exact merged main tree when runtime, source delivery,
generated artifacts or deployment state changed, at the operator's cadence;
the canonical suite runs on the tree that is DEPLOYED (prove `main^{tree}`
equality after the final landing). role=runner hosts never carry two versions.
Verify the script reports the merged SHA, `/api/health` version and
`GET / = 200` (there is no `/api/version`).

Test lanes use isolated HOME/TMPDIR/cache/state/ports/databases; never formal
tests against the live service or authenticated sites; never capture on a host
whose tree is being edited. Host timezone and load are evidence: force `TZ`
where local time matters; record load for timing runs.

## A7 | Engineering invariants

A gate must see the subject it claims to judge: define the complete
denominator, assert it nonzero, reconcile collection to execution, return
UNKNOWN for unavailable measurement, and never derive the expected set solely
from the artifact under test. The inverse holds: identity, timestamps, mutable
paths, comments or unrelated text must not fail an unchanged subject -- strip
comments or parse structure when prose must not count.

Every fix reproduces the defect's shape: audit the new implementation, harness,
artifact, cleanup and recorder for the same missing denominator, stale
identity, ordering, path, environment or fail-open condition.

Tests prove preconditions before verdicts: assert the precondition explicitly
(the fixture created the file/process/identity/row/race); assert exact fired
counts; include a negative control; assert the distinctive outcome. Empty iterables, unrelated early
refusal or teardown restoration must not manufacture green.

A process probe matches every command line containing its pattern, including
the shell that wrote the script (the `[b]racket` trick does not hide it).
Anchor on the invocation (`^bash /path/to/script`) or a known PID; a count
uniform across hosts is the tell.

A RENDERED PAGE IS EVIDENCE; A CANDIDATE LIST IS A CLAIM ABOUT IT. BD once
saved 5 GB of the wrong scene under the right title because a Related Videos
grid exposed 159 media links; one screenshot answered it, and the same
screenshot later proved three "mis-filed" rows were correct. Capture what the
browser sees before theorising. The operator harness carries `bd-shoot.py`
(site id, URL, destination) for this; it drives a real browser against an
authenticated site, so it is an operator instrument, never a gate.

A CONTAINMENT TEST IS A DENOMINATOR CHOICE. SHA ancestry decided zero of 24
tagged candidates (every cut is rebased); patch-id misreported three landed
tags (trio collisions change the patch); only comparing THIS BLOB for every
touched file answered correctly. Pick the test that answers the question and
say which one you used.

A DIAGNOSTIC THAT COLLAPSES DISTINCT FAILURES COSTS THE INVESTIGATION: name the
step that failed and carry the server's own words (a 401 and a broken pairing
endpoint lead to opposite actions).

Environment-changing tests remove inherited values rather than declining to
set them. To ask whether importing code touches a resource, instrument the
boundary; source reading is not runtime evidence. Isolate HOME, TMPDIR, cache,
database, ports, cwd, module globals, logging, subprocesses and services.
SQLite: `immutable=1` only for surveying; normal open to assert writes;
preserve WAL/SHM during recovery. The requirements gate evaluates specifiers or
returns UNKNOWN. In shallow clones only `git merge-base --is-ancestor` exit 0
proves ancestry; fetching by SHA may obtain the object without its history.

A row, document or commit message cites a TRACKED path plus a function or
anchor NAME; line numbers go stale and the doc anchor gate resolves
`file:line` against tracked paths only, so a harness script is cited BY NAME
ALONE (an earlier draft of this paragraph used the colon form as its example
and refused the whole tree).

Any source rewriter or mutation harness asserts the old anchor occurs exactly
once; mutates in memory and writes once; proves bytes changed by exact
arithmetic; parses the result; restores the original and aborts on malformed
output; separates invalid mutants from caught/escaped; proves RED with the
mutant and GREEN without; records recovery state before the first irreversible
write; inspects `git status` after interruption. Never `sed -i` as an applied
check; locate exact text with `rg` and patch explicitly. Use
`toolchain/bin/bd-mutate` rather than rebuilding it.

An action with an irreversible side effect proves its evidence record is
writable before acting. Create conditional artifacts lazily; remove only
targets whose identity and ownership are proven. Missing cleanup evidence is a
failure, not a successful no-op.

## A8 | Focused authorities and commands

This table is a starting point, not the tool denominator. Inspect
`toolchain/bin` and read the nearest tool's implementation and selftest before
hand-writing a replacement. The denominator also includes operator harness
scripts outside the repository. Before creating a file at any path, prove the
name is unused in both the harness directory and the repository: a name that
exists belongs to its caller, and writing new logic under it silently changed
an argument contract once.

A Codex worker is addressed by host IP and session name, never hostname. A
host may carry two workers while it runs no lane; a capacity host carrying a
lane or the canonical suite takes none.

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

Populations are distinct: `bulk_downloader/` application Python, `tests/`
pytest plus fixtures/corpus, `tools/` build/analysis Python, `toolchain/bin`
extensionless tools, `frontend/` the SPA, `scripts/` install/deploy/service,
`project-knowledge/` generated/current knowledge. Measure membership at
decision time.

Before a broad scan, name the population and use `rg`/`git ls-files` or the
purpose-built tool. CAPTURE WHOLE TO DISK, READ A SLICE; a second hand-rolled
heredoc is a missing `bd-*` tool. Generated remote source is transport data:
send its UTF-8 bytes through an ASCII-safe decoder and verify a digest before
publication; never embed it in a bootstrap heredoc. Parallel read-only
discovery is fine when authorized, but one integrator and one writer remain.
Local-model or worker classifications are proposals, never tests, reviews,
absence proofs, merge approval or deployment authority.

Before claiming completion, audit the objective requirement by requirement
against current files, tests, CI, PR, merge, deployment and roadmap evidence.
Do not redefine completion around the work already performed.
