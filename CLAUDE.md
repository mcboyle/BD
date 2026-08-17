# CLAUDE.md — operating contract for BulkDownloader

You are working on **BulkDownloader (BD)**: a self-hosted Flask + Playwright +
React/TypeScript SPA batch video downloader. Single developer/operator (Matt),
deploying to `/home/mboyle/BulkDownloader` on **six** headless hosts as of
2026-08-14 — `test5` (`7b4ea932c297`, the master and the live service), `test4`
(`102b31c04e7b`), `test6` (`1d60f39bd8d6`), `test7` (`5b29e22f94aa`), `test3`
(`cec56f663148`) and `test2` (`978caa5c0337`). The first four were rebuilt to
one spec on 2026-08-12; `test3` and `test2` were provisioned from bare metal on
2026-08-14, `test2` deliberately as a SECOND independently-built ext4 host, so
that "ext4 causes the wedge" could be told apart from "test6 is a weird box that
happens to be ext4" (it could: ext4 is refuted). All six machine-ids above were
re-measured 2026-08-14; re-measure with `bd-fleet` rather than quoting any of
it, including from here.

**This sentence read "single deployment target: headless host `test4`" for the
whole life of this file, and was wrong from the second box onward — through
four. It then said "four" from 2026-08-12 to 2026-08-14 while the fleet grew to
six.** So it has gone stale TWICE, undercounting both times, and the cut that
should have caught the second was TITLED `a host list that existed nowhere
tracked` (v3.66.1128) — that cut tracked `docs/repo/hosts.example`, wrote a
five-host figure into a now-retired session ledger, and never touched this paragraph.
It was corrected 2026-08-14 only because the operator said "It is 6" out loud;
no gate reported it, and none can.
It is the first thing every session reads, so it set the frame for
everything after it, and nothing in the tree could contradict it: no `.py` or
`.sh` resolves or branches on a hostname, so a wrong host count breaks no test
and fails no gate. This file's own section 1 is the lesson — a claim about
BEHAVIOUR passes every freshness gate untouched, because those gates ask whether
a cited PATH still resolves, never whether the sentence around it is still true.

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
that wastes a session. What survives is narrower than what used to be written
here: **the DEPLOY PATH resolves no hostname.** It is still `git fetch` +
`git reset --hard` run *on* whichever box you are on.

**THE BROADER CLAIM THAT USED TO SIT HERE WAS FALSE, AND ITS DENOMINATOR IS THE
LESSON.** It read "nothing in any `.py` or `.sh` resolves, connects to, or
branches on a hostname", citing a v3.66.1024 re-derivation that found one hit,
in a report string, never branched on. Re-measured at v3.66.1072 over **every
tracked file** rather than two globs: five hit the pattern, and one of them
BRANCHES on it — `toolchain/bin/bd-jobs` sets `here = socket.gethostname()` and
selects local-versus-remote launch with
`if args.host in (None, "", here, "local")`. `downloader_ui.py` has carried a
`gethostname()` call since the initial import, so the sentence was already
wrong when written.

The glob is why. `bd-jobs` is one of the tracked, extensionless,
python-shebang `toolchain/bin` scripts that section 1 warns about **in this
same file** — a `*.py`/`*.sh` predicate cannot see any of them. So the
first thing every session reads carried a false claim for 48 releases,
produced by exactly the denominator failure documented 250 lines below it, and
no gate could notice because both freshness gates ask whether a cited PATH
resolves, never whether the sentence is true.

It cost a real session: `bd-jobs run --host test6` was refused with "test6 has
no bd-jobs — deploy it there first" while the file sat there, executable,
because that hostname branch sent the launch remote and `test6` is a fleet
LABEL with no DNS entry. **When you assert something about "tracked sources",
enumerate `git ls-files` — not `*.py` and `*.sh`.**

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

