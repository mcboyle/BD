"""Row 857: header-stage perceptual video dedup.

Before a media transfer commits to the full download, the first
``dedup_header_bytes`` of the stream are piped through ffmpeg, up to three
keyframes are aHashed (8x8 grayscale) and the combined 64-bit hash is looked
up in the local ``header_hashes`` Hamming index. A match rejects the transfer
mid-stream with a structured log; a miss registers the destination path.

Fixtures are synthetic (lavfi testsrc/testsrc2, +faststart) so no site and no
login is touched (Fleet Rule 21). The pipeline tests drive the unmodified
``TransportMixin._http_download`` against a local origin, the same shape as
tests/test_part_staging_collision.py.
"""
import logging
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BD_GATE_SCOPE = "module"

os.environ.setdefault("BD_HOME", tempfile.mkdtemp(prefix="bd_vdd_"))
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

HEADER_BYTES = 64 * 1024


def _load():
    import sys
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from bulk_downloader import dedup as mod
    return mod


def _ffmpeg():
    from bulk_downloader import ffmpeg_bin
    exe = ffmpeg_bin.ffmpeg()
    if not exe:
        pytest.skip("no local ffmpeg binary")
    return exe


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    """a = original, b = re-encode of a (crf 30, different bitrate), c = unique."""
    exe = _ffmpeg()
    d = tmp_path_factory.mktemp("clips")
    a, b, c = d / "a.mp4", d / "b.mp4", d / "c.mp4"
    x264 = ["-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    subprocess.run([exe, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=duration=8:size=320x240:rate=25", "-crf", "18", *x264, str(a)],
                   check=True, capture_output=True)
    subprocess.run([exe, "-y", "-v", "error", "-i", str(a), "-crf", "30", *x264, str(b)], check=True, capture_output=True)
    subprocess.run([exe, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=duration=8:size=320x240:rate=25", "-crf", "18", *x264, str(c)],
                   check=True, capture_output=True)
    for p in (a, b, c):
        assert p.stat().st_size > HEADER_BYTES, p
    return {"a": a.read_bytes(), "b": b.read_bytes(), "c": c.read_bytes()}


# ── pure half: hashing ──────────────────────────────────────────────────────

def test_header_hash_from_prefix_is_16_hex_with_keyframes(clips):
    m = _load()
    r = m.compute_header_hash(clips["a"][:HEADER_BYTES])
    assert r.ok, r.error
    assert len(r.hash_hex) == 16 and int(r.hash_hex, 16) >= 0
    assert 1 <= r.frames <= 3
    assert r.bytes_sampled == HEADER_BYTES


def test_reencoded_duplicate_is_within_distance_and_unique_is_not(clips):
    m = _load()
    ha = m.compute_header_hash(clips["a"][:HEADER_BYTES]).hash_hex
    hb = m.compute_header_hash(clips["b"][:HEADER_BYTES]).hash_hex
    hc = m.compute_header_hash(clips["c"][:HEADER_BYTES]).hash_hex
    assert m.hamming_distance(ha, hb) <= m.HEADER_DISTANCE
    assert m.hamming_distance(ha, hc) > m.HEADER_DISTANCE
    assert m.hamming_distance(hb, hc) > m.HEADER_DISTANCE


def test_undecodable_prefix_fails_open_not_reject():
    m = _load()
    r = m.compute_header_hash(b"\x00" * 4096)
    assert r.ok is False and r.frames == 0 and r.hash_hex == ""


def test_combine_two_frames_keeps_both_frames():
    # E5 (prior cut): with n=2 every differing bit tied and frame 2 was
    # discarded. Ties must resolve deterministically without collapsing to
    # frame 1: swapping the two frames must give the same combined hash.
    m = _load()
    f1, f2 = "0000000000000000", "ffffffffffffffff"
    assert m._combine_frame_hashes([f1, f2]) == m._combine_frame_hashes([f2, f1])
    assert m._combine_frame_hashes([f1, f2]) != f1
    assert m._combine_frame_hashes(["00000000000000ff"]) == "00000000000000ff"
    # 3 frames: strict majority per bit.
    assert m._combine_frame_hashes(["000000000000000f", "000000000000000f",
                                    "00000000000000f0"]) == "000000000000000f"


# ── registry: header index ─────────────────────────────────────────────────

def test_registry_header_index_round_trip_and_distance(tmp_path):
    m = _load()
    reg = m.HashRegistry(str(tmp_path / "h.db"))
    dest = tmp_path / "x.mp4"
    dest.write_bytes(b"x")
    assert reg.add_header(str(dest), "3333333333333333", frames=3, source_url="u")
    hits = reg.find_header_duplicates("3333333333333331", distance=4)
    assert [h["path"] for h in hits] == [str(dest)] and hits[0]["distance"] == 1
    assert reg.find_header_duplicates("0f4d4d4d4d4df00f", distance=4) == []
    assert reg.find_header_duplicates("3333333333333333", exclude_path=str(dest)) == []


def test_registry_header_index_ignores_paths_that_no_longer_exist(tmp_path):
    # E4 (prior cut): the index must map canonical destination paths, and a
    # destination that never materialised (failed/aborted transfer) must not
    # reject a later download as a duplicate of nothing.
    m = _load()
    reg = m.HashRegistry(str(tmp_path / "h.db"))
    ghost = str(tmp_path / "never-written.mp4")
    assert reg.add_header(ghost, "3333333333333333", frames=2)
    assert reg.find_header_duplicates("3333333333333333") == []


# ── preflight decision + structured rejection log ──────────────────────────

def test_preflight_rejects_reencoded_duplicate_with_structured_log(tmp_path, clips, caplog):
    m = _load()
    reg = m.HashRegistry(str(tmp_path / "h.db"))
    a_dest = tmp_path / "a.mp4"
    a_dest.write_bytes(clips["a"])
    assert m.preflight_header_reject(clips["a"][:HEADER_BYTES], registry=reg,
                                     final_path=str(a_dest), source_url="https://s/a") is None
    b_dest = tmp_path / "b.mp4"
    with caplog.at_level(logging.INFO, logger="bulk_downloader.dedup"):
        rec = m.preflight_header_reject(clips["b"][:HEADER_BYTES], registry=reg,
                                        final_path=str(b_dest), source_url="https://s/b")
    assert rec is not None
    assert rec.duplicate_of == str(a_dest)
    assert rec.distance <= m.HEADER_DISTANCE
    assert rec.final_path == str(b_dest) and rec.source_url == "https://s/b"
    assert rec.bytes_sampled == HEADER_BYTES and rec.frames >= 1
    line = [r for r in caplog.records if r.getMessage().startswith("dedup_header_reject")]
    assert len(line) == 1
    msg = line[0].getMessage()
    assert f"duplicate_of={a_dest}" in msg and f"distance={rec.distance}" in msg
    assert "url=https://s/b" in msg and f"hash={rec.hash_hex}" in msg
    assert reg.find_header_duplicates(rec.hash_hex, exclude_path=str(a_dest)) == [], \
        "a rejected transfer must not be registered"


def test_preflight_registers_unique_content_and_passes(tmp_path, clips):
    m = _load()
    reg = m.HashRegistry(str(tmp_path / "h.db"))
    a_dest, c_dest = tmp_path / "a.mp4", tmp_path / "c.mp4"
    a_dest.write_bytes(clips["a"])
    c_dest.write_bytes(clips["c"])
    assert m.preflight_header_reject(clips["a"][:HEADER_BYTES], registry=reg,
                                     final_path=str(a_dest)) is None
    assert m.preflight_header_reject(clips["c"][:HEADER_BYTES], registry=reg,
                                     final_path=str(c_dest)) is None
    assert {r["path"] for r in reg.find_header_duplicates(
        m.compute_header_hash(clips["c"][:HEADER_BYTES]).hash_hex)} == {str(c_dest)}


def test_preflight_fails_open_on_undecodable_prefix(tmp_path):
    m = _load()
    reg = m.HashRegistry(str(tmp_path / "h.db"))
    assert m.preflight_header_reject(b"\x00" * 4096, registry=reg,
                                     final_path=str(tmp_path / "z.mp4")) is None
    assert reg.header_stats()["count"] == 0


# ── pipeline: the transfer itself rejects before the full download ─────────

class _Origin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bodies: dict = {}
    holds: dict = {}
    served: dict = {}
    lock = threading.Lock()

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = self.bodies.get(path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        hold = self.holds.get(path)
        if hold is None:
            self.wfile.write(body)
            with self.lock:
                self.served[path] = len(body)
            return
        after, event = hold
        self.wfile.write(body[:after])
        self.wfile.flush()
        with self.lock:
            self.served[path] = after
        event.wait(timeout=60)
        try:
            self.wfile.write(body[after:])
            with self.lock:
                self.served[path] = len(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _fmt, *_args):
        return


class _Ctx:
    def cookies(self):
        return []


def _harness(site_id, db_path):
    from bulk_downloader.runner_transport import TransportMixin

    class _H(TransportMixin):
        def __init__(self):
            self.site_id = site_id
            self.config = {
                "use_curl_cffi": False,
                "chunk_size_mb": 0,  # clamps to the 64 KiB floor == HEADER_BYTES
                "auto_chunk_size": False,
                "parallel_chunks": 1,
                "use_ramdisk_stage": False,
                "max_mbps": 0,
                "dedup_header_bytes": HEADER_BYTES,
                "dedup_db_path": db_path,
            }
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()
            self._lock = threading.RLock()
            self.jobs = {}
            self.log = logging.getLogger("video-dedup-pipeline")
            self._throughput_samples = 0
            self._throughput_ewma_bps = 0.0
            self.events = []
            self.job_updates = []

        def log_event(self, kind, message, url=None, extra=None):
            self.events.append((kind, message, url, extra or {}))

        def _update_job(self, url, status, message, **extra):
            self.job_updates.append((url, status, message))

        def _pick_fastest_mirror(self, file_url):
            return file_url

        def _start_daily_byte_accumulator(self):
            return None

        def _finish_daily_byte_accumulator(self, accumulator):
            return None

    return _H()


@pytest.fixture(scope="module")
def origin(clips):
    hold_b = threading.Event()
    handler = type("_DedupOrigin", (_Origin,), {
        "bodies": {"/a.mp4": clips["a"], "/b.mp4": clips["b"], "/c.mp4": clips["c"]},
        "holds": {"/b.mp4": (HEADER_BYTES, hold_b)},
        "served": {}, "lock": threading.Lock(),
    })
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base": f"http://127.0.0.1:{srv.server_address[1]}", "handler": handler,
               "hold_b": hold_b}
    finally:
        hold_b.set()
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)


