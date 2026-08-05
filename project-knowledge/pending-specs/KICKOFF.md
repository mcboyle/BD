# KICKOFF -- resume the 2026-08-03/05 session after a compaction

WHAT THIS IS. A session resume note, written 2026-08-05 at branch tip
`9e5520c`. It exists so a compacted session can re-enter the work without
re-deriving state that was already measured.

WHAT THIS IS NOT. **Not a second agent-facing contract.** CLAUDE.md section 8:
there is ONE, and a second document an agent reads before acting is the defect,
not a resource. Read CLAUDE.md. Read this to find your place, then RE-DERIVE
before you act -- every figure below is a measurement with a timestamp, and
section 1 says measurements go stale silently and are then read as authority.

VERIFY FIRST, always:

```bash
git -C /home/user/BD log --oneline -1
```

This container reverted its checkout three times on 2026-08-04. One source read
against a stale tree produced a confidently wrong conclusion about a fix that
was present and shipping. If HEAD is not what this file says, that is the first
thing to reconcile, not a detail.

---

## 1 | Where the tree is

| fact | value | how to re-derive |
| --- | --- | --- |
| version | `3.66.867` | `grep __version__ bulk_downloader/__init__.py` |
| `main` | `3fded70` (register 15.29, PR #169) | `git log --oneline origin/main -1` |
| branch | `claude/bulkdownloader-handoff-j9o59v` | -- |
| branch tip | `9e5520c` | `git log --oneline -1` |
| ahead of main by | 3 commits, **all artifact-only** | `git log --oneline origin/main..HEAD` |

The three commits (`a25e577`, `f6910f3`, `9e5520c`) are successive saves of one
markdown file, `project-knowledge/pending-specs/recon-queue-items-1-9.md`. They
carry no source change.

**They do NOT get their own PR.** Matt's instruction: roll them into the first
real cut (item 1). So item 1's PR opens carrying four commits, three of which
are the spec.

## 2 | What shipped, and what the box has actually seen

Nine cuts merged this session, v3.66.861 through 867 plus two register
sections. PRs #162-#169, every one CI-green.

**Box evidence -- reported by Matt, not measured here** (CLAUDE.md section 9:
do not claim a state on the box you have not been told):

| what | result |
| --- | --- |
| `./capture.sh` at v3.66.862 | PASS, `14591 / 14506 / 0 / 85` |
| `./capture.sh` at v3.66.866 | PASS, `14608 / 14523 / 0 / 85` |
| band at v3.66.867 | 139 passed |

The two captures reconcile exactly: **+17 collected, +17 passed, skips
unchanged at 85**. That is the arithmetic section 5 asks for -- if the totals
had not reconciled, more had changed than either cut claimed.

**v3.66.867 has NOT had a full capture.** Only its derived band ran on the box.
A deploy + capture on 867-or-later is operator-side pending and is the only
binding evidence for the release-gate-adjacent work below.

## 3 | The corpus incident, in one paragraph

A census of 1658 wacz/archive files found **228 carrying secrets (13.8%)**. The
distribution was B4 197, B1 22, live checkout 5, bd-archive-2026-08 4, B2 0.
Files literally named `.redacted`, and a directory named `from_scrub_manifest/`,
both certified files that carried secrets. All 228 were scrubbed, proved clean
twice, and the originals deleted; the re-census returned **1658 SAFE / 0 SECRET
with the total unchanged**, which is the 1:1 replacement proof.

**No predictor existed** -- not date, not path, not subdomain, not label. That
is the finding worth carrying: `bd-archive-exec.py`'s `wacz` subcommand selects
by the `.redacted` label and is therefore **known-defective; do not use that
subcommand.**

## 4 | The nine queued items

Full plans, evidence and three adversarial lenses:
`project-knowledge/pending-specs/recon-queue-items-1-9.md` (281 KB).
Twelve agents, 0 errors, ~1.64M subagent tokens. Plans measured at `3fded70`,
lenses at `a25e577`.

**Verdicts: 7 REAL, 2 PARTIALLY-REAL, 0 NOT-REAL, 0 CANNOT-EVALUATE.**

| # | subject | verdict | size |
| --- | --- | --- | --- |
| 1 | `bd-parband` attributes a verdict to a suite it never ran; `.bd_last_band.json` unignored | REAL | M |
| 2 | `test_pk_mirrors_do_not_drift.py` does not fire | REAL | M |
| 3 | `bd-opv`, `bd-env-report-check`, `bd-equiv`, `bd-fullsuite` can report a verdict while blind | REAL | L |
| 4 | `bd-claim` is inert from a shell (keyed on `os.getpid()`, 9 occurrences) | REAL | M |
| 5 | `ai_boot_readiness.json` has no in-flight marker | REAL | M |
| 6 | CLAUDE.md section 6 owes a line: an interrupted `bd-mutate` does not restore the tree | REAL | S/M |
| 7 | the 12-tool retirement, ~20 references across 7 surviving tools | **PARTIALLY-REAL** | L |
| 8 | `bd-band` carries zip-era `/home/claude` paths | **PARTIALLY-REAL** | M |
| 9 | `capture.sh` does not record the commit it captured | REAL | M |

**Read the two PARTIALLY-REALs before trusting their queue entries.** Both were
filed as flat statements and came back qualified. A plan is the artifact; the
queue line is not.

### The lenses found things the plans did not

Do not skip these -- they are the reason the review was run:

- **Three plans reach for a RED that fails for the wrong reason** (item 2's
  RED-B, item 5's RED-4, item 8's TEST B): a `TypeError` from a
  not-yet-existing parameter, while the plan states a behavioural reason for
  the red. Each has a strictly better assertion available against the shipping
  signature. This is the mirror of the usual failure -- not "a test that passes
  the moment you write it" but "a test that fails the moment you write it, for
  a reason unrelated to the defect". Both leave you without evidence.
- **Four plans propose a gate whose denominator is a hand-written list of
  names.** Only item 2 derives it from the tree. A hand-list is how every defect
  in this batch survived; three of the four fixes re-adopt it at a larger
  constant. Section 0, reproduced by its own fix.
- **Three plans copy an existing "good precedent" without auditing the
  precedent** -- and each precedent has a silent failure branch. Read the
  precedent's failure branch before reusing it.
- **Item 3 has a hard band gap:** it is missing
  `tests/test_pk_mirrors_do_not_drift.py`, and three of its four subject tools
  carry tracked byte-identical `project-knowledge/` mirrors. `bd-band-derive`
  cannot see mirrors -- items 1, 2 and 8 each measured that blindness
  independently.
- **Item 4 is missing `tests/test_v3_66_653_dep_freshness.py`**, which lens 3
  established is an **ELEVENTH axis-6 gate**. CLAUDE.md section 4's table says
  ten and is stale. Its `_tracked_py` keeps every tracked `.py` **plus** every
  tracked file with a python shebang, so the 469 extensionless `bd-*` scripts
  are in its denominator.

### Two decisions still owed by Matt

1. **Item 2 as one cut or two.** The recon agent recommends splitting it.
2. **Item 6 part (b)** -- adding a `bd-mutate` journal and `--recover`. That is
   a tool behaviour change and CLAUDE.md section 9 puts it behind explicit
   per-task authorization. Part (a), the CLAUDE.md prose, is free.

Item 9 carries the same constraint and it is sharper: `capture.sh` **is** the
release gate, so both its cuts need Matt's go, and only his box run is binding.

## 5 | After 1-9: the order Matt chose, by speed x blast radius

He explicitly approved **splitting s5 into three cuts**.

| tier | work | why here |
| --- | --- | --- |
| 0 | item 13 re-scope recon | zero blast radius, runs concurrently with anything |
| 1 | item 14a (`regen_nfos_from_history:474`); s5 **prose** cut (~71 files) | large file count, no semantic risk |
| 2 | retire `scripts/build_release.sh` | adds a 4th axis-6 `*_stays_retired` gate, moves the `.sh` enumerator, needs section 4's table corrected |
| 3 | s5 **code** cut (34 files); item 19 driven toward under-150 | real code, bounded |
| 4 | s5 **tests** cut (19 files, read individually); items 14b-d | tests are a denominator -- see section 4 |
| last | the session-close register section | must be last; `bd-freshcheck.check_session_close_tip` grades the LAST title containing "close" |

That last row is why register 15.29 is deliberately titled as a mid-session
record and not a close.

## 6 | Operator-side, waiting on Matt

- Deploy + `./capture.sh --workers=$(nproc)` on 867 or later, exit code
  captured **unpiped**. Re-pin the graph hash **before** capture or step [2b]
  reports drift and `capture_verdict.py` turns it into a whole-capture FAIL.
- The archive sequence he approved: 91 dirty `.db` recover-then-evaluate,
  rebuildable bulk delete, then consolidate.
- Scripts already delivered to him as files: `probe11.py` (read-only `mode=ro`
  SQLite probe), `bd-wacz-survey.py` (read-only), and `bd-archive-exec.py`
  (dry-run default -- with the `wacz` subcommand defect noted in section 3).

## 7 | Container traps this session actually hit

Not theory. Each of these cost a wrong conclusion or a wasted round trip:

- **The container reverts to a Jul-28 base image on restart.** `lxml` and other
  deps disappear. `.claude/hooks/session-start.sh` (shipped v3.66.865, made
  delegating in 866) now probes both requirement manifests and delegates repair
  to `scripts/cloud-setup.sh`. It **never resets the tree**.
- **`bd-mutate` killed by the 2-minute tool timeout LEFT A MUTANT IN THE
  TREE** -- twice. Run it detached, with `PYTHONDONTWRITEBYTECODE=1`, and grep
  for the mutant text before believing a postflight.
- **Reading a tool's source on a rolled-back tree** produced a wrong conclusion
  about a `.warc` fix that was present. Section 1's rule, in this container.
- Interpreter is `venv/bin/python`. Bare `python3` is 3.11 without project
  deps and gives you seven failures that do not exist.

## 8 | The one-line restart

```bash
git -C /home/user/BD log --oneline -1        # expect 9e5520c or later
sed -n '1,30p' project-knowledge/pending-specs/recon-queue-items-1-9.md
```

Then start item 1. Its plan is complete, its RED is proven (a green `PASS` with
`passed=5` minted for a file that does not exist), its band is 18 suites, and
it carries one requirement `bd-band-derive` cannot tell you: `bd-parband` has a
tracked byte-identical mirror at `project-knowledge/bd-parband`, and editing
one without the other turns `test_pk_mirrors_do_not_drift.py` red **in the same
cut**.