**WHEN THIS FILE RECORDS A MISTAKE CLASS, CHECK THE CHANGE IN FRONT OF YOU
AGAINST IT — BEFORE COMMITTING, NOT AFTER.** Reading a rule and applying it to
your own diff are different acts, and this file is a monument to the gap: every
worked example below was written by someone who had just read the paragraph
above it. The discipline that actually works is mechanical and takes a minute —
before staging, name the one or two classes your change could plausibly belong
to (a new gate? section 0. a new fixture? section 6's harness rule. a doc edit?
section 4's freshness gates) and re-read only those, against the diff rather
than in the abstract. The cost of skipping it is not a wrong idea, it is a
shipped one: the guard at v3.66.1024 was RED-first, well argued, and fought a
position two existing tests already encoded, which one minute against section 0
would have surfaced.

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
  again, it still cannot read the tracked shell scripts that embed Python
  heredocs (a handful; section 1 carries the measurement and the caveat --
  this line asserted **17** for releases while section 1 retracted it 200
  lines below, so a reader met the wrong number first).
- `scripts/deploy.sh`'s BD_HOME warning was gated on BD_HOME being **exported**
  while `capture.sh` DEFAULTS it a few lines further down, so it stayed silent
  in the common case. (The anchor here read `:55` for releases after an
  inserted block pushed the default to `:101` -- the class `bd-freshcheck`
  cannot judge: the path resolves, the line is in range, the sentence is
  false. Grep the literal `BD_HOME=` rather than trusting a line number.)
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

**A TOOL BUILT TO PREVENT A FAILURE CAN MANUFACTURE IT — SO VERIFY YOU CAN
RECORD BEFORE YOU ACT.** `bd-jobs` exists because an ssh-launched sampler
outlived its killed task by 88 minutes with nothing recording that it existed.
On the tool's own FIRST live invocation at v3.66.1040, `run --host <other>`
started the remote command and *then* tried to register it — and registration
failed, because `bd-jobs` was not deployed on that host yet. The job ran with no
record: precisely the orphan the tool is for, created by the tool, on first use.

The ordering is the rule and it generalises well past ssh. **When an action has
a side effect you cannot undo and a record you might fail to write, prove the
record can be written first, and refuse if it cannot.** An unregisterable host
is a refusal, not a launch. This is the "fix reproduces the shape of the defect"
paragraph above in its purest form — the tool whose entire subject is untracked
work produced untracked work — and note what caught it: not the eleven tests
that shipped with it, but typing the command in once.

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
bd-freshcheck : _DOCS names CLAUDE.md and project-knowledge/IMPROVEMENT_BACKLOG.md
bd-doc-truth  : docs_scanned=<it prints the count and the exclusion itself>
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
- **`git ls-files -- '*.py'` is NOT "the Python files in this repo."** A large
  population of tracked, python-shebang, extensionless scripts lives under
  `toolchain/bin`, and a `*.py` glob reaches none of it. Measure both:

  ```bash
  git ls-files -- '*.py' | wc -l
  git ls-files | while read f; do head -1 "$f" | grep -q '^#!.*python' && echo "$f"; done
  ```

  **THE NUMBERS THAT USED TO SIT HERE ARE GONE ON PURPOSE, AND THE REASON IS
  THIS BULLET'S OWN HISTORY.** It has carried a census three times — each
  measured, each stated as fact, each stale within releases (`.py` grew under
  every one of them), including a total this file PREDICTED from a subtraction
  and got wrong because another term moved. A figure here cannot stay true and
  nothing warns you: the sentence still reads as a measurement. Section 8 says
  `toolchain/bin` is its own population; **type on the shebang, not the
  extension, and state the denominator you used in the same sentence as the
  count.**

  This is not a small distinction. At v3.66.1072 a false claim in this file's
  own front matter — "nothing branches on a hostname" — was produced by exactly
  this glob, because the one file that DOES branch is extensionless.
  Type on the shebang as well as the extension, or state that the extensionless
  population is excluded.

  **The word "executable" was WRONG here for weeks, and the way it was wrong is
  the point.** This bullet used to say "tracked, **executable**, python-shebang"
  — but a large minority of those files are tracked `100644`, not `100755`. An
  auditor who filtered on the exec bit — as the prose instructed — measured
  **1** file under `project-knowledge/` and concluded the paragraph had rotted
  by 200x. It had not. The predicate was over-specified, and *the prose was the
  thing that over-specified it*. (The mode split stated here was itself stale
  when re-measured at v3.66.1072, in both terms and in its characterisation of
  where the `100644` half lives — which is why it is now described rather than
  counted.) Three successive readings of this one bullet were wrong
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

  **HIT AGAIN at v3.66.1077, hunting leaked tmpdirs.** A census asked "does this
  file clean up after `mkdtemp`?" by searching the WHOLE FILE for `rmtree`,
  `yield`, `TemporaryDirectory` and friends — so a file that cleans up in one
  test and leaks in another scored clean, and one that merely mentions the word
  scored clean too. **The ground truth was `/tmp` itself**, not the source:
  enumerate what actually accumulated and work backwards. And enumerate it
  ONCE — a first attempt ran `ls -d /tmp/bd-* /tmp/pytest-of-* /tmp/*`, which
  lists the first two families twice, inflating the total by 19% and reordering
  the ranking it existed to produce.
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
- **A DEFERRAL THAT LIVES ONLY IN PROSE HAS NOT BEEN DEFERRED — IT HAS BEEN
  DROPPED.** The ITEM LEDGER works for exactly one reason: a test reads it.
  `test_register_promises_resolve.py`'s newest check went red **one cut after
  being written**, naming 15.88 and item 33, on a staleness nobody had planned
  for — which is what a machine-visible promise buys. Set that against the
  77-item improvement backlog produced by the same review: the most valuable
  artifact of that session, sitting in an untracked text file no gate reads, so
  nothing but a human re-reading the list could tell a done item from an open
  one. **Fixed at v3.66.1052**: it is now `project-knowledge/IMPROVEMENT_BACKLOG.md`,
  tracked, and `tests/test_v3_66_1052_the_backlog_is_machine_visible.py` reads
  it. The gate checks the FORMAT, not whether a row is honest -- v3.66.1072
  found row 91 sitting OPEN through the two cuts that had already fixed it.
  "Next session will pick this up" is worth exactly as much as the machinery
  that will ask about it, which is usually nothing. Put a deferral where a test
  can see it, or drop it deliberately and say you did — the third option,
  leaving it in a paragraph, only feels like the safe one.
- **NEVER FILTER AT CAPTURE TIME. Capture whole, filter at read time.** A
  `| head`, a `tail -n`, a `grep` in the collecting command discards evidence
  that cannot be recovered without re-running the job, and the thing you
  discarded is disproportionately the thing you needed: the tail of a run is
  where the summary, the verdict and the failure names live. Measured
  2026-08-12: a fleet sampler wrote its per-run log whole and its verdict line
  separately, which is the only reason a `[gw24] node down` at 99% could be
  attributed at all -- a `head`-filtered capture of the same run would have
  shown a wall of dots and nothing else. The rule generalises past `head` to
  any narrowing applied before the artifact is stored, including `--tb=no`, a
  `-q` that suppresses the names you will want, and a summary written in place
  of the log rather than beside it.
- **CHECK ANY INSTRUMENT YOU BUILD FOR ITS OWN BLIND SPOTS, AND STATE THEM IN
  ITS OUTPUT.** This is section 0 turned on the tools rather than on the code,
  and it is the failure with the longest half-life here, because an instrument's
  wrong answer is inherited by everything downstream of it and arrives wearing
  the authority of a measurement. `bd-mutation-test`'s docstring has recorded
  the shape since v3.66.737 -- "the tool built to hunt gate-blindness was itself
  a blind gate". The socket recorder prints its own blind spots on every run
  (child processes, C-level sockets, raw `_socket`, DNS) and that is the model:
  not a caveat in a README, a line in the output the reader cannot skip. Ask of
  anything you build: what can this NOT see, and does its output say so?
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

6. **MEASURE THE COST OF ANYTHING YOU ADD TO EVERY TEST RUN.** An autouse
   fixture, a conftest hook, a plugin, a recorder: each is paid once per test,
   ~15,600 times per capture, and nothing in the suite reports that price. The
   socket recorder is the worked example in both directions -- it earns its
   place, and it also leaked 744 directories under `/tmp` in one session
   because a per-run cost nobody measured was also a per-run artifact nobody
   removed. Time a representative band with and without the addition and put
   the delta in the commit message; a cost you did not measure is a cost you
   will pay forever without noticing.
7. **A DOC-ONLY CUT IS A SHAPE, AND OFTEN THE RIGHT ONE.** Its band is the
   freshness gates and the pin gates, it carries no runtime risk, and it cannot
   break the box. When a finding's whole value is that the next session knows
   it, shipping it as prose beside the code that proves it is cheaper and
   safer than waiting to bundle it with a source change -- and the bundle is
   what usually loses it. The counterweight is section 1: prose goes stale
   silently and is then read as authority, so a doc-only cut still states what
   it MEASURED, at which commit and on which host, rather than what it believes.

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
  `git status --porcelain` over the `generated=(...)` array in `ci.yml` -- read
  the array, not a count; it grew to seven when `STATIC_KB_MANIFEST.json`
  joined -- so it caught it —
  after the push. A generated file is only true for the tree that generated it,
  and nothing warns you: the file still exists and still looks plausible.
- **Untracked files from OTHER cuts contaminate the regen.**
  `tools/build_pin_index.py` counts `(root/'tests').glob('*.py')`, which does
  not care about tracking. Three RED test files staged for later cuts inflated
  `test_files_scanned` by 3. Move out-of-scope work OUT of `tests/` before
  regenerating, or the artifact describes a tree you are not shipping.

**Gates that cannot see untracked files.** Most axis-6 gates enumerate
`git ls-files` (section 4 carries the table; do not count from here -- a count
in a section that does not carry the list can only rot, and this one did). A NEW test file is therefore invisible to them until
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

**A FLOOR MEANS YOU MAY ADD TO IT. IT DOES NOT MEAN YOU MAY DROP FROM IT.**
Measured at v3.66.1037: `bd-band-derive` named 28 files for a `deploy.sh`
change; a hand-picked subset was run instead, omitting `test_deploy_script.py`,
and **23 of its tests were red for two hours without anyone seeing them**. The
subset looked reasonable -- it contained the new test file and the obvious
gates -- which is exactly why the tool exists: the file it knew about and I did
not was the one that broke. Run everything it names, then add. If a named file
looks irrelevant, that is a fact about your model of the change, not about the
file.

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
correct for the code it changed, and CI still went red on an `IMPROVEMENT_BACKLOG.md`
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
grep -rln "ls-files\|rglob\|--collect-only" tests/*.py     # 82 hits; read them all
```

Then keep only the ones whose enumerated path reaches `tests/` or the whole
repo. **The membership question is not "does it walk the tree" — it is "does
editing a TEST FILE change this gate's denominator".**

**COUNT THE `yes` ROWS OF THE TABLE BELOW; DO NOT TRUST A NUMBER IN THIS
SENTENCE.** Four successive revisions stated a count here and every one was
wrong within releases — usually because a later cut in the SAME session added a
member, once wrong in both directions at once (a false "yes" and a missed
member). The prose said **nine** while the table beneath it listed ten, so a
reader who counted and a reader who quoted disagreed, and the file supplied
both answers. That is section 0 applied to the table itself. Treat the list as
a starting point and re-derive:

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
| `test_v3_66_939_ci_gate_shards_cover_every_gate.py` | `git ls-files -- tests/test*.py` for the `BD_GATE_SCOPE` policy (added v3.66.1072) | yes — **and it is the one that FAILS you**: a new test file must declare a scope or sit in the frozen baseline |
| `test_gitignore_rules_actually_match.py` | `git ls-files` → `.gitignore` paths only (`_gitignore_files`, `:60-67`) | **NO — `.gitignore` only** |
| `test_v3_66_820_share_tools_saw_no_session_keys.py` | `git ls-files` → text extensions (`:451-456`) | **NO — `.py` not in the set** |
| `test_playwright_engines_single_source.py` | `git ls-files -z '*.sh'` | **NO — `.sh` only** |

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

**A GREEN BAND AT ONE `-n` DOES NOT RETIRE A CROSS-FILE FAILURE.** Measured over
v3.66.1042-1045 on one commit and one band: **8 failures at `-n 32` on an
86-core box, 0 at `-n 28` on the same box, 0 under xdist on a 64-core box, and
the same 8 again on the next run at 32.** The failing tests pass 14/14 in
isolation everywhere. The cause is a real defect — a test that wipes
`bulk_downloader.*` from `sys.modules` orphans any later test module holding an
import-time `from bulk_downloader import …` binding — and whether it FIRES
depends on whether `--dist loadfile` puts the leaker and the victim on the same
worker, which depends on the worker count, the file count and the machine.

So the failure set is a property of the **schedule**, not of the tree, and three
reflexes are wrong here. A green band does not mean fixed. A red band does not
mean this cut broke it. And a comparison across two hosts says nothing unless
their core counts match. Do not write a core count here -- `bd-fleet` reports
them, and the fleet was rebuilt to one spec on 2026-08-12, retiring the spread
this sentence used to name. Compare a
distribution against the same worker count on the same box, and use `bd-ab`,
which refuses a single sample for this reason. `tests/_run_context.py` now
records the worker count and load beside every result so the comparison is
possible at all.

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
  `subprocess` with no `timeout=`; unbounded HTTP), 6% of the tracked test
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
  BD_DISABLE_KEEPALIVE=1 PYTHONUNBUFFERED=1 venv/bin/python -m pytest tests/ \
      -n 24 --dist loadfile --timeout=240 --timeout-method=thread \
      -p no:randomly
  ```

  Every flag is load-bearing and a different one is a different experiment:
  `--timeout` is what turns a hang into a named test instead of a stall,
  `--timeout-method=thread` dumps its stack, and `--dist loadfile` is the
  distribution that was actually measured. Run it under a whole-run cap as
  well, and wait on a written exit
  marker rather than on `pgrep` (§5's rule about a wrapper matching itself).

  **`-q` WAS REMOVED FROM THIS FORM AT v3.66.1126, AND `-u` ADDED. BOTH ARE
  OBSERVABILITY FIXES FOR A GATE THAT COULD NOT SEE ITS SUBJECT** -- section 0,
  in the sanctioned command itself. Neither changes what is executed.

  `-q` sets `verbose == -1`, and xdist guards its entire crash-recovery
  narration behind `verbose >= 0`:

  ```python
  # xdist.dsession.DSession.report_line, as of pytest-xdist 3.8.0
  def report_line(self, line):
      if self.terminal and self.config.option.verbose >= 0:
          self.terminal.write_line(line)
  ```

  (Symbol and pinned version, NOT `file:line`: these live in `venv/`, are not
  tracked here, and a line-number anchor into a
  dependency would rot silently on the next xdist bump with no gate able to see
  it -- and note that WRITING one here as an example is itself enough to create
  the broken claim, section 0's naming trap, which is why this sentence
  describes the shape instead of spelling it. bd-freshcheck
  resolves anchors against TRACKED files and correctly refused one.)

  So under `-q` these NEVER appear: `replacing crashed worker gwN`,
  `maximum crashed workers reached: N`. Meanwhile xdist's own
  `pytest_testnodedown` hook implementation writes UNGUARDED, so `[gwN] node down: Not properly
  terminated` DOES appear. The reader is shown the symptom and denied the
  response. Measured on a minimal reproducer, same code path, only the flag
  differing: `-q` -> 0 replace lines; no flag (verbose 0) -> 8; `-v` -> 8.
  **Dropping `-q` is sufficient; `-v` is not required** and costs ~16k lines a
  run. `-v` buys only the per-test names on top -- worth it for a wedge hunt,
  not for routine sweeps.

  `PYTHONUNBUFFERED=1` exists because a run that never exits never flushes.
  It is spelled as an ENV VAR rather than as `-u` on purpose: `-u` is an
  interpreter flag and never reaches `sys.argv`, so any gate comparing a built
  command against the argv pytest actually received can never see it.
  bd-sweep-run's selftest caught that as 24 red checks the first time this was
  written as `-u`. Measured across 657
  captures: **15 of 15 WEDGED logs end MID-LINE at a 4KB stdio boundary, and
  642 of 642 COMPLETED logs end with a newline.** A completed run flushes at
  process exit; a wedged one strands everything written since the last flush --
  which is exactly the recovery narration, since it is emitted immediately after
  the crash. Every wedge captured before this change is missing its final ~4KB,
  and absence of a diagnostic in those logs is an ARTEFACT, not a finding.

  **THE GENERAL RULE, because two independent blindfolds here produced the
  identical symptom and fixing either alone would have left the other:** when a
  gate reads a tool's output for a diagnostic, verify BOTH that the tool emits it
  at the configured verbosity AND that the output survives to disk. Absence of a
  message is evidence of nothing until both are established.

  **`-n 24` IS THE FIXED CANONICAL COUNT; HOST CAPACITY DOES NOT REWRITE THE
  EXPERIMENT.** Post-Cut-2 qualification ran the complete collected population
  repeatedly on two compatible hosts. `-n 34` was not faster than `-n 24`, so
  24 is the measured performance knee and the canonical fleet command pins it
  exactly. Keep `-n 4` as the explicit comparison and regression oracle. Any
  other count is a separately labelled diagnostic, and every result records
  the resolved count; a count taken at one worker level cannot be compared with
  one taken at another.

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
- `pgrep -f "<cmd>"` **matches its own wrapper** — and that is the SMALL half.
  The big half, measured at v3.66.1037: a process-liveness check matches **any
  process whose command line merely MENTIONS the pattern**, which on this fleet
  routinely includes the shell that invoked you, the wrapper above it, and the
  agent harness above that. A `deploy.sh` preflight asking "is a test run in
  flight" answered YES on a completely idle host and would have refused every
  deploy forever, because the invoking shell's own argv said `-m pytest`.
  Splitting the literal (`-m[ ]pytest`) fixes only self-match, not this.

  **Match on what the process IS, not on what its command line SAYS**:
  `ps -eo comm=,args=` and require `comm` to be the interpreter, so shells,
  greps and editors quoting the pattern all fall out. Never read a bare match as
  "still running" — check `/proc/<pid>` or a written exit marker. This bites hardest
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
- **AGENT DEPTH IS NOT FREE, AND MORE AGENTS IS OFTEN SLOWER.** Measured at
  v3.66.926: eleven agents were SLOWER at a register re-derivation than doing
  it inline, 3 items against 12, because most of the work was one grep or one
  tool invocation apiece. Concurrency is capped at `min(16, nproc - 2)` per
  workflow, so a deeper tier only lengthens the queue behind the same gate, and
  every additional tier is another writer in a shared tree you did not start
  (section 2b). Spawn breadth for genuinely independent work; spawn depth
  almost never.

- **`BD_HOME` does not protect `~/.config/bulk-downloader`**, which resolves
  from `$HOME`, not `BD_HOME`. That is `tests/conftest.py`'s path-keyed store
  guard's job.

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

- **IN A SHALLOW CLONE, ONLY `--is-ancestor` EXIT 0 IS TRUSTWORTHY.** A 0 means
  a connected path was found, which a shallow boundary cannot fabricate. Every
  nonzero is UNKNOWN until the clone is deepened: **1 means "not in this
  history", 128 means "I cannot see it"**, and conflating them is a gate firing
  on its own blindness with a confident, specific, wrong claim. `bd-freshcheck`
  hit exactly that -- it reported a register section STALE, naming a commit the
  branch genuinely contained.

  **Never repair a 128 with a by-sha fetch -- not because it fails, but because
  it SUCCEEDS INTO A WRONG ANSWER.** `git fetch --depth=1 origin <full sha>`
  delivers the object without the connecting history, so `--is-ancestor` then
  returns 1: a detectable blindness converted into an undetectable false
  negative. Deepen instead, and do not hand-pick the depth -- `bd-freshcheck`
  carries a measured `_DEEPEN` constant for this and re-asks the question
  itself. (A short sha cannot even be used as an existence probe: git reads it
  as a REF NAME and answers `couldn't find remote ref`, and every close section
  names short shas.)

  Nothing runs on the happy path -- the first `is-ancestor` short-circuits, so
  a clean tree does no network and does not touch `.git`. CI carries
  `fetch-depth: 0` on the gate jobs, which is why none of this arms there. The
  boxes are not shallow (`test5`: no `.git/shallow`), so a nonzero on a box is
  authoritative; the machinery is for scratch clones.

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

**AN EDIT SCRIPT MUST MUTATE IN MEMORY AND WRITE ONCE, AT THE END.** Measured
at v3.66.1035: a three-file bump wrote `__version__` and the test pin, then
asserted out on the CHANGELOG anchor -- leaving the tree half-bumped, with a
version nothing recorded. An assertion that fires after a write has already
happened does not prevent the damage, it just stops the rest. Collect every
replacement, assert all of them, then write; a script that fails then fails
having changed nothing.

**AND NEVER RETYPE AN ANCHOR THAT CONTAINS PUNCTUATION -- `rg` THE LITERAL LINE
AND PASTE IT.** This file mixes em dashes and double hyphens unpredictably, and
an anchor is an exact-match string: the same sentence typed from memory with
`--` where the file has `—` fails, and the failure looks like the target having
moved rather than like a typo. Two edits were lost to that in one session.

**`sed -i` IS NOT AN ACCEPTABLE APPLIED-CHECK.** It asserts nothing about
uniqueness, so it will happily rewrite three sites when you meant one and
report success. Section 6's whole subject is that the applied-check is the
safeguard; use the count-plus-length-arithmetic form above even for something
as small as a version literal, where a second match is exactly what you would
not notice.

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

**A HARNESS MUST ASSERT THAT IT BUILT THE SHAPE, BEFORE IT ASSERTS THE
VERDICT.** Three times in the v3.66.1037 cut a test passed because its fixture
produced nothing to judge, and each looked like a green test:

- a fake `ps` printed a PID column that `ps -eo comm=,args=` never emits, so it
  certified a detector that answered wrongly on the real format;
- a fake install directory symlinked a python that was not a venv, so `pytest`
  was unimportable, the process exited before anything observed it, and the
  "detected" case tested an empty process table;
- `bash -c '...; sleep 25'` TAIL-CALL EXECS the sleep, so the process replaces
  itself and the pattern under test leaves its argv entirely. The mutant
  escaped twice before that was noticed.

The fix is one line of discipline: **before asserting the outcome, assert the
precondition** -- that the process exists, that its `comm` is what you meant,
that the file has the bytes you wrote. Section 0's non-empty-denominator rule,
applied to fixtures rather than to gates. Without it, "not flagged" and
"nothing was there to flag" are the same green.

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
natively, and the orphan class that the retired `deploy_manifest` tooling
existed to detect can no longer occur. **Both paths are GONE** --
`tests/test_deploy_manifest_stays_retired.py` enforces their absence, so do not
go looking for them and do not re-add one. Two consequences are
**not** improvements: `git reset --hard` has no equivalent of `unzip -x`, so it
discards operator live-edits that the overlay was configured to preserve (see
`project-knowledge/GATE_AUTHORITY.md` section C); and it moves files without making the running
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
  defect class **in either direction**. Item 48 was invisible to the box gate
  for its whole life and was closed at v3.66.1069 by fixing the one real
  leaker; cite it as the SHAPE, not as a live defect. When the question is cross-test
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
    advisory `pyflakes`, the CHANGELOG ASCII check, the version-pin and
    guard-SHA checks, and `bd-freshcheck`. **It runs NO pytest at all.** These
    must run once, which is why they are not sharded.
  - `gate-suites` -- a **10-way** matrix (**toolchain**, **parity-graph**,
    **measurement-tools**, **isolation**, **tree-gates-1** through
    **tree-gates-4**, **toolchain-verifiers**, **artifacts-pins**) carrying the
    repo-wide gate suites. Read the matrix, not this sentence: the membership
    is `_DECLARED` in `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`,
    and a test asserts the two agree exactly.

    **THIS SAID "a 5-way matrix" AND NAMED FIVE UNTIL v3.66.1131, IN THE SAME
    SENTENCE THAT TELLS YOU NOT TO TRUST IT.** The instruction was already
    correct and already there, and the count still rotted and was still quoted
    -- an agent reading the bullet learned a wrong number and a right method
    from one paragraph, and the wrong number is the part that gets used. It was
    caught only because `bd-ci-verdict` prints the expected set it parses out of
    `ci.yml` on every run, so the disagreement appeared beside a merge decision
    rather than in a document nobody re-measures. That is the general remedy for
    this whole class: put the derived count in a TOOL'S OUTPUT, where it is
    re-derived every time it is read, not in prose that can only decay.

  **THIS BULLET SAID `gates` RAN "exactly ONE pytest file:
  test_v3_66_939_ci_gate_shards_cover_every_gate", AND IT RAN NONE.** Measured
  at v3.66.1071: that job's pytest step was deleted at `f736748` -- the same
  commit that created 939 and sharded the lane -- and 939 was named in that
  diff only inside a comment. It was in no shard either, so **from v3.66.939 to
  v3.66.1071 the only check that would notice a suite falling out of every
  shard was itself a suite that had fallen out of every shard**, and nothing
  ran it on any PR. Wired into the `measurement-tools` shard at v3.66.1072.

  Note which instrument found it: not reading this paragraph, which had been
  read many times, but parsing `ci.yml` and asking which steps contain the
  string `pytest`. Section 1's rule, on the sentence that states section 1's
  rule about CI.

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
  notice.

  **A GATE CI DOES NOT RUN IS A GATE THAT DOES NOT EXIST. `_DECLARED` was
  HAND-PINNED and could not notice a gate nobody declared; since v3.66.1072 a
  new test file must classify ITSELF.** 944 and 947 were never added. Then 1031
  and 1034 were written by a session that had just read the note about 944 and
  947, and were also never added. Then 1062, 1064, 1067 and 1068 -- eight in
  all, the last four found live in the tree at v3.66.1071.

  The mechanism now: every tracked `tests/test*.py` file either declares a
  module-level `BD_GATE_SCOPE` (`"repo-wide"` or `"module"`) or sits in the
  frozen legacy baseline `tests/gate_scope_baseline.txt`, which may only
  shrink. Declaring `"repo-wide"` requires membership of `_DECLARED`, which the
  union assertion then forces into a shard. **So the rule is still "wire it in
  in the cut that creates it" -- what changed is that forgetting is now RED
  rather than silent.**

  Do not try to replace the marker with a derived predicate; it was measured
  and it does not work. Against the eight files that actually went undeclared,
  an AST census over real call nodes catches **3 of 8** on a `git ls-files`
  argument, and **4 of 8** if you also match code naming repo infrastructure --
  which widens the candidate pool from 34 files to 136, buying one hit for a
  124-entry exemption list. 947, 1031, 1067 and 1068 carry no structural signal
  distinguishing them from an ordinary feature test, because a gate is
  repo-wide by virtue of what it ASSERTS ABOUT, which its syntax does not
  record. **The blind spot that remains: nothing checks that a `"module"`
  answer is honest.**

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
  outside `_DECLARED`. The derived band is that evidence, and the box is still the
  gate. What CI does catch is real: a stale generated artifact, an unbumped
  pin, a new import edge, a shifted source window -- exactly what a cut
  forgets, which is why `bd-regen-order` must run after the LAST source edit.

  **READ CI STATUS FROM THE STATUS COLUMN, NEVER FROM A POSITIONAL FIELD.**
  `gh pr checks` prints a check NAME that contains spaces -- `gate-suites
  (toolchain)` -- so `awk '{print $2}'` returns a fragment of the name and not
  the verdict, and a pipeline built on it reports whatever that fragment
  happens to be. Measured 2026-08-12: an `awk`-based summary printed the check
  names with `pass` appearing only on the two rows whose names have no spaces,
  which reads as "2 of 7 passed" on a run where all 7 had passed. Match on the
  status TOKEN instead -- `grep -oE '(pass|fail|pending)'` and count -- so the
  answer cannot depend on how many words someone put in a job name. This is
  section 0's denominator rule applied to a shell one-liner: the field you
  indexed was not the field you meant.

  **The one-minute budget and its remedy.** The step's comment set the rule on
  2026-08-03: "81 tests, 52s. Keep it under a minute; if it grows past that,
  SPLIT rather than silently dropping files." It grew, and **the split
  shipped** at v3.66.939 -- `ci.yml` records that the operator chose it. So the
  standing instruction when a shard runs long is: split it, or ask. **Never
  trim a shard's list to make it fast** -- every entry is a gate that was RED
  somewhere nothing else could see, and a truncated list reads as coverage it
  does not have.

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

`--workers=N` is parsed by `capture.sh` (grep the literal `--workers=*`; the
anchor has moved once already) and forwarded to `pytest -n N
--dist loadfile`; it affects **only** the `capture_parallel` lane -- which is
**almost the whole suite**, not a small slice: measured at v3.66.1102,
`--collect-only -m capture_parallel` collects **15075 of 15869** tests, 794
deselected. This line read "176 files / 1458 tests at v3.66.824" for 278
releases, which is wrong by an order of magnitude and invited the reasonable but
false inference that the lane is a small subset whose behaviour cannot be
compared with a full run. Re-derive it; ask `--collect-only -m capture_parallel`, not
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
| port | **5555** (`BD_PORT`, host `0.0.0.0`) | `downloader_ui.py` (grep `BD_PORT`) |
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
  bundle on the box. Git history preserves that reconciliation. The case can still arise for any
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
`CODEX_HANDOFF.md` was retired at v3.66.842; its surviving open groups are
atomic rows in `project-knowledge/IMPROVEMENT_BACKLOG.md`. It was a second document an
agent read before acting, describing a *different machine*: it once shipped 14
commands against a dot-prefixed `venv` that does not exist here, while this file
said otherwise, and a session followed the wrong one and reported seven failures
that were not real. A gate existed to keep the two from contradicting each
other; removing the second contract removes the failure class the gate was
watching for, which is the stronger fix. If you find a second agent-facing
document, that is the defect — not a resource.

**LOOK IN `toolchain/bin` BEFORE YOU HAND-ROLL ANYTHING.** Count them
(`ls toolchain/bin/bd-* | wc -l`) rather than trusting this sentence. It said
"roughly 240" from v3.66.1029 to v3.66.1141 -- first while three instruments
measured 238, then while the real figure climbed past 250, so it was wrong in
both directions in turn and re-read many times without being re-measured. **A
figure written here cannot stay true and nothing warns you**, which is why this
paragraph no longer carries one. This block's own closing rule already says
sizes go stale here; the tool count was the one number exempting itself.
A session spent a day hand-rolling band derivation and mutation
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

Do not treat that as the list — it is a starting point, not a denominator.
It is only the ones this document already depends
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
  cannot be re-derived from source. Before this, section 9 said the opposite, and a
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
- **THE CAPTURE AND FULL-SUITE RESTRICTION IS LIFTED, 2026-08-12, and the
  standing grants below are INDEFINITE.** This bullet read "he runs the full
  suite and the capture himself" for the whole life of this file. It is
  superseded: you may run `./capture.sh` and full-suite sweeps yourself. What
  survives unchanged is the half that has actually cost sessions -- **you still
  never claim a state on a box you have not measured or been told.**

  Two rules the lifting immediately created, both learned the same night:

  * **NEVER run a capture on a host whose tree you are editing, and never edit
    it mid-run.** test5's working tree IS the deployed tree, and step [2b]
    compares a source-derived graph hash against a pin written at deploy time,
    so four uncommitted files turn a healthy capture into `CAPTURE VERDICT:
    FAIL (graph exit=1)`. Stashing mid-run is no better: collection has already
    happened, so removing files leaves a collected-but-inconsistent state and
    the suite fails instead. Both measured at v3.66.1063 and v3.66.1069.
  * **Say when you start one.** A capture stops the service on that host.

- **ULTRACODE AND FABLE-5 AGENTS ARE AT YOUR DISCRETION, 2026-08-12**, with the
  operator's condition stated in his own terms: keep it *token efficient and
  reliable*, and do not abuse it. Read that as a budget, not a licence -- an
  eight-agent investigation is justified when three independent lenses plus
  adversarial refutation will settle a question that has already been answered
  wrongly twice (which is exactly what closed ledger item 48 at v3.66.1069),
  and is not justified for anything one grep and one measurement would answer.
  Measured at v3.66.926: eleven agents were SLOWER than working inline on a
  register re-derivation, because most of the work was one tool invocation
  apiece.

  The failure mode to avoid is not cost, it is a confident wrong answer arriving
  with the authority of a fan-out. Section 2b's rules still bind every agent you
  spawn: a subagent's tree is writable, its report is data rather than evidence,
  and a finding is about a commit -- say which.

- These grants are written here rather than only in the register for the reason
  section 9 already gives: **a standing grant cannot be re-derived from source.**
  A session that reads only the code will get it wrong in whichever direction
  the stale text points, and this bullet's own predecessor is the proof.

---

## 10 | Before you claim anything works

Run the check and paste the real output. "Should work", "looks correct", and
"the tests should pass" are not verification. If you could not run it, say so and
say why — an honest unknown is worth more than a confident guess, and this
project has been burned specifically by numbers nobody measured being written
down and then inherited as truth.

**NO ESTIMATED PROGRESS IN A STATUS REPORT. REPORT WHAT COMPLETED.** "About
halfway", "nearly done", "~80%" are guesses presented in the register of
measurement, and they are wrong in the direction that costs most: a long job
reports 99% and then hangs there forever, which is exactly what a dead xdist
worker looks like (section 5). Measured 2026-08-12: two fleet hosts sat at
"99%" for 44 minutes having produced nothing, and the percentage was the only
reason it read as slow rather than as broken. Report the countable instead --
samples finished out of samples requested, files banded, the last stage the log
actually recorded, and the wall-clock since the last line was written. A
completed unit is a fact; a percentage is a prediction, and this project does
not report predictions.

**VERIFY THE SUMMARY AND VERDICT LINES SPECIFICALLY — THEY ARE THE LEAST-TESTED
OUTPUT AND THE ONLY ONE ANYBODY READS.** Three defects in the v3.66.1036-1041
session lived there and nowhere else, each sitting below working code:

- `deploy_fleet.sh --dry-run` fell straight through to the success line and
  printed `all 3 host(s) deployed and verified` having touched nothing.
- A bring-up script graded itself with `grep -c "exit=[^0]" "$L"`. **`grep -c`
  prints `0` and exits `1` when nothing matches**, so the check that meant "no
  step failed" reported failure on a run in which every step returned 0. Its log
  survives and it still reproduces on demand: the count line reads `0` and the
  command reports exit 1 in the same breath.
- `bd-jobs list` printed a perfectly consistent `DEAD` for a job whose command
  line had been mangled into `bash -c "-- sleep 90"`.

The body of a tool is exercised by its tests; the last line it prints is
exercised by nobody. Read the verdict against the evidence above it, and be
especially suspicious of a verdict derived from a **count** — zero is the value
every one of these got right and then reported wrong.

**TEST THE SEAM, NOT ONLY THE COMPONENTS.** `bd-jobs` shipped at v3.66.1040 with
eleven passing tests and a green self-test, and its first real use failed
instantly: `argparse.REMAINDER` keeps the `--`, so `run --host X -- sleep 90`
sent `bash -c "-- sleep 90"`, which bash answered with `invalid option`. Both
sides were tested and both were correct — argparse parsed, the registry
recorded, the reaper reaped. Nothing tested the *join*, because every test
either handed `cmd_run` an already-split list or inspected the registry
afterwards, and not one asked what string reached the shell. Downstream the
failure was invisible in the worst way: `list` reported DEAD, truthfully, for a
process that had lived a millisecond, and the whole picture looked consistent.

**AND A GREEN BATTERY IS NOT COVERAGE EVIDENCE — CHECK WHICH PATH IT TOOK.**
Same tool, measured at v3.66.1040: its self-test reported zero failures while
exercising the registry primitives — `register`, `alive`, `load_all`, `forget`,
`proc_starttime` — and not the command path a user actually types. (Do not
quote a check count here; it has moved already. Read the `selftest` body and
ask which entry points it calls.) It is a green verdict over the registry primitives, printed by a tool
whose primary path is *assemble a command, launch it, register it*. Of the
eleven tests, the one that did call `cmd_run` returned at the preflight refusal
before any command was assembled. So ask of any tool before you believe its
battery: **which check executed the thing a user actually types, end to end?**
If the answer is none, run it by hand once. That took ten seconds here and found
what eleven tests and seven checks could not.

**AND WHEN EVERY REFUSAL SHARES AN EXIT CODE, ASSERT THE REASON, NOT THE CODE.**
This one has now escaped twice, from the same tool, one cut apart. `bd-jobs`
refuses with exit 2 for a missing script, a failed copy, an unregisterable host
and an empty command; `bd-ab` refuses with exit 2 for one sample, a missing
file, an unreadable revision and an identical revision. A test asserting
`returncode == 2` therefore passes when ANY of them fires, so a mutant deleting
the guard under test sails through to the next refusal and the code is
identical. Four mutants escaped exactly that way and every one of them looked
like a covered behaviour.

Two things close it, and both are needed. Assert the distinctive words of the
refusal you mean. And **stub the conditions that come after it**, or the check
you are testing is not the only thing that can produce the answer — the
network calls in those cases refused on their own, from a hostname that did not
resolve, in a test that never meant to leave the machine.

**WHEN A FUNCTION HAS N OUTCOMES, ASSERT THAT EACH ONE IS REACHABLE.** `reap`
has three — kill a job it can prove, forget a stale entry, refuse an entry it
cannot identify — and the refusal branch was unreachable as written: `alive()`
returned False for a missing start time, so such an entry was classified stale
and silently deleted while its process kept running, converting a tracked job
into exactly the untracked orphan the tool exists to prevent. The other two
outcomes had tests and passed. Enumerate the branches first, then write one test
per branch that fails if the branch is never taken. **A branch nothing can reach
is dead code that reads as a safety feature**, and it is read that way by the
next person precisely because it is written in the language of safety.

---

## 11 | Context economy

**A session that runs out of window loses the work, so the window is a
resource like any other — and it was never measured here until v3.66.1140.**
Measure visible costs from durable command and result artifacts rather than
guessing, while stating the boundary honestly: no local transcript estimate can
observe the provider's complete context, and recorded thinking blocks may carry
zero visible characters while still consuming reasoning tokens.

The census that produced these rules, over 3,158 transcript rows of one
session, chars/token calibrated at 2.95 on a prose corpus:

| bucket | ~tokens | n | share |
| --- | --- | --- | --- |
| tool_result | 143,374 | 476 | 39% |
| **tool_use:Bash — the agent's OWN commands** | **102,910** | 367 | **28%** |
| assistant_text | 41,691 | 298 | 11% |
| tool_use:Edit | 40,828 | 70 | 11% |

Four rules follow from it, and the second is the one nobody expects.

- **A SWEEP OVER HOSTS OR FILES GOES TO A SUBAGENT.** Its tool output never
  enters the caller's window; only its report does. The session measured above
  used **zero** subagents and spent 39% of its context on raw output it did not
  need in full — the top TEN results were 31% of all result bytes. For fleet
  work `bd-fleet-run` already does this: whole output to a per-host artifact,
  one summary line back.
- **A SECOND HAND-ROLLED HEREDOC IS A MISSING `bd-*` TOOL.** 45 heredocs cost
  27,594 tokens in that session, most of them re-deriving what an earlier
  heredoc had already derived. Section 8 says look in `toolchain/bin` first;
  this is the same rule with a price on it. Writing a program into the
  conversation costs the program *and* keeps costing it on every subsequent
  turn.
- **CAPTURE WHOLE TO DISK, READ A SLICE.** This does **not** contradict
  section 1's NEVER FILTER AT CAPTURE TIME — that rule governs the **artifact**,
  and this one governs the **display**. `cmd > /tmp/x.log 2>&1; echo "exit=$?";
  tail -20 /tmp/x.log` keeps every byte recoverable and costs 200 tokens
  instead of 10,000. A `--grep` that filters what is *shown* is fine; a `| head`
  in the collecting command is still forbidden.
- **ANCHOR AN EDIT ON THE SHORTEST LINE `rg -c` PROVES UNIQUE.** 70 edits cost
  40,828 tokens, because `old_string` carried whole paragraphs of prose. Section
  6 says never retype an anchor containing punctuation — paste it, but paste the
  *shortest* unique one.

**The counterweight, so this is not read as licence to skim.** None of the
above permits reading less than you need: section 1's rule that audit beats
recollection is unchanged, and a narrowed *investigation* is exactly the
denominator failure section 0 is about. What these rules cut is **re-reading
into the transcript what is already on disk** — not looking.

**And measure before optimising.** Every figure above came from running the
census, and the largest single item was a surprise to the agent that had just
spent the tokens. Section 1 applies to this section as much as to any other:
re-derive, do not quote.
