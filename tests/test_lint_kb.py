"""Self-test for KB_PLAN_v2 C.1 — `tools/lint_kb.py`.

The lint operates on KB files that don't ship in the zip (the
canonical Layer-2 KB lives in project knowledge). So the suite
can't simply run `lint_kb --kb-dir <repo>` and expect a clean
result. Instead this self-test:

  1. Imports the lint module cleanly (catches syntax errors or
     missing stdlib deps).
  2. Builds a minimal, in-memory, known-good KB directory inside
     a tmp dir and asserts the lint finds zero errors.
  3. Plants each kind of finding the lint is supposed to detect
     into the fixture, one at a time, and asserts the lint surfaces
     the expected code.
  4. Verifies the banned-phrases parser actually reads from a
     `BANNED_PHRASES_V1` fenced block — protects against a future
     edit that breaks the contract between KB_WORKFLOW.md and
     this script.

The fixtures are deliberately tiny (10-30 lines per file). The
goal is to verify the checks fire and don't fire on the right
cases, not to validate the actual KB content. The actual KB is
lint-checked by the operator running `python tools/lint_kb.py
--kb-dir ~/kb` on their workstation.
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest  # noqa: F401 — see test_endpoint_catalog_in_sync.py
              # for the runner-stub rationale.

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.lint_kb import (                                             # noqa: E402
    lint,
    _read_banned_phrases,
    _parse_header,
    KB_FILES,
)


# ── Fixture builders ─────────────────────────────────────────────


_MINIMAL_KB_WORKFLOW = """\
KB_VERSION: 2
LAST_VERIFIED: self-test, 2026-05-22

# KB WORKFLOW — minimal fixture

## Banned phrases

```BANNED_PHRASES_V1
Note that
Historically,
```

## Other content

Filler so the file isn't trivially short.
"""

_MINIMAL_PROJECT_STATE = """\
KB_VERSION: 2
LAST_VERIFIED: self-test, 2026-05-22

# PROJECT STATE — minimal fixture

## 1. What this is

A minimal fixture for lint_kb self-test. Not real project state.

## 2. Current version

Fake v0.0.1 for testing purposes only.
"""

_MINIMAL_OPEN_THREADS = """\
KB_VERSION: 2
LAST_VERIFIED: self-test, 2026-05-22

# OPEN THREADS — minimal fixture

The literal phrase `(resolved)` is mentioned here in the preamble
on purpose; if it says (resolved), it's not still open.

## Open — placeholder

Nothing actually open. This file exists only to satisfy the lint.
"""


def _write_kb(target: Path, files: dict[str, str]) -> None:
    """Write a dict of {filename: content} into target dir."""
    target.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (target / name).write_text(content, encoding="utf-8")


def _minimal_kb() -> dict[str, str]:
    """A KB that passes the lint with zero errors and zero warns
    (modulo the warns the lint emits even on a tiny KB — currently
    none of them fire on this fixture)."""
    return {
        "KB_WORKFLOW.md": _MINIMAL_KB_WORKFLOW,
        "PROJECT_STATE.md": _MINIMAL_PROJECT_STATE,
        "OPEN_THREADS.md": _MINIMAL_OPEN_THREADS,
    }


# ── Tests ────────────────────────────────────────────────────────


def test_lint_kb_module_imports():
    """Smoke: the lint module imports without error. Catches a
    future edit that breaks the script's top-level."""
    import tools.lint_kb  # noqa: F401
    assert hasattr(tools.lint_kb, "lint")
    assert hasattr(tools.lint_kb, "Finding")


def test_minimal_kb_is_clean():
    """A minimal valid KB produces no findings. If this fails, the
    lint is over-eager — e.g. flagging the literal `(resolved)` in
    the OPEN_THREADS preamble despite the exemption."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        _write_kb(kb, _minimal_kb())
        findings = lint(kb)
        # Expect zero findings; print details if any so the failure
        # message is actionable.
        if findings:
            details = "\n".join(
                f"  [{f.severity}] {f.code} {f.file}:{f.lineno} {f.message}"
                for f in findings
            )
            assert False, f"minimal KB should be clean; got:\n{details}"


def test_stale_kb_version_is_flagged():
    """A KB file declaring KB_VERSION != canonical triggers STALE_KB_VERSION."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        # Bump PROJECT_STATE's KB_VERSION to a wrong value.
        files["PROJECT_STATE.md"] = files["PROJECT_STATE.md"].replace(
            "KB_VERSION: 2", "KB_VERSION: 99")
        _write_kb(kb, files)
        findings = lint(kb)
        codes = {f.code for f in findings if f.severity == "ERROR"}
        assert "STALE_KB_VERSION" in codes, (
            f"expected STALE_KB_VERSION; got {codes!r}"
        )


