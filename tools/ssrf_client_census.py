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


# ==========================================================================
# Row 805 -- the EVERY-TRANSPORT egress census.
#
# The httpx census above judges one transport.  ``bulk_downloader/`` also
# reaches the network through ``urllib.request`` (``urlopen`` / an opener's
# ``.open``), ``requests`` and ``aiohttp``, and those sites were outside every
# census, so a new one could be added with nothing in the tree noticing.
#
# This census derives the EGRESS population from the tree the same way (git for
# the file denominator, ``ast`` for the sites) and reconciles it against ONE
# in-tree declaration, ``bulk_downloader/ssrf_egress_exemptions.py``, which
# must account for every site by ``<file>::<enclosing function>`` with a
# nonempty reason.  A site the declaration does not name is UNACCOUNTED and
# fails; a declaration entry no site matches is STALE and fails; an
# unmeasurable population is UNKNOWN, never OK.
#
# ``getattr(requests, verb)(url)`` IS censused -- the verb is dynamic but the
# module is named at the call site.  What this cannot see is a transport
# reached through a name rebound at runtime, and a raw socket egress: the
# censused transports are urllib.request, requests and aiohttp.
# ==========================================================================

EXEMPTION_REL = "bulk_downloader/ssrf_egress_exemptions.py"
EXEMPTION_MAPPING = "ACCOUNTED"
URLLIB_REQUEST_MODULE = "urllib.request"
URLLIB_OPENERS = ("urlopen",)
REQUESTS_MODULE = "requests"
REQUESTS_CALLABLES = ("get", "post", "put", "patch", "delete", "head", "options", "request", "Session")
OPENER_BUILDER = "build_opener"
OPENER_DISPATCH = "open"
SESSION_BASE = "Session"
MODULE_SCOPE = "<module>"
AIOHTTP_MODULE = "aiohttp"
AIOHTTP_CALLABLES = ("ClientSession", "request")
DYNAMIC_BUILTIN = "getattr"
DYNAMIC_DISPATCH = "<dynamic>"
ACCOUNTING_KINDS = ("guarded", "exempt")


@dataclass(frozen=True)
class EgressSite:
    file: str
    line: int
    transport: str     # "urllib.request" | "requests" | "aiohttp"
    dispatch: str      # the call as censused, e.g. "urlopen" / "requests.get"
    function: str      # qualified owner (Class.method / function), or "<module>"

    @property
    def key(self) -> str:
        return f"{self.file}::{self.function}"

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass(frozen=True)
class EgressCensus:
    root: str
    files: Tuple[str, ...]
    sites: Tuple[EgressSite, ...]
    accounted: Tuple[Tuple[str, str, str], ...]   # (key, kind, reason) from the tree declaration

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(sorted({s.key for s in self.sites}))

    @property
    def declared_keys(self) -> Tuple[str, ...]:
        return tuple(sorted({key for key, _kind, _reason in self.accounted}))

    @property
    def unaccounted(self) -> Tuple[EgressSite, ...]:
        declared = set(self.declared_keys)
        return tuple(s for s in self.sites if s.key not in declared)

    @property
    def stale(self) -> Tuple[str, ...]:
        live = set(self.keys)
        return tuple(key for key in self.declared_keys if key not in live)


def _egress_bindings(tree: ast.AST):
    """Names in one file bound to the non-httpx transports this census judges."""
    urllib_request_aliases = set()      # module aliases: urllib.request / _ur / _u
    urlopen_names = set()               # bare names bound to urllib.request.urlopen
    builder_names = set()               # bare names bound to urllib.request.build_opener
    opener_names = set()                # names bound to a built opener (its .open is egress)
    requests_aliases, requests_callables = set(), {}
    aiohttp_aliases, aiohttp_callables = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == URLLIB_REQUEST_MODULE:
                    urllib_request_aliases.add(alias.asname or URLLIB_REQUEST_MODULE)
                elif alias.name == REQUESTS_MODULE:
                    requests_aliases.add(alias.asname or REQUESTS_MODULE)
                elif alias.name == AIOHTTP_MODULE:
                    aiohttp_aliases.add(alias.asname or AIOHTTP_MODULE)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == URLLIB_REQUEST_MODULE:
                for alias in node.names:
                    if alias.name in URLLIB_OPENERS:
                        urlopen_names.add(alias.asname or alias.name)
                    elif alias.name == OPENER_BUILDER:
                        builder_names.add(alias.asname or alias.name)
            elif module == "urllib":
                for alias in node.names:
                    if alias.name == "request":
                        urllib_request_aliases.add(alias.asname or "request")
            elif module == REQUESTS_MODULE:
                for alias in node.names:
                    if alias.name in REQUESTS_CALLABLES:
                        requests_callables[alias.asname or alias.name] = alias.name
            elif module == AIOHTTP_MODULE:
                for alias in node.names:
                    if alias.name in AIOHTTP_CALLABLES:
                        aiohttp_callables[alias.asname or alias.name] = alias.name
    # An opener is built once and reached later by name; bind those names in a
    # second pass, so an alias imported below its use is still seen.
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)):
            continue
        callee = node.value.func
        built = (isinstance(callee, ast.Name) and callee.id in builder_names) or (
            isinstance(callee, ast.Attribute) and callee.attr == OPENER_BUILDER
            and _dotted_name(callee.value) in urllib_request_aliases)
        if built:
            opener_names.add(node.targets[0].id)
    return (urllib_request_aliases, urlopen_names, opener_names,
            requests_aliases, requests_callables, aiohttp_aliases, aiohttp_callables)


