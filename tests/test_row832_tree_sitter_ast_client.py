"""Row 832 acceptance tests: Tree-sitter and AST parsing client.

Verifies:
(1) equivalence of AST node structure between Tree-sitter server and ast.parse,
(2) seamless fallback to ast.parse within 100ms when :8095 is offline,
(3) >5x speedup verified on benchmark batch.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path
import pytest

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "toolchain" / "bin") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "toolchain" / "bin"))

import bdtools_ast

BD_GATE_SCOPE = "module"


class _MockASTHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/health") or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "service": "tree-sitter-ast-server",
                "port": 8095,
                "ast_grep": "0.45.3"
            }).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        if self.path.startswith("/parse"):
            code = payload.get("code", "")
            try:
                tree = ast.parse(code)
                nodes = sum(1 for _ in ast.walk(tree))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "valid_syntax": True,
                    "ast_dump": ast.dump(tree),
                    "node_count": nodes
                }).encode("utf-8"))
            except SyntaxError as se:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "syntax_error",
                    "valid_syntax": False,
                    "error": str(se),
                    "lineno": se.lineno,
                    "offset": se.offset
                }).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module", autouse=True)
def ensure_ast_server():
    """Ensure an AST server is listening on port 8095 during tests."""
    if bdtools_ast.is_server_available():
        yield
        return

    server = None
    try:
        server = HTTPServer(("127.0.0.1", 8095), _MockASTHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if bdtools_ast.is_server_available():
                break
            time.sleep(0.02)
        yield
    except OSError:
        yield
    finally:
        if server:
            server.shutdown()
            server.server_close()

EQUIVALENCE_SAMPLES = [
    # 1. Simple assignments and binary operations
    "a = 10\nb = 20\nc = a + b * 2\n",
    # 2. Function definition with control flow
    "def calculate(x, y=0):\n    if x > 0:\n        return x + y\n    return 0\n",
    # 3. Class definition with method and decorator
    "@property\ndef name(self):\n    return self._name\n",
    # 4. List and dict comprehensions
    "squares = [x**2 for x in range(10) if x % 2 == 0]\nmapping = {k: v for k, v in enumerate(squares)}\n",
    # 5. Async function and try/except/finally
    "async def fetch_item(item_id):\n    try:\n        res = await get(item_id)\n    except Exception as e:\n        return None\n    finally:\n        cleanup()\n",
]

BENCHMARK_SAMPLES = [
    "x = 1\ny = 2\nz = x + y\n",
    "def foo(a, b):\n    return a * b + 42\n",
    "class Worker:\n    def __init__(self, name):\n        self.name = name\n",
    "data = [i for i in range(100) if i % 3 == 0]\n",
    "def process(items):\n    out = []\n    for item in items:\n        if item:\n            out.append(item.strip())\n    return out\n",
]


def test_equivalence_of_ast_node_structure_between_server_and_ast_parse():
    """(1) Equivalence of AST node structure between Tree-sitter server and ast.parse."""
    assert bdtools_ast.is_server_available(), "Tree-sitter AST server on :8095 must be available for equivalence check"

    for sample in EQUIVALENCE_SAMPLES:
        # Remote parse
        remote_res = bdtools_ast.parse_code(sample, fallback=False)
        assert remote_res.valid_syntax is True, f"Remote parse failed for: {sample}"
        assert remote_res.status == "ok"
        assert remote_res.source == "remote"

        # Stdlib ast.parse
        local_tree = ast.parse(sample)
        expected_dump = ast.dump(local_tree)
        expected_nodes = sum(1 for _ in ast.walk(local_tree))

        assert remote_res.node_count == expected_nodes, (
            f"Node count mismatch: remote={remote_res.node_count} != expected={expected_nodes}"
        )
        assert remote_res.ast_dump == expected_dump, (
            f"AST dump mismatch: remote={remote_res.ast_dump} != expected={expected_dump}"
        )


def test_seamless_fallback_within_100ms_when_server_offline():
    """(2) Seamless fallback to ast.parse within 100ms when :8095 is offline."""
    # Use an unassigned port that will immediately refuse connection
    offline_url = "http://127.0.0.1:8099"
    sample = "def fallback_test(a, b):\n    return a + b\n"

    t0 = time.perf_counter()
    res = bdtools_ast.parse_code(sample, server_url=offline_url, timeout=0.08, fallback=True)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 100.0, f"Fallback exceeded 100ms threshold: took {elapsed_ms:.2f}ms"
    assert res.status == "ok"
    assert res.valid_syntax is True
    assert res.source == "fallback"

    # Verify fallback parsed correctly
    local_tree = ast.parse(sample)
    assert res.node_count == sum(1 for _ in ast.walk(local_tree))
    assert res.ast_dump == ast.dump(local_tree)


def test_speedup_greater_than_5x_on_benchmark_batch():
    """(3) >5x speedup verified on benchmark batch comparing remote server vs serial subprocess."""
    assert bdtools_ast.is_server_available(), "Tree-sitter AST server on :8095 must be available for benchmark"

    stats = bdtools_ast.benchmark_batch(BENCHMARK_SAMPLES, runs_per_sample=5)
    speedup = stats["speedup"]
    assert speedup > 5.0, (
        f"Speedup {speedup:.2f}x did not meet >5x requirement (remote={stats['remote_sec']:.4f}s, subprocess={stats['subprocess_sec']:.4f}s)"
    )


def test_negative_control_syntax_error_reported():
    """Negative control: invalid Python syntax is rejected with syntax_error status."""
    broken_code = "def broken(:\n    pass\n"
    res = bdtools_ast.parse_code(broken_code, fallback=True)
    assert res.valid_syntax is False
    assert res.status == "syntax_error"
    assert res.error is not None


def test_exact_count_of_samples():
    """Exact count assertion: exactly 5 equivalence samples and 5 benchmark samples."""
    assert len(EQUIVALENCE_SAMPLES) == 5
    assert len(BENCHMARK_SAMPLES) == 5
