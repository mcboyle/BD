#!/usr/bin/env python3
"""Build the deterministic L0 code-intelligence SQLite graph.

Python is parsed with the standard-library AST.  TypeScript and TSX retain the
existing grep-level export/fetch/secret-name inventory.  The SQLite schema is
kept compatible with schema-1 readers while function metadata and graph
provenance are emitted at schema version 2.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Sequence


SCHEMA = 2
TOOL_VERSION = "2"

PROD = (
    ("bulk_downloader", (".py",)),
    ("tools", (".py",)),
    ("frontend/src", (".ts", ".tsx")),
)

SECRET_RE = re.compile(
    r"(password|passwd|secret|token|cookie|api[_-]?key|authorization|bearer|"
    r"private[_-]?key|signing|otp|credential|session[_-]?key|access[_-]?token)",
    re.I,
)
SUBPROCESS_NAMES = {
    "run",
    "Popen",
    "call",
    "check_output",
    "check_call",
    "getoutput",
    "getstatusoutput",
}
FETCH_NAMES = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "request",
    "urlopen",
    "fetch",
}
PATH_SINK_NAMES = {"join", "abspath", "realpath", "open", "makedirs", "mkdir"}
REDACT_RE = re.compile(r"(redact|scrub|mask|sanitiz|_is_secret|_mask)", re.I)
AUTH_RE = re.compile(
    r"(auth|login|logout|permission|csrf|admin|authorize|authenticate|require)",
    re.I,
)
CONFIG_ROOTS = {"config", "settings", "environ"}
CONFIG_READ_METHODS = {"get", "getenv"}
CONFIG_WRITE_METHODS = {"set", "update"}
LOCK_WORDS = ("lock", "mutex", "semaphore")
METRIC_OWNERS = (
    "metric",
    "metrics",
    "stats",
    "statsd",
    "counter",
    "histogram",
    "gauge",
    "telemetry",
)
METRIC_METHODS = {
    "increment",
    "incr",
    "decrement",
    "decr",
    "observe",
    "record",
    "timing",
    "emit_metric",
}
NAME_RE = re.compile(r"[A-Za-z0-9_.:/-]{1,200}")
HEURISTIC_METHOD = "name_substring"
HEURISTIC_CONFIDENCE = 0.6


def sloc(path: str | os.PathLike[str]) -> int:
    """Count non-blank source lines without failing the extraction."""
    count = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as source:
            for line in source:
                if line.strip():
                    count += 1
    except OSError:
        pass
    return count


def _repository_relative_path(
    path: str | os.PathLike[str],
    root: str | os.PathLike[str],
    *,
    path_module=os.path,
) -> str:
    """Return a Git-style relative path without altering POSIX backslashes."""
    relative_path = path_module.relpath(os.fspath(path), os.fspath(root))
    if path_module.sep == "\\":
        return relative_path.replace("\\", "/")
    return relative_path


def prod_files(root: str | os.PathLike[str]) -> list[str]:
    """Return the sorted production inputs relative to *root*."""
    root_path = os.fspath(root)
    output: list[str] = []
    for base, extensions in PROD:
        directory = os.path.join(root_path, base)
        for current, _, names in os.walk(directory):
            if "node_modules" in current or "__pycache__" in current:
                continue
            for name in names:
                if name.endswith(extensions):
                    output.append(
                        _repository_relative_path(
                            os.path.join(current, name), root_path
                        )
                    )
    return sorted(output)


def _expr(node: ast.AST | None) -> str | None:
    """Return a deterministic expression without retaining source bodies."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return json.dumps(node.value, ensure_ascii=False)
    return ast.unparse(node)


def _literal_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return None


