#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

BACKLOG_RE = re.compile(r"canonical-task-register schema=1 rows=(\d+) open=(\d+) ids-sha256=([0-9a-f]{64})")
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*([^|]+)\|", re.MULTILINE)
MAX_AGE_SECONDS = 86400

def git(repo, *args):
    env = os.environ.copy(); env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                                text=True, capture_output=True, check=False,
                                timeout=15, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"git {' '.join(args)} observation failed: {type(exc).__name__}") from exc
    if result.returncode:
        raise ValueError(f"git {' '.join(args)} failed")
    return result.stdout.strip()

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def sanitize_identity(value):
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        host = parsed.hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, ""))
    return value

def comparable_identity(value):
    if not isinstance(value, str):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return value
    path = parsed.path[:-4] if parsed.path.endswith(".git") else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))

def derive_backlog(text):
    rows = [(int(match.group(1)), match.group(2).strip()) for match in ROW_RE.finditer(text)]
    if not rows:
        return None
    ids = [str(row[0]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("UNKNOWN: duplicate backlog row identity")
    opened = sum(status == "OPEN" or status.startswith("OPEN ") for _, status in rows)
    digest = hashlib.sha256(",".join(ids).encode("ascii")).hexdigest()
    completed = [str(identity) for identity, status in rows if status.startswith(("CLOSED", "MOOT", "FIXED"))]
    return len(rows), opened, digest, completed

def derive(repo: Path):
    repo = repo.resolve()
    if git(repo, "status", "--porcelain=v1"):
        raise ValueError("UNKNOWN: repository is dirty")
    head = git(repo, "rev-parse", "HEAD")
    origin_main = git(repo, "rev-parse", "origin/main")
    identity = sanitize_identity(git(repo, "remote", "get-url", "origin"))
    version_path = repo / "bulk_downloader" / "__init__.py"
    backlog_path = repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
    ci_path = repo / ".github" / "workflows" / "ci.yml"
    try:
        version_text = version_path.read_text(encoding="utf-8")
        backlog_text = backlog_path.read_text(encoding="utf-8")
        ci_hash = sha(ci_path)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"UNKNOWN: required input unavailable: {type(exc).__name__}") from exc
    version_match, backlog_match = VERSION_RE.search(version_text), BACKLOG_RE.search(backlog_text)
    if not version_match or not backlog_match:
        raise ValueError("UNKNOWN: version or backlog authority malformed")
    tracked_tests = git(repo, "ls-files", "tests/test*.py").splitlines()
    derived_backlog = derive_backlog(backlog_text)
    declared = (int(backlog_match.group(1)), int(backlog_match.group(2)), backlog_match.group(3))
    if derived_backlog is None:
        raise ValueError("UNKNOWN: backlog table has no parseable rows")
    rows, opened, ids_digest, completed = derived_backlog
    marker_matches = (rows, opened, ids_digest) == declared
    test_list_bytes = ("\n".join(tracked_tests) + ("\n" if tracked_tests else "")).encode()
    inputs = {"bulk_downloader/__init__.py": sha(version_path),
              "project-knowledge/IMPROVEMENT_BACKLOG.md": sha(backlog_path),
              ".github/workflows/ci.yml": ci_hash,
              "git:tracked-tests": hashlib.sha256(test_list_bytes).hexdigest(),
              "git:HEAD": hashlib.sha256((head + "\n").encode()).hexdigest(),
              "git:origin-main": hashlib.sha256((origin_main + "\n").encode()).hexdigest()}
    return {"schema": "bd-current-state/v1",
            "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "repository": {"root": str(repo), "identity": identity, "head": head,
                           "origin_main": origin_main, "dirty": False},
            "version": version_match.group(1),
            "backlog": {"rows": rows, "open": opened, "ids_sha256": ids_digest,
                        "declared_rows": declared[0], "declared_open": declared[1],
                        "declared_ids_sha256": declared[2], "marker_matches": marker_matches},
            "completed_tasks": completed,
            "tests": {"tracked_test_files": len(tracked_tests),
                      "tracked_paths_sha256": hashlib.sha256(test_list_bytes).hexdigest()},
            "ci": {"workflow_sha256": ci_hash},
            "input_sha256": dict(sorted(inputs.items()))}

def comparable(payload):
    copy = dict(payload); copy.pop("generated_utc", None)
    repository = copy.get("repository")
    if isinstance(repository, dict):
        repository = dict(repository)
        repository["identity"] = comparable_identity(repository.get("identity"))
        copy["repository"] = repository
    return copy

def validate_freshness(payload):
    value = payload.get("generated_utc")
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("STALE: invalid generated_utc")
    try:
        observed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("STALE: invalid generated_utc") from exc
    age = (datetime.now(timezone.utc) - observed).total_seconds()
    if age < -300 or age > MAX_AGE_SECONDS:
        raise ValueError("STALE: current-state overlay timestamp outside acceptance window")

def atomic_write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".tmp")
    data = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    with tmp.open("x", encoding="utf-8") as handle:
        handle.write(data); handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        current = derive(args.repo)
        if args.check:
            existing = json.loads(args.out.read_text(encoding="utf-8"))
            validate_freshness(existing)
            if existing.get("backlog", {}).get("marker_matches") is False:
                raise ValueError("STALE: backlog marker does not match derived table")
            if comparable(existing) != comparable(current):
                raise ValueError("STALE: current-state overlay does not match repository")
            print("CURRENT STATE PASS")
        else:
            atomic_write(args.out, current); print("CURRENT STATE GENERATED")
        return 0
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr); return 2

if __name__ == "__main__": sys.exit(main())
