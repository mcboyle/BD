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
tools).

bd-state is the BINDING final cross-check and this runs it. Not "if present on
PATH" -- v3.66.888: both of this tool's final gates resolved to absent
locations and failed open. The schema gate read a retired-era absolute path
while the schema sat tracked in project-knowledge/, and bd-state was invoked by
bare name, so a PATH miss printed a note and fell through to "RESULT: pack
ready". Each gate's excuse for skipping was the other one. Both now resolve
repo-local paths, and an unlocatable gate exits non-zero rather than passing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
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


def _schema_path():
    """The STATE schema, or None if it genuinely cannot be found.

    v3.66.888: this used to be a hardcoded `/mnt/project/STATE_schema.json` --
    a retired-era absolute path that exists neither here nor on the box -- and
    `schema_gate` returned [] when it was absent, excusing itself with "schema
    unavailable in this env: skip (bd-state still gates)". The schema is
    TRACKED, at project-knowledge/STATE_schema.json, so the gate was skipping
    itself while the file it needed sat in the repo. The repo copy is probed
    FIRST and the legacy absolute path is kept only as a fallback.
    """
    for cand in (REPO / "project-knowledge" / "STATE_schema.json",
                 Path("/mnt/project/STATE_schema.json")):
        if cand.is_file():
            return cand
    return None


def _bd_state_cmd():
    """argv prefix for bd-state, or None if it cannot be located.

    NOT a bare name. `subprocess.run(["bd-state", ...])` is PATH-dependent, and
    bd-state is NOT on PATH -- so the call raised FileNotFoundError, the caller
    swallowed it with a printed note, and main() fell through to "RESULT: pack
    ready" and returned 0. The binding final gate never ran and the tool
    reported success. Invoked through sys.executable because the tool is an
    extensionless python script whose exec bit is not guaranteed by a checkout.
    """
    cand = REPO / "toolchain" / "bin" / "bd-state"
    if cand.is_file():
        return [sys.executable, str(cand)]
    found = shutil.which("bd-state")
    return [found] if found else None


def schema_gate(state):
    """Required keys the state is missing. Returns a LIST -- main() prints it.

    An unfindable schema is NOT an empty list; `main` checks `_schema_path()`
    separately and fails, because "nothing missing" and "nothing checked" must
    not share a return value.
    """
    schema_path = _schema_path()
    if schema_path is None:
        return []
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

    # UNKNOWN is a third state and it fails. "nothing missing" and "the schema
    # could not be found" must not both read as clean.
    if _schema_path() is None:
        sys.exit("FAIL: STATE_schema.json not found (looked in "
                 "project-knowledge/ and /mnt/project/); the schema gate "
                 "cannot run, which is not the same as passing")
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

    # THE BINDING FINAL GATE. Not "if available" -- unreachable is a failure.
    # It was invoked by bare name, so it was PATH-dependent; bd-state is not on
    # PATH, the FileNotFoundError branch printed a note, and execution fell
    # through to "RESULT: pack ready" and return 0. Advising the operator to
    # "run it manually" in the middle of stdout is not a gate.
    cmd = _bd_state_cmd()
    if cmd is None:
        sys.exit("FAIL: bd-state could not be located (looked in "
                 "toolchain/bin/ and on PATH); it is the binding final gate, "
                 "so not running it is a failure, not a skip")
    r = subprocess.run(cmd + ["--state", str(out_state), "--zip", a.zip],
                       capture_output=True, text=True)
    tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]
    print(f"  bd-state: {tail[0]}")
    if r.returncode != 0:
        sys.exit("FAIL: bd-state pin mismatch")
    print("RESULT: pack ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
