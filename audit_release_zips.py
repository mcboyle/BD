#!/usr/bin/env python3
"""NEW-13 (v3.66.43): scan release zips for shipped-secrets defects.

Ported and extended from the v3.65.2-era audit_release_zips_1b.py. Checks
a directory (or list) of release zips for files that must never ship:

  vapid_keys.json        — web-push private key      (v3.65.1 B5)
  secrets.json[.tmp]     — AES-GCM vault blob + salt  (v3.66.39 B2 / NEW-2)
  vault_tokens.json[.tmp]— LIVE bearer vault tokens   (v3.66.39 B2 / NEW-2)
  secrets_meta.json[.tmp]— secret-key index           (v3.66.39 B2 / NEW-2)

Plus a content check on app_config.json (v3.65.1 B5b): a non-pristine
config carrying a populated stream_token_secret is flagged.

This is a verification tool for a defect class already fixed in the
current code; its value is re-scanning PREVIOUSLY-BUILT zips an operator
may still have (or may have distributed). Single-operator framing: "did I
ever share a version that leaked one of these?"

Usage:
    python audit_release_zips.py <dir-or-zip> [<dir-or-zip> ...]
    python audit_release_zips.py            # defaults to CWD

Exit 0 means at least one ZIP was measured and all were clean. Exit 1 means at
least one measured ZIP was flagged CRITICAL. Exit 2 means no ZIP population was
available to audit, so the result is UNKNOWN rather than clean.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

FORBIDDEN_NAMES = {
    "vapid_keys.json",          # v3.65.1 B5
    "secrets.json",             # v3.66.39 B2
    "secrets.json.tmp",         # NEW-2
    "vault_tokens.json",        # v3.66.39 B2
    "vault_tokens.json.tmp",    # NEW-2
    "secrets_meta.json",        # v3.66.39 B2
    "secrets_meta.json.tmp",    # NEW-2
}


def audit_zip(path: Path) -> set[str]:
    """Return the set of findings for one zip (empty == clean)."""
    bad: set[str] = set()
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        for n in names:
            if Path(n).name in FORBIDDEN_NAMES:
                bad.add(Path(n).name)
            # v3.65.1 B5b: a populated app_config.json is non-pristine.
            if Path(n).name == "app_config.json":
                try:
                    content = zf.read(n).decode("utf-8", errors="ignore")
                except Exception:
                    content = ""
                if "stream_token_secret" in content and len(content) > 50:
                    bad.add("app_config.json (non-pristine)")
    return bad


def _iter_zips(target: Path):
    if target.is_dir():
        yield from sorted(target.glob("*.zip"))
    elif target.suffix == ".zip" and target.exists():
        yield target


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or [Path.cwd()]
    any_critical = False
    scanned = 0
    for target in targets:
        for zp in _iter_zips(target):
            scanned += 1
            findings = audit_zip(zp)
            if findings:
                any_critical = True
                print(f"CRITICAL  {zp}: {', '.join(sorted(findings))}")
            else:
                print(f"CLEAN     {zp}")
    if scanned == 0:
        print("UNKNOWN   no zips found", file=sys.stderr)
        return 2
    return 1 if any_critical else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
