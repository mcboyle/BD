#!/usr/bin/env python3
"""
generate_handoff_manifest.py — write MANIFEST.sha256 for a handoff drop.

Lists every file under `changed/` plus the four `_post_*.md` docs at the
handoff root, with sha256 hashes in standard `sha256sum` format. The
next session runs `sha256sum -c MANIFEST.sha256` (Linux) for an
immediate pass/fail per file, or this script with `--verify` from any
platform.

Why this exists: during the post-v3.63.8 session, 7 of 7 generated
`changed/` files reached the next session as in-context documents but
only 3 reached it as on-disk files. A manifest with checksums makes
that discrepancy visible in one command instead of relying on the next
instance noticing.

Usage from a handoff session (default — generate):

    python tools/generate_handoff_manifest.py /path/to/handoff_root

Where `handoff_root` contains `changed/` and the four `_post_*.md` docs.
Writes `MANIFEST.sha256` at the handoff root. Pre-existing manifest is
overwritten.

Usage from the receiving session (verify):

    python tools/generate_handoff_manifest.py --verify /path/to/handoff_root

Exits 0 if every listed file is present and hashes match, 1 otherwise.
Reports each mismatch and each missing/extra file on stderr.

Cross-platform: pure stdlib, no shell. The generator writes forward
slashes in paths so the manifest is byte-identical across Linux and
Windows.
"""

import argparse
import hashlib
import sys
from pathlib import Path


MANIFEST_NAME = "MANIFEST.sha256"
POST_DOC_GLOB = "_post_*.md"
CHANGED_DIR = "changed"


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(root: Path) -> list[Path]:
    """Every file under changed/ + every _post_*.md at root.

    Order: handoff docs first (alphabetical), then changed/ tree
    (alphabetical full path). Deterministic so two runs produce
    byte-identical manifests.
    """
    files: list[Path] = []

    for p in sorted(root.glob(POST_DOC_GLOB)):
        if p.is_file():
            files.append(p)

    changed = root / CHANGED_DIR
    if changed.is_dir():
        for p in sorted(changed.rglob("*")):
            if p.is_file():
                files.append(p)

    return files


def _rel_posix(root: Path, p: Path) -> str:
    """Relative path with forward slashes — same on Linux and Windows."""
    return p.relative_to(root).as_posix()


def generate(root: Path) -> Path:
    """Write MANIFEST.sha256 at `root`. Return the manifest path."""
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    files = _collect_files(root)
    if not files:
        raise SystemExit(
            f"no handoff files found under {root} — "
            f"expected {POST_DOC_GLOB} at root and/or a {CHANGED_DIR}/ tree"
        )

    manifest_path = root / MANIFEST_NAME

    lines = []
    for p in files:
        if p.resolve() == manifest_path.resolve():
            continue  # never hash the manifest itself
        digest = _sha256_of(p)
        rel = _rel_posix(root, p)
        # `sha256sum` format: "<hex>  <path>\n" (two spaces, LF only).
        lines.append(f"{digest}  {rel}\n")

    # LF line endings, UTF-8, no BOM — keep byte-identical across OS.
    with manifest_path.open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(lines)

    return manifest_path


def verify(root: Path) -> int:
    """Return 0 if all entries match, 1 otherwise."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"FAIL: no manifest at {manifest_path}", file=sys.stderr)
        return 1

    expected: dict[str, str] = {}
    with manifest_path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line:
                continue
            # sha256sum format: "<64-hex>  <path>".
            if len(line) < 66 or line[64:66] != "  ":
                print(
                    f"FAIL: malformed manifest line {lineno}: {line!r}",
                    file=sys.stderr,
                )
                return 1
            digest, path_str = line[:64], line[66:]
            expected[path_str] = digest

    listed_paths = set(expected)
    present_paths: set[str] = set()
    mismatches: list[str] = []

    for path_str, want in expected.items():
        p = root / path_str
        if not p.is_file():
            mismatches.append(f"MISSING  {path_str}")
            continue
        present_paths.add(path_str)
        got = _sha256_of(p)
        if got != want:
            mismatches.append(f"MISMATCH {path_str}  expected {want}  got {got}")

    # Detect extra files in changed/ or extra _post_*.md not in the manifest.
    on_disk = set()
    for p in _collect_files(root):
        if p.resolve() == manifest_path.resolve():
            continue
        on_disk.add(_rel_posix(root, p))
    extras = sorted(on_disk - listed_paths)
    for x in extras:
        mismatches.append(f"EXTRA    {x}  (on disk, not in manifest)")

    if mismatches:
        for m in mismatches:
            print(m, file=sys.stderr)
        print(
            f"FAIL: {len(mismatches)} manifest discrepancy(ies) at {root}",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(expected)} file(s) verified against {manifest_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument(
        "root",
        type=Path,
        help="handoff root containing changed/ and/or _post_*.md docs",
    )
    ap.add_argument(
        "--verify",
        action="store_true",
        help="verify existing MANIFEST.sha256 instead of generating one",
    )
    args = ap.parse_args()

    if args.verify:
        return verify(args.root)

    manifest_path = generate(args.root)
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
