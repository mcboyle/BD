#!/usr/bin/env python3
"""build_function_index.py — KB_PLAN_v2 B.1.

Generates FUNCTION_INDEX.md from an AST walk of six load-bearing
source files:

    bulk_downloader/app.py
    bulk_downloader/runner.py
    bulk_downloader/runner_util.py
    bulk_downloader/runner_integrations.py
    bulk_downloader/runner_manual.py
    bulk_downloader/runner_challenge.py
    bulk_downloader/runner_teach.py
    bulk_downloader/runner_integrity.py
    bulk_downloader/runner_accounts.py
    bulk_downloader/runner_browser.py
    bulk_downloader/runner_scheduler.py
    bulk_downloader/runner_telemetry.py
    bulk_downloader/runner_queue.py
    bulk_downloader/runner_extractors.py
    bulk_downloader/runner_auth.py
    bulk_downloader/runner_transport.py
    bulk_downloader/db.py
    bulk_downloader/login_impl/_common.py
    bulk_downloader/login_impl/manual.py
    bulk_downloader/login_impl/replay.py
    bulk_downloader/login_impl/submit.py
    bulk_downloader/extractors.py

Each module-level function (or async function), each class, and each
class method one level deep gets one line in the index. The six-file
scope is deliberate — KB_PLAN_v2 B.1 explicitly says "Don't index the
full bulk_downloader/ tree. 184 modules, thousands of functions." The
goal is to make navigation of the five files where fresh Claude
sessions actually edit cheap, not to mirror the codebase.

Output format (one line per callable):

    - L<NNNN> <name> <tag> — <docstring first line>

Where:
  - L<NNNN> is the line number with 4-digit zero padding so rows
    sort visually.
  - <name> is the function name. Class methods are indented two
    spaces and rendered as `Class.method`.
  - <tag> is one of:
      `GET /api/foo`              — Flask route (per-method if multi)
      `[private]`                 — name starts with `_` (single
                                    underscore; dunders are tagged
                                    `[dunder]`)
      `[dunder]`                  — name starts and ends with `__`
      (empty)                     — public, non-route
  - <docstring first line> is the first non-empty stripped line of
    the function's docstring, capped at 120 chars. Functions
    without a docstring have an empty description.

Multi-method routes emit one tag string (`GET,POST /api/foo`) on a
single row, not one row per method — distinct from the endpoint
catalog (which emits one row per method+path). The function index
is organized by source-file location; the endpoint catalog is
organized by URL.

Multiple `@app.route` decorators on the same function (Flask supports
this for aliasing) emit comma-separated tags joined with `; `.

Usage:

    # Regenerate from current source. Writes FUNCTION_INDEX.md at
    # repo root. Exits 0 on success.
    python tools/build_function_index.py

    # Check-only: regenerate in-memory, diff against on-disk, exit
    # 1 on drift. Used by the drift test and build_release.py.
    python tools/build_function_index.py --check

    # Print to stdout. Useful for piping into a grep pipeline.
    python tools/build_function_index.py --stdout

Implementation notes:

  - Pure-AST. No source-tree imports — this script works even if
    bulk_downloader/app.py has a runtime import error, because we
    never execute it.

  - Flask routes are extracted by walking the decorator AST directly
    (not via `ast.unparse`). The plan called this out: `ast.unparse`
    on `@app.route("/x", methods=["POST"])` historically truncated
    the methods kwarg on older Pythons. Modern Python preserves it,
    but we don't depend on that — we parse the string literal and
    list literal ourselves. This also means a route declared with a
    runtime-built path (e.g. `@app.route(SOME_CONSTANT)`) renders
    with a placeholder, not a crash.

  - Nested functions (inside other functions) are NOT walked.
    Class methods one level deep ARE walked. The plan's "one level
    of class methods" phrasing.

  - Sorted by source-line within each file. Files appear in the
    order listed in TARGETS.
"""
import argparse
import ast
import difflib
import os
import sys
from pathlib import Path
from typing import Iterable


# The five files the index covers. KB_PLAN_v2 B.1 specifies these
# exactly. Changing the list requires a plan update.
TARGETS: tuple[str, ...] = (
    "bulk_downloader/app.py",
    "bulk_downloader/runner.py",
    "bulk_downloader/runner_util.py",
    "bulk_downloader/runner_integrations.py",
    "bulk_downloader/runner_manual.py",
    "bulk_downloader/runner_challenge.py",
    "bulk_downloader/runner_teach.py",
    "bulk_downloader/runner_integrity.py",
    "bulk_downloader/runner_accounts.py",
    "bulk_downloader/runner_browser.py",
    "bulk_downloader/runner_scheduler.py",
    "bulk_downloader/runner_telemetry.py",
    "bulk_downloader/runner_queue.py",
    "bulk_downloader/runner_extractors.py",
    "bulk_downloader/runner_auth.py",
    "bulk_downloader/runner_transport.py",
    "bulk_downloader/db.py",
    "bulk_downloader/login_impl/_common.py",
    "bulk_downloader/login_impl/manual.py",
    "bulk_downloader/login_impl/replay.py",
    "bulk_downloader/login_impl/submit.py",
    "bulk_downloader/extractors.py",
)

