<!-- verified-against: v3.66.161 -->
# extraction_core — `decision_confidence` relocation: DECISION

**Decision:** **(b) Leave `decision_confidence` in `capture_workbench` by design.**
**Status:** Step 5 part 2 **closed-by-decision** (not deferred, not open).
**Version:** `3.66.161` (no bump) · **Date:** 2026-06-06 · **Scope:** documentation only — **zero code change**.

This note supersedes the "Stopped / needs an explicit decision" framing of Step 5 part 2 in
`EXTRACTION_CORE_STEPS_1_5_REPORT.md §9`. The decision is now made and recorded here.

---

## 1. The decision

`decision_confidence` (and its subtree) **stays in `capture_workbench`**. Explicitly:

- **Do not** relocate `decision_confidence` into `extraction_core`.
- **Do not** move the `CP_*` change-plan taxonomy into `extraction_core`.
- **Do not** move any change-plan / recommendation semantics into the derivation core.
- **Do not** start confidence-model unification under this scope.
- **Do not** change runtime code as part of this closure.

## 2. Rationale

- `extraction_core`'s charter is **pure derivation primitives** (stdlib-only, side-effect-free,
  characterizable against a frozen golden). That is what makes the routing safe and provable.
- `decision_confidence` is **not** a derivation primitive: it post-processes an assembled
  `DetectorDraft` together with its computed flow / stability / recommendation context.
- It depends on the `CP_*` change-plan taxonomy (6 constants, 32 use-sites in workbench).
  Relocating it faithfully would require dragging that taxonomy into the derivation core.
- Doing so would **pollute the derivation core with workbench semantics** — the primary
  scope-creep risk flagged in `CAPTURE_REFACTOR_STRATEGY.md`.
- There is **no frozen characterization golden** for `decision_confidence`, so the
  faithful-copy / byte-stability proof that gated Steps 3–5p1 cannot be constructed the
  same way. Relocating now would forfeit the very safety mechanism this refactor relies on.
- The **useful, safe relocation is already complete**: the three producers are routed onto
  `extraction_core` and green. The remaining "confidence" item is a model-unification
  question, not a relocation — and that is where numbers can actually move.
- Any future confidence-model unification must be **separately scoped, characterized, and
  gated** — it is not part of Phase 1 and is not blocked by this closure.

## 3. Verified current tree state (3.66.161)

Confirmed by direct inspection of the loaded tree on 2026-06-06; **no edits made**:

| Check | Expected | Verified |
|---|---|---|
| `extraction_core` importers | exactly 3 | ✅ `tools/build_template_from_wacz.py`, `bulk_downloader/capture_template.py`, `bulk_downloader/capture_workbench.py` |
| `DraftPattern` class definition | only in `extraction_core` | ✅ `bulk_downloader/extraction_core.py:112` (workbench only *imports* it) |
| `decision_confidence` definition | workbench implementation | `bulk_downloader/capture_workbench_impl/analysis.py:951` (`_decision_confidence`); 0 in core |
| `CP_*` constant definitions | workbench implementation | `bulk_downloader/capture_workbench_impl/_common.py:226` (6 constants); 0 in core |
| confidence-model unification | not started | ✅ none in `extraction_core` |
| `extraction_core` test suites | green | ✅ `test_extraction_core` 21/21 + `test_extraction_core_characterization` 9/9 = **30/30** |

## 4. Step status after this decision

- **Steps 1–4, Step 5 part 1:** complete, routed, proven byte-identical, green. (Unchanged.)
- **Step 5 part 2 (`decision_confidence` relocation):** **CLOSED — leave-in-workbench by design.**
  No further relocation work is open or pending.
- **Step 6 (promote byte-stability on a real capture corpus):** **future, corpus-gated /
  on-real-corpus validation only — NOT a blocker.** It runs on-stash against a real promote-able
  capture corpus when one is available; it is not runnable in-sandbox and does not gate Phase 1
  closure. (The on-stash `bd_capture` log bundle is deployment-verification evidence, not a
  template corpus, and does not satisfy Step 6.)

## 5. Optional, non-behavioral cleanups (NOT required by this decision)

Carried from the report as nice-to-haves; deliberately **not** done here (would be code edits):

- `_slug` is now unused in `capture_workbench` (kept earlier for a minimal diff) — removable later.
- `DraftPattern`'s permanent home is now `extraction_core`; the historical workbench note is moot.

These are tracked only; pick them up opportunistically in a future touch of those files.

---

## Verdict

- **`extraction_core` Phase 1: CLOSED.** The three producers are consolidated onto the pure
  derivation core, proven byte-stable, and green.
- **Confidence work: deferred as a separate, future design effort** — independently scoped,
  characterized, and gated. Not part of Phase 1; not a blocker.
