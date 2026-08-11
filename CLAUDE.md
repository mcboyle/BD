# CLAUDE.md — operating contract for BulkDownloader

You are working on **BulkDownloader (BD)**: a self-hosted Flask + Playwright +
React/TypeScript SPA batch video downloader. Single developer/operator (Matt),
single deployment target: headless host **`test4`** — verified from `uname` and
the `mboyle@test4` prompt in a capture, deploying to
`/home/mboyle/BulkDownloader`.

Older prose here, in `project-knowledge/`, and in the SDD reports calls that box
`stash`. That is a saved PuTTY session name, not a hostname
(`.superpowers/sdd/wacz-processing-report.md` records the session alongside the
same `mboyle` user). `stash` and `test4` are **the same machine** — two
sessions spent time treating that as an open question and it was never one.

**A SECOND BOX NOW EXISTS, AND IT IS A DIFFERENT CLAIM FROM THE ONE ABOVE.**
The operator is standing up a fresh Linux VM alongside `test4` (v3.66.1024).
So "there is no second box" is retired as of that cut — it was always about
`stash` being a PuTTY session name, never a promise that one host was
permanent, and the two readings are easy to conflate in exactly the direction
that wastes a session. What survives unchanged is the reason it never mattered:
nothing in any `.py` or `.sh` resolves, connects to, or branches on a hostname
(re-derived at v3.66.1024 — the only hit for `gethostname|platform.node|uname
-n` in tracked sources is `live_tests/harness.py:251`, interpolated into a
report string and never branched on). The deploy is still `git fetch` +
`git reset --hard` run *on* whichever box you are on.

**So a reading is now about a HOST as well as a commit.** Section 2b already
says a finding is about a commit and you must say which; with two boxes the
same is true of the machine. `docs/repo/FRESH_HOST_BRINGUP.md` is the operator
runbook for the new one and records what must be migrated by hand — it is
OPERATOR-facing, not a second agent contract, and section 8's rule that this
file is the only agent-facing contract is unchanged.

Read this file fully before your first edit. It encodes rules that were learned
by breaking things, not by preference. Where a rule looks arbitrary, it is
usually load-bearing and the reason is stated.

---

## 0 | The one rule that generates most of the others

**A gate that cannot see the thing it is asked about reports OK — and that is
worse than no gate.**

A check asserts over a denominator that structurally excludes its subject, so it
reports clean: truthfully, and uselessly. Real instances from this codebase:

- A band tool didn't count `.tsx`/`.ts` as source, so it reported "changed
  source (0)" on a real frontend cut.
- `test_gui_parity` asserted `all(key.startswith("BD_"))` over a scan that
  matched on that prefix — so unprefixed vars were invisible, and the test
  certified none existed.
- A deploy manifest reported "no orphans" while examining only source files
  rather than the full tree.
- A capture check inspected the chromium build while `launch(headless=True)`
  actually executes the headless *shell*.

**Fix pattern, every time:** make the denominator contain the subject; derive
reachability rather than assert it; and make a check that cannot verify *say so*
— **unknown is a third state, and it fails.**

**The inverse is equally damaging: a gate that fires on identity.** A manifest
pin once hashed bytes that included a wall-clock `generated` field, so an
unchanged tree "changed" every run. Two sessions nearly reconciled a diff that
did not exist. A gate that cries wolf gets switched off, so over-sensitivity is a
soundness bug, not a safe default. Attest over **content**, not bytes.

**THE FIX REPRODUCES THE SHAPE OF THE DEFECT. Audit it before you ship it.**
This is the highest-yield rule on the page and it was learned five times in one
session (v3.66.847/848). Every one of these was written BY someone who had just
read this section, in order to fix an instance of it:

- `tools/check_requirements.py` exists to replace `pip check`, whose
  denominator is the set of INSTALLED packages and so cannot see an
  uninstalled requirement. It shipped returning **0 with silent stdout** for a
  file that parses to zero requirement names -- "every entry resolves", over
  nothing.
- The declaration gate written to constrain that fix shipped with a
  denominator excluding `tests/`. Repaired, it then claimed to cover
  `toolchain/` -- where **231 of 2376** tracked Python
  files are extensionless `bd-*` scripts a `*.py` glob cannot see. Repaired
  again, it still cannot read the 17 tracked shell scripts that embed Python
  heredocs.
- `scripts/deploy.sh`'s BD_HOME warning was gated on BD_HOME being **exported**
  while `capture.sh:55` DEFAULTS it, so it stayed silent in the common case.
- A residue report's own new field (`twins["blind_pages"]`) was declared and
  **never written**, so the JSON said no page went blind while the verdict
  beside it said `conclusive: false`.
- One fix's shadow-detection filtered on "installed under `_REPO`" -- and
  `venv/` lives inside the repo, so it filtered out every site-packages
  distribution and the map came back empty.

The last two were caught by the fix's OWN non-empty-denominator assertion, on
first run, before review. That is the whole argument for writing the assertion
*before* the verdict: it is the only thing that reliably catches this, because
the author has just convinced themselves the logic is right.

**CREATING A PATH IS A PROMISE TO REMOVE IT, AND NOTHING GATES THAT PROMISE.**
Measured at v3.66.1035: the stage-1 socket recorder created its run directory
when it ARMED -- every pytest invocation -- while it only WROTE when something
called out. A clean run therefore left an empty directory, and nothing anywhere
removed them: **744 of them under `/tmp` after one session**, on every host,
growing with every band and every capture. It was invisible because it lives
outside the repo, so `git status` stays clean, no gate enumerates it, and the
cost is a slow leak nobody trips over until a disk does.

The shape generalises past this instance: an artifact created unconditionally
and consumed conditionally leaks by construction. Create lazily, at first use,
so the footprint of a run that found nothing is nothing -- and bound the
retention of whatever you do keep. Ask of any new path: **what removes this,
and when?** If the answer is "nothing", that is the defect.

**A COMMENT IS INSIDE THE DENOMINATOR OF EVERY GATE THAT READS SOURCE TEXT.**
Four times in the v3.66.876-879 session an assertion could not tell prose from
code, and each was written by someone who had just read this section:

- a test forbade a literal that its own explanatory comment contained;
- a comment quoted the dead interpreter path and tripped the test written in the
  same commit;
- the comment explaining why `RELEASE_WORK` is *un*prefixed spelled the prefixed
  name in order to say it had been removed —
  `config_surface_inventory.py:_scan_shell_env` regexes the whole file with no
  comment stripping, so the comment re-entered the env ledger and failed the
  gate the rename had just fixed (**CI caught it, not review**);
- an assertion that `requirements-test.txt` is graded read the name out of the
  comment explaining the fix, so a mutant grading only the core manifest stayed
  green (**`bd-mutate` caught it, not review**).

**And a line-scoped assertion about a shell LOOP is wrong in both directions.**
Three times across v3.66.879/880: a script grading several manifests writes
`for X in a b c; do ... "$X" ...; done`, so the literals sit only on the `for`
line. A per-line check therefore fails a CORRECT implementation for its form
(two round trips), and the inverse check — "must not set CORE_FAILED" — passes
**silently**, because the body names no literal. That one escaped its mutant.
Both shapes are now mechanized in `tests/shell_source.py`
(`shell_code_only`, `blocks_containing`); use it rather than hand-rolling a
fourth copy.

Explaining a removal by naming the removed thing recreates it. Assert over
**comment-stripped** source, and cite the mechanism rather than the literal —
the discipline §7 already states for secrets, generalised. Note which instrument
caught each: review caught none of the four.

**A SUBPROCESS HARNESS THAT COPIES `os.environ` CANNOT TEST THE ABSENCE OF A
FLAG THE BAND SETS.** Measured at v3.66.926, and it is the cheapest instance of
section 0 yet found. A test existed specifically to check the path where
`BD_DISABLE_KEEPALIVE` is UNSET -- its docstring said so, in as many words --
and its helper built the child env with `env = dict(os.environ)`, popping only
`BD_TEST_MODE`. Every band in this repo runs
`BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest ...`, and `tests/conftest.py`
sets the same flag in an autouse fixture, so the child inherited it and the test
written for the unflagged half exercised the flagged half. It passed. It could
not have failed. Two real defects shipped behind it and were found by an
adversarial agent instead.

Generalise it: **a test that varies an environment variable must POP that
variable, not merely refrain from setting it** -- the parent's value is part of
the denominator. The same applies to `subprocess` without `env=`, to
`pytest.MonkeyPatch.setenv` without a matching `delenv`, and to any harness
that inherits ambient state it is supposed to be controlling.

**TO ASK WHETHER IMPORTING SOMETHING TOUCHES A RESOURCE, MONKEYPATCH THE
RESOURCE -- DO NOT READ.** Same cut, and the numbers are the argument: reading
`app.py` found ONE of four module-scope database writers. Wrapping
`sqlite3.connect` with a stack-trace recorder and importing the module in a
fresh subprocess found all four, including one ~1700 lines below the obvious
block and two that only fire when a flag is unset. The instrument fixes the
denominator here exactly as AST does for imports (section 1): a reader's
denominator is "the code I thought to look at", which is never the whole module.

```python
_real = sqlite3.connect
def traced(*a, **k):
    if str(a[0]) != ":memory:":
        hits.append(traceback.extract_stack()[:-1])
    return _real(*a, **k)
sqlite3.connect = traced
import the_module_under_test        # in a SUBPROCESS, with cwd + env controlled
```

Run it with the gating flag both set and unset. Three of the four sites here
were invisible in one of those two configurations.

**THIS FILE IS IN THE GATE'S DENOMINATOR AND ITS CLAIMS STILL GO STALE — AND
THE PARAGRAPH THAT USED TO SIT HERE IS THE PROOF.** Until v3.66.944 this space
carried a heading reading "THE FRESHNESS GATE CANNOT SEE THIS FILE", measured at
v3.66.927 from a doc-truth line reporting "65 document(s) scanned in
`project-knowledge`". The gate was later widened to cover root documents and
nothing updated the paragraph, so the strongest statement of the
documents-go-stale rule in this file spent releases being an instance of it.
Re-measured at v3.66.944:

```
bd-freshcheck : _DOCS names CLAUDE.md and project-knowledge/SESSION_CARRY.md
bd-doc-truth  : docs_scanned=78  (65 in project-knowledge + 13 root documents)
                excluded=[CHANGELOG.md]   stale_count=0
```

**But the conclusion survives the correction, because the denominator is not the
predicate.** Both checks ask whether a cited **path** still resolves. Neither can
ask whether the cited line still SAYS what the sentence around it claims —
`bd-freshcheck` prints that limit itself on every run ("this covers the DERIVABLE
half of staleness only"). So a claim about *behaviour* passes both gates
untouched, which is exactly how section 5 asserted for weeks that
`check_requirements.py` "calls `version(name)` and discards the result —
specifiers are never compared… **Open, and nothing here can see it**" while the
tool had already grown `Requirement()` + `specifier.contains()`.

That was caught only because a register re-derivation **ran the tool instead of
quoting the note**, and this correction was caught the same way. It remains the
only defence: this file's *factual* claims are unverified by machine even now, so
re-derive before citing one, **including from here** — and note that the sentence
you are most likely to trust is the one that has been re-read most often without
being re-measured.

**Reading this section does not inoculate you against it.** The same session
that wrote the five items above also re-derived an import census with `grep`
after reading section 1's warning about exactly that, and got it wrong in both
directions. Treat "I have read the contract" as worth nothing; treat "I ran the
check and pasted the output" as worth everything.

---

## 1 | The method rule

**Documents go stale silently and are then read as authority.**

Every figure taken from a register, tracker, changelog, or grep has at some point
been wrong. Every figure obtained by *running the tool* was right.

- **Verify-then-act.** Before working any queued item, re-derive its status from
  source. Historically ~half of a stale register's "open" items are already
  closed or mis-scoped.
- **Grep is not a denominator.** For import/symbol questions use AST. A grep for
  playwright importers was wrong in *both* directions simultaneously — two files
  matched the string with no import node, and two real importers were invisible.
- **AST is not automatically better.** An AST re-derivation returned 13 instead
  of 12 because the predicate was `'playwright' in name`, which also matches
  `playwright_stealth` — a different distribution. **The instrument fixes the
  denominator; the predicate fixes the subject. Say which you used.**
- **`git ls-files -- '*.py'` is NOT "the Python files in this repo."**
  Re-measured at v3.66.943: **2145** files end in `.py`, and a further **231**
  are tracked, python-shebang, extensionless `bd-*` scripts — **all of them
  under `toolchain/`**, none anywhere else. Total **2376**. A `*.py` glob
  reaches essentially none of the 231.

  **THE PREDICTION IN THIS BULLET WAS HALF RIGHT, AND THE HALF THAT MISSED IS
  THE LESSON.** The v3.66.933 revision said the retirement would leave "231 and
  the total 2365". The 231 landed exactly. The total did not: it is 2376,
  because `.py` grew 2141 -> 2145 while the retirement was pending. A figure
  predicted from a subtraction goes stale the moment any OTHER term moves, and
  nothing warns you — the sentence still reads as a measurement. **Re-derive
  rather than quote, including a number this file told you to expect.**
  Section 8 already says `toolchain/bin` is
  its own population; this is what that costs you when the enumerator forgets.
  Type on the shebang as well as the extension, or state that the extensionless
  population is excluded.

  **The word "executable" was WRONG here for weeks, and the way it was wrong is
  the point.** This bullet used to say "tracked, **executable**, python-shebang"
  — but only **232** of those carried mode `100755`; the other **224**, almost
  all of `project-knowledge/`, are tracked `100644`. An auditor who filtered on
  the exec bit — as the prose instructed — measured **1** file under
  `project-knowledge/` and concluded the paragraph had rotted by 200x. It had
  not. The predicate was over-specified, and *the prose was the thing that
  over-specified it*. Three successive readings of this one bullet were wrong
  during a single audit (ignoring `bd-*`; requiring `100755`; then reading the
  survivor count as drift), which is section 1's own lesson landing inside
  section 1's own worked example. **Filter on the shebang, not the mode.**

  *(Unverified: an earlier revision claimed "seventeen tracked shell scripts
  also embed Python heredocs no AST walk will ever read". Two independent
  predicates find **3** at `803a39a`. The CLASS is real — a heredoc is
  invisible to an AST walk over files — but the count did not reproduce, so it
  is stated as unknown rather than corrected to a number nobody has stood
  behind.)*
- **A GREP OVER PROSE MISSES ANY PHRASE THE LINE WRAP SPLIT.** Hit THREE times
  in the v3.66.915-927 session, every time while verifying that something was
  recorded. `grep 'Four frames'` returns nothing for a document containing
  "Four\nframes", so the checker concludes UNRECORDED and rewrites a note that
  already exists — or, worse, concludes a finding was lost and goes looking for
  it. Twice it produced a confident "*** UNRECORDED ***" for text sitting in the
  file. Normalise whitespace before asserting a phrase is absent
  (`tr '\n' ' '`, or a regex with `\s+` between words), and treat a
  single-token probe as a floor rather than an answer: `grep '97'` matches any
  number containing 97.
- **AND A PREDICATE OVER THE WRONG PART OF THE SYNTAX IS WORSE THAN A GREP,
  because it looks rigorous.** Same session: an AST scan for "tests that write
  to the ambient database without isolation" examined each test function's
  PARAMETER LIST for a fixture name, and reported **97** offenders. The measured
  truth was **2**. Isolation established inside the body — `db.DB_PATH = ...`,
  `mkdtemp()`, a module-scope fixture — is invisible to a parameter check, so
  the denominator was right and the predicate was wrong. The tell was
  arithmetic: 97 leaking tests cannot produce a 2-row delta. **When an
  instrument's count disagrees with a direct measurement by an order of
  magnitude, the instrument is wrong — check it before acting on it.**
- **Say which denominator a count is over, in the same sentence as the count.**
  A requirements note stated its instrument as "every tracked .py file (2108
  files)" and two lines later said "All three importers" — a number true only
  of a `bulk_downloader/`-only subset. Both halves were honest and the pair was
  not. When the denominator later grew to 2577, the count had to be
  **re-measured rather than assumed**; it happened not to move, and that is a
  fact you can only state after checking.