@pytest.fixture(scope="module")
def pipeline_run(origin, clips, tmp_path_factory):
    """Download a (unique, registers), then b (re-encode of a, held at the
    header boundary by the origin), then c (unique). Measured, not asserted."""
    from bulk_downloader.detect import safe_dest
    dl = tmp_path_factory.mktemp("dl")
    db = str(dl / "video_hashes.db")
    out = {"dl": dl}

    final_a = safe_dest(dl / "Scene A [1080p].mp4")
    h_a = _harness("site-a", db)
    out["a_result"] = h_a._http_download("https://example.invalid/a", None, _Ctx(),
                                         origin["base"] + "/a.mp4", final_a)
    out["final_a"] = final_a
    out["a_events"] = h_a.events

    final_b = safe_dest(dl / "Scene A again [360p].mp4")
    h_b = _harness("site-b", db)
    b_box = {}

    def _run_b():
        try:
            b_box["result"] = h_b._http_download("https://example.invalid/b", None, _Ctx(),
                                                 origin["base"] + "/b.mp4", final_b)
        except BaseException as exc:  # noqa: BLE001 - recorded, judged below
            b_box["exc"] = exc

    t = threading.Thread(target=_run_b, daemon=True)
    t.start()
    t.join(timeout=60)
    out["b_finished_while_origin_held"] = not t.is_alive()
    with origin["handler"].lock:
        out["b_served_at_decision"] = origin["handler"].served.get("/b.mp4")
    origin["hold_b"].set()
    t.join(timeout=60)
    out["b_box"] = b_box
    out["final_b"] = final_b
    out["b_part"] = Path(str(final_b) + ".part")
    out["b_events"] = h_b.events
    out["b_harness"] = h_b

    final_c = safe_dest(dl / "Scene C [1080p].mp4")
    h_c = _harness("site-c", db)
    out["c_result"] = h_c._http_download("https://example.invalid/c", None, _Ctx(),
                                         origin["base"] + "/c.mp4", final_c)
    out["final_c"] = final_c
    out["db"] = db
    return out


