"""Census of httpx client constructions in the tracked application package (row 703).

The drift gate ``tests/test_row703_ssrf_transport_is_installed_everywhere.py``
judges whether EVERY ``httpx.Client`` / ``httpx.AsyncClient`` construction in
``bulk_downloader/`` is pinned through the one guarded-transport seam
(``bulk_downloader/ssrf_transport.py``).  The population that gate judges is
derived HERE, from the tree -- ``git ls-files`` for the file denominator and
``ast`` for the constructions -- never from the mutant spec the gate reconciles
against, and never from a line grep: a construction line does not carry its
``transport=`` keyword on the same line, so text cannot decide this.

Three outcomes, and UNKNOWN is not OK: when the population cannot be measured
(no git, no tracked files, an unparseable file) ``verdict`` returns
``("UNKNOWN", reason)`` and the gate fails on it.

What counts as pinned, by shape (anything else is unpinned):

* ``transport=guarded_transport(<POLICY>)`` where ``guarded_transport`` was
  imported from the seam module (``from bulk_downloader.ssrf_transport import
  guarded_transport``, aliased or not) or reached through a seam module alias
  (``ssrf_transport.guarded_transport(...)``), and ``<POLICY>`` is the seam's
  ``PINNED`` / ``PUBLIC_ONLY`` name or its string value;
* the shared builder ``_make_default_http_get`` in
  ``provider_resolve_impl/_common.py``, whose construction passes a local
  ``transport`` bound from ``_SSRFGuardedTransport_factory()`` -- the seam the
  PUBLIC_ONLY policy delegates to.

Honest limits: a construction reached through ``getattr`` or a name rebound at
runtime is invisible to a static census; the gate's fixture corpus pins the
recognised forms and their bare counterparts.
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

OK, FAIL, UNKNOWN = "OK", "FAIL", "UNKNOWN"

PACKAGE_PATHSPEC = "bulk_downloader/*.py"
SEAM_MODULE = "bulk_downloader.ssrf_transport"
FACTORY_NAME = "guarded_transport"
POLICY_NAMES = ("PINNED", "PUBLIC_ONLY")
POLICY_VALUES = ("pinned", "public-only")
CLIENT_NAMES = ("Client", "AsyncClient")
# httpx's module-level helpers build a throwaway client with the DEFAULT transport
# (none of them accepts transport=), so every call is an unpinned egress.
HELPER_NAMES = ("get", "post", "put", "patch", "delete", "head", "options", "request", "stream")
HELPER_EVIDENCE = "module-level helper builds a throwaway client with the default transport (no transport= is possible)"
SHARED_BUILDER_FILE = "bulk_downloader/provider_resolve_impl/_common.py"
SHARED_BUILDER_FUNCTION = "_make_default_http_get"
SHARED_BUILDER_FACTORY = "_SSRFGuardedTransport_factory"


class CensusUnavailable(RuntimeError):
    """The population cannot be measured; the caller must report UNKNOWN."""


@dataclass(frozen=True)
class Construction:
    file: str
    line: int
    kind: str          # "Client" | "AsyncClient" | "httpx.<helper>" for a module-level helper call
    pinned: bool
    policy: str        # "pinned" | "public-only" | "shared-builder" | "" when unpinned
    evidence: str      # why it was judged pinned / unpinned

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class Census:
    root: str
    files: Tuple[str, ...]                 # every tracked package file parsed
    constructions: Tuple[Construction, ...]

    @property
    def unpinned(self) -> Tuple[Construction, ...]:
        return tuple(c for c in self.constructions if not c.pinned)

    @property
    def pinned(self) -> Tuple[Construction, ...]:
        return tuple(c for c in self.constructions if c.pinned)

    def per_file(self) -> dict:
        counts: dict = {}
        for c in self.constructions:
            counts[c.file] = counts.get(c.file, 0) + 1
        return counts


def tracked_package_files(root: Path) -> Tuple[str, ...]:
    """Tracked ``bulk_downloader/**.py`` under ``root``, from git, or CensusUnavailable."""
    try:
        run = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", PACKAGE_PATHSPEC],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise CensusUnavailable(f"git could not run: {type(exc).__name__}: {exc}") from exc
    if run.returncode != 0:
        raise CensusUnavailable(
            f"git ls-files failed rc={run.returncode}: {run.stderr.strip()[:200]}")
    files = tuple(line for line in run.stdout.splitlines() if line.strip())
    if not files:
        raise CensusUnavailable(
            f"git ls-files returned no tracked {PACKAGE_PATHSPEC} under {root}")
    return files


def _bindings(tree: ast.AST):
    """Names bound to the httpx module, to httpx client classes, to httpx's
    module-level helpers, to the seam module, and to the seam factory,
    anywhere in the file."""
    httpx_aliases, client_names, seam_aliases, factory_names = set(), set(), set(), set()
    policy_names = set()
    helper_names: dict = {}   # local name -> httpx helper it is bound to
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "httpx":
                    httpx_aliases.add(alias.asname or "httpx")
                elif alias.name == SEAM_MODULE:
                    seam_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "httpx":
                for alias in node.names:
                    if alias.name in CLIENT_NAMES:
                        client_names.add(alias.asname or alias.name)
                    elif alias.name in HELPER_NAMES:
                        helper_names[alias.asname or alias.name] = alias.name
            elif module.split(".")[-1] == SEAM_MODULE.split(".")[-1]:
                for alias in node.names:
                    if alias.name == FACTORY_NAME:
                        factory_names.add(alias.asname or alias.name)
                    elif alias.name in POLICY_NAMES:
                        policy_names.add(alias.asname or alias.name)
            elif module == SEAM_MODULE.rsplit(".", 1)[0]:
                for alias in node.names:
                    if alias.name == SEAM_MODULE.rsplit(".", 1)[1]:
                        seam_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            if (isinstance(target, ast.Name) and isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id in httpx_aliases and value.attr in CLIENT_NAMES):
                client_names.add(target.id)
            elif (isinstance(target, ast.Name) and isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id in httpx_aliases and value.attr in HELPER_NAMES):
                helper_names[target.id] = value.attr
    return httpx_aliases, client_names, seam_aliases, factory_names, policy_names, helper_names


def _client_kind(call: ast.Call, httpx_aliases, client_names) -> Optional[str]:
    func = call.func
    if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id in httpx_aliases and func.attr in CLIENT_NAMES):
        return func.attr
    if isinstance(func, ast.Name) and func.id in client_names:
        return func.id if func.id in CLIENT_NAMES else "Client"
    return None


def _helper_kind(call: ast.Call, httpx_aliases, helper_names) -> Optional[str]:
    """``httpx.get(...)`` / ``_hx.stream(...)`` / a from-imported or rebound
    helper name: the module-level shape that can never carry a transport."""
    func = call.func
    if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and func.value.id in httpx_aliases and func.attr in HELPER_NAMES):
        return f"httpx.{func.attr}"
    if isinstance(func, ast.Name) and func.id in helper_names:
        return f"httpx.{helper_names[func.id]}"
    return None


def _policy_of(factory_call: ast.Call, policy_names) -> Optional[str]:
    if not factory_call.args:
        return None
    first = factory_call.args[0]
    if isinstance(first, ast.Name) and first.id in policy_names:
        return POLICY_VALUES[POLICY_NAMES.index(first.id)] if first.id in POLICY_NAMES else first.id
    if isinstance(first, ast.Attribute) and first.attr in POLICY_NAMES:
        return POLICY_VALUES[POLICY_NAMES.index(first.attr)]
    if isinstance(first, ast.Constant) and first.value in POLICY_VALUES:
        return str(first.value)
    return None


def _builder_pins(func: ast.FunctionDef) -> bool:
    """True when ``func`` binds ``transport_cls = _SSRFGuardedTransport_factory()``
    and ``transport = transport_cls(...)`` -- the shared builder's own pin."""
    cls_bound = transport_bound = False
    for node in ast.walk(func):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)):
            continue
        target, callee = node.targets[0].id, node.value.func
        if (target == "transport_cls" and isinstance(callee, ast.Name)
                and callee.id == SHARED_BUILDER_FACTORY):
            cls_bound = True
        if (target == "transport" and isinstance(callee, ast.Name)
                and callee.id == "transport_cls"):
            transport_bound = True
    return cls_bound and transport_bound


def _judge(call: ast.Call, *, rel: str, enclosing: Tuple[ast.FunctionDef, ...],
           seam_aliases, factory_names, policy_names) -> Tuple[bool, str, str]:
    transport = None
    for keyword in call.keywords:
        if keyword.arg == "transport":
            transport = keyword.value
    if transport is None:
        return False, "", "no transport= keyword on the construction"
    if isinstance(transport, ast.Call):
        callee = transport.func
        via = None
        if isinstance(callee, ast.Name) and callee.id in factory_names:
            via = callee.id
        elif (isinstance(callee, ast.Attribute) and callee.attr == FACTORY_NAME
              and isinstance(callee.value, ast.Name) and callee.value.id in seam_aliases):
            via = f"{callee.value.id}.{callee.attr}"
        if via is not None:
            policy = _policy_of(transport, policy_names)
            if policy is None:
                return False, "", f"transport={via}(...) names no recognised policy"
            return True, policy, f"transport={via}({policy})"
        return False, "", "transport= is a call that is not the seam factory"
    builder = [f for f in enclosing if f.name == SHARED_BUILDER_FUNCTION]
    if (isinstance(transport, ast.Name) and transport.id == "transport"
            and rel == SHARED_BUILDER_FILE and builder and _builder_pins(builder[0])):
        return True, "shared-builder", f"{SHARED_BUILDER_FUNCTION} binds transport from {SHARED_BUILDER_FACTORY}()"
    return False, "", "transport= is not the seam factory nor the shared builder's pin"


def constructions_in(rel: str, source: str) -> Tuple[Construction, ...]:
    """Every httpx client construction in one file's source, judged."""
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as exc:
        raise CensusUnavailable(f"{rel} does not parse: {exc}") from exc
    httpx_aliases, client_names, seam_aliases, factory_names, policy_names, helper_names = _bindings(tree)
    if not httpx_aliases and not client_names and not helper_names:
        return ()
    found = []
    # Walk with the innermost enclosing function known, for the builder rule.
    stack: list = [(tree, ())]
    while stack:
        node, enclosing = stack.pop()
        for child in ast.iter_child_nodes(node):
            inner = enclosing + (child,) if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else enclosing
            if isinstance(child, ast.Call):
                kind = _client_kind(child, httpx_aliases, client_names)
                if kind is not None:
                    pinned, policy, evidence = _judge(
                        child, rel=rel, enclosing=enclosing, seam_aliases=seam_aliases,
                        factory_names=factory_names, policy_names=policy_names)
                    found.append(Construction(rel, child.lineno, kind, pinned, policy, evidence))
                helper = _helper_kind(child, httpx_aliases, helper_names)
                if helper is not None:
                    found.append(Construction(rel, child.lineno, helper, False, "", HELPER_EVIDENCE))
            stack.append((child, inner))
    return tuple(sorted(found, key=lambda c: (c.file, c.line)))


def census(root: Path) -> Census:
    """The judged population under ``root`` (a git checkout), or CensusUnavailable."""
    root = Path(root)
    files = tracked_package_files(root)
    constructions: list = []
    for rel in files:
        path = root / rel
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CensusUnavailable(f"{rel} is tracked but unreadable: {exc}") from exc
        constructions.extend(constructions_in(rel, source))
    return Census(str(root), files, tuple(constructions))


def verdict(root: Path) -> Tuple[str, str, Optional[Census]]:
    """(state, detail, census).  OK only when the population is measured,
    nonzero, and every construction is pinned.  UNKNOWN when it cannot be
    measured.  FAIL names every unpinned construction."""
    try:
        result = census(root)
    except CensusUnavailable as exc:
        return UNKNOWN, str(exc), None
    if not result.constructions:
        return UNKNOWN, (
            f"zero httpx client constructions found across {len(result.files)} tracked "
            f"{PACKAGE_PATHSPEC} files -- an empty population proves nothing"), result
    if result.unpinned:
        lines = [f"{c.where} {c.kind}: {c.evidence}" for c in result.unpinned]
        return FAIL, (
            f"{len(result.unpinned)} of {len(result.constructions)} httpx client constructions "
            f"across {len(result.per_file())} files are NOT pinned through the guarded transport:\n  "
            + "\n  ".join(lines)), result
    return OK, (
        f"{len(result.constructions)} constructions across {len(result.per_file())} files, "
        f"all pinned"), result


def main(argv: Optional[Iterable[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="git checkout to census")
    args = parser.parse_args(list(argv) if argv is not None else None)
    state, detail, result = verdict(Path(args.root).resolve())
    if result is not None:
        for c in result.constructions:
            print(f"{'PINNED  ' if c.pinned else 'UNPINNED'} {c.where} {c.kind} {c.policy or '-'} {c.evidence}")
    print(f"{state}: {detail}")
    return {OK: 0, FAIL: 1}.get(state, 2)


if __name__ == "__main__":
    raise SystemExit(main())
