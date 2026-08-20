# Cut tiering — robustness matched to the failure mode

This is standing authority under CLAUDE.md A3 (change lifecycle) and A5
(verification). It refines, and does not replace, either. Where a tier's gate
floor and a CLAUDE.md A-section disagree, the stricter applies.

## Why this exists

v3.66.1195 was a docs/register/version cut with no runtime code. It took four
freezes, four independent review rounds, seven fleet full-suite runs and seven
floor reruns, and still carried six wrong numbers -- row 120's 17, its
06-12 span end, its 29 download-flow fetches and its 5 post-06-12 sessions, plus
row 128's 42 and a 325-release miscount -- across three of the four freezes.
The ceremony was heavy but aimed wrong: a fleet full suite runs the whole test
population and is blind to a wrong count in a backlog row, while the cheap check
that catches it — re-derive every number from source, once — was never run up
front. Robustness must match the failure mode, not the ceremony level.

## The tier axes

A cut's tier is the maximum of two axes:

- BLAST RADIUS — what a mistake in this cut could break (one file, one
  subsystem, a generated artifact, a release guard, the running service).
- EXPOSURE — reversibility and who sees it (local test vs a deployed runtime or
  security boundary).

Assign the tier when the cut is opened. Run that tier's gate floor. An agent may
always ESCALATE a tier when unsure; it may never drop below a floor.

## The tiers and their gate floors

### T0 — trivial
A comment, a typo, a version-string-only edit. No claim, no logic change.
Floor: the single guard that covers the change, plus `bd-freshcheck --repo-only`.
One self-check. Merge on green. No review round, no fleet, no full suite.

### T1 — localized, or a pure docs/register cut
One module, or one test, or a docs/register/backlog cut with no cross-subsystem
readers and no runtime behavior change.
Floor:
- RED-first only if the cut changes behavior.
- Affected floor via `bd-band-derive` (a floor, never a ceiling).
- `bd-regen-order` + `bd-freshcheck --repo-only`.
- ONE independent review, scoped to the FAILURE MODE.
- NUMBER-DISCIPLINE (mandatory for any cut that asserts counts, dates, or sizes):
  re-derive EVERY number in the diff mechanically from source, in one pass,
  BEFORE the first freeze; attach the derivation and its output as evidence;
  never copy a number from prose. A glob is a denominator choice — state the
  denominator and prove it is the right one before measuring.
- NO fleet lanes and NO full suite unless a generated artifact or CI shard is
  touched.

### T2 — standard
Cross-subsystem change, a new gate, a generated-artifact change, a fixture or
corpus change, or anything touching a secret-scanning boundary.
Floor: everything in T1, plus —
- RED-first plus a mutation battery on the new logic (`bd-mutate`).
- Affected floor plus tree-wide denominators and a deleted-consumer sweep.
- Read every regenerated diff and explain it.
- One independent review PLUS one adversarial verify.
- Exact-head GitHub CI.
- The full local suite, OR one fleet lane — not the whole fleet.

### T3 — major / release-guard
A Tier-A guarded file, the deploy path, a runtime or frontend change that ships,
or a safety boundary.
Floor: the full CLAUDE.md A3 lifecycle — RED-first, mutation, adversarial
multi-lens verify, fleet full-suite lanes across matched hosts, independent
review, exact-head CI, deploy, and health/version verification.

## The rule of proportion

Run the gate that catches THIS tier's failure mode. Never run a higher tier's
ceremony to feel safe: it wastes lanes and, worse, its noise hides the real
defect — v3.66.1195's fleet lanes exercised seventeen thousand runtime tests
each and could not see any of its six wrong numbers, because those numbers live
in backlog prose that no test reads. Pass or fail, the lane was blind to the
actual failure mode.

## Schedule-sensitive shared state (T2+)

A test that inspects a real shared directory a concurrent xdist worker or the
operator can write -- `/tmp/bd-jobs`, `/tmp/bd-runctx`, any absolute `/tmp`
registry, HOME-anchored real state, or a module-level constant naming a real
non-`tmp_path` directory -- must NOT assert on a bare before/after snapshot of
that directory. Under `-n 24 --dist loadfile` the scheduler decides whether an
inspector and a writer overlap, so such a test fails once and passes on re-run:
it measures the suite, not its subject. Do exactly one of:

- **ISOLATE** -- monkeypatch the tool's directory constant to `tmp_path` when the
  test controls what writes the directory.
- **ATTRIBUTE** -- when the test is a leak detector that must read the real
  directory, assert only over entries THIS run created, identified by pid lineage
  or a stamped marker.
- **GROUP** -- only if neither is sound, pin the inspector and every writer of
  that directory to one worker via `@pytest.mark.xdist_group` (a pattern unused
  in the tree today, so justify each use).

A floor failure that does not reproduce is schedule-sensitive: establish its
causation and isolate the test. Re-running to green launders a real flake --
row 179 was found only because its instrument was traced, not retried.

## Deciding a tier — worked examples

- A backlog adjudication that only edits register text and the version trio: T1.
  Its mandatory gate is number-re-derivation, not fleet lanes.
- Renaming a mislabeled test fixture and adding a name-vs-content gate: T2
  (fixture + new gate + secret-scan region).
- Wiring a resume decision into `tools/capture_session.py` with a new cockpit
  route and an SPA build: T3 (Tier-A guarded file + deploy + frontend).
- Fixing a typo in a docstring: T0.

When two readings of a cut give different tiers, take the higher one.