def test_pipeline_duplicate_is_rejected_before_the_full_download(pipeline_run):
    r = pipeline_run
    assert r["final_a"].exists() and r["final_a"].stat().st_size == r["a_result"][0]
    # The decision landed while the origin was still holding the remainder:
    # only the header bytes had been served when the transfer gave up.
    assert r["b_finished_while_origin_held"], "transfer b waited for the whole body"
    assert r["b_served_at_decision"] == HEADER_BYTES
    assert not r["final_b"].exists(), "duplicate was promoted to its final path"
    assert not r["b_part"].exists(), "duplicate left its .part behind"
    assert "result" not in r["b_box"], r["b_box"]


def test_pipeline_rejection_is_the_dedup_exception_with_a_record(pipeline_run):
    m = _load()
    exc = pipeline_run["b_box"].get("exc")
    assert isinstance(exc, m.HeaderDuplicateRejected), repr(exc)
    rec = exc.record
    assert rec.duplicate_of == str(pipeline_run["final_a"])
    assert rec.final_path == str(pipeline_run["final_b"])
    assert rec.source_url == "https://example.invalid/b"
    assert rec.bytes_sampled == HEADER_BYTES


def test_pipeline_rejection_emits_a_structured_runner_event(pipeline_run):
    kinds = [e[0] for e in pipeline_run["b_events"]]
    assert kinds.count("dedup_header_reject") == 1
    ev = [e for e in pipeline_run["b_events"] if e[0] == "dedup_header_reject"][0]
    assert ev[2] == "https://example.invalid/b"
    assert ev[3]["duplicate_of"] == str(pipeline_run["final_a"])
    assert ev[3]["bytes_sampled"] == HEADER_BYTES and "distance" in ev[3] and "hash" in ev[3]
    assert not [e for e in pipeline_run["a_events"] if e[0] == "dedup_header_reject"]


