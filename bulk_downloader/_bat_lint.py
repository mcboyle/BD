"""Batch-file paren-aware tokenizer + lint rules.

Designed for `bat_lint()` in dev_suite.py. Kept as a standalone module
so it can be unit-tested without touching the rest of the suite.

The core abstraction: walk a .bat file line by line, while maintaining
two pieces of state — paren depth (how many open `(...)` blocks we are
inside) and current line's command type — and emit a list of issues.

cmd.exe parsing rules encoded here (Windows Server 2025 build 26100
calibration, cross-checked against earlier Windows builds where
documented):

  1. `^` at end of a logical line continues to the next line (the `^`
     itself is consumed). Continuation does NOT happen inside double
     quotes.
  2. `^X` escapes X. `^(`, `^)`, `^<`, `^>`, `^&`, `^|`, `^"` are
     literal characters and do NOT participate in paren depth or
     redirection detection. The `^` is consumed.
  3. Inside `"..."` (unescaped quotes), `(`, `)`, `<`, `>` are literal
     and do not count.
  4. REM lines (start of logical line, case-insensitive `REM` followed
     by whitespace or EOL) are comments. Their parens are NOT counted
     for depth — verified on real Windows 10/11/Server 2019+/2022/2025.
     (Older NT-era cmd.exe did count them; we ignore that case.)
  5. `::label` is a comment ONLY at depth 0. Inside `(...)` it is
     malformed and breaks parse — flagged.
  6. `:label` is a label declaration. Legal only at depth 0; inside
     `(...)` cmd's parser breaks on the next non-whitespace token —
     flagged.
  7. `(` outside quotes/escapes increments depth. `)` outside
     quotes/escapes decrements depth.
  8. Lines starting (after whitespace) with `if`, `for`, `else`, `(`,
     `)` are "structural" — their parens are part of block syntax
     and only the BODY parens of an inline-body block are command
     args. We take the conservative view: structural parens are not
     flagged, command parens at depth > 0 ARE flagged unless escaped.

Public API:
  parse(text)            -> list[ParsedLine] with line state info
  lint_text(text)        -> list[Issue] (uses parse plus rule checks)
  lint_bytes(raw)        -> dict (per-file: non-ASCII + CRLF + lint)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ──────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ParsedLine:
    """A single logical line after CRLF strip and ^-continuation
    joining. `physical_first` is the 1-based line number where the
    logical line started; `physical_last` is where it ended (same as
    first if no continuation)."""
    text: str
    physical_first: int
    physical_last: int
    depth_at_start: int   # block depth when this line starts
    depth_at_end: int     # block depth after this line


@dataclass
class Issue:
    line: int             # physical line where issue starts
    rule: str             # short identifier, e.g. "L4_paren_in_arg"
    detail: str           # human-readable description


# ──────────────────────────────────────────────────────────────────────
# Lexer: walks one logical line and yields tokens by type so depth
# updates and rule checks share a single source of truth.
# ──────────────────────────────────────────────────────────────────────

# A "token" here is just a (kind, value, col) triple. We don't build a
# full AST — we just need to know where each special character is and
# whether it's escaped/quoted, so that rule checks can look at positions
# without re-implementing the escape logic.

def _walk_chars(line: str):
    """Yield (col, char, in_quotes, escaped) for each character of the
    line. col is 0-based.

    Quotes toggle on unescaped `"`. The toggle happens BEFORE the
    quote character is yielded (so the `"` itself is reported with
    the NEW state — this matches cmd's behavior where the quote is
    part of the inside of the string).

    `^` at end of line is NOT consumed here — that's handled by the
    line-continuation pre-pass before we ever get to this function.
    """
    i = 0
    in_quotes = False
    n = len(line)
    while i < n:
        c = line[i]
        if c == '^' and i + 1 < n and not in_quotes:
            # ^X: escape the next char. The ^ is consumed; the next
            # char is yielded with escaped=True.
            nxt = line[i + 1]
            yield (i + 1, nxt, in_quotes, True)
            i += 2
            continue
        if c == '"' and not in_quotes:
            in_quotes = True
            yield (i, c, in_quotes, False)
            i += 1
            continue
        if c == '"' and in_quotes:
            yield (i, c, in_quotes, False)
            in_quotes = False
            i += 1
            continue
        yield (i, c, in_quotes, False)
        i += 1


def _classify_line_parens(line: str):
    """Walk a logical line and yield (col, ch, role) for each `(`
    and `)` outside quotes and escapes. `role` is one of:
      'block_open', 'block_close'  — counts toward block depth
      'for_open', 'for_close'      — part of a for-arg list
      'unmatched_close'            — `)` that closes a for-arg too
                                     deep or stray text
    REM / `::` lines are entirely skipped (no parens yielded).
    """
    stripped = line.lstrip()
    if _is_rem_line(stripped):
        return
    if stripped.startswith("::"):
        return

    # Pre-classify the line: if it starts with `for ` (or `for/`),
    # the FIRST `(` we encounter outside quotes is a for-arg opener.
    # Subsequent parens at the same nesting are for-arg internals.
    # After the for-arg list closes, the rest of the line is normal
    # (so a `do (` opens a block).
    lead = stripped.lower()
    starts_with_for = (lead.startswith("for ") or
                       lead.startswith("for/"))

    state = "for_pre_open" if starts_with_for else "normal"
    for_args_depth = 0

    for col, ch, in_q, esc in _walk_chars(line):
        if in_q or esc:
            continue
        if ch not in "()":
            continue

        if state == "for_pre_open":
            if ch == '(':
                state = "for_in_args"
                for_args_depth = 1
                yield (col, ch, "for_open")
                continue
            else:
                # Stray `)` before `(` in a 'for ' line: weird, treat
                # as unmatched. Fall through to normal handling.
                state = "normal"

        if state == "for_in_args":
            if ch == '(':
                for_args_depth += 1
                yield (col, ch, "for_open")
                continue
            if ch == ')':
                for_args_depth -= 1
                yield (col, ch, "for_close")
                if for_args_depth == 0:
                    state = "normal"
                continue

        # state == "normal"
        if ch == '(':
            yield (col, ch, "block_open")
        else:
            yield (col, ch, "block_close")


def _line_paren_delta(line: str) -> int:
    """How much does this logical line change BLOCK paren depth?
    See `_classify_line_parens` for the semantics."""
    delta = 0
    for _col, _ch, role in _classify_line_parens(line):
        if role == "block_open":
            delta += 1
        elif role == "block_close":
            delta -= 1
    return delta


def _is_rem_line(stripped: str) -> bool:
    """Is this logical line a REM comment? `REM` (case-insensitive)
    followed by whitespace, `:`, `/`, or end of line."""
    if len(stripped) < 3:
        return False
    if stripped[:3].lower() != "rem":
        return False
    if len(stripped) == 3:
        return True
    return stripped[3] in " \t/:"


def _is_label_line(stripped: str) -> Optional[str]:
    """If this line is a `:label` declaration, return the label.
    Returns None for non-labels. `::comment` returns the empty string
    by convention (caller treats specially)."""
    if not stripped.startswith(":"):
        return None
    if stripped.startswith("::"):
        return ""        # special: comment form
    # Real label: :name (alphanumerics + underscore)
    m = re.match(r":([A-Za-z_][\w]*)\s*$", stripped)
    if m:
        return m.group(1)
    # Something like ":foo bar" — cmd treats trailing junk as the
    # label's "comment", but it's still a label.
    m = re.match(r":([A-Za-z_][\w]*)", stripped)
    if m:
        return m.group(1)
    return None


# ──────────────────────────────────────────────────────────────────────
# Parser: turn raw text into a list of ParsedLine, joining
# ^-continuations and tracking depth across the file.
# ──────────────────────────────────────────────────────────────────────

def parse(text: str) -> List[ParsedLine]:
    """Parse a .bat file's text. `text` should be decoded already.
    The parser joins `^`-continuations into single logical lines and
    tracks block depth."""
    raw_lines = text.splitlines()
    parsed: List[ParsedLine] = []
    depth = 0

    i = 0
    while i < len(raw_lines):
        physical_first = i + 1
        # Strip trailing CR if it leaked through (splitlines usually
        # handles it but be defensive)
        ln = raw_lines[i].rstrip("\r")
        # Handle ^-continuation: but only if the ^ at end is NOT
        # itself escaped and is NOT inside an open quote that
        # spans onto here.
        while _ends_with_unescaped_caret_outside_quotes(ln) and \
                i + 1 < len(raw_lines):
            # Drop the trailing ^, append next physical line
            ln = ln[:-1] + raw_lines[i + 1].rstrip("\r")
            i += 1
        physical_last = i + 1

        start_depth = depth
        delta = _line_paren_delta(ln)
        depth_after = depth + delta
        parsed.append(ParsedLine(
            text=ln,
            physical_first=physical_first,
            physical_last=physical_last,
            depth_at_start=start_depth,
            depth_at_end=depth_after,
        ))
        depth = depth_after
        i += 1

    return parsed


def _ends_with_unescaped_caret_outside_quotes(line: str) -> bool:
    """Does `line` end with a `^` that is not itself escaped and not
    inside a still-open double-quoted string?"""
    s = line.rstrip(" \t")
    if not s.endswith("^"):
        return False
    # Walk to see if the trailing ^ is escaped (^^) and to track
    # whether we're inside an unclosed quote at the end.
    in_q = False
    i = 0
    while i < len(s) - 1:
        c = s[i]
        if c == '^' and i + 1 < len(s) - 0 and not in_q:
            # Skip the escaped char
            i += 2
            continue
        if c == '"':
            in_q = not in_q
        i += 1
    if in_q:
        return False
    # Now check: is the final ^ at position len(s)-1 the start of an
    # ^X pair where X is also a ^? That would mean it's `^^`, an
    # escaped caret literal — not a continuation.
    if len(s) >= 2 and s[-2] == '^':
        # `...^^` — is the second-to-last `^` itself part of an
        # earlier escape? Count consecutive carets from the end.
        run = 0
        j = len(s) - 1
        while j >= 0 and s[j] == '^':
            run += 1
            j -= 1
        # An odd run-length ends in an unescaped caret (continuation);
        # even run-length means all carets are paired (literal `^`s).
        return run % 2 == 1
    return True


# ──────────────────────────────────────────────────────────────────────
# Rule checks
# ──────────────────────────────────────────────────────────────────────

# Lines starting with these keywords (case-insensitive, after leading
# whitespace) are control-flow scaffolding — their parens are
# structural and exempt from L4. A bare `(` or `)` line is also
# structural.
_STRUCTURAL_LEADERS = ("if ", "if/", "for ", "for/", "else ", "else",
                       "(", ")")


def _is_structural_line(stripped: str) -> bool:
    """Is this line just block-structure scaffolding (no command body
    whose parens we should police)? Examples: `if errorlevel 1 (`,
    `) else (`, `)`, `for /f ... do (`."""
    if not stripped:
        return True
    if stripped in ("(", ")"):
        return True
    low = stripped.lower()
    if low.startswith(_STRUCTURAL_LEADERS):
        # if/for can have a body on the same line that contains
        # commands — `if errorlevel 1 echo hi`. To keep this check
        # conservative, only treat the leader as fully structural
        # when the line ends with `(` (opening a block) or `)`
        # (closing one). Otherwise treat it as a regular command
        # that happens to start with `if`/`for`.
        if stripped.endswith(("(", ")")):
            return True
    return False


def _find_unescaped_parens_in_args(line: str) -> List[int]:
    """Return columns (0-based) of `(` / `)` characters that are
    command-argument parens — i.e. unescaped, unquoted, NOT part of a
    `for ... in (...)` argument list, AND that pair up within the
    same line at depth > 0.

    The v3.63.7 bug class is exactly this: a line like
    `echo will install ... (slow).` inside a `if (...)` block contains
    a `(` followed by a `)` on the same line, both outside quotes.
    cmd.exe's paren tracker treats them as opening and closing block
    delimiters, miscounting the outer block depth, and the next char
    after the spurious close (often a `.`) becomes a top-level token
    that cmd can't parse.

    Legitimate block-control lines yield only an opener (line ends
    with `(`) or only a closer (line starts with `)`), never both —
    that's the discriminator.
    """
    classified = list(_classify_line_parens(line))
    block_parens = [(col, ch) for col, ch, role in classified
                    if role in ("block_open", "block_close")]
    if len(block_parens) < 2:
        # Only an opener or only a closer (or nothing) — this is
        # legitimate block-control scaffolding; not L4.
        return []
    # Two or more block parens on one line. Check if they pair up
    # (net delta zero) — that's the v3.63.7 shape.
    delta = 0
    for _col, ch in block_parens:
        if ch == '(':
            delta += 1
        else:
            delta -= 1
        if delta < 0:
            # `)` before any `(` — line opens with a closer and may
            # contain a re-opener (e.g. `) else (`). Those are
            # structural too. Treat them as not-L4.
            return []
    if delta != 0:
        # Net non-zero — e.g. two openers in `if X ( for %%Y in (..)
        # do (` — also structural (multiple block openers chained on
        # one line, no in-line closers).
        return []
    # Net zero: there's at least one open+close pair on the line.
    # That's the bug shape.
    return [col for col, _ch in block_parens]


def _check_for_redirection_in_loop(line: str) -> bool:
    """L3: in a `for ... in (...)` line, are there unquoted < or >
    inside the parenthesized argument? Returns True if a violation
    is present."""
    low = line.lower().strip()
    if not (low.startswith("for ") or low.startswith("for/")):
        return False
    if " in (" not in low:
        return False
    # Walk only the `in (...)` portion. We can't trust .split() on
    # the original line because case matters less than position;
    # find the literal " in (" in a lowercased copy, then slice the
    # original at that position.
    pos = low.find(" in (")
    after = line[pos + len(" in ("):]
    # Strip quoted substrings, then check for < or > that are NOT
    # escaped. We walk chars to respect ^-escaping.
    for col, ch, in_q, esc in _walk_chars(after):
        if in_q or esc:
            continue
        if ch in "<>":
            return True
    return False


def lint_text(text: str) -> List[Issue]:
    """Run all rules over decoded .bat text. Returns a list of issues
    sorted by physical line."""
    issues: List[Issue] = []
    parsed = parse(text)

    for pl in parsed:
        stripped = pl.text.strip()

        # ── L3: unquoted < or > inside for ... in (...) ────────────
        if _check_for_redirection_in_loop(pl.text):
            issues.append(Issue(
                line=pl.physical_first,
                rule="L3_for_redirect",
                detail=("unquoted redirection (< or >) inside a "
                        "`for ... in (...)` loop — cmd.exe parses "
                        "those as redirection and aborts"),
            ))

        # REM lines: comments, skip the rest of the rules.
        if _is_rem_line(stripped):
            continue

        # NOTE: rules L5 (label inside (...)) and L6 (:: comment
        # inside (...)) were initially included in this lint, based
        # on the v3.63.7 session's first diagnosis of `install_ai_ollama.bat`.
        # That diagnosis was disproved by the bisector — the actual bug
        # was unescaped parens (L4), not in-block labels. Shipped
        # `.bat` files in this repo (e.g. gpu_check.bat) DO use labels
        # inside (...) blocks successfully, so the rule would produce
        # false positives. Rules L5/L6 are intentionally not enforced.

        # ── L4: unescaped () in command args at depth > 0 ──────────
        if pl.depth_at_start > 0 and not _is_structural_line(stripped):
            cols = _find_unescaped_parens_in_args(pl.text)
            if cols:
                # On a command line at depth > 0, EVERY unescaped
                # paren is suspicious. Report the first one.
                issues.append(Issue(
                    line=pl.physical_first,
                    rule="L4_paren_in_arg",
                    detail=(f"unescaped `{pl.text[cols[0]]}` in "
                            "command args while inside a (...) "
                            "block — escape as `^(` / `^)`. "
                            "cmd.exe counts unescaped parens in "
                            "command arguments and miscloses the "
                            "outer block"),
                ))

    return issues


def lint_bytes(raw: bytes) -> dict:
    """Lint a .bat file's raw bytes. Returns:
        {
            'non_ascii': int,
            'crlf_ok': bool,
            'bare_lf': int,
            'issues': [Issue, ...],
        }
    """
    non_ascii = sum(1 for b in raw if b > 127)
    crlf_count = raw.count(b"\r\n")
    lf_count = raw.count(b"\n")
    bare_lf = lf_count - crlf_count
    text = raw.decode("utf-8", "replace")
    issues = lint_text(text)
    return {
        "non_ascii": non_ascii,
        "crlf_ok": (bare_lf == 0),
        "bare_lf": bare_lf,
        "issues": issues,
    }
