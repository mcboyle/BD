"""Row 1038 -- cold start, attributed to the module that cost it.

Before this module the repo could time a cold start but never explain one. ``tools/nuitka_eval.py``
measures a single ``startup_s``: seconds from launching the COMPILED binary to its first ``/health``
answer. ``perf_lab`` profiles a process that is already up -- RSS, child processes, tracemalloc,
interpreter stats. Neither can say which import spent the time, so an import regression arrives as
"startup got slower" with no owner and no next step.

CPython already measures this on every run: ``python -X importtime`` prints self time, cumulative
time and the import TREE to stderr. Nothing in the repo read it. This module does: it runs a cold
start in a subprocess, parses that table back into a tree, and attributes the cost to the modules
the package owns.

Two properties are deliberate. First, ``self`` and ``cumulative`` are never collapsed into one
number -- a module that is 1ms of its own work and 300ms of cost is a different problem from one
that is 300ms of its own, and the fix is different too. Second, a measurement that did not happen
is reported as ``ok: False`` with the child's stderr, never as a zero: a zero here reads like a
very fast import, which is the most misleading answer this module could give.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

#: The package whose modules this profiler attributes cost to.
OWNED_PREFIX = "bulk_downloader"

#: The directory that CONTAINS the package, used as the child's cwd when a caller names none. The
#: subprocess is a fresh interpreter with none of this one's sys.path edits, so without it a repo
#: checkout that was never pip-installed cannot import the very package it is profiling -- and the
#: failure would look like a slow import rather than a missing one.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `import time:   <self> | <cumulative> |   <indented dotted name>`. The name's leading spaces are
# the tree depth (CPython indents two per level, starting at one), so they are captured, not
# stripped. The header line and any interleaved stderr fail this match and are dropped.
_ROW = re.compile(r"^import time:\s*(\d+)\s*\|\s*(\d+)\s*\|(\s*)(\S.*?)\s*$")

# The name is spliced into ``-c "import <name>"``: only a dotted identifier may reach the child.
_MODULE_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")


def parse_importtime(text: str) -> List[Dict[str, Any]]:
    """Parse a ``-X importtime`` table into rows carrying depth and parent.

    Rows arrive in post-order -- every child is printed before the parent whose import triggered
    it -- so the parent of a row is the next row that is shallower than it. Anything that is not a
    well-formed table row is ignored rather than guessed at: the child's stderr is a shared channel
    and a traceback printed into the middle of the table must not become a fabricated module.
    """
    rows: List[Dict[str, Any]] = []
    for line in (text or "").splitlines():
        found = _ROW.match(line)
        if found is None:
            continue
        self_us, cumulative_us, indent, module = found.groups()
        rows.append({
            "module": module,
            "self_us": int(self_us),
            "cumulative_us": int(cumulative_us),
            # One leading space is depth 0; each further pair of spaces is one level down.
            "depth": max(0, (len(indent) - 1) // 2),
            "parent": None,
        })
    # Post-order: scan forward for the first row shallower than this one.
    for index, row in enumerate(rows):
        for candidate in rows[index + 1:]:
            if candidate["depth"] < row["depth"]:
                row["parent"] = candidate["module"]
                break
    return rows


def tree_residuals(rows: List[Dict[str, Any]], tolerance_us: int = 0) -> Dict[str, int]:
    """Modules whose cumulative time does not equal their own plus their children's.

    This is CPython's own arithmetic, so it is a check on the PARSE rather than on the program
    being measured: a parser that mis-reads the indentation attaches children to the wrong parent
    and the sums stop closing. The return value names the modules and by how much, because
    "the tree is wrong" is not something a reader can act on.
    """
    children: Dict[str, int] = {}
    for row in rows:
        if row["parent"] is not None:
            children[row["parent"]] = children.get(row["parent"], 0) + row["cumulative_us"]
    residuals = {}
    for row in rows:
        expected = row["self_us"] + children.get(row["module"], 0)
        delta = row["cumulative_us"] - expected
        if abs(delta) > tolerance_us:
            residuals[row["module"]] = delta
    return residuals


def owned_costs(rows: List[Dict[str, Any]], prefix: str = OWNED_PREFIX,
                exclude_root: bool = True) -> List[Tuple[str, int]]:
    """``(module, self_us)`` for the package's own modules, costliest first.

    Self time, not cumulative, and the profiled root dropped: the root's cumulative IS the whole
    measurement, so ranking by cumulative just names the thing being measured, and ranking a
    third-party module tells the operator to go fix the standard library.
    """
    roots = {row["module"] for row in rows if row["depth"] == 0} if exclude_root else set()
    owned = [(row["module"], row["self_us"]) for row in rows
             if row["module"].split('.')[0] == prefix.split('.')[0]
             and row["module"] not in roots]
    owned.sort(key=lambda item: (-item[1], item[0]))
    return owned


def capture_importtime(module: str, python: Optional[str] = None, cwd: Optional[str] = None,
                       timeout: float = 120.0) -> str:
    """Return the raw ``-X importtime`` stderr for importing *module* in a FRESH interpreter.

    A subprocess is the measurement, not an implementation detail: the module under test is
    already imported in this process, so timing it here would measure a dict lookup.
    """
    if not isinstance(module, str) or not _MODULE_NAME.fullmatch(module):
        raise ValueError("%r is not a dotted module name" % (module,))
    completed = subprocess.run(
        [python or sys.executable, "-X", "importtime", "-c", "import %s" % module],
        capture_output=True, text=True, cwd=cwd or _PACKAGE_PARENT, timeout=timeout, check=False)
    return completed.stderr or ""


def profile_cold_start(module: str, python: Optional[str] = None, cwd: Optional[str] = None,
                       timeout: float = 120.0, prefix: str = OWNED_PREFIX) -> Dict[str, Any]:
    """Cold-start *module* in a fresh interpreter and attribute the cost.

    ``ok`` is False -- with ``total_us`` None and the child's stderr in ``error`` -- whenever the
    import did not actually complete. That is the whole point of the flag: an unimported module
    takes no time to import, and reporting that as a fast cold start would be a lie the caller
    could not see through.
    """
    report: Dict[str, Any] = {"module": module, "ok": False, "total_us": None,
                              "rows": [], "owned": [], "error": None}
    if not isinstance(module, str) or not _MODULE_NAME.fullmatch(module):
        report["error"] = "%r is not a dotted module name; nothing was run" % (module,)
        return report
    try:
        completed = subprocess.run(
            [python or sys.executable, "-X", "importtime", "-c", "import %s" % module],
            capture_output=True, text=True, cwd=cwd or _PACKAGE_PARENT, timeout=timeout,
            check=False)
    except Exception as exc:               # noqa: BLE001 -- a launch failure is a MEASUREMENT result
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        return report
    stderr = completed.stderr or ""
    rows = parse_importtime(stderr)
    if completed.returncode != 0:
        # Keep the traceback: "ModuleNotFoundError" is the answer, not noise to be summarised away.
        report["error"] = "\n".join(line for line in stderr.splitlines()
                                    if not line.startswith("import time:")).strip() or \
            "exit %d with no diagnostic" % completed.returncode
        return report
    root = next((row for row in rows if row["module"] == module), None)
    if root is None:
        report["error"] = ("the -X importtime table does not contain %r; it may already have been "
                           "imported at interpreter start" % module)
        return report
    report.update({"ok": True, "total_us": root["cumulative_us"], "rows": rows,
                   "owned": owned_costs(rows, prefix=prefix)})
    return report


def check_budget(rows: List[Dict[str, Any]], budget_us: int, root: Optional[str] = None,
                 prefix: str = OWNED_PREFIX) -> Dict[str, Any]:
    """Compare a parsed table against a cold-start budget and name who broke it.

    ``root`` is the module the budget is about, and its cumulative time is the total. Without it
    the total is the SUM of every top-level import in the run -- a real table has several (``site``,
    ``encodings``, the frozen importlib bootstrap) and the interpreter genuinely paid for all of
    them, so taking only the largest would quietly under-report the cold start.
    """
    if root is not None:
        total_us = next((row["cumulative_us"] for row in rows if row["module"] == root), 0)
    else:
        total_us = sum(row["cumulative_us"] for row in rows if row["depth"] == 0)
    owned = owned_costs(rows, prefix=prefix)
    return {
        "ok": total_us <= budget_us,
        "total_us": total_us,
        "budget_us": budget_us,
        "over_us": max(0, total_us - budget_us),
        "owned": owned,
        "worst": owned[0] if owned else None,
    }
