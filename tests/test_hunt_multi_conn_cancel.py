BD_GATE_SCOPE = "module"

import sys
import threading
import types

from bulk_downloader import multi_conn


def test_cancel_stops_writing_active_chunk(monkeypatch, tmp_path):
    stop = threading.Event()
    observed = threading.Event()
    cancel_set = threading.Event()

    class SignalEvent(threading.Event):
        def set(self):
            super().set()
            cancel_set.set()
    network_deltas = []

    class Response:
        status_code = 206

        def __init__(self):
            self.headers = {"content-range": "bytes 0-3/4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, _buffer_size):
            yield b"ab"
            assert observed.wait(2), "cancellation watcher did not observe stop"
            assert cancel_set.wait(2), "watcher did not set the cancel event"
            yield b"cd"

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, _method, _url, *, headers):
            assert headers["Range"] == "bytes=0-3"
            return Response()

    def cancel_check():
        if stop.is_set():
            observed.set()
            return True
        return False

    def count_bytes(delta):
        network_deltas.append(delta)
        stop.set()

    monkeypatch.setitem(sys.modules, "httpx", type("Httpx", (), {"Client": Client}))
    monkeypatch.setattr(multi_conn, "threading", types.SimpleNamespace(
        Event=SignalEvent, Lock=threading.Lock, Thread=threading.Thread))
    monkeypatch.setattr(multi_conn, "is_available", lambda: True)
    monkeypatch.setattr(multi_conn, "_guard_url", lambda _url: (True, ""))
    path = tmp_path / "cancelled.mp4"

    result = multi_conn.download(
        "https://cdn.test/f", str(path), content_length=4,
        chunk_count=1, cancel_check=cancel_check,
        bytes_callback=count_bytes, chunk_retries=0,
    )

    assert result.error == "cancelled"
    assert result.bytes_written == 2
    assert path.read_bytes() == b"ab\x00\x00"
    assert network_deltas == [2]