# Cap docstring first line at this many chars.
_DESC_MAX = 120


def _repo_root() -> Path:
    """This script lives at <root>/tools/build_function_index.py."""
    return Path(__file__).resolve().parent.parent


# ── AST helpers ───────────────────────────────────────────────────


def _string_value(node: ast.AST) -> str | None:
    """If `node` is a string Constant, return its value; else None.

    Used to extract `"/api/foo"` from `@app.route("/api/foo")`. Returns
    None for any expression that isn't a literal string (variable
    reference, f-string, concatenation), so the caller can decide
    whether to render a placeholder or skip.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _list_of_strings(node: ast.AST) -> list[str] | None:
    """If `node` is `[<str>, <str>, ...]`, return the list of values;
    else None. Used for the `methods=[...]` kwarg on @app.route."""
    if not isinstance(node, ast.List):
        return None
    out: list[str] = []
    for el in node.elts:
        s = _string_value(el)
        if s is None:
            return None  # Mixed or non-literal — give up; caller
            # uses None as the signal to fall back.
        out.append(s)
    return out


def _route_tags_from_decorators(decorators: list[ast.expr]) -> list[str]:
    """Extract route hints from a function's decorator list.

    Returns a list of strings like `"GET /api/foo"` — one per
    `@app.route(...)` decorator on the function. Empty list if no
    route decorators present.

    Multiple `@app.route` decorators on one function (the aliasing
    pattern) produce multiple entries in the returned list. The
    caller joins them.
    """
    tags: list[str] = []
    for dec in decorators:
        # We're looking for calls of the form `app.route(...)` —
        # `Call(func=Attribute(value=Name('app'), attr='route'), ...)`.
        # Also accept any `.route(...)` call regardless of the
        # receiver name, since some files use blueprints (`bp.route`).
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not isinstance(func, ast.Attribute) or func.attr != "route":
            continue
        # First positional arg = path.
        if not dec.args:
            continue
        path = _string_value(dec.args[0])
        if path is None:
            # Non-literal path (rare; e.g. dynamic blueprint). Use
            # a placeholder so the row still appears with a sentinel.
            path = "<dynamic>"
        # methods= kwarg (optional; default GET).
        methods: list[str] = ["GET"]
        for kw in dec.keywords:
            if kw.arg == "methods":
                got = _list_of_strings(kw.value)
                if got:
                    # Normalize: uppercase, drop OPTIONS/HEAD (Flask
                    # adds those implicitly), preserve order.
                    methods = [m.upper() for m in got
                               if m.upper() not in ("OPTIONS", "HEAD")]
                    if not methods:
                        # Pathological case (methods=["OPTIONS"]) —
                        # fall back to a sentinel rather than emit
                        # an empty methods string.
                        methods = ["?"]
                break
        tags.append(f"{','.join(methods)} {path}")
    return tags


def _docstring_first_line(node: ast.AST) -> str:
    """Return the first non-empty stripped line of `node`'s docstring,
    truncated to _DESC_MAX. Empty string if no docstring.

    Uses `ast.get_docstring` for the parse and unindent, then picks
    the first non-empty line."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
        return ""
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return ""
    for raw in doc.splitlines():
        line = raw.strip()
        if line:
            if len(line) > _DESC_MAX:
                line = line[: _DESC_MAX - 1] + "…"
            return line
    return ""


def _name_tag(name: str) -> str:
    """Tag a name as `[private]`, `[dunder]`, or empty."""
    if name.startswith("__") and name.endswith("__"):
        return "[dunder]"
    if name.startswith("_"):
        return "[private]"
    return ""


# ── Row model ─────────────────────────────────────────────────────


class _Row:
    """One row of the index. Sortable by line number within a file."""

    __slots__ = ("lineno", "display_name", "tag", "desc", "indent")

    def __init__(self, lineno: int, display_name: str, tag: str,
                 desc: str, indent: int):
        self.lineno = lineno
        self.display_name = display_name
        self.tag = tag
        self.desc = desc
        self.indent = indent

    def render(self) -> str:
        """Format this row in the index's standard form."""
        prefix = "  " * self.indent + "- "
        # Line number padded to 4 digits so visual sort matches numeric
        # for files under 10000 lines. app.py is currently 17K but the
        # padding is for readability inside the typical-case files; the
        # extra digits on app.py are tolerated.
        line_field = f"L{self.lineno:04d}"
        body = f"{prefix}{line_field} `{self.display_name}`"
        if self.tag:
            body += f" `{self.tag}`"
        if self.desc:
            body += f" — {self.desc}"
        return body


