"""Lint guard: no raw ``\\uXXXX`` escape may appear in JSX *text* (children).

React does NOT decode backslash-u escapes that sit in JSX text nodes -- it
renders them literally, so ``<p>Loading\\u2026</p>`` shows the eight characters
"Loading\\u2026" on screen instead of "Loading…". Inside a *quoted string* the
escape is fine: JavaScript decodes ``"Loading\\u2026"`` and ``placeholder={"…"}``
at parse time, so those are allowed.

Heuristic: strip quoted/template-literal substrings from each line, then flag any
``\\uXXXX`` that remains -- that remainder is JSX text (or bare code), never a
string body. This catches the v3.66.510 offenders (BulkEnqueue, AiAssist, Budget,
Schedules) while leaving the many legitimate in-string escapes untouched. Use a
literal Unicode character (— … –) in JSX text instead.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "frontend" / "src"

# Order matters: template literals first, then double, then single quotes.
_STRING_RE = re.compile(
    r"`(?:[^`\\]|\\.)*`"        # template literal
    r"|\"(?:[^\"\\]|\\.)*\""    # double-quoted
    r"|'(?:[^'\\]|\\.)*'"        # single-quoted
)
_BACKSLASH_U_RE = re.compile(r"\\u[0-9a-fA-F]{4}")


def _strip_strings(line: str) -> str:
    return _STRING_RE.sub("", line)


def test_no_raw_unicode_escape_in_jsx_text():
    # POPULATION: ALL-SOURCE, deliberately (row 232). React renders a raw
    # \uXXXX in a JSX TEXT node as eight literal characters wherever it renders
    # it -- in the browser and inside a spec's jsdom alike -- so BOTH are
    # defects and neither is a fixture vouching for the other. This is the same
    # judgement that leaves test_t5_t6_wired.py's /api/auth_surface scan
    # repo-wide: a spec naming a dead route is also a defect.
    #
    # THE DEFECT ROW 232 FIXES HERE IS THE OTHER ONE. `if not _SRC.is_dir():
    # return` was a SILENT PASS -- a gate that cannot see its subject reported
    # OK. CLAUDE.md A7: unavailable measurement is UNKNOWN, never OK.
    assert _SRC.is_dir(), (
        "the SPA source tree is missing at %s, so this gate scanned NOTHING. "
        "A lint over an empty population reports no offenders and proves "
        "nothing -- that is UNKNOWN, not a pass." % (_SRC,))
    files = sorted(_SRC.rglob("*.tsx"))
    assert files, (
        "no .tsx files under %s; the population is empty and this gate's "
        "verdict is vacuous" % (_SRC,))
    offenders = []
    for fp in files:
        for n, line in enumerate(fp.read_text(encoding="utf-8").splitlines(), 1):
            outside = _strip_strings(line)
            if _BACKSLASH_U_RE.search(outside):
                rel = fp.relative_to(_REPO_ROOT)
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        "Raw \\uXXXX escape found in JSX text (React renders it literally). "
        "Use the literal Unicode character instead. Offenders:\n"
        + "\n".join(offenders)
    )
