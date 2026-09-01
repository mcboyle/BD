#!/usr/bin/env python3
"""Re-derive the gate-scope baseline pins from the ASSEMBLED cut.

tests/test_v3_66_1173_gate_scope_debt_is_paid.py pins two values over
tests/gate_scope_baseline.txt: an entry COUNT and an identity DIGEST. Every row
that classifies a gate deletes that gate's baseline line, so both pins move by
one row's worth -- and a cut carrying five such rows moves them five times.

No member row can know the final number. Each one pins the value that was
correct when it was written (row 292 pinned 1251, row 310 pinned 1250, row 297
pinned 1251 before either), so hand-pins from different rows CONFLICT with each
other and at most one of them can be right. That is not a merge question; it is
a "compute it from the tree you actually assembled" question -- the same shape
as bd-union-resolve's _collapse_pins, where union is right for a LIST and wrong
for a SCALAR.

Run AFTER every member patch is applied and BEFORE the release trio.
Refuses rather than guesses: a missing file is UNKNOWN, not OK.
"""
import hashlib, pathlib, re, sys

cut = pathlib.Path(sys.argv[1])
base = cut / "tests" / "gate_scope_baseline.txt"
gate = cut / "tests" / "test_v3_66_1173_gate_scope_debt_is_paid.py"
if not base.is_file() or not gate.is_file():
    print(f"  baseline re-pin: UNKNOWN -- {'baseline' if not base.is_file() else 'gate'} file missing; refusing")
    raise SystemExit(1)

# EXACTLY the gate's own filter. A digest over a differently-filtered population
# is a different number that merely looks like an answer.
entries = {l.strip() for l in base.read_text(encoding="utf-8").splitlines()
           if l.strip() and not l.lstrip().startswith("#")}
count = len(entries)
digest = hashlib.sha256(("\n".join(sorted(entries)) + "\n").encode("utf-8")).hexdigest()
assert count > 0, "empty baseline -- refusing to pin a zero denominator"

src = orig = gate.read_text(encoding="utf-8")
n_cnt = len(re.findall(r"assert len\(entries\) == \d+", src))
n_dig = len(re.findall(r'BASELINE_IDS_SHA256 = "[0-9a-f]{64}"', src))
if n_cnt != 1 or n_dig != 1:
    print(f"  baseline re-pin: UNKNOWN -- {n_cnt} count pin(s), {n_dig} digest pin(s); refusing")
    raise SystemExit(1)

old_cnt = re.search(r"assert len\(entries\) == (\d+)", src).group(1)
old_dig = re.search(r'BASELINE_IDS_SHA256 = "([0-9a-f]{64})"', src).group(1)
src = re.sub(r"assert len\(entries\) == \d+", f"assert len(entries) == {count}", src, count=1)
src = re.sub(r'BASELINE_IDS_SHA256 = "[0-9a-f]{64}"',
             f'BASELINE_IDS_SHA256 = "{digest}"', src, count=1)
if src == orig:
    print(f"  baseline re-pin: already correct at {count} entries")
    raise SystemExit(0)
gate.write_text(src, encoding="utf-8")
print(f"  baseline re-pin: count {old_cnt} -> {count}, digest {old_dig[:8]} -> {digest[:8]} (derived from the assembled cut)")