# ── Walker ────────────────────────────────────────────────────────


def _walk_module(tree: ast.Module) -> list[_Row]:
    """Walk a parsed module and return rows for every public surface:
    module-level functions, classes, and class methods one level deep.
    """
    rows: list[_Row] = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows.append(_row_for_function(n, parent_class=None, indent=0))
        elif isinstance(n, ast.ClassDef):
            # Emit a row for the class itself, then walk its methods
            # one level deep.
            class_desc = _docstring_first_line(n)
            class_tag = _name_tag(n.name) or "[class]"
            rows.append(_Row(
                lineno=n.lineno,
                display_name=n.name,
                tag=class_tag,
                desc=class_desc,
                indent=0,
            ))
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    rows.append(_row_for_function(m, parent_class=n.name,
                                                  indent=1))
        # Skip everything else (Import, Assign, If, etc.) — the index
        # only covers callables.
    rows.sort(key=lambda r: r.lineno)
    return rows


def _row_for_function(node: ast.FunctionDef | ast.AsyncFunctionDef,
                      parent_class: str | None,
                      indent: int) -> _Row:
    """Build a _Row for a single function or async function."""
    if parent_class is not None:
        display_name = f"{parent_class}.{node.name}"
    else:
        display_name = node.name
    # Tag: route hints take precedence (a route function isn't "just
    # private" even if it starts with underscore — though Flask routes
    # virtually never do). If both a route and an underscore prefix
    # apply, emit both for transparency.
    route_tags = _route_tags_from_decorators(node.decorator_list)
    name_tag = _name_tag(node.name)
    parts: list[str] = []
    if route_tags:
        parts.append("; ".join(route_tags))
    if name_tag:
        parts.append(name_tag)
    if isinstance(node, ast.AsyncFunctionDef):
        parts.append("[async]")
    tag = " ".join(parts)
    desc = _docstring_first_line(node)
    return _Row(
        lineno=node.lineno,
        display_name=display_name,
        tag=tag,
        desc=desc,
        indent=indent,
    )


# ── Renderer ──────────────────────────────────────────────────────


# Bump this when the header text or row format changes. The drift
# test asserts the on-disk header declares this exact version, so a
# header edit without a bump is caught.
INDEX_SCHEMA_VERSION = 2


_HEADER = """\
# FUNCTION_INDEX

Auto-generated by `tools/build_function_index.py` (KB_PLAN_v2 B.1).
DO NOT EDIT BY HAND — `tests/test_function_index_in_sync.py` will
fail the build. To regenerate after adding/removing/renaming a
function in a covered file, or editing a covered function's
docstring first line:

    python tools/build_function_index.py

Scope covers the files where fresh Claude sessions actually edit, plus the
runner mixin modules extracted during the PHASE 3 decomposition:

  - bulk_downloader/app.py       (Flask routes, before_request hooks)
  - bulk_downloader/runner.py    (SiteRunner — the worker pool)
  - bulk_downloader/runner_util.py (runner leaf kernel — shared helpers/consts)
  - bulk_downloader/runner_integrations.py (SiteRunner IntegrationsMixin — stash/plex/jellyfin/qb/jd)
  - bulk_downloader/runner_manual.py (SiteRunner ManualMixin + _ManualDownloadSession)
  - bulk_downloader/runner_challenge.py (SiteRunner ChallengeMixin — captcha/turnstile)
  - bulk_downloader/runner_teach.py (SiteRunner TeachMixin — teach/learn selector drafts)
  - bulk_downloader/runner_integrity.py (SiteRunner IntegrityMixin — dedup/hash/verify/embed)
  - bulk_downloader/runner_accounts.py (SiteRunner AccountsMixin — account/pool rotation)
  - bulk_downloader/runner_browser.py (SiteRunner BrowserMixin — Playwright context/launch/stealth)
  - bulk_downloader/runner_scheduler.py (SiteRunner SchedulerMixin — retry loops/subscriptions/scheduler)
  - bulk_downloader/runner_telemetry.py (SiteRunner TelemetryMixin — events/log/error classify)
  - bulk_downloader/runner_queue.py (SiteRunner QueueMixin — URL queue load/reorder/bulk/export)
  - bulk_downloader/runner_extractors.py (SiteRunner ExtractorsMixin — per-site extractors + ytdlp/deep-detect fallback)
  - bulk_downloader/runner_auth.py (SiteRunner AuthMixin — login async/manual, cookie/relogin, auth-required, redirect)
  - bulk_downloader/runner_transport.py (SiteRunner TransportMixin — HTTP download engine: proxy/direct/multi-conn/parallel/probe/promote)
  - bulk_downloader/db.py        (SQLite + queue persistence)
  - bulk_downloader/login.py     (multi-strategy login + selectors)
  - bulk_downloader/extractors.py (per-site URL extractors)

Tags:
  `GET /api/foo`   — Flask route (multi-method routes shown as
                     `GET,POST /api/foo`; aliased routes joined with
                     `; `)
  `[class]`        — class definition row
  `[private]`      — single-underscore name
  `[dunder]`       — `__dunder__` name
  `[async]`        — async function or async method
  (empty)          — public, non-route, non-async

Schema version: {schema}
"""


