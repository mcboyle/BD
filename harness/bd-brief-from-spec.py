#!/usr/bin/env python3
"""Generate a codex brief for a row from its canonical spec text.

Several rows already carry full measured evidence and acceptance criteria in
bd-night-spec.txt. Re-typing that into a brief invites drift between the row and
what the worker is told, so this extracts the row's own text verbatim and wraps
it in the standing header. Refuses rather than emitting a stub when the row body
is too thin to act on -- a worker given a one-line row invents the requirements.
"""
import pathlib, re, sys

SPEC = pathlib.Path("/home/mboyle/bd-night-spec.txt")
OUT = pathlib.Path("/home/mboyle/bd-codex-briefs")
HEADER = """PROCEED WITHOUT ASKING. Implement this row end to end now: RED test, implement,
run the affected band, regenerate, and report. Do NOT stop to request approval --
there is nobody at the other end of this pipe and a question is a failed run. If
a detail is genuinely ambiguous, choose the option that preserves existing
behaviour, state the assumption in your report, and keep going. Do NOT edit the
canonical backlog; the row text below is your authority whether or not it
appears in any file in this worktree.

Write only inside THIS worktree. Do not push, merge, or deploy. Do not edit
bulk_downloader/__init__.py, CHANGELOG.md, or tests/test_settings_center_slice4.py.
Read CLAUDE.md first, especially A7 (a gate must see the subject it claims to
judge; an unavailable measurement returns UNKNOWN, never OK).

TEST INTEGRITY -- this is where cuts get refused, and it applies to every row:
  * RED FIRST on the CURRENT tree, failing for the intended defect.
  * Assert the PRECONDITION explicitly before any verdict -- assert the fixture
    really built the shape the row is about. A fixture that silently built
    something else passes for the wrong reason and proves nothing.
  * Include a NEGATIVE CONTROL that must fail for the intended reason.
  * Assert the DISTINCTIVE diagnostic, not merely a non-zero exit -- several
    refusal paths usually share one exit code.
  * Assert EXACT nonzero counts, never "did not error".
  * Declare BD_GATE_SCOPE in every new tests/test*.py file, or CI cannot
    classify it and a gate CI does not run does not exist.

No live network in tests. Run toolchain/bin/bd-band-derive for the affected band
and run all of it, then regenerate from the worktree root with
  venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"
and inspect every generated diff before finishing.

Report: the exact RED failure you observed, the GREEN result, every exact count
asserted, and every file you changed.

======================= THE ROW =======================
"""

def body(num):
    txt = SPEC.read_text().splitlines()
    start = None
    for i, l in enumerate(txt):
        if re.match(rf"^#\s*{num}\|", l):
            start = i; break
    if start is None:
        return None
    out = [txt[start]]
    for l in txt[start + 1:]:
        if re.match(r"^#\s*\d{3}\|", l):
            break
        if l.startswith("#"):
            out.append(l)
        elif not l.strip():
            continue
        else:
            break
    return "\n".join(x.lstrip("# ").rstrip() if x.startswith("#") else x for x in out)

for num in sys.argv[1:]:
    b = body(num)
    if b is None:
        print(f"row {num}: NOT FOUND in spec -- skipped"); continue
    if len(b) < 400:
        print(f"row {num}: body only {len(b)} chars -- TOO THIN, refusing to emit a stub"); continue
    p = OUT / f"row{num}.txt"
    if p.exists():
        print(f"row {num}: brief already exists ({p.stat().st_size} bytes) -- left alone"); continue
    p.write_text(HEADER + b + "\n")
    print(f"row {num}: wrote {p.name} ({p.stat().st_size} bytes, row body {len(b)})")
