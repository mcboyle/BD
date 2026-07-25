#!/usr/bin/env python3
"""graph_build -- resolve call edges + materialize the §3-§12 projections.

Reads KNOWLEDGE_GRAPH.db (populated by l0_extract), resolves intra-repo call
edges (callee name -> defining function node), adds `call_resolved` edges, and
emits the deterministic JSON projections that the audit sessions consume:
  CALL_GRAPH.json · MODULE_CATALOG.json · SECURITY_SURFACE.json ·
  ERROR_CATALOG.json · TAINT_MAP.json · DEAD_CODE.json ·
  CONFIG_LINEAGE.json · CONCURRENCY_MAP.json · METRICS_CATALOG.json

Mechanical fields only -- the L2 (purpose/data_flow) are left null for the audit
sessions to fill (SCHEMAS §3 gate checks presence, not value). stdlib + offline.

Usage:  python3 graph_build.py [--db DB] [--outdir DIR]
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SCHEMA = 1
TOOL_VERSION = "graph-build-2"
AUTH_DECOS = ("require", "login_required", "auth", "csrf", "admin", "bearer")
WRITE_ROUTE_DECOS = ("post", "put", "patch", "delete")
UNKNOWN_SOURCE_SHA = "0" * 64
PROJECTION_SCHEMAS = {
    "CALL_GRAPH.json": "call_graph",
    "CONFIG_LINEAGE.json": "config_lineage",
    "CONCURRENCY_MAP.json": "concurrency_map",
    "DEAD_CODE.json": "dead_code",
    "ERROR_CATALOG.json": "error_catalog",
    "METRICS_CATALOG.json": "metrics_catalog",
    "MODULE_CATALOG.json": "module_catalog",
    "SECURITY_SURFACE.json": "security_surface",
    "TAINT_MAP.json": "taint_map",
}
PROJECTION_FILENAMES = tuple(PROJECTION_SCHEMAS)
TAINT_PATH_CONTROL_FIELDS = frozenset({
    "confidence",
    "evidence_status",
    "explicit",
    "is_proof",
    "method",
    "proof",
    "proof_status",
    "proven",
    "provenance",
    "reason",
    "source",
    "source_function",
    "source_path",
    "source_to_sink_proof",
    "verified",
})


@dataclass(frozen=True)
class GraphInput:
    source_sha: str
    input_hashes: dict[str, str]
    modules: dict[str, dict]
    functions: dict[str, dict]
    calls: tuple[tuple[str, str, dict], ...]
    contains: dict[str, tuple[str, ...]]


def _foundation_functions() -> tuple[Callable, Callable, Callable, Callable]:
    """Load the shared artifact helpers only when projections are requested.

    Hash-only release gates intentionally copy this script without the rest of
    the tools package. Keeping the projection dependency lazy preserves that
    established interface while still requiring the shared implementation for
    every durable projection write.
    """
    repository_root = Path(__file__).resolve().parent.parent
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    try:
        from tools.code_intelligence.artifacts import atomic_write_json as atomic
        from tools.code_intelligence.schemas import is_safe_config_key as safe_key
        from tools.code_intelligence.schemas import make_envelope as envelope
        from tools.code_intelligence.schemas import validate_projection as validate
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeError(
            "shared code-intelligence artifact foundation is required"
        ) from error
    return atomic, envelope, validate, safe_key


def atomic_write_json(path, value, validator):
    """Delegate projection writes to the shared atomic JSON implementation."""
    atomic, _, _, _ = _foundation_functions()
    return atomic(Path(path), value, validator)


def make_envelope(name, version, source_sha, tool_version, input_hashes):
    """Delegate envelope construction to the shared schema implementation."""
    _, envelope, _, _ = _foundation_functions()
    return envelope(name, version, source_sha, tool_version, input_hashes)


def validate_projection(name, value):
    """Delegate projection validation to the shared schema implementation."""
    _, _, validate, _ = _foundation_functions()
    return validate(name, value)


def is_safe_config_key(value):
    """Delegate config-key grammar checks to the shared schema definition."""
    _, _, _, safe_key = _foundation_functions()
    return safe_key(value)


def _compare_artifact_dirs(left, right):
    """Load the shared comparator lazily for hash-only CLI compatibility."""
    repository_root = Path(__file__).resolve().parent.parent
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    try:
        from tools.code_intelligence.artifacts import compare_artifact_dirs
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeError(
            "shared code-intelligence artifact foundation is required"
        ) from error
    return compare_artifact_dirs(Path(left), Path(right))


def projection(name, graph, payload):
    artifact = {
        "schema": SCHEMA,
        **make_envelope(
            name,
            SCHEMA,
            graph.source_sha,
            TOOL_VERSION,
            graph.input_hashes,
        ),
        **payload,
    }
    if graph.source_sha == UNKNOWN_SOURCE_SHA:
        artifact["source_binding"] = "unbound_legacy"
    return artifact


def _canonical_json_text(value):
    if value is None:
        return ""
    text = str(value)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text
    return json.dumps(
        parsed,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _table_exists(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _column_names(connection, table):
    return {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table})")
    }


def content_hash(db):
    """Deterministic CONTENT digest of the graph (P2).

    The raw KNOWLEDGE_GRAPH.db file hash is fragile -- SQLite re-lays pages on
    VACUUM / re-save, so `sha256sum the .db` drifts even when the graph is
    identical (the pilot saw f02b vs pinned 79f2). This hashes the *logical*
    content instead: every node and edge row, canonicalized (sorted, stable
    field order), so a content-preserving re-save yields the SAME hash and a
    real node/edge change yields a different one. This is what KNOWLEDGE_GRAPH.
    db.sha256 pins; check_hash() recomputes and compares it.
    """
    cx = sqlite3.connect(db)
    try:
        nodes = []
        for row in cx.execute(
            "SELECT id,kind,path,qualname,span,sha256,lines,meta_json FROM nodes"
        ):
            nodes.append((*row[:-1], _canonical_json_text(row[-1])))
        nodes.sort()

        edge_columns = _column_names(cx, "edges")
        if "meta_json" in edge_columns:
            edge_rows = cx.execute(
                "SELECT src,dst,kind,meta_json FROM edges"
            ).fetchall()
            edges = [
                (*row[:-1], _canonical_json_text(row[-1]))
                for row in edge_rows
            ]
        else:
            edges = [
                (*row, "{}")
                for row in cx.execute("SELECT src,dst,kind FROM edges")
            ]
        edges.sort()

        metadata = []
        if _table_exists(cx, "meta"):
            metadata = sorted(
                (str(key), _canonical_json_text(value))
                for key, value in cx.execute("SELECT k,v FROM meta")
            )
    finally:
        cx.close()
    h = hashlib.sha256()
    h.update(b"nodes\x00")
    for row in nodes:
        h.update(("\x1f".join("" if c is None else str(c) for c in row)).encode())
        h.update(b"\x1e")
    h.update(b"edges\x00")
    for row in edges:
        h.update(("\x1f".join("" if c is None else str(c) for c in row)).encode())
        h.update(b"\x1e")
    h.update(b"meta\x00")
    for row in metadata:
        h.update(("\x1f".join(row)).encode())
        h.update(b"\x1e")
    return h.hexdigest()


def check_hash(db, pin_path):
    """Recompute-and-compare (P2). Read the pinned content hash from pin_path,
    recompute content_hash(db), and return 0 iff they match (else 1, printing the
    mismatch). A missing pin is a hard fail -- the pin is the point."""
    if not os.path.exists(pin_path):
        print(f"graph check-hash: FAIL -- pin {pin_path} absent")
        return 1
    want = open(pin_path).read().strip()
    got = content_hash(db)
    if got == want:
        print(f"graph check-hash: OK -- content hash matches pin ({got[:16]}...)")
        return 0
    print(f"graph check-hash: FAIL -- content hash {got[:16]}... != pin "
          f"{want[:16]}... (graph content changed; re-pin with --write-hash if intended)")
    return 1


def write_hash(db, pin_path):
    """Write the current content hash to pin_path (deliberate re-pin)."""
    h = content_hash(db)
    with open(pin_path, "w") as f:
        f.write(h + "\n")
    print(f"graph write-hash: wrote {h[:16]}... -> {pin_path}")
    return 0


def _metadata_object(raw, context):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON metadata for {context}") from error
    if not isinstance(value, dict):
        raise ValueError(f"metadata for {context} must be an object")
    return value


def _graph_metadata(connection):
    if not _table_exists(connection, "meta"):
        return {}
    return {
        str(key): str(value)
        for key, value in connection.execute(
            "SELECT k,v FROM meta ORDER BY k"
        )
    }


def _input_hashes(metadata):
    raw_hashes = metadata.get("input_hashes")
    if raw_hashes is None:
        return {}
    try:
        value = json.loads(raw_hashes)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("graph input_hashes metadata is invalid") from error
    if not isinstance(value, dict):
        raise ValueError("graph input_hashes metadata must be an object")
    if any(not isinstance(key, str) or not isinstance(digest, str)
           for key, digest in value.items()):
        raise ValueError("graph input_hashes metadata must map strings to strings")
    return dict(sorted(value.items()))


def load(db):
    connection = sqlite3.connect(db)
    try:
        metadata = _graph_metadata(connection)
        raw_version = metadata.get(
            "schema_version", metadata.get("schema", "1")
        )
        try:
            schema_version = int(raw_version)
        except (TypeError, ValueError) as error:
            raise ValueError("graph schema version is invalid") from error
        if schema_version not in (1, 2):
            raise ValueError(f"unsupported graph schema version: {schema_version}")

        source_sha = metadata.get("source_sha", UNKNOWN_SOURCE_SHA)
        if schema_version == 2 and source_sha == UNKNOWN_SOURCE_SHA:
            raise ValueError("schema-2 graph is missing source_sha metadata")
        if schema_version == 2 and "input_hashes" not in metadata:
            raise ValueError(
                "schema-2 graph is missing input_hashes metadata"
            )
        input_hashes = _input_hashes(metadata)

        modules = {}
        functions = {}
        node_rows = connection.execute(
            "SELECT id,kind,path,qualname,span,sha256,lines,meta_json "
            "FROM nodes ORDER BY id"
        )
        for node_id, kind, path, qualname, span, sha256, lines, raw_meta in node_rows:
            record = {
                "id": node_id,
                "path": path,
                "qual": qualname,
                "span": span,
                "sha256": sha256,
                "lines": lines,
                "meta": _metadata_object(raw_meta, f"node {node_id}"),
            }
            if kind == "module":
                modules[node_id] = record
            elif kind == "function":
                functions[node_id] = record

        edge_columns = _column_names(connection, "edges")
        if "meta_json" in edge_columns:
            edge_rows = connection.execute(
                "SELECT src,dst,kind,meta_json FROM edges "
                "ORDER BY src,dst,kind,meta_json"
            )
        else:
            edge_rows = (
                (src, dst, kind, "{}")
                for src, dst, kind in connection.execute(
                    "SELECT src,dst,kind FROM edges ORDER BY src,dst,kind"
                )
            )

        calls = []
        contains = defaultdict(list)
        import_edges = defaultdict(list)
        for src, dst, kind, raw_meta in edge_rows:
            edge_meta = _metadata_object(
                raw_meta, f"edge {src} -> {dst} ({kind})"
            )
            if kind == "call":
                calls.append((src, dst, edge_meta))
            elif kind == "contains":
                contains[src].append(dst)
            elif kind == "imports":
                import_edges[src].append(dst)

        for module_id, module in modules.items():
            module["import_edges"] = tuple(sorted(import_edges[module_id]))
    finally:
        connection.close()

    return GraphInput(
        source_sha=source_sha,
        input_hashes=input_hashes,
        modules=modules,
        functions=functions,
        calls=tuple(calls),
        contains={
            module_id: tuple(sorted(function_ids))
            for module_id, function_ids in sorted(contains.items())
        },
    )


def _with_call_metadata(call_meta, record):
    conflicts = sorted(set(call_meta).intersection(record))
    if conflicts:
        raise ValueError(
            "call edge metadata conflicts with projection fields: "
            + ", ".join(conflicts)
        )
    return {**record, **call_meta}


def resolve_calls(fns, calls):
    """Best-effort call resolution with explicit confidence and provenance."""
    by_qualified = defaultdict(list)
    by_last = defaultdict(list)
    for fid, f in fns.items():
        qualified = f["qual"] or ""
        by_qualified[qualified].append(fid)
        by_qualified[fid].append(fid)
        last = qualified.split(".")[-1]
        by_last[last].append(fid)
    edges = []
    unresolved = []
    for call in calls:
        if len(call) == 2:
            src, name = call
            call_meta = {}
        else:
            src, name, call_meta = call
        call_meta = dict(call_meta)
        exact = sorted(set(by_qualified.get(name, [])))
        last = name.split(".")[-1]
        if len(exact) == 1:
            edges.append(_with_call_metadata(call_meta, {
                "from": src,
                "to": exact[0],
                "kind": "call",
                "reason": "exact_qualified",
                "confidence": 1.0,
            }))
            continue

        candidates = exact or sorted(set(by_last.get(last, [])))
        if not exact and len(candidates) == 1:
            edges.append(_with_call_metadata(call_meta, {
                "from": src,
                "to": candidates[0],
                "kind": "call",
                "reason": "unique_last_segment",
                "confidence": 0.6,
            }))
        elif len(candidates) == 0:
            unresolved.append(_with_call_metadata(call_meta, {
                "from": src,
                "name": name,
                "reason": "missing",
                "confidence": 0.0,
            }))
        else:
            unresolved.append(_with_call_metadata(call_meta, {
                "from": src,
                "name": name,
                "reason": "ambiguous",
                "candidates": candidates,
                "confidence": 0.0,
            }))
    return edges, unresolved


def _record_key(record):
    return json.dumps(
        record,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _annotated_fact(function_id, function, fact, **legacy):
    return {
        **fact,
        **legacy,
        "path": function["path"],
        "function": function_id,
        "source": dict(fact),
        "method": fact.get("method", "l0_static_analysis"),
        "confidence": fact.get("confidence", 1.0),
        "reason": "l0_mechanical_fact",
    }


def _security_projection(functions):
    auth_gates = []
    secret_sites = []
    sql_sites = []
    subprocess_sites = []
    path_sinks = []
    for function_id, function in sorted(functions.items()):
        meta = function["meta"]
        decorators = list(meta.get("decorators", []))
        lowered = [decorator.lower() for decorator in decorators]
        if any(token in decorator for decorator in lowered for token in AUTH_DECOS):
            auth_gates.append({
                "name": function["qual"],
                "at": f"{function['path']}:{function['span']}",
                "decorators": decorators,
                "path": function["path"],
                "function": function_id,
                "method": "name_substring",
                "confidence": 0.6,
                "reason": "auth_decorator_name",
                "source": {"decorators": decorators},
            })
        for auth_call in meta.get("auth_calls", []):
            if not isinstance(auth_call, dict):
                raise ValueError(f"auth fact for {function_id} must be an object")
            auth_gates.append(_annotated_fact(
                function_id,
                function,
                auth_call,
                name=function["qual"],
                at=f"{function['path']}:{auth_call.get('at')}",
                decorators=[],
                auth_call=auth_call.get("name"),
            ))
        for field in meta.get("secrets", []):
            secret_sites.append({
                "field": field,
                "op": "read",
                "at": f"{function['path']}:{function['span']}",
                "masked": None,
                "path": function["path"],
                "function": function_id,
                "method": "identifier_inventory",
                "confidence": 1.0,
                "reason": "secret_named_identifier",
                "source": {"field": field},
            })
        for sink in meta.get("sinks", []):
            if not isinstance(sink, dict):
                raise ValueError(f"sink fact for {function_id} must be an object")
            kind = sink.get("kind")
            at = f"{function['path']}:{sink.get('at')}"
            if kind in ("sql", "sql_fstring"):
                sql_sites.append(_annotated_fact(
                    function_id,
                    function,
                    sink,
                    at=at,
                    fstring=kind == "sql_fstring",
                    parametrized=None,
                ))
            elif kind == "subprocess":
                subprocess_sites.append(_annotated_fact(
                    function_id,
                    function,
                    sink,
                    at=at,
                    shell=sink.get("shell", False),
                ))
            elif kind == "path":
                path_sinks.append(_annotated_fact(
                    function_id,
                    function,
                    sink,
                    at=at,
                    allowlisted=None,
                ))

    auth_gates.sort(key=_record_key)
    secret_sites.sort(key=_record_key)
    sql_sites.sort(key=_record_key)
    subprocess_sites.sort(key=_record_key)
    path_sinks.sort(key=_record_key)
    return {
        "auth_gates": auth_gates,
        "secret_sites": secret_sites,
        "sql_sites": sql_sites,
        "subprocess_sites": subprocess_sites,
        "path_sinks": path_sinks,
        "totals": {
            "auth_gates": len(auth_gates),
            "secret_sites": len(secret_sites),
            "sql_sites": len(sql_sites),
            "sql_fstring": sum(site["fstring"] for site in sql_sites),
            "subprocess_sites": len(subprocess_sites),
            "shell_true": sum(
                bool(site["shell"]) for site in subprocess_sites
            ),
            "path_sinks": len(path_sinks),
        },
    }


def _error_projection(functions):
    handlers = []
    for function_id, function in sorted(functions.items()):
        raises = sorted({
            raised
            for raised in function["meta"].get("raises", [])
            if raised
        })
        if raises:
            handlers.append({
                "at": f"{function['path']}:{function['span']}",
                "fn": function["qual"],
                "raises": raises,
                "maps_to": None,
                "expected": None,
                "ok": None,
                "path": function["path"],
                "function": function_id,
                "provenance": {
                    "method": "l0_static_analysis",
                    "node_id": function_id,
                    "path": function["path"],
                },
            })
    return {"handlers": sorted(handlers, key=_record_key)}


def _taint_projection(functions):
    sources = []
    sinks = []
    paths = []
    for function_id, function in sorted(functions.items()):
        meta = function["meta"]
        source_facts = [
            *meta.get("sources", []),
            *meta.get("taint_sources", []),
        ]
        for source in source_facts:
            if not isinstance(source, dict):
                raise ValueError(
                    f"taint source for {function_id} must be an object"
                )
            sources.append(_annotated_fact(
                function_id,
                function,
                source,
                id=f"{function['path']}:{source.get('at')}",
                at=f"{function['path']}:{source.get('at')}",
                in_fn=function["qual"],
                source_path=function["path"],
                source_function=function_id,
            ))
        for sink in meta.get("sinks", []):
            if not isinstance(sink, dict):
                raise ValueError(f"sink fact for {function_id} must be an object")
            sinks.append(_annotated_fact(
                function_id,
                function,
                sink,
                id=f"{function['path']}:{sink.get('at')}",
                kind=sink.get("kind"),
                at=f"{function['path']}:{sink.get('at')}",
                in_fn=function["qual"],
                source_path=function["path"],
                source_function=function_id,
            ))
        for path_fact in meta.get("taint_paths", []):
            if not isinstance(path_fact, dict):
                raise ValueError(
                    f"taint path for {function_id} must be an object"
                )
            retained = {
                key: value
                for key, value in path_fact.items()
                if key not in TAINT_PATH_CONTROL_FIELDS
            }
            paths.append({
                **retained,
                "source": dict(path_fact),
                "source_path": function["path"],
                "source_function": function_id,
                "explicit": True,
                "method": "explicit_path_heuristic",
                "confidence": 0.6,
                "proof": False,
                "reason": "explicit_l0_path_evidence",
            })
    return {
        "sources": sorted(sources, key=_record_key),
        "sinks": sorted(sinks, key=_record_key),
        "paths": sorted(paths, key=_record_key),
        "note": (
            "sink inventory; source->sink paths are emitted only from explicit "
            "path evidence"
        ),
    }


def _dead_code_projection(functions, resolved):
    incoming = defaultdict(int)
    for edge in resolved:
        incoming[edge["to"]] += 1

    uncalled = []
    for function_id, function in sorted(functions.items()):
        qualified = function["qual"] or ""
        last = qualified.split(".")[-1]
        if last.startswith("__") or last == "main" or "." in qualified:
            continue
        if incoming.get(function_id, 0) == 0:
            uncalled.append({
                "fn": function_id,
                "confidence": 0.5,
                "note": (
                    "no resolved intra-repo caller "
                    "(may be route/CLI/dynamic)"
                ),
                "reason": "no_resolved_intra_repo_caller",
                "excluded_evidence": [
                    "dynamic_dispatch",
                    "framework_registration",
                    "route_or_cli_entrypoint",
                ],
                "path": function["path"],
                "qualname": qualified,
            })
    uncalled.sort(key=lambda record: record["fn"])
    return {
        "uncalled": uncalled[:2000],
        "uncalled_total": len(uncalled),
        "unreachable_routes": [],
    }


def _module_dependency_target(dependency, modules_by_path):
    dependency = str(dependency)
    normalized = dependency.replace(".", "/")
    package_initializer = f"{normalized}/__init__"
    candidates = []
    for path in modules_by_path:
        without_suffix = path[:-3] if path.endswith(".py") else path
        if (
            dependency == path
            or normalized == without_suffix
            or package_initializer == without_suffix
        ):
            candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def _module_projection(graph):
    modules_by_path = {
        module["path"]: (module_id, module)
        for module_id, module in graph.modules.items()
    }
    catalog = {}
    for path, (module_id, module) in sorted(modules_by_path.items()):
        function_ids = graph.contains.get(module_id, ())
        sink_kinds = sorted({
            sink.get("kind")
            for function_id in function_ids
            if function_id in graph.functions
            for sink in graph.functions[function_id]["meta"].get("sinks", [])
            if isinstance(sink, dict) and sink.get("kind")
        })
        secrets = sorted({
            field
            for function_id in function_ids
            if function_id in graph.functions
            for field in graph.functions[function_id]["meta"].get("secrets", [])
        })
        public_api = [{
            "name": graph.functions[function_id]["qual"],
            "signature": "(" + ", ".join(
                graph.functions[function_id]["meta"].get("args", [])
            ) + ")",
            "raises": sorted({
                raised
                for raised in graph.functions[function_id]["meta"].get(
                    "raises", []
                )
                if raised
            }),
        } for function_id in sorted(function_ids)
          if function_id in graph.functions
          and "." not in graph.functions[function_id]["qual"]
          and not graph.functions[function_id]["qual"].startswith("_")]
        dependencies = sorted({
            *module["meta"].get("imports", []),
            *module.get("import_edges", ()),
        })
        catalog[path] = {
            "purpose": None,
            "data_flow": None,
            "public_api": public_api,
            "invariants": [],
            "depends_on": dependencies,
            "depended_by": [],
            "sinks": sink_kinds,
            "secrets": secrets,
            "function_count": len(function_ids),
            "provenance": {
                "method": "l0_static_analysis",
                "node_id": module_id,
                "path": path,
                "sha256": module["sha256"],
            },
        }

    for dependent_path, module in catalog.items():
        for dependency in module["depends_on"]:
            target = _module_dependency_target(dependency, modules_by_path)
            if target is not None and dependent_path not in catalog[target]["depended_by"]:
                catalog[target]["depended_by"].append(dependent_path)
    for module in catalog.values():
        module["depended_by"].sort()
    return {"modules": catalog}


def _fact_object(value, context):
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be an object")
    return value


def _required_fact_string(fact, field, context):
    value = fact.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context} {field} must be a non-empty string")
    return value.strip()


def _heuristic_evidence(fact, context):
    method = fact.get("method")
    if method is None:
        return None, 0.0
    if not isinstance(method, str) or not method.strip():
        raise ValueError(f"{context} method must be a non-empty string or null")
    confidence = fact.get("confidence", 0.0)
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0.0 <= confidence <= 1.0
    ):
        raise ValueError(f"{context} confidence must be between 0.0 and 1.0")
    method = method.strip()
    confidence = float(confidence)
    if method == "name_substring" and confidence != 0.6:
        raise ValueError(
            f"{context} name_substring confidence must be 0.6"
        )
    return method, confidence


def _fact_line(fact, context):
    line = fact.get("at")
    if line is not None and (
        isinstance(line, bool) or not isinstance(line, int)
    ):
        raise ValueError(f"{context} at must be an integer or null")
    return line


def _site_fact(function_id, function, fact, context):
    source = {
        **dict(_fact_object(fact, context)),
        "path": function["path"],
        "function": function_id,
    }
    return {
        **source,
        "call_site": {
            "path": function["path"],
            "line": source.get("at"),
        },
        "path": function["path"],
        "function": function_id,
        "source": source,
    }


def _sorted_unique_records(records):
    by_key = {
        _record_key(record): record
        for record in records
    }
    return [by_key[key] for key in sorted(by_key)]


def build_config_lineage(graph):
    """Build deterministic config access lineage without inferring L2 meaning."""
    settings = defaultdict(
        lambda: {
            "readers": set(),
            "writers": set(),
            "read_sites": [],
            "write_sites": [],
        }
    )
    for function_id, function in sorted(graph.functions.items()):
        for access, target, sites in (
            ("read", "readers", "read_sites"),
            ("write", "writers", "write_sites"),
        ):
            for raw_fact in function["meta"].get(f"config_{access}s", []):
                fact = _fact_object(
                    raw_fact, f"config {access} fact for {function_id}"
                )
                key = fact.get("key")
                if not is_safe_config_key(key):
                    raise ValueError(
                        f"config {access} fact for {function_id} key must "
                        "use safe config-key grammar"
                    )
                assert isinstance(key, str)
                bounded_fact = {
                    "key": key,
                    "at": _fact_line(
                        fact, f"config {access} fact for {function_id}"
                    ),
                }
                settings[key][target].add(function_id)
                settings[key][sites].append(
                    _site_fact(
                        function_id,
                        function,
                        bounded_fact,
                        f"config {access} fact for {function_id}",
                    )
                )

    unknown_fields = {
        "effect": 0.0,
        "gui_exposure": 0.0,
        "runtime_tunable": 0.0,
    }
    return {
        "settings": {
            key: {
                "readers": sorted(value["readers"]),
                "writers": sorted(value["writers"]),
                "effect": None,
                "gui_exposure": None,
                "runtime_tunable": None,
                "confidence": 0.5,
                "field_confidence": dict(unknown_fields),
                "provenance": {
                    "method": "l0_static_analysis",
                    "read_sites": _sorted_unique_records(
                        value["read_sites"]
                    ),
                    "write_sites": _sorted_unique_records(
                        value["write_sites"]
                    ),
                    "unknown_fields": dict(unknown_fields),
                },
            }
            for key, value in sorted(settings.items())
        }
    }


def build_concurrency_map(graph):
    """Build a bounded heuristic concurrency inventory from L0 facts."""
    operations = []
    for function_id, function in sorted(graph.functions.items()):
        for raw_fact in function["meta"].get("concurrency_ops", []):
            context = f"concurrency fact for {function_id}"
            fact = _fact_object(raw_fact, context)
            method, confidence = _heuristic_evidence(fact, context)
            bounded_fact = {
                "kind": _required_fact_string(fact, "kind", context),
                "name": _required_fact_string(fact, "name", context),
                "operation": _required_fact_string(
                    fact, "operation", context
                ),
                "at": _fact_line(fact, context),
                "method": method,
                "confidence": confidence,
            }
            operation = _site_fact(
                function_id, function, bounded_fact, context
            )
            operation.update({
                "kind": bounded_fact["kind"],
                "name": bounded_fact["name"],
                "operation": bounded_fact["operation"],
                "method": method,
                "confidence": confidence,
            })
            operations.append(operation)
    operations.sort(key=_record_key)
    return {
        "shared_state": [],
        "locks": [
            dict(operation)
            for operation in operations
            if operation["kind"] == "lock"
        ],
        "operations": operations,
    }


def build_metrics_catalog(graph):
    """Build normalized metric emission sites from bounded L0 evidence."""
    metrics = []
    for function_id, function in sorted(graph.functions.items()):
        for raw_fact in function["meta"].get("metric_emits", []):
            context = f"metric fact for {function_id}"
            fact = _fact_object(raw_fact, context)
            method, confidence = _heuristic_evidence(fact, context)
            bounded_fact = {
                "name": _required_fact_string(fact, "name", context),
                "operation": _required_fact_string(
                    fact, "operation", context
                ),
                "at": _fact_line(fact, context),
                "method": method,
                "confidence": confidence,
            }
            metric = _site_fact(
                function_id, function, bounded_fact, context
            )
            metric.update({
                "name": bounded_fact["name"],
                "operation": bounded_fact["operation"],
                "containing_function": function_id,
                "method": method,
                "confidence": confidence,
            })
            metrics.append(metric)
    return {"metrics": sorted(metrics, key=_record_key)}


def _write_projections(outdir, artifacts):
    for name, _, artifact in artifacts:
        validate_projection(name, artifact)
    for name, filename, artifact in artifacts:
        atomic_write_json(
            Path(outdir) / filename,
            artifact,
            lambda value, projection_name=name: validate_projection(
                projection_name, value
            ),
        )


def build(db, outdir):
    graph = load(db)
    resolved, unresolved = resolve_calls(graph.functions, graph.calls)
    call_graph = {
        "nodes": sorted(graph.functions),
        "edges": sorted(resolved, key=_record_key),
        "unresolved": sorted(unresolved, key=_record_key),
        "unresolved_count": len(unresolved),
    }
    security = _security_projection(graph.functions)
    errors = _error_projection(graph.functions)
    taint = _taint_projection(graph.functions)
    dead_code = _dead_code_projection(graph.functions, resolved)
    modules = _module_projection(graph)
    config = build_config_lineage(graph)
    concurrency = build_concurrency_map(graph)
    metrics = build_metrics_catalog(graph)

    artifacts = [
        (
            "call_graph",
            "CALL_GRAPH.json",
            projection("call_graph", graph, call_graph),
        ),
        (
            "module_catalog",
            "MODULE_CATALOG.json",
            projection("module_catalog", graph, modules),
        ),
        (
            "security_surface",
            "SECURITY_SURFACE.json",
            projection("security_surface", graph, security),
        ),
        (
            "error_catalog",
            "ERROR_CATALOG.json",
            projection("error_catalog", graph, errors),
        ),
        (
            "taint_map",
            "TAINT_MAP.json",
            projection("taint_map", graph, taint),
        ),
        (
            "dead_code",
            "DEAD_CODE.json",
            projection("dead_code", graph, dead_code),
        ),
        (
            "config_lineage",
            "CONFIG_LINEAGE.json",
            projection("config_lineage", graph, config),
        ),
        (
            "concurrency_map",
            "CONCURRENCY_MAP.json",
            projection("concurrency_map", graph, concurrency),
        ),
        (
            "metrics_catalog",
            "METRICS_CATALOG.json",
            projection("metrics_catalog", graph, metrics),
        ),
    ]
    _write_projections(outdir, artifacts)

    return {
        "resolved_call_edges": len(resolved),
        "unresolved": len(unresolved),
        "auth_gates": len(security["auth_gates"]),
        "sql_fstring_sites": sum(
            site["fstring"] for site in security["sql_sites"]
        ),
        "subprocess_sites": len(security["subprocess_sites"]),
        "uncalled_fns": dead_code["uncalled_total"],
        "modules": len(modules["modules"]),
    }


def _reject_json_constant(value):
    raise ValueError(f"non-finite JSON constant: {value}")


def _json_object_without_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON member")
        value[key] = item
    return value


def _read_json_artifact(path):
    try:
        raw = Path(path).read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_json_object_without_duplicates,
        )
        json.dumps(value, allow_nan=False)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    return raw, value


def _copy_validated_artifact(raw, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)


def _projection_differences(generated, target, comparison_root):
    generated = Path(generated)
    target = Path(target)
    desired_shadow = Path(comparison_root) / "desired"
    target_shadow = Path(comparison_root) / "target"
    malformed = set()

    for filename, schema_name in PROJECTION_SCHEMAS.items():
        desired_path = generated / filename
        loaded = _read_json_artifact(desired_path)
        if loaded is None:
            malformed.add(filename)
        else:
            raw, value = loaded
            try:
                validate_projection(schema_name, value)
            except (TypeError, ValueError):
                malformed.add(filename)
            else:
                _copy_validated_artifact(raw, desired_shadow / filename)

        target_path = target / filename
        if not target_path.is_file():
            continue
        loaded = _read_json_artifact(target_path)
        if loaded is None:
            malformed.add(filename)
            continue
        raw, value = loaded
        try:
            validate_projection(schema_name, value)
        except (TypeError, ValueError):
            malformed.add(filename)
            continue
        _copy_validated_artifact(raw, target_shadow / filename)

    if target.is_dir():
        for path in sorted(target.rglob("*.json")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(target).as_posix()
            if relative_path in PROJECTION_SCHEMAS:
                continue
            loaded = _read_json_artifact(path)
            if loaded is None:
                malformed.add(relative_path)
                continue
            raw, _ = loaded
            _copy_validated_artifact(raw, target_shadow / relative_path)

    by_path = {
        difference.path: difference.state
        for difference in _compare_artifact_dirs(
            desired_shadow,
            target_shadow,
        )
    }
    by_path.update({path: "malformed" for path in malformed})
    return tuple(sorted(by_path.items()))


class _NoSafeCheckTemporaryRoot(RuntimeError):
    """No writable temporary root exists outside the compared target tree."""


def _check_temporary_root_candidates(db):
    raw_candidates = []
    try:
        raw_candidates.append(tempfile.gettempdir())
    except OSError:
        pass
    for variable in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(variable)
        if value:
            raw_candidates.append(value)
    raw_candidates.append(Path(db).expanduser().resolve().parent)
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            raw_candidates.append(Path(local_app_data) / "Temp")
        system_root = os.environ.get("SystemRoot")
        if system_root:
            raw_candidates.append(Path(system_root) / "Temp")
    else:
        raw_candidates.extend(("/tmp", "/var/tmp", "/usr/tmp"))
    raw_candidates.append(Path.cwd())

    seen = set()
    for raw_candidate in raw_candidates:
        try:
            candidate = Path(raw_candidate).expanduser().resolve()
            if candidate in seen or not candidate.is_dir():
                continue
        except (OSError, RuntimeError):
            continue
        seen.add(candidate)
        yield candidate


def _new_check_temporary_directory(db, target):
    for candidate in _check_temporary_root_candidates(db):
        if candidate.is_relative_to(target):
            continue
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="bd-graph-check-",
                dir=candidate,
            )
        except OSError:
            continue
        try:
            scratch = Path(temporary.name).resolve()
        except (OSError, RuntimeError):
            temporary.cleanup()
            continue
        if scratch.is_relative_to(target):
            temporary.cleanup()
            continue
        return temporary
    raise _NoSafeCheckTemporaryRoot


def check_projections(db, outdir):
    """Regenerate projections in isolation and report durable artifact drift."""
    target = Path(outdir).expanduser().resolve()
    try:
        temporary = _new_check_temporary_directory(db, target)
    except _NoSafeCheckTemporaryRoot:
        print("graph check: ERROR -- no safe temporary directory available")
        return 1
    with temporary as scratch:
        scratch_path = Path(scratch)
        generated = scratch_path / "generated"
        build(db, generated)
        differences = _projection_differences(
            generated,
            target,
            scratch_path / "comparison",
        )
    for path, state in differences:
        print(f"{path}: {state}")
    return 1 if differences else 0


def _default_root():
    repository_root = Path(__file__).resolve().parent.parent
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    try:
        from tools.code_intelligence.paths import discover_repo_root

        return discover_repo_root(Path.cwd())
    except (ModuleNotFoundError, OSError, ValueError):
        return repository_root


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="repository root (default: discovered Git repository root)",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database path (default: <root>/artifacts/KNOWLEDGE_GRAPH.db)",
    )
    ap.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="projection directory (default: <root>/artifacts)",
    )
    ap.add_argument(
        "--hash-pin",
        type=Path,
        default=None,
        help=(
            "content-hash pin file for --check-hash/--write-hash "
            "(default: <database>.sha256)"
        ),
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="regenerate in temporary storage and report projection drift",
    )
    ap.add_argument("--check-hash", action="store_true",
                    help="recompute content hash and compare to --hash-pin (P2 gate); rc!=0 on drift")
    ap.add_argument("--write-hash", action="store_true",
                    help="write the current content hash to --hash-pin (deliberate re-pin)")
    a = ap.parse_args(argv)
    projection_mode = not (a.check_hash or a.write_hash)
    root = None
    if a.root is not None:
        root = a.root.expanduser().resolve()
    elif a.db is None or (projection_mode and a.outdir is None):
        root = _default_root()
    database = (
        a.db.expanduser().resolve()
        if a.db is not None
        else root / "artifacts" / "KNOWLEDGE_GRAPH.db"
    )
    hash_pin = (
        a.hash_pin.expanduser().resolve()
        if a.hash_pin is not None
        else database.with_suffix(database.suffix + ".sha256")
    )
    outdir = (
        a.outdir.expanduser().resolve()
        if a.outdir is not None
        else root / "artifacts" if projection_mode else None
    )
    if a.check_hash:
        return check_hash(database, hash_pin)
    if a.write_hash:
        return write_hash(database, hash_pin)
    if a.check:
        assert outdir is not None
        return check_projections(database, outdir)
    assert outdir is not None
    stats = build(database, outdir)
    print("graph_build:", json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
