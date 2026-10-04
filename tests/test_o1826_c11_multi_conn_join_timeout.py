BD_GATE_SCOPE = "module"

"""O1826 BRIEF-11: multi_conn.download() joins chunk threads with a bound.

M124: a chunk worker that hangs (no progress, never returns) used to block
download() forever because the join was unbounded. M123: the MPTCP
telemetry entry was registered as host:443 regardless of the URL.
"""

import http.server
import socketserver
import threading
import time

import httpx
import pytest

import bulk_downloader.ssrf_transport as ssrf_transport
from bulk_downloader import multi_conn
from bulk_downloader.mptcp_subflow import (
    MptcpCapabilityResult,
    MptcpCapabilityState,
)

# Hard wall-clock budget for one download() call in these tests.
BUDGET_S = 10.0
# Small timeouts; with small_window the stall window = 2 * 0.5 + 1 = 2 s.
FAST = dict(timeout_s=0.5, connect_timeout_s=0.5, chunk_retries=0)


@pytest.fixture
def small_window(monkeypatch):
    # Shrink the phase count so stall cases finish fast; the mechanism
    # (what re-marks activity) is what these tests pin, not the constant.
    monkeypatch.setattr(multi_conn, "_STALL_TIMEOUT_PHASES", 2,
                        raising=False)


@pytest.fixture
def harness(monkeypatch):
    negotiator = multi_conn.get_mptcp_negotiator()
    state = {"cap": MptcpCapabilityState.UNVERIFIABLE}
    monkeypatch.setattr(multi_conn, "_guard_url", lambda url: (True, ""))
    monkeypatch.setattr(
        negotiator, "check_capability",
        lambda: MptcpCapabilityResult(
            state=state["cap"], reason="test", kernel_mptcp_enabled=None),
    )
    release = threading.Event()
    yield state, release
    release.set()  # let any hung worker thread exit


def _writer(payload, *, hang_index=None, release=None, slow_index=None,
            hung=None):
    """Fake _download_chunk that writes the real bytes for each chunk."""

    def fake(client, url, chunk, output_path, *, headers, on_progress,
             chunk_retries, cancel_event=None, on_activity=None):
        if chunk.index == hang_index:
            # Hangs (no progress, no return) until cancelled or released.
            # A worker that was never cancelled then retries and writes its
            # range late, as the real _download_chunk does.
            hung["cancel_event"] = cancel_event
            while not (cancel_event.is_set() or release.is_set()):
                release.wait(0.05)
            if not cancel_event.is_set():
                with open(output_path, "r+b") as f:
                    f.seek(chunk.start)
                    f.write(b"Z" * chunk.length)
            hung["exited"].set()
            return False, 0, "released"
        data = payload[chunk.start:chunk.start + chunk.length]
        step = 1 if chunk.index == slow_index else len(data)
        written = 0
        with open(output_path, "r+b") as f:
            while written < len(data):
                if chunk.index == slow_index:
                    release.wait(0.4)  # slow but steadily progressing
                piece = data[written:written + step]
                f.seek(chunk.start + written)
                f.write(piece)
                written += len(piece)
                on_progress(chunk.index, written, len(piece))
        return True, written, ""

    return fake


def _run(budget_s=BUDGET_S, **kwargs):
    box = {}
    t = threading.Thread(
        target=lambda: box.setdefault("res", multi_conn.download(**kwargs)),
        daemon=True,
    )
    t.start()
    t.join(budget_s)
    return box.get("res")


@pytest.mark.parametrize("cap", [
    MptcpCapabilityState.UNVERIFIABLE,
    MptcpCapabilityState.SUPPORTED,
])
def test_hung_chunk_fails_download_within_budget(harness, small_window,
                                                 monkeypatch, tmp_path, cap):
    state, release = harness
    state["cap"] = cap
    payload = bytes(range(256)) * 4
    out = tmp_path / "f.bin"
    hung = {"exited": threading.Event()}
    monkeypatch.setattr(multi_conn, "_download_chunk",
                        _writer(payload, hang_index=1, release=release,
                                hung=hung))

    res = _run(url="https://example.com/f.bin", output_path=str(out),
               content_length=len(payload), chunk_count=2, **FAST)

    assert res is not None, (
        f"download() did not return within {BUDGET_S}s: hung chunk "
        "thread blocks the unbounded join (O1826 M124)")
    cancelled_at_return = hung["cancel_event"].is_set()
    assert res.ok is False
    assert res.chunks_failed == 1 and res.chunks_completed == 1
    assert "1=chunk_1_stalled" in res.error, res.error

    # The caller's single-conn fallback writes a fresh file; the stalled
    # worker, once it wakes, must not write into it.
    fresh = b"F" * len(payload)
    out.write_bytes(fresh)
    release.set()
    assert hung["exited"].wait(BUDGET_S)
    assert out.read_bytes() == fresh, (
        "late stalled worker overwrote the fallback's file: cancel_event "
        "not set on the stall exit (O1826 N1)")
    assert cancelled_at_return, (
        "cancel_event not set when download() returned (O1826 N1)")

    if cap == MptcpCapabilityState.SUPPORTED:
        # O1849: the telemetry entry is closed on the stall exit too.
        negotiator = multi_conn.get_mptcp_negotiator()
        assert res.mptcp_conn_id
        assert negotiator.get_connection_stats(res.mptcp_conn_id) is None, (
            "MPTCP entry left open after a stalled return (O1826 N2)")


