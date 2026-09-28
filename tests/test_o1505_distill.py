"""O1505: bd_satellite_distill.py distils through the LiteLLM "distill" alias (hermes3 @125) with room for the schema JSON.

BD_O1505_DISTILL_CANDIDATE = absolute path of the candidate bd_satellite_distill.py (opt-in; no live fallback). Hermetic:
LiteLLM and Ollama are in-test stubs on 127.0.0.1. Live evidence: harness-work/PLAN-2040/gpu-verify-B1-B/distill-ab.txt
(12 real say.log texts: shipped 0/12 distilled at the 3 s breaker; candidate 11/12, p50 2.0 s).
"""
BD_GATE_SCOPE = "module"
import importlib.util
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

CANDIDATE = os.environ.get("BD_O1505_DISTILL_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
GOOD = {"from": "bd-x", "status": "PASS", "summary": "p2 cut done", "path": "/a/DONE.md", "action": "lens"}


class Stub:
    def __init__(self, kind, delay=0.0, status=200):
        self.seen = []
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.seen.append(body)
                time.sleep(delay)
                if kind == "litellm":
                    out = {"choices": [{"message": {"content": json.dumps(GOOD)}}]}
                else:
                    out = {"message": {"content": json.dumps(GOOD)}}
                b = json.dumps(out).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.url = base + ("/v1/chat/completions" if kind == "litellm" else "/api/chat")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def load(monkeypatch, **env):
    for k in ("BD_DISTILL_OLLAMA_FALLBACK", "BD_DISTILL_GATEWAY_MODEL", "BD_DISTILL_MAX_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert os.path.isfile(CANDIDATE), f"candidate supplied but absent: {CANDIDATE}"
    spec = importlib.util.spec_from_file_location(f"distill_{time.time_ns()}", CANDIDATE)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


TEXT = "bd-worker-B1-B finished the P2 cut, DONE file at /home/mboyle/bd-cuts/cut/o1503-gpu-p2/DONE.md, lens queued"


def test_primary_path_is_the_distill_alias_with_room_for_the_json(monkeypatch):
    m = load(monkeypatch)
    lit, oll = Stub("litellm"), Stub("ollama")
    try:
        out, ok = m.distill_message("bd-x", TEXT, endpoint=oll.url, gateway_url=lit.url, api_key="k")
    finally:
        lit.close(); oll.close()
    assert lit.seen, "stub saw no request"
    assert ok and json.loads(out)["summary"] == "p2 cut done"
    body = lit.seen[0]
    assert body["model"] == "distill", f"O1505: gateway model {body['model']!r}, want the dedicated 'distill' alias"
    assert body["max_tokens"] >= 70, f"O1505: max_tokens {body['max_tokens']} truncates the schema JSON (42-65 seen)"
    assert not oll.seen, "O1505: direct Ollama (228) was called although the distill alias answered"


def test_ollama_fallback_is_opt_in(monkeypatch):
    m = load(monkeypatch)
    lit, oll = Stub("litellm", status=500), Stub("ollama")
    try:
        out, ok = m.distill_message("bd-x", TEXT, endpoint=oll.url, gateway_url=lit.url, api_key="k")
    finally:
        lit.close(); oll.close()
    assert lit.seen and not ok and out == TEXT
    assert not oll.seen, "O1505: fell back onto 228 without BD_DISTILL_OLLAMA_FALLBACK=1 (breaks 125 isolation)"


def test_ollama_fallback_when_enabled_uses_same_token_room(monkeypatch):
    m = load(monkeypatch, BD_DISTILL_OLLAMA_FALLBACK="1")
    lit, oll = Stub("litellm", status=500), Stub("ollama")
    try:
        out, ok = m.distill_message("bd-x", TEXT, endpoint=oll.url, gateway_url=lit.url, api_key="k")
    finally:
        lit.close(); oll.close()
    assert ok and oll.seen, "O1505: enabled fallback did not reach Ollama"
    assert oll.seen[0]["options"]["num_predict"] >= 70 and "num_ctx" not in oll.seen[0]["options"]


def test_breaker_still_bounds_a_slow_gateway(monkeypatch):
    m = load(monkeypatch)
    lit = Stub("litellm", delay=5.0)
    try:
        t0 = time.time(); out, ok = m.distill_message("bd-x", TEXT, gateway_url=lit.url, api_key="k", timeout=3.0)
        wall = time.time() - t0
    finally:
        lit.close()
    assert not ok and out == TEXT
    assert wall < 3.5, f"O1505: 3 s breaker overrun ({wall:.1f} s) -- bd-say waits on this"


def test_emergency_text_bypasses_the_model(monkeypatch):
    m = load(monkeypatch)
    lit = Stub("litellm")
    try:
        out, ok = m.distill_message("bd-x", "STOP all sends now", gateway_url=lit.url, api_key="k")
    finally:
        lit.close()
    assert out == "STOP all sends now" and not ok and not lit.seen