def _egress_kind(call: ast.Call, bindings) -> Optional[Tuple[str, str]]:
    """``(transport, dispatch)`` when this call leaves the process, else None."""
    (urllib_aliases, urlopen_names, opener_names,
     requests_aliases, requests_callables, aiohttp_aliases, aiohttp_callables) = bindings
    func = call.func
    if isinstance(func, ast.Call):
        # ``getattr(requests, verb)(url)`` -- the verb is chosen at runtime, but
        # the transport is not: the module is named right here, so the site is
        # censusable even though the method is not.
        inner = func.func
        if isinstance(inner, ast.Name) and inner.id == DYNAMIC_BUILTIN and func.args:
            holder = _dotted_name(func.args[0])
            if holder is not None:
                if holder in requests_aliases:
                    return REQUESTS_MODULE, f"requests.{DYNAMIC_DISPATCH}"
                if holder in urllib_aliases:
                    return URLLIB_REQUEST_MODULE, f"{holder}.{DYNAMIC_DISPATCH}"
                if holder in aiohttp_aliases:
                    return AIOHTTP_MODULE, f"aiohttp.{DYNAMIC_DISPATCH}"
        return None
    if isinstance(func, ast.Name):
        if func.id in urlopen_names:
            return URLLIB_REQUEST_MODULE, func.id
        if func.id in requests_callables:
            return REQUESTS_MODULE, f"requests.{requests_callables[func.id]}"
        if func.id in aiohttp_callables:
            return AIOHTTP_MODULE, f"aiohttp.{aiohttp_callables[func.id]}"
        return None
    if isinstance(func, ast.Attribute):
        owner = func.value
        dotted = _dotted_name(owner)
        if dotted is not None:
            if dotted in opener_names and func.attr == OPENER_DISPATCH:
                return URLLIB_REQUEST_MODULE, f"{dotted}.{OPENER_DISPATCH}"
            if dotted in urllib_aliases and func.attr in URLLIB_OPENERS:
                return URLLIB_REQUEST_MODULE, f"{dotted}.{func.attr}"
            if dotted in requests_aliases and func.attr in REQUESTS_CALLABLES:
                return REQUESTS_MODULE, f"requests.{func.attr}"
            if dotted in aiohttp_aliases and func.attr in AIOHTTP_CALLABLES:
                return AIOHTTP_MODULE, f"aiohttp.{func.attr}"
    return None


def _dotted_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = _dotted_name(node.value)
        return None if head is None else f"{head}.{node.attr}"
    return None


def _session_subclass(node: ast.AST, bindings) -> Optional[str]:
    """``class X(requests.Session)`` -- a transport of its own, whose ``request``
    override is the dispatch every caller reaches."""
    (_urllib_aliases, _urlopen_names, _opener_names,
     requests_aliases, requests_callables, _aiohttp_aliases, _aiohttp_callables) = bindings
    if not isinstance(node, ast.ClassDef):
        return None
    for base in node.bases:
        if (isinstance(base, ast.Attribute) and base.attr == SESSION_BASE
                and _dotted_name(base.value) in requests_aliases):
            return f"requests.{SESSION_BASE} subclass"
        if isinstance(base, ast.Name) and requests_callables.get(base.id) == SESSION_BASE:
            return f"requests.{SESSION_BASE} subclass"
    return None