def test_stall_window_charges_the_worker_client_timeout():
    # Chunk workers build httpx.Client(timeout=timeout_s): connect, read
    # and write are each bounded by timeout_s, never connect_timeout_s.
    assert multi_conn._join_stall_window_s(30.0) == (
        multi_conn._STALL_TIMEOUT_PHASES * 30.0
        + multi_conn._JOIN_STALL_SLACK_S)
    assert multi_conn._STALL_TIMEOUT_PHASES >= 12


def test_retry_after_a_full_timeout_attempt_is_not_stalled(
        harness, small_window, monkeypatch, tmp_path):
    # Real _download_chunk, chunk_retries=1 (O1826 D10 O1/ASK 3). Attempt 1
    # stays silent for most of the window, then times out; attempt 2 stays
    # silent 1 s more before its bytes. Together they exceed the window, so
    # only a fresh window per attempt keeps this healthy retry alive.
    payload = bytes((i * 13) % 256 for i in range(2048))
    window = multi_conn._join_stall_window_s(FAST["timeout_s"])
    attempts = {}
    lock = threading.Lock()

    class Resp:
        status_code = 206

        def __init__(self, start, end):
            self.headers = {
                "content-range": f"bytes {start}-{end}/{len(payload)}"}
            self._data = payload[start:end + 1]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_bytes(self, _buffer_size):
            yield self._data

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def stream(self, _method, _url, *, headers):
            start, end = (int(v) for v in
                          headers["Range"].split("=")[1].split("-"))
            with lock:
                attempts[start] = attempts.get(start, 0) + 1
                n = attempts[start]
            if n == 1:
                time.sleep(window - 0.5)
                raise httpx.ReadTimeout("timed out")
            time.sleep(1.0)
            return Resp(start, end)

    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setattr(ssrf_transport, "guarded_transport",
                        lambda *a, **k: None)
    out = tmp_path / "retry.bin"

    res = _run(url="https://example.com/retry.bin", output_path=str(out),
               content_length=len(payload), chunk_count=2,
               **dict(FAST, chunk_retries=1))

    assert res is not None and res.ok is True, (
        f"healthy retry judged stalled: {res!r} (O1826 D10 O1)")
    assert attempts == {0: 2, 1024: 2}, attempts
    assert out.read_bytes() == payload


def test_normal_multi_chunk_download_completes_and_verifies(
        harness, monkeypatch, tmp_path):
    payload = bytes((i * 7) % 256 for i in range(4096))
    out = tmp_path / "ok.bin"
    monkeypatch.setattr(multi_conn, "_download_chunk", _writer(payload))

    res = _run(url="https://example.com/ok.bin", output_path=str(out),
               content_length=len(payload), chunk_count=4, **FAST)

    assert res is not None and res.ok is True, res
    assert res.chunks_completed == 4 and res.chunks_failed == 0
    assert out.read_bytes() == payload


def test_slow_progressing_chunk_is_not_cut_off(harness, small_window,
                                               monkeypatch, tmp_path):
    # Runs ~8 x 0.4 s, longer than the stall window, but never idles past
    # it: the bound is on lack of progress, not on total duration.
    _state, release = harness
    payload = b"abcdefghijklmnop"
    out = tmp_path / "slow.bin"
    monkeypatch.setattr(multi_conn, "_download_chunk",
                        _writer(payload, slow_index=1, release=release))

    res = _run(url="https://example.com/slow.bin", output_path=str(out),
               content_length=len(payload), chunk_count=2, **FAST)

    assert res is not None and res.ok is True, res
    assert res.elapsed_s > multi_conn._join_stall_window_s(FAST["timeout_s"])
    assert out.read_bytes() == payload