class _AnnotationRedactor(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        text = _literal_text(node.value)
        if text is None or SECRET_RE.search(text) is None:
            return node
        replacement: str | bytes = "<redacted>"
        if isinstance(node.value, bytes):
            replacement = b"<redacted>"
        return ast.copy_location(ast.Constant(value=replacement), node)


def _redacted_annotation_tree(node: ast.AST) -> ast.AST:
    clone = copy.deepcopy(node)
    redacted = _AnnotationRedactor().visit(clone)
    ast.fix_missing_locations(redacted)
    return redacted


def _annotation_expr(node: ast.AST | None) -> str | None:
    """Normalize annotation structure without retaining sensitive literals."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            forward = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            value = (
                "<redacted>"
                if SECRET_RE.search(node.value)
                else node.value
            )
        else:
            value = ast.unparse(_redacted_annotation_tree(forward))
        return json.dumps(value, ensure_ascii=False)
    return ast.unparse(_redacted_annotation_tree(node))


def _parameter_default(argument: ast.arg, default: ast.AST | None) -> str | None:
    if default is None:
        return None
    secret_named = SECRET_RE.search(argument.arg) is not None
    sensitive_literal = False
    for item in ast.walk(default):
        if not isinstance(item, ast.Constant):
            continue
        text = _literal_text(item.value)
        if text is not None and (secret_named or SECRET_RE.search(text)):
            sensitive_literal = True
            break
    if sensitive_literal:
        return "<redacted>"
    return _expr(default)


def _parameter_records(arguments: ast.arguments) -> list[dict[str, object]]:
    ordered: list[dict[str, object]] = []
    positional = list(arguments.posonlyargs) + list(arguments.args)
    defaults: list[ast.AST | None] = [
        None
    ] * (len(positional) - len(arguments.defaults)) + list(arguments.defaults)
    positional_only_count = len(arguments.posonlyargs)
    for index, (argument, default) in enumerate(zip(positional, defaults)):
        kind = (
            "positional_only"
            if index < positional_only_count
            else "positional_or_keyword"
        )
        ordered.append({
            "name": argument.arg,
            "kind": kind,
            "default": _parameter_default(argument, default),
            "annotation": _annotation_expr(argument.annotation),
        })
    if arguments.vararg is not None:
        ordered.append({
            "name": arguments.vararg.arg,
            "kind": "var_positional",
            "default": None,
            "annotation": _annotation_expr(arguments.vararg.annotation),
        })
    for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        ordered.append({
            "name": argument.arg,
            "kind": "keyword_only",
            "default": _parameter_default(argument, default),
            "annotation": _annotation_expr(argument.annotation),
        })
    if arguments.kwarg is not None:
        ordered.append({
            "name": arguments.kwarg.arg,
            "kind": "var_keyword",
            "default": None,
            "annotation": _annotation_expr(arguments.kwarg.annotation),
        })
    return ordered


def _name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _call_name(node: ast.Call) -> str:
    return _name(node.func)


def _decorator_name(node: ast.AST) -> str:
    return _name(node.func if isinstance(node, ast.Call) else node)


def _is_true(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _literal_name(node: ast.AST | None) -> str | None:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    candidate = node.value.strip()
    return candidate if NAME_RE.fullmatch(candidate) else None


def _call_key(node: ast.Call) -> str | None:
    return _literal_name(node.args[0]) if node.args else None


def _is_config_target(node: ast.AST) -> bool:
    name = _name(node).lower()
    return bool(name) and name.rsplit(".", 1)[-1] in CONFIG_ROOTS


def _return_shape(value: ast.AST) -> str | None:
    if isinstance(value, ast.Constant) and value.value is None:
        return None
    if isinstance(value, ast.Dict):
        return "dict"
    if isinstance(value, ast.List):
        return "list"
    if isinstance(value, ast.Tuple):
        return "tuple"
    if isinstance(value, ast.Set):
        return "set"
    if isinstance(value, ast.Call):
        return "call"
    if isinstance(value, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return "comprehension"
    if isinstance(value, ast.Await):
        return "await"
    if isinstance(value, (ast.Yield, ast.YieldFrom)):
        return "yield"
    if isinstance(value, ast.Name):
        return "name"
    if isinstance(value, ast.Attribute):
        return "attribute"
    if isinstance(value, ast.Constant):
        return "constant"
    return type(value).__name__.lower()


class _LocalBodyVisitor(ast.NodeVisitor):
    """Collect a function body without leaking facts from nested scopes."""

    def __init__(self) -> None:
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST) -> None:
        self.nodes.append(node)
        super().generic_visit(node)

    def _visit_function_declaration(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        self.nodes.append(node)
        for expression in node.decorator_list:
            self.visit(expression)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if (
            node.args.vararg is not None
            and node.args.vararg.annotation is not None
        ):
            self.visit(node.args.vararg.annotation)
        if (
            node.args.kwarg is not None
            and node.args.kwarg.annotation is not None
        ):
            self.visit(node.args.kwarg.annotation)
        for expression in (
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ):
            self.visit(expression)
        if node.returns is not None:
            self.visit(node.returns)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_declaration(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_declaration(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.nodes.append(node)
        for expression in (
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ):
            self.visit(expression)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.nodes.append(node)
        for expression in node.decorator_list:
            self.visit(expression)
        for expression in node.bases:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)


def _local_nodes(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    visitor = _LocalBodyVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.nodes


def _dedupe_records(
    records: Iterable[dict[str, object]],
    fields: tuple[str, ...],
) -> list[dict[str, object]]:
    seen: set[tuple[object, ...]] = set()
    result: list[dict[str, object]] = []
    for record in sorted(
        records,
        key=lambda item: tuple(str(item.get(field, "")) for field in fields),
    ):
        identity = tuple(record.get(field) for field in fields)
        if identity not in seen:
            seen.add(identity)
            result.append(record)
    return result


def _config_facts(
    nodes: Iterable[ast.AST],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    reads: list[dict[str, object]] = []
    writes: list[dict[str, object]] = []
    for node in nodes:
        if (
            isinstance(node, ast.AugAssign)
            and isinstance(node.target, ast.Subscript)
            and _is_config_target(node.target.value)
        ):
            key = _literal_name(node.target.slice)
            if key is not None:
                record = {"key": key, "at": node.lineno}
                reads.append(record)
                writes.append(record)
        if isinstance(node, ast.Subscript) and _is_config_target(node.value):
            key = _literal_name(node.slice)
            if key is None:
                continue
            record = {"key": key, "at": node.lineno}
            if isinstance(node.ctx, ast.Load):
                reads.append(record)
            elif isinstance(node.ctx, (ast.Store, ast.Del)):
                writes.append(record)
            continue
        if not isinstance(node, ast.Call) or not isinstance(
            node.func, ast.Attribute
        ):
            continue
        owner = node.func.value
        method = node.func.attr.lower()
        full_name = _name(node.func).lower()
        is_environment_get = full_name == "os.getenv"
        if not (_is_config_target(owner) or is_environment_get):
            continue
        if method in CONFIG_READ_METHODS or is_environment_get:
            key = _call_key(node)
            if key is not None:
                reads.append({"key": key, "at": node.lineno})
        elif method in CONFIG_WRITE_METHODS:
            if method == "update":
                if node.args and isinstance(node.args[0], ast.Dict):
                    for key_node in node.args[0].keys:
                        key = _literal_name(key_node)
                        if key is not None:
                            writes.append({"key": key, "at": node.lineno})
                for keyword in node.keywords:
                    if keyword.arg is not None:
                        writes.append({"key": keyword.arg, "at": node.lineno})
            else:
                key = _call_key(node)
                if key is not None:
                    writes.append({"key": key, "at": node.lineno})
        elif method == "setdefault":
            key = _call_key(node)
            if key is not None:
                reads.append({"key": key, "at": node.lineno})
                writes.append({"key": key, "at": node.lineno})
    sort_fields = ("at", "key")
    return (
        _dedupe_records(reads, sort_fields),
        _dedupe_records(writes, sort_fields),
    )


def _context_name(node: ast.AST) -> str:
    return _name(node.func) if isinstance(node, ast.Call) else _name(node)


def _concurrency_facts(nodes: Iterable[ast.AST]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for node in nodes:
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                name = _context_name(item.context_expr)
                lowered = name.lower()
                if name and any(word in lowered for word in LOCK_WORDS):
                    records.append({
                        "kind": "lock",
                        "name": name,
                        "operation": "context",
                        "at": node.lineno,
                        "method": HEURISTIC_METHOD,
                        "confidence": HEURISTIC_CONFIDENCE,
                    })
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if not name:
            continue
        lowered = name.lower()
        last = lowered.rsplit(".", 1)[-1]
        kind: str | None = None
        if any(word in lowered for word in LOCK_WORDS) or (
            last in {"acquire", "release"} and "lock" in lowered
        ):
            kind = "lock"
        elif "thread" in lowered:
            kind = "thread"
        elif "process" in lowered or "multiprocessing" in lowered:
            kind = "process"
        elif (
            "asyncio" in lowered
            or last in {"create_task", "gather", "run_in_executor"}
        ):
            kind = "async"
        elif "queue" in lowered:
            kind = "queue"
        elif "scheduler" in lowered or "schedule" in lowered:
            kind = "scheduler"
        if kind is None:
            continue
        operation = last
        if kind in {"thread", "process"} and last in {"thread", "process"}:
            operation = "create"
        records.append({
            "kind": kind,
            "name": name,
            "operation": operation,
            "at": node.lineno,
            "method": HEURISTIC_METHOD,
            "confidence": HEURISTIC_CONFIDENCE,
        })
    return _dedupe_records(records, ("at", "kind", "name", "operation"))


def _metric_facts(nodes: Iterable[ast.AST]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node)
        if not call_name:
            continue
        lowered = call_name.lower()
        operation = lowered.rsplit(".", 1)[-1]
        if (
            operation not in METRIC_METHODS
            and not any(owner in lowered for owner in METRIC_OWNERS)
        ):
            continue
        literal = _call_key(node)
        owner = call_name.rsplit(".", 1)[0] if "." in call_name else call_name
        name = literal or owner
        records.append({
            "name": name,
            "operation": operation,
            "at": node.lineno,
            "method": HEURISTIC_METHOD,
            "confidence": HEURISTIC_CONFIDENCE,
        })
    return _dedupe_records(records, ("at", "name", "operation"))


def _secret_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    nodes: Iterable[ast.AST],
    config_reads: Iterable[dict[str, object]],
    config_writes: Iterable[dict[str, object]],
) -> list[str]:
    names = {
        parameter["name"]
        for parameter in _parameter_records(node.args)
        if SECRET_RE.search(str(parameter["name"]))
    }
    for record in (*tuple(config_reads), *tuple(config_writes)):
        key = str(record["key"])
        if SECRET_RE.search(key):
            names.add(key)
    for item in nodes:
        if isinstance(item, ast.Subscript):
            key = _literal_name(item.slice)
            if key is not None and SECRET_RE.search(key):
                names.add(key)
        elif isinstance(item, ast.Attribute) and SECRET_RE.search(item.attr):
            names.add(item.attr)
    return sorted(names)


def _sink_facts(nodes: Iterable[ast.AST]) -> tuple[
    list[dict[str, object]], list[dict[str, object]]
]:
    sinks: list[dict[str, object]] = []
    flags: list[dict[str, object]] = []
    for node in nodes:
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if not name:
                continue
            last = name.rsplit(".", 1)[-1]
            lowered = name.lower()
            if last in SUBPROCESS_NAMES:
                shell = any(
                    keyword.arg == "shell" and _is_true(keyword.value)
                    for keyword in node.keywords
                    if isinstance(keyword, ast.keyword)
                )
                sinks.append({
                    "kind": "subprocess",
                    "at": node.lineno,
                    "shell": shell,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                })
            elif last in FETCH_NAMES and (
                "request" in lowered
                or "urlopen" in lowered
                or "fetch" in lowered
                or "session" in lowered
            ):
                sinks.append({
                    "kind": "fetch",
                    "at": node.lineno,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                })
            elif last in PATH_SINK_NAMES and (
                "path" in lowered or "os." in lowered or last == "open"
            ):
                sinks.append({
                    "kind": "path",
                    "at": node.lineno,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                })
            elif REDACT_RE.search(name):
                sinks.append({
                    "kind": "redaction",
                    "at": node.lineno,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                })
            if last == "get_json" and any(
                keyword.arg == "silent" and _is_true(keyword.value)
                for keyword in node.keywords
                if isinstance(keyword, ast.keyword)
            ):
                flags.append({"f": "get_json_silent", "at": node.lineno})
            if last == "float":
                flags.append({"f": "float_coerce", "at": node.lineno})
            if last in {"execute", "executescript"} or "cursor" in lowered:
                sinks.append({
                    "kind": "sql",
                    "at": node.lineno,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                })
        elif isinstance(node, ast.JoinedStr):
            static_text = "".join(
                part.value
                for part in node.values
                if isinstance(part, ast.Constant)
                and isinstance(part.value, str)
            )
            if re.search(
                r"\b(FROM|INTO|UPDATE|TABLE)\b", static_text, re.I
            ) and any(isinstance(part, ast.FormattedValue) for part in node.values):
                sinks.append({
                    "kind": "sql_fstring",
                    "at": node.lineno,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                })
    return (
        _dedupe_records(sinks, ("at", "kind", "shell")),
        _dedupe_records(flags, ("at", "f")),
    )


class PyVisitor(ast.NodeVisitor):
    """Collect deterministic module and function facts."""

    def __init__(self, relpath: str, source: str):
        self.rel = relpath
        self.source = source
        self.scope: list[str] = []
        self.fns: list[dict[str, object]] = []
        self.imports: list[str] = []

    def _qual(self, name: str) -> str:
        return ".".join([*self.scope, name]) if self.scope else name

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module.split(".")[0])
        self.generic_visit(node)

    def _handle_fn(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        local_nodes = _local_nodes(node)
        parameters = _parameter_records(node.args)
        calls = [
            {"name": _call_name(item), "at": item.lineno}
            for item in local_nodes
            if isinstance(item, ast.Call) and _call_name(item)
        ]
        calls.sort(key=lambda item: (int(item["at"]), str(item["name"])))
        returns = [
            item for item in local_nodes if isinstance(item, ast.Return)
        ]
        raises = sorted({
            name
            for item in local_nodes
            if isinstance(item, ast.Raise) and item.exc is not None
            for name in [
                _name(item.exc.func)
                if isinstance(item.exc, ast.Call)
                else _name(item.exc)
            ]
            if name
        })
        config_reads, config_writes = _config_facts(local_nodes)
        sinks, flags = _sink_facts(local_nodes)
        facts: dict[str, object] = {
            "qualname": self._qual(node.name),
            "lineno": node.lineno,
            "end_lineno": getattr(node, "end_lineno", node.lineno),
            "parameters": parameters,
            "args": [str(parameter["name"]) for parameter in parameters],
            "has_kwargs": node.args.kwarg is not None,
            "returns": {
                "annotation": _annotation_expr(node.returns),
                "has_value": any(
                    item.value is not None
                    and not (
                        isinstance(item.value, ast.Constant)
                        and item.value.value is None
                    )
                    for item in returns
                ),
                "has_none": any(
                    isinstance(item.value, ast.Constant)
                    and item.value.value is None
                    for item in returns
                ),
                "has_bare": any(item.value is None for item in returns),
                "shapes": sorted({
                    shape
                    for item in returns
                    if item.value is not None
                    for shape in [_return_shape(item.value)]
                    if shape is not None
                }),
            },
            "decorators": sorted(filter(
                None, (_decorator_name(item) for item in node.decorator_list)
            )),
            "auth_calls": [
                {
                    **call,
                    "method": HEURISTIC_METHOD,
                    "confidence": HEURISTIC_CONFIDENCE,
                }
                for call in calls
                if AUTH_RE.search(str(call["name"]))
            ],
            "calls": calls,
            "raises": raises,
            "sinks": sinks,
            "secrets": _secret_names(
                node, local_nodes, config_reads, config_writes
            ),
            "flags": flags,
            "config_reads": config_reads,
            "config_writes": config_writes,
            "concurrency_ops": _concurrency_facts(local_nodes),
            "metric_emits": _metric_facts(local_nodes),
        }
        self.fns.append(facts)
        self.scope.append(node.name)
        for child in node.body:
            self.visit(child)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_fn(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _without_ts_strings(source: str) -> str:
    return re.sub(
        r"(?s)(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|"
        r"`(?:\\.|[^`\\])*`|//[^\n]*|/\*.*?\*/)",
        " ",
        source,
    )


def ts_facts(relpath: str, source: str) -> dict[str, object]:
    """Return the compatible grep-level TS/TSX facts without secret values."""
    del relpath
    exports = re.findall(r"export\s+(?:async\s+)?function\s+(\w+)", source)
    exports += re.findall(r"export\s+const\s+(\w+)\s*=", source)
    fetches = len(re.findall(r"\bfetch\(", source))
    code_only = _without_ts_strings(source)
    identifiers = set(re.findall(r"\b[A-Za-z_$][A-Za-z0-9_$]*\b", code_only))
    secrets = sorted(name for name in identifiers if SECRET_RE.search(name))
    return {
        "exports": sorted(set(exports)),
        "fetch_calls": fetches,
        "secrets": secrets[:10],
    }


@dataclass(frozen=True)
class _FallbackFileFact:
    path: str
    sha256: str


def _fallback_snapshot(root: Path, files: Sequence[str]) -> SimpleNamespace:
    facts: list[_FallbackFileFact] = []
    for relative_path in files:
        raw = (root / relative_path).read_bytes()
        facts.append(
            _FallbackFileFact(relative_path, hashlib.sha256(raw).hexdigest())
        )
    digest = hashlib.sha256()
    for fact in facts:
        digest.update(
            f"{fact.path}\0{fact.sha256}\n".encode(
                "utf-8", "surrogateescape"
            )
        )
    return SimpleNamespace(
        source_sha=digest.hexdigest(),
        files=tuple(facts),
        source_binding="legacy_non_git_fallback",
    )


def _is_git_worktree(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as error:
        raise RuntimeError("cannot determine Git worktree state") from error
    return result.returncode == 0 and result.stdout.strip() == "true"


def _foundation_build_snapshot(root: Path):
    candidates = (root, Path(__file__).resolve().parent.parent)
    for candidate in reversed(candidates):
        package = candidate / "tools" / "code_intelligence" / "snapshot.py"
        candidate_text = str(candidate)
        if package.is_file() and candidate_text not in sys.path:
            sys.path.insert(0, candidate_text)
    importlib.invalidate_caches()
    module = importlib.import_module("tools.code_intelligence.snapshot")
    return module.build_snapshot


def _tracked_snapshot(root: Path, files: Sequence[str]) -> object:
    """Use tracked provenance for Git and a marked fallback only otherwise."""
    if not _is_git_worktree(root):
        return _fallback_snapshot(root, files)
    try:
        build_snapshot = _foundation_build_snapshot(root)
    except ImportError as error:
        raise RuntimeError(
            "foundation snapshot is unavailable for Git worktree"
        ) from error
    snapshot = build_snapshot(root)
    return SimpleNamespace(
        source_sha=snapshot.source_sha,
        files=snapshot.files,
        source_binding="tracked_tree",
    )


def _snapshot_hashes(snapshot: object) -> dict[str, str]:
    return {fact.path: fact.sha256 for fact in snapshot.files}


def _validate_snapshot_inputs(
    snapshot: object, files: Sequence[str]
) -> dict[str, str]:
    hashes = _snapshot_hashes(snapshot)
    missing = sorted(set(files) - set(hashes))
    if missing:
        if snapshot.source_binding == "tracked_tree":
            raise RuntimeError(
                f"untracked production input: {missing[0]}"
            )
        raise RuntimeError(
            f"production input missing from snapshot: {missing[0]}"
        )
    return hashes


def _insert_meta(
    connection: sqlite3.Connection,
    key: str,
    value: str | int,
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, str(value))
    )


def _populate_database(
    root: Path,
    database: Path,
    files: Sequence[str],
    snapshot: object,
) -> dict[str, int]:
    snapshot_hashes = _validate_snapshot_inputs(snapshot, files)
    connection = sqlite3.connect(database)
    try:
        connection.executescript("""
          CREATE TABLE nodes(
            id TEXT PRIMARY KEY,
            kind TEXT,
            path TEXT,
            qualname TEXT,
            span TEXT,
            sha256 TEXT,
            lines INTEGER,
            meta_json TEXT
          );
          CREATE TABLE edges(src TEXT, dst TEXT, kind TEXT, meta_json TEXT);
          CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
          CREATE INDEX ix_nodes_path ON nodes(path);
          CREATE INDEX ix_edges_kind ON edges(kind);
        """)
        module_count = function_count = edge_count = parse_errors = 0
        for relative_path in files:
            absolute_path = root / relative_path
            try:
                raw = absolute_path.read_bytes()
            except OSError as error:
                raise RuntimeError(
                    f"production input changed after snapshot: {relative_path}"
                ) from error
            sha256 = hashlib.sha256(raw).hexdigest()
            if sha256 != snapshot_hashes[relative_path]:
                raise RuntimeError(
                    f"production input changed after snapshot: {relative_path}"
                )
            source = raw.decode("utf-8", "replace")
            lines = sum(1 for line in source.splitlines() if line.strip())
            if relative_path.endswith(".py"):
                try:
                    tree = ast.parse(source, filename=relative_path)
                except SyntaxError as error:
                    parse_errors += 1
                    connection.execute(
                        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                        (
                            relative_path,
                            "module",
                            relative_path,
                            relative_path,
                            "",
                            sha256,
                            lines,
                            json.dumps(
                                {"parse_error": str(error)}, sort_keys=True
                            ),
                        ),
                    )
                    module_count += 1
                    continue
                visitor = PyVisitor(relative_path, source)
                visitor.visit(tree)
                connection.execute(
                    "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                    (
                        relative_path,
                        "module",
                        relative_path,
                        relative_path,
                        "",
                        sha256,
                        lines,
                        json.dumps(
                            {"imports": sorted(set(visitor.imports))},
                            sort_keys=True,
                        ),
                    ),
                )
                module_count += 1
                seen_function_ids: set[str] = set()
                for facts in visitor.fns:
                    base_id = (
                        f"{relative_path}::{facts['qualname']}"
                    )
                    function_id = base_id
                    if function_id in seen_function_ids:
                        function_id = f"{base_id}#{facts['lineno']}"
                    seen_function_ids.add(function_id)
                    calls = list(facts["calls"])
                    facts["unresolved_calls"] = [
                        {
                            "from": function_id,
                            "name": call["name"],
                            "at": call["at"],
                        }
                        for call in calls
                    ]
                    metadata_fields = (
                        "args",
                        "has_kwargs",
                        "parameters",
                        "returns",
                        "decorators",
                        "auth_calls",
                        "calls",
                        "unresolved_calls",
                        "raises",
                        "sinks",
                        "secrets",
                        "flags",
                        "config_reads",
                        "config_writes",
                        "concurrency_ops",
                        "metric_emits",
                    )
                    connection.execute(
                        "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                        (
                            function_id,
                            "function",
                            relative_path,
                            facts["qualname"],
                            f"{facts['lineno']}-{facts['end_lineno']}",
                            "",
                            int(facts["end_lineno"])
                            - int(facts["lineno"])
                            + 1,
                            json.dumps(
                                {
                                    key: facts[key]
                                    for key in metadata_fields
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
                    function_count += 1
                    connection.execute(
                        "INSERT INTO edges VALUES(?,?,?,?)",
                        (relative_path, function_id, "contains", "{}"),
                    )
                    edge_count += 1
                    for call in calls:
                        connection.execute(
                            "INSERT INTO edges VALUES(?,?,?,?)",
                            (
                                function_id,
                                call["name"],
                                "call",
                                json.dumps({"at": call["at"]}, sort_keys=True),
                            ),
                        )
                        edge_count += 1
                for imported in sorted(set(visitor.imports)):
                    connection.execute(
                        "INSERT INTO edges VALUES(?,?,?,?)",
                        (relative_path, imported, "imports", "{}"),
                    )
                    edge_count += 1
            else:
                connection.execute(
                    "INSERT INTO nodes VALUES(?,?,?,?,?,?,?,?)",
                    (
                        relative_path,
                        "module",
                        relative_path,
                        relative_path,
                        "",
                        sha256,
                        lines,
                        json.dumps(
                            ts_facts(relative_path, source), sort_keys=True
                        ),
                    ),
                )
                module_count += 1

        input_hashes = {
            fact.path: fact.sha256
            for fact in snapshot.files
            if fact.path in files
        }
        metadata = {
            "schema": SCHEMA,
            "schema_name": "knowledge_graph",
            "schema_version": SCHEMA,
            "source_sha": snapshot.source_sha,
            "source_binding": snapshot.source_binding,
            "tool_version": TOOL_VERSION,
            "input_hashes": json.dumps(input_hashes, sort_keys=True),
            "files": len(files),
            "modules": module_count,
            "functions": function_count,
            "edges": edge_count,
            "parse_errors": parse_errors,
        }
        for key, value in metadata.items():
            _insert_meta(connection, key, value)
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("SQLite integrity check failed")
    finally:
        connection.close()
    return {
        "files": len(files),
        "modules": module_count,
        "functions": function_count,
        "edges": edge_count,
        "parse_errors": parse_errors,
    }


def build_db(
    root: str | os.PathLike[str],
    db_path: str | os.PathLike[str],
) -> dict[str, int]:
    """Build, integrity-check, and atomically replace a graph database."""
    root_path = Path(root).expanduser().resolve()
    destination = Path(db_path).expanduser().resolve()
    files = prod_files(root_path)
    snapshot = _tracked_snapshot(root_path, files)
    _validate_snapshot_inputs(snapshot, files)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(raw_temporary)
    try:
        stats = _populate_database(root_path, temporary, files, snapshot)
        final_files = prod_files(root_path)
        final_snapshot = _tracked_snapshot(root_path, final_files)
        _validate_snapshot_inputs(final_snapshot, final_files)
        if (
            tuple(final_files) != tuple(files)
            or final_snapshot.source_binding != snapshot.source_binding
            or final_snapshot.source_sha != snapshot.source_sha
        ):
            raise RuntimeError("source tree changed during graph build")
        os.replace(temporary, destination)
        return stats
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _default_root() -> Path:
    try:
        from tools.code_intelligence.paths import discover_repo_root

        return discover_repo_root(Path.cwd())
    except (ModuleNotFoundError, ValueError):
        return Path(__file__).resolve().parent.parent


def main(argv: Sequence[str] | None = None) -> int:
    root_default = _default_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=root_default,
        help="repository root (default: discovered Git repository root)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="database path (default: <root>/artifacts/KNOWLEDGE_GRAPH.db)",
    )
    arguments = parser.parse_args(argv)
    root = arguments.root.expanduser().resolve()
    database = (
        arguments.db.expanduser().resolve()
        if arguments.db is not None
        else root / "artifacts" / "KNOWLEDGE_GRAPH.db"
    )
    stats = build_db(root, database)
    print("l0_extract:", json.dumps(stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
