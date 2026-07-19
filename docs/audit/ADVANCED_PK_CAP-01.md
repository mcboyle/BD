# Advanced Project Knowledge — CAP-01 (v3.66.532)

*Rendered from `CAP-01_advanced.json` (advanced-kb/2 (belief-centric)). The JSON is the truth; this is a view. Mechanical facts (signatures/edges/sinks) live in the graph — this holds only what reading the code produced.*

## 1. Concepts, in the order you must learn them

*A curriculum, not an index: each concept lists what you must understand first. Read top-down and nothing is forward-referenced.*

**1. redaction profile / surface** — A per-capture policy (network_signed_urls / dom_embedded_urls / emails in {keep_full, strip/default, redact/keep}). It governs ONLY signed-URL STRUCTURE + emails -- NEVER the credential floor. 'keep_full' = reduced_redaction = the capture is stamped local_only.
  *Spans:* redaction_profile.py (REC batch), capture_artifact_redact.py.

**2. the sensitive-key Source of Truth** — capture_redact.SENSITIVE_QS_KEY -- the single regex that defines which query/kv keys mark a secret. The kv floor DERIVES from it (minus signing-metadata, plus header-origin csrf/xsrf/bearer) rather than keeping a parallel hand-maintained list. This derivation IS invariant I0008.
  *Spans:* capture_redact.py (REC batch), capture_artifact_redact.py.

**3. value-shape vs key-name detection** — A string is redacted by EITHER its key looking sensitive (key-name, SoT) OR its value looking like a secret (content: email/JWT/signed-URL/userinfo/opaque-token). Two-sided because bodies hide signing material under innocent key names -- key-name matching alone was the v3.66.52 fixture bug.
  *Prerequisites:* the sensitive-key Source of Truth.
  *Spans:* capture_artifact_redact.py, capture_bodies.py.

**4. the redaction floor** — The ALWAYS-ON credential scrub applied at capture time and at the WACZ export boundary. Hard credentials (jwt / userinfo / opaque-token / kv-secret) are removed regardless of profile; the floor is the irreducible guarantee that no credential reaches a derived/exported artifact.
  *Prerequisites:* redaction profile / surface, value-shape vs key-name detection, the sensitive-key Source of Truth.
  *Spans:* capture_artifact_redact.py, wacz_export.py, capture_bodies.py, session_capture.py, dom_capture.py.

**5. artifact provenance tiers** — RAW capture (in-memory, redact=False dev-only, loudly _UNREDACTED) -> ASSEMBLED capture dict (redacted at capture time) -> DERIVED artifact (template draft / review candidate). redact_artifact scrubs DERIVED; redact_capture+scan_floor_secrets gate the ASSEMBLED export. Confusing the tier is how a raw value leaks.
  *Prerequisites:* the redaction floor.
  *Spans:* session_capture.py, capture_artifact_redact.py, wacz_export.py.

**6. the WACZ export boundary** — build_wacz_bytes is the LAST gate before a capture becomes durable. It re-scrubs (defense-in-depth), force-scrubs floor residuals, re-scans, and RAISES WaczRedactionError if any hard credential survives. json.dumps here also fail-closes on non-JSON leaves.
  *Prerequisites:* the redaction floor, artifact provenance tiers.
  *Spans:* wacz_export.py.

## 2. Constraint surfaces (where bugs become countable)

*A constraint carves program states into legal/illegal. Bugs are points where the code crosses a surface it should respect. Count incidence vs guards; a hole is found by subtraction, not by hunting.*

### K-no-hard-cred — no hard credential (jwt / userinfo / opaque-token / raw kv-secret) may survive into capture.json, under ANY profile
- **Incidence:** 6 point(s), 6 guarded → **0 hole(s).**
    - `capture_artifact_redact.scan_floor_secrets` — detector ✓ guarded
    - `capture_artifact_redact._value_findings` — detector ✓ guarded
    - `wacz_export.build_wacz_bytes` — fail-closed gate (WaczRedactionError) ✓ guarded
    - `session_capture.record_network` — capture-time scrub ✓ guarded
    - `capture_bodies.redact_body` — body scrub ✓ guarded
    - `dom_capture.redact_dom_node` — DOM input-value scrub ✓ guarded
- **Witness:** W2 (jwt flagged under keep_full)
- 6 incidence points, 6 guarded -> 0 holes. The verify-pass method (count incidence vs guards) finds a hole by subtraction; here the count is clean.

### K-kv-from-SoT — the kv secret-keyword set must EQUAL or CONSULT the URL-query SoT (I0008) -- it may be a strict SUBSET of the SoT (it subtracts signing-metadata) but must never DRIFT from it
- **Incidence:** 1 point(s), 1 guarded → **0 hole(s).**
    - `capture_artifact_redact._kv_key_is_secret` — the derivation ✓ guarded
- **Witness:** W1 (12 credential keys scrub, 6 structural keys kept)
- Single incidence point. The HISTORICAL hole (VR-P03) was a parallel hand-maintained tuple that drifted from the SoT -> code/state/apikey/... survived. The fix removed the parallel list. A reintroduced parallel list reopens the hole -- this is exactly counterfactual MOD-C.

