# Lessons learned -- cloud-env provisioning + blind gates (through v3.66.818)

ASCII-only. Durable. Written so the next session does not re-derive what this one
paid for. Every item below was MEASURED in a live container, not reasoned about.
Do not quote this file as authority (CLAUDE.md section 1) -- re-derive at
decision time. Where a number appears it is stamped with when it was true.

Supersedes nothing. Section 1 of LESSONS_LEARNED_v3_66_811.md named
`tools/diag_csrf_bootstrap.py` as an open interpreter trap; this session removed
its root cause (item 8 below), so that specific instance is closed. The general
rule in that file still stands.

---

## 1. A gate can examine NOTHING and report success

`bd-guardcheck` -- named in CLAUDE.md section 2 as THE way to re-derive the seven
release-guard SHAs -- printed this on a clean tree:

    warn: no STATE.json found
      bulk_downloader/extraction_core.py     FILE MISSING
      ... all seven ...
    0 ok, 0 drifted, 7 missing.        exit=0

All seven files existed and matched the pinned table. `if drift: return 1` was
the only non-zero path, so MISSING never failed. Anyone reading the exit code
concluded the guards were verified.

The lesson is not "that tool had a bug". It is that **a summary line with zero in
every bucket is a failure signal, not a pass**. If buckets do not sum to the
expected denominator, refuse. Unknown is a third state and it FAILS.

## 2. Hand-pasted copies fork, and the fork is invisible to every gate

The Claude Code panel held a full copy of `scripts/cloud-setup.sh`. Pasted copies
do not receive commits. Measured 2026-07-27: the panel copy was the `fa97020`
vintage -- 3 commits and 91 lines behind, still carrying a guard pin the tree had
moved past and a GTK list missing `x11-utils`.

Meanwhile 13 tests in `tests/test_provision_test_host.py` asserted over the REPO
copy, which never executed. Green tests about a file with no bearing on the
environment they ran in.

**The remedy is not a better gate over the fork.** It is to leave nothing in the
pasted surface worth forking: `scripts/cloud-bootstrap.sh` locates the checkout
and `exec`s the repo copy, and a test fails on any install verb added to it. If
a config surface must live outside the repo, it should hold a POINTER, never
logic.

## 3. A test can be vacuous because of a CONCURRENT edit

`tests/test_import_graph_gate_fails_closed.py` shipped from an agent looking
correct and passing 6/6. Mutation proved it guarded nothing: deleting the
fail-closed pre-check it exists to protect left it 6/6 GREEN.

Cause: the fixture copied the live `tools/dependency_graph.py`, which a different
agent had concurrently made raise. That tool's traceback happens to contain the
word "unparseable", the file count, the filename, AND a non-zero exit -- which
satisfied every assertion. The test was measuring a crash in another tool.

Measured discriminator, both cells, same fixture:

    reverted gate -> "Traceback" present, "denominator:" absent
    fixed gate    -> "Traceback" absent,  "denominator:" present

**A refusal is a designed exit; a traceback is the absence of one.** When a test
asserts "exit != 0 and the output mentions X", ask what ELSE could produce that
pair. Assert on the component's OWN exception class or OWN message, not on words
that any failure in the neighbourhood would also emit.

## 4. Mutation is the only evidence that a gate works

Across five cuts this session, four verified sound and one was vacuous -- and the
vacuous one passed its own suite 6/6. "The tests pass" distinguishes those cases
not at all.

The check that works, every time: reintroduce the original defect, confirm the
test FAILS, restore, confirm it passes, and verify the restore by sha256. If the
test still passes with the fix removed, the test is the defect.

Corollary observed twice this session: a mutation that produces a SyntaxError is
a FALSE KILL. The suite fails, but for the wrong reason, and proves nothing. Run
`bash -n` / `ast.parse` on the mutated file before believing a kill.

## 5. Documents are read as authority, and they go stale silently

CLAUDE.md section 2 said `.venv/bin/python`. The cloud environment builds `venv`.
`.venv` did not exist, so the command exited 127, the fallback selected bare
`python3` -- which is 3.11 WITHOUT the project dependencies -- and a test band was
measured on the wrong interpreter. Seven "failures" were reported that did not
exist; on `venv/bin/python` (3.12) the same band was 547 passed, 0 failed.

`CODEX_HANDOFF.md` says the box uses `.venv`, and that is TRUE -- of a different
machine (the WSL Codex box). Two correct documents describing two machines, read
as one contract.

**Decision recorded: `venv` is canonical in this repo.** No `.venv`, and no
symlink -- a symlink converts a visible exit 127 into a silent wrong answer.

