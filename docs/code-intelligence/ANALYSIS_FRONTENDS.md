# Code-Intelligence Analysis Frontends

These commands are standalone. They do not change production service behavior and
are not wired into `bd-audit-gate` until a later integration review.

## Common result states

`pass`, `fail`, `advisory`, `unknown`, `timeout`, and `error` are distinct.
Unknown is never a synonym for pass. `--gate` converts a required unknown or a blocking policy violation into exit 1.

| Exit | Meaning |
|---:|---|
| 0 | completed with pass, advisory, or non-gating unknown |
| 1 | blocking failure, gate-required unknown, timeout, or execution error |
| 2 | CLI usage or input-validation error |

## bd-coverage-map

Schema: `bd.coverage-gaps` version 2.

```bash
python3 toolchain/bin/bd-coverage-map \
  --root . \
  --coverage /path/to/coverage.json \
  --graph /path/to/KNOWLEDGE_GRAPH.db \
  --radon-json /path/to/radon.json \
  --test-catalog /path/to/test_catalog.json \
  --out /path/to/COVERAGE_GAPS.json \
  --json
```

Omit `--coverage` only to record an explicit `unknown` artifact. Use `--check
/path/to/COVERAGE_GAPS.json` to compare deterministic content. `radon` is
optional; standard-library execution works without `radon`.

## semantic_diff.py

Schema: `bd.semantic-diff` version 1.

```bash
python3 tools/semantic_diff.py \
  --before-tree /path/to/old \
  --after-tree /path/to/new \
  --out /path/to/SEMANTIC_DIFF.json \
  --json
```

Tree and snapshot inputs are mutually exclusive per side. `--cst-adapter libcst` is optional and cannot change the standard-library policy verdict. Standard-library execution works without `libcst`.

## reachability.py

Schema: `bd.reachability` version 1.

```bash
python3 tools/reachability.py \
  --root . \
  --app bulk_downloader.app:app \
  --security-surface /path/to/SECURITY_SURFACE.json \
  --call-graph /path/to/CALL_GRAPH.json \
  --deferrals /path/to/REACHABILITY_DEFERRALS.json \
  --out /path/to/REACHABILITY.json \
  --json
```

Authenticated probing requires an explicit `--authenticated-fixture module:function`. Operator wiring, navigation, auth probes, graph paths, and deferrals remain separate evidence fields.

## differential_oracle.py

Schema: `bd.differential-oracle` version 1.

```bash
python3 tools/differential_oracle.py --list-adapters
python3 tools/differential_oracle.py \
  --root . \
  --adapter consumer-agreement \
  --adapter url-classifier-truth \
  --corpus /path/to/oracle-corpus \
  --seed 17 \
  --out /path/to/DIFFERENTIAL_ORACLE.json \
  --json
```

Allowed divergences remain visible and do not hide forbidden divergences.

## fuzz_harness.py

Schema: `bd.fuzz-results` version 1.

```bash
python3 tools/fuzz_harness.py --list-adapters
python3 tools/fuzz_harness.py \
  --root . \
  --adapter redaction \
  --adapter path-guard \
  --seed 42 \
  --timeout 10 \
  --reproducer-dir /path/to/reproducers \
  --out /path/to/FUZZ_RESULTS.json \
  --json
```

The standard-library replay runner is always available. `--generator hypothesis` is optional and belongs in the isolated audit environment. Importing the module does not execute fuzzing. Standard-library execution works without `hypothesis`.
Built-in adapters own their internal corpora and reject `--corpus`. The
`--corpus` option is reserved for externally registered adapters that consume
the supplied versioned corpus through the shared adapter context.

## Optional-dependency compatibility

Standard-library execution works without `libcst`, `hypothesis`, or `radon`.
When installed, these optional packages add their respective analysis modes but
do not change the documented policy boundaries above.

## Secret and path safety

Artifacts and reproducers contain normalized relative paths, hashes, case IDs,
counts, and scrubbed summaries. They must not contain credentials, cookies,
authorization headers, signed queries, or raw captured bodies.
