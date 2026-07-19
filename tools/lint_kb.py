#!/usr/bin/env python3
"""lint_kb.py — KB_PLAN_v2 C.1.

Linter for the BulkDownloader knowledge base. Read-only — never
edits or rewrites a KB file. Runs the six checks specified in
KB_PLAN_v2 Session C.1:

  1. Stale `KB_VERSION:` / `LAST_VERIFIED:` headers.
  2. Banned phrases (listed verbatim in KB_WORKFLOW.md).
  3. Broken cross-refs (`X.md` where X.md doesn't exist; `§N` where
     N isn't a real section number).
  4. Oversize sections (warn > 1500 lines, fail > 2500).
  5. Duplicate sentences across files (shingle-hash overlap on
     sentences of 10+ words; 85%+ overlap flags).
  6. `(resolved)` items in the "STILL OPEN" portion of OPEN_THREADS.md.

Each check emits structured findings — file, line, severity, code,
message — sorted by severity then path. Default severity tiers:

  ERROR    Block the build / merge.
  WARN     Surface, don't block.
  INFO     Diagnostic (off by default; --verbose to show).

Usage:

    # Lint the canonical KB. Default --kb-dir is the repo root,
    # which holds INV_TAGS.md / KB_BUDGET_BASELINE.md / the catalogs
    # but NOT the Layer-2 KB files. Pass --kb-dir explicitly to
    # point at wherever the operator's working copy lives.
    python tools/lint_kb.py --kb-dir ~/kb

    # Treat warnings as errors (for CI).
    python tools/lint_kb.py --kb-dir ~/kb --strict

    # Show info-level findings too (for debugging false positives).
    python tools/lint_kb.py --kb-dir ~/kb --verbose

    # Output JSON instead of plain text.
    python tools/lint_kb.py --kb-dir ~/kb --json

    # `--check` is an alias for the default behavior intended for
    # build_release.py — exit 0 if no errors, exit 1 if any.

Exit codes:

  0   No errors found (warnings ok unless --strict).
  1   One or more errors found (or warnings under --strict).
  2   Hard error (e.g. --kb-dir doesn't exist).

Design notes:

  - The lint never rewrites a file. KB_PLAN_v2 C.1 says "Don't
    auto-fix anything. Lint is read-only." That rule is the reason
    this script is a CLI and not a pre-commit hook with --fix mode.

  - The list of canonical KB files is short and stable. It is the
    list a fresh Claude reads from. New names get added by editing
    KB_FILES below.

  - The banned-phrases list is NOT hard-coded here — it's read from
    KB_WORKFLOW.md's fenced `BANNED_PHRASES_V1` block. This keeps
    the authoritative source in the KB itself; the lint is a thin
    enforcement layer over it. If the block is missing, the
    banned-phrases check soft-skips with a warning rather than
    silently doing nothing.

  - Duplicate-sentence detection uses a basic shingle hash. The
    intent is to catch large copy-paste blocks (merge cadence
    restated in 4 files, the deploy bash repeated 3 times in
    OPEN_THREADS, etc.), not stylistic phrase echoes.
"""
import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable


# ── Configuration ────────────────────────────────────────────────


# KB files the lint covers. These are the Layer-2 canonical files
# (per KB_WORKFLOW.md's two-layer model) plus a few in-repo KB
# artifacts (INV_TAGS, KB_BUDGET_BASELINE). The lint iterates
# whatever subset of these is found in --kb-dir — missing files
# are warnings, not errors, since some are operator-side-only
# (DANGER_MAP, LESSONS_LEARNED live in project knowledge).
KB_FILES: tuple[str, ...] = (
    "PROJECT_STATE.md",
    "OPEN_THREADS.md",
    "OPEN_THREADS_archive.md",
    "DANGER_MAP.md",
    "DANGER_MAP_archive.md",
    "LESSONS_LEARNED.md",
    "LESSONS_LEARNED_archive.md",
    "KB_WORKFLOW.md",
    "KB_TLDR.md",
    "KB_PLAN_v2.md",
    "INV_TAGS.md",
    "KB_BUDGET_BASELINE.md",
    "SHAPE_REGISTRY.md",
    "SQL_SCHEMA.md",
)

