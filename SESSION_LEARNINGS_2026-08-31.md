# What this session learned -- 2026-08-31

Durable reasoning that is not recoverable from the code, the register, or git.
Companion to HANDOFF_2026-08-31_SESSION_END.md (state) and
OPERATOR_DECISIONS.md (rulings 1-36).

## The session's largest single finding

THE v3.66.1360 HOLD WAS WRONG, AND HOLDING IT COST A DAY. A prior session
refused to merge PR 657 because an extracted-release-zip suite reported 1,498
failures. Two facts settled it in twenty minutes: the zip flow is RETIRED in
this repository (toolchain/bin/bd-precut says so in its own refusal text), and
the same suite fails IDENTICALLY on the already-shipped v3.66.1358 baseline.
The failures were environmental -- 142 of the first 180 were one process-global
sites_config conflict from running all 1,495 test files in a single process.

The lesson generalises: A GATE WITH NO GREEN PROVENANCE ANYWHERE IS NOT
EVIDENCE. Before treating a red gate as a defect, find a run where it was
green. If none exists on any host for any version, the gate is the suspect.

## The defect class that dominated everything

Nearly every real defect found tonight was ONE SHAPE: a surface reporting OK,
or a specific wrong answer, over a measurement it never took.

  skip_if_exists   filename equality read as content identity
  secrets_store    an unreadable vault read as UNINITIALISED, then RENAMED
                   AWAY, then any password created a new one over it
  extension_vault  the same, and a MERE READ destroyed the store
  policy_gates     a failed DB read read as zero usage, reported ok at full
                   throttle -- unthrottled downloading exactly when usage
                   could not be seen
  backup_verify    an empty archive read as a good backup
  alerts_engine    a broken event table read as "no alert fired"
  download_hold    attempts counted and reported as effects
  bdctl --json     any HTTP error read as nothing to print

CLAUDE.md A7 names this, and the value of the rule is that it PREDICTS where to
look. The five-lens hunt that found 31 defects was built directly on it.

## v3.66.1359's blast radius: correct change, four broken readers

Making an uninitialised vault report ok=False was RIGHT. But four separate
consumers read the new 503 as something else -- a transport failure
(urlopen raises HTTPError like URLError), a DB verdict, a ratchet breach, a
missing password. NONE was caught by the cut that made the change, because
each lived in a different suite. WHEN A SHARED SURFACE CHANGES ITS VERDICT,
ENUMERATE ITS READERS; the band will not do it for you.

## Instruments that lied, and how they were caught

- A probe read /tmp/h.json that curl had FAILED to write, so it printed a
  healthy verdict for a host that was not answering. A probe that reads a file
  it did not just write is not a measurement.
- `pkill -f "bd-band"` killed its own ssh shell. `pgrep -cf claude` returned a
  uniform 2 on every host -- the tell that a probe is counting itself.
- `chmod a-w` on a DIRECTORY did not stop an EXISTING file being rewritten, so
  an induced-failure test induced nothing and passed. An induction that does
  not induce manufactures the evidence it was meant to test.
- `git clone --bare` from a bundle reported 187 of 695 heads, because the
  default refspec ignores refs/archive/*. The bundle was complete; the
  verifier was wrong.
- Three agents reported their own anchorcheck PASS covered NONE of their
  changed files. That honesty is the standard; the tool's green means nothing
  without its denominator.

## Process failures worth not repeating

1. I REPORTED FOUR FIXES AS MERGED WHEN THEY WERE ONLY TAGGED. The band kept
   showing their failures and I kept dismissing them as "known, already
   fixed" -- true of the fix existing, false about it being on main. A band
   forced the check. Verify by asking git what is on main, not by memory.
2. A DETACHED HEAD AFTER A REBASE gives a PR with ZERO CI CHECKS, and zero
   reads as pending rather than broken. It waits forever. Now refused by
   bd-rebase-cut.py.
3. I WROTE A TOOL THAT ALREADY EXISTED. bd-rebase-cut.py was written
   2026-08-25 for the exact collision I had been resolving by hand all
   session. Read the inventory first; extend the existing authority.
4. A cherry-pick carried the PREVIOUS cut's release trio and register file
   from a worker's older base, which would have silently reverted four
   unparks. Diff every cut against main before committing; a clean-rebase
   message is not that check.

## Two open judgement calls, deliberately not decided

- ROW 889 vs THE FROZEN SET. Narrowing the import graph to product->product
  works and removes 59% of its churn, but reverses a documented decision that
  widened it because the gate was blind to 57% of the import surface. Same
  number, two readings. Abandoned on the operator's ruling; branch preserved.
- ROW 458, THE MUTATION REGISTRY. Most fixes ship with no mutation coverage
  because row 357 refuses any new anchor carrying a value-bearing token, and
  its exception registry is empty and unproven. Extending it means editing the
  anchor-integrity gate's own test. Correct, and correctly deferred to a
  reviewed cut.

## The correction that closes the session's own defect class

I reported 17 cuts merged, v3.66.1360 through v3.66.1377. MEASURED on
origin/main b2619507: three version numbers in that range are absent from
main -- 1365, 1372 and 1374 -- and 1374 is not a renumbering. It was the
four-fixture cut for rows 413, 414, 417 and 418. None of its four test files
exists under tests/ on main, no commit on main names the version, the
CHANGELOG has no entry for it, and all four rows are still OPEN in the
register. The work is real, reviewed and tagged at recover/row41{3,4,7,8}-fix.
It never landed.

This is the third time in one session that I reported merged when I had only
tagged, and the second time after writing down the lesson. What finally caught
it was not memory or care. It was widening a measurement: bd-checkpoint-write
counted the queue by SHA containment, which under-reports a rebase, then by
patch-id, which under-reports a conflict resolution, and only the third
denominator -- does main hold THIS blob for every file the candidate touched --
answered the question that matters. The first two denominators were not wrong
about what they measured. They were wrong about what I asked them.

The same widening caught two more the same hour: verify.sh reported
"nothing missing" while comparing only counts (so it could not see that the
live bd-qa-row.sh was two days behind the archived one), and its file glob
was *.sh plus *.py, which silently excluded six extensionless executables --
including bd-checkpoint-write, the tool that writes the continuity state the
archive exists to preserve.

A GLOB IS A DENOMINATOR CHOICE, AND SO IS A CONTAINMENT TEST. Both of mine
were narrower than the claim I put on top of them.