def egress_sites_in(rel: str, source: str) -> Tuple[EgressSite, ...]:
    """Every non-httpx egress site in one file's source, with the qualified name
    of the function or class that owns it."""
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as bad_syntax:
        raise CensusUnavailable(
            f"{rel} does not parse for the egress census: {bad_syntax}") from bad_syntax
    bindings = _egress_bindings(tree)
    if not any(bindings):
        return ()
    found = []
    stack: list = [(tree, MODULE_SCOPE)]
    while stack:
        node, qualname = stack.pop()
        for child in ast.iter_child_nodes(node):
            inner = qualname
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = child.name if qualname == MODULE_SCOPE else f"{qualname}.{child.name}"
            subclass = _session_subclass(child, bindings)
            if subclass is not None:
                found.append(EgressSite(rel, child.lineno, REQUESTS_MODULE, subclass, inner))
            if isinstance(child, ast.Call):
                kind = _egress_kind(child, bindings)
                if kind is not None:
                    transport, dispatch = kind
                    found.append(EgressSite(rel, child.lineno, transport, dispatch, qualname))
            stack.append((child, inner))
    return tuple(sorted(found, key=lambda s: (s.file, s.line)))


def declared_accounting(root: Path) -> Tuple[Tuple[str, str, str], ...]:
    """The in-tree accounting declaration, read from the TREE by ``ast`` (never
    imported, so the census of a fixture tree never runs that tree's code)."""
    path = Path(root) / EXEMPTION_REL
    if not path.exists():
        # Measurable, and accounting for nothing: the caller reports every site
        # as UNACCOUNTED and names it.  Absence of the declaration is a FAIL,
        # not an UNKNOWN -- the population was read from the tree either way.
        return ()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=EXEMPTION_REL)
    except (OSError, SyntaxError) as exc:
        raise CensusUnavailable(f"{EXEMPTION_REL} cannot be read: {exc}") from exc
    mapping = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == EXEMPTION_MAPPING):
            mapping = node.value
    if not isinstance(mapping, ast.Dict):
        raise CensusUnavailable(
            f"{EXEMPTION_REL} declares no {EXEMPTION_MAPPING} dict literal")
    entries = []
    for key_node, value_node in zip(mapping.keys, mapping.values):
        try:
            key = ast.literal_eval(key_node)
            value = ast.literal_eval(value_node)
        except ValueError as exc:
            raise CensusUnavailable(
                f"{EXEMPTION_MAPPING} holds an entry that is not a literal: {exc}") from exc
        if not isinstance(key, str) or not isinstance(value, (tuple, list)) or len(value) != 2:
            raise CensusUnavailable(
                f"{EXEMPTION_MAPPING}[{key!r}] is not (kind, reason)")
        kind, reason = value
        if kind not in ACCOUNTING_KINDS:
            raise CensusUnavailable(
                f"{EXEMPTION_MAPPING}[{key!r}] kind {kind!r} is not one of {ACCOUNTING_KINDS}")
        if not isinstance(reason, str) or not reason.strip():
            raise CensusUnavailable(
                f"{EXEMPTION_MAPPING}[{key!r}] carries no reason")
        entries.append((key, kind, reason))
    return tuple(entries)


def egress_census(root: Path) -> EgressCensus:
    """The judged non-httpx egress population under ``root``, or CensusUnavailable."""
    root = Path(root)
    files = tracked_package_files(root)
    sites: list = []
    for rel in files:
        path = root / rel
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CensusUnavailable(f"{rel} is tracked but unreadable: {exc}") from exc
        sites.extend(egress_sites_in(rel, source))
    return EgressCensus(str(root), files, tuple(sites), declared_accounting(root))


def egress_verdict(root: Path) -> Tuple[str, str, Optional[EgressCensus]]:
    """(state, detail, census).  OK only when the population is measured,
    nonzero, every site is accounted for in the tree and no declared entry is
    stale.  UNKNOWN when the population or the declaration cannot be read."""
    try:
        result = egress_census(root)
    except CensusUnavailable as why:
        return UNKNOWN, str(why), None
    if not result.sites:
        return UNKNOWN, (
            f"zero non-httpx egress sites found across {len(result.files)} tracked "
            f"{PACKAGE_PATHSPEC} files -- an empty population proves nothing"), result
    problems = []
    if result.unaccounted:
        problems.append(
            f"{len(result.unaccounted)} of {len(result.sites)} egress sites are NOT accounted for in "
            f"{EXEMPTION_REL}:\n  " + "\n  ".join(
                f"{s.where} {s.transport} {s.dispatch} (key {s.key})" for s in result.unaccounted))
    if result.stale:
        problems.append(
            f"{len(result.stale)} declared {EXEMPTION_MAPPING} entries match no egress site "
            f"(stale):\n  " + "\n  ".join(result.stale))
    if problems:
        return FAIL, "\n".join(problems), result
    return OK, (
        f"{len(result.sites)} non-httpx egress sites across {len(set(s.file for s in result.sites))} "
        f"files, all accounted for by {len(result.declared_keys)} in-tree entries"), result
