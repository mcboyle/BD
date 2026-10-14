"""multi_conn chunk bounds (bughunt download-engine FINDINGS #1, #10).

#1: a 206 whose header names the right range but whose body runs past it was
written in full before the length check, so the surplus landed in the next
chunk's region. The retry rewrote only its own chunk; when the neighbour had
already finished, download() returned ok=True over a corrupt file.

#10: plan_chunks(content_length < chunk_count) planned end=-1 chunks
(Range "bytes=0--1") instead of fewer, non-empty chunks.
"""

import hashlib
import threading
import time

import httpx

from bulk_downloader import multi_conn, ssrf_transport
from bulk_downloader.multi_conn import ChunkPlan, _download_chunk, plan_chunks

BD_GATE_SCOPE = "module"

BODY = bytes((i * 7 + 3) % 251 for i in range(4096))


class _Response:
    def __init__(self, content_range, body):
        self.status_code = 206
        self.headers = {"content-range": content_range}
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self, _buffer_size):
        yield self._body


class _Client:
    def __init__(self, bodies):
        self.bodies = list(bodies)

    def stream(self, _method, _url, *, headers):
        assert headers["Range"] == "bytes=0-3"
        return _Response("bytes 0-3/8", self.bodies.pop(0))


def test_overlong_206_body_never_writes_past_its_chunk(tmp_path):
    path = tmp_path / "output"
    path.write_bytes(b"....NEXT")
    ok, written, err = _download_chunk(
        _Client([b"ABCDspill"]), "https://cdn.test/f", ChunkPlan(0, 0, 3),
        str(path), headers={}, on_progress=lambda *_a: None, chunk_retries=0,
    )
    assert path.read_bytes()[4:] == b"NEXT", "over-long body overwrote the next chunk"
    assert ok is False and "overlong" in err, (ok, written, err)


def test_overlong_206_attempt_is_retried_clean(tmp_path):
    path = tmp_path / "output"
    path.write_bytes(b"....NEXT")
    ok, written, err = _download_chunk(
        _Client([b"ABCDspill", b"abcd"]), "https://cdn.test/f", ChunkPlan(0, 0, 3),
        str(path), headers={}, on_progress=lambda *_a: None, chunk_retries=1,
    )
    assert (ok, written, err) == (True, 4, "")
    assert path.read_bytes() == b"abcdNEXT"


def _mock_download(monkeypatch, tmp_path, handler, content_length, chunk_count):
    monkeypatch.setattr(multi_conn, "_guard_url", lambda url: (True, ""))
    monkeypatch.setattr(ssrf_transport, "guarded_transport",
                        lambda *a, **k: httpx.MockTransport(handler))
    out = tmp_path / "f.bin"
    res = multi_conn.download("https://example.test/f.bin", str(out),
                              content_length=content_length, chunk_count=chunk_count)
    return res, out


def test_download_is_not_ok_true_over_a_corrupt_file(monkeypatch, tmp_path):
    """FINDINGS #1 end to end: chunk 0's first answer carries 200 surplus bytes and
    is held until chunk 1's region is on disk, so the spill lands on finished data."""
    out_path = tmp_path / "f.bin"
    first = {"c0": True}
    lock = threading.Lock()

    def handler(req):
        a, b = (int(x) for x in req.headers["range"].split("=")[1].split("-"))
        payload = BODY[a:b + 1]
        if a == 0:
            with lock:
                spill, first["c0"] = first["c0"], False
            if spill:
                deadline = time.monotonic() + 20
                while out_path.read_bytes()[1024:2048] != BODY[1024:2048]:
                    assert time.monotonic() < deadline, "chunk 1 never landed"
                    time.sleep(0.01)
                payload += b"X" * 200
        return httpx.Response(206, headers={"content-range": f"bytes {a}-{b}/{len(BODY)}"},
                              content=payload)

    res, out = _mock_download(monkeypatch, tmp_path, handler, len(BODY), 4)
    got = out.read_bytes()
    bad = [i for i in range(len(BODY)) if got[i] != BODY[i]]
    assert bad == [], f"ok={res.ok} corrupt_bytes={len(bad)} first_bad={bad[0]}"
    assert res.ok is True and hashlib.sha256(got).digest() == hashlib.sha256(BODY).digest()


def test_well_behaved_server_control(monkeypatch, tmp_path):
    def handler(req):
        a, b = (int(x) for x in req.headers["range"].split("=")[1].split("-"))
        return httpx.Response(206, headers={"content-range": f"bytes {a}-{b}/{len(BODY)}"},
                              content=BODY[a:b + 1])

    res, out = _mock_download(monkeypatch, tmp_path, handler, len(BODY), 4)
    assert res.ok is True and out.read_bytes() == BODY


def test_plan_chunks_never_plans_an_empty_range():
    for length in range(1, 20):
        for count in range(1, 20):
            plan = plan_chunks(length, count)
            assert all(c.start <= c.end for c in plan), (length, count, plan)
            assert [(c.start, c.end) for c in plan][0][0] == 0
            assert plan[-1].end == length - 1
            assert all(plan[i].end + 1 == plan[i + 1].start for i in range(len(plan) - 1))
            assert len(plan) == min(length, count, 16)
    assert [(c.start, c.end) for c in plan_chunks(3, 4)] == [(0, 0), (1, 1), (2, 2)]
    assert [(c.start, c.end) for c in plan_chunks(4, 4)] == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_tiny_file_downloads_with_more_chunks_than_bytes(monkeypatch, tmp_path):
    ranges = []

    def handler(req):
        rng = req.headers["range"]
        ranges.append(rng)
        a, b = (int(x) for x in rng.split("=")[1].split("-", 1))
        if b < a:
            return httpx.Response(416, headers={"content-range": "bytes */3"})
        return httpx.Response(206, headers={"content-range": f"bytes {a}-{b}/3"},
                              content=b"xyz"[a:b + 1])

    res, out = _mock_download(monkeypatch, tmp_path, handler, 3, 4)
    assert res.ok is True, (res.error, sorted(ranges))
    assert out.read_bytes() == b"xyz"
    assert sorted(ranges) == ["bytes=0-0", "bytes=1-1", "bytes=2-2"]
