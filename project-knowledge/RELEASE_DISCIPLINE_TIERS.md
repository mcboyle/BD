<!-- verified-against: v3.66.805 -->
# Release discipline — risk tiers + declared depth

**Status:** static (project-knowledge) doc. Set 2026-06-16; reframed the same day.

Two distinct failure modes prompted this — **not** "the rules are too strict":

1. **Over-ceremony on trivial work** — running the *full* heavy ritual (full
   re-derive + manual corpus walk + 15-file pack regen) on a single non-guard file
   when the change didn't warrant it. The fix is matching ceremony to risk (the tiers).
2. **Unannounced depth** — going bootstrap → multi-hour deep verify without ever
   saying "this is a long, deliberate pass, here's why." The operator couldn't tell
   necessary rigor apart from wandering. The fix is *declaring the depth up front*
   and letting the operator choose (below).

**This doc is NOT a license to do less rigor.** Deep, in-depth sessions (recognizer
work, a safety gate, a guard change) are legitimate and wanted. Where rigor is the
point, apply it **fully** — the change here is that you *announce* it and the
operator *opts in*, so a long careful session reads as intended, not as drift. The
floor below never relaxes in any tier.

**Read alongside** `CLAUDE.md` sections 2-4 (release checklist) and
`AUTOMATION_POLICY.md`. This doc tiers that checklist; it does not replace the floor.

---

## Declare the depth up front (session intent)

Before a session goes deep — **any Tier-A cut, or any deliberately long/in-depth
pass regardless of tier** — state, in one or two sentences, *before* diving in:

- **Scope** — what's being changed/investigated.
- **Rough effort** — e.g. "quick (<½ session)", "one focused cut (~1 session)",
  "deep multi-hour pass".
- **Goal + gate** — what "done" looks like and how it's verified.

…then get a **go** (or "timebox it" / "quick pass only" / "not now"). Depth becomes
a thing the operator opted into, not something I wandered into. This costs one line
and is the whole point of the reframe: **visibility, not avoidance.** A terse "go"
is sufficient authorization; the announcement is so the operator isn't surprised by
a 2-hour session they didn't know was a 2-hour session.

If mid-session the work turns out deeper than announced, say so and re-confirm
rather than silently continuing.

---

## The non-negotiable floor (all tiers, always)

These never relax, regardless of tier:

1. **7 release-guards stay byte-identical** to baseline (`extraction_core.py`,
   `session_capture.py`, `tools/capture_session.py`, `dom_capture.py`,
   `dom_recorder.py`, `capture_bodies.py`, `tools/build_release.py`). Any change is
   sha-declared (before/after) — never silent. Re-derive from the **extracted zip**.
2. **F2** — real `.wacz` captures are local-only; report kinds/counts, never values.
3. **Child-safety** and the rest of the constitution.
4. **No deploy from the sandbox.** Matt deploys (overlay + pycache clear + restart).
5. **RED-first for any logic change** — a new-behavior test must fail on the
   pristine baseline before the fix, so the test is proven to test the fix.
6. **`verify_release.py --zip` must print `RESULT: PASS`** (gate on true `$?`, via
   redirect not pipe) and the **band runs from the extracted zip**, never the work
   tree alone.

If you cannot satisfy the floor, you are not in a lighter tier — you stop.

---

## Tier A — heavy (full ritual; announce depth first)

**Trigger:** a guard edit, a route change, an `app.py`/`runner.py` edit, a SPA
*write* tranche, or anything that moves `legacy_parity`. (Migration tranches like
**T11/T12**, `app.py` decomposition F5.1, any new `/api/` route.) **A safety gate
(e.g. T11) is Tier A and gets full rigor — announced, then done thoroughly, never
rushed.**

