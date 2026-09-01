# Register-vs-tree ledger, 2026-09-01

Produced by the `reconcile-register-with-tree` workflow at origin/main 5291de20
(v3.66.1388). It examined 475 rows -- the whole register -- and found
8 disagreements, then re-verified every one by hand at that HEAD.

This is the measurement behind row 473, which says the register and the tree
disagree IN BOTH DIRECTIONS. Read the confidence section before acting: two
findings need stronger tests and one is called a false positive by the ledger
itself.

NOTHING HERE HAS BEEN ACTED ON. Operator ruling 44 says do not start backlog
items; correcting these rows is an operator decision.

---

Verified every finding against HEAD `5291de20` before writing. All eight check out textually; two need stronger tests before action, and the set has a coverage gap.

---

# Register/tree disagreement ledger — row 473 measurement

Base: `/home/mboyle/BulkDownloader`, HEAD `5291de20`, `project-knowledge/IMPROVEMENT_BACKLOG.md` (559 lines), tracked state clean. All eight findings re-verified by hand at this HEAD; results below.

## Kind A — wrongly CLOSED, deliverable not in the tree (rows 316, 26, 27)

**Row 316 (CLOSED @1298).** A future session would believe row 260's misattribution was corrected, and that the register audits its own accuracy successfully. Row 316's entire deliverable was one register edit; line 346 still reads `| 260 |  CLOSED @1277 |`, and `grep -c '@1270+@1277'` returns 1 — that single occurrence is row 316's own prose claiming the edit. The row certifies a fix to itself that was never applied.

**Rows 26, 27 (both CLOSED @1248).** A future session would believe BD has a general vacuous-test detector and mechanized cross-test over-sensitivity controls. It has neither — only narrow decidable slices. Both status tokens were flipped by commit `977aabb4c`, which in the same commit appended prose reading "THE ROW REMAINS OPEN" (26) and "THIS ROW STAYS OPEN" (27). The v3.66.1248 changelog says "each gain a decidable slice." The register's own partial-closure idiom (`CLOSED @<n> -- PARTIAL`, used by rows 3 and 35) is absent from both.

## Kind B — wrongly OPEN, work shipped under a successor row (426, 427)

A future session would believe filename-equality still marks downloads done, and that colliding downloads still interleave one shared `.part`. Both guards are in the tree: `runner_transport.py:1211` calls `db_skip_identity` behind the "EXISTENCE IS NOT IDENTITY" comment at 1196; `staging_claim.py:295` raises `StagingClaimedByAnotherJob`, called from `runner_transport.py:1751`. Shipped under successor rows 479 and 481 (both CLOSED @1388). The compounding harm: the genuine residuals now live in rows 483, 485, 498, 501, 503, 506, so a session working from 426/427's text hunts the wrong remainder.

## Kind C — OPEN, but the row's own anchors no longer exist (518)

A future session following this row literally would re-add `_EXPECTED_DECLARED_GATE_COUNT = 235` — the hand-bumped constant that operator ruling 41 deliberately retired at v3.66.1381 under row 531. I confirmed the literal is gone from `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`; it survives only in three other tests as an explicitly *retired* constant. The row's RED denominator ("exactly 8 failing claims") and GREEN condition ("still 235") are both unsatisfiable here. The underlying defect is *not* fully resolved — three stale shard-name prose claims persist at shifted lines — so this needs re-derivation, not closure.

## Kind D — correctly CLOSED, evidence pointer dangling (108)

A future session would go read `test_register_promises_resolve.py` for the both-directions proof and find nothing (`git ls-files | grep register_promises` → 0). The file was deleted at `856c2919` as part of row 107's deliberate retirement of the ledger mechanism. The closed work is genuinely done; only the citation is stale. Mild secondary hazard: a session that reads the dangling pointer as a fake closure may rebuild a retired second task authority, which CLAUDE.md A1 forbids.

## Damage ranking

1. **316** — a false closure inside the register's own self-audit. Worst: it makes the instrument attest to its own correctness while wrong.
2. **26, 27** — real test-integrity work believed shipped. Other cuts lean on these as existing safeguards, so the wrong belief propagates into future verification decisions rather than stopping at one row.
3. **518** — nominally the cheaper direction, but literal compliance *reverses a shipped operator ruling*. This outranks the other OPEN rows because acting on it does damage, not just waste.
4. **426, 427** — duplicated work plus a misaimed remainder.
5. **108** — one wasted lookup, small revival risk.

Kind A over Kind B holds as the task frames it, with 518 as the documented exception: an OPEN row whose instructions are actively wrong costs more than an OPEN row that is merely obsolete.

## Confidence, and what I would re-check by hand

**Row 285 — I believe this is a FALSE POSITIVE; do not act on it.** The finding's own caveat is correct and I would promote it to a verdict. The row's acceptance is explicitly marked unwaivable and demands a real deploy on test4 (10.0.70.85) recording hostname, commit, tree SHA, exact command and `/api/health`, plus a proven induced-failure path. `sed -n '2125,2150p' CHANGELOG.md | grep -c "test4\|10.0.70"` returns **0**. The code and gate shipped at v3.66.1284; the acceptance did not. Under A2 that is UNKNOWN, and UNKNOWN is a failing state — the row is correctly OPEN pending operator evidence. Re-check against the evidence store, not the changelog.

**Rows 426, 427 — re-check by blob equality before closing.** I confirmed symbol presence, which is the weak containment test A7 warns about; only per-file blob comparison for every file the candidate touched survives a rebase. Specifically: `runner_transport.py` has **two** `staging_claim.claim` call sites (1751 and 2244) and the finding measured only 1751, so the second download path is unmeasured.

**Rows 26, 27 — ask before reopening.** The textual contradiction is strong but cannot separate "closed by mistake" from "closed deliberately as PARTIAL with the idiom omitted." The remedies differ (reopen vs. append `-- PARTIAL, remainder named`), so this is an operator question.

**Row 518's surviving residue** — the three shifted lines (471, 536, 547) are this pass's own re-measurement of a file that has already moved once; re-derive at current HEAD before writing any RED.

**High confidence, safe to act on:** 316 (pure string check, re-verified myself), 108 (file absence plus a named deleting commit).

## Coverage gap in this measurement

Row 473 names **four** OPEN-but-shipped rows: 426, 427, 432, 447. This pass covers two. At HEAD: **432 is now CLOSED @1384** (self-resolved since 473 was written), but **447 (`SAFE-DEST-IS-AN-EXISTS-PROBE-NOT-A-RESERVATION`) is still OPEN and was not measured** — it belongs in Kind B pending a check.

Separately, 473's second direction — shipped work carrying *no register row*, its example being the extension-vault work at v3.66.1373 — produced zero findings, and structurally cannot produce any: these are per-row checks, and there is no row to check. `grep -n "1373"` on the register returns only row 473's own prose. That class needs a tree-side denominator (releases → rows), not a row-side sweep, or 473's acceptance stays half-measured.
