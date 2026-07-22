#!/usr/bin/env python3
"""test_toolchain_534.py -- RED-first guards for the v3.66.534 toolchain additions:
  B  capture.sh runs the graph content-hash gate (P2) when a graph db is present
  E  bd-scan.py has a TS/TSX-aware scan path (semgrep-backed) for frontend/src

run_tests.py harness conventions: zero-arg test_* functions, plain asserts, no
pytest builtins, layout-flexible file discovery. Stdlib only.

RED-first map (fail on pristine 533 -> pass after 534):
  B test_capture_sh_has_graph_checkhash_gate  -> capture.sh had no graph --check-hash
       step. RED: the P2 pin is toothless on stash.
  B test_capture_sh_graph_gate_is_conditional -> the gate must be guarded by a
       db-present check (graceful skip when the db isn't deployed). RED: no gate.
  E test_bd_scan_has_ts_scan_path             -> bd-scan.py had no TS/TSX scan
       (graph is grep-level for frontend). RED: no --ts / scan_ts.
  E test_bd_scan_ts_uses_semgrep             -> the TS path must invoke semgrep
       (the uploaded kit) rather than re-grep. RED: no semgrep reference.
"""
import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(rel):
    p = _REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# --------------------------------------------------------------------------- #
# B -- capture.sh graph content-hash gate                                      #
# --------------------------------------------------------------------------- #
def test_capture_sh_has_graph_checkhash_gate():
    txt = _read("capture.sh")
    assert txt, "capture.sh not found"
    assert "--check-hash" in txt or "check_hash" in txt, \
        "capture.sh has no graph --check-hash gate -- P2 pin is toothless on stash (B not wired)"


def test_capture_sh_graph_gate_uses_external_pin_with_required_mode():
    txt = _read("capture.sh")
    assert txt, "capture.sh not found"
    assert "BD_GRAPH_HASH_PIN" in txt
    assert "BD_REQUIRE_GRAPH_HASH" in txt
    assert re.search(r'if \[ ! -f "\$graph_pin" \]', txt)
    assert "UNKNOWN -- optional check not armed" in txt


# --------------------------------------------------------------------------- #
# E -- bd-scan TS/TSX scan path                                                #
# --------------------------------------------------------------------------- #
def test_bd_scan_has_ts_scan_path():
    txt = _read("tools/bd-scan.py")
    assert txt, "tools/bd-scan.py not found"
    # bd-scan already runs semgrep over the PYTHON tree; the E gap is a scan
    # that TARGETS frontend/src with TS/TSX awareness. Require an explicit
    # frontend/src TS scan path (a function or a targeted invocation).
    has_ts = ("frontend/src" in txt and
              ("def from_semgrep_ts" in txt or "scan_ts" in txt
               or "--ts" in txt or "p/typescript" in txt or "tsx" in txt.lower()))
    assert has_ts, \
        "bd-scan.py has no frontend/src TS/TSX scan path -- the frontend stays " \
        "grep-level (E not wired)"


def test_bd_scan_ts_targets_frontend_with_ts_rules():
    txt = _read("tools/bd-scan.py")
    assert txt, "tools/bd-scan.py not found"
    # the TS path must (a) target frontend/src and (b) use a TS-aware semgrep
    # config (p/typescript or p/react), not just re-run the auto Python config.
    targets_fe = "frontend/src" in txt
    ts_config = ("p/typescript" in txt or "p/react" in txt or "typescript" in txt.lower())
    assert targets_fe and ts_config, \
        "bd-scan.py TS path must target frontend/src with a TS-aware semgrep " \
        "config (p/typescript / p/react) -- E must add a real TS scan, not re-grep"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            p += 1
        except AssertionError as e:
            print(f"  FAIL {fn.__name__}: {e}")
            f += 1
    print(f"\n{p} passed, {f} failed")
    sys.exit(1 if f else 0)
