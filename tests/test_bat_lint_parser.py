"""Tests for bulk_downloader._bat_lint.

Covers the paren tracker, line continuation, quote/escape semantics,
and each of the L1-L6 lint rules with isolated known-good and
known-bad fixtures.

Each test uses tiny .bat snippets so the failure mode is obvious.
The corpus is designed to catch regressions in the parser itself
(would re-flag valid syntax) AND the rule checks (would miss real
bugs).
"""
from __future__ import annotations

from bulk_downloader import _bat_lint as bl


# ──────────────────────────────────────────────────────────────────────
# Parser: paren depth tracking
# ──────────────────────────────────────────────────────────────────────

class TestDepthTracking:
    def test_flat_file_is_depth_zero(self):
        text = "@echo off\nset X=1\necho hi\n"
        for pl in bl.parse(text):
            assert pl.depth_at_start == 0
            assert pl.depth_at_end == 0

    def test_simple_if_block_increments_then_decrements(self):
        text = "if errorlevel 1 (\n    echo bad\n)\necho after\n"
        parsed = bl.parse(text)
        assert parsed[0].depth_at_start == 0
        assert parsed[0].depth_at_end == 1
        assert parsed[1].depth_at_start == 1
        assert parsed[1].depth_at_end == 1
        assert parsed[2].depth_at_start == 1
        assert parsed[2].depth_at_end == 0
        assert parsed[3].depth_at_start == 0

    def test_nested_block(self):
        text = ("if errorlevel 1 (\n"
                "    if /i not \"X\"==\"Y\" (\n"
                "        echo inner\n"
                "    )\n"
                ")\n")
        parsed = bl.parse(text)
        depths = [(p.depth_at_start, p.depth_at_end) for p in parsed]
        assert depths == [(0, 1), (1, 2), (2, 2), (2, 1), (1, 0)]

    def test_paren_in_quoted_string_is_not_depth(self):
        text = 'echo "this (paren) is quoted"\n'
        parsed = bl.parse(text)
        assert parsed[0].depth_at_end == 0

    def test_escaped_paren_is_not_depth(self):
        text = 'echo ^(slow^)\n'
        parsed = bl.parse(text)
        assert parsed[0].depth_at_end == 0

    def test_caret_caret_is_literal_caret(self):
        # `^^` is a literal `^`, the second `^` is NOT an escape
        text = 'echo a^^b\n'
        parsed = bl.parse(text)
        assert parsed[0].depth_at_end == 0
        # And paren depth tracking still works after literal caret
        text2 = 'echo a^^b (\n)\n'
        parsed2 = bl.parse(text2)
        assert parsed2[0].depth_at_end == 1
        assert parsed2[1].depth_at_end == 0


# ──────────────────────────────────────────────────────────────────────
# Parser: ^-line-continuation joining
# ──────────────────────────────────────────────────────────────────────

class TestLineContinuation:
    def test_simple_continuation_joins(self):
        text = "echo hello ^\n    world\n"
        parsed = bl.parse(text)
        assert len(parsed) == 1
        assert "hello" in parsed[0].text
        assert "world" in parsed[0].text
        assert parsed[0].physical_first == 1
        assert parsed[0].physical_last == 2

    def test_continuation_preserves_depth_across_join(self):
        text = ("if X==Y (\n"
                "    echo a ^\n"
                "        b\n"
                ")\n")
        parsed = bl.parse(text)
        # 3 logical lines: opener, joined echo, closer
        assert len(parsed) == 3
        assert parsed[1].depth_at_start == 1
        assert "a" in parsed[1].text and "b" in parsed[1].text

    def test_caret_inside_quotes_is_not_continuation(self):
        text = 'echo "trailing caret ^"\nnext line\n'
        parsed = bl.parse(text)
        # The `^"` inside quotes is an escaped quote per cmd, but at
        # minimum the trailing-caret-in-quotes should NOT continue.
        assert len(parsed) == 2

    def test_doubled_caret_at_eol_is_not_continuation(self):
        # `^^` at end-of-line is literal `^`, NOT a continuation
        text = "echo done^^\necho next\n"
        parsed = bl.parse(text)
        assert len(parsed) == 2


# ──────────────────────────────────────────────────────────────────────
# Rule L4: unescaped () in command args at depth > 0
# (The v3.63.7 bug class.)
# ──────────────────────────────────────────────────────────────────────

class TestL4ParenInArg:
    def test_v3_63_7_bug_flagged(self):
        # The exact line from install_ai_ollama.bat L47 pre-fix
        text = ("if errorlevel 1 (\n"
                "    echo will install but run on CPU (slow).\n"
                ")\n")
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert len(l4) == 1
        assert l4[0].line == 2

    def test_escaped_paren_in_arg_is_clean(self):
        text = ("if errorlevel 1 (\n"
                "    echo will install but run on CPU ^(slow^).\n"
                ")\n")
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert l4 == []

    def test_paren_in_quoted_arg_is_clean(self):
        # `set /p "VAR=prompt with (parens):"` — parens inside quotes
        text = ("if errorlevel 1 (\n"
                "    set /p \"X=Continue? (Y/N): \"\n"
                ")\n")
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert l4 == []

    def test_paren_in_arg_at_depth_zero_is_clean(self):
        # Top-level lines aren't inside a block; the bug class
        # only fires inside (...). e.g. file header echo with parens.
        text = 'echo BulkDownloader (Windows install)\n'
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert l4 == []

    def test_structural_paren_lines_not_flagged(self):
        # Lines that are pure block structure — their parens are
        # block delimiters, not args.
        text = ("if X==Y (\n"
                ") else (\n"
                "    echo middle\n"
                ")\n")
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert l4 == []

    def test_v3_63_7_second_bug_at_L93(self):
        # The other instance from install_ai_ollama.bat
        text = ('if errorlevel 1 (\n'
                '    echo ERROR: download failed (exit !RC!). '
                'Check network.\n'
                ')\n')
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert len(l4) == 1


