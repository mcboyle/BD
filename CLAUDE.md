# CLAUDE.md — operating contract for BulkDownloader

Read fully before editing. Sole agent-facing contract for BulkDownloader: standing authority, safety boundaries, required commands, links to focused owners. Incidents live in Git and `CHANGELOG.md`, not here.

## A1 | Authority and scope

BulkDownloader (BD): self-hosted Flask, Playwright, React/TypeScript batch downloader, operated by Matthew. Authoritative repo: `/home/mboyle/BulkDownloader`, official origin `mcboyle/BD`. Deployed tree and working tree may be same dir (`test5`).

Sole agent-facing contract. Do not create/revive second bootstrap prompt, handoff prompt, session contract, promise ledger, task register, casebook, or authority. Current product work lives only in `project-knowledge/IMPROVEMENT_BACKLOG.md`; Git history and `CHANGELOG.md` are evidence, not task queues.

Every reading = claim about commit AND host. Before relying on result, record/verify: IP + hostname TOGETHER (hostnames not unique on fleet -- IP is identity); repo path + origin; branch, HEAD, tree SHA, base, ahead/behind; clean tracked/untracked/index state; interpreter, environment, exact command; service/fleet boundary touched. Finding without host, commit, tree identity not transferable. Stale checkout's green tests are about that checkout. Fetch, resolve exact object, prove ancestry + containment before comparing.

Measure volatile facts from current tree (`git ls-files`, parsers, tool output, CI APIs, probes, manifests) -- never copy counts from prose. Glob = denominator choice: include extensionless scripts, frontend source, fixtures, generated inputs, deleted paths when claim covers them. Verify what tool executes, not docstring. Re-derive backlog status before acting; preserve explicit uncertainty when evidence can't decide.

## A2 | Authorization and state

Matthew's terse directives (`go`, `continue`, `cut`, bare artifact) authorize routine work in established scope -- never unrelated repos, hosts, data, people, costs, credentials, destructive targets. `hold`, `wait`, `pause`, `stop` = stop at next safe point; read-only inspection continues only when it can't affect active evidence or external state. `resume` restores previous scope, no broadening.

PASS/FAIL not exhaustive. UNKNOWN = failing third state whenever required claim can't be measured: missing, malformed, truncated, stale, wrong-SHA/tree/host, zero-denominator, digest-mismatched, unobserved, transport-failed evidence = UNKNOWN/HOLD, never permission.

Keep authorities separate: implementation permits scoped source/test/doc changes; merge requires reviewed exact head, terminal required tests, exact-head CI, current PR metadata, clean state; deployment requires exact merged tree + sanctioned deploy path; infrastructure, package, fleet, external-service actions stay inside operator's explicit scope and cost/license limits.

Deferred work = machine-visible row in canonical backlog (unique id, status, evidence, acceptance criteria, dependency). Prose like "later" not a deferral. No row for completed, obsolete, already-represented work. Any train may carry register commit (bd-register-append/close/amend); workers PROPOSE row text, never assign ids -- integrator assigns. Register rows never gate runtime lane.

Meaningful choice changing product or expanding authority: present evidence, ask. Routine, reversible, in-scope work continues without confirmation. Landing authority = integrator: lands train once required lenses BOARD, lane green, exact-head CI green. Adjudicator rules on refusals, lane failures, lens disagreement; verifies every landing by blob. Routine choice takes recommended default, logged in OPERATOR_DECISIONS.md with word DEFAULT, reaches operator in digest; only blocker interrupts. Every TIMED hold names EXPIRY ACTION (resume, escalate, stop) -- duration alone not one. Bare `hold` stands until `resume`. Never narrow ambiguous objective to get green.

Know which host you change. Never edit checkout during authoritative capture or timing run. Never test against live service or authenticated site unless that exact contact authorized + isolated.

## A3 | Change lifecycle

One coherent feature per cut, or one coherent safety contract; no unrelated cleanup or second backlog item. Doesn't fit one cut: split or ask.