## 3. Exceptions to the rules you just learned (the bug-magnets)

*A newcomer gets hurt by the carve-outs, not the rules. Each is a relation between a general rule and its scoped exception.*

### X-keep_full-signed-url
- **Rule:** K-no-hard-cred / the floor scrubs sensitive query params
- **Exception:** a signed_url / kv_secret residual in a query is LEGAL (forgiven by scan_floor_secrets) when the surface is keep_full
- **Why allowed:** keep_full is a deliberate operator choice (reduced_redaction) for a network surface where the signed URL's structure is wanted; the capture is then stamped local_only and must never circulate. Hard credentials (jwt/userinfo/opaque) are STILL never forgiven -- the exception is scoped to the signed-URL/kv-query shape only.
- **At:** `capture_artifact_redact.scan_floor_secrets (surface_full skip)` · **Witness:** W3 (keep_full forgives signed residual; default surface flags it)
- **Teaching:** This is the #1 thing a newcomer gets wrong: they read 'the floor scrubs signed URLs' and assume always. It does NOT under keep_full. The carve-out is the bug-magnet.

## 4. Beliefs — purpose vs behavior, with witnesses

*Each belief carries how it was known (derivation), how sure (confidence), and — where reading produced them — the assumptions the code makes by NOT checking, the surprises, and the corpses of rejected alternatives.*

### B-redact-core-purpose — `capture_artifact_redact.py`
- **Stated purpose:** redact secret CONTENT from capture-derived artifacts; scan_artifact_secrets returns ALL residual sensitive values so callers/tests can prove emptiness
- **Observed behavior:** matches for str/dict/list leaves; DIVERGES for bytes/set leaves -- scan_artifact_secrets / scan_floor_secrets silently SKIP them (return no finding) rather than flagging. So 'returns all residuals' is true only for JSON-shaped input.
- **Purpose vs behavior:** ⚠️ DRIFT (benign-mitigated): stated 'all residuals' vs observed 'JSON leaves only'. Mitigated because (a) inputs are JSON-derived and (b) the wacz_export json.dumps boundary raises on a bytes/set leaf (fail-closed). The canonical bytes/set scan lives upstream in capture_redact.scrub_globals (I0010).
- **Confidence:** confirmed · **Derivation:** read every line + traced the leaf-type branches in both scanners + confirmed the json.dumps backstop by witness W4
- **Witness:** W4 (json.dumps raises on bytes leaf)
- **Assumption (by absence-of-check):** capture leaves are JSON-serializable (str/dict/list/num/bool/None) -- assumed by NOT type-checking non-JSON leaves in the scanners → *verified-mitigated* (tested_by: W4 + the upstream scrub_globals guard test_v3_66_529)
- **Surprise:** the always-on kv floor is a strict SUBSET of the SoT, not a superset -- it SUBTRACTS signing-metadata (expires/policy/hash/x-amz-) before re-checking. Counterintuitive: you expect a 'floor' to catch MORE than the configurable pass, but here it deliberately catches a careful subset so a keep_full surface can still retain Expires.
- **Rejected alternative / tempting mistake:** wholesale-delegate the kv floor to the SoT (just call SENSITIVE_QS_KEY) → *why it fails:* the SoT also flags signing-metadata keys (expires/policy/x-amz-) which a keep_full surface legitimately RETAINS; delegating over-strips Expires and regressed test_v3_66_245. The subtract-then-recheck dance exists precisely to avoid this. Corpse documented in the _kv_key_is_secret docstring.

### B-kv-SoT-derivation — `capture_artifact_redact._kv_key_is_secret`
- **Stated purpose:** True iff a kv key marks its value a CREDENTIAL the always-on floor scrubs even under keep_full
- **Observed behavior:** csrf/xsrf/bearer via _KV_CRED_EXTRA; else SoT-match minus signing-metadata residue. Correctly scrubs code/state/apikey/challenge/captcha/nonce/otp; keeps color/width/resolution.
- **Purpose vs behavior:** ✓ MATCH
- **Confidence:** confirmed · **Derivation:** read + witness W1 (probe of 12 must-scrub / 6 must-keep keys)
- **Witness:** W1
- **Enforces:** K-kv-from-SoT, I0008
- **History:** This IS the VR-P03 fix (v3.66.521). Before it, _KV_SECRET_KEYWORDS was a hand-maintained tuple that drifted from the SoT, missing the anchored OAuth-fragment keys code/state + apikey/challenge/captcha/nonce/otp -> an auth #code=...&state=... landed in capture.json. The fix is load-bearing; see counterfactual MOD-C.

