#!/usr/bin/env python3
"""Resolve conflicts in APPEND-ONLY REGISTRY files by keeping BOTH sides.

`.github/workflows/ci.yml` and test_..._939_ci_gate_shards_cover_every_gate.py
are lists that every cut appends one entry to. Two cuts appending near the same
place produce a textual conflict that is not a semantic one -- both entries
belong. A union resolve is correct HERE and nowhere else, so the file set is
explicit and any other conflicted path makes this refuse.

Union is safe for these two because a duplicate line is caught downstream: the
shard test asserts every gate appears exactly once, and CI parses ci.yml.
"""
import pathlib, re, subprocess, sys

UNION_OK = {".github/workflows/ci.yml",
            "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py"}
# COLLAPSE STACKED SINGLE-VALUE PINS AFTER A UNION.
# Union is the right rule for a LIST of shard entries and the wrong rule for a
# SCALAR. When six batched rows each append `_EXPECTED_DECLARED_GATE_COUNT = N`,
# unioning them yields four bindings of which only the last is live -- dead code
# that still answers a grep, and the exact defect row 295's AST guard refuses.
# Batching amplifies this: one row per cut hid it, six rows per cut cannot.
# Keep the LAST binding (the live one) and drop the rest; the integrator's
# gate-count auto-re-pin then corrects its value from the measured population.
_STACKABLE_PINS = ("_EXPECTED_DECLARED_GATE_COUNT",)


def _collapse_pins(text: str) -> tuple[str, int]:
    dropped = 0
    for name in _STACKABLE_PINS:
        pat = re.compile(rf"^{re.escape(name)} = \d+\n", re.M)
        hits = list(pat.finditer(text))
        if len(hits) <= 1:
            continue
        keep = hits[-1]
        for m in reversed(hits[:-1]):
            text = text[:m.start()] + text[m.end():]
            dropped += 1
    return text, dropped


CONFLICT = re.compile(r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n", re.S)

work = pathlib.Path(sys.argv[1])
out = subprocess.run(["git", "-C", str(work), "diff", "--name-only", "--diff-filter=U"],
                     capture_output=True, text=True).stdout.split()
if not out:
    print("no conflicted paths"); raise SystemExit(0)
bad = [p for p in out if p not in UNION_OK]
if bad:
    sys.exit("conflicts outside the append-only set -- refusing: " + ", ".join(bad))

for rel in out:
    p = work / rel
    s = p.read_text(encoding="utf-8")
    n = len(CONFLICT.findall(s))
    # keep ours then theirs, in that order, dropping the markers
    s2 = CONFLICT.sub(lambda m: m.group(1) + m.group(2), s)
    assert "<<<<<<<" not in s2 and ">>>>>>>" not in s2, rel
    s2, dropped = _collapse_pins(s2)
    p.write_text(s2, encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "--", rel], check=True)
    extra = f", collapsed {dropped} stacked pin binding(s)" if dropped else ""
    print(f"  union-resolved {rel}: {n} conflict(s), both sides kept{extra}")
