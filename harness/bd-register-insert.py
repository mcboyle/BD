#!/usr/bin/env python3
"""Insert ONE known register row into a backlog file, in numeric order.

  usage: bd-register-insert.py <backlog-path> <row-number> <the row line>

Used by bd-rebase when a worktree's register conflicts with main: main's copy is
taken whole, then this puts the worker's own row back. Distinct from
bd-register-merge.py, which is bd-integrate-row.sh's tool and takes two
WORKTREES -- do not merge the two; I clobbered that one on 2026-08-28 by writing
this logic under its name and blocked the lane.

THIS IS NOT THE TOOL FOR ADDING A NEW ROW. That is
`toolchain/bin/bd-register-append` followed by `bd-register-close`, which are
repository-owned, atomic, and maintain the canonical header. Reaching for this
one instead on 2026-08-31 produced a register whose header said rows=473 while
its table held 474, and bd-register-append then refused the whole cut with
"canonical register header does not match derived table" -- a confusing failure
three steps away from its cause.

So this tool now REPAIRS the header after every insertion, using the
repository's own parser rather than a second implementation of it. If the
repository cannot be located, or its parser refuses, the insertion is written
and the staleness is REPORTED loudly with exit 4: a silently stale header is the
failure this note exists to end, and pretending the write did not happen would
be worse.
"""
import pathlib, re, sys

_HEADER = re.compile(
    r"<!-- canonical-task-register schema=1 rows=\d+ open=\d+ ids-sha256=[0-9a-f]{64} -->"
)


def _repair_header(path: pathlib.Path) -> int:
    """Recompute the canonical header with the REPOSITORY's parser.

    Deliberately not a second implementation: a header derived by different code
    than the one the gates read is a denominator that agrees by luck.
    """
    repo = path.resolve().parent.parent
    parser = repo / "project-knowledge" / "build_current_overlay.py"
    if not parser.is_file():
        print(f"HEADER NOT REPAIRED: no canonical parser at {parser}; the row was "
              f"inserted and the header is now STALE", file=sys.stderr)
        return 4
    text = path.read_text(encoding="utf-8")
    matches = list(_HEADER.finditer(text))
    if len(matches) != 1:
        print(f"HEADER NOT REPAIRED: canonical header occurs {len(matches)} times, "
              f"expected exactly 1; the row was inserted and the header is STALE",
              file=sys.stderr)
        return 4
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_bd_current_overlay", parser)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        derived = module.derive_backlog(text)
        if derived is None:
            raise ValueError("canonical register parser examined zero rows")
        rows, opened, digest = derived[0], derived[1], derived[2]
    except Exception as exc:
        print(f"HEADER NOT REPAIRED: {type(exc).__name__}: {exc}; the row was "
              f"inserted and the header is STALE", file=sys.stderr)
        return 4
    marker = (f"<!-- canonical-task-register schema=1 rows={rows} open={opened} "
              f"ids-sha256={digest} -->")
    old = matches[0].group(0)
    if old == marker:
        print("header already agrees with the derived table")
        return 0
    path.write_text(text[:matches[0].start()] + marker + text[matches[0].end():],
                    encoding="utf-8")
    print(f"header repaired: {old.split('schema=1 ')[1][:24]}... -> rows={rows} open={opened}")
    return 0


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr); return 2
    path, row_s, line = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if not row_s.isdigit():
        print(f"row must be numeric, got {row_s!r}", file=sys.stderr); return 2
    row = int(row_s)
    if not re.match(rf"^\|\s*{row}\s*\|", line):
        print(f"the supplied line is not row {row}: {line[:80]!r}", file=sys.stderr); return 2
    text = path.read_text(encoding="utf-8")
    if re.search(rf"^\|\s*{row}\s*\|", text, re.M):
        print(f"row {row} already present -- not inserting"); return 0
    lines = text.split("\n")
    numbered = [(i, int(m.group(1))) for i, L in enumerate(lines)
                if (m := re.match(r"^\|\s*(\d+)\s*\|", L))]
    if not numbered:
        print("no numbered rows found -- refusing to guess a position", file=sys.stderr); return 3
    after = [i for i, n in numbered if n > row]
    idx = after[0] if after else numbered[-1][0] + 1
    lines.insert(idx, line)
    out = "\n".join(lines)
    if len(out) != len(text) + len(line) + 1:
        print("length arithmetic failed -- refusing to write", file=sys.stderr); return 3
    if len(re.findall(rf"^\|\s*{row}\s*\|", out, re.M)) != 1:
        print(f"row {row} would appear more than once -- refusing", file=sys.stderr); return 3
    path.write_text(out, encoding="utf-8")
    print(f"re-inserted row {row} at line {idx + 1}")
    return _repair_header(path)

if __name__ == "__main__":
    sys.exit(main())
