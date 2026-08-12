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
