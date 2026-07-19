# AUDIT CAP-01 — capture / redaction / guard surface (v3.66.532)

**Batch:** CAP-01 · **Audited source:** v3.66.532 (live = built = deployed; ledger `generated_against` = 3.66.532)
**Scope:** 9 files / 3,935 SLOC (4,998 raw lines) — the capture → WACZ → redaction intake.
**Method:** every line read to the AUDIT_PLAN §4 rubric, graph pre-digest (risk / security-surface / taint / open-findings / invariants) as the map, `~/rev` battery (bandit/vulture/radon) as cross-check. **F2:** kinds/counts only — no captured values read or recorded.
**Attestation:** `guard_touch=false` · `tracker_write=false` · tree re-verified byte-identical (`bd-preflight` PASS, 2207/2207). Read-only throughout.

---

## Verdict

**0 confirmed defects. 1 triage finding (cross-batch SSRF). 9 positive assurances.** The redaction surface is mature and well-defended — several gaps I probed turned out to be already closed by prior audits (notably the v3.43.62 live-recorder URL hardening and the v3.66.521/529 redaction-floor fixes). The one genuinely open item (F-CAP01-01) is a host-allowlist bypass on a `live_recorder.watch()` override path whose true severity depends on APP-layer route wiring not in this batch.

The 5 capture-side **guard files** were SHA-verified byte-identical to the STATE pin before reading and never edited:

| Guard file | SHA | match |
|---|---|---|
| `tools/capture_session.py` | 27be68b9 | ✓ |
| `bulk_downloader/session_capture.py` | 547d70c9 | ✓ |
| `bulk_downloader/dom_recorder.py` | 1657d0a0 | ✓ |
| `bulk_downloader/dom_capture.py` | 0559903d | ✓ |
| `bulk_downloader/capture_bodies.py` | 2d851646 | ✓ |

---

## Per-file (risk-ordered)

**`capture_artifact_redact.py`** (risk 0.399) — the derivation-boundary redaction core. Content-driven detectors (email/JWT/signed-URL/userinfo/kv-secret/opaque-token), all ReDoS-bounded ({m,n}), depth-capped walks. **I0008** (KV keyword set derived from the SoT `SENSITIVE_QS_KEY` minus signing-metadata plus header-origin csrf/xsrf/bearer) is correctly implemented (the VR-P03 fix). The scanners silently skip `bytes`/`set` leaves — acceptable here because inputs are JSON-derived and the WACZ `json.dumps` boundary fail-closes on a non-JSON leaf; the canonical bytes/set fix lives upstream in `capture_redact.scrub_globals` (guarded by `test_v3_66_529`). *Positive assurance.*

**`capture_bodies.py`** (0.221, guard) — opt-in (default OFF) body retention, two-sided key+value-shape redaction, bounded depth/size. Self-documents one **known residual**: the DEFERRED-F2 path-signed manifest-URI gap (`#EXT-X-KEY`/`#EXT-X-MAP`, path-signed segments survive the text scrub), mitigated by the "local-only, never circulate" stamp. Tracked, not a new finding. *Positive assurance.*

**`provenance.py`** (0.201) — append-only tamper-evident ledger. **Injection clear:** `query()` builds its dynamic WHERE from constant `" AND col = ?"` fragments with every value bound — the 12 "sql_sites" are this safe pattern, and `:91` "sql_fstring" is a false positive (an stderr error-message f-string). Fail-open is documented/intended; `verify_chain` is the backstop. *Positive assurance.*

**`wacz_export.py`** (0.186) — WACZ packager. Export-boundary scrub that **fails closed** (`WaczRedactionError`, kinds-not-values) after a force-scrub re-scan. Its `json.dumps(capture)` is the serialization backstop that fail-closes the artifact-scanner non-JSON-leaf gap. *Positive assurance.*

**`dom_capture.py`** (0.174, guard) — DOM/behavioral capture. block/mask classes + Wave-2 F2 input-value masking + storage value-dropping (sink-side). Two **known residuals** (documented): raster `record_dom_snapshot` PII (opt-in, caller-gated, never auto-taken logged-in) and mask length-disclosure ≤8 chars. *Positive assurance.*

