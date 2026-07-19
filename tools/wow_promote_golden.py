#!/usr/bin/env python3
"""wow_promote_golden.py — NEXT-2 runtime-shape golden for the wowgirls re-promote.

The on-stash ``reviewed/auth.wowgirls.com.template.json`` was once promoted in RAW
WACZ-builder shape (``resolution_priority`` + ``network_discovery``, NO top-level
``resolutions``), bypassing ``normalize_template_draft``; a one-liner mapped a
top-level ``resolutions`` in place to clear ``validate_templates`` but left the file
raw. The PROPER fix is to re-promote it cleanly through the documented pipeline:

    build_template_from_wacz.build_template      (rich draft, from the §9 wow fixture)
      -> template_normalize.normalize_draft       (runtime reviewed-shape candidate)
      -> template_manager.promote_gate_errors      (the shared readiness/safety gate)
      -> promote_template.py                        (operator, on stash)

This golden pins the DETERMINISTIC runtime shape that the normalizer produces for
the wow capture, so the operator can re-validate a re-promote on stash against a
fixed reference (``--check`` exits 1 on drift). It also makes the headline NEXT-2
fact explicit and guarded: the normalized candidate now carries a top-level
``resolutions`` ladder and clears ``promote_gate_errors`` — the two things the raw
on-stash template lacked.

Same discipline as ``capture_model_golden.py``: a deterministic regen + a
``--check`` that diffs against the committed golden. NOT wired into the build gate
(``build_release`` is a release guard; the band test ``test_wow_promote_runtime_shape``
is the drift guard, like the recognizer corpus).

POSTURE: read-only / F2-safe. The projection is the runtime template SHAPE only —
host/selectors/templated network patterns/resolution ladder — never a signed value
(``scan_artifact_secrets`` is asserted clean by the test). The per-capture identity
fields (the temp WACZ filename + its sha) are NON-deterministic inputs, not
behaviour, so they are dropped from the projection.

stdlib + project modules; browser-free; plain ``python3``.

CLI:
    python3 tools/wow_promote_golden.py            # print current projection
    python3 tools/wow_promote_golden.py --write     # (re)write the golden
    python3 tools/wow_promote_golden.py --check      # exit 1 on drift
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import build_recognizer_corpus as _BRC                         # type: ignore  # noqa: E402
from bulk_downloader.template_normalize import normalize_draft  # noqa: E402

GOLDEN_PATH = _ROOT / "tests" / "golden" / "wow_promote.golden.json"
FIXTURE = _ROOT / "tests" / "corpus" / "recognizer" / "wow.cap.json"

# Per-capture identity fields — a temp WACZ filename + the sha of the freshly
# rebuilt synthetic archive — vary run-to-run. They are capture INPUTS, not
# normalizer behaviour, so the projection drops them (top-level ``source_capture``
# and the two ``source`` sub-keys). Everything else the normalizer derives is
# deterministic, including ``evidence_counts`` and ``source.captured_at``.
_VOLATILE_SOURCE_KEYS = ("capture_file", "capture_sha256")


def build_candidate() -> dict:
    """Run the real pipeline: §9 wow fixture -> rich draft -> normalized candidate."""
    draft = _BRC.build_from_fixture(FIXTURE)
    return normalize_draft(draft)


def build_projection() -> dict:
    """The deterministic, F2-safe runtime-shape projection of the wow candidate."""
    cand = dict(build_candidate())
    src = dict(cand.get("source") or {})
    for k in _VOLATILE_SOURCE_KEYS:
        src.pop(k, None)
    cand["source"] = src
    cand["source_capture"] = "<volatile>"
    return cand


def _canonical(obj) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def write_golden() -> Path:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(_canonical(build_projection()) + "\n", "utf-8")
    return GOLDEN_PATH


def check_golden():
    """Return (ok, diff_lines)."""
    if not GOLDEN_PATH.is_file():
        return False, [f"golden missing: {GOLDEN_PATH} (run --write)"]
    want = GOLDEN_PATH.read_text("utf-8").rstrip("\n")
    have = _canonical(build_projection())
    if want == have:
        return True, []
    import difflib
    diff = list(difflib.unified_diff(
        want.splitlines(), have.splitlines(),
        fromfile="committed_golden", tofile="current_projection", lineterm=""))
    return False, diff


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="NEXT-2 wowgirls re-promote runtime-shape golden.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="(re)write the golden file")
    g.add_argument("--check", action="store_true", help="exit 1 on drift")
    args = ap.parse_args(argv)

    if args.write:
        p = write_golden()
        print(f"wrote {p}")
        return 0
    if args.check:
        ok, diff = check_golden()
        if ok:
            print("wow_promote golden: OK (no drift)")
            return 0
        print("wow_promote golden DRIFT:", file=sys.stderr)
        print("\n".join(diff[:80]), file=sys.stderr)
        return 1
    print(_canonical(build_projection()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
