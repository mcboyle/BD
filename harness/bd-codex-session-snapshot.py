#!/usr/bin/env python3
"""Persist a no-signal snapshot of live Codex, tmux, repo, and fleet state.

The source session JSONL files remain Codex's resume authority.  This tool makes
an immutable, complete-line copy of every session file currently held open by a
Codex process and inventories the root thread plus all descendant subagents.
It never attaches to, signals, stops, or restarts a process.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from typing import Any


HOME = Path("/home/mboyle")
CODEX = HOME / ".codex"
SESSIONS = CODEX / "sessions"
DEFAULT_OUTPUT = HOME / "bd-persist" / "codex-session-snapshots"
DEFAULT_REPO = HOME / "BulkDownloader"
DEFAULT_ROLES = HOME / ".config" / "bd" / "roles"
DEFAULT_PROBE = HOME / "bd-persist" / "harness" / "bd-worker-probe.sh"
UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


def run(argv: list[str], *, cwd: Path | None = None, stdin: str | None = None,
        timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def process_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else -1):
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text(encoding="utf-8").strip()
            raw = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            stat_fields = (entry / "stat").read_text(encoding="utf-8").split()
            ppid = int(stat_fields[3])
        except (FileNotFoundError, PermissionError, OSError, ValueError, IndexError):
            continue
        lowered = f"{comm} {raw}".lower()
        if not any(token in lowered for token in ("codex", "claude", "tmux")):
            continue
        # Do not preserve secrets accidentally supplied on a command line.
        redacted = re.sub(
            r"(?i)(--(?:api-key|access-token|password|token)(?:=|\s+))\S+",
            r"\1<redacted>",
            raw,
        )
        rows.append({"pid": int(entry.name), "ppid": ppid, "comm": comm, "argv": redacted})
    return rows


def open_session_paths(processes: list[dict[str, Any]]) -> list[Path]:
    found: set[Path] = set()
    for process in processes:
        if "codex" not in process["comm"].lower() and "codex" not in process["argv"].lower():
            continue
        fd_dir = Path("/proc") / str(process["pid"]) / "fd"
        try:
            descriptors = list(fd_dir.iterdir())
        except (FileNotFoundError, PermissionError, OSError):
            continue
        for descriptor in descriptors:
            try:
                target = Path(os.readlink(descriptor))
            except (FileNotFoundError, PermissionError, OSError):
                continue
            try:
                target.relative_to(SESSIONS)
            except ValueError:
                continue
            if target.name.endswith(".jsonl") and target.is_file():
                found.add(target)
    return sorted(found)


def first_metadata(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        record = json.loads(handle.readline())
    payload = record.get("payload", {}) if isinstance(record, dict) else {}
    source = payload.get("source")
    spawn: dict[str, Any] = {}
    if isinstance(source, dict):
        spawn = source.get("subagent", {}).get("thread_spawn", {})
    return {
        "id": payload.get("id") or payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "originator": payload.get("originator"),
        "cli_version": payload.get("cli_version"),
        "parent_thread_id": payload.get("parent_thread_id") or spawn.get("parent_thread_id"),
        "agent_path": payload.get("agent_path") or spawn.get("agent_path"),
        "agent_nickname": payload.get("agent_nickname") or spawn.get("agent_nickname"),
        "source_kind": "subagent" if spawn else str(source),
    }


def session_inventory(root_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    by_parent: dict[str, list[dict[str, Any]]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for path in SESSIONS.rglob("*.jsonl"):
        try:
            metadata = first_metadata(path)
            identity = metadata.get("id")
            if not isinstance(identity, str):
                continue
            stat_result = path.stat()
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        row = {
            **metadata,
            "path": str(path),
            "bytes": stat_result.st_size,
            "mtime_ns": stat_result.st_mtime_ns,
        }
        by_id[identity] = row
        parent = metadata.get("parent_thread_id")
        if isinstance(parent, str):
            by_parent.setdefault(parent, []).append(row)

    pending = [root_id]
    included: set[str] = set()
    while pending:
        identity = pending.pop()
        if identity in included:
            continue
        included.add(identity)
        pending.extend(
            child["id"] for child in by_parent.get(identity, [])
            if isinstance(child.get("id"), str)
        )
    for identity in sorted(included):
        row = by_id.get(identity)
        if row is not None:
            records.append(row)
    records.sort(key=lambda row: (row["mtime_ns"], row["id"]))
    return records


def copy_complete_jsonl(source: Path, destination: Path) -> dict[str, Any]:
    """Copy a fixed observed prefix, dropping only a possibly torn final line."""
    digest = hashlib.sha256()
    line_count = 0
    source_size = 0
    with source.open("rb") as reader, destination.open("wb") as writer:
        source_size = os.fstat(reader.fileno()).st_size
        remaining = source_size
        tail = b""
        while remaining:
            chunk = reader.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            data = tail + chunk
            parts = data.split(b"\n")
            tail = parts.pop()
            for part in parts:
                line = part + b"\n"
                json.loads(part.decode("utf-8"))
                writer.write(line)
                digest.update(line)
                line_count += 1
        writer.flush()
        os.fsync(writer.fileno())
    os.chmod(destination, 0o600)
    return {
        "source": str(source),
        "snapshot": str(destination),
        "source_bytes_observed": source_size,
        "snapshot_bytes": destination.stat().st_size,
        "complete_json_lines": line_count,
        "sha256": digest.hexdigest(),
        "dropped_torn_tail_bytes": len(tail),
    }


def tmux_state(output: Path) -> dict[str, Any]:
    sessions_result = run([
        "tmux", "list-sessions", "-F",
        "#{session_name}\t#{session_id}\t#{session_created}\t#{session_attached}\t#{session_windows}\t#{session_path}",
    ])
    panes_result = run([
        "tmux", "list-panes", "-a", "-F",
        "#{session_name}\t#{window_index}\t#{pane_index}\t#{pane_pid}\t#{pane_current_command}\t#{pane_current_path}",
    ])
    atomic_text(output / "tmux-sessions.tsv", sessions_result.stdout)
    atomic_text(output / "tmux-panes.tsv", panes_result.stdout)
    captures = []
    for line in panes_result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        target = f"{fields[0]}:{fields[1]}.{fields[2]}"
        captured = run(["tmux", "capture-pane", "-epJS", "-2000", "-t", target])
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", target) + ".txt"
        atomic_text(output / "tmux" / name, captured.stdout)
        captures.append({"target": target, "path": f"tmux/{name}", "rc": captured.returncode})
    return {
        "sessions_rc": sessions_result.returncode,
        "panes_rc": panes_result.returncode,
        "captures": captures,
    }


def git_state(cwd: str | None) -> dict[str, Any] | None:
    if not cwd:
        return None
    directory = Path(cwd)
    if not directory.is_dir():
        return None
    inside = run(["git", "rev-parse", "--is-inside-work-tree"], cwd=directory)
    if inside.returncode != 0:
        return None
    def value(*args: str) -> str:
        result = run(["git", *args], cwd=directory)
        return result.stdout.strip() if result.returncode == 0 else f"UNKNOWN(rc={result.returncode})"
    return {
        "cwd": cwd,
        "branch": value("branch", "--show-current"),
        "head": value("rev-parse", "HEAD"),
        "tree": value("rev-parse", "HEAD^{tree}"),
        "status": value("status", "--short", "--branch"),
    }


def role_rows(path: Path) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        fields = line.split()
        if len(fields) >= 3 and not fields[0].startswith("#"):
            result.append((fields[0], fields[1], fields[2]))
    return result


def fleet_state(output: Path, roles: Path, probe: Path) -> list[dict[str, Any]]:
    probe_text = probe.read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for role, name, address in role_rows(roles):
        if name in seen:
            continue
        seen.add(name)
        if name == "test5" or address in {"local", "127.0.0.1", "localhost"}:
            result = run(["bash", str(probe)], timeout=15)
        else:
            result = run([
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                f"mboyle@{address}", "bash", "-s",
            ], stdin=probe_text, timeout=15)
        rows.append({
            "role": role,
            "name": name,
            "address": address,
            "rc": result.returncode,
            "probe": result.stdout.strip(),
            "stderr": result.stderr.strip()[:500],
        })
    atomic_text(
        output / "fleet-probe.json",
        json.dumps(rows, indent=2, sort_keys=True) + "\n",
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-thread", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES)
    parser.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    args = parser.parse_args()
    if UUID.fullmatch(args.root_thread) is None:
        parser.error("--root-thread must be a lowercase UUID")

    os.umask(0o077)
    args.output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = args.output_root / stamp
    temporary = Path(tempfile.mkdtemp(prefix=f".{stamp}.", dir=args.output_root))
    try:
        (temporary / "sessions").mkdir(mode=0o700)
        (temporary / "tmux").mkdir(mode=0o700)
        processes = process_rows()
        active_paths = open_session_paths(processes)
        inventory = session_inventory(args.root_thread)
        active_copies = []
        for source in active_paths:
            destination = temporary / "sessions" / source.name
            copied = copy_complete_jsonl(source, destination)
            copied["snapshot"] = destination.relative_to(temporary).as_posix()
            active_copies.append(copied)
        tmux = tmux_state(temporary)
        fleet = fleet_state(temporary, args.roles, args.probe)
        cwds = sorted({row.get("cwd") for row in inventory if row.get("cwd")})
        git_rows = [state for cwd in cwds if (state := git_state(cwd)) is not None]
        manifest = {
            "schema": "bd-codex-session-snapshot/v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "host": socket.gethostname(),
            "root_thread": args.root_thread,
            "official_resume": f"cd {args.repo} && codex resume {args.root_thread}",
            "official_resume_all": "codex resume --all --include-non-interactive",
            "source_authority": str(SESSIONS),
            "sessions_in_root_tree": inventory,
            "active_session_copies": active_copies,
            "processes": processes,
            "tmux": tmux,
            "git": git_rows,
            "fleet": fleet,
            "explicitly_excluded": [
                str(CODEX / "auth.json"),
                "credential values and environment contents",
            ],
            "no_signal_guarantee": True,
        }
        atomic_text(temporary / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        os.replace(temporary, final)
        directory_fd = os.open(args.output_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        atomic_text(args.output_root / "LATEST", final.name + "\n")
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    print(final)
    print(f"sessions={len(inventory)} active_copies={len(active_copies)} fleet_hosts={len(fleet)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
