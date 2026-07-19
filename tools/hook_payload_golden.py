#!/usr/bin/env python3
"""hook_payload_golden.py — R3 (plugin-v3): hook-payload contract golden.

Pins every event's documented payload key-set so a producer-side rename/removal
trips a CI gate instead of silently quarantining live plugins. Locks the contract
BEFORE E1 widens the event surface.

The declared contract is the ``HOOK_EVENTS`` registry in ``bulk_downloader.plugins``:
each entry's prose documents ``Payload: {k1, k2, ...}``. This tool derives that
key-set per event and pins it. The check is **subset** semantics, matching R5's
forward-compat split:

  * a golden key that is no longer present (removed/renamed) -> VIOLATION
  * a whole event missing from the producer side             -> VIOLATION
  * an ADDITIVE key not in the golden                         -> OK (no trip)

Same discipline as the capture-model golden and the dependency-graph gate: a
deterministic regen + a ``--check`` that diffs against the committed golden and
exits non-zero on drift.

POSTURE: read-only; stdlib + project module only; browser-free; plain python3.

CLI:
    python3 tools/hook_payload_golden.py            # print the derived key-sets
    python3 tools/hook_payload_golden.py --write    # (re)write the golden
    python3 tools/hook_payload_golden.py --check     # exit 1 on drift (build gate)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bulk_downloader import plugins as _P  # noqa: E402

GOLDEN_PATH = _ROOT / "tests" / "golden" / "hook_payloads.golden.json"

_PAYLOAD_RE = re.compile(r"Payload:\s*\{([^}]*)\}")


def derive_payload_keys(hook_events: dict) -> dict:
    """Return ``{event: sorted([payload_key, ...])}`` parsed from the doc registry.

    An event whose doc string carries no ``Payload: {...}`` clause maps to an
    empty list (it documents no payload keys).
    """
    out: dict = {}
    for event, doc in hook_events.items():
        m = _PAYLOAD_RE.search(doc or "")
        if not m:
            out[event] = []
            continue
        keys = [k.strip() for k in m.group(1).split(",") if k.strip()]
        out[event] = sorted(set(keys))
    return out


def check(golden: dict, current: dict) -> list:
    """Return a list of violation strings (empty == contract intact).

    Subset semantics: every golden event must still exist, and every golden key
    for that event must still be present. Additive keys/events are allowed.
    """
    violations: list = []
    for event, gkeys in golden.items():
        if event not in current:
            violations.append(f"{event}: event removed from the producer contract")
            continue
        missing = [k for k in gkeys if k not in current[event]]
        if missing:
            violations.append(
                f"{event}: payload key(s) removed/renamed: {sorted(missing)}")
    return violations


def load_golden() -> dict:
    return json.load(open(GOLDEN_PATH, encoding="utf-8"))


def write_golden() -> dict:
    data = derive_payload_keys(_P.HOOK_EVENTS)
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", "utf-8")
    return data


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="hook-payload contract golden")
    ap.add_argument("--write", action="store_true", help="(re)write the golden")
    ap.add_argument("--check", action="store_true", help="exit 1 on contract drift")
    args = ap.parse_args(argv)

    current = derive_payload_keys(_P.HOOK_EVENTS)
    if args.write:
        data = write_golden()
        print(f"wrote {GOLDEN_PATH} ({len(data)} events)")
        return 0
    if args.check:
        if not GOLDEN_PATH.is_file():
            print(f"FAIL: golden missing ({GOLDEN_PATH})")
            return 1
        violations = check(load_golden(), current)
        if violations:
            print("FAIL: hook-payload contract drift:")
            for v in violations:
                print(f"  - {v}")
            return 1
        print(f"OK: hook-payload contract intact ({len(current)} events)")
        return 0
    print(json.dumps(current, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
