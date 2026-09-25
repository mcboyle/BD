BD_GATE_SCOPE = "module"

from bulk_downloader.multi_conn import ChunkPlan, _download_chunk


def test_chunk_rejects_response_for_a_different_byte_range(tmp_path):
    path = tmp_path / "output"
    path.write_bytes(b"xxxx")

    class Response:
        def __init__(self, status, content_range):
            self.status_code = status
            self.headers = {"content-range": content_range} if content_range else {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, _buffer_size):
            yield b"AB"

    class Client:
        def __init__(self, status, content_range):
            self.status = status
            self.content_range = content_range

        def stream(self, _method, _url, *, headers):
            assert headers["Range"] == "bytes=2-3"
            return Response(self.status, self.content_range)

    def fetch(status, content_range):
        return _download_chunk(
            Client(status, content_range), "https://cdn.test/f",
            ChunkPlan(1, 2, 3), str(path), headers={},
            on_progress=lambda *_args: None, chunk_retries=0,
        )

    assert fetch(206, "bytes 2-3/4")[0] is True
    assert path.read_bytes() == b"xxAB"
    for status, content_range in (
        (206, "bytes 0-1/4"),
        (206, None),
        (200, None),
    ):
        path.write_bytes(b"xxxx")
        assert fetch(status, content_range)[0] is False
        assert path.read_bytes() == b"xxxx"
