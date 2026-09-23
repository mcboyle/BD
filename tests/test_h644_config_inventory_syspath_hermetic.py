"""H644: tools/config_surface_inventory.py must not let sys.path decide its counts.

Run as a script (``python tools/config_surface_inventory.py``) the interpreter
puts only ``tools/`` on sys.path; imported under pytest the repo root is there
too. The tool's ``bulk_downloader`` import (the app's editable/gated field sets)
silently falls back to empty sets when the root is absent, so the same tree
reported open_runtime_tunable=2 as a script and 0 in-process
(TRAINER/T48-CI-RED-AND-REPIN-CAUSE-FOUND-20260922T0810Z.md). The ratchet
baseline anchors on that count, so a generator whose output depends on ambient
sys.path cannot pin it. The tool now puts its own repo root on sys.path.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "config_surface_inventory.py"
for p in (str(ROOT), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import config_surface_inventory as csi  # noqa: E402


def _script_counts(cwd: Path) -> dict:
    # -I: isolated mode -- no cwd, no PYTHONPATH, no user site on sys.path, so the
    # only way the repo root is importable is the tool putting it there itself.
    proc = subprocess.run(
        [sys.executable, "-I", str(TOOL), "--root", str(ROOT), "--json"],
        cwd=str(cwd), stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout)["counts"]


def test_script_run_reports_the_same_counts_as_in_process_build(tmp_path):
    in_process = csi.build(str(ROOT))["counts"]
    assert _script_counts(tmp_path) == in_process


def test_the_app_field_sets_resolve_when_run_as_a_script(tmp_path):
    # The fallback (empty sets) is what made the script count differ; with the app
    # importable the editable set is never empty on this tree.
    proc = subprocess.run(
        [sys.executable, "-I", "-c",
         "import runpy; "
         f"g = runpy.run_path({str(TOOL)!r}, run_name='h644'); "
         f"e, _ = g['_editable_site_fields']({str(ROOT)!r}); print(len(e))"],
        cwd=str(tmp_path), stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert int(proc.stdout.strip()) > 0
