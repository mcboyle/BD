# Defect Scanner Precision Design

## Goal

Unblock the v3.66.818 release without raising or bypassing the
`defect_DP_total=2314` ratchet. Remove the 44 independently reviewed false
positives introduced by the code-intelligence work while retaining positive
controls for every affected detector.

## Scope

The active scanner is `toolchain/bin/bd-defect-scan`. Its installed static-KB
mirror is `project-knowledge/bd-defect-scan`, and `tools/defect_patterns.py`
contains the same detector core behind a different CLI. The change is limited
to detector precision, regression tests, reviewed triage metadata, and the
static-KB manifest entry affected by the mirrored scanner. It must not
regenerate graph, GUI-parity, route, or unrelated knowledge artifacts.

## Detector corrections

- **DP-03:** use statement order and variable-local data flow. A comparison
  before `float()` is not evidence that the converted value feeds the
  comparison. Preserve the true-positive case where post-conversion bounds
  omit a finite-value guard.
- **DP-06:** resolve names in their lexical scope, including parameters,
  imports, assignments, loop/with/exception targets, definitions, builtins,
  and enclosing bindings. A sibling scope must never satisfy a missing name.
- **DP-08:** compare only module-level keyword sets representing equivalent
  redaction surfaces. Token, token-sequence, and compact-suffix
  representations are intentionally different domains.
- **DP-10:** construct an f-string skeleton and require a formatted expression
  in an SQL identifier position. English text containing `from`, `into`, or
  `join` is not SQL.
- **DP-13:** recognize only pass statements and logger-shaped calls as
  pass/log-only handlers. Non-logging recovery actions and explicit
  try/except/else predicates are not swallow candidates.
- **DP-15:** compare actual defaults for the same logical artifact.
  `abspath(parameter)` is normalization, not a default strategy.
- **DP-18:** require nested iteration derived from string-like input and no
  effective bound. A bounded graph traversal whose function name contains
  `path` is not an unbounded string scan.

Nested functions and classes are separate scopes for every detector.

## Reviewed residual triage

Some DP-13 bare-pass cleanup handlers are syntactically indistinguishable from
real swallowed failures. They remain detector candidates, but a tree scan may
exclude only entries present in a reviewed suppression ledger.

Each ledger entry contains:

- detector ID;
- repository-relative path;
- a SHA-256 fingerprint of the normalized AST node;
- a concise rationale.

The line number is informational, not identity. Any semantic edit changes the
AST fingerprint, invalidates the entry, and makes the candidate reappear. The
scanner must reject malformed or duplicate ledger entries and must never treat
an unreadable ledger as an empty one.

## Test strategy

`tests/test_defect_scan_precision.py` loads the active scanner and follows
red/green TDD:

1. Actual-file negative controls reproduce all seven audited detector classes.
2. Synthetic positive controls prove the original vulnerable shapes still
   fire.
3. DP-06 sibling-scope and DP-13 logger/pass controls prevent broad
   suppression.
4. Ledger tests prove exact matches suppress, changed AST fingerprints
   reappear, and malformed ledgers fail closed.
5. A parity test keeps the detector core synchronized across the active,
   static-KB, and tools copies.

## Acceptance criteria

- The regression tests fail against the pre-fix scanner and pass after the
  minimal corrections.
- A cold scan of the merged source reports no detector errors and
  `total_findings <= 2314`.
- `/root/.bd_metrics_baseline.json` remains byte-for-byte unchanged, including
  `defect_DP_total=2314` and `coupling_ratio=0.45`.
- `bd-ratchet --check --tree /root/BulkDownloader-main` exits zero.
- The scanner self-test, focused scanner tests, the 719-test expanded suite,
  release gates, and ZIP verification all pass freshly.
- Only the scanner mirror's static-KB manifest entry is refreshed; unrelated
  generated artifacts remain untouched.
- Packaging resumes only after these checks are green.

## Rollback

The scanner/test/ledger commit is isolated from the release-version commit.
If verification fails, abandon the release branch or revert that commit; the
baseline and previously merged main commit remain unchanged.
