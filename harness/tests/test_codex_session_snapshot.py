from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


SCRIPT = Path(__file__).parents[1] / "bd-codex-session-snapshot.py"
ROOT_THREAD = "01a050a2-c73d-7d53-92e1-313bc08bed38"


def load_snapshot_module():
    spec = importlib.util.spec_from_file_location("bd_codex_session_snapshot", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_session_copy_path_survives_atomic_directory_publish(tmp_path, monkeypatch):
    """Catch publishing a manifest that still points at its renamed staging dir."""
    module = load_snapshot_module()
    source = tmp_path / "active.jsonl"
    source.write_text('{"type":"session_meta","payload":{"id":"x"}}\n', encoding="utf-8")
    output_root = tmp_path / "snapshots"

    monkeypatch.setattr(module, "process_rows", lambda: [])
    monkeypatch.setattr(module, "open_session_paths", lambda _processes: [source])
    monkeypatch.setattr(module, "session_inventory", lambda _root_id: [])
    monkeypatch.setattr(module, "tmux_state", lambda _output: {})
    monkeypatch.setattr(module, "fleet_state", lambda _output, _roles, _probe: [])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(SCRIPT),
            "--root-thread",
            ROOT_THREAD,
            "--output-root",
            str(output_root),
        ],
    )

    assert module.main() == 0
    final = output_root / (output_root / "LATEST").read_text(encoding="utf-8").strip()
    manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
    copy = manifest["active_session_copies"][0]

    assert copy["snapshot"] == "sessions/active.jsonl"
    assert (final / copy["snapshot"]).read_bytes() == source.read_bytes()
