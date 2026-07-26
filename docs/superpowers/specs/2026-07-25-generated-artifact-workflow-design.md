# Generated-Artifact Workflow Design

## Goal

Prevent dependency-graph, GUI-parity, route-index, endpoint-catalog,
function-index, and pin-index drift from being discovered late or repeatedly.

## Design

`toolchain/bin/bd-regen-order` is the single canonical regeneration command.
It discovers the repository root from its own location, prefers the repository
`.venv`, accepts an explicit work tree, preserves the existing generator order,
and never refreshes intent baselines without the existing declaration flags.

Local review and release workflows run the canonical command before their
verification gates. CI runs the same command in its disposable checkout and
then fails if any tracked generated artifact changed. This keeps CI
authoritative without committing generated output automatically.

The required order remains:

1. GUI parity inventory
2. route index
3. endpoint catalog
4. dependency graph
5. function index
6. pin index
7. route-count verification

Route-map, import-graph, and reachability ratchets remain explicit declarations
and are not silently re-pinned.

## Verification

Tests pin root discovery, Python selection, generator order, CI wiring, release
wrapper wiring, and the mandatory pre-review command. Controller verification
runs the canonical regeneration twice and requires identical tracked artifact
hashes after the first and second runs, followed by all generated-artifact
in-sync gates and the standing Task 5 suite.
