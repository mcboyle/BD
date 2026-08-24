"""Reading PYTHON source in assertions, without the mistake everyone makes.

@1067. `tests/shell_source.py` exists because three assertions about shell
scripts were wrong about their own INSTRUMENT rather than about the code. This
is the same fix for Python, and it was written after the identical failure
landed in the gate built to police it:

`tests/test_v3_66_1034_guards_survive_a_module_wipe.py::_module_wipe_leakers`
classifies a file as "restores its module-table wipe" by regexing the WHOLE
file. 1034 itself deletes `sys.modules` entries and never restores them -- that
is its declared job -- yet it scored as SAFE, because the restore pattern
appears twice inside its own text:

  * :203  the regex SOURCE LITERAL:  r"saved_modules|sys\\.modules\\.update\\(|..."
  * :237  an assertion MESSAGE quoting the restore idiom it recommends

Measured at v3.66.1066: census 13, budget 13, and the ONE leaker causing live
CSRF failures absent from its own list. CLAUDE.md section 0 states the rule --
"A COMMENT IS INSIDE THE DENOMINATOR OF EVERY GATE THAT READS SOURCE TEXT" --
and notes that explaining a removal by naming the removed thing recreates it.
A string literal is the same trap with different quoting.

TWO INDEPENDENT AGENTS' CLASSIFIERS WERE FOOLED BY THE SAME PROSE during the
investigation that found this, so it is a live trap rather than a theoretical
one.

    from python_source import python_code_only

    src = python_code_only("tests/some_test.py")
    assert "sys.modules.update(" in src   # now means the CALL, not a mention
"""

from __future__ import annotations

import io
import tokenize
from pathlib import Path


def python_code_only(path: Path | str) -> str:
    """The file's executable text: comments and STRING LITERALS removed.

    Docstrings, message strings and regex patterns are all `STRING` tokens, so
    one rule covers every way a file can talk ABOUT code without running it.
    Each removed token is replaced by a single space rather than deleted, so
    line and column structure survives well enough to read.

    STRIPPING STRINGS IS THE POINT, not collateral damage. A predicate hunting
    a CALL (`sys.modules.update(`) must not match the pattern that describes
    it. If you need the literals, do not use this function.

    Falls back to the raw text on a tokenize error -- a file that does not
    tokenize is a bigger problem than this helper, and returning the raw text
    keeps such a file OVER-reported rather than silently exempt, which is the
    safe direction for every caller so far.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    last_line, last_col = 1, 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            ttype, tstr, (srow, scol), (erow, ecol), _ = tok
            if srow > last_line:
                out.append("\n" * (srow - last_line))
                last_col = 0
            if scol > last_col:
                out.append(" " * (scol - last_col))
            if ttype in (tokenize.COMMENT, tokenize.STRING):
                # keep the newlines a multi-line string spans, drop its content
                out.append("\n" * (erow - srow))
                out.append(" ")
            else:
                out.append(tstr)
            last_line, last_col = erow, ecol
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    return "".join(out)


def mentions_only(path: Path | str, needle: str) -> bool:
    """True when `needle` appears ONLY in comments/strings, never in code.

    The diagnostic form: it answers "is this file talking about the thing, or
    doing it?" -- which is the question a wrong classification actually got
    backwards.
    """
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    return needle in raw and needle not in python_code_only(path)


# ── assembled literals, added @1211 ─────────────────────────────────────
# WHY A SUBSTRING TEST IS NOT ENOUGH FOR A TOMBSTONE. Every tombstone gate in
# this tree asks "does the retired thing appear anywhere". Each asked it with
# `NEEDLE in text`, and a contiguous substring is trivially avoided by BUILDING
# the string at runtime. Measured 2026-08-24 against merged main:
#
#   os.path.join("/home", "claude")          evades NEEDLE = "/"+"home"+"/"+"claude"
#   "toolchain/bin/bd-deploy" + "-manifest"  evades RETIRED = ".../bd-deploy-manifest"
#   "scripts/" + "build_release.sh"          evades the same shape
#
# and in each case the ratchet, the census and the tombstone all stayed green
# while the retired carrier was back in the tree. The needle being assembled in
# the GATE (which these gates already do, so the gate's own source is not a
# carrier) does nothing about the SUBJECT assembling it too.
#
# So: fold what the file can produce. Every string a constant expression could
# evaluate to is a string that file effectively contains.
#
# DECLARED LIMIT, because a gate must say what it cannot see: this folds
# CONSTANTS only. A needle built from a variable, an f-string interpolation, a
# computed index, `chr()` arithmetic or a value read at runtime is not folded
# and is not caught. That is the residual, and it is smaller than the residual
# it replaces -- contiguous-substring-only -- by exactly the assembled-literal
# class measured above.

def assembled_strings(path: Path | str) -> set[str]:
    """Every string this file's CONSTANT expressions can evaluate to.

    Includes plain literals, implicit and explicit concatenation of constants,
    `str.join` over constant parts, and `os.path.join` / `posixpath.join` of
    constants -- the shapes a tombstone evasion actually uses.
    """
    import ast
    import posixpath

    source = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    out: set[str] = set()

    def fold(node):
        """Return the string this node evaluates to, or None."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = fold(node.left), fold(node.right)
            if left is not None and right is not None:
                return left + right
            return None
        if isinstance(node, ast.JoinedStr):        # f-string of constants only
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    parts.append(value.value)
                else:
                    return None
            return "".join(parts)
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", getattr(func, "id", None))
            if name == "join":
                args = [fold(a) for a in node.args]
                if len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple)):
                    args = [fold(e) for e in node.args[0].elts]
                    sep = fold(func.value) if isinstance(func, ast.Attribute) else None
                    if sep is not None and all(a is not None for a in args):
                        return sep.join(args)
                    return None
                if args and all(a is not None for a in args):
                    # os.path.join / posixpath.join semantics
                    return posixpath.join(*args)
            return None
        return None

    for node in ast.walk(tree):
        folded = fold(node)
        if folded:
            out.add(folded)
    return out


def contains_assembled(path: Path | str, needle: str) -> bool:
    """True if NEEDLE appears in the file's text OR in anything it assembles."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if needle in text:
        return True
    return any(needle in s for s in assembled_strings(path))
