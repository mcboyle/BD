#!/usr/bin/env python3
"""build_pin_index.py — KB Tier-A / A3.

Generates PIN_INDEX.json: an AST scan of tests/*.py for the drift-prone, hand-typed pin
forms, so `what-pins X` is a query instead of a grep, and the count-dict class behind the
v3.66.302 miss can never ship stale undetected.

WHY AST (not regex): the version-pin *fixtures* (test_release_hygiene_gates,
test_scan_version_pins_fixture) carry their pins INSIDE string literals (`_mktree({...})`,
`body = '...'`). An AST walk sees those as `ast.Constant` strings, never as `ast.Assert`
nodes — so the bump()-footgun (a scanner matching a synthetic fixture string) is structurally
impossible. A fixture-file allowlist is belt-and-suspenders.

v1 scope (honest, non-over-capturing, non-forking):
  - DIRECTLY indexed:
      version    : assert __version__ == "X"
      count_dict : assert <expr> == {<str>: <int>, ...}    (the 302 class)
  - HANDLED ELSEWHERE (coverage-mapped, not re-indexed):
      guard_sha   : STATE.json (7 SHAs, auto-repinned by bd-handoff; gated by verify_release)
      route_count : tools/check_route_counts.py G12 (source == inventory == test_wave2_backlog pins)

Per-pin schema: {form, file, line, value, gates_what}
  - gates_what = enclosing "def name — first docstring line" (best-effort)
  - `line` is informational, NOT equality-gated (the gate fires on real pin changes)

Usage:
    python tools/build_pin_index.py            # write PIN_INDEX.json
    python tools/build_pin_index.py --check     # exit 1 if the stable projection drifted
    python tools/build_pin_index.py --stdout
"""
import os
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import ast
import json
import sys
from pathlib import Path

SCHEMA_VERSION = 1
_FIXTURE_FILES = {"test_release_hygiene_gates.py", "test_scan_version_pins_fixture.py"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _is_str_const(n) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def _is_int_const(n) -> bool:
    # bool is an int subclass in Python; exclude True/False from "count" values.
    return isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool)


def _count_dict_value(node):
    """If `node` is a {str: int, ...} literal (len>=1), return it as a plain dict; else None."""
    if not isinstance(node, ast.Dict) or not node.keys:
        return None
    out = {}
    for k, v in zip(node.keys, node.values):
        if not (_is_str_const(k) and _is_int_const(v)):
            return None
        out[k.value] = v.value
    return out


def _enclosing_funcs(tree):
    """Map each FunctionDef to (lineno, end_lineno, label) for attributing asserts."""
    funcs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            first = doc.strip().splitlines()[0].strip() if doc.strip() else ""
            label = f"{node.name} — {first}" if first else node.name
            funcs.append((node.lineno, getattr(node, "end_lineno", node.lineno), label))
    return funcs


def _gates_what(funcs, lineno):
    best = None
    for start, end, label in funcs:
        if start <= lineno <= end:
            # innermost (largest start) wins
            if best is None or start > best[0]:
                best = (start, label)
    return best[1] if best else "(module level)"


def _scan_file(path: Path, root: Path):
    # Persist repository paths in one cross-platform form. os.path.relpath()
    # emitted backslashes on Windows, making a locally generated index fail
    # the same gate on Linux (and vice versa).
    rel = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return [], rel  # unparseable; skip (recorded in counts)
    funcs = _enclosing_funcs(tree)
    pins = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)):
            continue
        cmp = node.test
        # only pure equality comparisons
        if not all(isinstance(op, ast.Eq) for op in cmp.ops):
            continue
        operands = [cmp.left] + list(cmp.comparators)
        line = node.lineno
        gw = _gates_what(funcs, line)

        # version: a __version__ Name on one side, a str Constant on the other
        names = [o for o in operands if isinstance(o, ast.Name)]
        # ATTRIBUTE form too: `assert bulk_downloader.__version__ == "3.66.x"`.
        # Keying only on ast.Name made that whole idiom structurally invisible --
        # the denominator excluded a subject the index is asked about, so a stray
        # pin written that way would have read as "no stray pins".
        #
        # Measured, not assumed (AST scan of tests/, this cut): Attribute-form
        # pins against a string constant = 0 today, which is why PIN_INDEX.json's
        # pin projection is byte-identical after this change. The adjacent idiom
        # IS live -- tests/test_contracts.py:111 compares
        # `health_v == bulk_downloader.__version__` -- it just compares against a
        # variable, so it is correctly not a pin. This predicate closes the hole
        # before something lands in it; tests/test_versync_gate.py's
        # test_attribute_form_stray_pin_is_caught is what keeps it honest.
        attrs = [o for o in operands if isinstance(o, ast.Attribute)]
        strs = [o for o in operands if _is_str_const(o)]
        has_version = (any(n.id == "__version__" for n in names)
                       or any(a.attr == "__version__" for a in attrs))
        if has_version and strs:
            pins.append({"form": "version", "file": rel, "line": line,
                         "value": strs[0].value, "gates_what": gw})
            continue

        # count_dict: any operand is a {str:int,...} literal
        for o in operands:
            cd = _count_dict_value(o)
            if cd is not None:
                pins.append({"form": "count_dict", "file": rel, "line": line,
                             "value": cd, "gates_what": gw})
                break
    return pins, rel


