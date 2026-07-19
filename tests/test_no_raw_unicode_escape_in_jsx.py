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
    if not _SRC.is_dir():
        return  # frontend tree not present in this checkout
    offenders = []
    for fp in sorted(_SRC.rglob("*.tsx")):
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
