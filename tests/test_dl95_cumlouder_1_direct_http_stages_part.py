"""dl95-cumlouder-1 (harness-work/DOT95-LANE/live-dl-f3/e2e-cumlouder.log, landing2; DOT95-LANE/LOG.md B4).

A service restart on test2 at 23:18:50Z killed a direct_http transfer at ~72% and left a truncated 95.4 MB .mp4 under
its FINAL name -- no .part, no history row -- because _do_direct_http_download streamed straight into output_path.
Any file at the final name looks finished to the library, dedupe and the next run.

A restart cannot be caught, so the property is asserted where the kill lands: DURING the transfer. The stub stream
looks at the filesystem between chunks. The final name must hold no bytes while bytes are in flight. It must receive
the file only by the promotion after success. Every failure must leave the final name untouched and drop the .part
and its claim. Drives the real _do_direct_http_download and the real staging_claim; only the network is stubbed.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import contextlib
import threading

import httpx
import pytest

from bulk_downloader import runner_transport as rt
from bulk_downloader import ssrf_transport, staging_claim
from bulk_downloader.runner_transport import TransportMixin

PAGE = "https://example.org/scene/1"
FILE = "https://cdn.example.org/scene-1.mp4"
CHUNKS = [b"a" * 65536, b"b" * 65536, b"c" * 65536]  # > the 8 KiB write buffer, so staged bytes are visible mid-transfer


class _Response:
    status_code = 200

    def __init__(self, chunks, during, total=None):
        size = sum(len(c) for c in chunks) if total is None else total
        self.headers = {"content-length": str(size)}
        self.chunks = chunks
        self.during = during

    def iter_bytes(self, _size):
        for i, c in enumerate(self.chunks):
            if i:
                self.during(i)
            yield c


class _Runner(TransportMixin):
    site_id = "dl95"

    def __init__(self):
        self.config = {}
        self._stop = threading.Event()

    def _download_proxy_url(self):
        return None

    def _regional_gateway_router(self):
        return None

    def _update_job(self, *args, **kwargs):
        pass


@pytest.fixture
def net(monkeypatch):
    box = {}
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: None)
    monkeypatch.setattr(ssrf_transport, "owning_stream", lambda *a, **k: contextlib.nullcontext(box["r"]))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: object())
    return box


def _residue(out):
    stage = staging_claim.staging_path_for(out)
    return [p.name for p in (stage, staging_claim.owner_path_for(stage)) if p.exists()]


def test_final_name_holds_no_bytes_while_the_transfer_is_in_flight(net, tmp_path):
    out = tmp_path / "scene.mp4"
    seen = []

    def during(i):
        stage = staging_claim.staging_path_for(out)
        seen.append((i, out.exists(), stage.exists() and stage.stat().st_size))

    net["r"] = _Response(CHUNKS, during)
    assert _Runner()._do_direct_http_download(PAGE, FILE, str(out))
    # Positive control: the probe ran mid-transfer (and, below, saw the bytes staged under the .part).
    assert [s[0] for s in seen] == [1, 2], seen
    assert not any(exists for _i, exists, _s in seen), (
        f"dl95-cumlouder-1: partial bytes sat under the FINAL name mid-transfer (a restart here leaves a short file): {seen}")
    assert all(staged for _i, _exists, staged in seen), f"no staged bytes observed mid-transfer: {seen}"
    assert out.read_bytes() == b"".join(CHUNKS)
    assert _residue(out) == []


def test_stop_mid_transfer_leaves_no_final_file_and_no_claim(net, tmp_path):
    out = tmp_path / "scene.mp4"
    runner = _Runner()
    net["r"] = _Response(CHUNKS, lambda i: runner._stop.set())
    assert runner._do_direct_http_download(PAGE, FILE, str(out)) is False
    assert not out.exists(), f"dl95-cumlouder-1: stopped transfer left {out.stat().st_size} bytes under the final name"
    assert _residue(out) == []


def test_short_body_never_reaches_the_final_name(net, tmp_path):
    out = tmp_path / "scene.mp4"
    net["r"] = _Response(CHUNKS[:1], lambda i: None, total=3 * 65536)
    assert _Runner()._do_direct_http_download(PAGE, FILE, str(out)) is False
    assert not out.exists()
    assert _residue(out) == []


def test_failed_retry_keeps_the_previous_complete_file(net, tmp_path):
    out = tmp_path / "scene.mp4"
    net["r"] = _Response(CHUNKS, lambda i: None)
    assert _Runner()._do_direct_http_download(PAGE, FILE, str(out))
    net["r"] = _Response(CHUNKS[:1], lambda i: None, total=3 * 65536)
    assert _Runner()._do_direct_http_download(PAGE, FILE, str(out)) is False
    assert out.read_bytes() == b"".join(CHUNKS), "a failed re-download truncated the complete file"


def test_a_claim_held_by_another_job_refuses_without_writing(net, tmp_path):
    out = tmp_path / "scene.mp4"
    other = staging_claim.job_identity("https://example.org/scene/OTHER")
    held = staging_claim.claim(str(out), other, resource_url=FILE)
    net["r"] = _Response(CHUNKS, lambda i: None)
    try:
        assert _Runner()._do_direct_http_download(PAGE, FILE, str(out)) is False
        assert not out.exists()
        assert staging_claim.owner_path_for(held).exists(), "the other job's claim was dropped"
    finally:
        staging_claim.release(held, other)


def test_multi_conn_writes_into_the_staging_path(net, tmp_path, monkeypatch):
    out = tmp_path / "scene.mp4"
    got = {}

    def fake_multi(self, page_url, file_url, output_path, *, headers, proxy_url=None):
        got["path"] = output_path
        with open(output_path, "wb") as f:
            f.write(b"m" * 4096)
        got["final_during"] = out.exists()
        return True

    monkeypatch.setattr(rt, "_MULTI_CONN_AVAILABLE", True)
    monkeypatch.setattr(rt, "_mconn", object())
    monkeypatch.setattr(_Runner, "_try_multi_conn_download", fake_multi)
    runner = _Runner()
    runner.config["use_multi_conn"] = True
    assert runner._do_direct_http_download(PAGE, FILE, str(out))
    assert got["path"] != str(out) and got["final_during"] is False, got
    assert out.read_bytes() == b"m" * 4096
    assert _residue(out) == []
