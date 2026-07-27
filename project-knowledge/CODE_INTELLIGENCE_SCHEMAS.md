<!-- version-agnostic; re-derive every count/SHA/version from source each session -->
<!-- verified-against: v3.66.818 -->
# Code-Intelligence Schemas

This document records the implemented foundation contract for deterministic,
offline graph extraction. `tools/l0_extract.py` produces `KNOWLEDGE_GRAPH.db`;
`tools/graph_build.py` produces the nine JSON projections below and validates
each one before writing it.

> Implemented foundation scope: deterministic graph extraction and nine
> schema-validated projections. Runtime analysis, review dispositions, and
> external static-KB synchronization require their named downstream gates and
> are not implied by successful graph generation.

## Shared projection envelope

Every generated projection carries these required envelope fields:

```json
{
  "schema": 1,
  "schema_name": "call_graph",
  "schema_version": 1,
  "source_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "tool_version": "graph-build-2",
  "input_hashes": {
    "bulk_downloader/a.py": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  },
  "generated_at": "2026-07-23T00:00:00Z"
}
```

`schema_name`, `schema_version`, `source_sha`, `tool_version`,
`input_hashes`, and `generated_at` are the shared validator-required fields.
`source_sha` and every `input_hashes` value are lowercase 64-character SHA-256
hex strings. `generated_at` is a timezone-aware timestamp. The writer also
emits `schema: 1` for the current graph projections. A legacy all-zero
`source_sha` is only valid with `source_binding: "unbound_legacy"`.

The projections are deterministic for the same source graph and inputs apart
from `generated_at`; `--check` compares the generated projection set while
ignoring that timestamp only.

## Implemented projections

The filenames and `schema_name` values are fixed by
`tools.graph_build.PROJECTION_SCHEMAS`.

### `CALL_GRAPH.json` (`call_graph`)

```json
{
  "nodes": ["bulk_downloader/a.py::caller"],
  "edges": [{"from": "bulk_downloader/a.py::caller", "to": "bulk_downloader/b.py::target", "kind": "call"}],
  "unresolved": [{"from": "bulk_downloader/a.py::caller", "name": "dynamic_target", "reason": "missing", "confidence": 0.0}]
}
```

This is the resolved intra-repository call inventory plus unresolved call
details; it is not a runtime reachability result.

### `CONFIG_LINEAGE.json` (`config_lineage`)

```json
{
  "settings": {
    "LIMIT": {
      "readers": ["bulk_downloader/a.py::sample"],
      "writers": ["bulk_downloader/a.py::sample"],
      "effect": null,
      "gui_exposure": null,
      "runtime_tunable": null,
      "confidence": 0.5,
      "field_confidence": {"effect": 0.0, "gui_exposure": 0.0, "runtime_tunable": 0.0},
      "provenance": {
        "method": "l0_static_analysis",
        "read_sites": [{"key": "LIMIT", "at": 4, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::sample", "call_site": {"path": "bulk_downloader/a.py", "line": 4}, "source": {"key": "LIMIT", "at": 4, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::sample"}}],
        "write_sites": [{"key": "LIMIT", "at": 5, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::sample", "call_site": {"path": "bulk_downloader/a.py", "line": 5}, "source": {"key": "LIMIT", "at": 5, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::sample"}}],
        "unknown_fields": {"effect": 0.0, "gui_exposure": 0.0, "runtime_tunable": 0.0}
      }
    }
  }
}
```

The three L2 fields remain null with zero confidence in the implemented
foundation; readers, writers, and their L0 provenance are structural facts.

### `CONCURRENCY_MAP.json` (`concurrency_map`)

```json
{
  "shared_state": [],
  "locks": [],
  "operations": []
}
```

Lock records are also operations. This is bounded L0 evidence, not a conclusion
about concurrency safety or shared-state completeness.

### `DEAD_CODE.json` (`dead_code`)

```json
{
  "uncalled": [{"fn": "bulk_downloader/a.py::candidate", "confidence": 0.5, "reason": "no_resolved_intra_repo_caller", "excluded_evidence": ["dynamic_dispatch", "framework_registration", "route_or_cli_entrypoint"], "path": "bulk_downloader/a.py", "qualname": "candidate"}],
  "uncalled_total": 1,
  "unreachable_routes": []
}
```

`uncalled` records a static candidate with stated exclusions; it does not prove
dead code or route reachability.

### `ERROR_CATALOG.json` (`error_catalog`)

```json
{
  "handlers": [{"at": "bulk_downloader/a.py:[1, 4]", "fn": "caller", "raises": ["ValueError"], "maps_to": null, "expected": null, "ok": null, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::caller", "provenance": {"method": "l0_static_analysis", "node_id": "bulk_downloader/a.py::caller", "path": "bulk_downloader/a.py"}}]
}
```

The foundation inventories statically observed raises. HTTP mapping and expected
status assessment remain downstream work.

### `METRICS_CATALOG.json` (`metrics_catalog`)

```json
{
  "metrics": [{"name": "sample.calls", "operation": "increment", "containing_function": "bulk_downloader/a.py::sample", "at": 4, "method": "name_substring", "confidence": 0.6, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::sample", "call_site": {"path": "bulk_downloader/a.py", "line": 4}, "source": {"name": "sample.calls", "operation": "increment", "at": 4, "method": "name_substring", "confidence": 0.6, "path": "bulk_downloader/a.py", "function": "bulk_downloader/a.py::sample"}}]
}
```

The catalog normalizes metric-emission sites from bounded L0 evidence; it does
not establish production telemetry delivery or coverage.

### `MODULE_CATALOG.json` (`module_catalog`)

```json
{
  "modules": {
    "bulk_downloader/a.py": {
      "purpose": null,
      "data_flow": null,
      "public_api": [{"name": "caller", "signature": "()", "raises": ["ValueError"]}],
      "invariants": [],
      "depends_on": ["bulk_downloader/b.py"],
      "depended_by": [],
      "sinks": ["path", "sql_fstring", "subprocess"],
      "secrets": [],
      "function_count": 1,
      "provenance": {"method": "l0_static_analysis", "node_id": "bulk_downloader/a.py", "path": "bulk_downloader/a.py", "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
    }
  }
}
```

`purpose` and `data_flow` are deliberately null in this L0 projection. L2/L3
interpretation, reviewed invariants, and advanced project knowledge are not
completed by this catalog.

### `SECURITY_SURFACE.json` (`security_surface`)

```json
{
  "auth_gates": [],
  "secret_sites": [],
  "sql_sites": [],
  "subprocess_sites": [],
  "path_sinks": [],
  "totals": {"auth_gates": 0, "secret_sites": 0, "sql_sites": 0, "sql_fstring": 0, "subprocess_sites": 0, "shell_true": 0, "path_sinks": 0}
}
```

This is a static inventory of bounded auth, secret-name, and sink facts. It is
not a security finding, exploit analysis, or governance disposition.

### `TAINT_MAP.json` (`taint_map`)

```json
{
  "sources": [],
  "sinks": [],
  "paths": [],
  "note": "sink inventory; source->sink paths are emitted only from explicit path evidence"
}
```

Paths are emitted only from explicit L0 path evidence and are labeled
heuristic, not proved data-flow analysis.

## Downstream boundaries

Successful validation or generation of these nine projections does not complete
runtime analysis, reachability, L2/L3 review, governance, contract execution,
audit completion, advanced knowledge, or external knowledge-base promotion.
Those activities remain governed by their sibling plans and their own named
gates. In particular, no projection grants a review disposition, proves a
contract at runtime, certifies an audit, or synchronizes content to an external
static KB.