def test_pipeline_unique_content_persists_and_is_indexed(pipeline_run, clips):
    m = _load()
    r = pipeline_run
    assert r["final_c"].exists() and r["final_c"].read_bytes() == clips["c"]
    reg = m.HashRegistry(r["db"])
    paths = {row["path"] for row in reg.header_rows()}
    assert paths == {str(r["final_a"]), str(r["final_c"])}


def test_pipeline_force_download_bypasses_the_header_gate(origin, clips, tmp_path):
    from bulk_downloader.detect import safe_dest
    m = _load()
    db = str(tmp_path / "h.db")
    reg = m.HashRegistry(db)
    a_dest = tmp_path / "a.mp4"
    a_dest.write_bytes(clips["a"])
    reg.add_header(str(a_dest), m.compute_header_hash(clips["a"][:HEADER_BYTES]).hash_hex, frames=2)
    final_c2 = safe_dest(tmp_path / "forced.mp4")
    h = _harness("site-f", db)
    h.jobs["https://example.invalid/forced"] = {"force_download": True}
    size, _ = h._http_download("https://example.invalid/forced", None, _Ctx(),
                               origin["base"] + "/a.mp4", final_c2)
    assert final_c2.exists() and size == len(clips["a"])
    assert not [e for e in h.events if e[0] == "dedup_header_reject"]


def test_pipeline_gate_can_be_disabled_by_config(origin, clips, tmp_path):
    from bulk_downloader.detect import safe_dest
    m = _load()
    db = str(tmp_path / "h.db")
    reg = m.HashRegistry(db)
    a_dest = tmp_path / "a.mp4"
    a_dest.write_bytes(clips["a"])
    reg.add_header(str(a_dest), m.compute_header_hash(clips["a"][:HEADER_BYTES]).hash_hex, frames=2)
    final = safe_dest(tmp_path / "off.mp4")
    h = _harness("site-off", db)
    h.config["dedup_header_preflight"] = False
    size, _ = h._http_download("https://example.invalid/off", None, _Ctx(),
                               origin["base"] + "/a.mp4", final)
    assert final.exists() and size == len(clips["a"])