- Numbers that move (tool counts, coupling, retirement pools) must be measured at
  decision time, never quoted — **including from this file.**
- **A verification can answer a different question than the item asks.** This is
  section 0 applied to investigations rather than to code: an inquiry whose
  denominator excludes the subject reports clean — truthfully, and uselessly. An
  item titled "call-time protected roots follow a relocated HOME" was verified
  NOT-REAL on genuinely strong evidence (17 passing tests, an AST scan finding
  zero freeze-style bindings, a counterfactual turning six tests red). All of it
  true, and all of it about "are the roots frozen?" — a different question. A
  direct probe of the title's actual claim showed the guard abandoning the
  operator's fixed path the moment HOME moved. **Re-read the item's own words
  against what you measured before writing NOT-REAL**, and treat a closure as
  the higher-stakes verdict: a wrong REAL costs a wasted cut, a wrong NOT-REAL
  closes the item permanently. The tell was in plain sight — the verification's
  own evidence stated the defective behaviour verbatim and graded it correct.
- **AUDIT BEATS RECOLLECTION, AND THE GAP IS NOT EFFORT.** Measured at
  v3.66.1035 on a review of the session that had just ended. Round one was
  written from memory and produced 35 plausible, well-argued improvements and
  **zero** live defects. Round two was `ls`, `rg` and `ps` against the actual
  tree, hosts and `/tmp`, took twenty minutes, and found **six real defects,
  three of them shipped that same day** -- 744 leaked directories, two gates
  wired into no CI shard, and a session-close ledger already contradicting the
  inventory. None of the three was in the list of 35.

  The reason is section 0's, applied to introspection: recollection's
  denominator is "what I remember doing", which structurally excludes the thing
  you did without noticing. So when the question is "what did this change
  leave behind", the answer comes from enumerating the tree, the processes and
  the paths outside the repo -- never from thinking harder. After any cut:
  untracked files, `/tmp` growth, orphan processes on every host, service
  health, and whether the register's newest close still agrees with its
  inventory.
- **Read the callee before you call it.** Guessing a signature, a fixture's
  columns or a dict's keys and then debugging the failure is always slower than
  opening the definition, and it produces a worse failure: a call with the wrong
  shape often *succeeds quietly* on the wrong thing rather than raising. Both
  shapes were hit in one session — `execute_plan` reads `from`/`to`, so a plan
  built with `src`/`dst` was **silently skipped** and its test passed for the
  wrong reason; `bulk_move` preserves the basename, so an assertion that the old
  filename stops matching asserted something false. Two more were columns that
  do not exist until some other module's `_ensure_tables()` lazy-adds them
  (`library_id`, `retention_excluded`) and one that lives on a different table
  (`retry_after` is on `queue`, not `history`). `grep -n 'def <name>'` costs
  seconds; each of those cost a round trip and one of them nearly shipped a
  test that proved nothing.

---

## 2 | Release discipline (non-negotiable)

1. **RED-first TDD.** Tests proven failing on pristine source *before*
   implementation. A test that passes the moment you write it has proven nothing.
2. **Seven SHA-pinned guard files** must stay byte-identical unless the operator
   explicitly declares a new SHA:

   | File | sha256 (16) |
   | --- | --- |
   | `bulk_downloader/extraction_core.py` | `5b6248a5c9e664ab` |
   | `bulk_downloader/session_capture.py` | `547d70c95cde9377` |
   | `bulk_downloader/dom_capture.py` | `0559903d0b159162` |
   | `bulk_downloader/dom_recorder.py` | `1657d0a0e39917ae` |
   | `bulk_downloader/capture_bodies.py` | `6c7f5c9a87510cca` |
   | `tools/capture_session.py` | `27be68b965689317` |
   | `tools/build_release.py` | `be25241eb867b85a` |

   *(Pinned at v3.66.805. Re-derive with `bd-guardcheck`, which reads
   `guards.json` — the single source of truth, hashed from the files. A
   `BD-GATE-UNRUNNABLE` message or exit 2 means the pins were **not** verified;
   do not proceed on it. Do not trust this table after the next cut.)*

   Until v3.66.818 `bd-guardcheck` reported `0 ok, 0 drifted, 7 missing` and
   **exited 0** on a clean tree — it could not see the files it certifies, and
   said so with a success code. A zero-in-every-bucket summary is a failure
   signal, not a pass.
3. **One feature per cut.** Clean blast radius beats a convenient batch.
4. **No speculative code** before reading the relevant source.
5. **The band is absolute.** A band failure means fix the tree or fix the
   environment — never explain it away. It has caught real design regressions
   that the feature's own test could not see.

Before packaging a change for review, regenerate all tracked artifacts from the
repository root and keep the resulting diffs in the review package:

```bash
venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
```

This command does not re-freeze intent baselines; declaration flags remain
explicit operator decisions.

### 2a | The cut-to-merge loop: failures that recur

Every rule below cost a red CI run, a red capture, or a wasted round trip
during 2026-08-01/02 (v3.66.833-840). None was a code defect; all were
process. They are grouped by where they bite.

**Regenerating artifacts.**

- **Regen AFTER the last source edit, not before.** `bd-regen-order` was run,
  then one more line changed in `db.py`, and `FUNCTION_INDEX.md` shipped with
  pre-fix line numbers. CI's check is `bd-regen-order` followed by
  `git status --porcelain` over the six generated artifacts, so it caught it —
  after the push. A generated file is only true for the tree that generated it,
  and nothing warns you: the file still exists and still looks plausible.
- **Untracked files from OTHER cuts contaminate the regen.**
  `tools/build_pin_index.py` counts `(root/'tests').glob('*.py')`, which does
  not care about tracking. Three RED test files staged for later cuts inflated
  `test_files_scanned` by 3. Move out-of-scope work OUT of `tests/` before
  regenerating, or the artifact describes a tree you are not shipping.

**Gates that cannot see untracked files.** Five of the seven axis-6 gates
enumerate `git ls-files`. A NEW test file is therefore invisible to them until
it is staged, so **their pre-merge pass proves nothing about that file**.
Measured: v3.66.839's test added a fixed-width source window, its band went
green, and `test_source_windows_do_not_shift` went 115 -> 116 the moment the
file landed on `main` — failing the NEXT cut's band for a defect introduced by
the previous one. Either `git add` before the final band run, or expect the
ratchet to fire one cut late. When it does, remove the window rather than
raising the baseline; that gate is one-directional by design.

**Git hygiene after a merge.**

- **`git fetch --prune` must accompany every post-merge reset.** GitHub's
  auto-delete removes the head branch, but the local `origin/<branch>` ref
  survives and becomes a dead baseline. The stop hook diffs against it and
  reports the squash commit as unverified work — three times, before the cause
  was found. The remedy it suggests (`--amend --reset-author`) would rewrite a
  commit on `main` that the deploy host resets to.
- **A refspec-scoped prune collects nothing else.** `git fetch --prune origin
  main` leaves every other stale ref in place. Use bare `git fetch --prune
  origin`.

**Test harnesses that misreport themselves.** Both of these make a harness
defect look like the defect under test, which is the same shape as a gate that
cannot see its subject:

- **Discriminate the exception you are hunting.** An end-to-end probe caught
  `AttributeError` and reported it as the bug — but an incomplete stub raises
  `AttributeError` too. On a tree where the bug was fixed, that test would
  still have "proven" it present. Match on the message and re-raise anything
  else.
- **Cut extracted source on STRUCTURE, never on a fixed width.** A harness that
  sliced a shell branch swallowed its closing `fi`; the fix then added a nested
  `if/else` and the extractor cut mid-construct. Both produced bash syntax
  errors presenting as subject failures. Cut on indentation or on a balanced
  delimiter.

**Subagent output is data, not evidence.** A verifier returned
`{"status":"PASS","summary":"test","evidence":["a","b"]}` — a placeholder, no
work done. Read what an agent returned before counting it as verification; a
result that is present is not a result that is substantive.

### 2b | Running work across several agents

Earned at v3.66.847/848, where nine workflows ran against one branch. Every
rule here cost real rework, and the first two cost the most.

**A subagent worktree comes up at the SESSION'S BASE COMMIT, not at your
branch tip.** Measured: four worktrees in one session all checked out the
pre-cut base. The failure is silent and it has two flavours, both worse than a
crash:

- Two mutation agents found the feature absent, **reimplemented it
  themselves**, and mutated their own code. Their escape lists were precise,
  well-evidenced measurements about code that was not shipping.
- Two more landed on a commit that had the feature but not its later fixes,
  and reported defects that were already closed. Acting on that report means
  fixing a defect that does not exist.

A preflight of "the tests pass" does **not** catch this: on the old tree the
OLD tests pass, so it is green for the wrong reason. The fix that works, and
is now proven:

```bash
git fetch origin <branch> && git checkout --detach FETCH_HEAD
git log --oneline -1                  # say which commit you measured
grep -c '<a symbol the change introduced>' <the file you will mutate>
```

Assert the symbol is PRESENT before measuring anything. And note `venv/` is
gitignored, so it does **not** exist in a worktree — use the absolute
`/home/user/BD/venv/bin/python`, or a bare `python3` silently gives you 3.11
without the project dependencies (section 5).

**Never `git add -A` while another agent is writing the tree.** This is now
enforced rather than merely stated: `bd-claim add <paths>` declares what you are
editing, and `.githooks/pre-commit` refuses to commit a path another LIVE
process holds. `scripts/cloud-setup.sh` arms it (`core.hooksPath .githooks`);
on the box it is opt-in, one command. It is inert unless a claim is in flight,
so a single-operator session never sees it, and a dead claimant is reaped rather
than wedging the repo. Override with `BD_SKIP_CLAIM_CHECK=1` when you are sure.

The failure it models: a regen commit
swept a concurrent workflow's uncommitted RED battery into itself; the branch
tip then carried tests whose implementation was not committed and **failed its
own guard tests** until someone noticed. `git add <explicit paths>`, always,
and check `git status` first.

**A NEAR-MISS AT v3.66.926 SHOWS WHAT THAT RULE ACTUALLY BUYS.** A review agent
running a mutation probe appended this to `bulk_downloader/app.py` in the shared
working tree, and did not remove it:

```python
# --- MUTANT (latch): boot at module scope unless the flag is set ---
if not _os.environ.get("BD_DISABLE_KEEPALIVE"):
    boot_once()
```

That is a verbatim re-introduction of the defect the cut had just removed. It
landed between a `git add` and the next `git status`, so `git add -A` would have
committed it, CI would have passed it (no gate covers it), and the PR would have
shipped a fix that undoes itself. The explicit-path staging is the only reason
it did not. **A read-only-sounding "review" agent still has a writable tree** --
mutation, probing and bisection all edit files. After any agent run, `git diff`
before you believe your own tree, and sweep for stray files as well as stray
edits: the same run also left a copied `tests/test_*_probe_*.py`, which
`tests/`-enumerating axis-6 gates and `build_pin_index.py` would have counted. One agent avoided this correctly by staging a
blob derived from `git show HEAD:CHANGELOG.md` rather than adding the file.

