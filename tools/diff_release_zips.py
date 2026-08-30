#!/usr/bin/env python3
"""diff_release_zips.py — standalone release-artifact comparator."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from typing import Dict, List

FORBIDDEN_SUFFIX = (".pyc", ".pyo", ".wacz")
FORBIDDEN_NAME = ("sites_config.json", "downloader_history.db",
                  "downloader_history.db-wal", "downloader_history.db-shm",
                  ".DS_Store", "debug.flag")
FORBIDDEN_SEGMENT = (
    "__pycache__/", "node_modules/", "/captures/", "captures/",
    "/screenshots/", "screenshots/",
    # v3.66.748 (audit R18): Hypothesis's example database + constants cache is
    # pure test-run state -- regenerated on demand, never source. 27 entries
    # were SHIPPING in the release zip while this gate reported clean, because
    # its denominator did not contain the thing being asked about. The gate that
    # exists to catch test-runner junk could not see the test-runner junk.
    ".hypothesis/",
)
FORBIDDEN_SENSITIVE = re.compile(r'(\.env|\.pem$|\.key$|\.db($|-wal|-shm))')

# Redacted recognizer fixtures under tests/fixtures/ and the explicitly
# synthetic corpus under tests/capture_corpus_synthetic/ are the intended
# in-repo regression captures, so they are exempt from the .wacz forbidden
# rule. Every other .wacz stays forbidden — the F2-LOCAL posture for real
# captures is unchanged.
def _is_allowed_fixture(n: str) -> bool:
    return (
        n.startswith("tests/fixtures/") and n.endswith(".redacted.wacz")
    ) or (
        n.startswith("tests/capture_corpus_synthetic/")
        and n.endswith(".wacz")
    )


def shas(zip_path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            out[name] = hashlib.sha256(zf.read(name)).hexdigest()
    return out


def forbidden_artifacts(names: List[str]) -> List[str]:
    bad = []
    for n in names:
        if _is_allowed_fixture(n):
            continue
        if any(seg in n for seg in FORBIDDEN_SEGMENT):
            bad.append(n)
        elif n.endswith(FORBIDDEN_SUFFIX):
            bad.append(n)
        elif n.rsplit("/", 1)[-1] in FORBIDDEN_NAME:
            bad.append(n)
        elif FORBIDDEN_SENSITIVE.search(n):
            bad.append(n)
    return sorted(set(bad))


def version_of(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if n.endswith("bulk_downloader/__init__.py"):
                txt = zf.read(n).decode("utf-8", "replace")
                m = re.search(r'__version__\s*=\s*"([0-9.]+)"', txt)
                if m:
                    return m.group(1)
    return ""


def changelog_top(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path) as zf:
        for n in zf.namelist():
            if n.rsplit("/", 1)[-1] == "CHANGELOG.md":
                txt = zf.read(n).decode("utf-8", "replace")
                m = re.search(r'##\s*v?([0-9]+\.[0-9]+\.[0-9]+)', txt)
                if m:
                    return m.group(1)
    return ""


_CHANGELOG_PREAMBLE_BLOCKS = (
    (
        "Versioning is loose — pre-3.43 was unstructured, 3.43+ is grouped by",
        "phase number. Notes here cover recent releases. For pre-v3.46 history",
        "see [CHANGELOG_archive.md](CHANGELOG_archive.md).",
    ),
    (
        "Versioning is loose — pre-3.43 was unstructured, 3.43+ is grouped by",
        "phase number. Notes here cover recent releases. The former pre-v3.46",
        "archive is not present in this repository; consult source-control history.",
    ),
)


def changelog_preamble_layout(text: str) -> Dict:
    """Measure content between the independently parsed preamble and first H2.

    The first release header cannot define the start of its own denominator:
    that is how a headerless draft above it escaped at v3.66.1240.  The
    preamble is instead one of the two explicit blocks carried by accepted
    history.  A candidate cannot redefine an orphan bullet block as its own
    preamble. Blank separator lines are allowed; any other line before the
    first level-two header is orphan content.
    """
    lines = text.splitlines()
    if not lines:
        return {"status": "unknown", "reason": "CHANGELOG.md is empty",
                "offending_lines": []}
    if lines[0].strip() != "# Changelog":
        return {"status": "unknown",
                "reason": "the '# Changelog' title is absent or displaced",
                "offending_lines": []}

    cursor = 1
    while cursor < len(lines) and not lines[cursor].strip():
        cursor += 1
    matched_preamble = next(
        (
            block for block in _CHANGELOG_PREAMBLE_BLOCKS
            if tuple(lines[cursor:cursor + len(block)]) == block
        ),
        None,
    )
    if matched_preamble is None:
        return {"status": "unknown",
                "reason": "the explicit prose preamble is not recognized",
                "offending_lines": []}
    cursor += len(matched_preamble)

    headers = [
        index for index, line in enumerate(lines)
        if line.startswith("## ")
    ]
    if not headers:
        return {"status": "unknown",
                "reason": "CHANGELOG.md has no level-two header",
                "offending_lines": []}
    first_header = headers[0]
    if first_header < cursor:
        return {"status": "unknown",
                "reason": "the first level-two header precedes the preamble end",
                "offending_lines": []}

    offending = [
        index + 1 for index in range(cursor, first_header)
        if lines[index].strip()
    ]
    return {
        "status": "finding" if offending else "ok",
        "reason": "content exists after the preamble and before the first header"
                  if offending else "",
        "preamble_end_line": cursor,
        "first_header_line": first_header + 1,
        "offending_lines": offending,
    }


def changelog_layout(zip_path: str) -> Dict:
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.rsplit("/", 1)[-1] != "CHANGELOG.md":
                continue
            try:
                text = zf.read(name).decode("utf-8")
            except UnicodeDecodeError as exc:
                return {"status": "unknown",
                        "reason": "CHANGELOG.md is not valid UTF-8: %s" % exc,
                        "offending_lines": []}
            return changelog_preamble_layout(text)
    return {"status": "unknown", "reason": "CHANGELOG.md is absent",
            "offending_lines": []}


def _line_ranges(line_numbers: List[int]) -> str:
    ranges = []
    start = previous = line_numbers[0]
    for line_number in line_numbers[1:]:
        if line_number == previous + 1:
            previous = line_number
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = line_number
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


# A bundler (vite) emits content-hashed asset filenames like
# `dist/assets/index-<hash>.js` / `.css` / `.js.map`. A legitimate SPA
# rebuild therefore REMOVES the old hashed name and ADDS a new one for the
# same logical asset — which naively reads as a "dropped" frontend file.
# This is NOT a dropped capability: `check_frontend_present` independently
# verifies the critical SPA files still exist in the new zip. We exempt a
# removed dist asset from `frontend_dropped` ONLY when an added file shares
# its directory, stem, and extension (i.e. a 1-for-1 rehash). A dropped
# asset with NO same-stem/ext replacement still fails the gate.
_HASHED_ASSET = re.compile(
    r"^(?P<dir>.*/dist/assets/)(?P<stem>[A-Za-z0-9_]+)-(?P<hash>[A-Za-z0-9_-]+)"
    r"(?P<ext>\.css|\.js|\.js\.map)$")


def _asset_key(path: str):
    """Return (dir, stem, ext) for a content-hashed dist asset, else None."""
    m = _HASHED_ASSET.match(path)
    if not m:
        return None
    return (m.group("dir"), m.group("stem"), m.group("ext"))


def _is_rehash(removed_path: str, added_paths) -> bool:
    """True iff `removed_path` is a content-hashed dist asset that has a
    same-(dir, stem, ext) replacement among `added_paths` — a bundler rehash,
    not a dropped frontend file."""
    rk = _asset_key(removed_path)
    if rk is None:
        return False
    return any(_asset_key(a) == rk for a in added_paths)


def diff(old_zip: str, new_zip: str) -> Dict:
    o, n = shas(old_zip), shas(new_zip)
    removed = sorted(p for p in o if p not in n)
    added = sorted(p for p in n if p not in o)
    changed = sorted(p for p in o if p in n and o[p] != n[p])
    fe_dropped = [p for p in removed
                  if p.startswith("frontend/") and "node_modules/" not in p
                  and not _is_rehash(p, added)]
    fe_changed = [p for p in changed if p.startswith("frontend/") and "node_modules/" not in p]
    return {
        "old_version": version_of(old_zip), "new_version": version_of(new_zip),
        "old_changelog_top": changelog_top(old_zip), "new_changelog_top": changelog_top(new_zip),
        "new_changelog_layout": changelog_layout(new_zip),
        "old_files": len(o), "new_files": len(n),
        "removed": removed, "added": added, "changed": changed,
        "frontend_dropped": fe_dropped, "frontend_changed": fe_changed,
        "forbidden_new": forbidden_artifacts(list(n)),
    }


def _run(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="Release zip comparator")
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    d = diff(args.old, args.new)
    if args.json:
        print(json.dumps(d, indent=2))
    else:
        print("RELEASE DIFF  old=%s (%d files, v%s)  new=%s (%d files, v%s)"
              % (args.old, d["old_files"], d["old_version"], args.new, d["new_files"], d["new_version"]))
        print("  removed=%d added=%d changed=%d" % (len(d["removed"]), len(d["added"]), len(d["changed"])))
        print("  frontend_dropped=%d frontend_changed=%d forbidden_new=%d"
              % (len(d["frontend_dropped"]), len(d["frontend_changed"]), len(d["forbidden_new"])))
        for p in d["frontend_dropped"]:
            print("  FRONTEND DROPPED ", p)
        for p in d["forbidden_new"]:
            print("  FORBIDDEN ARTIFACT ", p)
        layout = d["new_changelog_layout"]
        if layout["status"] == "finding":
            print("  CHANGELOG ORPHAN PROSE  lines %s appear after the preamble "
                  "and before the first level-two header"
                  % _line_ranges(layout["offending_lines"]))
        elif layout["status"] == "unknown":
            print("  CHANGELOG LAYOUT UNKNOWN  %s" % layout["reason"])
        if d["new_changelog_top"] and d["new_version"] and d["new_changelog_top"] != d["new_version"]:
            print("  CHANGELOG MISMATCH  __version__=%s  CHANGELOG top=%s"
                  % (d["new_version"], d["new_changelog_top"]))

    layout = d["new_changelog_layout"]
    failed = (bool(d["frontend_dropped"]) or bool(d["forbidden_new"])
              or layout["status"] == "finding")
    if d["new_changelog_top"] and d["new_version"] and d["new_changelog_top"] != d["new_version"]:
        failed = True
    if layout["status"] == "unknown":
        print("RELEASE DIFF GATE: UNKNOWN")
        return 2
    print("RELEASE DIFF GATE:", "FAIL" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(_run(sys.argv[1:]))
    except (OSError, zipfile.BadZipFile) as e:
        print("ERROR:", e, file=sys.stderr)
        sys.exit(2)
