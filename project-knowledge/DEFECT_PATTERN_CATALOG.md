<!-- verified-against: v3.66.520 -->
<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
# Defect-Pattern Catalog

The seed for `defect_patterns.py` `[PLANNED]` — every confirmed bug-class, codified
as a detectable AST/grep pattern so the *next* instance is caught for free. Built
from the v3.66.520 parallel-verify pass (16 deduped code defects) + `F0001`. Each
pattern: the generalized rule, a detection signature, severity, and the finding it
came from. **Add a row whenever a new bug-class is confirmed** — this is the
flywheel.

> Detection notes use `ast` node shapes where precise, `grep -nE` where a regex is
> enough. A pattern is "high-precision" (auto-finding) or "triage" (candidate list).

---

## P-class — error contract / 500s

### DP-01 — non-object JSON body crashes a handler  (from VR-P09)
**Rule:** `request.get_json(silent=True) or {}` then `.get(...)` — `silent=True`
guards malformed JSON but a JSON array/scalar is *valid*, stays truthy, and `.get`
raises `AttributeError` → 500. **Signature (high-precision):** AST — `Call`
`get_json(silent=True)` feeding a `BoolOp(or)` whose result is later `.get`-ed
without an `isinstance(_, dict)` guard. Grep seed: `get_json(silent=True)\s*or\s*\{\}`.
**Fix shape:** `b = b if isinstance(b, dict) else {}`. **Sev:** medium (pre-auth on
auth-unconfigured cockpit routes). The 520 pass found this pattern 144× / 3 guarded.