def test_archive_file_kb_version_is_not_flagged():
    """Archive files declaring a stale KB_VERSION should NOT trigger
    STALE_KB_VERSION (they have their own version namespace)."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["OPEN_THREADS_archive.md"] = (
            "KB_VERSION: 1\nLAST_VERIFIED: old, 2025-01-01\n\n"
            "# OPEN_THREADS — archive\n\n(empty fixture)\n"
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = {f.code for f in findings if f.severity == "ERROR"}
        assert "STALE_KB_VERSION" not in codes, (
            f"archive file should be exempt; findings={[f.code for f in findings]!r}"
        )


def test_banned_phrase_is_flagged():
    """A banned phrase (from KB_WORKFLOW's BANNED_PHRASES_V1 block)
    appearing in another KB file triggers BANNED_PHRASE."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["PROJECT_STATE.md"] += "\nNote that this should fire.\n"
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "BANNED_PHRASE" in codes, (
            f"expected BANNED_PHRASE; got {codes!r}"
        )


def test_banned_phrase_inside_backticks_is_ignored():
    """The lint exempts banned phrases inside backtick quotes —
    so a documentation line referencing the rule doesn't trigger
    the rule itself."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["PROJECT_STATE.md"] += (
            "\nThis line documents the rule: `Note that` is banned.\n"
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "BANNED_PHRASE" not in codes, (
            f"backtick-quoted banned phrase should be exempt; "
            f"got {codes!r}"
        )


def test_broken_md_ref_is_flagged():
    """A reference to NONEXISTENT.md that isn't in known siblings
    or --allow-missing-ref triggers BROKEN_MD_REF."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["PROJECT_STATE.md"] += "\nSee TOTALLY_FAKE.md for details.\n"
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "BROKEN_MD_REF" in codes, (
            f"expected BROKEN_MD_REF; got {codes!r}"
        )


def test_allow_missing_ref_suppresses_broken_md_ref():
    """Passing the .md name via allow_missing should suppress the
    BROKEN_MD_REF finding for that name."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["PROJECT_STATE.md"] += "\nSee TOTALLY_FAKE.md for details.\n"
        _write_kb(kb, files)
        findings = lint(kb, allow_missing=frozenset({"TOTALLY_FAKE.md"}))
        codes = [f.code for f in findings]
        assert "BROKEN_MD_REF" not in codes, (
            f"BROKEN_MD_REF for allowed name should be suppressed; "
            f"got {codes!r}"
        )


def test_bad_section_ref_is_flagged():
    """A PROJECT_STATE §99 reference (99 not in 1-8) triggers
    BAD_SECTION_REF."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["KB_TLDR.md"] = (
            "KB_VERSION: 2\n\n# KB_TLDR\n\nSee PROJECT_STATE §99 for details.\n"
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "BAD_SECTION_REF" in codes, (
            f"expected BAD_SECTION_REF; got {codes!r}"
        )


def test_valid_section_ref_is_not_flagged():
    """PROJECT_STATE §1 through §8 are valid references."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["KB_TLDR.md"] = (
            "KB_VERSION: 2\n\n# KB_TLDR\n\nSee PROJECT_STATE §3 and §7.\n"
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "BAD_SECTION_REF" not in codes, (
            f"valid section refs should not be flagged; got {codes!r}"
        )


def test_resolved_in_still_open_is_flagged():
    """`(resolved)` appearing in an OPEN_THREADS section (not the
    preamble) triggers RESOLVED_IN_STILL_OPEN."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["OPEN_THREADS.md"] += (
            "\n## Open — extra\n\n"
            "### Some item (resolved) — should be moved\n\n"
            "Body text.\n"
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "RESOLVED_IN_STILL_OPEN" in codes, (
            f"expected RESOLVED_IN_STILL_OPEN; got {codes!r}"
        )


def test_resolved_in_preamble_is_not_flagged():
    """The preamble's literal mention of `(resolved)` (paired with
    'if it says ... not still open') is exempt."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        # The minimal fixture already has this. No change.
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "RESOLVED_IN_STILL_OPEN" not in codes, (
            f"preamble mention should be exempt; got {codes!r}"
        )


def test_file_oversize_is_flagged():
    """A file over the FAIL threshold (2500 lines) triggers
    FILE_OVERSIZE as an ERROR."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        # 3000 lines of filler. Each line is one char; the lint
        # counts lines, not bytes.
        files["PROJECT_STATE.md"] = (
            files["PROJECT_STATE.md"]
            + "\n"
            + "\n".join(f"filler line {i}" for i in range(3000))
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = {f.code for f in findings if f.severity == "ERROR"}
        assert "FILE_OVERSIZE" in codes, (
            f"expected FILE_OVERSIZE error; got "
            f"{[(f.severity, f.code) for f in findings]!r}"
        )


def test_duplicate_sentence_is_flagged():
    """A long sentence appearing in two KB files triggers
    DUPLICATE_SENTENCE."""
    long_sentence = (
        "The runner uses an internal dispatch order that processes "
        "stash-dedup before any download step in the priority chain."
    )
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        files = _minimal_kb()
        files["PROJECT_STATE.md"] += "\n" + long_sentence + "\n"
        files["KB_TLDR.md"] = (
            "KB_VERSION: 2\n\n# KB_TLDR\n\n" + long_sentence + "\n"
        )
        _write_kb(kb, files)
        findings = lint(kb)
        codes = [f.code for f in findings]
        assert "DUPLICATE_SENTENCE" in codes, (
            f"expected DUPLICATE_SENTENCE; got {codes!r}"
        )


def test_banned_phrases_parser_reads_fenced_block():
    """The fence parser must locate the BANNED_PHRASES_V1 block
    and return its non-empty, non-comment lines as phrases."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        (kb / "KB_WORKFLOW.md").write_text(
            "KB_VERSION: 2\n\n"
            "# KB WORKFLOW\n\n"
            "## Banned phrases\n\n"
            "```BANNED_PHRASES_V1\n"
            "First phrase\n"
            "# this is a comment\n"
            "Second phrase\n"
            "\n"
            "Third phrase\n"
            "```\n",
            encoding="utf-8",
        )
        phrases = _read_banned_phrases(kb)
        assert phrases is not None
        assert phrases == ["First phrase", "Second phrase", "Third phrase"], (
            f"unexpected parse: {phrases!r}"
        )


def test_banned_phrases_parser_returns_none_on_missing_fence():
    """If KB_WORKFLOW.md exists but has no BANNED_PHRASES_V1 fence,
    the parser returns None (the lint then emits a soft-skip
    finding rather than running the check)."""
    with tempfile.TemporaryDirectory() as td:
        kb = Path(td)
        (kb / "KB_WORKFLOW.md").write_text(
            "KB_VERSION: 2\n\n# KB WORKFLOW\n\nNo banned phrases block here.\n",
            encoding="utf-8",
        )
        phrases = _read_banned_phrases(kb)
        assert phrases is None, f"expected None; got {phrases!r}"


def test_header_parser_handles_metadata_block():
    """_parse_header should extract KEY: VALUE lines from the top
    metadata block and stop at the first non-matching line."""
    text = (
        "KB_VERSION: 2\n"
        "LAST_VERIFIED: 2026-05-22\n"
        "\n"
        "# Heading\n"
        "KB_VERSION: nope\n"  # After blank line; ignored.
    )
    h = _parse_header(text)
    assert h == {"KB_VERSION": "2", "LAST_VERIFIED": "2026-05-22"}, (
        f"unexpected: {h!r}"
    )


def test_kb_files_constant_includes_expected_names():
    """KB_FILES should at minimum include the canonical six files +
    the three archives. Acts as a contract test against the constant
    being silently truncated."""
    expected = {
        "PROJECT_STATE.md", "OPEN_THREADS.md", "OPEN_THREADS_archive.md",
        "DANGER_MAP.md", "DANGER_MAP_archive.md",
        "LESSONS_LEARNED.md", "LESSONS_LEARNED_archive.md",
        "KB_WORKFLOW.md",
    }
    missing = expected - set(KB_FILES)
    assert not missing, (
        f"KB_FILES is missing canonical entries: {missing!r}"
    )