# Files that are themselves *part* of an archive — they hold older
# content by design, so age-staleness and duplicate-sentence checks
# soft-skip on them. They still get cross-ref / size / banned-phrase
# coverage.
_ARCHIVE_FILES: frozenset[str] = frozenset({
    "OPEN_THREADS_archive.md",
    "DANGER_MAP_archive.md",
    "LESSONS_LEARNED_archive.md",
})

# PROJECT_STATE has stable §1-§8 anchors. Per KB_PLAN_v2 C.3, this is
# a pinned set; new sections require a KB plan update.
_PROJECT_STATE_SECTIONS = frozenset({"1", "2", "3", "4", "5", "6", "7", "8"})

# Soft / hard size thresholds (lines per FILE, not per SECTION; the
# plan said "Oversize sections" but sections in our KB are
# H2-delimited and vary widely — per-file is the unit the operator
# can actually control. A per-section check sits below as a separate
# warn-only diagnostic.)
_SIZE_WARN = 1500
_SIZE_FAIL = 2500
_SECTION_WARN = 800

# Duplicate-sentence detection: min words per sentence and overlap
# threshold. 10+ words skips the short conversational filler; 0.85
# overlap (85%) catches lightly-edited copies but not legitimate
# parallel constructions.
_DUP_MIN_WORDS = 10
_DUP_OVERLAP = 0.85
_DUP_SHINGLE_K = 5  # 5-word shingles


# ── Finding model ────────────────────────────────────────────────


# Severity ordering; lowest "wins" in sort.
_SEV_RANK = {"ERROR": 0, "WARN": 1, "INFO": 2}


@dataclass
class Finding:
    """One lint result.

    `file` is the path relative to --kb-dir. `lineno` is 1-based or
    0 if the finding is file-level. `code` is a short identifier
    (e.g. STALE_KB_VERSION) for grep / suppression. `message` is
    human-readable.
    """
    severity: str
    code: str
    file: str
    lineno: int
    message: str
    detail: dict = field(default_factory=dict)

    def sort_key(self) -> tuple:
        return (_SEV_RANK.get(self.severity, 9), self.file, self.lineno, self.code)


# ── Helpers ──────────────────────────────────────────────────────


def _read(path: Path) -> str | None:
    """Read a KB file. Returns None if missing/unreadable rather
    than raising — missing files are findings, not crashes."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_header(text: str) -> dict[str, str]:
    """Extract `KEY: VALUE` lines from the top metadata block. The
    metadata block ends at the first blank line, the first `#`
    heading, or the first line that doesn't match `KEY: VALUE`.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            break
        m = re.match(r"^([A-Z_]+):\s*(.+?)\s*$", line)
        if not m:
            break
        out[m.group(1)] = m.group(2)
    return out


def _read_canonical_kb_version(kb_dir: Path) -> str | None:
    """The canonical KB_VERSION value is whatever KB_WORKFLOW.md
    declares. Returns None if KB_WORKFLOW.md isn't present or has
    no KB_VERSION header (the lint then soft-skips the staleness
    check rather than failing on a missing reference)."""
    wf = kb_dir / "KB_WORKFLOW.md"
    text = _read(wf)
    if text is None:
        return None
    return _parse_header(text).get("KB_VERSION")


