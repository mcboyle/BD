#!/usr/bin/env python3
"""DECOMP-R0 — import-graph regression gate.

Freezes the intended internal import-edge set of the product (bulk_downloader/ +
tools/) and asserts a decomposition cut adds no edge outside it. The complement
to the surface-lock / route_map invariants: those prove *nothing left*, this
proves *nothing new crept in* (accidental coupling, lazy-accessor sprawl — H-14).

  python3 tools/decomp/import_graph_gate.py --check    # exit 1 on any NEW edge
  python3 tools/decomp/import_graph_gate.py --update    # re-freeze the baseline
  python3 tools/decomp/import_graph_gate.py --list      # print edge counts

A NEW edge present in the live graph but not the baseline FAILS --check. An edge
removed by a cut (coupling deleted — a good thing) is reported but does not fail;
re-freeze with --update to keep the baseline tight. Declaring an intended new
edge = running --update in the same cut, the way a guard-SHA change is declared.

Fail-closed contract (CLAUDE.md 0). The edge set is produced by
tools/dependency_graph.py, whose `_parse()` returns None on SyntaxError and whose
`build()` then skips the file. A file the parser cannot read therefore
contributes *no edges*, and this gate used to report `PASS` and exit 0 over that
silently reduced denominator — truthfully and uselessly. Two consequences are
now enforced instead:

  * Unknown is a third state and it fails. Every mode (--check / --update /
    --list) first parses the same file set the graph walks; if any file raises,
    the tool exits non-zero naming the count and the files, and touches nothing.
  * --update refuses to SHRINK the baseline unless --shrink is passed. Baking a
    reduced edge set in is how a temporarily blind gate becomes permanently
    blind, so dropping frozen edges is an explicit operator declaration —
    exactly like a guard-SHA change.

Design note: this tool deliberately loads tools/dependency_graph.py *by path*
(not `from tools.dependency_graph import build`) so the gate contributes no
static import edge to the very graph it measures — the baseline stays the pure
product surface, and DEPENDENCY_GRAPH.json is unperturbed by adding this file.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

_BASELINE_VERSION = "1"


class UnparseableSourceError(RuntimeError):
    """Raised when a file in the graph's own denominator will not parse.

    The graph builder skips such a file, so any answer computed over it is an
    answer about a tree that is not the tree. Refuse rather than report."""

    def __init__(self, files: list[str], denominator: str):
        self.files = list(files)
        self.denominator = denominator
        super().__init__(
            f"{len(self.files)} file(s) unparseable -- refusing "
            f"(denominator: {denominator}); the import graph would silently omit "
            "their edges:\n  " + "\n  ".join(self.files)
        )


class BaselineShrinkError(RuntimeError):
    """Raised when --update would drop edges the baseline currently freezes."""

    def __init__(self, removed: list[tuple[str, str]]):
        self.removed = list(removed)
        shown = self.removed[:20]
        more = len(self.removed) - len(shown)
        tail = f"\n  ... (+{more} more)" if more > 0 else ""
        super().__init__(
            f"refusing to shrink the baseline: {len(self.removed)} frozen edge(s) "
            "are absent from the live graph. Re-run with --shrink to declare the "
            "removal deliberately:\n  "
            + "\n  ".join(f"- {s} -> {d}" for s, d in shown)
            + tail
        )


def _repo_root() -> Path:
    # tools/decomp/import_graph_gate.py -> repo root is two parents up.
    return Path(__file__).resolve().parents[2]


def _baseline_path(root: Path | None = None) -> Path:
    root = root or _repo_root()
    return root / "tools" / "decomp" / "import_graph_baseline.json"


def _load_dependency_graph(root: Path | None = None):
    """Load tools/dependency_graph.py by path (no static import edge)."""
    root = root or _repo_root()
    dep = root / "tools" / "dependency_graph.py"
    if not dep.exists():
        raise FileNotFoundError(f"{dep} not found")
    spec = importlib.util.spec_from_file_location("_r0_dependency_graph", dep)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _test_files(root: Path) -> list[Path]:
    """tests/*.py, pruned exactly as dependency_graph._py_files prunes.

    v3.66.889: tests/ was outside this gate's denominator entirely. MEASURED at
    v3.66.888: the baseline held 1618 edges over 506 source keys, and including
    tests/ adds 2132 more from 1234 files (0 parse failures) -- 57% of the real
    internal import surface was ungated.

    THE ENUMERATOR WIDENS HERE, NOT IN `dependency_graph._py_files`, and that is
    deliberate. That walker also feeds DEPENDENCY_GRAPH.json AND the config
    sub-graph, so widening it there would make test files count as config
    readers/writers -- a semantic change to a DIFFERENT gate's denominator,
    riding along invisibly. Only this gate's file list grows; the predicate
    that decides what counts as an edge stays single-sourced in
    dependency_graph._internal_imports.
    """
    files: list[Path] = []
    for dirpath, _dirs, names in os.walk(root / "tests"):
        if "__pycache__" in dirpath:
            continue
        for nm in names:
            if nm.endswith(".py"):
                files.append(Path(dirpath) / nm)
    return sorted(files)


def _tests_out_map(root: Path, dep) -> dict[str, list[str]]:
    """Edges out of tests/, derived with the BUILDER's parse and predicate.

    Reuses dep._parse / dep._internal_imports / dep._bd_mods / dep._tool_stems
    rather than reimplementing them, so the only difference between this and
    `dep.build()`'s edge loop is which files it runs over. A forked predicate
    would be two denominators that drift -- CLAUDE.md section 8's "two
    populations named tools" defect, in a gate.
    """
    needed = ("_parse", "_internal_imports", "_bd_mods", "_tool_stems")
    if dep is None or not all(callable(getattr(dep, n, None)) for n in needed):
        # Unknown is a third state. Returning {} here would silently narrow the
        # denominator back to the pre-889 set while still reporting a verdict.
        raise UnparseableSourceError(
            "dependency_graph is missing one of %s, so tests/ edges cannot be "
            "derived with the builder's own predicate; refusing to report a "
            "verdict over a narrower denominator than the baseline declares"
            % (needed,))
    bd_mods, tool_stems = dep._bd_mods(root), dep._tool_stems(root)
    out: dict[str, set[str]] = {}
    for p in _test_files(root):
        tree, _reason = dep._parse(p)
        if tree is None:
            # assert_fully_parseable already fails closed over this same set;
            # skipping here would be unreachable, and silent if it were not.
            continue
        rp = p.relative_to(root).as_posix()
        for nid in dep._internal_imports(tree, bd_mods, tool_stems):
            if nid != rp:
                out.setdefault(rp, set()).add(nid)
    return {k: sorted(v) for k, v in out.items()}


def _source_files(root: Path, dep=None) -> tuple[list[Path], str]:
    """The files the graph walks, and the name of where that list came from.

    Preferring `dependency_graph._py_files` keeps this denominator *identical*
    to the builder's by construction rather than by assertion. The fallback is a
    faithful copy of that walk, used only if the private helper is renamed; it
    is never a narrower set, and the label says which one ran."""
    if dep is not None and callable(getattr(dep, "_py_files", None)):
        return (sorted(list(Path(p) for p in dep._py_files(root)) + _test_files(root)),
                "dependency_graph._py_files + tests/")
    files: list[Path] = list(_test_files(root))
    for rel in ("bulk_downloader", "tools"):
        for dirpath, _dirs, names in os.walk(root / rel):
            if "__pycache__" in dirpath:
                continue
            for nm in names:
                if nm.endswith(".py"):
                    files.append(Path(dirpath) / nm)
    return sorted(files), "import_graph_gate fallback walk"


def unparseable_files(root: Path | None = None, dep=None) -> tuple[list[str], str]:
    """(repo-relative paths that will NOT parse, denominator label).

    Mirrors `dependency_graph._parse`: same read (utf-8, errors="replace"), same
    `ast.parse`. An unreadable file counts as unparseable too — unknown fails."""
    root = root or _repo_root()
    bad: list[str] = []
    files, label = _source_files(root, dep)
    for p in files:
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError):
            bad.append(p.relative_to(root).as_posix())
    return sorted(bad), label


def assert_fully_parseable(root: Path | None = None, dep=None) -> str:
    """Raise UnparseableSourceError unless the whole denominator parses."""
    root = root or _repo_root()
    bad, label = unparseable_files(root, dep)
    if bad:
        raise UnparseableSourceError(bad, label)
    return label


def current_out_map(root: Path | None = None) -> dict:
    """The live directed internal edge map {src: [dst, ...]} (== package.out).

    Refuses (raises UnparseableSourceError) rather than returning an edge map
    computed over a denominator the parser could not fully read. This is the one
    choke point every mode funnels through, so --check, --update and --list all
    inherit the fail-closed behaviour."""
    root = root or _repo_root()
    dep = _load_dependency_graph(root)
    assert_fully_parseable(root, dep)
    g = dep.build(root)
    merged = {k: sorted(v) for k, v in g["package"]["out"].items()}
    # tests/ edges, same predicate, wider file list. A test file cannot collide
    # with a bulk_downloader/ or tools/ key, so this is a disjoint union.
    for src, dsts in _tests_out_map(root, dep).items():
        merged[src] = sorted(set(merged.get(src, [])) | set(dsts))
    return merged


def current_edge_set(root: Path | None = None) -> set[tuple[str, str]]:
    return {(s, d) for s, lst in current_out_map(root).items() for d in lst}


def load_baseline(root: Path | None = None) -> dict:
    p = _baseline_path(root)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} missing — run `python3 tools/decomp/import_graph_gate.py --update`."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def baseline_edge_set(base: dict) -> set[tuple[str, str]]:
    return {(s, d) for s, lst in base["edges"].items() for d in lst}


def compare_edges(
    baseline: set[tuple[str, str]], current: set[tuple[str, str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (new, removed) — new = current\\baseline, removed = baseline\\current."""
    new = sorted(current - baseline)
    removed = sorted(baseline - current)
    return new, removed


def check(root: Path | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    root = root or _repo_root()
    base = baseline_edge_set(load_baseline(root))
    return compare_edges(base, current_edge_set(root))


def _serialize(out_map: dict) -> str:
    edges = {k: sorted(v) for k, v in out_map.items()}
    obj = {
        "baseline_version": _BASELINE_VERSION,
        "edge_count": sum(len(v) for v in edges.values()),
        "edges": edges,
    }
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def write_baseline(root: Path | None = None, allow_shrink: bool = False) -> int:
    """Re-freeze the baseline. Raises UnparseableSourceError if the tree cannot
    be fully read, and BaselineShrinkError if the rewrite would drop frozen
    edges without `allow_shrink`. Nothing is written on either refusal."""
    root = root or _repo_root()
    out_map = current_out_map(root)          # refuses on unparseable input
    if not allow_shrink and _baseline_path(root).exists():
        cur = {(s, d) for s, lst in out_map.items() for d in lst}
        _new, removed = compare_edges(baseline_edge_set(load_baseline(root)), cur)
        if removed:
            raise BaselineShrinkError(removed)
    content = _serialize(out_map)
    _baseline_path(root).write_text(content, encoding="utf-8")
    return sum(len(v) for v in out_map.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="fail (exit 1) if the live graph adds any edge outside the baseline")
    g.add_argument("--update", action="store_true",
                   help="re-freeze the baseline from the live graph (declare intended edges)")
    g.add_argument("--list", action="store_true",
                   help="print baseline + live edge counts")
    ap.add_argument("--shrink", action="store_true",
                    help="with --update: declare that dropping frozen edges is "
                         "intended (without it, a shrinking re-freeze is refused)")
    args = ap.parse_args(argv)
    root = _repo_root()

    if args.shrink and not args.update:
        ap.error("--shrink is only meaningful with --update")

    if args.update:
        try:
            n = write_baseline(root, allow_shrink=args.shrink)
        except (UnparseableSourceError, BaselineShrinkError) as e:
            print(f"FAIL: {e}", file=sys.stderr)
            print("baseline NOT rewritten.", file=sys.stderr)
            return 1
        print(f"baseline re-frozen: {n} edges -> {_baseline_path(root).relative_to(root)}")
        return 0

    if args.list:
        try:
            cur = current_out_map(root)
        except UnparseableSourceError as e:
            print(f"FAIL: {e}", file=sys.stderr)
            return 1
        cur_n = sum(len(v) for v in cur.values())
        try:
            base = load_baseline(root)
            print(f"baseline: {base['edge_count']} edges | live: {cur_n} edges")
        except FileNotFoundError as e:
            print(str(e))
            return 1
        return 0

    # default: --check
    #
    # live_edges is computed HERE, inside the try. It used to be computed in
    # the PASS f-string below, i.e. outside it -- a second full graph rebuild
    # whose UnparseableSourceError had no handler. Observed, not theorised: a
    # source file changing between the two builds produced a raw traceback
    # instead of this gate's designed FAIL, defeating the one-choke-point
    # property the fail-closed path depends on.
    try:
        new, removed = check(root)
        live_edges = sum(len(v) for v in current_out_map(root).values())
    except UnparseableSourceError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        print("The gate cannot see part of its own denominator, so it reports "
              "nothing. Fix the file(s) above, then re-run.", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(str(e))
        return 1
    if removed:
        print(f"note: {len(removed)} baseline edge(s) no longer present "
              f"(coupling removed — re-freeze with --update to tighten):")
        for s, d in removed:
            print(f"  - {s} -> {d}")
    if new:
        print(f"FAIL: {len(new)} NEW import edge(s) outside the frozen baseline:")
        for s, d in new:
            print(f"  + {s} -> {d}")
        print("If intended, re-freeze in the SAME cut: "
              "`python3 tools/decomp/import_graph_gate.py --update`")
        return 1
    print(f"PASS: no new import edges (baseline holds, {live_edges} edges).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