**`session_capture.py`** (0.141, guard) — A-T1 capture core. Capture-time redaction default-ON; cookies/request-bodies → placeholder; WS metadata-only by default; `_UNREDACTED` dev stamp inert in release. **Challenge handling is automation-policy-clean** — detection-only, passive self-clear, explicit human handoff ("Never solve"), redacted evidence bundle, resume gated on `operator_complete()`+`can_resume()`. *Positive assurance.*

**`tools/capture_session.py`** (0.142, guard) — CLI live driver. No SQL (`:270` is a `print()` banner FP). path_sinks 175/189/1093 are operator/CLI-controlled url-memory + WACZ-verify reads (atomic writes, query-stripped values, no web taint). No eval/exec/subprocess. *Positive assurance.*

**`dom_recorder.py`** (0.020, guard) — rrweb wiring. All injected JS vendored-static; every `page.evaluate` a static string; `maskAllInputs:true`; no-CDN guarantee; bounded buffer/snapshot with drop-counting. The `";"` ASI separator is one of the 5 ASI checks (`test_dom_recorder_asi`), distinct from the 7 release guards. *Positive assurance.*

**`live_recorder.py`** (0.059) — live-stream recorder. Both subprocess sites are **list-form, shell=False**; argument injection is closed because `rec.url` is entry-validated `^https?://...$` at **both** population paths (`watch()` and `_load_state()` rehydration — both added by the v3.43.62 audit with explicit "recordings.json is user-writable, do not trust" reasoning). Room slugs allowlisted. → **F-CAP01-01** below. *Positive assurance + 1 triage finding.*

---

## Finding

### F-CAP01-01 — SSRF residual on the `watch()` override path · medium · **triage**
`bulk_downloader/live_recorder.py` 421–438. On the normal path `parse_live_url` constrains the URL host to known cam sites; when **both** `site_override` and `room_override` are passed, that host check is skipped and only the `^https?://...$` **shape** check remains. A shape-valid but arbitrary-host URL (`http://169.254.169.254/...`, `http://localhost:5555/...`, any private host) is then stored as `rec.url` and fetched by `streamlink --json` / `ffmpeg -i` → the server fetches an operator-chosen internal URL. Not shell/arg injection (so the mechanical `shell=false` scan scored it safe and missed it — a manual-only catch).
**Fix:** constrain the host on the override path too — require `parse_live_url` to recognize the host, or add an `ipaddress.is_global` SSRF guard (mirroring **I0003**), in both `watch()` and `_load_state()`.
**Reachability (why triage, not confirmed):** depends on the APP-layer route that calls `watch()` — is it behind the operator auth gate, and are the override params caller-exposed? That wiring is in the APP batch. Do not fix in isolation until the cross-batch route audit confirms reachability.

---

## New invariant (UNGUARDED candidate)

**I-CAP01-rec-url-shape** — any `url` stored in a live_recorder `Recording` must be entry-validated `^https?://...$` before reaching a subprocess argv; both `watch()` and `_load_state()` must enforce it (`_is_room_live` relies on the invariant rather than re-validating). Enforced by convention today; UNGUARDED until a serial fix-cut adds a RED guard (pair it with the F-CAP01-01 host-allowlist fix).

## New defect-pattern (candidate)

**DP-CAP01-cand-1** — list-form `shell=False` is not sufficient to clear a URL/path argv element: also require shape-validation (no leading `-` → arg injection) and host-constraint if the value triggers a network fetch (SSRF). Promote to `defect_patterns.py` after confirming on the F-CAP01-01 repro.

## False-positive confirmations (for `bd-triage`)
`provenance.py:91` and `capture_session.py:270` "sql_fstring" → stderr/print f-strings, not SQL sinks. `provenance.py` 12 "sql_sites" → the safe parameterized dynamic-WHERE pattern (constant fragments, bound `?` values; scanner left `parametrized=null`).