def _render_file_section(rel_path: str, rows: list[_Row]) -> str:
    """Render one file's section of the index — a heading plus its
    sorted rows, inside a fenced block for monospace readability."""
    lines = [f"## `{rel_path}` ({len(rows)} entries)", ""]
    if not rows:
        lines.append("(no public callables)")
        lines.append("")
        return "\n".join(lines) + "\n"
    lines.append("```")
    for r in rows:
        lines.append(r.render())
    lines.append("```")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_index(root: Path) -> str:
    """Build the full FUNCTION_INDEX.md content for the given repo
    root. Pure function over the source files — used by both the
    write path and the drift test."""
    out: list[str] = []
    out.append(_HEADER.format(schema=INDEX_SCHEMA_VERSION))
    out.append("")
    total_rows = 0
    for rel in TARGETS:
        path = root / rel
        if not path.is_file():
            # A missing target is a hard error — the five-file scope
            # is part of the contract. If somebody renames or moves
            # one of these, they need to update TARGETS here and
            # rerun.
            raise FileNotFoundError(
                f"build_function_index.py target missing: {rel}. "
                f"Update TARGETS or restore the file."
            )
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
        rows = _walk_module(tree)
        total_rows += len(rows)
        out.append(_render_file_section(rel, rows))
    out.append(f"_Total entries: {total_rows} across "
               f"{len(TARGETS)} files._")
    out.append("")
    return "\n".join(out)


# ── CLI ───────────────────────────────────────────────────────────


def _index_path() -> Path:
    """The canonical on-disk location for FUNCTION_INDEX.md."""
    return _repo_root() / "FUNCTION_INDEX.md"


def _write_if_changed(content: str, path: Path) -> bool:
    """Atomic-write `content` to `path` only if the on-disk file
    differs. Returns True if a write happened."""
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing == content:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


def _diff_text(have: str, want: str, label: str) -> str:
    """Bounded unified diff for human-readable build-log output."""
    diff = list(difflib.unified_diff(
        have.splitlines(keepends=True),
        want.splitlines(keepends=True),
        fromfile=f"{label} (on disk)",
        tofile=f"{label} (regenerated)",
        n=2,
    ))
    if len(diff) > 60:
        diff = diff[:60] + [f"... (+{len(diff) - 60} more lines)\n"]
    return "".join(diff)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="diff regenerated content against on-disk "
                         "FUNCTION_INDEX.md and exit 1 on drift; do "
                         "not write")
    ap.add_argument("--stdout", action="store_true",
                    help="print regenerated content to stdout instead "
                         "of writing the file (mutually exclusive "
                         "with --check)")
    args = ap.parse_args(argv)

    if args.check and args.stdout:
        print("FAIL: --check and --stdout are mutually exclusive",
              file=sys.stderr)
        return 2

    root = _repo_root()
    try:
        content = render_index(root)
    except FileNotFoundError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2
    except SyntaxError as e:
        print(f"FAIL: could not parse source: {e}", file=sys.stderr)
        return 2

    path = _index_path()

    if args.stdout:
        sys.stdout.write(content)
        return 0

    if args.check:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if existing == content:
            print(f"OK: {path.name} is in sync "
                  f"({len(content.splitlines())} lines)")
            return 0
        print(f"FAIL: {path.name} drift detected.", file=sys.stderr)
        print(_diff_text(existing, content, path.name), file=sys.stderr)
        print(f"Run `python tools/build_function_index.py` to fix.",
              file=sys.stderr)
        return 1

    wrote = _write_if_changed(content, path)
    if wrote:
        print(f"WROTE: {path} ({len(content.splitlines())} lines)")
    else:
        print(f"OK: {path.name} already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