def build_index() -> dict:
    root = _repo_root()
    tests_dir = root / "tests"
    pins = []
    scanned = 0
    for path in sorted(tests_dir.glob("*.py")):
        if path.name in _FIXTURE_FILES:
            continue  # belt-and-suspenders (AST already excludes their string-literal pins)
        scanned += 1
        file_pins, _ = _scan_file(path, root)
        pins.extend(file_pins)

    pins.sort(key=lambda p: (p["file"], p["line"], p["form"]))

    by_file = {}
    for p in pins:
        by_file[p["file"]] = by_file.get(p["file"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": "AST scan of tests/*.py",
        "note": ("DO NOT EDIT BY HAND — tests/test_pin_index_in_sync.py fails the build. "
                 "Regenerate: python tools/build_pin_index.py. `line` is informational."),
        "coverage": {
            "covers": ["version", "count_dict"],
            "handled_elsewhere": {
                "guard_sha": ("STATE.json — the 7 byte-identical guard SHAs, re-derived from "
                              "the built zip and auto-repinned by bd-handoff; gated by "
                              "verify_release / bd-state. Not a hand-typed tree pin."),
                "route_count": ("tools/check_route_counts.py (G12) — source-decorator count == "
                                "gui_parity inventory count == the integer pins in "
                                "tests/test_wave2_backlog.py."),
            },
            "known_gaps": [
                ("bare integer-equality asserts (assert X == N) are intentionally NOT indexed "
                 "— too ambiguous to separate from arbitrary test asserts; the route-count "
                 "subset that matters is gated by G12."),
                ("count_dict captures every {str: int} equality literal; a few may be "
                 "non-count dicts. Over-capture is intentional — surfacing a non-pin is cheap, "
                 "missing a real pin is the failure we are preventing."),
            ],
        },
        "counts": {
            "total": len(pins),
            "version": sum(1 for p in pins if p["form"] == "version"),
            "count_dict": sum(1 for p in pins if p["form"] == "count_dict"),
            "test_files_scanned": scanned,
            "by_file": dict(sorted(by_file.items())),
        },
        "pins": pins,
    }


def unparseable_test_files():
    """Test files the AST scan CANNOT read, over the SAME denominator build_index
    uses (tests/*.py minus _FIXTURE_FILES).

    _scan_file swallows SyntaxError and returns no pins, so an unparseable file
    contributes nothing and is recorded nowhere -- it looks exactly like a file
    with no pins. A consumer asking "are there stray version pins?" would get a
    clean answer over a set it could not actually read. Unknown is a third state
    and the caller must be able to see it.
    """
    root = _repo_root()
    bad = []
    for path in sorted((root / "tests").glob("*.py")):
        if path.name in _FIXTURE_FILES:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
            bad.append((rel, type(e).__name__))
    return bad


def _serialize(d: dict) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False) + "\n"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # An UNRECOGNIZED argument must never fall through to the write below. It
    # used to: bd-regen probed each generator's capability by running it with
    # --help, which landed here, wrote PIN_INDEX.json, and thereby ERASED the
    # drift the read-only check had been asked to report.
    known = {"--check", "--stdout"}
    unknown = [a for a in argv if a not in known]
    if unknown:
        sys.stderr.write(
            "build_pin_index.py: unrecognized argument(s): %s\n"
            "usage: build_pin_index.py [--check | --stdout]  "
            "(no args = write PIN_INDEX.json)\n" % " ".join(unknown))
        return 2
    out = _repo_root() / "PIN_INDEX.json"
    d = build_index()
    text = _serialize(d)

    if "--stdout" in argv:
        sys.stdout.write(text)
        return 0

    if "--check" in argv:
        if not out.exists():
            sys.stderr.write("PIN_INDEX.json missing — run without --check to generate.\n")
            return 1
        committed = json.loads(out.read_text(encoding="utf-8"))
        stable = ("form", "file", "value", "gates_what")
        key = lambda p: (p["file"], str(p["form"]), str(p["value"]), str(p.get("gates_what")))
        proj = lambda ps: sorted([{k: p.get(k) for k in stable} for p in ps], key=key)
        if proj(d["pins"]) != proj(committed["pins"]):
            sys.stderr.write("PIN_INDEX.json STALE (stable projection drifted) — regenerate.\n")
            return 1
        sys.stdout.write(f"PIN_INDEX.json IN-SYNC ({d['counts']['total']} pins).\n")
        return 0

    out.write_text(text, encoding="utf-8")
    sys.stdout.write(f"wrote {out.name}: {d['counts']['total']} pins "
                     f"({d['counts']['version']} version, {d['counts']['count_dict']} count_dict) "
                     f"across {len(d['counts']['by_file'])} files.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