**Do the whole §4 checklist:** RED-first → full in-sync regen as applicable
(`build_function_index`, `build_endpoint_catalog`, `dependency_graph`,
`gui_parity_inventory`, `check_route_counts` G12) → guard-SHA confirmation →
`build_release` → extract → band → `verify_release --zip` → **MAX adversarial
audit** (re-derive everything from the extracted zip assuming the build report is
wrong) → **full session-close pack** (regenerate STATE + KB_HANDOFF + CONTINUATION
+ Backlog + Roadmap + refcards 1/5 + kickoff + execution-order, and update the 3
living files). SPA wiring uses **full `/api/…` literals**, not a concatenated base.

## Tier B — light (non-guard single-file logic, no route)

**Trigger:** a logic change confined to a **non-guard** file with **no route /
app / runner / frontend** change. (All of 259/260/261 were Tier B — recognizer and
`capture_scrub.py` polish.) *Tier B is about not over-ceremony-ing low-risk work —
the rigor that applies (RED-first, guards byte-identical, verify_release) is still
done in full; only the ceremony that doesn't fit the risk is dropped.*

**Do:**
- Bootstrap (`bd-boot` → `bd-prestage` loop → `bd-install` → `bd-venv` →
  `bd-preflight` → `bd-state` → `bd-status`) and confirm the guard SHAs against
  STATE. (`setup.sh` FAILS BY DESIGN — do not run it; corrected @805.)
- **RED-first** test for the change.
- A **targeted regression band** of the suites the change can touch — *not* the
  whole `tests/` dir (it hangs), and *not* a manual corpus walk.
- **Confirm in-sync gates are still in sync, but regenerate nothing** unless one
  actually drifted (a non-guard module adding only module-level code that uses
  already-imported deps moves no graph edge; `FUNCTION_INDEX` tracks only
  `app.py`/`runner.py`).
- Version bump (3-part) + CHANGELOG + the version pin.
- `build_release` → extract → band → **`verify_release --zip` PASS**.
- Re-confirm guards **7/7 byte-identical** from the extracted zip.
- **Minimal pack delta** at session-close: regenerate **STATE.json + KB_HANDOFF +
  CONTINUATION** only; **copy the rest of the pack forward unchanged**; append a
  living-file stamp **only if** there is a durable learning. *Skip the MAX
  re-derive and the full 15-file regen.*

**Robustness for Tier B comes from a committed test, not a per-session manual
check.** When a Tier-B change fixes a real mis-call, *pin it* (e.g. a scorecard
entry) so a regression is a red test next session, not a corpus walk. (261 did
this: `tests/test_recognizer_scorecard.py::test_no_tell_markup_skin_scored_native_custom`.)

> A **deliberately deep Tier-B pass** (e.g. a thorough recognizer investigation) is
> fine and wanted — it just gets the depth announcement above, so the length is
> chosen, not a surprise.

## Tier C — trivial (docs / living files)

**Trigger:** a pure documentation or living-file edit, no code, no build.

**Do:** make the edit; bump STATE's pointer if a shipped doc changed; no build, no
band, no pack regen beyond the touched file. (Example: `LEGACY_MIGRATION_PLAN.md`
errata, this doc.)

---

## Picking the tier (decision order)

1. Touches a **guard**, a **route**, `app.py`/`runner.py`, a **SPA write**, or
   `legacy_parity`? → **Tier A.**
2. Else, is it a **code/logic** change in a non-guard file? → **Tier B.**
3. Else (docs/living files only)? → **Tier C.**

When unsure between A and B, choose **A**. The floor (guards, F2, RED-first,
verify_release) applies in every tier, so a B-tier mistake cannot ship a guard
change or an unverified banner — the cost of guessing B is only that you did less
*ceremony*, never less *safety*.

## One more standing rule (learned 261)

**One instance per work tree.** Two Claude instances on the same `/home/claude/work`
will clobber each other (261 saw `player_recognition.py` churn sha three times
between read-only views, and release zips appear unbuilt-by-you). If two are
unavoidable, reconcile explicitly before either touches shared files, and treat any
unexplained sha change as untrusted — re-derive from the guard-verified extracted
zip.
