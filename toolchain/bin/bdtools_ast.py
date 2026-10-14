#!/usr/bin/env python3
"""bdtools_ast -- Tree-sitter & AST client for mutation gates and audits (Row 832).

# Queries the dedicated Tree-sitter & AST microservice on port 8095 (or AST_SERVER_URL)
# to extract AST node trees and footgun patterns in <2ms, falling back seamlessly to
# stdlib `ast.parse` within 100ms when the service is offline.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import http.client
import json
import os
import subprocess
import sys
import time
from typing import Optional
import urllib.parse
import urllib.request

DEFAULT_SERVER_URL = os.environ.get("AST_SERVER_URL", "http://127.0.0.1:8095")
DEFAULT_TIMEOUT_SEC = 0.08  # 80ms default timeout ensures fallback well within 100ms


@dataclass(frozen=True)
class ParseResult:
    status: str            # "ok" | "syntax_error" | "offline" | "error"
    valid_syntax: bool
    ast_dump: str
    node_count: int
    source: str            # "remote" | "fallback"
    error: Optional[str] = None
    lineno: Optional[int] = None
    offset: Optional[int] = None


def _parse_stdlib(code: str) -> ParseResult:
    """Parse code locally using Python stdlib ast.parse."""
    try:
        tree = ast.parse(code)
        nodes = sum(1 for _ in ast.walk(tree))
        return ParseResult(
            status="ok",
            valid_syntax=True,
            ast_dump=ast.dump(tree),
            node_count=nodes,
            source="fallback",
        )
    except SyntaxError as se:
        return ParseResult(
            status="syntax_error",
            valid_syntax=False,
            ast_dump="",
            node_count=0,
            source="fallback",
            error=str(se),
            lineno=se.lineno,
            offset=se.offset,
        )


# Every way the remote leg can fail to give a usable answer. ValueError covers a
# malformed URL port (urlparse .port raises it), undecodable/invalid JSON and a
# reply field of the wrong type; TypeError a reply value int() cannot take.
_UNREACHABLE = (OSError, http.client.HTTPException, ValueError, TypeError)


def _endpoint(server_url: str, default_port: int | None = None) -> tuple[str, int]:
    """(host, port) of SERVER_URL; ValueError on a port urlparse cannot read."""
    parsed = urllib.parse.urlparse(server_url)
    host = parsed.hostname or "127.0.0.1"
    if default_port is None:
        default_port = 443 if parsed.scheme == "https" else 80
    return host, parsed.port or default_port


def is_server_available(server_url: str = DEFAULT_SERVER_URL, timeout: float = DEFAULT_TIMEOUT_SEC) -> bool:
    """Check if the Tree-sitter AST server is reachable and reporting healthy."""
    try:
        host, port = _endpoint(server_url)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        if resp.status == 200:
            data = json.loads(resp.read().decode("utf-8"))
            conn.close()
            return isinstance(data, dict) and data.get("status") == "ok"
        conn.close()
        return False
    except _UNREACHABLE:
        return False


def parse_code(
    code: str,
    server_url: str = DEFAULT_SERVER_URL,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    fallback: bool = True,
) -> ParseResult:
    """Parse Python code using the remote Tree-sitter server, falling back to ast.parse."""
    try:
        host, port = _endpoint(server_url)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        body = json.dumps({"code": code}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        conn.request("POST", "/parse", body=body, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        if not isinstance(data, dict):
            raise ValueError(f"reply is not a JSON object: {data!r}")

        if data.get("status") == "ok":
            return ParseResult(
                status="ok",
                valid_syntax=bool(data.get("valid_syntax", True)),
                ast_dump=str(data.get("ast_dump", "")),
                node_count=int(data.get("node_count", 0)),
                source="remote",
            )
        elif data.get("status") == "syntax_error":
            return ParseResult(
                status="syntax_error",
                valid_syntax=False,
                ast_dump="",
                node_count=0,
                source="remote",
                error=data.get("error"),
                lineno=data.get("lineno"),
                offset=data.get("offset"),
            )
        else:
            if not fallback:
                raise RuntimeError(f"Server returned unexpected status: {data}")
            return _parse_stdlib(code)

    except _UNREACHABLE as exc:
        if not fallback:
            raise RuntimeError(f"Remote AST server error at {server_url}: {exc}") from exc
        return _parse_stdlib(code)


def query_pattern(
    pattern: str,
    code: str,
    lang: str = "python",
    server_url: str = DEFAULT_SERVER_URL,
    timeout: float = 1.0,
) -> dict:
    """Query ast-grep / footgun pattern on the remote server."""
    try:
        host, port = _endpoint(server_url)
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        body = json.dumps({"pattern": pattern, "code": code, "lang": lang}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        conn.request("POST", "/query", body=body, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        if not isinstance(data, dict):
            raise ValueError(f"reply is not a JSON object: {data!r}")
        return data
    except _UNREACHABLE as exc:
        return {"status": "offline", "matches": "", "returncode": 1, "error": str(exc)}


def benchmark_batch(
    code_samples: list[str],
    runs_per_sample: int = 5,
    server_url: str = DEFAULT_SERVER_URL,
) -> dict[str, float]:
    """Benchmark remote parsing server vs serial subprocess ast.parse."""
    # 1. Benchmark serial subprocess ast.parse
    t0 = time.perf_counter()
    for sample in code_samples:
        for _ in range(runs_per_sample):
            subprocess.run(
                [sys.executable, "-c", f"import ast; ast.parse({sample!r})"],
                check=True,
                capture_output=True,
            )
    subprocess_sec = time.perf_counter() - t0

    # 2. Benchmark remote server with persistent HTTP connection
    host, port = _endpoint(server_url, 8095)

    t0 = time.perf_counter()
    conn = http.client.HTTPConnection(host, port, timeout=2.0)
    for sample in code_samples:
        body = json.dumps({"code": sample}).encode("utf-8")
        headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        for _ in range(runs_per_sample):
            conn.request("POST", "/parse", body=body, headers=headers)
            resp = conn.getresponse()
            _ = resp.read()
    conn.close()
    remote_sec = time.perf_counter() - t0

    speedup = (subprocess_sec / remote_sec) if remote_sec > 0 else 0.0
    return {
        "subprocess_sec": subprocess_sec,
        "remote_sec": remote_sec,
        "speedup": speedup,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: bdtools_ast.py <parse <file> | health | benchmark>", file=sys.stderr)
        return 2

    cmd = sys.argv[1]
    if cmd == "health":
        avail = is_server_available()
        print(f"AST server at {DEFAULT_SERVER_URL}: {'ONLINE' if avail else 'OFFLINE'}")
        return 0 if avail else 1
    elif cmd == "parse":
        if len(sys.argv) < 3:
            print("Usage: bdtools_ast.py parse <file>", file=sys.stderr)
            return 2
        path = sys.argv[2]
        try:
            with open(path, encoding="utf-8") as f:
                code = f.read()
        except (OSError, UnicodeDecodeError) as exc:
            print(f"CANNOT-EVALUATE parse {path}: reason=UNREADABLE ({exc})", file=sys.stderr)
            return 2
        res = parse_code(code)
        print(f"Status: {res.status} (source: {res.source}), nodes: {res.node_count}")
        return 0 if res.valid_syntax else 1
    elif cmd == "benchmark":
        samples = ["x = 1\ny = 2\nz = x + y\n"] * 5
        try:
            stats = benchmark_batch(samples)
        except _UNREACHABLE as exc:
            print(f"CANNOT-EVALUATE benchmark {DEFAULT_SERVER_URL}: reason=SERVER-UNREACHABLE ({exc})",
                  file=sys.stderr)
            return 2
        print(f"Subprocess: {stats['subprocess_sec']:.4f}s, Remote: {stats['remote_sec']:.4f}s, Speedup: {stats['speedup']:.1f}x")
        return 0
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
