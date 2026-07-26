"""Deterministic, dependency-free semantic extraction for Python functions."""

from __future__ import annotations

import ast
import copy
import errno
import hashlib
import importlib
import inspect
import json
import keyword
import math
import os
import re
import secrets
import stat
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from .results import CheckResult, ResultState
from .schemas import make_envelope, validate_envelope

PolicyClass = Literal["breaking", "risky", "informational", "unknown"]
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

AUTH_NAMES = ("auth", "login", "csrf", "token", "admin", "permission", "require")
CONFIG_NAMES = ("config", "getenv", "settings")
CONCURRENCY_NAMES = ("lock", "thread", "process", "queue", "asyncio", "scheduler")
METRIC_NAMES = ("metric", "counter", "histogram", "gauge", "observe")
SINK_NAMES = ("execute", "popen", "run", "open", "send_file", "fetch", "request")

_MAX_TREE_FILES = 20_000
_MAX_TREE_ENTRIES = 100_000
_MAX_TREE_DIRECTORIES = 50_000
_MAX_TREE_DEPTH = 100
_MAX_TREE_BYTES = 64 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_AST_NODES = 100_000
_MAX_TOTAL_AST_NODES = 3_000_000
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
_MAX_FUNCTIONS = 100_000
_MAX_CHANGES = 200_000
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_MAX_SEMANTIC_WORK = 50_000_000
_STATIC_DESCRIPTOR = "<builtin:staticmethod>"
_CLASS_DESCRIPTOR = "<builtin:classmethod>"


def _replace_supports_dir_fd() -> bool:
    try:
        parameters = inspect.signature(os.replace).parameters
    except (TypeError, ValueError):
        return False
    return {"src_dir_fd", "dst_dir_fd"} <= set(parameters)


_REPLACE_SUPPORTS_DIR_FD = _replace_supports_dir_fd()
_MEMBER_SUPPORTS_DIR_FD = (
    os.open in getattr(os, "supports_dir_fd", set())
    and os.stat in getattr(os, "supports_dir_fd", set())
)
_UNLINK_SUPPORTS_DIR_FD = os.unlink in getattr(os, "supports_dir_fd", set())

FileIdentity = tuple[int, int, int, int, int, int, str]
TreeIdentity = tuple[tuple[str, FileIdentity], ...]
DirectoryIdentity = tuple[int, int, int]
_REDACTED_LITERAL = re.compile(
    r"<redacted><(?:str|bytes|bool|int|float|complex):sha256:[0-9a-f]{64}>"
)
_SENSITIVE_NAME = re.compile(
    r"(?:password|passwd|passcode|secret|token|cookie|api[_-]?key|"
    r"authorization|bearer|private[_-]?key|signing|otp|pin|credential|"
    r"session[_-]?key|access[_-]?token)",
    re.I,
)


@dataclass(frozen=True)
class FunctionSemantics:
    path: str
    qualname: str
    positional_only: tuple[str, ...]
    positional: tuple[str, ...]
    keyword_only: tuple[str, ...]
    vararg: str | None
    kwargs: str | None
    defaults: tuple[tuple[str, str], ...]
    annotations: tuple[tuple[str, str], ...]
    return_annotation: str | None
    return_shapes: tuple[str, ...]
    raises: tuple[str, ...]
    decorators: tuple[str, ...]
    auth_gates: tuple[str, ...]
    calls_resolved: tuple[str, ...]
    calls_unresolved: tuple[str, ...]
    config_ops: tuple[str, ...]
    concurrency_ops: tuple[str, ...]
    metric_ops: tuple[str, ...]
    sinks: tuple[str, ...]


class _SemanticWorkBudget:
    """Bound state-copy work independently of the AST node limit."""

    def __init__(self, maximum: int) -> None:
        self.remaining = maximum

    def consume(self, amount: int) -> None:
        self.remaining -= max(1, amount)
        if self.remaining < 0:
            raise ValueError("semantic analysis budget exceeded")


class _RedactStrings(ast.NodeTransformer):
    def __init__(self, *, scalar_values: bool = False) -> None:
        self.scalar_values = scalar_values

    def visit_Constant(self, node: ast.Constant) -> ast.Constant:
        if isinstance(node.value, str) and _REDACTED_LITERAL.fullmatch(node.value):
            return node
        redact = isinstance(node.value, (str, bytes)) or (
            self.scalar_values
            and isinstance(node.value, (bool, int, float, complex))
        )
        if redact:
            if isinstance(node.value, str):
                raw = node.value.encode("utf-8")
                tag = "str"
            elif isinstance(node.value, bytes):
                raw = node.value
                tag = "bytes"
            else:
                raw = repr(node.value).encode("ascii")
                tag = type(node.value).__name__
            return ast.copy_location(ast.Constant(value=f"<redacted><{tag}:sha256:{hashlib.sha256(raw).hexdigest()}>"), node)
        return node

    def visit_Call(self, node: ast.Call) -> ast.Call:
        try:
            call_name = ast.unparse(node.func)
        except (MemoryError, RecursionError, ValueError):
            call_name = ""
        sensitive_call = _SENSITIVE_NAME.search(call_name) is not None
        node.func = self.visit(node.func)
        node.args = [
            _RedactStrings(scalar_values=True).visit(argument)
            if sensitive_call
            else self.visit(argument)
            for argument in node.args
        ]
        for item in node.keywords:
            item.value = (
                _RedactStrings(scalar_values=True).visit(item.value)
                if sensitive_call
                or (item.arg is not None and _SENSITIVE_NAME.search(item.arg))
                else self.visit(item.value)
            )
        return node

    def visit_Dict(self, node: ast.Dict) -> ast.Dict:
        values: list[ast.AST] = []
        for key, value in zip(node.keys, node.values):
            sensitive_key = (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and _SENSITIVE_NAME.search(key.value) is not None
            )
            values.append(
                _RedactStrings(scalar_values=True).visit(value)
                if sensitive_key
                else self.visit(value)
            )
        node.keys = [
            self.visit(key) if key is not None else None for key in node.keys
        ]
        node.values = values
        return node


def _expr_text(node: ast.AST, *, sensitive: bool = False) -> str:
    """Unparse an expression after replacing all user string literals."""
    wrapper = ast.Expression(body=copy.deepcopy(node))
    redacted = _RedactStrings(scalar_values=sensitive).visit(wrapper)
    return ast.unparse(ast.fix_missing_locations(redacted))


def _call_name(node: ast.Call) -> str:
    return _expr_text(node.func)


def _node_headers(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.AST]:
    # CPython evaluates decorator expressions top-to-bottom, then positional
    # defaults, keyword-only defaults, and annotations before MAKE_FUNCTION.
    # Decorator application itself happens afterward, bottom-to-top, and has
    # no separate AST Call node. CPython 3.12 emits regular positional
    # annotations before positional-only annotations, followed by vararg,
    # keyword-only, kwarg, and return annotations.
    values: list[ast.AST] = [*node.decorator_list]
    values.extend(value for value in node.args.defaults if value is not None)
    values.extend(value for value in node.args.kw_defaults if value is not None)
    values.extend(
        arg.annotation
        for arg in [*node.args.args, *node.args.posonlyargs]
        if arg.annotation
    )
    if node.args.vararg and node.args.vararg.annotation:
        values.append(node.args.vararg.annotation)
    values.extend(
        arg.annotation for arg in node.args.kwonlyargs if arg.annotation
    )
    if node.args.kwarg and node.args.kwarg.annotation:
        values.append(node.args.kwarg.annotation)
    if node.returns:
        values.append(node.returns)
    return values


