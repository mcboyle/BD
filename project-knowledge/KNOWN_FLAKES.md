# Known test hazards and diagnostic outcomes

This card is machine-consumed by `toolchain/bin/bd-flakes`. It is a diagnostic
aid, not permission to turn a failed mandatory test into success. Re-derive a
failure against the exact candidate, preserve the first raw result, and classify
the mechanism before selecting a retry.

## Stable operating rules

- Use the sanctioned real-pytest command with fixed `-n 24`, `--dist loadfile`,
  and the repository timeout contract for the canonical full suite.
- A serial replay can distinguish load/scheduling from deterministic behavior;
  it never replaces the canonical result by itself.
- Do not kill by a short `pkill -f` pattern. It can match the controller's own
  command line. Kill and verify the exact owned process group.
- Preserve complete logs, raw exit status, collection/execution denominators,
  and pre/post repository state for every failed or superseded attempt.

## Previously observed mechanisms

- Browser wall-clock assertions can exceed their threshold under host load while
  passing in a low-load complete suite. Preserve both results and confirm with a
  complete canonical run; do not average the failure away.
- `test_v3_66_729_body_contract_fixtures.py` historically accumulated singleton
  state across placement/order. The production isolation fix and its regressions
  are the authority; the old high-worker symptom is not a standing skip.
- `test_session_keeper.py::test_get_takeover_lock_is_reentrant` and
  `test_consolidation.py::test_health_wrapper_equals_core` have produced
  parallel-only diagnostic failures. A current serial replay is required before
  calling the mechanism environmental.
- Capture-corpus, browser, live-site, or optional-AI skips are valid only when the
  current test emits its explicit unavailable-environment reason. Historical
  skip totals are not a current denominator.

## Never classify as a flake

- An unexplained guard or generated-artifact drift.
- A release ZIP verification failure.
- Missing, duplicate, deselected, or zero-test execution.
- A worker crash, internal pytest error, timeout, swallowed subprocess status,
  dirty checkout, or unexplained residue.
- Any failure that reproduces against the same exact inputs.