## 6. `pip check` cannot see an uninstalled requirement

The provisioning report said `runtime deps OK` while `beautifulsoup4` and
`pytest-xdist` were both absent from the venv. `pip check` validates the
dependency tree of what IS installed; its denominator structurally excludes a
requirement that was never installed at all.

To answer "are the requirements satisfied", parse `requirements.txt` and resolve
each name via `importlib.metadata`. Anything else is asking a different question.

## 7. A fallback that always succeeds is not a fallback

Written this session, caught by its own test: the first draft of
`cloud-bootstrap.sh` listed `/home/user/BD` among its repo probes. That made the
"no checkout" branch UNREACHABLE on the machine it was meant to protect -- the
failure-path test matched the real repo, `exec`'d the real provisioner, and hung
for two minutes installing packages instead of failing.

Absolute paths to a live checkout belong in operator config (`BD_REPO`), never in
the script. A probe list should be `$HOME`-relative so a test can control it.

## 8. Two shell traps that cost real debugging time

**`find(1)` ranked by path depth prefers test fixtures.** `find_repo` collected
candidates and took the shallowest. On any host that has run the suite, /tmp
fills with two- and three-file pytest fixtures containing
`bulk_downloader/__init__.py`, and those are SHALLOWER than the real checkout.
Measured 2026-07-27: 70 candidates, winner `/tmp/coretest_ek6dnxfj`, a THREE-FILE
fixture directory. Named probes only; a miss must fail loud.

**Assignments inside `$( )` do not escape.** `find_repo` set
`BD_REPO_CANDIDATES` and was invoked as `REPO="$(find_repo)"`. The assignment
died with the subshell, the guard that read it always saw its `:-1` default, and
the "multiple checkouts found" WARN could never fire. The script LOOKED like it
handled ambiguity. It could not.

## 9. A predicate can exclude its own subject (the BOM probe)

`tools/diag_csrf_bootstrap.py` -- written specifically to test a BOM hypothesis
stated in its own docstring -- compared against `b'\xef\xbb\xbf'` written with
DOUBLED backslashes: twelve ASCII characters, not the three-byte UTF-8 BOM.

Measured before changing it:

    real UTF-8 BOM             -> "no"    (the case it exists to catch)
    literal backslash-xef text -> "YES"   (a case nobody cares about)

It had never been able to answer its own question, and it reported "no"
truthfully and uselessly for however long it had existed.

## 10. `ast.parse(feature_version=...)` does NOT restore older syntax rules

Measured under 3.12 against the file that fails on 3.11:

    None PARSED OK / (3,11) PARSED OK / (3,8) PARSED OK

`feature_version` gates a narrow set of constructs; it does not turn a 3.12
parser into a 3.11 parser. There is no in-process way to ask the older question
-- it costs a real subprocess against a real older interpreter, or it cannot be
answered. A test that believes otherwise reports OK while blind.

(Related: exactly 1 of 2033 tracked `.py` files failed to parse under 3.11, and
every AST tool swallowed it, so the import graph came out 1365 instead of 1366
while reporting success. Both AST gates now refuse instead.)

## 11. Test the cause, not the floor

A first draft asserted the tree parses under 3.11, because the sandbox's bare
`python3` is 3.11 and tools reached for it. That is testing a symptom. Once
`bd-cut`, `sast.sh` and `dast.sh` resolve the work-tree venv, nothing selects
3.11, and asserting a floor no interpreter in the pipeline runs is a denominator
drifting from its subject -- and it FAILED on any host without `python3.11`
installed, which is every stock Ubuntu 24.04 including the operator's box.

## 12. Workflow concurrency is bounded by cores

Agent fan-out is capped at `min(16, nproc - 2)`. On the 4-core cloud container
that is **2**. A ten-agent workflow is five sequential rounds, not one parallel
burst. Measured: 11 agents took ~48 minutes; 10 agents took ~42 minutes.

Fan-out on this host buys INDEPENDENCE (separate context, adversarial review),
not speed. Choose it for the former and budget for the latter.

## 13. Over-sensitive gates train you to ignore them

Two fired repeatedly on correct states this session: a verified-commit hook that
flags GitHub's own squash-merge commit (which cannot be amended without
rewriting merged history), and an uncommitted-changes hook that cannot tell
legitimate in-flight work from forgotten work.

CLAUDE.md section 0's inverse is not a footnote. A gate that cries wolf gets
switched off, and then it protects nothing. When adding a gate, check what it
does on a CORRECT tree before checking what it does on a broken one.
