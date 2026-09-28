"""O1503 P1: bd-rag embeds through the LiteLLM rag-embed alias and runs a shadow index beside the primary.

BD_O1503_GPU_P1_CANDIDATE = absolute path of the candidate bd-rag dir (common2.py, server2.py, query2.py). Opt-in, no live
fallback. Hermetic: every endpoint is an in-test stub on 127.0.0.1; the candidate runs in a subprocess with the env it reads
at import. Evidence: findings/GPU-WORKLOAD-READINESS-bd-worker-B1-B.md (RAG embed GO on bge-m3@72, F3 num_ctx reloads).
"""
BD_GATE_SCOPE = "module"
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

CANDIDATE = os.environ.get("BD_O1503_GPU_P1_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


class Stub:
    """Records every request; answers with self.reply(path, body) after self.delay seconds."""

    def __init__(self, reply, delay=0.0):
        self.seen, self.reply, self.delay = [], reply, delay
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, body):
                stub.seen.append({"path": self.path, "headers": dict(self.headers), "body": body})
                time.sleep(stub.delay)
                b = json.dumps(stub.reply(self.path, body)).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

            def do_POST(self):
                self._answer(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

            def do_GET(self):
                self._answer(None)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def run(code, env, timeout=30):
    d = Path(CANDIDATE)
    assert (d / "common2.py").is_file() and (d / "server2.py").is_file(), f"candidate dir incomplete: {d}"
    e = {"PATH": os.environ["PATH"], "PYTHONPATH": str(d), "HOME": os.environ.get("HOME", "/tmp"), **env}
    try:
        r = subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail(f"O1503: candidate did not return in {timeout}s (module-level server start?)")
    assert r.returncode == 0, f"O1503: candidate failed rc={r.returncode}: {r.stderr[-600:]}"
    return r.stdout


EMBED = "from common2 import embed; import json; print(json.dumps(embed(['alpha', 'bravo'], 'search_document: ')))"


def test_openai_shape_via_litellm_alias_orders_vectors_and_sends_key_no_prefix_no_num_ctx():
    # LiteLLM may return data out of order; the index field is authoritative.
    st = Stub(lambda p, b: {"data": [{"index": 1, "embedding": [0.0, 2.0]}, {"index": 0, "embedding": [1.0, 0.0]}]})
    try:
        out = json.loads(run(EMBED, {"BD_RAG_EMBED_URL": st.url + "/v1/embeddings", "BD_RAG_MODEL": "rag-embed",
                                     "BD_RAG_EMBED_KEY": "k-test", "BD_RAG_NO_PREFIX": "1"}))
    finally:
        st.close()
    assert st.seen, "stub saw no request"  # positive control: the probe can see a call
    assert out == [[1.0, 0.0], [0.0, 2.0]], f"O1503: vectors not in input order: {out}"
    req = st.seen[0]
    assert req["path"] == "/v1/embeddings" and req["headers"].get("Authorization") == "Bearer k-test"
    assert req["body"] == {"model": "rag-embed", "input": ["alpha", "bravo"]}, f"O1503: body carries prefix/num_ctx: {req['body']}"


def test_ollama_shape_unchanged_and_key_not_leaked():
    st = Stub(lambda p, b: {"embeddings": [[1.0], [2.0]]})
    try:
        out = json.loads(run(EMBED, {"BD_RAG_EMBED_URL": st.url + "/api/embed", "BD_RAG_EMBED_KEY": "k-test"}))
    finally:
        st.close()
    assert out == [[1.0], [2.0]]
    req = st.seen[0]
    assert req["body"] == {"model": "nomic-embed-text", "input": ["search_document: alpha", "search_document: bravo"]}
    assert "Authorization" not in req["headers"], "O1503: embed key sent to a keyless Ollama endpoint"


def test_short_openai_answer_is_an_error_not_a_silent_misalignment():
    st = Stub(lambda p, b: {"data": [{"index": 0, "embedding": [1.0]}]})
    code = ("from common2 import embed\ntry:\n    embed(['a', 'b'], '')\nexcept ValueError as e:\n    print('VALUEERROR', e)\n")
    try:
        out = run(code, {"BD_RAG_EMBED_URL": st.url + "/v1/embeddings"})
    finally:
        st.close()
    assert "VALUEERROR" in out and "1 vectors for 2 texts" in out, f"O1503: short answer accepted: {out}"


PRIMARY = [{"id": 1, "path": "a.md", "heading": "H1", "commit": "fs", "score": 1.0, "slice": "x"},
           {"id": 2, "path": "b.md", "heading": "H2", "commit": "fs", "score": 0.5, "slice": "y"}]

SERVE = """
import json, threading, time, urllib.request, server2
server2.search = lambda q, k=5, pf=None: json.loads('''%s''')
srv = server2.ThreadingHTTPServer(("127.0.0.1", 0), server2.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
t0 = time.time()
with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/q?q=where+is+x&k=2", timeout=30) as r:
    body = json.load(r)
print(json.dumps({"primary_sec": round(time.time() - t0, 2), "body": body}))
deadline = time.time() + %s
while time.time() < deadline and not open(%r).read().strip():
    time.sleep(0.1)
""" % (json.dumps(PRIMARY), "{wait}", "{log}")


def serve(tmp_path, shadow_url, wait):
    log = tmp_path / "shadow.jsonl"; log.write_text("")
    code = SERVE.replace('"{wait}"', str(wait)).replace("'{log}'", repr(str(log))).replace("{wait}", str(wait)).replace("{log}", str(log))
    out = json.loads(run(code, {"BD_RAG_SHADOW_URL": shadow_url, "BD_RAG_SHADOW_LOG": str(log)}, timeout=wait + 30))
    rows = [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    return out, rows


def test_shadow_logs_both_paths_and_never_delays_primary(tmp_path):
    shadow_ans = [{"id": 9, "path": "b.md", "heading": "H2", "commit": "fs", "score": 1, "slice": "y"},
                  {"id": 8, "path": "c.md", "heading": "H3", "commit": "fs", "score": 1, "slice": "z"}]
    st = Stub(lambda p, b: shadow_ans, delay=3.0)  # a slow shadow must not hold the primary answer
    try:
        out, rows = serve(tmp_path, st.url, wait=15)
    finally:
        st.close()
    assert out["body"] == PRIMARY, "O1503: primary answer changed by shadow mode"
    assert out["primary_sec"] < 2.0, f"O1503: primary waited on the shadow ({out['primary_sec']} s)"
    assert st.seen and st.seen[0]["path"].startswith("/q?q=where+is+x&k=2"), f"shadow not asked the same query: {st.seen}"
    assert len(rows) == 1, f"O1503: shadow log rows={rows}"
    r = rows[0]
    assert r["q"] == "where is x" and r["k"] == 2
    assert r["primary"] == ["a.md#H1", "b.md#H2"] and r["shadow"] == ["b.md#H2", "c.md#H3"] and r["overlap"] == 1


def test_shadow_down_is_logged_and_primary_still_answers(tmp_path):
    out, rows = serve(tmp_path, "http://127.0.0.1:9", wait=10)  # port 9 (discard) refuses
    assert out["body"] == PRIMARY
    assert len(rows) == 1 and rows[0]["shadow"] is None and "err" in rows[0], f"O1503: shadow failure not recorded: {rows}"


def test_no_shadow_url_means_no_log_and_no_thread(tmp_path):
    out, rows = serve(tmp_path, "", wait=1)
    assert out["body"] == PRIMARY and rows == []
