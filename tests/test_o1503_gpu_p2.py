"""O1503 P2: bd-ci-summarize -- async CI-log summaries on the LiteLLM ci-summary alias, input capped at 8k tokens.

BD_O1503_GPU_P2_CANDIDATE = absolute path of the candidate bd-ci-summarize.py (opt-in; no live fallback). Hermetic: the
LiteLLM endpoint is an in-test stub on 127.0.0.1, the queue lives in tmp_path. Evidence: findings/GPU-WORKLOAD-READINESS-
bd-worker-B1-B.md (CI summarisation GO for async; 228 NUM_PARALLEL=4; F3 num_ctx reloads).
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

CANDIDATE = os.environ.get("BD_O1503_GPU_P2_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
CAP_CHARS = 8000 * 3


class Stub:
    def __init__(self, status=200, delay=0.0, content="FAIL\ntests/test_x.py::test_y -- AssertionError at x.py:12"):
        self.seen, self.live, self.peak, self.lock = [], 0, 0, threading.Lock()
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with stub.lock:
                    stub.live += 1; stub.peak = max(stub.peak, stub.live)
                    stub.seen.append({"headers": dict(self.headers), "body": body})
                time.sleep(delay)
                with stub.lock:
                    stub.live -= 1
                b = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
                self.send_response(status); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1/chat/completions"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def q(tmp_path):
    cand = Path(CANDIDATE)
    assert cand.is_file(), f"candidate supplied but absent: {cand}"
    return tmp_path


def cli(q, *args, url="http://127.0.0.1:9/v1/chat/completions", timeout=60, **env):
    e = {"PATH": os.environ["PATH"], "HOME": str(q), "BD_CI_SUMMARY_QUEUE": str(q / "queue"), "LITELLM_URL": url,
         "LITELLM_MASTER_KEY": "k-test", "BD_CI_SUMMARY_TIMEOUT": "20", **env}
    return subprocess.run([sys.executable, CANDIDATE, *args], env=e, capture_output=True, text=True, timeout=timeout, check=False)


def log(q, name, text):
    p = q / name; p.write_text(text); return p


def state(q, s):
    return sorted((q / "queue" / s).glob("*.json"))


def test_enqueue_is_async_and_drain_writes_summary(q):
    st = Stub()
    try:
        lg = log(q, "ci.log", "collecting...\nFAILED tests/test_x.py::test_y\nE AssertionError\n")
        r = cli(q, "enqueue", str(lg), url=st.url)
        assert r.returncode == 0 and not st.seen, "O1503: enqueue made a network call (not async)"
        assert len(state(q, "pending")) == 1
        r = cli(q, "drain", url=st.url)
        assert r.returncode == 0, r.stderr
    finally:
        st.close()
    counts = json.loads(r.stdout)
    assert counts["done"] == 1 and counts["pending"] == 0 and counts["failed"] == 0, counts
    out = (q / "ci.log.summary.md").read_text()
    assert "FAIL" in out and "test_x.py::test_y" in out
    body = st.seen[0]["body"]
    assert body["model"] == "ci-summary", f"O1503: wrong alias {body['model']}"
    assert "num_ctx" not in json.dumps(body) and "options" not in body, f"O1503: num_ctx sent (reload/evict, F3): {body}"
    assert st.seen[0]["headers"].get("Authorization") == "Bearer k-test"
    assert len(state(q, "done")) == 1 and not state(q, "pending") and not state(q, "running")


def test_input_capped_at_8k_tokens_keeping_head_and_tail(q):
    st = Stub()
    lines = [f"line {i:06d} " + "x" * 60 for i in range(4000)]  # ~276k chars, far over the cap
    try:
        lg = log(q, "big.log", "\n".join(lines))
        cli(q, "enqueue", str(lg), url=st.url); cli(q, "drain", url=st.url)
    finally:
        st.close()
    assert st.seen, "stub saw no request"
    sent = st.seen[0]["body"]["messages"][0]["content"]
    assert len(sent) <= CAP_CHARS, f"O1503: {len(sent)} chars sent, cap {CAP_CHARS} (8k tokens x 3)"
    assert "line 000000" in sent and "line 003999" in sent, "O1503: cap dropped the head or the tail of the log"
    assert "cut by bd-ci-summarize" in sent


def test_small_log_is_sent_whole(q):
    st = Stub()
    try:
        lg = log(q, "s.log", "only line\n")
        cli(q, "enqueue", str(lg), url=st.url); cli(q, "drain", url=st.url)
    finally:
        st.close()
    assert st.seen[0]["body"]["messages"][0]["content"].endswith("only line\n")


def test_failures_retry_then_park_in_failed(q):
    st = Stub(status=500)
    try:
        lg = log(q, "f.log", "boom\n")
        cli(q, "enqueue", str(lg), url=st.url)
        for n in (1, 2):
            r = cli(q, "drain", url=st.url)
            assert json.loads(r.stdout)["pending"] == 1, f"O1503: attempt {n} not returned to pending: {r.stdout}"
        r = cli(q, "drain", url=st.url)
    finally:
        st.close()
    assert json.loads(r.stdout)["failed"] == 1
    job = json.loads(state(q, "failed")[0].read_text())
    assert job["attempts"] == 3 and "HTTPError" in job["last_error"]
    assert not (q / "f.log.summary.md").exists()


def test_workers_bounded_by_num_parallel_and_actually_parallel(q):
    st = Stub(delay=1.0)
    try:
        for i in range(8):
            cli(q, "enqueue", str(log(q, f"p{i}.log", f"log {i}\n")), url=st.url)
        assert cli(q, "drain", "--workers", "5", url=st.url).returncode != 0, "O1503: 5 workers accepted (228 NUM_PARALLEL=4)"
        t0 = time.time(); r = cli(q, "drain", "--workers", "4", url=st.url); wall = time.time() - t0
    finally:
        st.close()
    assert json.loads(r.stdout)["done"] == 8
    assert st.peak == 4, f"O1503: peak concurrency {st.peak}, want 4"
    assert wall < 6.0, f"O1503: 8 jobs x 1 s with 4 workers took {wall:.1f} s (serialised?)"


def test_concurrent_drains_never_double_process(q):
    st = Stub(delay=0.3)
    try:
        for i in range(6):
            cli(q, "enqueue", str(log(q, f"c{i}.log", f"log {i}\n")), url=st.url)
        e = {"PATH": os.environ["PATH"], "HOME": str(q), "BD_CI_SUMMARY_QUEUE": str(q / "queue"), "LITELLM_URL": st.url}
        ps = [subprocess.Popen([sys.executable, CANDIDATE, "drain", "--workers", "2"], env=e, stdout=subprocess.PIPE, text=True)
              for _ in range(3)]
        outs = [json.loads(p.communicate(timeout=60)[0]) for p in ps]
    finally:
        st.close()
    assert sum(o["done"] for o in outs) == 6 and len(st.seen) == 6, f"O1503: {len(st.seen)} calls for 6 jobs: {outs}"


def test_dry_run_plans_only(q):
    st = Stub()
    try:
        cli(q, "enqueue", str(log(q, "d.log", "x\n")), url=st.url)
        r = cli(q, "drain", url=st.url, DRY_RUN="1")
    finally:
        st.close()
    assert "DRY_RUN would summarise" in r.stdout and not st.seen and len(state(q, "pending")) == 1


def test_enqueue_refuses_missing_log(q):
    r = cli(q, "enqueue", str(q / "nope.log"))
    assert r.returncode != 0 and "REFUSED" in (r.stdout + r.stderr)


# ---- G2 (REFUTE bd-cx-worker-2, .review/VERDICT-correctness-bd-cx-worker-2.md) ----

def _load(q, monkeypatch, url):
    import importlib.util
    monkeypatch.setenv("BD_CI_SUMMARY_QUEUE", str(q / "queue")); monkeypatch.setenv("LITELLM_URL", url)
    monkeypatch.setenv("BD_CI_SUMMARY_TIMEOUT", "5")
    spec = importlib.util.spec_from_file_location(f"cis_{time.time_ns()}", CANDIDATE)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("status", [500, 200])
def test_job_is_in_exactly_one_state_at_every_step(q, monkeypatch, status):
    # F1: a retry that PUBLISHES pending/<job> before unlinking running/<job> opens a window in which a second drain
    # claims it and the first drain then deletes that claim. Snapshot the population after every queue mutation.
    st = Stub(status=status)
    try:
        m = _load(q, monkeypatch, st.url)
        cli(q, "enqueue", str(log(q, "r.log", "boom\n")), url=st.url)
        name = state(q, "pending")[0].name
        seen = []

        def census():
            seen.append(sum((q / "queue" / s / name).exists() for s in ("pending", "running", "done", "failed")))

        for fn in ("atomic_write",):
            orig = getattr(m, fn)
            monkeypatch.setattr(m, fn, lambda *a, _o=orig: (_o(*a), census())[0])
        real_rename, real_unlink = os.rename, Path.unlink
        monkeypatch.setattr(m.os, "rename", lambda a, b: (real_rename(a, b), census())[0])
        monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (real_unlink(self, *a, **k), census())[0])
        claimed = m.claim(state(q, "pending")[0])
        m.work(claimed)
    finally:
        st.close()
    assert seen, "census recorded nothing (probe cannot see queue mutations)"
    assert set(seen) == {1}, f"O1503: job population over time {seen} -- a second drain could claim a published copy (F1)"


def test_overlapping_writes_to_one_path_never_share_a_temporary(q, monkeypatch):
    # F2: per-PID temporaries collided across pool threads writing the same --out (two jobs, one summary path).
    # Deterministic: both threads write their temporary at once, then every os.replace holds 0.3 s -- so with a shared
    # temporary the first replace consumes it and the second finds nothing.
    m = _load(q, monkeypatch, "http://127.0.0.1:9/v1/chat/completions")
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (time.sleep(0.3), real(a, b))[1])
    target, errs = q / "shared.summary.md", []

    def one(txt):
        try:
            m.atomic_write(target, txt)
        except Exception as e:  # noqa: BLE001
            errs.append(repr(e))

    ts = [threading.Thread(target=one, args=(f"summary {i}\n",)) for i in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errs, f"O1503: overlapping writes to one --out collided on a shared temporary (F2): {errs}"
    assert target.read_text() in ("summary 0\n", "summary 1\n")
    assert not list(q.glob(".shared.summary.md.*.tmp")), "temporaries left behind"


def test_worker_budget_is_host_wide_across_drains(q):
    # F3: two overlapping drains with --workers 4 each must still issue at most 4 concurrent model calls.
    st = Stub(delay=1.0)
    try:
        for i in range(8):
            cli(q, "enqueue", str(log(q, f"g{i}.log", f"log {i}\n")), url=st.url)
        e = {"PATH": os.environ["PATH"], "HOME": str(q), "BD_CI_SUMMARY_QUEUE": str(q / "queue"), "LITELLM_URL": st.url}
        ps = []
        for _ in range(2):  # drain B starts only after A has claimed its 4, so each holds 4 jobs at once
            ps.append(subprocess.Popen([sys.executable, CANDIDATE, "drain", "--workers", "4", "--max", "4"], env=e,
                                       stdout=subprocess.PIPE, text=True))
            deadline = time.time() + 20
            while len(state(q, "running")) < 4 and time.time() < deadline:
                time.sleep(0.02)
            assert len(state(q, "running")) >= 4, "fixture: first drain never held 4 jobs"
        outs = [json.loads(p.communicate(timeout=60)[0]) for p in ps]
    finally:
        st.close()
    assert sum(o["done"] for o in outs) == 8, outs
    assert st.peak <= 4, f"O1503: {st.peak} concurrent model calls across two drains; host budget is 4 (F3)"
