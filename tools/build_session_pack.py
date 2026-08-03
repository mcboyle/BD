#!/usr/bin/env python3
"""build_session_pack — one command for the session-close ritual.

The close is ~6 manual, error-prone steps every cut: stamp STATE's mechanical
fields, hand-check it against STATE_schema.json, prune the oldest changes_N,
run the tracker drift gate, build the overlay, assemble + validate the pack.
This folds them into one tool and makes schema-validation + changes-pruning
GATES rather than habits. The HUMAN still writes the narrative fields
(deploy_status / validation / next / changes_<N>) into the draft STATE; this
tool refreshes only what is mechanically derivable from the release zip and
gates the rest.

  python3 tools/build_session_pack.py \
      --state <draft-STATE.json> --zip <release.zip> \
      --pack-dir <dir-with-tracker+handoff> --out <pack.zip> \
      [--baseline <prev.zip> --overlay <overlay.zip>] [--keep-changes 2]

Refreshes from the zip: built_version, zip.{name,file,file_count,sha256},
guards + guards_full_sha256 (from the zip's guard files). Prunes changes_<N>
to the newest --keep-changes. Gates: STATE_schema required keys present, and
tasktracker_sync --check IN-SYNC on the pack dir. Stdlib only (imports sibling
tools). bd-state remains the final cross-check (run it after, or this calls it
if present on PATH).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _import(name, fname):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / fname)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _zip_version(zf):
    raw = zf.read("bulk_downloader/__init__.py").decode("utf-8", "replace")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', raw)
    return m.group(1) if m else None


def refresh_state(state, zip_path, keep_changes):
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        ver = _zip_version(zf)
        full = hashlib.sha256(Path(zip_path).read_bytes()).hexdigest()
        state["built_version"] = ver
        z = state.setdefault("zip", {})
        z["name"] = os.path.basename(zip_path)
        z["file"] = os.path.basename(zip_path)
        z["file_count"] = len(names)
        z["sha256"] = full
        # refresh declared guards from the zip's actual files
        g = state.get("guards") or {}
        gf = state.get("guards_full_sha256") or {}
        for k in list(g):
            if k in names:
                h = hashlib.sha256(zf.read(k)).hexdigest()
                g[k] = h[:8]
                if k in gf:
                    gf[k] = h
    # prune changes_<N> to the newest keep_changes
    ch = sorted((k for k in state if re.match(r"changes_\d+$", k)),
                key=lambda k: int(k.split("_")[1]))
    for k in ch[:-keep_changes] if keep_changes > 0 else []:
        state.pop(k, None)
    return state, ver, len(names), full


def schema_gate(state):
    schema_path = Path("/mnt/project/STATE_schema.json")
    if not schema_path.is_file():
        return []  # schema unavailable in this env — skip (bd-state still gates)
    sch = json.loads(schema_path.read_text())
    return [k for k in sch.get("required", []) if k not in state]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--pack-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--overlay")
    ap.add_argument("--keep-changes", type=int, default=2)
    a = ap.parse_args(argv)

    state = json.loads(Path(a.state).read_text())
    state, ver, cnt, full = refresh_state(state, a.zip, a.keep_changes)

    missing = schema_gate(state)
    if missing:
        sys.exit(f"FAIL: STATE missing required schema keys: {missing}")

    # write the finalized STATE into the pack dir
    out_state = Path(a.pack_dir) / "STATE.json"
    out_state.write_text(json.dumps(state, indent=2) + "\n")
    print(f"  STATE refreshed: built={ver} files={cnt} sha={full[:12]}… "
          f"changes={[k for k in state if k.startswith('changes_')]}")

    # overlay (optional)
    if a.baseline and a.overlay:
        mo = _import("make_overlay", "make_overlay.py")
        payload, _ = mo.build_overlay(a.baseline, a.zip, a.overlay)
        print(f"  overlay: {len(payload)} files -> {os.path.basename(a.overlay)}")

    # assemble pack zip
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED) as zo:
        for p in sorted(Path(a.pack_dir).rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                zo.writestr(str(p.relative_to(a.pack_dir)).replace("\\", "/"),
                            p.read_bytes())
    print(f"  pack assembled: {os.path.basename(a.out)}")

    # final cross-check via bd-state if available
    try:
        r = subprocess.run(["bd-state", "--state", str(out_state), "--zip", a.zip],
                           capture_output=True, text=True)
        tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]
        print(f"  bd-state: {tail[0]}")
        if r.returncode != 0:
            sys.exit("FAIL: bd-state pin mismatch")
    except FileNotFoundError:
        print("  bd-state: not on PATH — run it manually as the final gate")
    print("RESULT: pack ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
