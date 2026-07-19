<!-- verified-against: v3.66.593 -->
# PHASE C — F2 / REDACTION / POSTURE HARDENING PLAN

<!-- authored against live baseline v3.66.593. CONSOLIDATES + REFRESHES the Phase-C material that -->
<!-- lived inside FORWARD_PLAN_post_172.md (a 172-era doc "SUPERSEDED IN PART"). The methodology + the -->
<!-- COMPLETE F2 list + the C1..C6 ordering are lifted from there and re-anchored to @593 state. -->
<!-- No cut/build authorized by this doc; every increment is its own gated wave (MODE: MAX AUDIT). -->

Pairs with: `9_SETTINGS_CENTER_SAFETY_SPEC.md`, `RELEASE_DISCIPLINE_TIERS.md`, `GLOSSARY.md`
(F2/redaction terms), the `TASK_TRACKER` (rows `C1`..`C6`, `BP-VH*`, `P3-T12-CALLSITE`), and
`STATE.guards` (the 7 guard baselines).

---

## 0. Preconditions (confirm before starting)

Phase C is **LAST by operator decision**: harden F2/redaction only after the A+B *functional*
baseline is locked. Rationale (unchanged): redaction increments (path-signing masks, structure-mode
URI masking, depth-2 decode) can **over-redact and mangle functional URLs**, silently breaking
capture/template/replay. While functionality still changes, a broken capture is ambiguous (feature
bug vs over-redaction). So: lock a known-good functional baseline, then phase each increment one dial
at a time against it — every regression attributable.

- **Phase A (functionality completion)** and **Phase B (convergence / unified data model)** are the
  precondition. As of @593 the app is mature (decomposition complete @446, GCW/parity complete @276,
  security-audit ledger closed). **Confirm A+B are locked** before Wave C1.
- Phase C is also the tail of the standing gated chain **BP-VH1/2/3 → P3-T12-CALLSITE → Phase C**.
  Confirm the BP-VH / P3-T12-CALLSITE precursors are complete (or explicitly sequence them ahead).

**The floor is separate and stays ON throughout.** The unconditional credential floor (fail-loud
`scan_floor_secrets`; cookies/Authorization/JWT/userinfo/opaque/email) is NOT what Phase C changes —
Phase C hardens the *capture-time* redaction *around* that floor. AST-verify the floor stays
unconditional every Phase-C release.

---

## 1. Phasing methodology — "verify what breaks a website"

This is the discipline every increment follows:

1. **Lock the functional baseline corpus (the oracle).** A set of real captures per site family that
   demonstrably succeed end-to-end (capture → build template → replay/download works). Record each
   functional outcome (template builds? media URL resolves? replay plays?). *Synthetic-only for
   circulation; a real `.wacz` with retained manifest bodies is LOCAL-ONLY — never circulate/commit.*
2. **Each redaction increment ships behind its own dial, default OFF**, read live through the 171
   `redaction_profile` machinery (fail-safe parse: unknown/empty → safe). Default-OFF must be
   **byte-identical** to prior behavior (AST-verify no unconditional path; the 171 floor gate stays
   unconditional and separate).
3. **Apply one increment → re-run the baseline corpus → diff the functional outcome:**
   - Breaks a site (template fails / URL mangled / replay dead) → the increment over-redacted a
     functional value → **narrow the rule** (anchor it, restrict to a path shape, exclude a host) and
     re-test.
   - Clean across the whole corpus → promote that dial to **default-ON**.
4. **Each increment is its own gated wave** (MODE: MAX AUDIT): re-derive from the artifact, negative
   controls (planted secrets still caught), idempotency / no-doubling, guard shas, determinism.
5. **Track per increment:** what it masks, what it could over-mask, the corpus-breakage result, the
   decision (default-on / narrow / drop).

---

## 2. The COMPLETE F2 / hardening list (all parked until Phase C)

