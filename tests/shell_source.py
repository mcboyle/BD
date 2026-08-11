"""Reading shell scripts in assertions, without the two mistakes everyone makes.

@880. Three times across v3.66.879 and 880 a test asserted something about a
shell script and was WRONG about its own instrument rather than about the code.
Both failure shapes are mechanical, both are avoidable, and both were re-made by
someone who had just fixed the other one:

1. **Line-scoped assertions about a LOOP BODY.** A script that grades several
   manifests does it with `for X in a b c; do ... "$X" ...; done`, so the
   literals appear ONLY on the `for` line and the body mentions `$X`. An
   assertion like `[l for l in lines if "a" in l and "check" in l]` therefore
   fails a CORRECT implementation for its FORM (v3.66.879 and 880 both did this,
   costing a round trip each) -- and, worse, the inverse assertion SILENTLY
   PASSES: a `CORE_FAILED` added inside the body contains no literal, so a
   per-line "must not gate" check cannot see it. That one ESCAPED its mutant.

2. **Reading prose as code.** A bare regex over the file sees comments, so the
   comment written to EXPLAIN a fix satisfies the assertion testing for it. A
   mutant removing the real behaviour then stays green. That escaped too.

Use these instead of hand-rolling either. `shell_code_only` fixes the
denominator; `blocks_containing` fixes the granularity.

    from shell_source import shell_code_only, blocks_containing

    code = shell_code_only(SETUP)
    graded = "\\n".join(blocks_containing(code, "check_requirements.py"))
    assert "requirements-cloak.txt" in graded

CLAUDE.md section 2a's rule -- cut extracted source on STRUCTURE, never on a
fixed width -- is the same lesson for a different reason, and a fixed-width
window is additionally counted by tests/test_source_windows_do_not_shift.py.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = ["shell_code_only", "enclosing_block", "blocks_containing"]


def shell_code_only(path: Path | str) -> str:
    """The script's executable text: whole-line `#` comments removed.

    Whole-line only. Stripping a trailing `# ...` would have to know whether the
    `#` sits inside a quoted string, and a wrong strip corrupts the subject
    rather than narrowing it -- which is a worse failure than the one it fixes.
    """
    text = Path(path).read_text()
    return "\n".join(l for l in text.splitlines()
                     if not l.lstrip().startswith("#"))


def enclosing_block(lines: list[str], idx: int) -> str:
    """The `for`/`while` ... `done` construct containing `lines[idx]`.

    Falls back to the single line when the target is not inside a loop, so an
    implementation that uses explicit repeated calls instead of a loop is judged
    on its own text rather than failing for its form. That fallback is the whole
    reason this is not simply "grep the for line".
    """
    start = None
    for i in range(idx, -1, -1):
        st = lines[i].strip()
        if re.match(r"^(for|while)\b", st):
            start = i
            break
        if st == "done":          # a sibling construct closed above us
            break
    if start is None:
        return lines[idx]
    depth, end = 0, len(lines) - 1
    for j in range(start, len(lines)):
        st = lines[j].strip()
        if re.search(r"\bdo\b", st):
            depth += 1
        if st.startswith("done"):
            depth -= 1
            if depth <= 0:
                end = j
                break
    return "\n".join(lines[start:end + 1])


def blocks_containing(code: str, needle: str) -> list[str]:
    """Every enclosing construct whose text reaches `needle`.

    Join the result before asserting membership. Asserting over the JOIN is what
    makes a loop-variable implementation pass and a genuinely-missing subject
    fail -- the distinction three hand-rolled versions of this got wrong.
    """
    lines = code.splitlines()
    return [enclosing_block(lines, i)
            for i, l in enumerate(lines) if needle in l]


def enclosing_if(lines: list[str], idx: int) -> str:
    """The `if` ... `fi` construct containing `lines[idx]`, or the bare line.

    The loop helpers above cover `for`/`while` and deliberately fall back to a
    single line for anything else -- correct for their subject, and a trap for
    a caller who assumes "block" means any block. Measured at v3.66.1037: an
    assertion that a preflight REFUSES was written against
    `blocks_containing`, got back the header line `if _running_pytest; then`,
    and could never see the refusal in the body. A mutant swapping the refusal
    for a printf escaped a hand-rolled character window first, then this.

    Same fallback contract as `enclosing_block`: a subject outside any `if` is
    judged on its own text rather than failing for its form.
    """
    start = None
    for i in range(idx, -1, -1):
        st = lines[i].strip()
        if re.match(r"^if\b", st):
            start = i
            break
        if st == "fi":            # a sibling construct closed above us
            break
    if start is None:
        return lines[idx]
    depth, end = 0, len(lines) - 1
    for j in range(start, len(lines)):
        st = lines[j].strip()
        if re.match(r"^if\b", st):
            depth += 1
        if st.startswith("fi"):
            depth -= 1
            if depth <= 0:
                end = j
                break
    return "\n".join(lines[start:end + 1])


def if_blocks_containing(code: str, needle: str) -> list[str]:
    """Every enclosing `if` construct whose text reaches `needle`."""
    lines = code.splitlines()
    return [enclosing_if(lines, i)
            for i, l in enumerate(lines) if needle in l]