### B-bodies-manifest-gap — `capture_bodies.py`
- **Stated purpose:** no signing material survives in a stored body -- not a JWT, not a signed URL, regardless of how the body labels its fields
- **Observed behavior:** holds for JSON/text bodies; does NOT yet hold for HLS/DASH MANIFEST bodies: a PATH-signed segment URL or #EXT-X-KEY/#EXT-X-MAP URI is neither detector-flagged nor a floor secret and CAN survive.
- **Purpose vs behavior:** ⚠️ KNOWN DRIFT (documented, scoped): the wholesale guarantee is explicitly suspended for manifest bodies; mitigated by stamping any BD_CAPTURE_BODIES=1 manifest capture local_only.
- **Confidence:** confirmed · **Derivation:** read the DEFERRED-F2 block + traced _redact_text token scrub (whitespace-bounded; a path-signed URI has no whitespace-bounded secret token and no query key)
- **Open question (operator/Matt):** Is the DEFERRED-F2 manifest path-signed-URI gap owned/scheduled (STRUCTURE-mode hardening), or is 'local_only' the permanent answer? No tracker item id is referenced in-code.
- **Coverage gap:** `a path-signed segment URL / #EXT-X-KEY URI in a retained manifest is masked` — tested=False (F0001/VR -- the behavior is ASSERTED-absent (it currently is NOT masked) so there is no green test; a future STRUCTURE-mode fix needs a RED-first test here)

### B-live-recorder-ssrf — `bulk_downloader/live_recorder.py`
- **Stated purpose:** record live cam streams from KNOWN cam sites (parse_live_url host-allowlist)
- **Observed behavior:** on the site_override+room_override path, parse_live_url is SKIPPED -- only URL SHAPE (^https?://...$) is checked, NOT the host. An arbitrary http(s) host (incl. 169.254.169.254 metadata, localhost:5555) is accepted and fetched by streamlink/ffmpeg.
- **Purpose vs behavior:** ⚠️ DRIFT (the bug): stated 'known cam sites' vs observed 'any http host on the override path'.
- **Confidence:** confirmed-at-boundary · **Derivation:** read watch()+_load_state()+_is_room_live+_build_cmd + RAN witness W6 (watch() accepted a metadata IP). Reachability to an untrusted caller is cross-batch (APP route) -> overall severity still pending that audit.
- **Witness:** W6 (CONFIRMED: watch() override path returns ok=True for a link-local metadata IP)
- **Enforces:** I-CAP01-rec-url-shape (shape only -- host NOT constrained on override path)
- **Assumption (by absence-of-check):** callers of watch() that pass site_override+room_override supply a trusted host -- assumed by NOT host-checking on that path → *KNOWN-FALSE if the override params are reachable from an untrusted API caller* (tested_by: W6 confirms the capability; APP-batch confirms reachability)

## 5. The counterfactual test — can you predict a change you haven't made?

*Three proposed modifications to the redaction floor. Using ONLY the rendered CAP-01 KB (not the code), sort them: which are SAFE and which reintroduces a known bug?*

| Mod | Change | KB prediction | Ground truth |
|---|---|---|---|
| MOD-A | add 'application/ld+json' to capture_bodies _is_text_or_json eligibility | **SAFE** | safe |
| MOD-B | lower _OPAQUE_MIN from 40 to 32 in capture_artifact_redact | **SAFE (strictly-more-scrubbing; low FP risk)** | safe |
| MOD-C | replace _kv_key_is_secret's SoT-derived check with a hand-maintained tuple ('password','token','secret','authorization') | **UNSAFE -- reintroduces VR-P03** | unsafe-reintroduces-VR-P03 |

- **MOD-A — SAFE.** C-value-vs-key: an ld+json body flows through the SAME two-sided redactor (_redact_json: key-name SoT + value-shape). The floor is content-type-agnostic on the value-shape side. New eligible type != new leak path. K-no-hard-cred incidence unchanged.
- **MOD-B — SAFE (strictly-more-scrubbing; low FP risk).** Lowering the opaque-token floor only ever scrubs MORE (never less), so K-no-hard-cred cannot be weakened. The surprise/structure carve-outs in _looks_like_opaque_token (hex hashes, dotted module paths, CSS class-chains) still protect structure. Net: safe for secrets; watch FP on 32-39-char structural blobs, but those hit the existing carve-outs.
- **MOD-C — UNSAFE -- reintroduces VR-P03.** K-kv-from-SoT + B-kv-SoT-derivation.history: a hand-maintained tuple is EXACTLY the pre-VR-P03 shape that drifted from the SoT. This tuple drops code/state/apikey/challenge/captcha/nonce/otp -> an OAuth #code=...&state=... fragment survives the kv floor into capture.json. Witness W1 goes RED (those keys stop scrubbing). This is the rejected-alternative the KB explicitly warns is a corpse.

**Pass condition:** A reader sorts A=safe, B=safe, C=unsafe using only the KB. The KB must let them PREDICT C reintroduces a specific historical bug -- not just describe what _kv_key_is_secret currently is. If the KB only described, C is indistinguishable from B (both 'change a redaction function').

**Result (executed):** KB predicted A=safe · B=safe · C=unsafe; ground truth confirmed all three (MOD-C dropped exactly the VR-P03 OAuth-fragment keys code/state/apikey/challenge/captcha/nonce/otp; A/B leaked nothing). The KB conferred *prediction*, not just description — the discriminator between C (unsafe) and B (safe) was the rejected-alternative corpse + the kv-from-SoT constraint + witness W1, none of which a map-only KB carries.