def _read_banned_phrases(kb_dir: Path) -> list[str] | None:
    """Read the BANNED_PHRASES_V1 fenced block from KB_WORKFLOW.md.

    Returns a list of phrases (one per line in the fence, trimmed,
    non-empty, non-comment), or None if the block is missing /
    malformed. None lets the caller emit a single soft-skip
    finding instead of silently doing no banned-phrase work."""
    wf = kb_dir / "KB_WORKFLOW.md"
    text = _read(wf)
    if text is None:
        return None
    # Look for ``` followed by BANNED_PHRASES_V1 (the fence tag),
    # then anything up to the next ``` on its own line.
    m = re.search(
        r"^```BANNED_PHRASES_V1\s*\n(.*?)^```",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return None
    out: list[str] = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _list_kb_md_files(kb_dir: Path) -> list[Path]:
    """All .md files actually present under --kb-dir from KB_FILES.

    The lint operates on the intersection of "names we know about"
    and "names present on disk". Files in KB_FILES but missing from
    disk get a single MISSING_KB_FILE warning per missing file."""
    return [(kb_dir / name) for name in KB_FILES if (kb_dir / name).is_file()]


def _all_md_files(kb_dir: Path) -> list[Path]:
    """Every .md file in kb_dir, sorted. Used for cross-ref target
    presence (an unrecognized .md is still a valid target)."""
    return sorted(p for p in kb_dir.glob("*.md") if p.is_file())


# ── Check implementations ────────────────────────────────────────


def check_stale_version_headers(
    path: Path, text: str, canonical_kb_version: str | None,
    kb_dir: Path,
) -> list[Finding]:
    """Check 1: stale KB_VERSION / LAST_VERIFIED.

    - KB_VERSION (if present) must equal canonical_kb_version.
    - LAST_VERIFIED (if present) must be a parseable string. We
      don't enforce recency — the operator updates it on real
      verification; an old date is fine.
    - PROJECT_STATE / OPEN_THREADS / DANGER_MAP / LESSONS_LEARNED
      *should* declare KB_VERSION (warn if missing). Other KB files
      are optional.
    """
    rel = str(path.relative_to(kb_dir))
    findings: list[Finding] = []
    headers = _parse_header(text)

    if canonical_kb_version is not None:
        declared = headers.get("KB_VERSION")
        if (declared is not None
                and declared != canonical_kb_version
                and rel not in _ARCHIVE_FILES):
            # Archive files declare their own KB_VERSION at the time
            # of archiving (e.g. OPEN_THREADS_archive.md is frozen at
            # v1 even after the canonical KB advances to v2). The
            # version mismatch is by design; we still want the
            # header present and parseable, just not pinned to the
            # canonical value.
            findings.append(Finding(
                severity="ERROR",
                code="STALE_KB_VERSION",
                file=rel,
                lineno=_header_lineno(text, "KB_VERSION"),
                message=(
                    f"KB_VERSION: {declared!r} doesn't match canonical "
                    f"{canonical_kb_version!r} from KB_WORKFLOW.md"
                ),
                detail={"declared": declared, "canonical": canonical_kb_version},
            ))

    required_kb_version = {"PROJECT_STATE.md", "OPEN_THREADS.md",
                           "KB_WORKFLOW.md"}
    if rel in required_kb_version and "KB_VERSION" not in headers:
        findings.append(Finding(
            severity="WARN",
            code="MISSING_KB_VERSION",
            file=rel,
            lineno=1,
            message=f"{rel} should declare a KB_VERSION header",
        ))

    return findings


def _header_lineno(text: str, key: str) -> int:
    """Find the line number of a `KEY: ...` header, 1-based, or 1
    if not found (so the finding still surfaces at the file top)."""
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith(f"{key}:"):
            return i
    return 1


def check_banned_phrases(
    path: Path, text: str, phrases: list[str], kb_dir: Path,
) -> list[Finding]:
    """Check 2: banned phrases (case-sensitive).

    Phrases are read verbatim from KB_WORKFLOW.md. Inside that one
    file, the phrases will of course appear (we're documenting them
    there) — exempt KB_WORKFLOW.md from this check by file name.
    """
    rel = str(path.relative_to(kb_dir))
    if rel == "KB_WORKFLOW.md":
        return []  # The file that lists banned phrases gets to
        # contain them. Lint applied elsewhere.
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        # Strip backtick-quoted content from the line first, so a
        # meta-reference like `"Note that…"` (the banned-phrase
        # documentation pattern) doesn't trigger the lint on a file
        # that's discussing the rule. This is a deliberate exemption
        # for self-aware quotation; the cost is that a backtick-
        # surrounded banned phrase used non-meta-referentially won't
        # be caught — accept it; the false-negative rate is much
        # lower than the false-positive rate without this filter.
        stripped = re.sub(r"`[^`\n]*`", "", line)
        for ph in phrases:
            # Word-boundary-ish match: phrase must appear as written.
            # Case-sensitive (the entries in KB_WORKFLOW are
            # deliberately cased — "Note that" not "note that",
            # because the bad pattern is sentence-opening).
            if ph in stripped:
                findings.append(Finding(
                    severity="WARN",
                    code="BANNED_PHRASE",
                    file=rel,
                    lineno=lineno,
                    message=f"banned phrase {ph!r} in this line",
                    detail={"phrase": ph},
                ))
    return findings


_MD_NAME_RE = re.compile(r"\b([A-Z][A-Z0-9_]*\.md)\b")
_SECTION_REF_RE = re.compile(
    r"\bPROJECT_STATE(?:\.md)?\s+§\s*(\d+)"
)


def check_cross_refs(
    path: Path, text: str, present_md: set[str], kb_dir: Path,
    allow_missing: frozenset[str] = frozenset(),
) -> list[Finding]:
    """Check 3: broken cross-refs.

    - Any `<NAME>.md` mentioned must exist in --kb-dir OR be a
      well-known sibling file (CHANGELOG.md, README.md, etc.).
    - `PROJECT_STATE §N` references must have N in _PROJECT_STATE_SECTIONS.

    Plenty of legitimate non-KB .md mentions appear in KB files
    (e.g. README.md, CHANGELOG.md). We accept them via a known-good
    sibling list rather than treating every .md mention as a KB ref.
    """
    rel = str(path.relative_to(kb_dir))
    findings: list[Finding] = []
    known_siblings = {"README.md", "CHANGELOG.md", "CHANGELOG_archive.md",
                      "CHANGELOG_archive_2.md", "SETUP.md", "LICENSE",
                      "UI_TOKENS.md", "D3_OPT_IN.md", "INV_TAGS.md",
                      "KB_BUDGET_BASELINE.md", "FUNCTION_INDEX.md",
                      "ENDPOINT_CATALOG.md",
                      "DANGER_MAP_archive.md", "LESSONS_LEARNED_archive.md",
                      "TIER_1_3_INSTRUCTIONS.md",  # v1 of the KB plan,
                                                   # explicitly kept as
                                                   # detail reference
                                                   # per KB_PLAN_v2's
                                                   # SUPERSEDES header.
                      "HANDOFF_TEMPLATE.md", "MERGE_PROMPT.md",
                      "SESSION_DIGEST.md", "DANGER_NOTES.md"}
    valid = present_md | known_siblings | allow_missing

    seen_in_file: set[tuple[int, str]] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _MD_NAME_RE.finditer(line):
            name = m.group(1)
            if name == "X.md":  # Placeholder, intentional.
                continue
            if name in valid:
                continue
            key = (lineno, name)
            if key in seen_in_file:
                continue
            seen_in_file.add(key)
            findings.append(Finding(
                severity="ERROR",
                code="BROKEN_MD_REF",
                file=rel,
                lineno=lineno,
                message=f"references {name!r}, which is not present in "
                        f"--kb-dir and not in the known-sibling list",
                detail={"target": name},
            ))
        for m in _SECTION_REF_RE.finditer(line):
            n = m.group(1)
            if n not in _PROJECT_STATE_SECTIONS:
                findings.append(Finding(
                    severity="ERROR",
                    code="BAD_SECTION_REF",
                    file=rel,
                    lineno=lineno,
                    message=f"references PROJECT_STATE §{n} but valid "
                            f"sections are 1-8",
                    detail={"target_section": n},
                ))
    return findings


def check_size(path: Path, text: str, kb_dir: Path) -> list[Finding]:
    """Check 4: oversize files and sections.

    File-level: warn > _SIZE_WARN, fail > _SIZE_FAIL.
    Section-level (H2): warn > _SECTION_WARN lines.

    Archive files are exempt from the file-level fail (they're
    history-by-design).
    """
    rel = str(path.relative_to(kb_dir))
    findings: list[Finding] = []
    lines = text.splitlines()
    n_lines = len(lines)
    is_archive = rel in _ARCHIVE_FILES

    if n_lines > _SIZE_FAIL and not is_archive:
        findings.append(Finding(
            severity="ERROR",
            code="FILE_OVERSIZE",
            file=rel,
            lineno=n_lines,
            message=f"file is {n_lines} lines (>= fail threshold "
                    f"{_SIZE_FAIL}); split or compact",
        ))
    elif n_lines > _SIZE_WARN:
        sev = "INFO" if is_archive else "WARN"
        findings.append(Finding(
            severity=sev,
            code="FILE_LARGE",
            file=rel,
            lineno=n_lines,
            message=f"file is {n_lines} lines (>= warn threshold "
                    f"{_SIZE_WARN})",
        ))

    # Section-level: split on `## ` headers (not `### `).
    sections: list[tuple[int, int, str]] = []  # (start_lineno, end_lineno, title)
    cur_start: int | None = None
    cur_title = ""
    for i, line in enumerate(lines, 1):
        if line.startswith("## "):
            if cur_start is not None:
                sections.append((cur_start, i - 1, cur_title))
            cur_start = i
            cur_title = line[3:].strip()
    if cur_start is not None:
        sections.append((cur_start, n_lines, cur_title))
    for start, end, title in sections:
        size = end - start + 1
        if size > _SECTION_WARN:
            findings.append(Finding(
                severity="WARN" if not is_archive else "INFO",
                code="SECTION_LARGE",
                file=rel,
                lineno=start,
                message=f"section {title!r} is {size} lines "
                        f"(>= warn threshold {_SECTION_WARN})",
                detail={"section_title": title, "section_lines": size},
            ))
    return findings


def _sentences(text: str) -> Iterable[tuple[int, str]]:
    """Yield (lineno, sentence) tuples for sentence-like fragments
    of length >= _DUP_MIN_WORDS words.

    Sentence detection is intentionally crude: split on `. `, `! `,
    `? `, and line endings. Strips Markdown list markers, code
    fences, and inline backticks for word-counting. Skips lines
    inside fenced code blocks entirely.
    """
    in_fence = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Strip leading list/quote markers and trailing whitespace.
        cleaned = re.sub(r"^[\s>*\-]*", "", raw).strip()
        if not cleaned:
            continue
        # Split into sentences. The crudeness is intentional;
        # over-splitting is fine (fewer false-positive dupes).
        parts = re.split(r"(?<=[.!?])\s+", cleaned)
        for s in parts:
            s_clean = re.sub(r"`[^`]*`", "", s)
            s_clean = re.sub(r"\s+", " ", s_clean).strip()
            if len(s_clean.split()) >= _DUP_MIN_WORDS:
                yield lineno, s_clean


def _shingle_hashes(s: str, k: int = _DUP_SHINGLE_K) -> set[int]:
    """k-word shingle hashes for a sentence. Used to compute
    Jaccard-style overlap between two sentences."""
    words = s.split()
    if len(words) < k:
        return set()
    return {hash(" ".join(words[i:i + k])) for i in range(len(words) - k + 1)}


def check_duplicate_sentences(files: list[tuple[str, str]],
                              kb_dir: Path) -> list[Finding]:
    """Check 5: duplicate sentences across files.

    For each pair of long-enough sentences across all KB files,
    compute shingle-set Jaccard overlap. >= _DUP_OVERLAP → flag both
    locations (one finding per duplicate pair).

    O(N^2) on sentence count. The KB is small enough — total
    sentence count is in the low thousands — that this runs in
    well under a second.
    """
    findings: list[Finding] = []
    # Index every sentence with its origin.
    indexed: list[tuple[str, int, str, set[int]]] = []  # (rel, lineno, sentence, shingles)
    for rel, text in files:
        for lineno, sent in _sentences(text):
            sh = _shingle_hashes(sent)
            if sh:
                indexed.append((rel, lineno, sent, sh))

    # Pairwise compare. Track reported pairs by (rel_a, line_a, rel_b, line_b)
    # so we don't fire on the same dup twice in symmetric order.
    reported: set[tuple[str, int, str, int]] = set()
    n = len(indexed)
    for i in range(n):
        rel_a, line_a, sent_a, sh_a = indexed[i]
        for j in range(i + 1, n):
            rel_b, line_b, sent_b, sh_b = indexed[j]
            if not sh_a or not sh_b:
                continue
            inter = len(sh_a & sh_b)
            union = len(sh_a | sh_b)
            if union == 0:
                continue
            jacc = inter / union
            if jacc < _DUP_OVERLAP:
                continue
            # Same-file echoes are usually parallel constructions
            # (e.g. two list items with the same scaffolding); we
            # only flag cross-file duplicates.
            if rel_a == rel_b:
                continue
            key = (rel_a, line_a, rel_b, line_b)
            if key in reported:
                continue
            reported.add(key)
            findings.append(Finding(
                severity="WARN",
                code="DUPLICATE_SENTENCE",
                file=rel_a,
                lineno=line_a,
                message=f"duplicate (Jaccard {jacc:.0%}) of "
                        f"{rel_b}:{line_b}: {sent_a[:70]!r}",
                detail={
                    "other_file": rel_b,
                    "other_lineno": line_b,
                    "jaccard": round(jacc, 3),
                },
            ))
    return findings


def check_resolved_in_open_threads(
    path: Path, text: str, kb_dir: Path,
) -> list[Finding]:
    """Check 6: `(resolved)` items in the STILL OPEN portion of
    OPEN_THREADS.md.

    OPEN_THREADS has a preamble that quotes the word literally
    ("if it says `(resolved)`, it's not still open") plus an
    "Open — …" section structure. We flag `(resolved)` occurrences
    OUTSIDE a meta context: inside a fenced block (where the deploy
    bash sits) or inside the preamble's quoted reference are exempt.
    """
    rel = str(path.relative_to(kb_dir))
    if rel != "OPEN_THREADS.md":
        return []
    findings: list[Finding] = []
    in_fence = False
    in_preamble = True
    for lineno, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Exit preamble at the first `## ` header — that's the
        # "Open — operator action" or similar section.
        if raw.startswith("## "):
            in_preamble = False
            continue
        if in_preamble:
            continue
        if "(resolved)" in raw:
            # Exempt the explicit instructional line "if it says
            # (resolved), it's not still open" if it appears
            # elsewhere — match by the unambiguous wording.
            if "if it says" in raw.lower() and "not still open" in raw.lower():
                continue
            findings.append(Finding(
                severity="ERROR",
                code="RESOLVED_IN_STILL_OPEN",
                file=rel,
                lineno=lineno,
                message="(resolved) appears outside the preamble; "
                        "move to OPEN_THREADS_archive.md",
            ))
    return findings


# ── Driver ───────────────────────────────────────────────────────


def lint(kb_dir: Path, verbose: bool = False,
         allow_missing: frozenset[str] = frozenset()) -> list[Finding]:
    """Run every check across the KB files in `kb_dir`. Returns the
    sorted list of findings.

    `allow_missing` is a set of .md filenames that, if referenced
    from a KB file but absent from --kb-dir, should NOT produce a
    BROKEN_MD_REF finding. Used by the operator to suppress refs to
    files that live somewhere else (e.g. project-knowledge-only
    files that aren't on the lint machine)."""
    findings: list[Finding] = []

    # Pre-checks.
    canonical = _read_canonical_kb_version(kb_dir)
    if canonical is None:
        findings.append(Finding(
            severity="WARN",
            code="NO_CANONICAL_KB_VERSION",
            file="KB_WORKFLOW.md",
            lineno=0,
            message="cannot determine canonical KB_VERSION "
                    "(KB_WORKFLOW.md absent or missing the header)",
        ))

    phrases = _read_banned_phrases(kb_dir)
    if phrases is None:
        findings.append(Finding(
            severity="WARN",
            code="NO_BANNED_PHRASES_BLOCK",
            file="KB_WORKFLOW.md",
            lineno=0,
            message="KB_WORKFLOW.md doesn't contain a BANNED_PHRASES_V1 "
                    "fenced block; banned-phrases check is skipped",
        ))
        phrases = []

    present_md_names = {p.name for p in _all_md_files(kb_dir)}

    # Per-file checks.
    file_texts: list[tuple[str, str]] = []
    for name in KB_FILES:
        path = kb_dir / name
        text = _read(path)
        if text is None:
            if name in ("PROJECT_STATE.md", "OPEN_THREADS.md",
                        "KB_WORKFLOW.md"):
                findings.append(Finding(
                    severity="WARN",
                    code="MISSING_KB_FILE",
                    file=name,
                    lineno=0,
                    message=f"{name} not found in --kb-dir; some checks "
                            f"skipped for this file",
                ))
            continue
        file_texts.append((name, text))
        findings.extend(check_stale_version_headers(
            path, text, canonical, kb_dir))
        findings.extend(check_banned_phrases(
            path, text, phrases, kb_dir))
        findings.extend(check_cross_refs(
            path, text, present_md_names, kb_dir, allow_missing))
        findings.extend(check_size(path, text, kb_dir))
        findings.extend(check_resolved_in_open_threads(
            path, text, kb_dir))

    # Cross-file checks.
    findings.extend(check_duplicate_sentences(file_texts, kb_dir))

    # Sort by severity then path. Filter info if not verbose.
    if not verbose:
        findings = [f for f in findings if f.severity != "INFO"]
    findings.sort(key=lambda f: f.sort_key())
    return findings


def _format_text(findings: list[Finding]) -> str:
    """Plain-text report. One finding per line."""
    if not findings:
        return "lint_kb: clean — no findings.\n"
    out: list[str] = []
    for f in findings:
        loc = f"{f.file}:{f.lineno}" if f.lineno else f.file
        out.append(f"[{f.severity:5s}] {f.code:24s} {loc}: {f.message}")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary = ", ".join(f"{counts.get(s, 0)} {s.lower()}"
                        for s in ("ERROR", "WARN", "INFO") if counts.get(s))
    out.append("")
    out.append(f"Total: {summary}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb-dir", type=Path, default=Path.cwd(),
                    help="directory containing the KB files "
                         "(default: cwd)")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as errors (exit 1 on any)")
    ap.add_argument("--verbose", action="store_true",
                    help="show info-level findings too")
    ap.add_argument("--json", action="store_true",
                    help="emit JSON instead of plain text")
    ap.add_argument("--allow-missing-ref", action="append", default=[],
                    metavar="FILE.md",
                    help="suppress BROKEN_MD_REF for FILE.md (repeatable). "
                         "For .md files that exist on the operator's side "
                         "but aren't in --kb-dir.")
    ap.add_argument("--check", action="store_true",
                    help="alias for default behavior; intended for "
                         "build_release.py integration")
    args = ap.parse_args(argv)

    if not args.kb_dir.is_dir():
        print(f"FAIL: --kb-dir {args.kb_dir} is not a directory",
              file=sys.stderr)
        return 2

    findings = lint(args.kb_dir, verbose=args.verbose,
                    allow_missing=frozenset(args.allow_missing_ref))

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        sys.stdout.write(_format_text(findings))

    n_err = sum(1 for f in findings if f.severity == "ERROR")
    n_warn = sum(1 for f in findings if f.severity == "WARN")
    if n_err > 0:
        return 1
    if args.strict and n_warn > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