# ──────────────────────────────────────────────────────────────────────
# (L5/L6 rules — labels and :: inside (...) — were removed. The
# v3.63.7 diagnosis that labels-inside-blocks were illegal was
# disproved by the bisector (the real bug was L4 unescaped parens).
# Shipped .bat files like gpu_check.bat use labels inside (...) blocks
# legitimately, so flagging them would produce false positives. Tests
# for those rules were removed alongside the rules.)


# ──────────────────────────────────────────────────────────────────────
# Rule L3: for ... in (...) with unquoted < or >
# (Pre-existing rule, tested for regression.)
# ──────────────────────────────────────────────────────────────────────

class TestL3ForRedirect:
    def test_unquoted_redirect_in_for_flagged(self):
        text = "for /f %%G in ('cmd > out.txt') do echo %%G\n"
        issues = bl.lint_text(text)
        l3 = [i for i in issues if i.rule == "L3_for_redirect"]
        assert len(l3) == 1

    def test_escaped_redirect_in_for_clean(self):
        # The canonical pattern: 2^>nul to suppress stderr
        text = "for /f %%G in ('cmd 2^>nul') do echo %%G\n"
        issues = bl.lint_text(text)
        l3 = [i for i in issues if i.rule == "L3_for_redirect"]
        assert l3 == []

    def test_quoted_redirect_in_for_clean(self):
        # The < or > is inside a quoted substring of the in() arg —
        # not a redirection.
        text = "for /f %%G in (\"a < b\") do echo %%G\n"
        issues = bl.lint_text(text)
        l3 = [i for i in issues if i.rule == "L3_for_redirect"]
        assert l3 == []


# ──────────────────────────────────────────────────────────────────────
# REM lines should be ignored by L4 (no false positive)
# ──────────────────────────────────────────────────────────────────────

class TestRemHandling:
    def test_rem_inside_block_with_parens_is_clean(self):
        text = ("if errorlevel 1 (\n"
                "    REM this comment (with parens) is fine\n"
                "    echo ok\n"
                ")\n")
        issues = bl.lint_text(text)
        # No L4 false positive
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert l4 == []

    def test_rem_with_slash_form(self):
        # `REM/` is treated as REM by cmd in some forms
        text = ("if errorlevel 1 (\n"
                "    REM/comment with (parens)\n"
                ")\n")
        issues = bl.lint_text(text)
        l4 = [i for i in issues if i.rule == "L4_paren_in_arg"]
        assert l4 == []

    def test_rem_does_not_alter_depth(self):
        # A REM line that LOOKS like it has parens shouldn't change
        # the block depth we track for following lines.
        text = ("if errorlevel 1 (\n"
                "    REM open paren (((\n"
                "    echo still inside\n"
                ")\n")
        # No assertion crash; just confirm parser doesn't blow up
        parsed = bl.parse(text)
        # The REM is depth 1 throughout — its parens DO change depth
        # for cmd's literal counter, but our parser policy ignores
        # REM parens for depth (matches modern Windows behavior).
        # The 'echo still inside' line should be at depth 1.
        echo_pl = [p for p in parsed if 'echo still' in p.text][0]
        assert echo_pl.depth_at_start == 1


# ──────────────────────────────────────────────────────────────────────
# lint_bytes: byte-level concerns
# ──────────────────────────────────────────────────────────────────────

class TestLintBytes:
    def test_pure_ascii_crlf_clean(self):
        raw = b"@echo off\r\necho hi\r\n"
        r = bl.lint_bytes(raw)
        assert r["non_ascii"] == 0
        assert r["crlf_ok"] is True
        assert r["bare_lf"] == 0
        assert r["issues"] == []

    def test_non_ascii_byte_counted(self):
        raw = "echo \u2014\r\n".encode("utf-8")
        r = bl.lint_bytes(raw)
        assert r["non_ascii"] == 3   # em-dash is 3 bytes in UTF-8

    def test_bare_lf_detected(self):
        raw = b"@echo off\necho hi\n"
        r = bl.lint_bytes(raw)
        assert r["crlf_ok"] is False
        assert r["bare_lf"] == 2

    def test_mixed_endings_counts_bare(self):
        raw = b"a\r\nb\nc\r\n"
        r = bl.lint_bytes(raw)
        assert r["bare_lf"] == 1


# ──────────────────────────────────────────────────────────────────────
# Integration: run against the actual shipped .bat files in the repo.
# This is the safety net for false positives.
# ──────────────────────────────────────────────────────────────────────

class TestRepoBatFiles:
    def test_all_shipped_bat_files_lint_clean(self):
        """The v3.63.7 release has 10 shipped .bat files. After the
        L47/L93 fixes, they should all lint clean. Any new issue
        here is either a real regression OR a parser false positive
        — both are bugs worth catching."""
        from pathlib import Path
        repo = Path(__file__).resolve().parent.parent
        bats = sorted(repo.glob("*.bat"))
        assert len(bats) >= 8, f"expected >= 8 .bat files, got {len(bats)}"
        problems = []
        for p in bats:
            r = bl.lint_bytes(p.read_bytes())
            if r["non_ascii"] or not r["crlf_ok"] or r["issues"]:
                problems.append((p.name, r))
        assert not problems, (
            f"shipped .bat files have lint issues:\n" +
            "\n".join(f"  {n}: {r}" for n, r in problems))
