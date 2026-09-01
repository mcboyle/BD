#!/usr/bin/env python3
"""bd-retrio -- resolve the release-trio collision a rebase always produces.

WHY THIS EXISTS. The release trio makes every stacked cut collide. A cut sets
bulk_downloader/__init__.py, the pin in tests/test_settings_center_slice4.py,
and prepends a CHANGELOG entry anchored on the previous release header; when
the cut ahead of it merges first, all three conflict, plus the generated
PIN_INDEX.json and STATIC_KB_MANIFEST.json that were regenerated from them.
N stacked cuts cost N of these. It happened three times on 2026-08-31 alone.

THE ONE THING THAT MUST NOT BE DONE BY HAND is the CHANGELOG entry. Retyping
a punctuation-sensitive header is exactly how an anchor goes quietly wrong,
and CLAUDE.md forbids retyping anchors for that reason. This recovers the
entry from the pre-rebase commit byte-for-byte and only rewrites its version
number, so the prose cannot drift.

WHAT IT DOES, in a worktree stopped mid-rebase with the trio conflicted:

  1. Takes the incoming (main) side for all five files -- main's trio is the
     truth about what shipped.
  2. Re-derives the cut's version as main's + 1, and asserts that number is
     not already used in the CHANGELOG.
  3. Recovers the cut's own CHANGELOG entry from --pre, rewrites only its
     version header, and re-anchors it on main's current head entry.
  4. Leaves PIN_INDEX.json and STATIC_KB_MANIFEST.json for bd-regen-order --
     a generated artifact is regenerated, never merged.

It does NOT continue the rebase, stage, commit, or push: the integrator reads
the result first.

  bd-retrio.py --work DIR --pre SHA [--version N.N.N]

Exit 0 = resolved. 2 = CANNOT-EVALUATE (not mid-rebase, unexpected conflict
set, entry not recoverable, version already present) -- refuse, never guess.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TRIO = ("CHANGELOG.md", "bulk_downloader/__init__.py",
        "tests/test_settings_center_slice4.py")
GENERATED = ("PIN_INDEX.json", "project-knowledge/STATIC_KB_MANIFEST.json")
_VER = re.compile(r"3\.66\.(\d+)")
_HDR = re.compile(r"^## v(3\.66\.\d+) ", re.M)


def git(work: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(work), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def fail(msg: str) -> int:
    print(f"CANNOT-EVALUATE: {msg}", file=sys.stderr)
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--pre", required=True, help="the cut's commit BEFORE the rebase")
    ap.add_argument("--version", help="override the derived version")
    a = ap.parse_args(argv)
    work = Path(a.work).resolve()

    conflicted = {l.strip() for l in
                  git(work, "diff", "--name-only", "--diff-filter=U").splitlines() if l.strip()}
    if not conflicted:
        return fail("no conflicted paths -- this worktree is not mid-rebase on the trio")
    unexpected = conflicted - set(TRIO) - set(GENERATED)
    if unexpected:
        return fail(f"conflicts outside the trio, resolve by hand: {sorted(unexpected)}")

    # THE TRIO MUST EXIST ON MAIN; the generated pair may legitimately not.
    # A tree that has never generated PIN_INDEX.json is not an error here, and
    # treating a missing optional artifact as a hard failure would refuse a
    # resolvable rebase -- the opposite of this tool's purpose.
    restored = []
    for rel in TRIO:
        blob = git(work, "show", f"origin/main:{rel}")
        (work / rel).write_text(blob, encoding="utf-8")
        restored.append(rel)
    for rel in GENERATED:
        p_gen = subprocess.run(["git", "-C", str(work), "show", f"origin/main:{rel}"],
                               capture_output=True, text=True)
        if p_gen.returncode == 0:
            (work / rel).write_text(p_gen.stdout, encoding="utf-8")
            restored.append(rel)

    init = (work / "bulk_downloader/__init__.py").read_text(encoding="utf-8")
    m = _VER.search(init)
    if not m:
        return fail("no 3.66.N version in bulk_downloader/__init__.py")
    mainv = f"3.66.{m.group(1)}"
    newv = a.version or f"3.66.{int(m.group(1)) + 1}"

    changelog = work / "CHANGELOG.md"
    s = changelog.read_text(encoding="utf-8")
    if f"## v{newv} " in s:
        return fail(f"v{newv} already has a CHANGELOG entry -- pick another --version")

    pre_log = git(work, "show", f"{a.pre}:CHANGELOG.md")
    heads = _HDR.findall(pre_log)
    if len(heads) < 2:
        return fail(f"{a.pre} CHANGELOG has fewer than two release headers")
    own, below = heads[0], heads[1]
    start = pre_log.index(f"## v{own} ")
    end = pre_log.index(f"## v{below} ")
    entry = pre_log[start:end]
    if not entry.isascii():
        return fail("recovered CHANGELOG entry is not ASCII")
    # Rewrite ONLY the version in the header; the prose is carried byte-for-byte.
    entry = entry.replace(f"## v{own} ", f"## v{newv} ", 1)

    anchor = f"## v{mainv} "
    if s.count(anchor) != 1:
        return fail(f"main's header {anchor.strip()} occurs {s.count(anchor)} times, expected 1")
    idx = s.index(anchor)
    changelog.write_text(s[:idx] + entry + s[idx:], encoding="utf-8")

    init_new = init.replace(f'__version__ = "{mainv}"', f'__version__ = "{newv}"', 1)
    if init_new == init:
        return fail(f"could not bump __version__ from {mainv}")
    (work / "bulk_downloader/__init__.py").write_text(init_new, encoding="utf-8")

    pin_path = work / "tests/test_settings_center_slice4.py"
    pin = pin_path.read_text(encoding="utf-8")
    pin_new = pin.replace(f'__version__ == "{mainv}"', f'__version__ == "{newv}"', 1)
    if pin_new == pin:
        return fail(f"could not bump the version pin from {mainv}")
    pin_path.write_text(pin_new, encoding="utf-8")

    print(f"main={mainv} -> cut={newv}")
    print(f"restored from origin/main: {', '.join(restored)}")
    print(f"CHANGELOG entry recovered from {a.pre} ({len(entry)} chars), re-anchored on v{mainv}")
    print("NEXT: regenerate the generated pair, then read the diff before continuing:")
    print(f"  venv/bin/python toolchain/bin/bd-regen-order --work {work}")
    print(f"  git -C {work} add {' '.join(TRIO)} {' '.join(GENERATED)}")
    print(f"  git -C {work} rebase --continue")
    return 0


if __name__ == "__main__":
    sys.exit(main())
