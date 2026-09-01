#!/usr/bin/env python3
"""Group eligible rows into FILE-DISJOINT batches that can share one version.

WHY THIS EXISTS. Every cut claims main+1, so N rows cost N sequential
verify+CI cycles (~20 min each). The operator ruled 2026-08-25 that same-class
rows may share one cut "because the serial integration lane -- not the parallel
builds -- is the bottleneck", and bd-integrate-row.sh already accepts several
rows in its first argument. This only decides WHICH rows may safely share one.

DISJOINT MEANS DISJOINT ON FILES THAT ACTUALLY CONFLICT.
Two populations are excluded from the overlap test on purpose:

  * THE RELEASE TRIO AND GENERATED ARTIFACTS -- the integrator writes those
    itself, after applying every patch. A worker never owns them.
  * THE APPEND-ONLY REGISTRIES -- ci.yml, the shard-coverage gate, the
    gate-scope baseline. bd-union-resolve.py exists precisely because two rows
    each appending an entry is NOT a conflict; both entries belong. Counting
    them as overlap collapsed a real 3-batch grouping into 14 and hid the win.

Anything else shared between two rows is a genuine textual conflict and they
go in different batches.

BATCHES ARE CAPPED. A batch fails as a unit, so an uncapped batch of 19 turns
one bad row into a bisect over 19. The cap bounds that blast radius; the
speedup is dominated by the first few merges anyway.

Reads the spec on stdin (row|slug|title), prints one batch per line.
"""
import os, pathlib, subprocess, sys

R = "/home/mboyle/BulkDownloader"
CAP = int(os.environ.get("BATCH_CAP", "6"))

# Rows that must never share a batch. A batch fails as a unit, so the rows
# most likely to fail are exactly the ones that must not take others down
# with them. Operator ruling 2026-08-27.
SOLO_ROWS = set(os.environ.get("SOLO_ROWS", "").split())


TRIO = {
    "project-knowledge/IMPROVEMENT_BACKLOG.md", "CHANGELOG.md", "PIN_INDEX.json",
    "project-knowledge/STATIC_KB_MANIFEST.json", "bulk_downloader/__init__.py",
    "tests/test_settings_center_slice4.py",
}
def _append_only() -> set[str]:
    """DERIVED FROM bd-union-resolve.py, NEVER HARDCODED HERE.

    The integrator refuses any conflict outside its own UNION_OK set. When this
    file carried its own guess it listed four paths; the real set is two, and the
    first batch died on `conflicts outside the append-only set -- refusing:
    tests/test_v3_66_1173_gate_scope_debt_is_paid.py`. Two files that LOOK
    append-only are not the same as two files the resolver will actually union.
    Read the authority; do not re-describe it.
    """
    import ast
    src = pathlib.Path("/home/mboyle/bd-union-resolve.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "UNION_OK" for t in node.targets)
                and isinstance(node.value, ast.Set)):
            return {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
    raise SystemExit("cannot read UNION_OK from bd-union-resolve.py -- refusing to "
                     "batch on a guessed append-only set")


APPEND_ONLY = _append_only()


def changed(row: str) -> set[str] | None:
    w = f"/home/mboyle/bd-codex-wt/row{row}"
    if not os.path.isdir(w):
        return None
    head = subprocess.run(["git", "-C", w, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    base = subprocess.run(["git", "-C", R, "merge-base", head, "origin/main"],
                          capture_output=True, text=True).stdout.strip()
    if not base:
        return None
    out = subprocess.run(
        ["git", "-C", w, "diff", base, "--name-only", "--", ".",
         ":(exclude)venv", ":(exclude)frontend/node_modules"],
        capture_output=True, text=True).stdout.split()
    return {f for f in out if f not in TRIO and f not in APPEND_ONLY}


def main() -> int:
    rows = []
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = line.split("|", 1)[0].strip()
        files = changed(row)
        # A row whose file set cannot be measured is NOT silently batched with
        # others -- unknown overlap is not the same as no overlap. It ships alone.
        rows.append((row, files if files is not None else None))

    batches: list[dict] = []
    solo = [r for r, f in rows if f is None or r in SOLO_ROWS]
    for row, files in sorted((r for r in rows
                          if r[1] is not None and r[0] not in SOLO_ROWS),
                             key=lambda x: -len(x[1])):
        for b in batches:
            if len(b["rows"]) < CAP and not (b["files"] & files):
                b["rows"].append(row)
                b["files"] |= files
                break
        else:
            batches.append({"rows": [row], "files": set(files)})

    # EMIT IN SPEC ORDER. The sort above is largest-first BIN PACKING, which is
    # correct for deciding what fits together and WRONG for deciding what runs
    # first: it made the biggest row lead every pass. On 2026-08-27 that meant
    # rows deliberately placed at the head of the spec to UNBLOCK the queue --
    # 318, 319, 320 -- lost every time to row 295, the largest, and had to be
    # forced through by hand. The operator's ordering is a priority statement;
    # packing must not silently override it.
    order = {row: i for i, (row, _f) in enumerate(rows)}
    batches.sort(key=lambda b: min(order.get(r, 10**6) for r in b["rows"]))
    for b in batches:
        b["rows"].sort(key=lambda r: order.get(r, 10**6))
        print(" ".join(b["rows"]))
    for row in sorted(solo, key=lambda r: order.get(r, 10**6)):
        print(row)                        # unmeasurable rows, one per batch
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