TRAIN = second sanctioned shape: up to sixteen independently reviewed patches, DISJOINT authored paths, one version trio, one lane, one exact-head CI. Width = throughput lever, since trio serializes landings. Lane failure bisected by patch with `bd-train.sh --bisect`; offending patch returns to worker with lane log. Two patches sharing path ride different trains. Train changelog entry names every patch.

Lifecycle, in order:


1. Read this contract + exact backlog row / roadmap section.

2. Record starting identity, clean state, locks/processes, PR state, service boundary, scope, permitted paths, rollback, evidence destination.

3. Enumerate readers, writers, tests, CI, generated artifacts, packaging, deployment consumers, docs, external deps.

4. Write meaningful RED-first test against defective base; prove preconditions + nonzero seam; record exact expected failure.

5. Implement smallest coherent correction. Never weaken assertions, suppress failures, add arbitrary sleeps, retry mandatory failure away.

6. Run focused GREEN, negative/adversarial controls, complete affected floor with real pytest. T0/T1 run affected band, `bd-precut --gate` tree gates, `bd-freshcheck --repo-only`; no full canonical suite per cut.

7. Regenerate tracked artifacts after last source edit, inspect every diff, rerun gates regeneration invalidated.

8. Freeze immutable candidate, push, run every final lane against that exact SHA/tree. Pre-freeze evidence never substitutes.

9. Adversarial review per `project-knowledge/CUT_TIERING.md`:
   T0/T1: ONE lens. T2: ONE CORRECTNESS lens that runs code;
   dispatcher checks worker's recorded `bd-mutate` battery.
   T3: BOTH CORRECTNESS and SHAPE lenses: correctness runs code,
   SHAPE MUTATES subject, BOARD requires both. Two lenses for T3
   because single correctness lens once boarded patch whose decoy
   literals satisfied three text gates while seam moved. If CUT_TIERING
   disagrees with this step, amend both in same cut. Record tier + reason
   in PR body. Reviewer output = data until cited facts checked.

10. Require exact-head GitHub CI + current PR body before merge.

11. Merge only reviewed head, prove merged-tree identity, deploy when
    runtime or deployment state changed, verify health and version.

12. Update durable roadmap evidence, prune only exact disposable artifacts,
    reconcile branches safely, report terminal state.

RED-first = test fails for intended defect on correct base -- not typo, missing dep, empty fixture, wrong env, untracked CI path. GREEN = same test reaches production path, passes. Test written after implementation has no RED provenance unless defective parent replayed.

Some gates judge TREE, not diff, so `bd-band-derive` can never select them. Run `toolchain/bin/bd-precut --gate` before freezing; runs that undertow, refuses on failure. Green band necessary, not sufficient: adversarial review, schedule interactions, generated state, packaging, full suite = separate questions.

Before packaging or final review, regenerate deterministically from root:

```bash
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

Never re-freeze intent baseline just to make gate green. Read generated diffs, explain them.

## A4 | Writer and Git safety

One authoritative integrator, sole writer for candidate. Other workers inspect immutable checkouts, return proposals/evidence; no push, merge, deploy, or editing integrator's tree.

Every worker result identifies exact base/candidate/tree/host. Fetch named branch/commit, detach at exact object, assert change-specific symbol exists before measuring. Green old test on old source not evidence.

Declare path ownership before concurrent work. Never `git add -A`, `git add .`, broad globs, regeneration staging while another writer can modify tree. Stage only inspected paths; recheck `git status`, staged names, staged diff right before every commit.

No `git reset --hard`, `git checkout --`, broad recursive deletion, other destructive recovery unless exact target + authority proven; sanctioned `scripts/deploy.sh` reset and explicitly authorized recovery = only exceptions. Preserve user's unrelated dirty work. Never amend/rewrite merged commit; GitHub merge commits are GitHub's records.

After merge: fetch + prune origin; prove candidate contained in `origin/main`, merged tree = reviewed tree; fast-forward authoritative `main`; delete local topic branch only after proving no unique content remains; verify zero unpushed commits, clean tracked state. Remote branch replacement exceptional: prove two-dot diff from remote branch to `origin/main` empty, then force-with-lease, never bare force.

Secret scanning = release boundary: run gitleaks/CI on exact candidate; never hide finding by editing baseline or moving realistic secret into fixture. Fixtures use documented zero-entropy values; touched baseline line newly evaluated.

Evidence uses exact immutable identity. Result from earlier candidate stale once applicable source, tests, workflow, generated artifacts, review premises change; docs-only changes transfer only when provably can't affect claimed behavior or denominator.

## A5 | Verification

Before reporting `VERDICT: PATCH` on T2/T3 patch, worker runs correctness lens checklist on itself: RED command with EXACT failure text, GREEN command + result, negative control, exact-count assertion, bd-mutate result, both tree-gate lines. DONE.md missing any = bounced by review dispatcher before lens spent. `bd-review-prep` checks only DONE.md exists, line 1 exactly `VERDICT: PATCH`; floor enforced by dispatcher, not tool. T0/T1 patch owes only what tier owes.

T0/T1 cuts run affected band from `bd-band-derive`, tree gates via `bd-precut --gate`, `bd-freshcheck --repo-only`; no full canonical suite per cut. Canonical suite runs once on DEPLOYED tree, per A6.

Use real pytest via repo interpreter. Derive affected tests with `toolchain/bin/bd-band-derive`; output = floor, never ceiling -- add tree-wide denominators, deleted-file consumers, docs/freshness, generated, release, adversarial tests subject requires. Docs or backlog edit: also run:

```bash
venv/bin/python toolchain/bin/bd-freshcheck --repo-only
```

Focused/affected pytest: remove ambient install-directory state:

```bash
env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest tests/test_target.py -q'
```

The only sanctioned canonical local full-suite command is:

```bash
env -u BD_INSTALL_DIR BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ -n 24 --dist loadfile --timeout=240 --timeout-method=signal --max-worker-restart=0 -p no:randomly
```

Every token load-bearing:
- `-n 24` fixed worker parallelism.
- `--dist loadfile` preserves scheduling contract.
- `--timeout-method=signal` reports hung test BY NAME where `thread` killed worker without stacks.
- `--max-worker-restart=0` turns worker death into abort, preventing 11.6-hour drain livelock.
- no `-q` keeps crash narration in pytest output.
- `PYTHONUNBUFFERED=1` preserves output from a run that never exits.

Different worker count, scheduler, plugin, interpreter, env = different experiment, cannot authorize merge. Never export `BD_INSTALL_DIR` into pytest (`BD_HOME` is a different resource); pop with `env -u`.

Wait that greps log for completion must gate on line existing ONLY when current run succeeded -- gate on verdict, seed seen-set, or read only past pre-run line count.

Every selected lane records nonzero expected/collected/executed denominators; pass/fail/error/skip/xfail/xpass/deselected identities; raw status; timeout state; exact command + digest; env identity; start/end UTC; pre/post repo state; complete log; result hashes; atomic completion marker. Partial JUnit file never defines own denominator.

Test seam, not only components: assert fixtures built intended shape, callbacks fired exact nonzero counts, negative controls fail for intended reason; when refusals share exit code, assert distinctive diagnostic. Prove each outcome of multi-outcome function reachable; green battery not coverage until exercised path + mutation catcher identified. Schedule-sensitive failure not retired by one green sample.

CI pytest denominator = ENUMERATED LIST OF NAMED FILES: `.github/workflows/ci.yml` hands pytest explicit paths (gate-suite shard `suites` values plus integration job files), never directory, so most tracked `tests/*.py` named by nothing. Derive both populations -- parse `suites` from workflow (not text scan; comments name files too), take tree from `git ls-files tests/`. A gate CI does not run does not exist. Whether enumeration is sufficient is under review by the operator; nothing relaxed/tightened while he decides. Every new `tests/test*.py` file declares `BD_GATE_SCOPE` or is explicitly classified by frozen legacy mechanism; repo-wide/safety gates must be directly present in a shard and `_DECLARED`. Read CI status from named status/conclusion fields, not positional CLI columns. Never trim a slow CI shard or omit a required test to regain green. Split or ask when shard exceeds budget. Don't cancel independent lanes because one fails.

Report completed measurements, not estimates. Verify summary lines against raw evidence. Say what not run, why. READY, merged, deployed, clean, complete require current exact SHA/tree/host evidence; else UNKNOWN/HOLD.

## A6 | Release and deployment

Integrator stamps release trio at land with `toolchain/bin/bd-land-trio`. Worker patches and `DONE.md` never carry trio or `PIN_INDEX.json` edits; assembler refuses worker patch touching those paths. Version bump = five carriers together (`bd-land-trio` PATHS; CI `Version pin coherence` checks all five and reports every mismatch): `bulk_downloader/__init__.py` sets `__version__`; `tests/test_settings_center_slice4.py` pins exact value; ASCII-only `CHANGELOG.md` entry prepended, anchored on previous release header; regenerated `PIN_INDEX.json` version pin and `project-knowledge/STATIC_KB_MANIFEST.json` `version_context`. Inspect `PIN_INDEX.json`; don't assume pin count/location. Run version, changelog, generated, release, frontend, packaging gates against final candidate.

Environment = `venv` (not `.venv`); use `venv/bin/python`, never fall through to system interpreter.

Release packaging includes required gitignored generated artifacts and `frontend/dist`, proves archive member set matches source tree, excludes runtime/private/retired residue, retains raw verifier status. Missing artifacts = failures, not permission to omit.

FETCH EXITING 0 IS NOT DELIVERY. Some hosts fetch from per-host bare mirror nothing pushes into; deploy.sh refuses with INTENDED-COMMIT-ABSENT, names remedy. Prove intended commit PRESENT on host before deploying; read `docs/repo/FLEET_TOPOLOGY.md` for which hosts fetch from where.

Git moves files; doesn't restart process, clear bytecode, regenerate artifacts, rebuild SPA. Use `scripts/deploy.sh` for existing host, `docs/repo/FRESH_HOST_BRINGUP.md` for new one; never hand-recreate either. Failed deploy not no-op -- can leave service down; preserve failing step, inspect state, remediate before claiming health. deploy.sh runs pre-reset script inode after own `git reset --hard`, so edits to later steps take effect next invocation.

After merge, deploy exact merged main tree when runtime, source delivery, generated artifacts, deployment state changed, at operator's cadence; canonical suite runs on DEPLOYED tree (prove `main^{tree}` equality after final landing). role=runner hosts never carry two versions. Verify script reports merged SHA, `/api/health` version, `GET / = 200` (no `/api/version`).

Test lanes use isolated HOME/TMPDIR/cache/state/ports/databases; never formal tests against live service or authenticated sites; never capture on host whose tree is being edited. Host timezone + load = evidence: force `TZ` where local time matters; formal timing run records load, runs with no competing local-model or full-suite work on host.

## A7 | Engineering invariants

Gate must see subject it claims to judge: define complete denominator, assert nonzero, reconcile collection to execution, return UNKNOWN for unavailable measurement, never derive expected set solely from artifact under test. Inverse holds: identity, timestamps, mutable paths, comments, unrelated text must not fail unchanged subject -- strip comments or parse structure when prose must not count.

Every fix reproduces defect's shape: audit new implementation, harness, artifact, cleanup, recorder for same missing denominator, stale identity, ordering, path, env, fail-open condition.

Tests prove preconditions before verdicts: assert the precondition explicitly (fixture created file/process/identity/row/race); assert exact fired counts; include negative control; assert distinctive outcome. Empty iterables, unrelated early refusal, teardown restoration must not manufacture green.

Process probe matches every command line containing pattern, including shell that wrote script (`[b]racket` trick doesn't hide it). Anchor on invocation (`^bash /path/to/script`) or known PID; count uniform across hosts = the tell.

RENDERED PAGE IS EVIDENCE; CANDIDATE LIST IS CLAIM ABOUT IT. BD once saved 5 GB of wrong scene under right title because Related Videos grid exposed 159 media links; one screenshot answered it, same screenshot later proved three "mis-filed" rows correct. Capture what browser sees before theorising. Operator harness carries `bd-shoot.py` (site id, URL, destination) for this; drives real browser against authenticated site, so operator instrument, never gate.

CONTAINMENT TEST IS DENOMINATOR CHOICE. SHA ancestry decided zero of 24 tagged candidates (every cut rebased); patch-id misreported three landed tags (trio collisions change patch); only comparing THIS BLOB for every touched file answered correctly. Pick test that answers question, say which used.

DIAGNOSTIC COLLAPSING DISTINCT FAILURES COSTS INVESTIGATION: name failed step, carry server's own words (401 vs broken pairing endpoint = opposite actions).

Environment-changing tests remove inherited values, not just decline to set. To ask whether importing code touches resource, instrument boundary; source reading not runtime evidence. Isolate HOME, TMPDIR, cache, database, ports, cwd, module globals, logging, subprocesses, services. SQLite: `immutable=1` only for surveying; normal open to assert writes; preserve WAL/SHM during recovery. Requirements gate evaluates specifiers or returns UNKNOWN. Shallow clones: only `git merge-base --is-ancestor` exit 0 proves ancestry; fetching by SHA may get object without history.

Row, doc, commit message cites TRACKED path plus function/anchor NAME; line numbers go stale, doc anchor gate resolves `file:line` against tracked paths only, so harness script cited BY NAME ALONE (earlier draft of this paragraph used colon form as example, refused whole tree).

Any source rewriter or mutation harness: asserts old anchor occurs exactly once; mutates in memory, writes once; proves bytes changed by exact arithmetic; parses result; restores original, aborts on malformed output; separates invalid mutants from caught/escaped; proves RED with mutant, GREEN without; records recovery state before first irreversible write; inspects `git status` after interruption. Never `sed -i` as applied check; locate exact text with `rg`, patch explicitly. Use `toolchain/bin/bd-mutate`, don't rebuild it.

Action with irreversible side effect proves evidence record writable before acting. Create conditional artifacts lazily; remove only targets with proven identity + ownership. Missing cleanup evidence = failure, not successful no-op.

## A8 | Focused authorities and commands

Table = starting point, not tool denominator. Inspect `toolchain/bin`, read nearest tool's implementation + selftest before hand-writing replacement. Denominator also includes operator harness scripts outside repo. Before creating file at any path, prove name unused in both harness dir and repo: existing name belongs to its caller; writing new logic under it silently changed argument contract once.

Codex worker addressed by host IP + session name, never hostname. Host may carry two workers while running no lane; capacity host carrying lane or canonical suite takes none.

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

Populations distinct:
- `bulk_downloader/`: application Python.
- `tests/`: pytest, fixtures, test corpus.
- `tools/`: build and analysis tools.
- `toolchain/bin/`: extensionless tools.
- `frontend/`: SPA frontend source.
- `scripts/`: install, deploy, service automation.
- `project-knowledge/`: generated and current knowledge base.

Measure membership at decision time.

Before broad scan, name population, use `rg`/`git ls-files` or purpose-built tool. CAPTURE WHOLE TO DISK, READ A SLICE. A SECOND HAND-ROLLED HEREDOC IS A MISSING `bd-*` TOOL. And measure before optimising: use complete captured denominator, then inspect bounded slices, promote repeated logic into toolchain. Generated remote source = transport data: send UTF-8 bytes through ASCII-safe decoder, verify digest before publication; never embed in bootstrap heredoc; test delimiter-collision payload + decodable-corruption refusal at real transport seam. Parallel read-only discovery fine when authorized, but one integrator, one writer remain. Local-model or worker classifications = proposals, never tests, reviews, absence proofs, merge approval, deployment authority.

Before claiming completion, audit objective requirement by requirement against current files, tests, CI, PR, merge, deployment, roadmap evidence. Don't redefine completion around work already done.