**`git fetch --prune` does NOT delete the LOCAL branch.** After a squash merge
the remote branch is auto-deleted and the prune collects the remote ref -- but a
local branch of the same name survives, still pointing at the PRE-SQUASH commit.
Push it and you re-open the merged work as a new PR: measured at v3.66.849,
where a stale local branch was pushed instead of the session's actual commit,
GitHub diffed it against its merge base, and the resulting PR (#146) was a
byte-for-byte duplicate of the one that had just merged. Delete the local branch
in the same breath as the prune:

```bash
git fetch --prune origin && git checkout -B main origin/main
git branch -D <the merged topic branch>
```

Nothing in the repo catches this -- the stop hook's "unpushed commit on main"
did. And the repair is section 7's two-dot diff, not `--force` alone.

**A finding is about a commit. Say which one.** Four bands, three mutation
batteries and five review lenses in that session each measured a different tip,
because the tree moved while they ran. A report that does not name its commit
cannot be acted on later, and "the band was green" is not a property of the
branch — it is a property of one commit on it.

**Give an agent the deviations, not just the plan.** The generated spec for
that cut had failed adversarial review; its amendments lived in the register.
Agents that were handed the spec alone reproduced its defects faithfully.

**A green band is not the absence of a regression.** v3.66.834's band was green
while the cut had introduced a false-SUCCESS path (a failed login reported as
succeeded, sending a worker at an expired jar). An independent adversarial pass
found it; the band could not, because no existing test covered the new
interleaving. Conversely v3.66.837's band DID catch its regression. Both are
normal: the band covers the denominator that already exists, and a new code
path is by definition outside it.

**Say which question you measured.** v3.66.837 claimed six call sites were
"live, MEASURED not assumed". What was measured was the CALL GRAPH; the sites
could not EXECUTE. Section 1's rule, in a commit message.

---

## 3 | Version bump = three edits, together

Never bump one without the others:

1. `bulk_downloader/__init__.py` → `__version__`
2. `tests/test_settings_center_slice4.py` → the `assert __version__ == "…"` pin
3. `CHANGELOG.md` → **ASCII-only** entry, prepended, anchored on the *previous*
   `## v…` header

Then regenerate `PIN_INDEX` (`venv/bin/python tools/build_pin_index.py`) and read
the `form == "version"` entries out of `PIN_INDEX.json` — do not assume the pin
list has stayed at one entry.

Do **not** reach for `grep -rnE '__version__ *== *"3\.66\.' tests/` here.
Measured at v3.66.838: **8 hits across four `.py` files**, of which exactly one
is a real pin (`tests/test_settings_center_slice4.py:200`). The other seven are
fixture string literals — `test_versync_gate.py` (3),
`test_release_hygiene_gates.py` (2), `test_scan_version_pins_fixture.py` (2) —
plus three `__pycache__` binary matches the raw grep also reports.
This paragraph itself said "five hits" and named only two of the four files
until v3.66.838; `test_versync_gate.py`'s three were invisible to it. A
worked example of the trap it is warning about, in the warning.
`build_pin_index.py` uses AST precisely so those fixtures are structurally
invisible to it. This is section 1's rule applied to this file: the instrument
fixes the denominator, the predicate fixes the subject.

CHANGELOG entries must be **ASCII-only**; an emoji trips a gate on the box.

---

## 4 | Band rules (which tests to run)

**A denominator change has the blast radius of the denominator, not the diff.**
Band every test touching the changed module.

**Derive it with `bd-band-derive`, NOT by hand.** This paragraph used to say
"derive it with `grep -rl`", and that instruction is what a whole session then
did — hand-rolling the same grep in eleven workflows, and getting a band that
was too NARROW every time. Measured 2026-08-03 on `tools/live_seed.py`:

```bash
venv/bin/python toolchain/bin/bd-band-derive --file <changed file>   # one file
venv/bin/python toolchain/bin/bd-band-derive --files a.py b.py       # a changed set
venv/bin/python toolchain/bin/bd-band-derive --emit                  # just the bd-band line
```

**RUN THE BAND WITH REAL PYTEST. Deriving is one question; running is another,
and the running half was answering with a shim until v3.66.950.**

```bash
BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest <the derived files> -q
```

`bd-band` and `bd-parband` now do exactly that and are fine to use. Before @950
they shelled out to `run_tests.py`, whose `run_tests_core` installs a
pytest-compatible **stub** into `sys.modules` whenever `pytest` is not ALREADY
imported — which at runner startup it never is — so the stub ran even on a
machine with real pytest installed, and test files doing `import pytest` got the
shim. Its own docstring says it is *"NOT a replacement for pytest in
production"* and exists for environments without pytest.

Measured at v3.66.949 across the whole suite, per-file isolation on both sides:

| | files |
| --- | --- |
| the shim reports non-PASS | 28 |
| of those, PASS under real pytest (verified 24/24) | **24** |

**86% of what the mandated band tool reported was manufactured.** One file, both
runners, same tree and interpreter: `tests/test_codex_handoff_stays_retired.py`
is `4 passed` under pytest and `IMPORT ERROR: No module named 'tracked_source'`
under the shim — it does not put `tests/` on `sys.path` the way pytest's rootdir
handling does, so the 22 files importing a sibling helper all die on import.
That is the floor; other divergence mechanisms account for the rest.

The tell was available the whole time and nobody pulled it: `bd-band` already
**refused to run** unless the interpreter could import pytest, reasoning that a
runner without it "would report failures that are interpreter artifacts, not
defects". Right conclusion, half applied — having proven pytest was there, it
ran the shim anyway.

*(A claim made while finding this and then retracted: the shim does NOT also
hide a real failure. `test_t14_vpn_probe_egress` passes in isolation under both
runners and fails only in a co-batched xdist run — that was a per-file-isolated
shim run being compared against a co-batched pytest run, blaming the runner for
an isolation difference. The case rests on the 24, which are measured.)*

| method | suites | missed |
| --- | --- | --- |
| `grep -rl live_seed tests/*.py` | 17 | — |
| `bd-band-derive --file tools/live_seed.py` | **25** | 0 |

A strict superset: 8 files the grep could not see, none dropped. It unions four
signals, and `grep -rl` is only one of them — the others are a filename-stem
glob, the curated `TOUCHED_FILE_TO_TEST.md` map, and declared COUNT-COUPLING
(a test that exercises a module without importing it or sharing its name). The
tool's own docstring says the module-consumer signal exists because the gap
"forced a by-hand `grep -rl <module> tests/` on every cut since. Now
mechanized." It was mechanized, and then a session did it by hand anyway,
because this file told it to.

Its output is a **floor, not a ceiling**: it says so itself, and everything else
in this section — axis-6 gates, source windows, the release-chore gates, a
deleted tracked file — still has to be added on top. Use it as the starting
point rather than as the answer, and use `grep -rl` only to check whether it
missed something, not instead of it.

- Route change → band **both** `test_route_index_in_sync` **and**
  `test_route_map_invariant`; re-freeze the baseline, then update
  `_BASELINE_SHA` at `tests/test_route_map_invariant.py:35` to the new file's
  sha256:

  ```bash
  PYTHONPATH="$PWD" venv/bin/python tools/route_map_snapshot.py \
      > tests/route_map_baseline.txt
  sha256sum tests/route_map_baseline.txt
  ```

  The redirect is required — `route_map_snapshot.py` writes to **stdout only**,
  it does not write the baseline file itself. `_BASELINE_SHA` is a constant in
  the test module, not an attribute of a `route_map_baseline` module; the `.txt`
  beside it is plain data. And **not** `env -u PYTHONPATH` — that strips the
  work tree from `sys.path`, produces an empty file, and silently overwrites the
  baseline. (Older copies of this rule said `PYTHONPATH=tree:/tmp/prestaged`;
  `/tmp/prestaged` does not exist here, and `venv` carries the deps.)
- Deleting a config key → band `grep "<key>" tests/`.
- Wiring a frontend control **is** a `ROUTE_INDEX` change (`spa_wired` flips).
  Regen order is **gui_parity before ROUTE_INDEX**.
- Any **new import edge** requires re-freezing the import-graph baseline in the
  **same** cut: `venv/bin/python tools/decomp/import_graph_gate.py --update`, and band
  `tests/test_import_graph_no_new_edges.py`. This is separate from regenerating
  `DEPENDENCY_GRAPH.json`.

  **INCLUDING AN EDGE FROM A TEST FILE, since v3.66.889.** The gate used to walk
  `bulk_downloader/` and `tools/` only, so 2132 of 3750 edges -- 57% of the real
  internal import surface, from 985 test files -- were outside the denominator
  it gated on. Adding a test that imports any product or tool module is now a
  baseline change, and `bd-band-derive` flags it: the gate and that flag widened
  in the SAME cut, because widening the gate alone would leave an author owing a
  re-freeze nothing told them about, failing on the box rather than in the
  sandbox. That is the shape recorded three paragraphs down, where
  `test_source_windows_do_not_shift` sat red on `main` for five releases.

  **So `test_import_graph_no_new_edges.py` is now an axis-6 gate too** -- a cut
  that touches no source at all still moves it. The table below predates that
  and does not list it.
- A `data_layer` route add must update **both** `test_wave2_backlog` **and**
  `test_v3_66_302_gui_parity_reconcile`.
- **Editing a function body — even adding a COMMENT — bands the fixed-width
  source-window tests that read that function.** They slice
  `src[pos:pos+3000]` from a `find("def <name>")` anchor, so anything you add
  above the assertion's target pushes it out of the window and `find()`
  returns `-1`. A grep for the *module* cannot reach them: they read source
  text and import nothing. Derive by **function name**, not module:

  ```bash
  grep -rln "def <function_you_touched>\|<function_you_touched>" tests/*.py
  ```

  Measured 2026-08-02: v3.66.835 added an 8-line comment to
  `start_manual_login`; `pause_site_keepers` moved 37 chars past the window
  and `open_manual_login_browser(` 15 past it, failing
  `test_v3_43_52_keeper_collision` **on the box** after four container bands
  called the cut green. Two more traps in that one file: `_bd_runner_src()`
  **concatenates** `runner.py` + every `runner_*.py`, so measuring the offset
  in the one file you edited is the wrong denominator; and the two assertions
  fail at *different* offsets, so fixing the first can leave the second red.
  Measure against the concatenation, and check every symbol the window asserts
  on. `test_source_windows_do_not_shift.py` ratchets the COUNT of these
  windows — it does not catch a shift inside an existing one.
- **Adding any `BD_`-prefixed name — including a shell local — bands
  `tests/test_gui_parity.py`.** Its env-var scan matches on the `BD_` prefix,
  so a script variable enters the ledger denominator and reads as "promoted
  but unledgered". v3.66.836 named a loop variable `BD_PROBE_PORT` in
  `install_service.sh` and failed the parity gate on the box. If the name is
  not a real config key, **do not prefix it**.

**Editing a DOC or a REGISTER bands the freshness gates, and no module-derived
band reaches them.** Measured at v3.66.879: the band derived for that cut was
correct for the code it changed, and CI still went red on a `SESSION_CARRY.md`
edit. `bd-freshcheck` reads *documents* — it resolves every `file:line` anchor in
the gating docs against `git ls-files`, and it requires the newest `### 15.N`
section whose title contains "close" to name a commit that is an ancestor of
HEAD. Neither subject is a module, so `bd-band-derive` cannot see them and
`grep -rl` on a changed `.py` cannot either.

Two failure shapes, both hit in one cut:

- **An anchor to an untracked path can never resolve.** Citing the launcher's
  `stop-hook-git-check.sh` under the agent home — a real file, deliberately
  outside the repo — with a `:NN` suffix is reported BROKEN, correctly. Name
  such a file *without* the suffix, as this bullet does, and say why.

  **Writing the bad form as an EXAMPLE is itself the bug.** The first draft of
  this bullet quoted the full `path:41-44` to illustrate what not to do, and
  failed the anchor gate on `CLAUDE.md` — the same shape §7 records for
  secrets, where naming one makes the document a place it lives. Describe the
  form; never spell it.
- **A section titled "…close…" must name a commit**, not a version. The
  predicate is **ancestry, not identity** — deliberately, because identity would
  go stale on the very next commit.

  **BUT NEVER NAME YOUR OWN UNMERGED BRANCH TIP: the squash DESTROYS it, and
  the gate goes red on `main` where no band reaches it.** Measured at
  v3.66.939. 15.59 was written naming `7db669c`, its own second branch commit.
  That is a genuine ancestor of the PR head, so `bd-freshcheck` passed, CI
  passed, and the PR merged green. The squash then wrote a NEW commit
  (`d670271`) and `7db669c` ceased to be in `main`'s history at all — so the
  push build of `main` failed on the register section that had just been
  certified. Green pre-merge, red post-merge, for a claim that was true when
  checked.

  15.58 survived the identical treatment because it named `b24e675`, the squash
  commit of the PREVIOUS PR — already on `main` when it was written, and
  therefore still an ancestor afterwards. **That is the rule: name a commit
  that is ALREADY on `main`** (the tip you branched from, or a
  previously-merged squash), never one that exists only on your branch. If you
  want the section to name the commit that contains it, the only safe moment
  is AFTER the merge — which means a follow-up edit, not a pre-merge guess.

  Nothing catches this before the merge, by construction: every pre-merge check
  runs on a tree where your branch tip still exists.

So when a cut touches `CLAUDE.md`, `project-knowledge/**`, or any tracked doc:

```bash
venv/bin/python toolchain/bin/bd-freshcheck --repo-only   # exit 0, or fix the claim
venv/bin/python -m pytest tests/test_toolchain_534.py     # the gate's own suite
```

**Naming trap:** `test_spa_wired_join_is_faithful` is a *function inside*
`tests/test_route_index_in_sync.py`, not a file. Passing it as a path makes the
runner fall back to a broad run → timeout → aborted cut. Band the **file**.

**Leak trap:** `test_phases_195_199` leaks `BD_INSTALL_DIR` — never co-band it
with `test_cut8_schedules`.

**The tests are a denominator too.** Some tests take *every tracked test file*
as their subject — ratchets and anti-drift gates that enumerate the tree rather
than import a module. **Adding or editing any test file changes their
denominator**, so they are in the blast radius of a cut that touches no source
at all. Deriving a band from "which tests import the module I changed" cannot
reach them, because they import nothing.

This is not hypothetical: `test_source_windows_do_not_shift.py` ratchets the
count of fixed-width source windows, #91 added one, and it sat **red on `main`
for five releases** — #92 through #96 all shipped over it — because every band
in between was derived from changed modules. Fixed at v3.66.822.

**No automatic predicate here — read.** Three attempts at an AST rule to find
these returned 76, 69 and 15 files on the same tree; the shapes are too varied
(`git ls-files`, `rglob`, `--collect-only`) and most enumerate
`bulk_downloader/`, not `tests/`. A precise-looking list that is wrong is worse
than an over-broad one you read. Start over-broad and check each hit's SUBJECT:

```bash
grep -rln "ls-files\|rglob\|--collect-only" tests/*.py | head -40
```

Then keep only the ones whose enumerated path reaches `tests/` or the whole
repo. **The membership question is not "does it walk the tree" — it is "does
editing a TEST FILE change this gate's denominator".** Re-measured 2026-08-03
at v3.66.842: **nine** qualify, plus one that no grep of `tests/` can reach
(below). The v3.66.833 revision said **seven**, and it was not wrong when
written — it went stale the same session it was written, because v3.66.841 and
842 each ADDED a member. An earlier revision listed four and was inherited as
complete through five cuts; the v3.66.825 revision was then wrong in **both**
directions — one false "yes", one missed member. Three consecutive revisions of
this table have now been wrong, which is section 0 applied to the table itself.
Treat the list below as a starting point too, and re-derive it:

| file | enumerates | moved by a new test file? |
| --- | --- | --- |
| `test_source_windows_do_not_shift.py` | `git ls-files tests/*.py` | yes |
| `test_pytest_runner_boundary.py` | `(REPO / "tests").glob("test*.py")` | yes |
| `test_capture_execution_lanes.py` | `tests_root.rglob("test*.py")` | yes |
| `test_deploy_manifest_stays_retired.py` | `git ls-files -- *.py *.sh` | yes |
| `test_task_tracker_stays_retired.py` | `git ls-files -- *.py *.sh` (added v3.66.841) | yes |
| `test_codex_handoff_stays_retired.py` | `git ls-files -- *.py *.sh` (added v3.66.842) | yes |
| `test_all_sources_parse.py` | `git ls-files -- *.py` (bare `*.py` reaches `tests/`) | yes |
| `test_generated_artifacts_are_not_tracked.py` | `git ls-files -z` (everything) | yes |
| `test_history_columns_go_through_migrations.py` | `_tracked("*.py")` (`:49`, call `:59`) — bare `*.py` crosses directories | yes |
| `test_v3_66_1034_guards_survive_a_module_wipe.py` | `git ls-files -- tests/*.py` for the leaker ratchet (added v3.66.1034) | yes |
| `test_gitignore_rules_actually_match.py` | `git ls-files` → `.gitignore` paths only (`_gitignore_files`, `:60-67`) | **NO — `.gitignore` only** |
| `test_v3_66_820_share_tools_saw_no_session_keys.py` | `git ls-files` → text extensions (`:451-456`) | **NO — `.py` not in the set** |
| `test_playwright_engines_single_source.py` | `git ls-files -z '*.sh'` (48 files) | **NO — `.sh` only** |

The NO rows are the distinction worth keeping. Each runs a genuine repo-wide
enumerator and then filters to a set a new `.py` test file cannot enter, so
counting them as axis-6 gates over-bands every test-only cut — but each still
has a real band: the playwright gate joins any cut touching a **shell script**
(v3.66.824 edited `scripts/cloud-setup.sh` and needed it); the share-tools
gate's set is text extensions (`.md`, `.json`, `.txt`, `.html`, …), so a cut
adding or renaming tracked text assets — corpus, fixtures, docs — moves it;
the gitignore gate moves only with a tracked `.gitignore`.
(`test_history_columns_go_through_migrations.py` also calls `_tracked` at
`:75`, but that call is `bulk_downloader/`-scoped and does not move.) A count
is not a band; ask what each one's subject actually is.

**A tenth gate is axis-6 and the grep above cannot find it, because its
enumerator is not in the test file.** `tests/test_pin_index_in_sync.py` imports
and reloads `tools.build_pin_index` (`:48-50`), and that tool globs
`(root / "tests").glob("*.py")` at **`:151` and `:211`** — so adding a test file
changes `test_files_scanned`, `PIN_INDEX.json` goes stale, and the gate fails
until you regenerate. Nothing in the test's own source matches
`ls-files|rglob|--collect-only`. Section 2a already records the consequence
without naming the gate: three untracked RED files from other cuts inflated
`test_files_scanned` by 3. **The recipe above is a starting point, not a
denominator** — a gate whose enumeration lives one import away is invisible to
it, and there is no reason to believe this is the only one. When a cut adds a
test file, regenerate `PIN_INDEX` regardless of what the grep returned.

---

## 5 | Environment traps

- **`test_v3_43_80_modules::test_all_modules_import` is environmental, not a
  regression.** It false-fails a bare band with `tray_app: Namespace Gtk not
  available`; it passes 49/49 with GTK typelibs and `DISPLAY=:99`. Fix the
  environment (`scripts/provision_test_host.sh`, below), then re-band — do not
  chase it as a code defect.
- **A SECOND environmental false-failure, container-only: there is no
  live-recording backend here.**
  `test_v3_66_729_body_contract_fixtures::test_the_app_never_5xxs_on_a_well_formed_request`
  fails with `/api/live/watch  app 5xx'd on OUR fixture -> no_backend`, and it
  is NOT a regression. Measured 2026-08-07: it reproduces identically on the
  pristine base **in the same directory**, all 10 tests in that file PASS on
  the box in the v3.66.932 capture, and `which streamlink ffmpeg yt-dlp`
  returns nothing in this container. Same shape as the GTK case above -- fix
  the environment or discount it, do not chase it as a code defect. And note
  what it costs in the other direction: a container band cannot tell you
  anything about that endpoint at all.
- **A THIRD environmental false-failure, container-only: `tests/test_e2e_smoke.py`
  fails 7/7 here.** The `_RealE2ESmoke` class drives a real browser at a real
  SPA; every failure is a playwright `wait_for_selector` timeout. PROVEN
  pre-existing 2026-08-07 at v3.66.938: the same 7 fail on a pristine HEAD with
  **0 modified paths** in the same directory. Same disposition as the GTK and
  `no_backend` cases above -- discount it, do not chase it -- and the same cost
  in the other direction: a container band says nothing about the SPA.

  **The method trap hit while proving it is worth more than the finding.**
  `git stash push --keep-index` on a fully-STAGED tree leaves the working tree
  unchanged, so the "pristine" run measured the cut against itself and proved
  nothing. It exits 0 and prints a stash entry, so it looks like it worked.
  Check `git status --porcelain | wc -l` reads **0** before believing a
  baseline run, and stash without `--keep-index` when the cut is staged.

- Three Python resolution paths exist (system / prestaged / service venv) and
  they carry **different playwright versions**. `import playwright` succeeding at
  a bare prompt proves nothing about what BD runs.
- Two Playwright browser pools exist with **different chromium revisions**.
  Behaviour differing inside vs outside the env wrapper may be a different
  browser build, not a different code path.
- **A real band exceeds the 2-minute default command timeout, and being killed
  at 2:00 looks nothing like a failure.** Measured at v3.66.880: a 28-file band
  took **169s** and the first attempt was reaped at 120s with exit 143, after
  which the freshness check queued behind it never ran at all — so the one gate
  that was about to go red was silently skipped. Pass an explicit timeout
  (`timeout: 600000`) **or** background the run and wait on its written exit
  marker; do not read a reaped run as a result. Backgrounding is the more
  reliable of the two because it has no cap, and it composes with §5's rule
  about waiting on a marker rather than on `pgrep`.

- **Never run the whole `tests/` directory locally — but BOTH of the reasons
  this rule used to give are now disproven, so do not cite them.** Re-measured
  2026-08-08 at v3.66.947, every run individually bounded:

  | claim this rule rested on | measured |
  | --- | --- |
  | `test_perf_lab.py` is *the* recorded hanger | 17 passed in **2.5s** — and identically with `BD_DISABLE_KEEPALIVE` popped, so it is not the flag holding it together |
  | a second hanger, `test_v3_66_146_nav_guard` | **no file of that name exists**, in any variant |
  | the two real `146` files are slow | 23 passed in **0.77s** |

  A targeted sweep found no hang either: **79** files carrying hang-prone
  shapes (`while True`; `.join()`/`.wait()`/`.acquire()` with no timeout;
  `subprocess` with no `timeout=`; unbounded HTTP), 6% of 1270 tracked test
  files, each run under a hard cap. 69 real test files all completed, 9 were
  helper modules collecting nothing, and the single timeout was **the bound,
  not a defect** — `test_fuzz_harness_frontend.py` legitimately takes 75s
  against a 60s cap, and returns 0 in 75s when the cap is raised. **A timeout
  is not evidence of a hang unless the bound exceeds the legitimate runtime**;
  set it from the slowest known file (75s here), not from a guess.

  **THE CIRCULARITY WAS BROKEN BY AN OPERATOR EXEMPTION, AND THE SUITE DOES NOT
  HANG.** The untested case was a hang emerging only in a FULL-SUITE run,
  through interaction no per-file probe can reproduce — untestable without
  running the suite, which was the rule. Matt granted a one-time exemption on
  2026-08-08; measured at v3.66.948 with `pytest-timeout` armed specifically to
  name a hanging test and dump its stack:

  ```
  14 failed, 14943 passed, 91 skipped in 635.42s (10m35s)   # 4 workers
  tests exceeding the 240s per-test cap: ZERO — the guard never fired
  ```

  The 14 are the documented container-only set (`test_e2e_smoke` ×7, the
  `no_backend` body-contract case, absent-interpreter `exec_bridge` ×5, a
  no-tunnel vpn probe). Item 34's four order-dependent failures are **absent**,
  which is @945's fix holding at full denominator.

  **SO THE SWEEP IS PERMITTED, IN EXACTLY ONE FORM. Never bare `pytest tests/`.**

  ```bash
  BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest tests/ \
      -n 4 --dist loadfile --timeout=240 --timeout-method=thread \
      -q -p no:randomly
  ```

  Every flag is load-bearing and a different one is a different experiment:
  `--timeout` is what turns a hang into a named test instead of a stall,
  `--timeout-method=thread` dumps its stack, `--dist loadfile` is the
  distribution that was actually measured, and `-n 4` matched THAT CONTAINER's
  four cores. Run it under a whole-run cap as well, and wait on a written exit
  marker rather than on `pgrep` (§5's rule about a wrapper matching itself).

  **`-n 4` IS NOT A CONSTANT -- DERIVE IT.** It is the one flag here that
  describes the machine rather than the experiment, and it was copied onto an
  86-core box at v3.66.1035 purely because this paragraph said 4. Use
  `-n "$(nproc)"` or a stated fraction of it, and RECORD the value with the
  result; a count taken at one worker level cannot be compared with one taken
  at another.

  **AND MACHINE LOAD DOMINATES THE FAILURE COUNT, WHICH MAKES A SINGLE SAMPLE
  UNINTERPRETABLE.** Measured on test5 at v3.66.1035, same tree, same commit:
  **1-8 failures on an otherwise idle box, and 18-29 with four concurrent
  suites running.** The mechanism is that `--dist loadfile` schedules files to
  workers DYNAMICALLY, so timing decides which files share a worker, and
  cross-file state leaks fire or do not accordingly.

  Three single samples were each read as signal in one session (19, 27, 51) and
  all three were noise; one A/B at n=4 per condition settled in twenty minutes
  what those three had confused for hours. So: **never conclude from one
  full-suite run.** Compare distributions under matched load, and treat any
  historical claim that rests on a single sample -- including in this file --
  as unproven rather than wrong.

  **WHAT THIS DOES NOT LICENSE.** One ordering was measured — `-p no:randomly`
  with `--dist loadfile` keeps each file whole on one worker, so an
  interleaving-dependent hang was never given the chance, and one green run is
  not a proof of absence. It was PARALLEL; a serial full run is a different
  denominator and is still untested. And it is still not a substitute for the
  box: §7's rule that sandbox green is necessary and not sufficient is
  unchanged, and 14 of these failures are environmental here and pass there.
  Use it to answer "does anything hang or interact", not "is the tree good".

  Note also what the sweep's own instrument got wrong: the candidate list was
  written without a trailing newline, so `while read` silently dropped its last
  entry and "full coverage" would have been false by one. Diff the input list
  against the results rather than trusting the loop.
- Always capture exit codes **unpiped**: `cmd > /tmp/out 2>&1; echo "exit=$?"`.
  Piping masks the exit code, and this bites even when you know about it.
- `pgrep -f "<cmd>"` **matches its own wrapper**. Never read it as "still
  running" — check `/proc/<pid>` or a written exit marker. This bites hardest
  inside a **wait loop**: `until ! pgrep -f 'pytest …'; do sleep 10; done` never
  exits, because the loop's own command line contains the pattern. A session
  reported a test lane as "running" for ten minutes when it had never started.
  Reaching for `pkill -f "until ! pgrep"` to clean that up matched *its* own
  command line and killed the shell. Wait on a **written marker or the job's
  own exit**, never on a process-table match.
- **Change one variable at a time, or the comparison is worthless.** To decide
  whether a failure was yours, run the baseline **in the same directory**.
  A session ran the pristine lane in a detached `git worktree` and got two
  spurious signals: a test that "only failed with the change" had in fact never
  *run* in the worktree (it self-skips when it cannot reach a service worker),
  and another failed only in the baseline because a probe could not find the
  real checkout from `/tmp`. The arithmetic gave it away — total collected
  differed by exactly the new file's test count while skips differed by five.
  If the totals do not reconcile, you changed more than you think.
- **The interpreter is `venv/bin/python`, never bare `python3`.** In the cloud
  container `/usr/local/bin/python3` is **3.11 without the project
  dependencies**, while `venv` is 3.12 (the box/CI interpreter). There is no
  `.venv` here — a command naming it exits 127 and the caller silently falls
  back to 3.11. That happened: a full test band was measured on 3.11 and
  reported seven failures that did not exist.
- **The Claude Code panel runs a thin bootstrap, not the provisioner.**
  `scripts/cloud-bootstrap.sh` is the text pasted into the panel; it locates the
  checkout and `exec`s `scripts/cloud-setup.sh` from it, so every fix to the
  provisioner reaches the next session with nothing to re-paste. Before this,
  the panel held a private copy that had forked three commits and 91 lines while
  13 tests certified the repo copy that never executed. If the env report's step
  labels do not match `scripts/cloud-setup.sh`, the panel has forked again.

  The bootstrap is pinned at **under 80 lines** by
  `test_bootstrap_stays_short`, and it sits at 79. That is not slack to spend:
  every line added there is a line that leaves the repo's sight. **Put new
  provisioning logic in `scripts/cloud-setup.sh`, never in the panel text.**

  The panel's **environment box** carries the session settings. These are not
  in any file, so they are the one thing a fresh session cannot re-derive —
  set them there:

  ```
  BD_HOME=/tmp/bd_home
  BD_REPO=/home/user/BD
  BD_SKIP_ARCHB=1
  BD_SKIP_BROWSERS=1
  BD_DISABLE_KEEPALIVE=1
  CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=2
  ```

  `BD_REPO` is the first probe rung, so setting it makes checkout location
  deterministic instead of relying on the glob. `BD_HOME` keeps app state out
  of the repo. The two `BD_SKIP_*` flags drop kasmvnc and the browser download
  — browsers are preinstalled at `PLAYWRIGHT_BROWSERS_PATH`, and the
  provisioner says which of "skipped but present" and "skipped and absent" is
  true rather than assuming the worst. `BD_DISABLE_KEEPALIVE` stops background
  threads outliving a test run.

  **THE BOX IS A CURATED SUBSET, NOT THE FULL SET.** Measured at v3.66.927:
  `scripts/cloud-setup.sh` reads **nine** `BD_*` variables and the box above
  names four of them. The five it does not name are all optional skips with
  working defaults — `BD_SKIP_AUDIT`, `BD_SKIP_CLOAK`, `BD_SKIP_EXTRAS`,
  `BD_SKIP_NET`, `BD_SKIP_SECTOOLS` — so they belong in the provisioner's own
  documentation, not in a box you paste once. They are named here so a future
  session knows the knobs exist rather than re-deriving them from the script.
  Note also that `BD_DISABLE_KEEPALIVE` runs the other way: it is IN the box and
  the provisioner never reads it, because its consumers are the app and the test
  suite. Neither direction is a defect; both look like one until you check.

  `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=2` caps how deep agents may spawn
  agents. Two is deliberate: the top-level session orchestrates, its agents do
  work, and nothing below that spawns further. Depth buys little here and costs
  a lot -- this container has **4 cores**, so the workflow concurrency cap is
  `min(16, nproc - 2)` = **2**, and a third tier only lengthens the queue behind
  that same 2-wide gate. Measured at v3.66.926: eleven agents were SLOWER at a
  register re-derivation than doing it inline, 3 items against 12, because most
  of the work was one grep or one tool invocation apiece. Depth also multiplies
  the residue problem in section 2b -- every additional tier is another writer
  in a shared tree you did not start.

  Note what these do **not** buy: `BD_HOME` does not protect
  `~/.config/bulk-downloader`, which resolves from `$HOME`, not `BD_HOME`.
  That is `tests/conftest.py`'s path-keyed store guard's job.

  **`BD_HOME` does not govern the database either — `BD_INSTALL_DIR` does.**
  `db._resolve_db_path()` resolves at call time in this order: an absolute
  monkeypatched `DB_PATH` verbatim; else `BD_INSTALL_DIR` joined with the
  relative `DB_PATH`; else the bare relative `DB_PATH`, which
  `sqlite3.connect()` resolves **against the current working directory**.
  `BD_HOME` is never consulted. So an ad-hoc probe that exports `BD_HOME`,
  imports `bulk_downloader.db` and runs from the repo root writes
  `downloader_history.db` **into the repo**. It is gitignored, so `git status`
  stays clean and nothing warns you.

  Rows then accumulate across probe runs, and the next probe reads them. That
  is not cosmetic: a probe of a *correct* fix returned two matching rows
  instead of one, which read as the fix over-reindexing — a defect that did not
  exist, in code that had already shipped. Section 5's rule about changing one
  variable at a time applies to state as well as to flags.

  `tests/conftest.py`'s `clean_workdir` gets this right and is the model:
  `monkeypatch.chdir(tmp_path)` **and** `monkeypatch.setenv("BD_INSTALL_DIR", ...)`,
  belt and braces, "even if subsequent code chdirs away". Hand-rolled probes get
  neither for free — set `BD_INSTALL_DIR` to a tmpdir, or use the fixture.

  **THE CONCRETE FORM, because the paragraph above did not stop it happening.**
  Item 36 was opened by a `downloader_history.db` appearing in the repo root
  hours after the fix meant to prevent it, and the caller was never identified.
  Reproduced at v3.66.958: ONE `selector_drift.status_all()` call with repo-root
  cwd and `BD_INSTALL_DIR` unset writes 12288 bytes and one table. So every
  ad-hoc probe opens with the two lines, not with the prose:

  ```bash
  export BD_INSTALL_DIR="$(mktemp -d)"   # NOT BD_HOME -- it governs nothing here
  venv/bin/python -c '...'               # absolute interpreter if you cd away
  ```

  **AND THAT LINE IS FOR A PROBE. NEVER EXPORT IT INTO A `pytest` RUN.** The
  two halves are opposite and the same variable serves both, which is why this
  keeps costing sessions. `capture.sh:78-99` refuses outright when it is set,
  and says why: it is inherited by every test in the run, `db._resolve_db_path`
  prefers it over the working directory, so every test shares ONE database and
  the isolation `tests/conftest.py` provides is defeated. 15.74 finding #4
  measured 89 false failures from exactly that.

  Measured again at v3.66.1024, same tree, same directory, the variable the
  only difference:

  | file | exported | popped |
  | --- | --- | --- |
  | `tests/test_provision_test_host.py` | 4 failed | **115 passed** |
  | `tests/test_v3_66_820_auth_health_reaped_on_site_delete.py` | 2 failed | **11 passed** |

  Both were reported to the operator as pre-existing order-dependent failures,
  "proven" by changing one variable in the same directory -- while
  `BD_INSTALL_DIR` sat set on BOTH sides. The comparison was sound for the
  question it answered ("did that cut do this?" -- no) and was then read as an
  answer to a question it never asked. Section 1's rule, on the session's own
  work, twice.

  **The form for a band, then, is the variable POPPED:**

  ```bash
  env -u BD_INSTALL_DIR bash -c 'BD_DISABLE_KEEPALIVE=1 venv/bin/python -m pytest ...'
  ```

  `env -u` rather than "just don't set it": your shell may already carry it
  from an earlier probe, and that is the whole failure mode.

  **A `conftest.py` guard for this was built and DELETED at v3.66.1024, which
  is the more useful half of the finding.** It refused an ambient value at
  session start, RED-first, with an over-sensitivity control that passed. The
  full suite then failed it: `tests/test_v3_66_946_the_leak_guard_does_not_fire
  _on_inherited_state.py` and `tests/test_v3_66_994_leakprobe_refuses_when
  _blind.py` encode the OPPOSITE, deliberate position -- a legitimate inherited
  value must be RESTORED rather than removed, or every test after a leaker runs
  without the install dir the operator configured. The guard fought a shipped
  decision, and threading it needed an exemption variable that would itself
  enter `test_gui_parity`'s `BD_` ledger. **The control that passed only
  covered a clean environment; the suite's own meta-tests were outside its
  denominator** -- section 0, inside the guard written to prevent a section 0
  failure. Do not rebuild it without reading those two files first.

  **No gate can catch this, and know why before proposing one:** refusing a
  database path that resolves inside the repo would break the BOX, which runs
  the service from its own checkout, where "inside the repo" and "the install
  dir" are the same directory. It would fire on production and be switched off
  -- section 0's over-sensitivity failure, shipped deliberately. The discipline
  is the whole mitigation, and the residue is silent: the file is gitignored,
  `git status` stays clean, and the next probe READS the rows the last one left.
- **`sqlite3` `immutable=1` SKIPS THE WAL, so it cannot see a committed row that
  has not been checkpointed.** Measured twice in one day at v3.66.927, in
  opposite directions, and both cost real time:

  | reading | rows |
  | --- | --- |
  | `file:db?immutable=1` (WAL ignored) | 114 |
  | plain `sqlite3.connect(db)` (WAL replayed) | 151 |

  The first was a survey of quarantined databases, where `immutable=1` is
  exactly right — it takes no lock and cannot replay a stray `-wal` into a file
  you are trying not to disturb — but its count is a **floor**, not the answer,
  and reporting it as the answer would have written off 37 real rows. The second
  was a test asserting what a writer had just committed, where `immutable=1` is
  exactly wrong and produced `no such table` for a table that existed. Written
  by the person who had measured the first case an hour earlier.

  **Rule: `immutable=1` for surveying a file you must not touch; a normal open
  for asserting what was written.** And when recovering a quarantined database,
  carry the `-wal`/`-shm` companions across under the NEW basename or the tail
  of the history is silently dropped.
- **`pip check` cannot see an uninstalled requirement.** Its denominator is what
  *is* installed. `runtime deps OK` was reported with `beautifulsoup4` and
  `pytest-xdist` both absent. To ask whether requirements are satisfied, parse
  `requirements.txt` and resolve each name.

  **And name resolution is not version satisfaction — FIXED, and this paragraph
  was stale about it for weeks.** `tools/check_requirements.py` used to call
  `version(name)` and *discard the result*, so specifiers were never compared,
  and a reverted image could restore correct NAMES at wrong VERSIONS with every
  gate reporting OK. It is the sole instrument in all three recovery paths
  (`session-start.sh`, `cloud-setup.sh`, `deploy.sh`), which is what made that
  serious.

  It now builds `Requirement(line)` and asserts
  `req.specifier.contains(have, prereleases=True)`, and it **raises
  `Unevaluable` when `packaging` is not importable** rather than falling back to
  a name-only answer — because "resolved a name" and "satisfies the pin" would
  otherwise be indistinguishable, which is section 0's whole subject.
  (`prereleases=True` is deliberate: a venv legitimately holding `2.0rc1` for
  `>=1.9` IS satisfied, and reporting otherwise sends the caller into a
  reinstall loop that cannot converge.)

  **Kept rather than deleted, because the staleness is the lesson.** This file
  asserted "Open, and nothing here can see it" while the tool it names had
  already grown the exact instrument. Section 1 says documents go stale
  silently and are then read as authority — that applies to THIS document, and
  the only reason it was caught is that a register re-derivation ran the tool
  instead of quoting the note. Re-derive before citing, including from here.

- **WHY the container "rolls back": the panel snapshots the filesystem, and the
  setup script runs once per CACHE BUILD -- not per session.** Stated by the
  operator 2026-08-05 and corroborated here. Anthropic runs the panel's setup
  script the first time a session starts in an environment, snapshots the
  filesystem, and every later session starts from that snapshot with the script
  SKIPPED. It re-runs only when the setup script changes, when the allowed
  network hosts change, or when the cache expires at roughly **7 days**.
  **Editing the environment-variables box does NOT trigger a rebuild**, and
  resuming a session never does.

  **The rebuild appears to be LAZY -- fired by the first NEW session start, not
  by the edit** (2026-08-05, the discriminator session, v3.66.882 at `5eb43d6`).
  Split the evidence, because only half of this is a reading: **MEASURED** is that
  this session WAS a cache build, from the timestamps below. **DERIVED** is the
  laziness itself — nothing was observed at or after the operator's 19:43Z edit,
  so an eager rebuild that began at 19:43 in a container this session never saw
  would produce identical readings here. Best explanation, not a measurement.
  The readings:

  | reading | value |
  | --- | --- |
  | container boot | ~`20:07Z` (uptime 6 min when read at 20:13:08Z) |
  | `cloud-setup.sh` start (report header) | `2026-08-05T20:07:23Z` |
  | report `generated_against_commit` | `5eb43d6` -- equal to HEAD |
  | `venv/bin/python` mtime | `20:07:28` |
  | `frontend/dist/index.html` mtime | `20:08:38` |
  | report finalized (file mtime) | `20:12:24` |
  | reflog OLDEST | `2026-08-05 20:07:19` |
  | HEAD / behind `origin/main` | `5eb43d6` / **0** |
  | hook blocks at session start | **none** (as predicted) |
  | `bd-restart-check` | `OK`, hook ran `20:12:41Z`, `source=startup` |

  **The discriminator could not discriminate, and the reason is section 0.** The
  protocol's two branches were "reflog-oldest ~= the rebuild time" (snapshot
  carries `.git`) versus "reflog-oldest ~= this session's own start" (fresh clone
  per session). Because the rebuild fires AT a session start, those are **the
  same number** -- 24 minutes apart in the design, zero apart in fact. A
  cache-BUILD session has a freshly provisioned repo under *either* model, so its
  denominator structurally excludes the subject. Do not re-run the protocol on a
  rebuild session expecting an answer.

  **So the model stands on the 2026-07-28 reading, which is the only kind that
  can settle it:** `.claude-env-report.md` was written `2026-07-28T18:42:15Z` at
  `cee4be70` (v3.66.818), and a session running **eight days later** read the
  reflog's OLDEST entry as `2026-07-28 18:42:12` -- the build minute, not its
  own start. A fresh clone per session would contradict that (its reflog would
  start that day). Snapshot-carries-`.git` is therefore the model, on **one**
  cached-session reading. That is the whole mechanism: the "rollback to an old
  commit" is the snapshot's HEAD, and the "venv losing packages" is a venv built
  before those packages were declared.

  **The confirming reading needs no freeze, and it is FOUR observables — read
  the branch one first.** This session baked a snapshot whose reflog begins
  `2026-08-05 20:07:19`, on a branch it created. Any session that starts WITHOUT
  a setup-script change is a cached session, so:

  ```bash
  git branch                                  # a FOREIGN claude/* branch present?
  git reflog --date=iso | tail -1             # vs. your own session start time
  git rev-list --count HEAD..origin/main      # behind?
  # and: which hook block appeared at session start
  ```

  | observable | snapshot carries `.git` | fresh clone per session |
  | --- | --- | --- |
  | `git branch` | lists **`claude/bulkdownloader-discriminator-das7sy`** — a branch this session never created | only `main` + its own |
  | reflog OLDEST | `2026-08-05 20:07:19` | ~= own session start |
  | behind `origin/main` | **>= 1** | **0** |
  | hook block | `*** STALE BASE ***` | none |

  **Read the foreign branch first**: a timestamp invites arithmetic, a branch
  named after someone else's session cannot be misread. Four agreeing
  observables beat one, and three of them are free. Record whichever set you
  get — `project-knowledge/SESSION_CARRY.md` 15.33 carries the full reading
  guide and the decision it feeds.

  Four consequences, and the third is the one that bites:

  1. **The @879 SessionStart repair is LOAD-BEARING, not belt-and-braces.** On a
     normal cached session it is the only thing that reconverges anything.
  2. A stale snapshot is the first suspect for any environment symptom. To
     distinguish it: record the version at startup, force a rebuild by touching
     the **panel's** setup script (a comment change suffices), and compare.
  3. **Anything you install by hand lives only until the session ends.** It is
     not in the snapshot, so the next session starts without it. Installing a
     package to fix a symptom fixes this session and nothing after it -- put it
     in `scripts/cloud-setup.sh` and force a cache rebuild instead.
  4. **It RECURS on every cache cycle, and the dangerous state is the one the
     hook correctly refuses to repair.** A rebuild resets content to today and
     then re-bakes it, so ~7 days on, sessions go stale again. The repairable
     case (main, clean, behind) is handled. The one that destroys work is not
     repairable: an agent branches from a snapshot-era commit, commits, and
     opens a PR — GitHub diffs it against current main, so **every commit merged
     since the snapshot appears as a REMOVAL and the PR silently reverts a
     week's work**. @881 makes the hook emit a distinct `*** STALE BASE ***`
     block naming the commit count and that consequence whenever HEAD is behind
     and the checkout is not on `main`.

     **On a cloud cached session, expect this block ONCE PER SESSION — and what
     must stay routine is the rebase RESPONSE, not the ignoring.** Measured at
     v3.66.883: the platform's session lifecycle is clone -> `main` ->
     `claude/<session>`, so a session is **never on `main`** by the time the
     hook runs, while the repair predicate requires
     `[ "$branch" = "main" ]` (`.claude/hooks/session-start.sh:169`). The
     REPAIRED path is therefore structurally unreachable **at a
     platform-created session start**, and STALE BASE is the only outcome
     available whenever HEAD is behind. **On a RESUME the branch is wherever the
     session parked it**, so REPAIRED *is* reachable on a later resume of a
     session that checked out `main` by hand — the unreachability is a property
     of how sessions START, not of the hook. Re-base
     before doing anything. Do not let its frequency train you to skip it: the
     consequence it names — a PR that silently reverts every commit merged since
     the snapshot — does not get smaller because the warning got familiar.
     (Whether it truly fires every cached session depends on the snapshot model,
     which SESSION_CARRY 15.33 leaves as a four-observable reading; the carve-out
     that would fix it is named there and deliberately not built.)
     A `*** REPAIR FAILED ***` block means the opposite half: the auto-repair on
     `main` did **not** happen, so you are on the snapshot base with the
     environment **not** reconverged. Resolve the collision git names in the
     block — usually an untracked file at a path `origin/main` now tracks — then
     repair by hand with `git merge --ff-only origin/main && bash
     scripts/cloud-setup.sh`.

  **THE CONFIRMING READING IS STILL UNOBTAINED, and the reason is worth more
  than the reading would have been. STEP 0 IS NOT OPTIONAL.** v3.66.884 was
  started to take it and could not: it was itself a cache BUILD, so its
  denominator excludes the subject for exactly the reason 15.33 gives. The
  readings, all direct: container boot ~`21:49:11Z` (uptime 274s at
  `21:53:45Z`); `cloud-setup.sh` header `2026-08-05T21:49:21Z` -- **ten seconds
  after boot**; report `generated_against_commit=f863c49` = HEAD; reflog three
  entries, oldest `21:49:18` and messageless; `git branch` = `main` + its own;
  `behind origin/main` = 0 after a fetch that exited 0; no hook block.

  **Every one of those four observables read FRESH-CLONE, and every one was
  void.** A build session manufactures the fresh-clone column under either
  model -- so the table is not merely uninformative there, it is actively
  MISLEADING, and an agent that runs the four readings without doing step 0
  first will conclude "fresh clone per session" with four agreeing signals and
  be wrong. Decide build-vs-cached FIRST, from `generated_at` against boot
  time; only then is the table meaningful.

  **A SECOND FACT THE ~7-DAY MODEL DOES NOT EXPLAIN: two cache builds occurred
  1h42m apart on 2026-08-05** -- `20:07:23Z` (recorded at v3.66.883) and
  `21:49:21Z` (here). Expiry cannot produce that. Neither can a repo commit:
  the panel hashes the pasted bootstrap TEXT, and `scripts/cloud-bootstrap.sh`
  carries a deliberate `[cache-rebuild: <date> <version>]` marker on line 2
  (added by PR #185) whose entire purpose is to force a rebuild when re-pasted.
  A re-paste between the two sessions would explain it exactly, and **from
  inside the container that is not observable** -- the panel's text cannot be
  read from here. Recorded as an open question, not as a trigger theory. What
  it costs you in practice: **do not plan a session around being cached.**
  `CLAUDE_CODE_CONTAINER_ID` is in the environment and is the cheapest way for
  a later session to tell whether it shares a container with an earlier one.

- **The container's clone is SHALLOW, and a nonzero `--is-ancestor` here does
  not mean what it says.** Measured 2026-08-05 in this container: `.git/shallow`
  exists, `git rev-list --count HEAD` returns **50**, and the graft is
  `75e9024` (2026-08-03). A commit older than the graft is not in this
  repository at all -- `cee4be70`, a genuine ancestor of `main` and the commit
  cited in the cache bullet above, is `fatal: Not a valid object name`, and
  `git merge-base --is-ancestor` on it exits **128**, not 1.

  The two exit codes mean opposite things: **1 is "it is not in this history",
  128 is "I cannot see it."** Conflating them is section 0's inverse defect -- a
  gate firing on its own blindness, and doing so with a confident, specific,
  wrong claim. `bd-freshcheck`'s register-close-tip check tested every nonzero
  alike -- the `rc2 != 0` branch of its close-tip `merge-base --is-ancestor`
  check. (Named by mechanism, not by `file:line`: the file is extensionless, so
  the anchor gate's own regex cannot see such an anchor and could never catch it
  going stale -- and the line DID move once already while this cut was written.)

  **REPAIRED at v3.66.884; the rules below are what survive the repair.** The
  reading that follows is the PRE-fix state, kept because it is the evidence.

  **DEMONSTRATED, not inferred from reading the code.** In a throwaway
  `git clone --depth 1` of this repo, `bd-freshcheck --repo-only` returned:

  ```
  exit=1
    STALE  register close tip
           15.30 says 'close at 5e87c68', which is NOT an ancestor of HEAD
           (5eb43d67c3e6) -- it names a commit this branch does not contain
  ```

  The register section is innocent and the sentence is false. Our own container
  passed only because 15.30's `5e87c68` sits 6 commits back, inside a 50-deep
  window; at ~12 cuts a session that window is a few days deep. The code comment
  directly above that line reasons carefully about over-sensitivity and does not
  consider that the instrument itself could be blind.

  **THE OBVIOUS REPAIR MAKES IT WORSE, AND THAT IS THE FINDING.** The reflex is
  "fetch the missing object, then re-ask". Measured on one `--depth 1` clone, in
  sequence: `git fetch --depth=1 origin <full sha>` **succeeds** (exit 0 -- GitHub
  does serve SHA-in-want for a reachable commit through this proxy), the object
  arrives, and `merge-base --is-ancestor` then returns **1**. Not 0, and no
  longer 128. The commit genuinely IS an ancestor -- ground truth is exit 0 in a
  deepened clone -- so fetching by sha delivers the object *without the
  connecting history* and converts a **detectable** blindness into an
  **undetectable false negative**. `git fetch --deepen=200 origin main` on the
  same clone then yields the correct 0.

  So: **never repair a 128 with a by-sha fetch -- not because it fails, but
  because it succeeds into a wrong answer.** Use `--deepen`, which needs no sha
  in hand and depends on no server capability.

  **AND A BY-SHA FETCH CANNOT EVEN BE USED AS AN EXISTENCE PROBE ON THIS
  REGISTER'S DATA.** Measured 2026-08-05 while building the fix, on a `--depth
  1` clone: `git fetch --depth=1 origin 5e87c68` exits **128, `couldn't find
  remote ref`** -- for the same commit whose FULL 40-char form fetches
  successfully seconds earlier. Git reads a short sha as a REF NAME, not a sha.
  The close sections all name short shas, so a design that concludes "the fetch
  failed, therefore the commit does not exist, therefore STALE" reproduces the
  false accusation it was written to remove. This killed the last step of the
  spec that had already been revised twice (SESSION_CARRY 15.33): the shipped
  fix carries the whole repair on `--deepen` and admits UNKNOWN rather than
  probing. **A sound-looking probe applied to the wrong sha FORMAT is still a
  gate that cannot see its subject.**

  **The general rule, and it is the one to carry away: in a shallow clone only
  `--is-ancestor` exit 0 is trustworthy.** A 0 means a connected path was found,
  which the shallow boundary cannot fake. Any nonzero -- 1 and 128 alike -- is
  UNKNOWN until the clone is no longer shallow, because a bounded deepen can
  leave an object present and its ancestry still uncomputable. Keying the verdict
  on whether the OBJECT is present is the trap: the false-1 state is exactly the
  one where it is. A fix built the obvious way would
  have reproduced the exact defect class it was written to remove, which is this
  file's section 0 landing inside the repair for an instance of section 0. (The
  first version of this measurement was itself wrong -- it tested a *fabricated*
  sha, where `not our ref` is guaranteed regardless of server policy, and
  concluded by-sha was refused. Corrected on re-measurement against the real
  commit. Recorded because a wrong measurement stated confidently is the thing
  section 1 exists to catch.)

  **WHAT THE v3.66.884 FIX DOES, and the one property to know before editing
  it.** A nonzero now resolves three ways instead of one: on a complete history
  it is authoritative and stays STALE; on a shallow one the clone is DEEPENED
  and the question re-asked, where exit 0 is the answer a boundary cannot
  fabricate; still shallow, or a deepen that failed, is UNKNOWN. Re-measured in
  a fresh `--depth 1` clone with the fixed tool: **exit 0**, and the verdict is
  the same sentence a full clone prints. The property that matters is that
  **nothing runs on the happy path** -- the first `is-ancestor` short-circuits,
  so the box and CI's `gates` job do no network and do not touch `.git`.
  Measured on this container across a full `--repo-only` run: depth stayed 50
  and `.git/shallow` survived. On the repair path it does deepen (the scratch
  clone went 1 -> 360 and un-shallowed), which is a real side effect: a gate
  that was read-only in every environment is now read-only in every environment
  it passes cleanly in.

  **CI is protected, and its comment was misstated in BOTH eras -- corrected at
  v3.66.884 on operator sign-off.** `.github/workflows/ci.yml` sets
  `fetch-depth: 0` on the `gates` job, so none of this is armed there. Its
  comment explained why by saying that under a depth-1 checkout the check
  "returns UNKNOWN (exit 2), failing for an environmental reason rather than a
  real one." That was wrong BEFORE the fix -- measured, it returned STALE
  (exit 1), which is fail-WRONG rather than fail-safe -- and it would have been
  wrong AFTER it too, since a depth-1 clone with a reachable remote now deepens
  and returns OK. Neither behaviour it described has ever existed, and it is
  the stated reason the depth is load-bearing, so a future editor removing
  gitleaks' need for full history would have reasoned straight from it.

  The comment now records what the depth actually buys: not a correct answer --
  the tool gets that either way -- but the avoidance of a step that reaches the
  NETWORK and DEEPENS the checkout in order to answer. **Unmeasured, and the
  comment says so:** whether that fetch can reach the remote from inside GitHub
  Actions. The OK is from this container, not from CI, and nothing here should
  be read as evidence about the Actions runner's egress.

  **MEASURED on the box 2026-08-08 (was "unverified"): `test4`'s clone is NOT
  shallow.** `test -e .git/shallow` is false there, so a nonzero
  `--is-ancestor` on the box is authoritative and needs none of the deepen
  machinery above. The shallow reading remains true of the **cloud container**
  and of scratch clones, which is where the whole repair path applies — do not
  collapse the two. Worth knowing why it mattered: item 21 was settled on an
  `exit=1` from the box, and that exit is only trustworthy because this line is
  now a measurement rather than an unknown.

- **The container rolls back to an old base image, and @879 changed what that
  costs you.** Five things revert together: the checkout, venv package
  *versions*, `frontend/dist`, `__pycache__`, and `.claude-env-report.md`. Until
  @879 the hook decided whether to provision by asking whether requirement
  *names* resolved — a denominator containing one of the five — so it repaired
  the tree and left the environment on the reverted image. The signature is the
  trigger now: a repaired rollback hands over to `scripts/cloud-setup.sh` on
  startup/resume, and on compact/clear says `ENVIRONMENT NOT RECONVERGED` rather
  than stalling a running session.

  Two consequences to know before you debug it. The repair fires **only on
  `main`** — a clean topic branch or detached HEAD parked at an ancestor is
  byte-lossless to reset and is still refused, because §2b tells you to
  `git checkout --detach FETCH_HEAD` before measuring and resetting that
  destroys the position you chose. And a **failed fetch is now reported**: an
  image reversion rewinds `refs/remotes/origin/main` together with HEAD, so
  without a successful fetch both sides of the comparison are equally stale and
  a real rollback is indistinguishable from a healthy tree.

- **`.claude-env-report.md` is STALE after every cut, by design — do not chase
  it.** `bd-env-report-check` treats the VERSION as decisive and `__version__`
  bumps on every merge, so exit 1 is the steady state rather than a signal.
  Wiring it to trigger reprovisioning would be a gate firing on identity, not
  content (§0's inverse defect). Section 7's advice to check its header still
  holds; what changed is that a STALE verdict alone tells you nothing.

- **`requirements-dev.txt` does not resolve in a cloud container, deliberately.**
  It carries the packaging chain (`pyinstaller`, `nuitka`, `zstandard`), which is
  why neither the session hook nor `cloud-setup.sh` installs it; CI installs it
  for the postgres job alone. The two manifests that ARE the container's floor
  are `requirements.txt` and `requirements-test.txt`. Do not "fix" the third.

**Provisioning a test host.** `scripts/provision_test_host.sh` is the one command
that takes a fresh Ubuntu 24.04 box to a green `./capture.sh`: system tier,
`install_linux.sh`, Xvfb on `:99`, parity-inventory regen, graph content pin.
Run it instead of hand-installing typelibs.

The graph pin is the newest step and the least obvious. `capture.sh` step [2b]
compares the rebuilt source graph against a pin under `/var/lib/`, **outside the
repo** — so `git reset --hard` never delivers it and a fresh box has none. With
`BD_REQUIRE_GRAPH_HASH` unset (default `0`) the MISSING branch prints
`UNKNOWN -- optional check not armed` and **returns 0**, so the capture goes
green with the graph never checked. The provisioner now arms it and then
re-runs the gate's own `--check-hash` **as the invoking user** — writing a pin
proves a write, not that `capture.sh` can read and match it.

Re-pin by hand after any source change, or step [2b] reports drift and
`capture_verdict.py` turns that stage exit into a whole-capture FAIL:

```bash
GDB=$(mktemp -d)/KNOWLEDGE_GRAPH.db
venv/bin/python tools/l0_extract.py --root "$PWD" --db "$GDB"
sudo venv/bin/python tools/graph_build.py --db "$GDB" \
    --hash-pin /var/lib/bulkdownloader/validation/KNOWLEDGE_GRAPH.content.sha256 \
    --write-hash
```

Only the second command takes `sudo`: `--write-hash` sets `projection_mode`
false and returns before emitting any projection, so it writes the pin and
nothing else. Running the whole block elevated is the section 5 footgun —
`l0_extract` would build under `HOME=/root`.

`scripts/lib/system_deps.sh` is the **single source of truth** for system
packages; `install_linux.sh`, `scripts/provision_test_host.sh` and
`scripts/cloud-setup.sh` all source it. Never inline a package list again --
three copies is a denominator that drifts, and the copy nobody updated is the one
the box runs (S0/S8).

---

## 6 | Any tool that rewrites source must verify after writing

Parse the result (`ast.parse`), confirm the expected content is present, and
**restore the original + abort** if malformed. A bump tool once corrupted a test
pin via an over-escaped `re.sub` replacement (`r'\\1"'` emits a *literal* `\1`)
and shipped a `SyntaxError`. Use a lambda replacement instead.

**`ast.parse` is not name resolution.** A file referencing an undefined name
parses fine. Check the names too — import the module.

**The applied-check: use length arithmetic.** Every edit or mutation asserts
`src.count(old) == 1` first — an anchor matching 442 sites and applied with
`count=1` rewrites whichever site `re.subn` reaches first, and the resulting
verdict is evidence about a different location. But proving the replacement
*landed* is where two plausible checks are each wrong half the time:

| check | fails silently when |
| --- | --- |
| `new in after` | `new` already occurs elsewhere — trivially true, a no-op reads as applied |
| `after.count(old) == 0` | **append-style** (`old` is a substring of `new`) |
| `after.count(new) == count(new) + 1` | **shrink-style** (`new` is a substring of `old`) |

All three were hit, the last two inside a single mutation battery. Use instead:

```python
assert src.count(old) == 1
after = src.replace(old, new, 1)
assert after != src and len(after) == len(src) - len(old) + len(new)
```

Length arithmetic is exact for one replacement of a unique anchor and cannot be
fooled by substring overlap in **either** direction.

**A mutant that does not parse is INVALID, not caught, and not escaped.**
Deleting a line can orphan an `except:` clause; the runner then sees a
collection error, no named guard flips, and the row reads as an escape. Validate
the mutant (`ast.parse`, or `bash -n` for shell) *before* judging it, and report
"invalid" as its own outcome.

**Use `bd-mutate`; do not rebuild the harness.** Every rule in the rest of this
section is mechanical, and five agents in one session hand-rolled them and got
four of them wrong — a non-unique anchor rewriting the wrong site, a
non-parsing mutant scored as an escape, stale bytecode reading a mutant off a
restored file, and a baseline that was never green. Each produced a confident
wrong number, which is worse than no battery.

```bash
venv/bin/python toolchain/bin/bd-mutate --spec mutants.json \
    --band "tests/test_a.py tests/test_b.py"
venv/bin/python toolchain/bin/bd-mutate --selftest
```

`mutants.json` is a list of `{label, file, old, new, catcher?}`. It enforces the
unique anchor and the length arithmetic, validates each mutant before judging
it, purges `__pycache__` and sets `PYTHONDONTWRITEBYTECODE`, proves the baseline
GREEN first, and restores by sha256. Exit 0 = every mutant caught, 1 = something
escaped, **2 = the battery has no verdict** (empty spec, red baseline, residue
from an interrupted battery, failed restore) — and 2 is not a softer 1.
Choosing good mutants is still yours.

**An interrupted battery does NOT restore the tree.** The restore is a
`finally:` in the per-mutant loop, so it runs on the normal path and on any
Python-level exception — and a signal whose default disposition is to terminate
the process never becomes one. Measured by killing a battery mid-band: SIGINT
unwinds through `KeyboardInterrupt` and the file comes back byte-identical, but
**SIGTERM (shell exit 143) and SIGKILL both leave the mutant on disk**, with the
band's `pytest` orphaned and still running against it. `timeout 30 bd-mutate …`
reproduces it exactly: exit 124, mutant still there. So every unattended way a
battery dies — an agent's command timeout, `timeout(1)`, an orchestrator reaping
a slow job — is a way it dies dirty, and Ctrl-C, the one case the `finally`
covers, is the case that does not happen when nobody is watching. Nor is Ctrl-C
a reliable way to test this: a bd-mutate backgrounded with `&` from a
non-interactive shell inherits SIGINT as SIG_IGN (`SigIgn` bit `0x2` in
`/proc/<pid>/status`), so `kill -INT` does nothing and the battery runs to
completion looking like a clean restore.

**A second run then launders the residue into the baseline.** `bd-mutate` reads
the "original" it will restore from the WORKING TREE, never from git, and its
restore check compares that same text back — so a battery started on a dirty
tree adopts the mutant as pristine and puts it back, reporting a clean
sha256-verified restore. Measured: a tree left at `len(hits) > 99` where `> 1`
belonged ran a full battery, scored an ESCAPE, and restored `> 99`. The verdict
was precise, well-evidenced, and about code that is not in git. A before/after
check cannot see that "before" was already wrong — section 0, inside the tool
this section tells you to trust.

**@875 added an on-disk journal, and you need to know what it does not cover.**
Before the first write, bd-mutate records the file, both shas and the original
bytes under `<git-dir>/bd-mutate-inflight/<pid>.json`, and deletes it only once
the restore verifies. The next battery refuses (exit 2) if that file still holds
the recorded mutant, and `bd-mutate --recover` writes the original back. It is
on disk rather than in a signal handler because that is the one property no
handler can have — it survives SIGKILL and OOM. A trap would have been the
partial fix that reproduces the shape: it converts a reliable failure into a
rare, silent one. **But the journal only sees residue from a battery that ran
WITH it.** A hand-left edit, or residue from a pre-@875 run, is still adopted as
pristine and restored — measured, identical behaviour before and after. So the
next line has not been retired by the fix.

**If a battery did not print its summary, `git status` before you believe
anything, and before you re-run.** `git diff -- <the files in your spec>` is the
check and `git checkout --` is the repair; the second run is what makes the
residue permanent. And read "restores by sha256" above for what it is — a
within-run consistency check, not a promise about a tree you did not watch it
leave.

**Stale bytecode defeats a same-length mutate-and-restore.** Python invalidates
a `.pyc` on `(mtime, size)`. A substitution of equal length (`lid` -> `hid`),
restored inside the same wall-clock second, changes neither — so the next run
imports the MUTANT while the source file's sha256 matches the original. Measured
at v3.66.848: a battery's postflight went red on a tree it had just proven
byte-identical, and the obvious reading was "the restore corrupted something".
It is the v3.66.161 footgun wearing a different hat. Run mutation batteries with
`PYTHONDONTWRITEBYTECODE=1` **and** purge `__pycache__` around each mutant, and
say which you did.

**Closing a mutation escape means proving RED in BOTH directions.** The test
must FAIL with the mutant applied and PASS on the real code. Proving only the
second is the default mistake and it is *invisible* in this phase, because
everything is green either way — a test that passes on both is not a test. Also
assert the over-sensitive direction in the same test: a fix for "reports clean
when blind" that simply calls every scan inconclusive passes the escape's test
and destroys the tool.

**An escape can be a HARNESS defect rather than a missing test, and those are
the ones that survive a battery.** Two shipped in one cut: a fake `curl` that
answered every URL identically — so `/api/health` and `/` could never disagree,
which is the only condition the check under test exists to detect — and a fake
`python` returning exit 0 for every `-c`, so a JSON read-back was unobservable
and a truncated file reached "VERIFIED". A test cannot catch what its harness
cannot distinguish. When an escape looks like an oversight, check whether the
fixture can even represent the failure.

---

## 7 | What git changes, and what it does not

This repository is new. Most of BD's tooling was built in a world with **no
version control**, where the only reference was the previously shipped zip.

**Now genuinely easier:** diffing work against a baseline (`git diff` replaces
zip comparison), tracking deletions, checkpointing (branches replace snapshot
tarballs), bisecting a regression, and reviewing a cut before it ships.

**Changed — the deploy path is now git.** The box updates with
`git fetch origin main` + `git reset --hard origin/main` + a service restart.
There is no zip overlay and no zip fallback. Deletions therefore propagate
natively, and the orphan class that `tools/deploy_manifest.py` and
`bd-deploy-manifest` exist to detect can no longer occur. Two consequences are
**not** improvements: `git reset --hard` has no equivalent of `unzip -x`, so it
discards operator live-edits that the overlay was configured to preserve (see
`GATE_AUTHORITY.md` section C); and it moves files without making the running
system match them, which is the first item below.

**Unchanged — do not assume git fixed these:**

- **A deploy moves files. It does not make the running system match them.**
  Four gaps survive the move from `unzip -o` to `git reset --hard`, because not
  one of them was ever a property of the overlay: `__pycache__/*.pyc` are not
  cleared (the v3.66.161 stale-bytecode footgun is unchanged); gitignored
  generated artifacts are not refreshed; the service is not restarted; and
  `frontend/dist/` is not delivered **at all** — it holds zero tracked files and
  is gitignored, so a missing or stale bundle is a silent 503 from
  `bulk_downloader/app.py`. Rebuild it with `cd frontend && npm ci && npm run
  build` whenever SPA source changed. Treat this as a condition to re-derive,
  not a list to memorise: anything generated-and-ignored joins the set.

  **`scripts/deploy.sh` now does all of this (v3.66.848), and you should use
  it.** It fetches and resets, runs `pip install -r requirements.txt` followed
  by a requirement RESOLUTION check (`tools/check_requirements.py`, because
  `pip install -r` exiting 0 is not proof every requirement is present and
  `pip check` cannot see one that was never declared), clears `__pycache__`,
  regenerates the gitignored artifacts, rebuilds `frontend/dist` and reads
  `index.html` BACK, restarts, and verifies `/api/health` and `GET /`.

  **A FAILED DEPLOY IS NOT A NO-OP, AND IT CAN LEAVE THE SERVICE DOWN.** The
  script stops the unit at step 8 and clears bytecode at step 9, so anything
  that fails in between parks the box with the service INACTIVE. Measured at
  v3.66.1035: an orphaned test run on the target was writing `.pyc` files while
  step 9 tried to remove them, `rm` failed with "Directory not empty", and
  test4 sat down until someone looked. Two lessons, and the second is the one
  that cost time: **check `systemctl is-active` before retrying a failed
  deploy** -- the retry re-runs the stop, so a second failure looks identical
  and tells you nothing new -- and **no test run may be in flight on the
  target**, which is why the preflight now refuses one.

  **One property of it matters before you edit it: every step after
  `git reset --hard` executes the PRE-reset copy of the script.** Git renames a
  *new inode* over the path and the running bash keeps reading the fd bound to
  the old one — verified by experiment, with `ls -i` changing across the reset,
  not reasoned from "bash reads lazily". An improvement to a post-reset step
  therefore lands one deploy late. That is left deliberate rather than fixed:
  re-exec'ing the post-reset copy would change which code the operator
  authorized to run. The corollary is that the FIRST landing of any change to
  the deploy script is still yours to do by hand.

  **In a *cloud container* none of this applies:** `scripts/cloud-setup.sh` runs
  `npm run build` and then reads `frontend/dist/index.html` back, failing the
  provision if the bundle is absent — exit 0 from `vite build` is not the
  property anyone depends on.
  Two tests fail without it and neither names the cause:
  `test_v3_66_790_nuitka_config::test_data_dirs_all_exist_in_tree` ("declared
  data dir does not exist: frontend/dist") and
  `test_phase1_root_flip::test_missing_asset_is_404_not_spa_html` (503). They
  were the last two failures a session had to wave away as environmental, so a
  future occurrence is now real signal rather than noise.
- **Gitignored generated artifacts still go stale, and `git clean -fd` will not
  remove them** -- that needs `-x`. `reports/gui_parity_inventory.json` is
  gitignored and build-time generated, so a stale copy left by an earlier deploy
  or provisioning run reads as parity drift and fails the **entire** suite:
  observed at v3.66.818 as
  a single failure, `only-regen=['pytest_capture_results']`, on an
  otherwise-green 13389-pass run. The durable fix is to **regenerate, not
  delete** -- `install_linux.sh`, `capture.sh` and
  `scripts/provision_test_host.sh` all regenerate it now.
- **`.claude-env-report.md` is in this class**, and it is worse because its own
  header instructs the reader to trust it. It is gitignored, survives
  `git clean -fd`, and is written once per provisioning run — one was found
  seven days old asserting v3.66.811 against a v3.66.818 tree. Check its
  `generated_against_version` / `generated_against_commit` header before
  believing any row in it. UNKNOWN provenance is not the same as current.
  `venv/bin/python toolchain/bin/bd-env-report-check` answers this for you:
  FRESH (0), STALE (1), UNKNOWN (2). In a container provisioned before
  v3.66.818 it returns 2, because a report that cannot be dated is
  indistinguishable from one written against another tree.
- **Band derivation is still required.** Tests are not derivable from a diff;
  blast radius follows the denominator.
- **The guard SHAs still apply.** Git history is not authorization.
- **The box is still the gate.** Sandbox green is necessary, not sufficient.
  The band lanes an agent runs in a container are evidence *toward* a cut, never
  evidence the box is green -- and they can differ in **both** directions, not
  just the optimistic one: `test_v3_43_80_modules` false-FAILS in a container
  without GTK typelibs and passes 49/49 on the box, so a container result can be
  pessimistic about real code and optimistic about the environment at once.

  **BUT THE GATE HAS A NAMED BLIND SPOT: `capture.sh` CANNOT SEE CROSS-FILE
  STATE LEAKS.** Measured at v3.66.1034 -- test6 passed capture at **15547
  pass / 0 fail** while the tree carried all 14 of item 48's `sys.modules`
  leakers, and a plain `pytest tests/` on the same commit failed between 5 and
  35. The lane split is why: `capture.sh` runs a serial lane and a
  deselected parallel lane, which do not reproduce the co-batching that a
  whole-suite run produces. So a green capture is not evidence about that
  defect class **in either direction**, and item 48 was invisible to the box
  gate for as long as it has existed. When the question is cross-test
  interference, the instrument is a full `pytest tests/` under matched load,
  not a capture.
- **GitHub CI green is not YOUR CUT'S test evidence -- but it is no longer "no
  tests at all", and this bullet said so for 85 releases after it stopped being
  true.** Read `.github/workflows/ci.yml` before treating a green check as a
  result; do not read this paragraph instead, which is the mistake it used to
  cause.

  **What it actually runs, re-measured 2026-08-08 at v3.66.958. THREE jobs, not
  two, and the gate lane is no longer inside `gates`.**

  - `postgres-integration` -- four mod3 files.
  - `gates` -- gitleaks, the generated-artifacts sync check, `compileall`,
    advisory `pyflakes`, the CHANGELOG ASCII check, and exactly ONE pytest
    file: `test_v3_66_939_ci_gate_shards_cover_every_gate`. These must run
    once, which is why they are not sharded.
  - `gate-suites` -- a 3-way matrix carrying the 15 repo-wide gate suites:
    **toolchain** (`test_toolchain_534`); **parity-graph** (`test_gui_parity`,
    `test_import_graph_no_new_edges`, `test_v3_66_653_dep_freshness`,
    `test_route_index_in_sync`); **artifacts-pins**
    (`test_generated_artifacts_are_not_tracked`,
    `test_source_windows_do_not_shift`, `test_pk_mirrors_stay_retired`,
    `test_pin_index_in_sync`, `test_all_sources_parse`, `test_versync_gate`,
    `test_settings_center_slice4`, `test_v3_66_799_audit_tool_selftests`,
    `test_release_hygiene_gates`, `test_scan_version_pins_fixture`).

  **THE PRIOR TEXT NAMED A FILE THAT DOES NOT EXIST.** It said
  `test_pk_mirrors_do_not_drift`; the file is `test_pk_mirrors_stay_retired.py`,
  and the rename went with the mirrors being RETIRED rather than merely
  drift-checked. A session banding from this paragraph passed the wrong path to
  pytest and got `file or directory not found` -- `ci.yml` was correct
  throughout. It also said "two jobs" after the shard split, and described the
  15 suites as running inside `gates`, where none of them now run. Three wrong
  claims in one bullet, in the bullet whose own next paragraph explains what
  the last stale version of it cost.

  Sharding adds exactly one failure mode -- a suite that falls out of every
  shard still leaves a green tick -- and
  `test_v3_66_939_ci_gate_shards_cover_every_gate` is the only thing that would
  notice, which is why it is the one pytest file `gates` still runs itself.

  **A GATE CI DOES NOT RUN IS A GATE THAT DOES NOT EXIST, and that test's own
  `_DECLARED` set is HAND-PINNED, so it cannot notice a gate nobody declared.**
  944 and 947 were never added. Then 1031 and 1034 were written by a session
  that had just read the note about 944 and 947, and were also never added --
  a repo-wide leaker ratchet that would have fired for nobody. Wire a new
  repo-wide gate into a `gate-suites` shard AND into `_DECLARED` **in the cut
  that creates it**; a follow-up cut is a cut that does not happen. Corrected
  at v3.66.1035, which added the `isolation` shard.

  **The prior text was written at v3.66.847 and was correct then**; @849 added
  the lane and nothing updated this bullet, so for 85 releases the contract told
  every agent that a red gates job could not be about their tests. Measured
  consequence, 2026-08-07: CI failed v3.66.934 on `test_import_graph_no_new_edges`
  -- a real, correct catch of a missing baseline re-freeze -- while this file
  said the job ran no pytest. **This bullet is now the worked example of section
  1's own rule: re-derive from `ci.yml`, do not quote from here.**

  **What is still true, and is the point.** CI's denominator is
  file-INDEPENDENT: gates whose subject is the tree itself, chosen because CI
  does not know what your cut changed. It says nothing about the changed
  module's own suites, so a cut can still be green there while its battery is
  red -- measured at v3.66.847, both checks passed on a commit carrying **14
  deliberately failing tests**, and that remains possible for any failure
  outside the 15. The derived band is that evidence, and the box is still the
  gate. What CI does catch is real: a stale generated artifact, an unbumped
  pin, a new import edge, a shifted source window -- exactly what a cut
  forgets, which is why `bd-regen-order` must run after the LAST source edit.

  **Its own budget is breached and the decision is open.** The step's comment
  says "Measured 2026-08-03: 81 tests, 52s. Keep it under a minute; if it grows
  past that, split rather than silently dropping files." It is now 161 tests
  and 140s -- double the tests, and well past the minute. Recorded in
  SESSION_CARRY as an open decision (split the job vs. raise the budget)
  because changing a CI job is a build change and needs the operator.

**The post-deploy checklist, and the step that keeps getting left off it.**
A v3.66.824 handoff listed four box actions -- deploy + clear `__pycache__`,
rebuild `frontend/dist`, restart the service, re-pin the graph hash -- and read
as complete. It omitted the gate itself, so the session that inherited it
prepared the box and never verified it. Every item above is **preparation**;
only the last line is verification:

```bash
cd ~/BulkDownloader && ./capture.sh --workers=$(nproc) > /tmp/capture.log 2>&1
echo "exit=$?"          # unpiped, per section 5
```

`--workers=N` is parsed at `capture.sh:67` and forwarded to `pytest -n N
--dist loadfile`; it affects **only** the `capture_parallel` lane (measured 176
files / 1458 tests at v3.66.824 -- ask `--collect-only -m capture_parallel`, not
grep, because `tests/conftest.py`'s `pytest_collection_modifyitems` assigns a
lane to every item and the in-file markers are only manual overrides). The
serial lane is hardcoded `-n 0` and no flag can widen it. Re-pin the graph hash
**before** running capture, or step [2b] reports drift and `capture_verdict.py`
turns that stage exit into a whole-capture FAIL.

**Box runtime facts a fresh session will otherwise guess.** Neither is derivable
from this file or the systemd unit, and both were guessed wrong in one session,
each producing a convincing failure signal about the wrong subject:

| fact | value | source |
| --- | --- | --- |
| service unit | `bulkdownloader` | `install_service.sh:88` |
| port | **5555** (`BD_PORT`, host `0.0.0.0`) | `downloader_ui.py:224-228` |
| liveness | `GET /` and `GET /api/health` -> 200 | -- |
| running version | `tools/deployed_version.txt` | rewritten by `ExecStartPre` on every start, so it reflects the **process**, not the tree |
| timezone | **`Etc/UTC` (UTC, +0000)**, NTP active | measured on the box 2026-08-01 via `timedatectl` |

The timezone is load-bearing for anything comparing a stored stamp to a local
date, and it is the reason a whole class of bug stays **dormant here**. The
queue table's `ts_updated` is UTC (SQLite `strftime(...,'now')`) while the
day-window consumers compare a LOCAL `%Y-%m-%d`; at `Asia/Tokyo` that loses 9
of 24 hourly instants and at `Pacific/Kiritimati` 14, but at UTC it loses none.
v3.66.825 fixed the clash anyway (`runner_util._utc_iso_to_local_iso`), because
correctness should not depend on the host's zone -- but **do not cite that cut
as having repaired live miscounting on this box; it did not.** Two sibling
fixes name the same trap: `bw_chart.py:38-50` and `storage_tier.py:297-304`.

The corollary matters more than the value: a UTC box **cannot reproduce**
timezone defects, so a green `./capture.sh` here is silent about them. Tests
for that class must force `TZ` (`os.environ["TZ"]` + `time.tzset()`) and
exercise both signs, or they prove nothing on this host and fail only somewhere
else.

There is no `/api/version`. The only version route is `/api/dev/version_check`
(`app_dev_maint.py:16`) and it is dev-mode gated. A `000` from curl means
nothing was listening on the port you chose -- check the port before concluding
the service is down; a `503` is the real failure, and means the SPA bundle was
not found.

**The squash-merge branch trap — CLOSED at source, 2026-08-01.** The repo now
has GitHub's **"Automatically delete head branches"** enabled, so a merge
removes the head branch and the next push *creates* it fresh. No stale ref, no
non-fast-forward, no force.

**Verified by experiment, not by reading the setting.** Enabling it acts only on
FUTURE merges — it never retroactively deletes branches merged earlier — so the
branch listing is byte-identical whether it is on or off, and nothing readable
from a session distinguishes them. PR #104 was an empty commit on a throwaway
branch (`claude/test-branch-autodelete`) created specifically so it carried no
pre-setting history to confound the result; on merge (`3e9a4ff`) the branch was
gone. That is the whole evidence, and it is why a bare "the setting is on" was
not written here first.

**The mechanism it fixes, kept because the fallback still needs it.** Squash
writes a *new* commit on `main`. A topic branch does not follow it, so
`origin/<branch>` still points at the pre-squash commit and the two have **no
common tip** even though their content is identical. The next ordinary push is
rejected non-fast-forward, and the tempting reflex — `--force` — is the one that
can discard someone else's work.

**When the sequence below is still required:**

- a branch **merged before 2026-08-01**, which nothing will collect.
  (The original population -- 21 no-merge-base branches plus a stale merged
  handoff branch -- was reconciled and DELETED 2026-08-01; the remote now
  carries `main` only, and the family is archived in the operator's verified
  bundle on the box. See SESSION_CARRY 15.4. The case can still arise for any
  future pre-setting branch.);

  **`git bundle verify` IS NOT PROOF A BUNDLE CAN BE RESTORED FROM.** Measured
  on the box 2026-08-08: a second bundle in the operator's home verified clean
  -- *"records a complete history"*, *"is okay"* -- and then failed to fetch
  into a fresh empty repo with `did not send all necessary objects`. `verify`
  only asks whether the bundle's declared PREREQUISITES are satisfiable, and
  prints "complete history" when the header declares none; it does not walk the
  packfile. Run from inside a populated repo the missing objects are present
  locally anyway, so there is nothing for it to notice -- a gate reporting OK
  over a subject it cannot see, in git's own tooling. The archive above was
  certified with exactly that check. It happens to pass the stronger one, which
  was established only as a side effect of an unrelated comparison. **Any
  bundle you intend to rely on gets the restore test, not the verify:**

  ```bash
  T=$(mktemp -d) && git init -q "$T" && \
    git -C "$T" fetch "$BUNDLE" 'refs/*:refs/restored/*' && echo RESTORABLE
  ```
- the setting being turned off, or a fork PR, where it does not apply;
- any push rejected non-fast-forward for a reason you have not identified —
  in which case the diff below is how you find out, not a formality.

Do not treat auto-delete as a licence to `--force`. The sequence is: prove the
content is already merged, then force **with lease**.

```bash
git fetch origin main
git diff --stat origin/main origin/<branch>     # MUST be empty
git reset --hard origin/main                    # continue from the merged tip
# ... new work, commit ...
git push -u origin <branch> --force-with-lease
```

The two-dot `git diff` is the load-bearing step: empty means the remote branch
carries nothing `main` lacks, so replacing it loses nothing. **Non-empty means
stop** — something is on that branch that was never merged, and forcing would
delete it. `--force-with-lease` is not optional; it refuses if the remote moved
since your last fetch, which is exactly the case where `--force` would
overwrite a collaborator.

**gitleaks CI has two traps, and they compound.** `.gitleaks-baseline.json`
holds **PATH-scoped** fingerprints (`file:rule:line`) because it was generated by
a directory scan, while `gitleaks-action` scans the **PR's whole commit range**
and emits **COMMIT-scoped** ones (`sha:file:rule:line`). The forms never match:

1. **The baseline cannot suppress a finding on a line your commit touches.**
   Earlier PRs passed only by never editing those lines; editing one re-scans it
   as new. `toolchain/bin/bd-redaction-compiler:44` carries an `api_key=` literal
   with 16 hex characters and is baselined path-scoped — **that line is armed.**
   The value is deliberately NOT reproduced here: the first version of this very
   paragraph quoted it, and gitleaks failed the PR on `CLAUDE.md` itself. A
   document that names a secret becomes a place the secret lives. Cite the
   location, never the value — and note that no follow-up commit could clear it,
   exactly as point 2 says: the commit had to be amended.
2. **A leak already in branch history cannot be fixed forward.** The range scan
   still sees the earlier commit, so a follow-up that removes the string changes
   nothing. Measured: the fix commit was clean and CI still failed, naming the
   *original* commit. The only remedy is to squash, so the secret appears solely
   as a REMOVED line.

Corpus values in a test that asserts about credentials must therefore be
zero-entropy repeats, not realistic-looking strings — and say so in a comment,
because the obvious "improvement" is to make them look real again.

**GitHub's own merge commits are not yours to re-author.** A squash lands as
`GitHub <noreply@github.com>`, signed with GitHub's web-flow key — it shows as
**Verified** on github.com and reports `%G? == E` locally only because that key
is not in the container's keyring. Never `--amend --reset-author` one: it is
published history on the default branch, and the deploy host updates with
`git reset --hard origin/main`, so rewriting it moves the tree under a running
deployment.

---

## 8 | Layout

```
bulk_downloader/     the application (.py)
tests/               test files (+ corpus/ and fixtures/ assets)
tools/               build, graph, regen, and gate scripts (.py)
frontend/            React/TS SPA (its own node_modules, not committed)
toolchain/bin/       bd-* operator tools (the "bdsuite")
project-knowledge/   durable docs, schemas, and cards
docs/repo/           environment and layout references
```

Sizes are deliberately not written here. Every count in this block has been
wrong at least once, and section 1 applies to this file too: measure at
decision time.

```bash
find bulk_downloader tools -name '*.py' | wc -l    # per directory as needed
find tests -name 'test_*.py' | wc -l
ls toolchain/bin/bd-* | wc -l
```

**This is the only agent-facing contract. There is no second one.**
`CODEX_HANDOFF.md` was retired at v3.66.842; its 34-task program's open groups
live in `project-knowledge/SESSION_CARRY.md` 15.15. It was a second document an
agent read before acting, describing a *different machine*: it once shipped 14
commands against a dot-prefixed `venv` that does not exist here, while this file
said otherwise, and a session followed the wrong one and reported seven failures
that were not real. A gate existed to keep the two from contradicting each
other; removing the second contract removes the failure class the gate was
watching for, which is the stronger fix. If you find a second agent-facing
document, that is the defect — not a resource.

**LOOK IN `toolchain/bin` BEFORE YOU HAND-ROLL ANYTHING.** There are roughly
240 bd-* tools -- count them (`ls toolchain/bin/bd-* | wc -l`) rather than
trusting this sentence, which sat at "240" for releases while three
instruments measured 238 (v3.66.1029). This block's own closing rule already
says sizes go stale here; the tool count was the one number exempting itself. A session spent a day hand-rolling band derivation and mutation
harnesses that already existed, got a narrower band every time, and rebuilt the
same defective harness repeatedly. The ones that answer questions THIS FILE
asks you to answer:

| question this file makes you ask | tool |
| --- | --- |
| what is the band for this diff? | `bd-band-derive` (section 4) |
| do the guard SHAs still hold? | `bd-guardcheck` (section 2) |
| are the generated artifacts in sync? | `bd-regen-order` (section 2) |
| is `.claude-env-report.md` current? | `bd-env-report-check` (section 7) |
| do my tests actually constrain the code? | `bd-mutate` (section 6) |
| is this band list about to trip a footgun? | `bd-bandcheck` |
| has any doc or register claim gone stale? | `bd-freshcheck` |

Do not treat that as the list — it is the four this document already depends
on. `ls toolchain/bin/ | grep <topic>` before writing a script, every time.

**AND READ THE DOCSTRING OF THE TOOL NEAREST YOUR PROBLEM.** Hard-won findings
live there and this file does not index them. `bd-mutation-test`'s docstring has
recorded the detector-with-the-bug-it-hunts shape since **v3.66.737** — "the
tool built to hunt gate-blindness was itself a blind gate" — years of sessions
before section 0 gained the same paragraph from rediscovering it. When you find
a lesson, ask whether some tool already learned it.

**BUT A DOCSTRING IS A CLAIM, NOT A MEASUREMENT — CHECK WHAT THE TOOL RUNS.**
Measured at v3.66.950, and it cost a wrong recommendation to the operator before
it was caught. `bd-fullsuite` opened *"run the ENTIRE tests/ suite in-sandbox,
**correctly**"* and argued convincingly for per-file process isolation. All true,
and it never mentioned that it delegates to a **pytest STUB** — `run_tests_core`,
whose own docstring says *"NOT a replacement for pytest in production"*. Reading
one docstring and stopping produced "this tool already does what you want, point
the contract at it", which was exactly backwards.

Two questions, and the second is the one that gets skipped: what does this tool
CLAIM, and what does it EXECUTE. `grep -n 'subprocess\|pytest\|run_tests'` on the
tool answers the second in seconds. The same check found that `bd-band` and
`bd-parband` — the band runners section 4 mandates — were routing through that
same stub, and that **86% of what they reported was manufactured**.

**A related shape, same cut: `bd-parband`'s selftest asserted its delegation
target was PRESENT.** That file is unconditionally present in a checkout, so the
check could not fail for the reason that mattered and reported PASS over the
wrong runner indefinitely. **Presence of a file is not reachability of a
runner** — ask the interpreter, not the filesystem.

**Two populations share the word "tools":** `tools/**/*.py` and the
`toolchain/bin` bd-* suite. They are **disjoint** populations with different
members, and several checks disagree only because they count different ones —
a denominator mismatch, not rot. Their totals have at times been far apart and
at other times identical, so never read equal counts as evidence the two sets
are the same, or unequal counts as evidence something rotted. Re-derive both,
then ask which one the check in front of you means.

---

## 9 | Working with Matt

- Terse directives ("go", "cut", "1", or a bare file upload) mean **full
  authorization within the established scope**.
- "hold" / "wait" means stop immediately.
- Read-only analysis and planning are free. **Runtime, build, version, guard, and
  release changes need explicit per-task authorization.**
- **MERGING IS THE ONE STANDING EXCEPTION, granted 2026-08-09: you may merge your
  own PR once the derived band is green AND CI is green.** Both, measured, not
  one standing in for the other -- CI's denominator is file-independent and says
  nothing about the changed module's suites, which is the whole reason section 7
  says a green tick is not your cut's test evidence. Everything else in the
  bullet above is unchanged, and this is not authority to deploy: the merge puts
  the commit on `main`, and the box is still updated and gated by Matt.

  It is written here rather than only in the register because a standing grant
  cannot be re-derived from source -- the same reason the panel's environment box
  is transcribed in section 5. Before this, section 9 said the opposite, and a
  session that read it would either ask for permission it already holds or read a
  merge in the history as unauthorized.
- **DEPLOY AUTHORITY IS GRANTED, 2026-08-11: you may deploy to test4/test5/test6
  yourself.** This REPLACES the standing "he deploys" rule that sat in the next
  bullet for the whole life of this file, and it is written here for the same
  reason the merge grant above is: a standing grant cannot be re-derived from
  source, so a session that reads only the code will get it wrong in whichever
  direction the stale text points.

  What did NOT change: `scripts/deploy.sh` is the mechanism (section 7 -- it
  clears `__pycache__`, rebuilds `frontend/dist`, and reads `index.html` back,
  none of which `git reset --hard` does); the graph hash is re-pinned BEFORE a
  capture, not after; and a deploy is still an outward, hard-to-reverse action,
  so it gets said out loud rather than folded silently into another task.

  Know which host you are deploying. On test5 the deployed tree and the agent's
  working tree are the SAME directory, so "deploy" there means the thing you
  have been editing becomes what the service serves.
- Report honestly over optimistically. Results first, no narration, no
  aspirational documentation. If something is unverified, say which part.
- He runs the full suite and the capture himself, and **you still never claim a
  state on a box you have not measured or been told** -- that half is unchanged,
  and it is the half that has cost sessions.

---

## 10 | Before you claim anything works

Run the check and paste the real output. "Should work", "looks correct", and
"the tests should pass" are not verification. If you could not run it, say so and
say why — an honest unknown is worth more than a confident guess, and this
project has been burned specifically by numbers nobody measured being written
down and then inherited as truth.