| # | Increment | Touches | Over-redaction risk |
|---|---|---|---|
| 1 | **base64-aware path-signing detector** — decode candidate path segments, scan for signing keywords, mask the segment ONLY. OBSERVED on rep171 (a DASH `.mpd` path decoded to `dirmatch/expiretime/<sig>` and survived; `redact_query` is query-only). | **unguarded** redactor primitive | **HIGHEST** (can mangle media URLs) — the prime corpus-breakage test |
| 2 | **STRUCTURE-mode manifest URI masking** — wholesale-mask every manifest URI/segment/key line regardless of detector; keep only non-URL structure. Closes the path-signed gap by construction for retained manifests. | **`capture_bodies.py` (GUARD — declare)** | High (retained manifests) |
| 3 | **manifest-retain → `local_only`/`reduced_redaction` stamp** — pure-metadata flag so a manifest-retaining WACZ can't masquerade as floor-clean. | metadata only | Near-zero |
| 4 | **email-in-text-body capture-time scrub** — add email/userinfo detection to `_value_is_dangerous`/`_redact_text` (today they check signed-URL/JWT/long-token but NOT email). Export floor already backstops it (defense-in-depth + posture-doc consistency); then correct the C-T2 docstring's "never deferred to export" claim. | `_redact_text` / `_value_is_dangerous` | Low (masks emails, not URLs) |
| 5 | **text/plain-mislabeled-manifest path** — a manifest served as `text/plain` is retained via the detector-dependent path; airtighten so mislabeled manifests get the same treatment as correctly-typed ones. | manifest-type handling | Moderate |
| 6 | **`redact_value` primitive hardening** — (a) doubly-URL-encoded nested signing param (decode one level today → depth-2 decode); (b) secret in a dict KEY (the walk redacts values not keys → add key scrubbing). Zero/benign real footprint so far — act only if a capture exhibits it. | **unguarded** primitive | Moderate |
| 7 | **HLS segment / `#EXT-X-KEY` / `#EXT-X-MAP` URI handling under manifest retention** — segment URLs are wholesale-masked by design today; verify that holds once manifest bodies are retained; fold into STRUCTURE-mode (#2). | manifest/HLS handling (with #2) | High (with #2) |
| 8 | **`SENSITIVE_QS_KEY` `code`/`k` question — DECIDED: LEAVE.** Do NOT naive-append (over-redacts ~26/32 keys; `k` matches any key with a 'k'). Revisit ONLY on evidence, and then via an anchored/exact mechanism (`\bcode\b`/`\bk\b` or a separate exact-key set), never the unanchored `re.search` set. | — | (would be severe if naive-appended — that's why it's parked) |
| 9 | **Floor-invariance verification** — each Phase-C release re-confirms the credential floor stayed unconditional (AST + empirical: planted secrets still caught) and the seven guard shas (baselines in `STATE.guards`). The floor must NEVER regress while everything around it changes. | verification (every wave) | — |

---

## 3. Phase-C ordering (low-risk → high-risk)

```
C1. #3  local_only / reduced_redaction stamp        — pure metadata, near-zero breakage. START HERE.
C2. #4  email/userinfo capture-time scrub            — low breakage, high posture value.
C3. #6  redact_value depth-2 + dict-key             — moderate; validate against the corpus.
C4. #1  base64-aware path-signing detector           — HIGHEST breakage risk; the main corpus test;
                                                       narrow iteratively (anchor / path-shape / host-exclude).
C5. #2  STRUCTURE-mode manifest URI masking          — after C4's lessons; fold in #7 (HLS key/map).
    + #7 HLS #EXT-X-KEY / #EXT-X-MAP under retention
C6. #5  text/plain-mislabel airtightening
Throughout: #9 floor-invariance verification (every release).
Revisit #8 ONLY on evidence (anchored/exact mechanism, never the unanchored set).
```

Rationale: front-load the zero/low-risk, high-value increments (C1 metadata, C2 email) to bank
posture wins with no corpus risk; do the primitive hardening (C3) before the two high-risk URL-path
increments (C4 base64 detector, then C5 structure-mode) so the primitive is solid when the mangling-
prone dials land; C4 is *the* corpus-breakage test and must be narrowed iteratively; C5 builds on
C4's lessons and absorbs HLS handling (#7); C6 airtightens the mislabel edge last.

---

## 4. Guard + floor implications (per the discipline)

- **Guard-declare required for #2 (`capture_bodies.py`).** It is one of the 7 SHA-pinned guards —
  any edit needs an explicit operator SHA declaration in the SAME cut (`bd-guard-declare`), and the
  baseline updated in `STATE.guards`. #1 and #6 touch the **unguarded** redactor primitive (no
  declare, but full MAX-AUDIT still applies).
- **Default-OFF byte-identity.** Every increment's default-OFF path must be byte-identical to prior
  behavior — AST-verify no unconditional new redaction path; the 171 floor gate stays unconditional
  and separate.
- **Floor-invariance every release (#9).** AST + empirical (planted secrets still caught) + the 7
  guard shas byte-identical. The floor is the thing that must never regress.
- **Reporting:** redaction findings as **kinds/counts, never values**.

---

## 5. Standing constraints for EVERY Phase-C cut

- **RED-first per increment**: tests for what it masks + negative controls (planted secrets still
  caught) + idempotency/no-doubling + default-OFF byte-identity + the corpus-breakage diff.
- Band from the **extracted zip**; on-stash `capture.sh` GREEN before ledger flip (Matt runs it).
- **LOCAL-ONLY captures**; any `BD_CAPTURE_BODIES=1` capture over a streaming site retains path-
  signed URLs — never circulate/fixture/commit a real `.wacz`; synthetic fixtures only.
- Registry sites are adult cam sites — never mimic them in fixtures or test them live.
- Version bump = 3-part edit; changelog ASCII; import-graph baseline re-frozen for any new edge.

---

## 6. Definition of done (Phase C)
Each of C1–C6 landed as its own gated wave, each behind a default-OFF dial promoted to default-ON
only after a clean corpus pass (or narrowed/dropped on breakage), the per-increment tracking recorded
(masks / could-over-mask / corpus result / decision), `capture_bodies.py` guard re-declared for #2,
the credential floor re-verified unconditional every release (#9) with 7 guard shas byte-identical,
and #8 left parked absent evidence. The C-T2 docstring corrected once #4 lands.