### DP-02 — raise→status mismatch  (from VR-P12, VR-P09)
**Rule:** a handler returns 500 where the error contract wants 4xx (non-finite echo,
unhandled `AttributeError` outside the global errorhandler's path prefix).
**Signature (triage):** `except` that re-raises or serializes a value that may be
`inf`/`nan`/non-JSON; routes outside `_on_attribute_error`'s prefix. Cross-check via
`ERROR_CATALOG.json`. **Sev:** low–medium.

---

## P-class — numeric / type safety

### DP-03 — NaN/inf evade a range check  (from VR-P08, VR-P12)
**Rule:** `n = float(v); if n < lo or n > hi:` — both comparisons are `False` for
`NaN`, so it passes as valid and persists. **Signature (high-precision):** AST —
`float(...)` followed by a `Compare` bounds test with **no** `math.isfinite`/`isnan`
guard on the path. Grep seed: `float\(` near `<\s*\w+\s+or\s+\w+\s*>`. **Fix:** gate
`if not math.isfinite(n): flag`. **Sev:** medium (confirmed live-config corruption).

### DP-04 — string/loose-type accepted for a typed field  (from VR-P11)
**Rule:** `float(v)` + range-only, no int-ness/type check → `"8"`, `3.7` accepted for
int fields. **Signature (triage):** numeric validator using `float()`/`int()` coercion
without `isinstance(v, int)` (excl. bool). **Sev:** low.

---

## P-class — caller/callee contract drift (the F0001 family)

### DP-05 — call passes a kwarg/arg the callee doesn't accept  (from VR-P04, VR-P06, VR-P07)
**Rule:** a call site passes `site_id=`/`dry_run=`/a positional the callee's signature
lacks → `TypeError` (often swallowed → dead feature, or 500). **Signature
(high-precision):** resolve callee via the call graph / jedi; diff call kwargs vs
callee params (no `**kwargs`). This is `semantic_diff`'s core check. **Sev:** medium
(dead endpoints / dead features). Includes the inverse: callee signature changed,
caller not updated.

### DP-06 — bare undefined name reached at runtime  (from F0001)
**Rule:** a name with no `def`/`import`/binding in the module, reached on a
test-gap path (`hasattr(undefined_name, …)` swallows only `AttributeError`, not
`NameError`). **Signature (high-precision):** ruff `F821` **triaged** (97% FP here —
suppress annotation-position/DI/`in globals()` hits via `bd-triage`), cross-checked
against the import graph for a true missing binding. The 1 true positive of 31 was
F0001. **Sev:** medium–high (crash on the untested path).

#### Deferred analyzer limitation — v3.66.818

The lexical DP-06 analyzer still has a semantic-parentage defect: syntactic
AST-parent lookup can leak lambda/comprehension bindings into an expression's
runtime evaluator scope and suppress a real undefined-name finding. The exact
nested lambda/comprehension fixture reproduces as `NameError` on Stash with
Python 3.12.3.

Product runtime impact: none known. This affects the development scanner, not
BulkDownloader runtime behavior. Gate impact: DP-06 is not corpus-gated, so
the detector self-check and defect-total ratchet can remain green while this
candidate is missed.

Release disposition: the operator explicitly approved cutting and transferring
v3.66.818 before this analyzer limitation is fixed. This is a release-specific
deferral, not acceptance of the DP-06 precision work.

Acceptance: add a RED-first regression in
`tests/test_defect_scan_precision.py` for the Stash fixture which executes to
`NameError`, emits exactly one non-error DP-06 candidate at the isolated nested
evaluator reference, and remains silent for the valid inherited
first-iterator/default lookup. Replace syntactic-parent context selection with
semantic evaluator-parentage, then run focused DP-06 tests and the release
ratchet/gates.

---

## P-class — secrets / redaction

### DP-07 — non-recursive secret masking  (from VR-P02)
**Rule:** a `_mask`/`_is_secret` that checks only top-level keys and does **not**
recurse → a secret nested in a non-secret-keyed container (e.g. `accounts[].password`)
returns raw. **Signature (high-precision):** AST — a masking fn iterating
`dict.items()`/`keys()` with no recursive call on `dict`/`list` values. **Sev:** high
(credential leak to browser/logs). **Differential check:** compare every `_mask` fn
against its `_is_secret` predicate (a `differential_oracle` pair).

### DP-08 — redaction keyword-set divergence  (from VR-P03)
**Rule:** a bare-`key=value` secret detector recognizes a **narrower** keyword set
than the URL-query detector (a comment claims "mirrors the SoT" but it doesn't) →
OAuth `#code=`/non-URL `otp=` survive the floor. **Signature (high-precision):** set
difference between the two keyword constants — assert they're equal (or one consults
the other as SoT). **Plus:** `redact_query` fragment-blindness (treats `#frag` as
query-tail). **Sev:** medium–high (auth code into durable capture).

### DP-09 — scanner blind to non-JSON types  (from VR-THD)
**Rule:** a redaction scanner that passes `bytes`/`set`/unknown types through
untouched returns falsely-clean. **Signature (triage):** scan/walk fn with branches
only for `dict`/`list`/`str`. **Sev:** low (not reachable in JSON-only pipeline today).

---

## P-class — injection / SSRF / traversal

### DP-10 — user-controlled identifier in SQL string  (from VR-P10)
**Rule:** `f"… FROM {table}"`/`.format` where the identifier is reachable from a
request body (values are parameterized but the **identifier** is interpolated).
**Signature (high-precision):** ruff `S608` cross-checked with `TAINT_MAP` — flag only
when the interpolated token is taint-reachable. **Fix:** whitelist the identifier.
**Sev:** low–medium (bounded, authenticated).

### DP-11 — IP/SSRF classifier missing a range  (from VR-P15)
**Rule:** an IP-safety classifier (denylist style) omits a special range (CGNAT
100.64.0.0/10, etc.). **Signature (high-precision):** classifier not using
`ipaddress.ip_address(x).is_global`/`is_private` as the allowlist test. **Fix:** flip
to `if not addr.is_global: reject`. **Differential:** all IP classifiers must agree
(`_is_safe_public_host` ↔ `_classify_ip` were the same gap via two entry points).
**Sev:** low.

### DP-12 — path/host component interpolated without sanitize  (from S2-DEFECT-06, S1-G8)
**Rule:** a provider-ID/host fragment interpolated into a URL/path before stripping
`../`/CRLF. **Signature (triage):** `TAINT_MAP` path/fetch sink with an un-sanitized
provider-ID/host source. **Sev:** low (bounded by fixed host).

---

## P-class — dead feature / config drift

### DP-13 — swallowed-exception dead feature  (from VR-P07, the ≈1076 swallow cluster)
**Rule:** a feature path wrapped in `try/except: log; pass` whose body always raises
(signature drift, missing attr) → silently dead when enabled. **Signature (triage):**
ruff `S110`/`SIM105`/`S112` clusters cross-checked with `semantic_diff` (does the
try-body call a drifted signature?). **Sev:** medium when the feature is opt-in.

### DP-14 — third-party API drift  (from VR-P01, VR-P05)
**Rule:** code calls a vendored-library attribute that moved/renamed across versions
(`apprise.AppriseURLBase`→`URLBase`; `add(u)` without the `tag=` the later `notify`
filters on) → silent no-op or always-fail. **Signature (triage):** call to a
third-party symbol not present in the installed version (pyright/jedi resolves it as
missing); behavioral pairs like `add(...)`/`notify(tag=...)` where the tag isn't set.
**Sev:** high when it disables a whole feature (all notifications dropped).

### DP-15 — config path/default divergence  (from S4-OBS-1)
**Rule:** two resolvers for the same config artifact default differently (CWD-relative
vs `$BD_HOME`). **Signature (high-precision, differential):** two functions returning a
path for the same logical file with different default branches → assert one shared
resolver. **Sev:** low (deployment-dependent).

### DP-16 — env-coupled test asserting accumulated runtime state  (from VR-THA)
**Rule:** a test asserts `>=N` templates/drafts/runtime artifacts that exist only as
accumulated state on a live box → green on-stash, RED on a clean tree. **Signature
(triage):** test reading `templates/drafts/` or asserting `total >= 2` without
self-seeding. **Fix:** self-seed from shipped gold, cleanup-only-what-created. **Sev:**
n/a (test hygiene, not product).

---

## P-class — test isolation / latent perf

### DP-17 — process-global leak across tests  (from VR-THB)
**Rule:** a test/tool mutates a process-global (`sys.path` stub, `path_allowlist`,
a deleted module attr read unguarded) and doesn't restore → ordering-dependent
failures. **Signature (triage):** `sys.path.append`/global assignment in a test/tool
without a `finally` restore; `getattr` of a removed attr without default. **Sev:** low.

### DP-18 — unbounded scan → O(n²) on hostile input  (from VR-P13)
**Rule:** a per-char/per-segment scan with no length cap on attacker-influenced input.
**Signature (triage):** nested loop over an unbounded string field reachable from a
capture/URL. **Sev:** low (latent; bound the scan).

---

## Calibration carried from the methodology
- `F821` ~97% FP (suppress annotation/DI/`in globals()`); the 1 real = DP-06.
- ruff default `E`/`F` ~90% style — review the extended `S`/`SIM` clusters.
- vulture <90 = framework callbacks/dynamic dispatch noise.
- Every suppression lives in `bd-triage`, never hand-applied.