class _FunctionFacts(ast.NodeVisitor):
    """Collect a function's own body plus structural child function headers."""

    def __init__(self, root: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.root = root
        self.calls: list[ast.Call] = []
        self.raises: list[ast.Raise] = []
        self.returns: list[ast.Return] = []
        self.yields: list[ast.Yield | ast.YieldFrom] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.raises.append(node)
        self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> None:
        self.returns.append(node)
        self.generic_visit(node)

    def visit_Yield(self, node: ast.Yield) -> None:
        self.yields.append(node)
        self.generic_visit(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> None:
        self.yields.append(node)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Defaults are evaluated outside a lambda; lambda bodies are not.
        for value in [*node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            for header in _node_headers(node):
                self.visit(header)
            for statement in node.body:
                self.visit(statement)
            return
        for header in _node_headers(node):
            self.visit(header)

    visit_AsyncFunctionDef = visit_FunctionDef


class _LocalBindings(ast.NodeVisitor):
    """Collect names bound in one function scope without crossing child scopes."""

    def __init__(self) -> None:
        self.function_names: set[str] = set()
        self.other_names: set[str] = set()
        self.other_bindings: dict[str, list[ast.AST]] = {}

    def _bind_other(self, name: str, node: ast.AST) -> None:
        self.other_names.add(name)
        self.other_bindings.setdefault(name, []).append(node)

    @property
    def names(self) -> set[str]:
        return self.function_names | self.other_names

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._bind_other(node.id, node)

    def _visit_definition_headers(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        for header in _node_headers(node):
            self.visit(header)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_names.add(node.name)
        self._visit_definition_headers(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._bind_other(node.name, node)
        for value in [*node.decorator_list, *node.bases]:
            self.visit(value)
        for value in node.keywords:
            self.visit(value.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for value in [*node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind_other(
                alias.asname or alias.name.split(".", 1)[0], node
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._bind_other(alias.asname or alias.name, node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind_other(node.name, node)
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._bind_other(node.name, node)
        if node.pattern is not None:
            self.visit(node.pattern)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._bind_other(node.name, node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._bind_other(node.rest, node)
        self.generic_visit(node)

    def _visit_comprehension(
        self,
        generators: Sequence[ast.comprehension],
        values: Sequence[ast.AST],
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])


def _pick(names: Sequence[str], needles: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(name for name in set(names) if any(needle in name.lower() for needle in needles)))


def _return_shape(value: ast.AST | None) -> str:
    if value is None or (isinstance(value, ast.Constant) and value.value is None):
        return "none"
    if isinstance(value, ast.Constant):
        return type(value.value).__name__.lower()
    return type(value).__name__.removesuffix("Expr").lower()


class _ScopeFunctionDeclarations(ast.NodeVisitor):
    def __init__(self, path: str, scope: tuple[str, ...]) -> None:
        self.path = path
        self.scope = scope
        self.functions: dict[str, list[str]] = {}
        self.nodes: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qualname = ".".join((*self.scope, node.name))
        self.functions.setdefault(node.name, []).append(
            f"{self.path}::{qualname}"
        )
        self.nodes.setdefault(node.name, []).append(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _scope_function_declarations(
    body: Sequence[ast.stmt], path: str, scope: tuple[str, ...]
) -> tuple[set[str], dict[str, str]]:
    visitor = _ScopeFunctionDeclarations(path, scope)
    for statement in body:
        visitor.visit(statement)
    bindings = _LocalBindings()
    for statement in body:
        bindings.visit(statement)
    direct_statements = {id(statement) for statement in body}
    declared: dict[str, str] = {}
    for name, values in visitor.functions.items():
        nodes = visitor.nodes[name]
        if len(values) != 1 or len(nodes) != 1:
            continue
        node = nodes[0]
        other_bindings = bindings.other_bindings.get(name, ())
        if not other_bindings:
            declared[name] = values[0]
            continue
        # A direct definition deterministically overwrites earlier bindings.
        # A later or control-flow-dependent non-function binding makes the
        # runtime target uncertain, so do not claim a stale function target.
        if id(node) in direct_statements and all(
            getattr(binding, "lineno", node.lineno) <= node.lineno
            for binding in other_bindings
        ):
            declared[name] = values[0]
    return set(visitor.functions), declared


class _ScopeDefinitions(ast.NodeVisitor):
    """Collect definitions owned by one lexical scope in source order."""

    def __init__(self) -> None:
        self.nodes: list[
            ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
        ] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.nodes.append(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.nodes.append(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _index_definitions(
    tree: ast.Module, path: str
) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    """Return qualnames, class scopes, and unambiguous function targets."""

    qualnames: dict[int, str] = {}
    class_scopes: dict[int, str] = {}
    targets: dict[int, str] = {}

    def index(body: Sequence[ast.stmt], scope: tuple[str, ...]) -> None:
        collector = _ScopeDefinitions()
        for statement in body:
            collector.visit(statement)
        function_totals: dict[str, int] = {}
        class_totals: dict[str, int] = {}
        for node in collector.nodes:
            totals = (
                class_totals
                if isinstance(node, ast.ClassDef)
                else function_totals
            )
            totals[node.name] = totals.get(node.name, 0) + 1
        function_seen: dict[str, int] = {}
        class_seen: dict[str, int] = {}
        for node in collector.nodes:
            if isinstance(node, ast.ClassDef):
                class_seen[node.name] = class_seen.get(node.name, 0) + 1
                base = ".".join((*scope, node.name))
                class_name = (
                    base
                    if class_seen[node.name] == 1
                    else f"{base}#{class_seen[node.name]}"
                )
                class_scopes[id(node)] = class_name
                index(node.body, (class_name,))
                continue
            function_seen[node.name] = function_seen.get(node.name, 0) + 1
            base = ".".join((*scope, node.name))
            qualname = (
                base
                if function_seen[node.name] == 1
                else f"{base}#{function_seen[node.name]}"
            )
            qualnames[id(node)] = qualname
            if function_totals[node.name] == 1:
                targets[id(node)] = f"{path}::{qualname}"
            index(node.body, (qualname,))

    index(tree.body, ())
    return qualnames, class_scopes, targets


class _ScopeDirectives(ast.NodeVisitor):
    """Collect global/nonlocal directives without crossing child scopes."""

    def __init__(self) -> None:
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()

    def visit_Global(self, node: ast.Global) -> None:
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocals.update(node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _scope_directives(
    body: Sequence[ast.stmt],
) -> tuple[set[str], set[str]]:
    visitor = _ScopeDirectives()
    for statement in body:
        visitor.visit(statement)
    return visitor.globals, visitor.nonlocals


def _direct_method_receiver(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_owner: ast.ClassDef | None,
    descriptor_states: Mapping[int, tuple[str, ...] | None],
    methods: Mapping[str, str],
) -> tuple[str, Mapping[str, str]] | None:
    if class_owner is None:
        return None
    callable_value, receiver_kind = _class_descriptor_access(
        descriptor_states.get(id(node))
    )
    if not callable_value or receiver_kind not in {
        "instance",
        "class",
    }:
        return None
    positional = [*node.args.posonlyargs, *node.args.args]
    return (positional[0].arg, methods) if positional else None


def _descriptor_wrappers(
    decorator_values: Sequence[str | None],
) -> tuple[str, ...] | None:
    wrappers: list[str] = []
    for value in reversed(decorator_values):
        if value == _STATIC_DESCRIPTOR:
            wrappers.append("static")
        elif value == _CLASS_DESCRIPTOR:
            wrappers.append("class")
        else:
            return None
    return tuple(wrappers)


def _raw_descriptor_callable(
    wrappers: tuple[str, ...] | None,
) -> bool:
    """Whether the raw post-decoration object calls the source function."""

    if wrappers is None:
        return False
    if not wrappers:
        return True
    if wrappers[-1] == "class":
        return False
    return _raw_descriptor_callable(wrappers[:-1])


def _class_descriptor_access(
    wrappers: tuple[str, ...] | None,
) -> tuple[bool, str | None]:
    """Model CPython 3.12 attribute access for known descriptor stacks."""

    if wrappers is None:
        return False, None
    if not wrappers:
        return True, "instance"
    inner, outer = wrappers[:-1], wrappers[-1]
    if outer == "static":
        return _raw_descriptor_callable(inner), None
    if not inner:
        return True, "class"
    if inner[-1] == "static":
        return _raw_descriptor_callable(inner[:-1]), None
    return _class_descriptor_access(inner)


class _ScopeCallAnalyzer(ast.NodeVisitor):
    """Resolve calls against the binding state at their execution point."""

    def __init__(
        self,
        *,
        body: Sequence[ast.stmt],
        kind: Literal["module", "class", "function"],
        globals_env: Mapping[str, str | None],
        enclosing_env: Mapping[str, str | None],
        parameters: set[str],
        methods: Mapping[str, str],
        receiver_maps: Mapping[str, Mapping[str, str]],
        direct_receiver: tuple[str, Mapping[str, str]] | None,
        targets: Mapping[int, str],
        class_scopes: Mapping[int, str],
        call_targets: dict[int, str | None],
        class_results: dict[int, dict[str, str | None]],
        descriptor_states: dict[int, tuple[str, ...] | None],
        budget: _SemanticWorkBudget,
    ) -> None:
        budget.consume(
            len(globals_env) + len(enclosing_env) + len(receiver_maps) + 1
        )
        self.budget = budget
        self.kind = kind
        self.globals_env = dict(globals_env)
        self.enclosing_env = dict(enclosing_env)
        self.methods = methods
        self.targets = targets
        self.class_scopes = class_scopes
        self.call_targets = call_targets
        self.class_results = class_results
        self.descriptor_states = descriptor_states
        self.global_updates: dict[str, str | None] = {}
        self.nonlocal_updates: dict[str, str | None] = {}
        self.comprehension_walrus: list[set[str]] = []
        self.global_names, self.nonlocal_names = _scope_directives(body)
        self.env: dict[str, str | None] = {}
        self.member_env: dict[str, str | None] = {}
        # Method maps are immutable provenance snapshots for this analysis.
        # Keep shared references so branch snapshots stay O(receiver names),
        # not O(class methods × branches).
        self.receiver_maps = dict(receiver_maps)
        if kind == "function":
            bindings = _LocalBindings()
            for statement in body:
                bindings.visit(statement)
            local_names = (
                bindings.names
                | parameters
            ) - self.global_names - self.nonlocal_names
            self.env.update({name: None for name in local_names})
            for name in local_names | self.global_names:
                self.receiver_maps.pop(name, None)
            if direct_receiver is not None:
                name, method_map = direct_receiver
                self.receiver_maps[name] = method_map

    def run(self, body: Sequence[ast.stmt]) -> None:
        for statement in body:
            self.visit(statement)

    def visible(self) -> dict[str, str | None]:
        self.budget.consume(
            len(self.globals_env) + len(self.enclosing_env) + len(self.env)
        )
        visible = dict(self.globals_env)
        visible.update(self.enclosing_env)
        visible.update(self.env)
        return visible

    def _lookup(self, name: str) -> str | None:
        if self.kind == "module":
            if name in self.env:
                return self.env[name]
        elif name in self.global_names:
            if name in self.globals_env:
                return self.globals_env[name]
        elif name in self.nonlocal_names:
            if name in self.enclosing_env:
                return self.enclosing_env[name]
        else:
            if name in self.env:
                return self.env[name]
            if name in self.enclosing_env:
                return self.enclosing_env[name]
            if name in self.globals_env:
                return self.globals_env[name]
        if name == "staticmethod":
            return _STATIC_DESCRIPTOR
        if name == "classmethod":
            return _CLASS_DESCRIPTOR
        return None

    def _bind_name(
        self,
        name: str,
        target: str | None = None,
        *,
        member_target: str | None = None,
    ) -> None:
        if self.kind != "module" and name in self.global_names:
            self.globals_env[name] = target
            if self.kind == "class":
                self.global_updates[name] = target
        elif self.kind != "module" and name in self.nonlocal_names:
            self.enclosing_env[name] = target
            if self.kind == "class":
                self.nonlocal_updates[name] = target
        else:
            self.env[name] = target
            if self.kind == "class":
                self.member_env[name] = member_target
        self.receiver_maps.pop(name, None)

    def _bind_target(
        self, node: ast.AST, target: str | None = None
    ) -> None:
        if isinstance(node, ast.Name):
            self._bind_name(node.id, target)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                self._bind_target(item)
        elif isinstance(node, ast.Starred):
            self._bind_target(node.value)

    def _resolve_call(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            target = self._lookup(node.func.id)
            return (
                target
                if target not in {_STATIC_DESCRIPTOR, _CLASS_DESCRIPTOR}
                else None
            )
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.receiver_maps
        ):
            return self.receiver_maps[node.func.value.id].get(node.func.attr)
        return None

    def visit_Call(self, node: ast.Call) -> None:
        self.call_targets[id(node)] = self._resolve_call(node)
        self.visit(node.func)
        for argument in node.args:
            self.visit(argument)
        for keyword_item in node.keywords:
            self.visit(keyword_item.value)

    def visit_Name(self, node: ast.Name) -> None:
        return

    def _descriptor_value(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            value = self._lookup(node.id)
            if value in {_STATIC_DESCRIPTOR, _CLASS_DESCRIPTOR}:
                return value
        return None

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, self._descriptor_value(node.value))
        if isinstance(node.target, ast.Name):
            for names in self.comprehension_walrus:
                names.add(node.target.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        decorator_values: list[str | None] = []
        for decorator in node.decorator_list:
            decorator_values.append(self._descriptor_value(decorator))
            self.visit(decorator)
        for header in _node_headers(node)[len(node.decorator_list):]:
            self.visit(header)
        wrappers = _descriptor_wrappers(decorator_values)
        self.descriptor_states[id(node)] = wrappers
        semantic_target = self.targets.get(id(node))
        raw_target = (
            semantic_target
            if _raw_descriptor_callable(wrappers)
            else None
        )
        member_callable, _receiver = _class_descriptor_access(wrappers)
        self._bind_name(
            node.name,
            raw_target,
            member_target=semantic_target if member_callable else None,
        )

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for value in [*node.decorator_list, *node.bases]:
            self.visit(value)
        for keyword_item in node.keywords:
            self.visit(keyword_item.value)
        nested_globals = (
            self.env if self.kind == "module" else self.globals_env
        )
        nested_enclosing = (
            {}
            if self.kind == "module"
            else (
                self.enclosing_env
                if self.kind == "class"
                else self.visible()
            )
        )
        nested = _ScopeCallAnalyzer(
            body=node.body,
            kind="class",
            globals_env=nested_globals,
            enclosing_env=nested_enclosing,
            parameters=set(),
            methods=self.methods,
            receiver_maps=self.receiver_maps,
            direct_receiver=None,
            targets=self.targets,
            class_scopes=self.class_scopes,
            call_targets=self.call_targets,
            class_results=self.class_results,
            descriptor_states=self.descriptor_states,
            budget=self.budget,
        )
        nested.run(node.body)
        self.class_results[id(node)] = dict(nested.member_env)
        for name, target in nested.global_updates.items():
            if self.kind == "module":
                self.env[name] = target
            else:
                self.globals_env[name] = target
                if self.kind == "class":
                    self.global_updates[name] = target
        for name, target in nested.nonlocal_updates.items():
            if self.kind == "function" and name in self.env:
                self.env[name] = target
            else:
                self.enclosing_env[name] = target
                if self.kind == "class":
                    self.nonlocal_updates[name] = target
            self.receiver_maps.pop(name, None)
        self._bind_name(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for value in [*node.args.defaults, *node.args.kw_defaults]:
            if value is not None:
                self.visit(value)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        descriptor_value = self._descriptor_value(node.value)
        for target in node.targets:
            self._bind_target(target, descriptor_value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._bind_target(
            node.target,
            self._descriptor_value(node.value)
            if node.value is not None
            else None,
        )

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._bind_target(target)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._bind_name(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self._bind_name(alias.asname or alias.name)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        self.run(node.body)

    visit_AsyncWith = visit_With

    def _state(
        self,
    ) -> tuple[
        dict[str, str | None],
        dict[str, str | None],
        dict[str, str | None],
        dict[str, Mapping[str, str]],
        dict[str, str | None],
    ]:
        self.budget.consume(
            len(self.env)
            + len(self.globals_env)
            + len(self.enclosing_env)
            + len(self.receiver_maps)
            + len(self.member_env)
        )
        return (
            dict(self.env),
            dict(self.globals_env),
            dict(self.enclosing_env),
            dict(self.receiver_maps),
            dict(self.member_env),
        )

    def _restore(
        self,
        state: tuple[
            dict[str, str | None],
            dict[str, str | None],
            dict[str, str | None],
            dict[str, Mapping[str, str]],
            dict[str, str | None],
        ],
    ) -> None:
        self.budget.consume(
            len(state[0])
            + len(state[1])
            + len(state[2])
            + len(state[3])
            + len(state[4])
        )
        (
            self.env,
            self.globals_env,
            self.enclosing_env,
            self.receiver_maps,
            self.member_env,
        ) = (
            dict(state[0]),
            dict(state[1]),
            dict(state[2]),
            dict(state[3]),
            dict(state[4]),
        )

    def _merge_maps(
        self,
        values: Sequence[Mapping[str, str | None]],
    ) -> dict[str, str | None]:
        self.budget.consume(sum(len(value) for value in values))
        merged: dict[str, str | None] = {}
        keys = set().union(*(value.keys() for value in values))
        missing = object()
        for key in keys:
            candidates = [value.get(key, missing) for value in values]
            if all(candidate == candidates[0] for candidate in candidates):
                if candidates[0] is not missing:
                    merged[key] = candidates[0]  # type: ignore[assignment]
            else:
                merged[key] = None
        return merged

    def _merge_states(
        self,
        states: Sequence[
            tuple[
                dict[str, str | None],
                dict[str, str | None],
                dict[str, str | None],
                dict[str, Mapping[str, str]],
                dict[str, str | None],
            ]
        ],
    ) -> None:
        self.env = self._merge_maps([state[0] for state in states])
        self.globals_env = self._merge_maps([state[1] for state in states])
        self.enclosing_env = self._merge_maps([state[2] for state in states])
        receiver_keys = set.intersection(
            *(set(state[3]) for state in states)
        )
        self.receiver_maps = {
            name: states[0][3][name]
            for name in receiver_keys
            if all(
                state[3][name] is states[0][3][name] for state in states
            )
        }
        self.member_env = self._merge_maps(
            [state[4] for state in states]
        )
        if self.kind == "class":
            for name in self.global_updates:
                if name in self.globals_env:
                    self.global_updates[name] = self.globals_env[name]
            for name in self.nonlocal_updates:
                if name in self.enclosing_env:
                    self.nonlocal_updates[name] = self.enclosing_env[name]

    def _branch(
        self,
        body: Sequence[ast.stmt],
        initial: tuple[
            dict[str, str | None],
            dict[str, str | None],
            dict[str, str | None],
            dict[str, Mapping[str, str]],
            dict[str, str | None],
        ],
    ) -> tuple[
        dict[str, str | None],
        dict[str, str | None],
        dict[str, str | None],
        dict[str, Mapping[str, str]],
        dict[str, str | None],
    ]:
        self._restore(initial)
        self.run(body)
        return self._state()

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        initial = self._state()
        body_state = self._branch(node.body, initial)
        else_state = self._branch(node.orelse, initial)
        self._merge_states([body_state, else_state])

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        initial = self._state()
        body_state = self._branch(node.body, initial)
        else_state = self._branch(node.orelse, initial)
        self._merge_states([initial, body_state, else_state])

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        initial = self._state()
        self._restore(initial)
        self._bind_target(node.target)
        self.run(node.body)
        body_state = self._state()
        else_state = self._branch(node.orelse, body_state)
        self._merge_states([initial, body_state, else_state])

    visit_AsyncFor = visit_For

    def visit_Try(self, node: ast.Try) -> None:
        initial = self._state()
        success = self._branch([*node.body, *node.orelse], initial)
        outcomes = [success]
        for handler in node.handlers:
            self._restore(initial)
            if handler.type is not None:
                self.visit(handler.type)
            if handler.name:
                self._bind_name(handler.name)
            self.run(handler.body)
            if handler.name:
                self._bind_name(handler.name)
            outcomes.append(self._state())
        self._merge_states(outcomes)
        self.run(node.finalbody)

    visit_TryStar = visit_Try

    def _bind_pattern(self, node: ast.pattern) -> None:
        if isinstance(node, ast.MatchAs):
            if node.pattern is not None:
                self._bind_pattern(node.pattern)
            if node.name:
                self._bind_name(node.name)
        elif isinstance(node, ast.MatchStar):
            if node.name:
                self._bind_name(node.name)
        elif isinstance(node, ast.MatchMapping):
            for pattern in node.patterns:
                self._bind_pattern(pattern)
            if node.rest:
                self._bind_name(node.rest)
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.pattern):
                    self._bind_pattern(child)

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        initial = self._state()
        outcomes = [initial]
        for case in node.cases:
            self._restore(initial)
            self._bind_pattern(case.pattern)
            if case.guard is not None:
                self.visit(case.guard)
            self.run(case.body)
            outcomes.append(self._state())
        self._merge_states(outcomes)

    def _visit_comprehension(
        self,
        generators: Sequence[ast.comprehension],
        values: Sequence[ast.AST],
    ) -> None:
        state = self._state()
        self.comprehension_walrus.append(set())
        for generator in generators:
            self.visit(generator.iter)
            self._bind_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        walrus_names = self.comprehension_walrus.pop()
        self._restore(state)
        for name in sorted(walrus_names):
            self._bind_name(name)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(
            node.generators, [node.key, node.value]
        )


def _function(
    path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    methods: Mapping[str, str],
    call_targets: Mapping[int, str | None],
) -> FunctionSemantics:
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    default_pairs = [
        (
            arg.arg,
            _expr_text(
                value, sensitive=_SENSITIVE_NAME.search(arg.arg) is not None
            ),
        )
        for arg, value in zip(positional, defaults)
        if value is not None
    ]
    default_pairs.extend(
        (
            arg.arg,
            _expr_text(
                value, sensitive=_SENSITIVE_NAME.search(arg.arg) is not None
            ),
        )
        for arg, value in zip(node.args.kwonlyargs, node.args.kw_defaults)
        if value is not None
    )
    facts = _FunctionFacts(node)
    for statement in node.body:
        facts.visit(statement)
    header_facts = _FunctionFacts(node)
    for header in _node_headers(node):
        header_facts.visit(header)
    calls = sorted(
        {_call_name(item) for item in [*facts.calls, *header_facts.calls]}
    )
    resolved: set[str] = set()
    unresolved: set[str] = set()
    for call in [*facts.calls, *header_facts.calls]:
        name = _call_name(call)
        target = call_targets.get(id(call))
        if target is not None:
            resolved.add(target)
        else:
            unresolved.add(name)
    raises = sorted({"raise:bare" if item.exc is None else _expr_text(item.exc.func if isinstance(item.exc, ast.Call) else item.exc) for item in facts.raises})
    decorators = tuple(_expr_text(item) for item in node.decorator_list)
    surfaces = sorted(set(calls) | set(decorators))
    annotated_args = [*node.args.posonlyargs, *node.args.args]
    if node.args.vararg is not None:
        annotated_args.append(node.args.vararg)
    annotated_args.extend(node.args.kwonlyargs)
    if node.args.kwarg is not None:
        annotated_args.append(node.args.kwarg)
    annotations = tuple(
        (
            arg.arg,
            _expr_text(
                arg.annotation,
                sensitive=_SENSITIVE_NAME.search(arg.arg) is not None,
            ),
        )
        for arg in annotated_args
        if arg.annotation is not None
    )
    return FunctionSemantics(
        path=path, qualname=qualname,
        positional_only=tuple(arg.arg for arg in node.args.posonlyargs),
        positional=tuple(arg.arg for arg in node.args.args), keyword_only=tuple(arg.arg for arg in node.args.kwonlyargs),
        vararg=node.args.vararg.arg if node.args.vararg else None, kwargs=node.args.kwarg.arg if node.args.kwarg else None,
        defaults=tuple(default_pairs), annotations=annotations, return_annotation=_expr_text(node.returns) if node.returns else None,
        return_shapes=tuple(sorted({_return_shape(item.value) for item in facts.returns} | {f"yield:{_return_shape(item.value)}" for item in facts.yields})), raises=tuple(raises), decorators=decorators,
        auth_gates=_pick(surfaces, AUTH_NAMES), calls_resolved=tuple(sorted(resolved)), calls_unresolved=tuple(sorted(unresolved)),
        config_ops=_pick(surfaces, CONFIG_NAMES), concurrency_ops=_pick(surfaces, CONCURRENCY_NAMES), metric_ops=_pick(surfaces, METRIC_NAMES), sinks=_pick(surfaces, SINK_NAMES),
    )


def _is_linkish(path: Path) -> bool:
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is None:
        if os.name != "nt":
            return False
        try:
            import ctypes

            attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            return attributes != 0xFFFFFFFF and bool(attributes & 0x400)
        except (AttributeError, OSError):
            return True
    return bool(isjunction(path))


def _directory_identity(info: os.stat_result) -> DirectoryIdentity:
    return info.st_dev, info.st_ino, info.st_mode


def _pin_directory(path: Path) -> tuple[int | None, DirectoryIdentity]:
    absolute = Path(os.path.abspath(path))
    if any(
        not candidate.exists() or _is_linkish(candidate)
        for candidate in (absolute, *absolute.parents)
    ):
        raise ValueError("directory invalid")
    before = absolute.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise ValueError("directory invalid")
    supports_dir_fd = os.open in getattr(os, "supports_dir_fd", set())
    if not supports_dir_fd or not hasattr(os, "O_DIRECTORY"):
        return None, _directory_identity(before)
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    try:
        opened = os.fstat(descriptor)
        after = absolute.lstat()
        if not (
            _directory_identity(before)
            == _directory_identity(opened)
            == _directory_identity(after)
        ):
            raise ValueError("directory invalid")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, _directory_identity(after)


def _directory_is_stable(
    path: Path, descriptor: int | None, identity: DirectoryIdentity
) -> bool:
    try:
        current = path.lstat()
        if _is_linkish(path) or _directory_identity(current) != identity:
            return False
        return (
            descriptor is None
            or _directory_identity(os.fstat(descriptor)) == identity
        )
    except OSError:
        return False


def _safe_tree_root(repo_root: Path) -> Path:
    absolute = Path(os.path.abspath(repo_root))
    if any(_is_linkish(candidate) for candidate in (absolute, *absolute.parents)):
        raise ValueError("semantic tree invalid")
    root = absolute.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("semantic tree invalid")
    return root


def _bounded_subprocess_output(
    command: Sequence[str], *, maximum: int
) -> tuple[int, bytes]:
    with tempfile.TemporaryFile() as output:
        completed = subprocess.run(
            list(command),
            stdout=output,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        size = output.tell()
        if size > maximum:
            raise ValueError("semantic tree invalid")
        output.seek(0)
        return completed.returncode, output.read(maximum + 1)


def _has_git_marker(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def _git_tracked_tree_files(root: Path) -> list[Path] | None:
    try:
        discovered_code, discovered_output = _bounded_subprocess_output(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            maximum=4096,
        )
        if discovered_code:
            if _has_git_marker(root):
                raise ValueError("semantic tree invalid")
            return None
        repository = Path(
            discovered_output.decode("utf-8", "surrogateescape").strip()
        ).resolve(strict=True)
        relative_root = root.relative_to(repository)
        listed_code, listed_output = _bounded_subprocess_output(
            [
                "git",
                "-C",
                str(repository),
                "ls-files",
                "-z",
                "--cached",
                "--",
                relative_root.as_posix() or ".",
            ],
            maximum=16 * 1024 * 1024,
        )
        if listed_code:
            raise ValueError("semantic tree invalid")
        raw_entries = [entry for entry in listed_output.split(b"\0") if entry]
        if not raw_entries:
            return []
        if len(raw_entries) > _MAX_TREE_ENTRIES:
            raise ValueError("semantic tree invalid")
        files: list[Path] = []
        seen: set[str] = set()
        for raw_entry in raw_entries:
            tracked = raw_entry.decode("utf-8", "surrogateescape")
            candidate = repository / tracked
            try:
                relative = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("semantic tree invalid") from exc
            relative_name = relative.as_posix()
            if (
                not relative_name
                or relative_name in seen
                or len(relative_name) > 4096
                or len(relative.parts) > _MAX_TREE_DEPTH
            ):
                raise ValueError("semantic tree invalid")
            seen.add(relative_name)
            if candidate.suffix != ".py":
                continue
            cursor = root
            for part in relative.parts:
                cursor /= part
                if _is_linkish(cursor):
                    raise ValueError("semantic tree invalid")
            files.append(candidate)
            if len(files) > _MAX_TREE_FILES:
                raise ValueError("semantic tree invalid")
        return sorted(files, key=lambda item: item.relative_to(root).as_posix())
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
    ) as exc:
        if isinstance(exc, ValueError) and str(exc) == "semantic tree invalid":
            raise
        if _has_git_marker(root):
            raise ValueError("semantic tree invalid") from exc
        return None


def _tree_files(repo_root: Path) -> list[Path]:
    root = _safe_tree_root(repo_root)
    tracked = _git_tracked_tree_files(root)
    if tracked is not None:
        return tracked
    files: list[Path] = []
    entry_count = 0
    directory_count = 0

    def walk_error(_error: OSError) -> None:
        raise ValueError("semantic tree invalid")

    for current, directories, filenames in os.walk(
        root, followlinks=False, onerror=walk_error
    ):
        current_path = Path(current)
        directory_count += 1
        if (
            directory_count > _MAX_TREE_DIRECTORIES
            or len(current_path.relative_to(root).parts) > _MAX_TREE_DEPTH
        ):
            raise ValueError("semantic tree invalid")
        entry_count += len(directories) + len(filenames)
        if entry_count > _MAX_TREE_ENTRIES:
            raise ValueError("semantic tree invalid")
        for name in [*directories, *filenames]:
            candidate = current_path / name
            if _is_linkish(candidate):
                raise ValueError("semantic tree invalid")
        for name in filenames:
            candidate = current_path / name
            if candidate.suffix == ".py":
                if not candidate.resolve().is_relative_to(root):
                    raise ValueError("semantic tree invalid")
                if len(candidate.relative_to(root).as_posix()) > 4096:
                    raise ValueError("semantic tree invalid")
                files.append(candidate)
                if len(files) > _MAX_TREE_FILES:
                    raise ValueError("semantic tree invalid")
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _snapshot_parsed(
    parsed_files: Mapping[str, ast.Module],
) -> dict[str, FunctionSemantics]:
    result: dict[str, FunctionSemantics] = {}
    budget = _SemanticWorkBudget(_MAX_SEMANTIC_WORK)
    for rel, tree in parsed_files.items():
        qualnames, class_scopes, targets = _index_definitions(tree, rel)
        call_targets: dict[int, str | None] = {}
        class_results: dict[int, dict[str, str | None]] = {}
        descriptor_states: dict[int, tuple[str, ...] | None] = {}
        module = _ScopeCallAnalyzer(
            body=tree.body,
            kind="module",
            globals_env={},
            enclosing_env={},
            parameters=set(),
            methods={},
            receiver_maps={},
            direct_receiver=None,
            targets=targets,
            class_scopes=class_scopes,
            call_targets=call_targets,
            class_results=class_results,
            descriptor_states=descriptor_states,
            budget=budget,
        )
        module.run(tree.body)
        module_globals = dict(module.env)

        def walk(
            body: Sequence[ast.stmt],
            methods: Mapping[str, str],
            inherited_local: Mapping[str, str | None],
            inherited_receivers: Mapping[str, Mapping[str, str]],
            runtime_globals: Mapping[str, str | None],
            *,
            class_owner: ast.ClassDef | None = None,
        ) -> None:
            for item in body:
                if isinstance(item, ast.ClassDef):
                    class_state = class_results.get(id(item), {})
                    exact_methods = {
                        name: target
                        for name, target in class_state.items()
                        if target is not None
                        and target.startswith(f"{rel}::")
                    }
                    walk(
                        item.body,
                        exact_methods,
                        inherited_local,
                        inherited_receivers,
                        runtime_globals,
                        class_owner=item,
                    )
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parameter_names = {
                        arg.arg
                        for arg in [
                            *item.args.posonlyargs,
                            *item.args.args,
                            *item.args.kwonlyargs,
                        ]
                    }
                    if item.args.vararg is not None:
                        parameter_names.add(item.args.vararg.arg)
                    if item.args.kwarg is not None:
                        parameter_names.add(item.args.kwarg.arg)
                    analyzer = _ScopeCallAnalyzer(
                        body=item.body,
                        kind="function",
                        globals_env=runtime_globals,
                        enclosing_env=inherited_local,
                        parameters=parameter_names,
                        methods=methods,
                        receiver_maps=inherited_receivers,
                        direct_receiver=_direct_method_receiver(
                            item,
                            class_owner,
                            descriptor_states,
                            methods,
                        ),
                        targets=targets,
                        class_scopes=class_scopes,
                        call_targets=call_targets,
                        class_results=class_results,
                        descriptor_states=descriptor_states,
                        budget=budget,
                    )
                    analyzer.run(item.body)
                    qualified = qualnames[id(item)]
                    semantics = _function(
                        rel,
                        item,
                        qualified,
                        methods,
                        call_targets,
                    )
                    result[f"{rel}::{qualified}"] = semantics
                    if len(result) > _MAX_FUNCTIONS:
                        raise ValueError("semantic tree invalid")
                    walk(
                        item.body,
                        methods,
                        analyzer.visible(),
                        analyzer.receiver_maps,
                        analyzer.globals_env,
                    )
                else:
                    # Covers every current and future statement body field
                    # (including match cases and TryStar) without descending
                    # through a function body outside this controlled walker.
                    def descend(value: ast.AST) -> None:
                        if isinstance(value, ast.stmt):
                            walk(
                                [value],
                                methods,
                                inherited_local,
                                inherited_receivers,
                                runtime_globals,
                                class_owner=class_owner,
                            )
                        else:
                            for child in ast.iter_child_nodes(value):
                                descend(child)
                    for child in ast.iter_child_nodes(item):
                        descend(child)
        walk(tree.body, {}, {}, {}, module_globals)
    return result


def _locations_parsed(
    parsed_files: Mapping[str, ast.Module],
    functions: Mapping[str, FunctionSemantics],
) -> list[dict[str, JsonValue]]:
    locations: list[dict[str, JsonValue]] = []
    for rel, parsed in parsed_files.items():
        counts: dict[str, int] = {}
        class_counts: dict[str, int] = {}

        def visit(body: Sequence[ast.stmt], scope: tuple[str, ...]) -> None:
            for node in body:
                if isinstance(node, ast.ClassDef):
                    class_base = ".".join((*scope, node.name))
                    class_counts[class_base] = class_counts.get(class_base, 0) + 1
                    class_name = (
                        class_base
                        if class_counts[class_base] == 1
                        else f"{class_base}#{class_counts[class_base]}"
                    )
                    visit(node.body, (class_name,))
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    base = ".".join((*scope, node.name))
                    counts[base] = counts.get(base, 0) + 1
                    name = base if counts[base] == 1 else f"{base}#{counts[base]}"
                    key = f"{rel}::{name}"
                    if key in functions:
                        locations.append(
                            {"function": key, "path": rel, "line": node.lineno}
                        )
                    visit(node.body, (name,))
                else:

                    def descend(value: ast.AST) -> None:
                        if isinstance(value, ast.stmt):
                            visit([value], scope)
                        else:
                            for child in ast.iter_child_nodes(value):
                                descend(child)

                    for child in ast.iter_child_nodes(node):
                        descend(child)

        visit(parsed.body, ())
    return sorted(locations, key=lambda row: str(row["function"]))


def _read_stable_file(
    path: Path, *, maximum: int, stage: str
) -> tuple[bytes, FileIdentity]:
    parent_descriptor: int | None = None
    try:
        absolute = Path(os.path.abspath(path))
        parent_descriptor, parent_identity = _pin_directory(absolute.parent)
        if parent_descriptor is None or not _MEMBER_SUPPORTS_DIR_FD:
            path_before = absolute.lstat()
            if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(
                path_before.st_mode
            ):
                raise ValueError(stage)
            if path_before.st_size > maximum:
                raise ValueError(stage)
            with absolute.open("rb") as source:
                opened_before = os.fstat(source.fileno())
                raw = source.read(maximum + 1)
                opened_after = os.fstat(source.fileno())
            path_after = absolute.lstat()
        else:
            path_before = os.stat(
                absolute.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(
                path_before.st_mode
            ):
                raise ValueError(stage)
            if path_before.st_size > maximum:
                raise ValueError(stage)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            file_descriptor = os.open(
                absolute.name, flags, dir_fd=parent_descriptor
            )
            try:
                opened_before = os.fstat(file_descriptor)
                chunks: list[bytes] = []
                remaining = maximum + 1
                while remaining:
                    chunk = os.read(file_descriptor, min(remaining, 1024 * 1024))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                opened_after = os.fstat(file_descriptor)
            finally:
                os.close(file_descriptor)
            path_after = os.stat(
                absolute.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        if stat.S_ISLNK(path_before.st_mode) or not stat.S_ISREG(path_before.st_mode):
            raise ValueError(stage)
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(
            getattr(path_before, field) != getattr(opened_before, field)
            or getattr(opened_before, field) != getattr(opened_after, field)
            or getattr(opened_after, field) != getattr(path_after, field)
            for field in stable_fields
        ):
            raise ValueError(stage)
        if len(raw) != path_after.st_size or len(raw) > maximum:
            raise ValueError(stage)
        if not _directory_is_stable(
            absolute.parent, parent_descriptor, parent_identity
        ):
            raise ValueError(stage)
        identity: FileIdentity = (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_mode,
            path_after.st_size,
            path_after.st_mtime_ns,
            path_after.st_ctime_ns,
            hashlib.sha256(raw).hexdigest(),
        )
        return raw, identity
    except (OSError, ValueError) as exc:
        raise ValueError(stage) from exc
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _capture_tree(
    repo_root: Path,
) -> tuple[
    dict[str, FunctionSemantics],
    str,
    TreeIdentity,
    list[dict[str, JsonValue]],
]:
    root = _safe_tree_root(repo_root)
    root_descriptor: int | None = None
    try:
        root_descriptor, root_identity = _pin_directory(root)
        tree_files = _tree_files(root)
        if not _directory_is_stable(root, root_descriptor, root_identity):
            raise ValueError("semantic tree invalid")
    except (OSError, ValueError) as exc:
        if root_descriptor is not None:
            os.close(root_descriptor)
        raise ValueError("semantic tree invalid") from exc
    digest = hashlib.sha256()
    identities: list[tuple[str, FileIdentity]] = []
    parsed_files: dict[str, ast.Module] = {}
    total = 0
    total_nodes = 0
    try:
        for file_path in tree_files:
            rel = file_path.relative_to(root).as_posix()
            raw, identity = _read_stable_file(
                file_path,
                maximum=_MAX_FILE_BYTES,
                stage="semantic tree invalid",
            )
            if not _directory_is_stable(root, root_descriptor, root_identity):
                raise ValueError("semantic tree invalid")
            total += len(raw)
            if total > _MAX_TREE_BYTES:
                raise ValueError("semantic tree invalid")
            try:
                tree = compile(
                    raw,
                    rel,
                    "exec",
                    flags=ast.PyCF_ONLY_AST,
                    dont_inherit=True,
                )
            except (MemoryError, RecursionError, SyntaxError, UnicodeError) as exc:
                raise ValueError("semantic tree invalid") from exc
            file_nodes = sum(1 for _ in ast.walk(tree))
            total_nodes += file_nodes
            if (
                file_nodes > _MAX_AST_NODES
                or total_nodes > _MAX_TOTAL_AST_NODES
            ):
                raise ValueError("semantic tree invalid")
            parsed_files[rel] = tree
            identities.append((rel, identity))
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw)
            digest.update(b"\0")
        semantics = _snapshot_parsed(parsed_files)
        locations = _locations_parsed(parsed_files, semantics)
        if not _directory_is_stable(root, root_descriptor, root_identity):
            raise ValueError("semantic tree invalid")
        return semantics, digest.hexdigest(), tuple(identities), locations
    except (MemoryError, RecursionError) as exc:
        raise ValueError("semantic tree invalid") from exc
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)


def _tree_identity(repo_root: Path) -> TreeIdentity:
    root = _safe_tree_root(repo_root)
    root_descriptor: int | None = None
    root_descriptor, root_identity = _pin_directory(root)
    identities: list[tuple[str, FileIdentity]] = []
    total = 0
    try:
        tree_files = _tree_files(root)
        if not _directory_is_stable(root, root_descriptor, root_identity):
            raise ValueError("semantic tree invalid")
        for file_path in tree_files:
            rel = file_path.relative_to(root).as_posix()
            raw, identity = _read_stable_file(
                file_path,
                maximum=_MAX_FILE_BYTES,
                stage="semantic tree invalid",
            )
            if not _directory_is_stable(root, root_descriptor, root_identity):
                raise ValueError("semantic tree invalid")
            total += len(raw)
            if total > _MAX_TREE_BYTES:
                raise ValueError("semantic tree invalid")
            identities.append((rel, identity))
        return tuple(identities)
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)


def snapshot_tree(repo_root: Path) -> dict[str, FunctionSemantics]:
    return _capture_tree(repo_root)[0]


def classify_change(field: str, before: JsonValue, after: JsonValue) -> PolicyClass:
    if field in {"positional_only", "positional", "keyword_only", "vararg", "kwargs", "defaults", "annotations", "return_annotation", "return_shapes", "raises"}:
        return "breaking"
    if field in {"decorators", "auth_gates", "config_ops", "concurrency_ops", "sinks"}:
        return "risky"
    if field == "metric_ops":
        return "informational"
    return "unknown"


def _jsonable(value: Any) -> JsonValue:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def compare_semantics(before: Mapping[str, FunctionSemantics], after: Mapping[str, FunctionSemantics]) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []

    def append(row: dict[str, JsonValue]) -> None:
        rows.append(row)
        if len(rows) > _MAX_CHANGES:
            raise ValueError("semantic comparison invalid")

    for key in sorted(set(before) | set(after)):
        if key not in before or key not in after:
            append({"function": key, "field": "presence", "before": key in before, "after": key in after, "policy": "breaking"})
            continue
        left, right = asdict(before[key]), asdict(after[key])
        for field in sorted(set(left) - {"path", "qualname"}):
            if left[field] != right[field]:
                append({"function": key, "field": field, "before": _jsonable(left[field]), "after": _jsonable(right[field]), "policy": classify_change(field, _jsonable(left[field]), _jsonable(right[field]))})
    return rows


def _hash_tree(path: Path) -> str:
    return _capture_tree(path)[1]


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("duplicate")
        output[key] = value
    return output


def _safe_identifier(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= 255
        and value.isidentifier()
        and not keyword.iskeyword(value)
    )


def _safe_qualname(value: object) -> bool:
    if type(value) is not str or not value or len(value) > 4096:
        return False
    for component in value.split("."):
        base, separator, occurrence = component.partition("#")
        if not _safe_identifier(base):
            return False
        if separator and (
            not occurrence.isdigit()
            or occurrence.startswith("0")
            or int(occurrence) < 2
        ):
            return False
    return True


def _safe_relative_path(value: object) -> bool:
    if (
        type(value) is not str
        or not value
        or len(value) > 4096
        or "\\" in value
        or value.startswith("/")
        or not value.endswith(".py")
    ):
        return False
    parts = value.split("/")
    return all(
        part not in {"", ".", ".."}
        and len(part) <= 255
        and all(character.isprintable() and character != "\x00" for character in part)
        for part in parts
    )


def _safe_function_key(value: object) -> bool:
    if type(value) is not str or value.count("::") != 1:
        return False
    path, qualname = value.split("::", 1)
    return _safe_relative_path(path) and _safe_qualname(qualname)


def _load_snapshot(
    path: Path, stage: str
) -> tuple[dict[str, FunctionSemantics], str, FileIdentity]:
    try:
        raw, identity = _read_stable_file(
            path, maximum=_MAX_SNAPSHOT_BYTES, stage=stage
        )
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("nonfinite")),
            parse_float=lambda value: (
                (_ for _ in ()).throw(ValueError("nonfinite"))
                if not math.isfinite(float(value))
                else float(value)
            ),
        )
        required = {"schema_name", "schema_version", "source_sha", "tool_version", "input_hashes", "generated_at", "functions"}
        if not isinstance(data, dict) or set(data) != required or data["schema_name"] != "bd.semantic-snapshot" or type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("invalid")
        validate_envelope(data, expected_name="bd.semantic-snapshot", supported_version=1)
        if not isinstance(data["functions"], dict) or len(data["functions"]) > 20_000:
            raise ValueError("invalid")
        names = {field.name for field in fields(FunctionSemantics)}
        functions: dict[str, FunctionSemantics] = {}
        for key, value in data["functions"].items():
            if not isinstance(key, str) or not isinstance(value, dict) or set(value) != names:
                raise ValueError("invalid")
            path_value, qualname = value["path"], value["qualname"]
            if (
                not _safe_relative_path(path_value)
                or not _safe_qualname(qualname)
                or key != f"{path_value}::{qualname}"
            ):
                raise ValueError("invalid")

            def string_list(
                name: str,
                *,
                ordered: bool = False,
                identifiers: bool = False,
                unique: bool = True,
            ) -> tuple[str, ...]:
                raw_values = value[name]
                if (
                    not isinstance(raw_values, list)
                    or len(raw_values) > 20_000
                    or any(
                        type(item) is not str
                        or not item
                        or len(item) > 4096
                        or (identifiers and not _safe_identifier(item))
                        for item in raw_values
                    )
                    or (unique and len(set(raw_values)) != len(raw_values))
                    or (not ordered and raw_values != sorted(raw_values))
                ):
                    raise ValueError("invalid")
                return tuple(raw_values)

            def expression(text: object, *, sensitive: bool = False) -> str:
                if type(text) is not str or not text or len(text) > 4096:
                    raise ValueError("invalid")
                parsed = ast.parse(text, mode="eval")
                return _expr_text(parsed.body, sensitive=sensitive)

            def expression_list(
                name: str, *, ordered: bool = False, unique: bool = True
            ) -> tuple[str, ...]:
                raw_values = string_list(
                    name, ordered=ordered, unique=unique
                )
                canonical = tuple(expression(item) for item in raw_values)
                if (unique and len(set(canonical)) != len(canonical)) or (
                    not ordered and list(canonical) != sorted(canonical)
                ):
                    raise ValueError("invalid")
                return canonical

            def pairs(name: str) -> tuple[tuple[str, str], ...]:
                raw_pairs = value[name]
                if not isinstance(raw_pairs, list) or len(raw_pairs) > 10_000:
                    raise ValueError("invalid")
                output_pairs: list[tuple[str, str]] = []
                for pair in raw_pairs:
                    if (
                        not isinstance(pair, list)
                        or len(pair) != 2
                        or not _safe_identifier(pair[0])
                    ):
                        raise ValueError("invalid")
                    output_pairs.append(
                        (
                            pair[0],
                            expression(
                                pair[1],
                                sensitive=_SENSITIVE_NAME.search(pair[0])
                                is not None,
                            ),
                        )
                    )
                if len({name for name, _ in output_pairs}) != len(output_pairs):
                    raise ValueError("invalid")
                return tuple(output_pairs)

            positional_only = string_list(
                "positional_only", ordered=True, identifiers=True
            )
            positional = string_list(
                "positional", ordered=True, identifiers=True
            )
            keyword_only = string_list(
                "keyword_only", ordered=True, identifiers=True
            )
            vararg = value["vararg"]
            kwargs = value["kwargs"]
            if (vararg is not None and not _safe_identifier(vararg)) or (
                kwargs is not None and not _safe_identifier(kwargs)
            ):
                raise ValueError("invalid")
            defaults = pairs("defaults")
            annotations = pairs("annotations")
            parameter_order = [
                *positional_only,
                *positional,
                *([vararg] if vararg else []),
                *keyword_only,
                *([kwargs] if kwargs else []),
            ]
            if len(parameter_order) != len(set(parameter_order)):
                raise ValueError("invalid")
            order = {name: index for index, name in enumerate(parameter_order)}
            defaultable = {*positional_only, *positional, *keyword_only}
            if any(name not in defaultable for name, _ in defaults) or any(
                name not in order for name, _ in annotations
            ):
                raise ValueError("invalid")
            if [order[name] for name, _ in defaults] != sorted(order[name] for name, _ in defaults):
                raise ValueError("invalid")
            if [order[name] for name, _ in annotations] != sorted(order[name] for name, _ in annotations):
                raise ValueError("invalid")
            positional_parameters = [*positional_only, *positional]
            positional_defaults = [
                positional_parameters.index(name)
                for name, _ in defaults
                if name in positional_parameters
            ]
            if positional_defaults and positional_defaults != list(
                range(positional_defaults[0], len(positional_parameters))
            ):
                raise ValueError("invalid")
            return_annotation = value["return_annotation"]
            if return_annotation is not None:
                return_annotation = expression(return_annotation)
            raises = tuple(
                item if item == "raise:bare" else expression(item)
                for item in string_list("raises")
            )
            if list(raises) != sorted(raises) or len(raises) != len(set(raises)):
                raise ValueError("invalid")
            functions[key] = FunctionSemantics(
                path=path_value,
                qualname=qualname,
                positional_only=positional_only,
                positional=positional,
                keyword_only=keyword_only,
                vararg=vararg,
                kwargs=kwargs,
                defaults=defaults,
                annotations=annotations,
                return_annotation=return_annotation,
                return_shapes=string_list("return_shapes"),
                raises=raises,
                decorators=expression_list(
                    "decorators", ordered=True, unique=False
                ),
                auth_gates=expression_list("auth_gates"),
                calls_resolved=string_list("calls_resolved"),
                calls_unresolved=expression_list("calls_unresolved"),
                config_ops=expression_list("config_ops"),
                concurrency_ops=expression_list("concurrency_ops"),
                metric_ops=expression_list("metric_ops"),
                sinks=expression_list("sinks"),
            )
            semantics = functions[key]
            if any(
                not re.fullmatch(r"(?:yield:)?[a-z][a-z0-9_]{0,63}", shape)
                for shape in semantics.return_shapes
            ) or any(
                not _safe_function_key(target)
                for target in semantics.calls_resolved
            ):
                raise ValueError("invalid")
        return (
            dict(sorted(functions.items())),
            hashlib.sha256(raw).hexdigest(),
            identity,
        )
    except Exception as exc:
        raise ValueError(stage) from exc


def _result(state: ResultState, summary: str, evidence: dict[str, JsonValue] | None = None) -> CheckResult:
    return CheckResult(name="semantic_diff", state=state, summary=summary, evidence=evidence or {})


def _invalid_path(output: Path, inputs: Sequence[Path]) -> bool:
    try:
        lexical = Path(os.path.abspath(output))
        if any(
            candidate.exists() and _is_linkish(candidate)
            for candidate in (lexical, *lexical.parents)
        ):
            return True
        output_key = output.resolve(strict=False)
        for item in inputs:
            if output_key == item.resolve(strict=False):
                return True
            if item.exists() and output.exists() and os.path.samefile(item, output):
                return True
            if item.is_dir() and (output_key == item.resolve() or item.resolve() in output_key.parents):
                return True
    except OSError:
        return True
    return False


def _bounded_json(
    value: object,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    if budget is None:
        budget = [200_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 32:
        raise ValueError("artifact")
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("artifact")
        return
    if type(value) is str:
        if len(value) > 16_384:
            raise ValueError("artifact")
        return
    if type(value) is list:
        if len(value) > 200_000:
            raise ValueError("artifact")
        for item in value:
            _bounded_json(item, depth=depth + 1, budget=budget)
        return
    if type(value) is dict:
        if len(value) > 200_000 or "generated_at" in value:
            raise ValueError("artifact")
        for key, item in value.items():
            if type(key) is not str or not key or len(key) > 255:
                raise ValueError("artifact")
            _bounded_json(item, depth=depth + 1, budget=budget)
        return
    raise ValueError("artifact")


def _validate_diff_artifact(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("artifact")
    validate_envelope(
        value, expected_name="bd.semantic-diff", supported_version=1
    )
    required = {
        "schema_name",
        "schema_version",
        "source_sha",
        "tool_version",
        "input_hashes",
        "generated_at",
        "engine",
        "changes",
        "summary",
        "locations",
        "cst",
    }
    if (
        set(value) != required
        or value["engine"] != "ast"
        or value["tool_version"] != "semantic-diff-1"
    ):
        raise ValueError("artifact")
    hashes = value["input_hashes"]
    if not isinstance(hashes, dict) or len(hashes) != 2:
        raise ValueError("artifact")
    before_keys = [
        key for key in hashes if key in {"before_tree", "before_snapshot"}
    ]
    after_keys = [
        key for key in hashes if key in {"after_tree", "after_snapshot"}
    ]
    if len(before_keys) != 1 or len(after_keys) != 1:
        raise ValueError("artifact")
    expected_source = hashlib.sha256(
        (
            hashes[before_keys[0]] + "\0" + hashes[after_keys[0]]
        ).encode("utf-8")
    ).hexdigest()
    if value["source_sha"] != expected_source:
        raise ValueError("artifact")

    changes = value["changes"]
    if not isinstance(changes, list) or len(changes) > 200_000:
        raise ValueError("artifact")
    policies = {"breaking", "risky", "informational", "unknown"}
    scalar_fields = {"vararg", "kwargs", "return_annotation"}
    pair_fields = {"defaults", "annotations"}
    vector_fields = {
        field.name
        for field in fields(FunctionSemantics)
        if field.name
        not in {
            "path",
            "qualname",
            *scalar_fields,
            *pair_fields,
        }
    }
    allowed_fields = scalar_fields | pair_fields | vector_fields | {"presence"}
    parameter_fields = {"positional_only", "positional", "keyword_only"}
    expression_fields = {
        "decorators",
        "auth_gates",
        "calls_unresolved",
        "config_ops",
        "concurrency_ops",
        "metric_ops",
        "sinks",
    }

    def canonical_expression(
        value: object, *, sensitive: bool = False
    ) -> bool:
        if type(value) is not str or not value or len(value) > 4096:
            return False
        try:
            return (
                _expr_text(
                    ast.parse(value, mode="eval").body,
                    sensitive=sensitive,
                )
                == value
            )
        except (MemoryError, RecursionError, SyntaxError, ValueError):
            return False

    seen_rows: set[tuple[str, str]] = set()
    previous: tuple[str, str] | None = None
    policy_counts = {policy: 0 for policy in policies}
    for row in changes:
        if not isinstance(row, dict) or set(row) != {
            "function",
            "field",
            "before",
            "after",
            "policy",
        }:
            raise ValueError("artifact")
        function = row["function"]
        field_name = row["field"]
        policy = row["policy"]
        if (
            not _safe_function_key(function)
            or field_name not in allowed_fields
            or policy not in policies
        ):
            raise ValueError("artifact")
        identity = (function, field_name)
        if identity in seen_rows or (previous is not None and identity < previous):
            raise ValueError("artifact")
        seen_rows.add(identity)
        previous = identity
        before_value, after_value = row["before"], row["after"]
        if field_name == "presence":
            if (
                type(before_value) is not bool
                or type(after_value) is not bool
                or before_value == after_value
            ):
                raise ValueError("artifact")
            expected_policy = "breaking"
        elif field_name in scalar_fields:
            if (
                before_value is not None
                and type(before_value) is not str
            ) or (
                after_value is not None
                and type(after_value) is not str
            ):
                raise ValueError("artifact")
            if before_value == after_value:
                raise ValueError("artifact")
            for item in (before_value, after_value):
                if item is None:
                    continue
                if field_name in {"vararg", "kwargs"}:
                    if not _safe_identifier(item):
                        raise ValueError("artifact")
                elif not canonical_expression(item):
                    raise ValueError("artifact")
            expected_policy = classify_change(
                field_name, before_value, after_value
            )
        elif field_name in pair_fields:
            for pair_values in (before_value, after_value):
                if not isinstance(pair_values, list) or any(
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(type(item) is not str for item in pair)
                    for pair in pair_values
                ):
                    raise ValueError("artifact")
                if len({pair[0] for pair in pair_values}) != len(pair_values):
                    raise ValueError("artifact")
                if any(
                    not _safe_identifier(pair[0])
                    or not canonical_expression(
                        pair[1],
                        sensitive=_SENSITIVE_NAME.search(pair[0]) is not None,
                    )
                    for pair in pair_values
                ):
                    raise ValueError("artifact")
            if before_value == after_value:
                raise ValueError("artifact")
            expected_policy = classify_change(
                field_name, before_value, after_value
            )
        else:
            if any(
                not isinstance(items, list)
                or any(type(item) is not str for item in items)
                for items in (before_value, after_value)
            ):
                raise ValueError("artifact")
            if before_value == after_value:
                raise ValueError("artifact")
            for items in (before_value, after_value):
                if field_name in parameter_fields:
                    if len(items) != len(set(items)) or any(
                        not _safe_identifier(item) for item in items
                    ):
                        raise ValueError("artifact")
                elif field_name == "return_shapes":
                    if (
                        items != sorted(items)
                        or len(items) != len(set(items))
                        or any(
                            re.fullmatch(
                                r"(?:yield:)?[a-z][a-z0-9_]{0,63}",
                                item,
                            )
                            is None
                            for item in items
                        )
                    ):
                        raise ValueError("artifact")
                elif field_name == "calls_resolved":
                    if (
                        items != sorted(items)
                        or len(items) != len(set(items))
                        or any(not _safe_function_key(item) for item in items)
                    ):
                        raise ValueError("artifact")
                elif field_name == "raises":
                    if (
                        items != sorted(items)
                        or len(items) != len(set(items))
                        or any(
                            item != "raise:bare"
                            and not canonical_expression(item)
                            for item in items
                        )
                    ):
                        raise ValueError("artifact")
                elif field_name in expression_fields:
                    if (
                        field_name != "decorators"
                        and (
                            items != sorted(items)
                            or len(items) != len(set(items))
                        )
                    ) or any(not canonical_expression(item) for item in items):
                        raise ValueError("artifact")
            expected_policy = classify_change(
                field_name, before_value, after_value
            )
        if policy != expected_policy:
            raise ValueError("artifact")
        _bounded_json(before_value)
        _bounded_json(after_value)
        policy_counts[policy] += 1

    summary = value["summary"]
    if not isinstance(summary, dict) or set(summary) != {
        "breaking",
        "risky",
        "informational",
        "unknown",
        "total",
    }:
        raise ValueError("artifact")
    if any(type(summary[key]) is not int or summary[key] < 0 for key in summary):
        raise ValueError("artifact")
    if summary["total"] != len(changes) or any(
        summary[policy] != policy_counts[policy] for policy in policies
    ):
        raise ValueError("artifact")

    locations = value["locations"]
    if not isinstance(locations, dict) or set(locations) != {"before", "after"}:
        raise ValueError("artifact")
    for side in ("before", "after"):
        rows = locations[side]
        if not isinstance(rows, list) or len(rows) > 200_000:
            raise ValueError("artifact")
        previous_function: str | None = None
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "function",
                "path",
                "line",
            }:
                raise ValueError("artifact")
            function, path, line = row["function"], row["path"], row["line"]
            if (
                not _safe_function_key(function)
                or not _safe_relative_path(path)
                or not function.startswith(f"{path}::")
                or type(line) is not int
                or line <= 0
                or (
                    previous_function is not None
                    and function <= previous_function
                )
            ):
                raise ValueError("artifact")
            previous_function = function

    cst = value["cst"]
    if not isinstance(cst, dict) or set(cst) != {
        "adapter",
        "positions",
        "status",
    }:
        raise ValueError("artifact")
    if cst["adapter"] not in {"none", "libcst"} or cst["status"] not in {
        "disabled",
        "available",
        "unavailable",
    }:
        raise ValueError("artifact")
    if (
        not isinstance(cst["positions"], list)
        or (cst["adapter"] == "none" and cst["status"] != "disabled")
        or (
            cst["adapter"] == "libcst"
            and cst["status"] not in {"available", "unavailable"}
        )
    ):
        raise ValueError("artifact")
    _bounded_json(cst["positions"])


def _diff_artifact_identity(value: dict[str, Any]) -> str:
    deterministic = dict(value)
    deterministic.pop("generated_at", None)
    payload = json.dumps(
        deterministic,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_check(path: Path) -> tuple[dict[str, Any], FileIdentity]:
    raw, identity = _read_stable_file(
        path, maximum=_MAX_ARTIFACT_BYTES, stage="check"
    )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("nonfinite")
            ),
            parse_float=lambda number: (
                (_ for _ in ()).throw(ValueError("nonfinite"))
                if not math.isfinite(float(number))
                else float(number)
            ),
        )
        if not isinstance(value, dict):
            raise ValueError("check")
        _validate_diff_artifact(value)
        return value, identity
    except Exception as exc:
        raise ValueError("check") from exc


def run_semantic_diff(
    *,
    before_tree: Path | None,
    after_tree: Path | None,
    before_snapshot: Path | None,
    after_snapshot: Path | None,
    output_path: Path,
    check_path: Path | None,
    gate: bool,
    cst_adapter: Literal["none", "libcst"],
) -> CheckResult:
    if sum(value is not None for value in (before_tree, before_snapshot)) != 1 or sum(value is not None for value in (after_tree, after_snapshot)) != 1:
        return _result(ResultState.ERROR, "semantic source invalid")
    if cst_adapter not in ("none", "libcst"):
        return _result(ResultState.ERROR, "semantic cst adapter invalid")
    inputs = [item for item in (before_tree, after_tree, before_snapshot, after_snapshot, check_path) if item is not None]
    if _invalid_path(output_path, inputs): return _result(ResultState.ERROR, "semantic artifact path invalid")
    try:
        before_tree_key = (
            os.path.normcase(os.path.abspath(before_tree))
            if before_tree is not None
            else None
        )
        after_tree_key = (
            os.path.normcase(os.path.abspath(after_tree))
            if after_tree is not None
            else None
        )
        before_snapshot_key = (
            os.path.normcase(os.path.abspath(before_snapshot))
            if before_snapshot is not None
            else None
        )
        after_snapshot_key = (
            os.path.normcase(os.path.abspath(after_snapshot))
            if after_snapshot is not None
            else None
        )
        same_tree_sources = bool(
            before_tree_key is not None
            and before_tree_key == after_tree_key
        )
        same_snapshot_sources = bool(
            before_snapshot_key is not None
            and before_snapshot_key == after_snapshot_key
        )
    except (OSError, RuntimeError):
        same_tree_sources = False
        same_snapshot_sources = False
    try:
        if before_tree is not None:
            before, before_hash, before_identity, before_locations = _capture_tree(
                before_tree
            )
        else:
            before, before_hash, before_identity = _load_snapshot(
                before_snapshot, "before_snapshot"  # type: ignore[arg-type]
            )
            before_locations = []
    except (
        MemoryError,
        RecursionError,
        ValueError,
        OSError,
        UnicodeError,
        SyntaxError,
    ) as exc:
        return _result(ResultState.ERROR, "before snapshot invalid" if str(exc) == "before_snapshot" else "before tree invalid", {"stage": "before_snapshot" if str(exc) == "before_snapshot" else "before_tree"})
    try:
        if same_tree_sources:
            after = before
            after_hash = before_hash
            after_identity = before_identity
            after_locations = before_locations
        elif same_snapshot_sources:
            after = before
            after_hash = before_hash
            after_identity = before_identity
            after_locations = []
        elif after_tree is not None:
            after, after_hash, after_identity, after_locations = _capture_tree(
                after_tree
            )
        else:
            after, after_hash, after_identity = _load_snapshot(
                after_snapshot, "after_snapshot"  # type: ignore[arg-type]
            )
            after_locations = []
    except (
        MemoryError,
        RecursionError,
        ValueError,
        OSError,
        UnicodeError,
        SyntaxError,
    ) as exc:
        return _result(ResultState.ERROR, "after snapshot invalid" if str(exc) == "after_snapshot" else "after tree invalid", {"stage": "after_snapshot" if str(exc) == "after_snapshot" else "after_tree"})
    try:
        changes = compare_semantics(before, after)
        counts = {policy: sum(row["policy"] == policy for row in changes) for policy in ("breaking", "risky", "informational", "unknown")}
        counts["total"] = len(changes)
        hashes = {("before_tree" if before_tree else "before_snapshot"): before_hash, ("after_tree" if after_tree else "after_snapshot"): after_hash}
    except (MemoryError, RecursionError, ValueError):
        return _result(ResultState.ERROR, "semantic comparison invalid")
    cst: dict[str, JsonValue] = {"adapter": cst_adapter, "positions": [], "status": "disabled" if cst_adapter == "none" else "available"}
    if cst_adapter == "libcst":
        try: importlib.import_module("libcst")
        except Exception: cst["status"] = "unavailable"
    source_sha = hashlib.sha256(
        (before_hash + "\0" + after_hash).encode("utf-8")
    ).hexdigest()
    artifact: dict[str, Any] = make_envelope(
        "bd.semantic-diff",
        1,
        source_sha,
        "semantic-diff-1",
        hashes,
    )
    artifact.update(
        {
            "engine": "ast",
            "changes": changes,
            "summary": counts,
            "locations": {
                "before": before_locations,
                "after": after_locations,
            },
            "cst": cst,
        }
    )
    try:
        _validate_diff_artifact(artifact)
        payload = (
            json.dumps(
                artifact,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        )
        if len(payload.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError("semantic artifact too large")
    except (MemoryError, RecursionError, TypeError, ValueError):
        return _result(ResultState.ERROR, "semantic artifact invalid")
    check_identity: FileIdentity | None = None
    if check_path is not None:
        try:
            checked, check_identity = _load_check(check_path)
        except (ValueError, OSError, UnicodeError):
            return _result(ResultState.ERROR, "semantic check invalid", {"stage": "check"})
        if _diff_artifact_identity(checked) != _diff_artifact_identity(artifact):
            return _result(ResultState.FAIL, "semantic artifact drift")

    def unstable_input() -> str | None:
        try:
            if before_tree is not None:
                if _tree_identity(before_tree) != before_identity:
                    return "before tree changed during analysis"
            else:
                _, current = _read_stable_file(
                    before_snapshot,  # type: ignore[arg-type]
                    maximum=_MAX_SNAPSHOT_BYTES,
                    stage="before_snapshot",
                )
                if current != before_identity:
                    return "semantic inputs changed during analysis"
            if same_tree_sources or same_snapshot_sources:
                pass
            elif after_tree is not None:
                if _tree_identity(after_tree) != after_identity:
                    return "after tree changed during analysis"
            else:
                _, current = _read_stable_file(
                    after_snapshot,  # type: ignore[arg-type]
                    maximum=_MAX_SNAPSHOT_BYTES,
                    stage="after_snapshot",
                )
                if current != after_identity:
                    return "semantic inputs changed during analysis"
            if check_path is not None:
                _, current = _read_stable_file(
                    check_path, maximum=_MAX_ARTIFACT_BYTES, stage="check"
                )
                if current != check_identity:
                    return "semantic inputs changed during analysis"
            return None
        except (OSError, ValueError):
            return "semantic inputs changed during analysis"

    parent_descriptor: int | None = None
    temporary_component: str | None = None
    temporary_reference: Path | None = None
    pending_component: str | None = None
    pending_reference: Path | None = None

    def cleanup_member(component: str | None, reference: Path | None) -> None:
        if component is None:
            return
        try:
            if parent_descriptor is not None and _UNLINK_SUPPORTS_DIR_FD:
                os.unlink(component, dir_fd=parent_descriptor)
            elif reference is not None:
                reference.unlink(missing_ok=True)
        except (OSError, TypeError):
            pass

    def replace_member(
        source_component: str,
        destination_component: str,
        source_reference: Path,
        destination_reference: Path,
    ) -> None:
        if parent_descriptor is not None and _REPLACE_SUPPORTS_DIR_FD:
            os.replace(
                source_component,
                destination_component,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            os.replace(source_reference, destination_reference)

    try:
        if drift := unstable_input():
            return _result(ResultState.ERROR, drift)
        absolute_output = Path(os.path.abspath(output_path))
        absolute_output.parent.mkdir(parents=True, exist_ok=True)
        parent_descriptor, parent_identity = _pin_directory(
            absolute_output.parent
        )
        temporary_component = (
            f".{absolute_output.name}.{secrets.token_hex(16)}.tmp"
        )
        pending_component = f"{temporary_component}.pending"
        pinned_parent = (
            Path(f"/proc/self/fd/{parent_descriptor}")
            if parent_descriptor is not None
            and Path(f"/proc/self/fd/{parent_descriptor}").is_dir()
            else absolute_output.parent
        )
        temporary_reference = pinned_parent / temporary_component
        pending_reference = pinned_parent / pending_component
        output_reference = pinned_parent / absolute_output.name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        if parent_descriptor is not None:
            output_descriptor = os.open(
                temporary_component,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        else:
            output_descriptor = os.open(temporary_reference, flags, 0o600)
        try:
            output_handle = os.fdopen(
                output_descriptor, "w", encoding="utf-8", newline=""
            )
        except BaseException:
            os.close(output_descriptor)
            raise
        with output_handle as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not _directory_is_stable(
            absolute_output.parent, parent_descriptor, parent_identity
        ):
            raise ValueError("output directory changed")
        replace_member(
            temporary_component,
            pending_component,
            temporary_reference,
            pending_reference,
        )
        temporary_component = None
        temporary_reference = None
        if drift := unstable_input():
            cleanup_member(pending_component, pending_reference)
            pending_component = None
            pending_reference = None
            return _result(ResultState.ERROR, drift)
        if not _directory_is_stable(
            absolute_output.parent, parent_descriptor, parent_identity
        ):
            raise ValueError("output directory changed")
        replace_member(
            pending_component,
            absolute_output.name,
            pending_reference,
            output_reference,
        )
        pending_component = None
        pending_reference = None
        if parent_descriptor is not None:
            try:
                os.fsync(parent_descriptor)
            except OSError as exc:
                if exc.errno not in {
                    errno.EBADF,
                    errno.EINVAL,
                    getattr(errno, "ENOTSUP", errno.EINVAL),
                }:
                    raise
        if not _directory_is_stable(
            absolute_output.parent, parent_descriptor, parent_identity
        ):
            return _result(ResultState.ERROR, "semantic artifact write failed")
    except BaseException:
        cleanup_member(temporary_component, temporary_reference)
        cleanup_member(pending_component, pending_reference)
        return _result(ResultState.ERROR, "semantic artifact write failed")
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
    state = ResultState.FAIL if gate and (counts["breaking"] or counts["unknown"]) else ResultState.ADVISORY
    return _result(state, "semantic changes detected" if changes else "no semantic changes", {"summary": counts})
