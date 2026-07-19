#!/usr/bin/env python3
"""test_bd_scan_jscpd_535.py -- RED-first guard for P2 @535: bd-scan gains a jscpd
copy-paste/clone detection path (source='jscpd'), wired behind --jscpd.

run_tests.py harness conventions: zero-arg test_* functions, plain asserts, no
pytest builtins, layout-flexible discovery. Stdlib only.

RED-first (fails on the 534 tree where bd-scan has no jscpd path; passes @535):
  test_bd_scan_has_jscpd_path   -> from_jscpd + --jscpd flag present
  test_bd_scan_jscpd_source_tag -> clones land under source='jscpd' (distinct
                                    from semgrep/bandit/vulture in by_source)
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    p = _REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_bd_scan_has_jscpd_path():
    txt = _read("tools/bd-scan.py")
    assert txt, "tools/bd-scan.py not found"
    assert "def from_jscpd" in txt, \
        "bd-scan.py has no from_jscpd() -- P2 jscpd clone detection not wired"
    assert "--jscpd" in txt, \
        "bd-scan.py has no --jscpd flag -- P2 not exposed on the CLI"


def test_bd_scan_jscpd_source_tag():
    txt = _read("tools/bd-scan.py")
    assert txt, "tools/bd-scan.py not found"
    # clones must be tagged source='jscpd' so they are distinct in by_source
    assert '"source": "jscpd"' in txt or "'source': 'jscpd'" in txt, \
        "bd-scan.py jscpd findings must carry source='jscpd' (distinct clone class)"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}"); p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}"); f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