@pytest.mark.parametrize("url,remote", [
    ("https://example.com/a.bin", ("example.com", 443)),
    ("http://example.com/a.bin", ("example.com", 80)),
    ("https://example.com:8443/a.bin", ("example.com", 8443)),
])
def test_mptcp_entry_uses_real_port(harness, monkeypatch, tmp_path, url,
                                    remote):
    state, _release = harness
    state["cap"] = MptcpCapabilityState.SUPPORTED
    seen = []
    negotiator = multi_conn.get_mptcp_negotiator()
    real_register = negotiator.register_connection
    real_add = negotiator.add_subflow

    def register(conn_id, local_addr, remote_addr, token=None):
        seen.append(remote_addr)
        return real_register(conn_id, local_addr, remote_addr, token)

    def add(conn_id, local_addr, remote_addr, is_backup=False):
        seen.append(remote_addr)
        return real_add(conn_id, local_addr, remote_addr, is_backup)

    monkeypatch.setattr(negotiator, "register_connection", register)
    monkeypatch.setattr(negotiator, "add_subflow", add)
    payload = b"0123456789"
    monkeypatch.setattr(multi_conn, "_download_chunk", _writer(payload))

    res = _run(url=url, output_path=str(tmp_path / "m.bin"),
               content_length=len(payload), chunk_count=2, **FAST)

    assert res is not None and res.ok is True, res
    assert seen and set(seen) == {remote}, seen
    # O1849: the process-wide telemetry entry is closed on exit.
    assert negotiator.get_connection_stats(res.mptcp_conn_id) is None


# Real httpx against a local server (O1826 D10 R1/ASK 2): slow redirect
# hops, slow headers, then a trickled body. Every wait is under timeout_s,
# so no httpx timeout fires, but the hops and the body each take longer
# than the stall window. Each single wait is longer than a window built
# from connect_timeout_s (2 * 0.1 + 1 s), which the workers never use.
SLOW = dict(timeout_s=3.0, connect_timeout_s=0.1, chunk_retries=0)
WAIT_S = 1.8
HOPS = 3
PIECES = 5


class _SlowHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    payload = b""

    def log_message(self, *args):
        pass

    def do_GET(self):
        time.sleep(WAIT_S)
        hop = int(self.path.rsplit("/", 1)[1])
        if hop < HOPS:
            self.send_response(302)
            self.send_header("Location", f"/hop/{hop + 1}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = (int(v) for v in
                      self.headers["Range"].split("=")[1].split("-"))
        data = self.payload[start:end + 1]
        self.send_response(206)
        self.send_header("Content-Range",
                         f"bytes {start}-{end}/{len(self.payload)}")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.flush()
        step = -(-len(data) // PIECES)
        for i in range(0, len(data), step):
            time.sleep(WAIT_S)
            self.wfile.write(data[i:i + step])
            self.wfile.flush()


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


@pytest.mark.parametrize("phases", [None, 2], ids=["shipped", "small"])
def test_slow_but_live_real_socket_is_not_stalled(harness, monkeypatch,
                                                  tmp_path, phases):
    if phases is not None:
        # Window 2 * 3 + 1 = 7 s: shorter than the hops (7.2 s to the
        # 206 headers) and than the body (9 s), so this only passes if
        # every response and every raw socket read re-marks activity.
        monkeypatch.setattr(multi_conn, "_STALL_TIMEOUT_PHASES", phases,
                            raising=False)
    payload = bytes((i * 31) % 251 for i in range(8192))
    handler = type("Handler", (_SlowHandler,), {"payload": payload})
    srv = _Server(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(ssrf_transport, "guarded_transport",
                        lambda *a, proxy=None, **k: httpx.HTTPTransport())
    monkeypatch.setattr(multi_conn, "_redirect_guard_hook", lambda r: None)
    out = tmp_path / "live.bin"
    try:
        res = _run(budget_s=60.0,
                   url=f"http://127.0.0.1:{srv.server_address[1]}/hop/0",
                   output_path=str(out), content_length=len(payload),
                   chunk_count=2, **SLOW)
    finally:
        srv.shutdown()
        srv.server_close()

    assert res is not None, "download() did not return within 60 s"
    assert res.ok is True, (
        f"slow but live transfer judged stalled: {res.error!r} "
        "(O1826 D10 R1)")
    assert res.chunks_completed == 2 and res.chunks_failed == 0
    assert out.read_bytes() == payload
