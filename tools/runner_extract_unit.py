#!/usr/bin/env python3
"""runner_extract_unit -- scaffold ONE unit's extraction into a mixin (mechanical move
only; the session reviews + adds the import block per RUNNER_IMPORT_MAP.md).

  python3 tools/runner_extract_unit.py <unit>          # dry-run: report the plan
  python3 tools/runner_extract_unit.py <unit> --apply  # write runner_<unit>.py + edit runner.py

Moves each method VERBATIM (decorator-inclusive span) from SiteRunner into
`class <Unit>Mixin:` in `bulk_downloader/runner_<unit>.py`, removes it from runner.py,
adds the mixin to SiteRunner's bases, and inserts the import. Does NOT compute the new
module's import block (emits a TODO from the seams data) and does NOT move associated
top-level classes (e.g. _ManualDownloadSession for `manual`) -- those are manual. After
--apply: run `runner_api_snapshot.py --check` (expect PASS + owners moved) and
`python -m py_compile` on both files.
"""
import ast, os, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from runner_struct import UNITS, unit_of, UNIT_MODULE  # canonical grouping
RUNNER = os.path.join(ROOT, "bulk_downloader", "runner.py")

def spans_for(unit):
    tree = ast.parse(open(RUNNER, encoding="utf-8").read())
    sr = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SiteRunner"][0]
    out = {}
    for b in sr.body:
        if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and unit_of(b.name) == unit:
            lo = b.lineno
            if b.decorator_list:
                lo = min(lo, min(d.lineno for d in b.decorator_list))
            out[b.name] = (lo, b.end_lineno)
    # also the class-line span for base editing
    return out, (sr.lineno)

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in UNIT_MODULE or sys.argv[1] == "util":
        sys.exit(f"usage: runner_extract_unit.py <{'|'.join(u for u in UNIT_MODULE if u not in ('util','core'))}> [--apply]")
    unit = sys.argv[1]; apply = "--apply" in sys.argv
    mod, mixin, _ = UNIT_MODULE[unit]
    spans, sr_line = spans_for(unit)
    lines = open(RUNNER, encoding="utf-8").read().splitlines(keepends=True)
    total = sum(hi - lo + 1 for lo, hi in spans.values())
    print(f"unit={unit} -> {mod} (class {mixin})")
    print(f"  {len(spans)} methods, {total} lines to move")
    for m, (lo, hi) in sorted(spans.items(), key=lambda kv: kv[1][0]):
        print(f"    {m}: L{lo}-{hi} ({hi-lo+1})")
    if not apply:
        print("\n(dry run -- pass --apply to write)"); return

    # build the mixin module body (verbatim blocks, already at 4-space class-body indent)
    blocks = []
    for m, (lo, hi) in sorted(spans.items(), key=lambda kv: kv[1][0]):
        blocks.append("".join(lines[lo-1:hi]))
    header = (f'"""runner_{unit} -- {UNIT_MODULE[unit][2]}\n\n'
              f'Extracted from runner.py (SiteRunner). Mixin: methods reference self.* only;\n'
              f'NO __init__. TODO: add the import block per RUNNER_IMPORT_MAP.md (kernel names\n'
              f'from .runner_util, NEVER from .runner -- avoid the import cycle).\n"""\n'
              f'# TODO imports: see RUNNER_IMPORT_MAP.md section [{unit}]\n\n\n'
              f'class {mixin}:\n')
    open(os.path.join(ROOT, "bulk_downloader", mod), "w", encoding="utf-8").write(
        header + "\n".join(b.rstrip("\n") for b in blocks) + "\n")

    # edit runner.py: remove blocks (bottom-up), add import + base
    keep = list(lines)
    for lo, hi in sorted(spans.values(), reverse=True):
        del keep[lo-1:hi]
    src = "".join(keep)
    # add the mixin base to class SiteRunner
    if "class SiteRunner(" in src:
        src = src.replace("class SiteRunner(", f"class SiteRunner({mixin}, ", 1)
    else:
        src = src.replace("class SiteRunner:", f"class SiteRunner({mixin}):", 1)
    # insert the import just before the SiteRunner class line
    src = src.replace("\nclass SiteRunner(", f"\nfrom .{mod[:-3]} import {mixin}  # noqa: E402\nclass SiteRunner(", 1)
    open(RUNNER, "w", encoding="utf-8").write(src)
    print(f"\nAPPLIED. wrote bulk_downloader/{mod}; runner.py now {src.count(chr(10))+1} lines")
    print("  NEXT: add the import block to the new module, then runner_api_snapshot.py --check")

if __name__ == "__main__":
    main()
