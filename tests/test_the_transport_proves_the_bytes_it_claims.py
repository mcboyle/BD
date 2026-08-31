"""The transport must prove the bytes it promotes, counts, and checkpoints.

Three findings on one surface -- ``bulk_downloader/runner_transport.py`` -- and
one contract: a number the transport hands to history, or to the resume
checkpoint, must be a MEASUREMENT of bytes that exist, never an inference from
something else that happened to be the right shape.

ROW 428 -- A 416 PROMOTES THE .part WITHOUT PROVING IT COMPLETE.
On resume, ``416 Range Not Satisfiable`` proves only that ``resume_from`` is not
inside the CURRENT resource.  The branch renamed ``tmp_path`` to ``final_path``
and returned ``(size, 0)`` unconditionally: the 416's ``Content-Range: bytes
*/N`` was never read (the string ``Content-Range`` occurred zero times in the
file) and no validator was required, because ``If-Range`` is sent only when the
optional ``.part.meta`` sidecar survives.  So a ``.part`` that is a partial of an
OLD, larger resource whose URL now serves something smaller was promoted whole:
a truncated stale file became a ``done`` row with ``bytes_fetched=0``.

ROW 430 -- BYTES-FETCHED MEASURED THE DISK, NOT THE WIRE.
``_dl_initial_bytes`` is ``resume_from`` and the returned count was
``final_size - _dl_initial_bytes``, an on-disk size delta.  When a resume is
answered ``200`` (resource changed, If-Range mismatch, or a server that ignores
Range) the loop restarts at ``downloaded=0`` in ``'wb'`` and streams the ENTIRE
new file -- yet the delta still subtracts the abandoned ``.part``.  With a
smaller new file the delta is negative and ``max(0, ...)`` reports
``bytes_fetched=0``: a ``done`` row asserting nothing crossed the wire for a real
transfer, which is exactly what ``db.py``'s contract says 0 means.  A larger new
file is undercounted by ``resume_from``.  The true streamed count was in scope
and discarded.

ROW 431 -- THE CHECKPOINT CLAIMED BYTES STILL IN THE WORKER'S WRITE BUFFER.
Parallel workers write through a buffered handle (``open(tmp_path, "r+b")``) and
increment ``progress[idx]`` immediately after ``f.write(buf)``, while the monitor
persists a checkpoint every 5 MB WITHOUT flushing any worker handle.  curl_cffi
may yield transport buffers far smaller than the requested chunk, so sub-buffer
yields accumulate in the BufferedWriter and the saved checkpoint runs ahead of
the bytes the OS has actually seen.  After SIGKILL/OOM the resume trusts it --
``reconcile_with_disk`` only checks the sparse ``.part`` is ``>= total``, which
``truncate(total)`` guarantees always -- so the workers skip a region that was
never written and the file is promoted with zero-filled holes mid-file.

HOW THE KILL IS MODELLED, AND WHY IT IS FAITHFUL.  Row 431's acceptance says
"hard-kills the workers".  This file reads the ``.part`` through an INDEPENDENT
file descriptor at the moment of the checkpoint save, and resumes run 2 from
exactly those bytes.  That is byte-for-byte the post-SIGKILL state: a
``BufferedWriter``'s residue lives in the PROCESS, while everything it has
written lives in the shared page cache, which is what any other descriptor --
and any reader after the process dies -- sees.  SIGKILL loses precisely the
userspace residue and nothing else.  A subprocess-plus-SIGKILL harness would
measure the same thing with a race and a timeout attached, so the deviation is
deliberate and stated rather than hidden.

WHAT IS REAL IN HERE.  Rows 428 and 430 run against a real loopback origin
(``ThreadingHTTPServer``) over the real client, through the real
``_http_download``: real ``Range``/``If-Range`` negotiation, real 200/206/416
answers, real ``.part`` and ``.part.meta`` files on disk.  Row 431 runs the real
``_http_download_parallel`` -- real threads, real monitor loop, real
``resume.save``, real buffered handles -- against an in-process origin whose only
job is to hand the workers sub-buffer-size yields on demand and to freeze them
without a sleep.  The subject there is the file handle and the checkpoint, not
the socket.

NO SLEEPS AS SYNCHRONISATION.  Workers are frozen and released with
``threading.Event``; the only sleeps in the run are production's own monitor
tick and cap window.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe


# ── the loopback origin (rows 428, 430) ──────────────────────────────────────

class _Origin(BaseHTTPRequestHandler):
    """A deliberately RFC-shaped origin.

    It is shaped once and shared by both rows so that row 430's "a genuine 416
    still records 0" control cannot pass by accident against an origin that
    omits the very header row 428's fix requires.
    """

    protocol_version = "HTTP/1.1"
    spec: dict = {}
    requests: list = []

    def log_message(self, *_a):        # keep pytest output clean
        return

    def do_GET(self):                  # noqa: N802 - BaseHTTPRequestHandler API
        cfg = self.spec.get(self.path)
        if cfg is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = cfg["body"]
        etag = cfg.get("etag")
        rng = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        type(self).requests.append(
            {"path": self.path, "range": rng, "if_range": if_range})

        extra: list[tuple[str, str]] = []
        if rng is None or cfg.get("ignore_range"):
            status, payload = 200, body
        else:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            if (if_range is not None and etag is not None and if_range != etag
                    and not cfg.get("ignore_if_range")):
                # RFC 9110 14.2: validator no longer matches -> whole resource.
                status, payload = 200, body
            elif start >= len(body):
                status, payload = 416, b""
                if not cfg.get("omit_content_range_on_416"):
                    extra.append(("Content-Range", f"bytes */{len(body)}"))
            else:
                status, payload = 206, body[start:]
                extra.append(
                    ("Content-Range",
                     f"bytes {start}-{len(body) - 1}/{len(body)}"))

        self.send_response(status)
        if etag:
            self.send_header("ETag", etag)
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


class _Ctx:
    """The only Playwright surface the transport touches."""

    def cookies(self):
        return []


def _serve(spec):
    handler = type("_BoundOrigin", (_Origin,), {"spec": spec, "requests": []})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, handler, thread


@pytest.fixture
def origin():
    state = {}

    def start(spec):
        srv, handler, thread = _serve(spec)
        state["srv"], state["thread"] = srv, thread
        return f"http://127.0.0.1:{srv.server_address[1]}", handler

    yield start
    srv = state.get("srv")
    if srv is not None:
        srv.shutdown()
        srv.server_close()
        state["thread"].join(timeout=10)


def _isolate(monkeypatch, tmp_path):
    """Every DB write this path makes (queue_upsert on resume, the daily byte
    accumulator) must land in the test's own tree.  Inherited values are
    REMOVED, not merely left unset -- CLAUDE.md A7."""
    monkeypatch.delenv("BD_INSTALL_DIR", raising=False)
    monkeypatch.setenv("BD_DISABLE_KEEPALIVE", "1")
    work = tmp_path / "bdhome"
    work.mkdir(exist_ok=True)
    monkeypatch.chdir(work)
    return work


def _harness(**config):
    from bulk_downloader.runner_transport import TransportMixin

    class _H(TransportMixin):
        def __init__(self):
            self.site_id = "txproof"
            self.config = {"use_curl_cffi": False, "parallel_chunks": 1}
            self.config.update(config)
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()          # set == running
            self._throughput_samples = 0
            self._throughput_ewma_bps = 0.0
            self.events: list[tuple[str, str]] = []
            self.job_updates: list[tuple] = []

        def log_event(self, kind, message, **_kw):
            self.events.append((kind, message))

        def _update_job(self, url, status, message, **_extra):
            self.job_updates.append((url, status, message))

    class _Log:
        def warning(self, *_a, **_k):
            return

        def info(self, *_a, **_k):
            return

    h = _H()
    h.log = _Log()
    return h


def _count_renames(monkeypatch):
    """Count Path.rename calls, calling through.  Row 428's acceptance asks for
    'renames exactly 0 times' on refusal and exactly once on the legitimate
    promotion, which is a count, not an inference from the filesystem."""
    real = Path.rename
    seen: list[tuple[str, str]] = []

    def counting(self, target):
        seen.append((str(self), str(target)))
        return real(self, target)

    monkeypatch.setattr(Path, "rename", counting)
    return seen


# ═════════════════════════════════════════════════════════════════════════════
# ROW 428 -- the 416 promotion
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "header,expected",
    [
        (None, None),                        # no header at all -> UNKNOWN
        ("", None),
        ("bytes */*", None),                 # unknown complete length
        ("bytes 0-5/10", None),              # satisfied-range form: malformed on a 416
        ("bytes */1000", 1000),
        ("  BYTES  */1000  ", 1000),         # case/space tolerant
        ("bytes */-1", None),
        ("bytes */abc", None),
    ],
)
def test_the_416_complete_length_parser_refuses_everything_it_cannot_read(
        header, expected):
    """A parser that guesses is the defect wearing a fix's clothes."""
    from bulk_downloader.runner_transport import _content_range_complete_length

    assert _content_range_complete_length(header) == expected


def _prepare_part(tmp_path, name, part_bytes, meta=None):
    dest = tmp_path / name
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(part_bytes)
    metap = part.with_suffix(part.suffix + ".meta")
    if meta is None:
        metap.unlink(missing_ok=True)
    else:
        metap.write_text(json.dumps(meta), encoding="utf-8")
    # PRECONDITIONS, asserted rather than assumed.
    assert part.stat().st_size == len(part_bytes)
    assert metap.exists() is (meta is not None)
    assert not dest.exists()
    return dest, part, metap


def test_a_416_over_a_shorter_resource_must_not_promote_the_stale_part(
        origin, monkeypatch, tmp_path):
    """The row-428 defect, end to end.

    The .part is 4096 bytes of an OLD resource; the URL now serves 1000 bytes.
    416 says only 'your offset is past the end of what I have now'.
    """
    _isolate(monkeypatch, tmp_path)
    old = b"A" * 4096
    new = b"N" * 1000
    base, handler = origin({"/f.bin": {"body": new, "etag": '"new"'}})
    dest, part, metap = _prepare_part(tmp_path, "f.bin", old, meta=None)
    renames = _count_renames(monkeypatch)

    from bulk_downloader.runner_transport import _HTTPDownloadFailed
    h = _harness()
    with pytest.raises(_HTTPDownloadFailed) as exc:
        h._http_download(base + "/f.bin", None, _Ctx(), base + "/f.bin", dest)

    # the seam actually fired: a resumed request, answered 416
    assert handler.requests, "the origin was never contacted"
    assert handler.requests[0]["range"] == "bytes=4096-", handler.requests[0]
    assert handler.requests[0]["if_range"] is None, (
        "no sidecar was present, so the resume went out bare -- which is the "
        "precondition that makes the promotion unverifiable")
    # the distinctive diagnostic, not merely 'some failure'
    msg = str(exc.value)
    assert "416" in msg and "1000" in msg and "4096" in msg, msg
    assert "complete-length" in msg, msg
    # renames exactly 0 times; no stale file was published
    assert renames == [], f"the .part was renamed anyway: {renames}"
    assert not dest.exists(), "a truncated stale file was promoted"
    assert not part.exists(), (
        "the unpromotable .part was left in place, so every retry re-runs the "
        "same 416 forever")
    assert not metap.exists()


def test_a_416_with_no_parseable_complete_length_is_unknown_not_permission(
        origin, monkeypatch, tmp_path):
    """A7: unavailable measurement is UNKNOWN, never OK."""
    _isolate(monkeypatch, tmp_path)
    old = b"A" * 4096
    base, handler = origin({"/g.bin": {"body": b"N" * 1000,
                                       "omit_content_range_on_416": True}})
    dest, part, _metap = _prepare_part(tmp_path, "g.bin", old, meta=None)
    renames = _count_renames(monkeypatch)

    from bulk_downloader.runner_transport import _HTTPDownloadFailed
    h = _harness()
    with pytest.raises(_HTTPDownloadFailed) as exc:
        h._http_download(base + "/g.bin", None, _Ctx(), base + "/g.bin", dest)

    assert handler.requests[0]["range"] == "bytes=4096-"
    msg = str(exc.value)
    assert "416" in msg and "no parseable" in msg, msg
    assert renames == []
    assert not dest.exists()
    assert not part.exists()


def test_a_416_whose_validator_no_longer_matches_must_not_promote(
        origin, monkeypatch, tmp_path):
    """Length alone is not identity: same size, different resource.

    The origin here evaluates Range BEFORE If-Range, which is why the fix
    cannot lean on the 200-on-mismatch dance: If-Range is an optimisation the
    origin may decline, the sidecar that carries the validator is optional, and
    the 416 arrives anyway.  Same length, different resource, and only the
    response's own validator distinguishes them.
    """
    _isolate(monkeypatch, tmp_path)
    old = b"A" * 2048
    base, handler = origin({"/h.bin": {"body": b"N" * 2048, "etag": '"v2"',
                                       "ignore_if_range": True}})
    dest, part, _m = _prepare_part(tmp_path, "h.bin", old,
                                    meta={"etag": '"v1"'})
    renames = _count_renames(monkeypatch)

    from bulk_downloader.runner_transport import _HTTPDownloadFailed
    h = _harness()
    with pytest.raises(_HTTPDownloadFailed) as exc:
        h._http_download(base + "/h.bin", None, _Ctx(), base + "/h.bin", dest)

    assert handler.requests[0]["if_range"] == '"v1"', (
        "the stashed validator never went out, so this case did not test what "
        "it claims to")
    msg = str(exc.value)
    assert "416" in msg and "validator" in msg, msg
    assert renames == []
    assert not dest.exists()


def test_a_genuinely_complete_part_still_promotes_exactly_once(
        origin, monkeypatch, tmp_path):
    """NEGATIVE CONTROL for row 428: the legitimate already-complete resume.

    Same length, matching validator -> one rename, the real bytes, and a
    transferred count of exactly 0 because nothing crossed the wire.
    """
    _isolate(monkeypatch, tmp_path)
    payload = bytes(random.Random(4).randrange(1, 256) for _ in range(3000))
    base, handler = origin({"/ok.bin": {"body": payload, "etag": '"v1"'}})
    dest, part, metap = _prepare_part(tmp_path, "ok.bin", payload,
                                       meta={"etag": '"v1"'})
    renames = _count_renames(monkeypatch)

    h = _harness()
    size, fetched = h._http_download(base + "/ok.bin", None, _Ctx(),
                                      base + "/ok.bin", dest)

    assert handler.requests[0]["range"] == "bytes=3000-"
    assert handler.requests[0]["if_range"] == '"v1"'
    assert len(renames) == 1, renames
    assert dest.exists() and dest.read_bytes() == payload
    assert size == 3000
    assert fetched == 0, (
        "a 416 moves no bytes; reporting anything else would restore the "
        "history lie row 430 is about")
    assert not part.exists() and not metap.exists()


def test_a_genuinely_complete_part_promotes_even_when_the_416_carries_no_etag(
        origin, monkeypatch, tmp_path):
    """NEGATIVE CONTROL: many CDNs omit ETag on a 416.  Requiring a response
    validator that the origin never sends would re-download every completed
    file; row 428 requires the validator check only when one WAS stashed and
    the response actually carries one."""
    _isolate(monkeypatch, tmp_path)
    payload = b"P" * 1500
    base, _handler = origin({"/noetag.bin": {"body": payload}})
    dest, part, _m = _prepare_part(tmp_path, "noetag.bin", payload, meta=None)

    h = _harness()
    size, fetched = h._http_download(base + "/noetag.bin", None, _Ctx(),
                                      base + "/noetag.bin", dest)
    assert (size, fetched) == (1500, 0)
    assert dest.read_bytes() == payload
    assert not part.exists()


# ═════════════════════════════════════════════════════════════════════════════
# ROW 430 -- bytes_fetched must count the wire
# ═════════════════════════════════════════════════════════════════════════════

def test_a_200_answer_to_a_resume_reports_the_bytes_it_actually_streamed(
        origin, monkeypatch, tmp_path):
    """RED: .part 5000 bytes, the new resource 3000 -- transferred goes
    negative and max(0, ...) reports 0 for a real 3000-byte transfer."""
    _isolate(monkeypatch, tmp_path)
    new = b"B" * 3000
    base, handler = origin({"/s.bin": {"body": new, "etag": '"v2"'}})
    dest, part, _m = _prepare_part(tmp_path, "s.bin", b"A" * 5000,
                                    meta={"etag": '"v1"'})

    h = _harness()
    size, fetched = h._http_download(base + "/s.bin", None, _Ctx(),
                                      base + "/s.bin", dest)

    # preconditions: the request resumed, the origin answered 200 (If-Range
    # mismatch), and the loop restarted in 'wb' rather than gluing bytes on.
    assert handler.requests[0]["range"] == "bytes=5000-"
    assert handler.requests[0]["if_range"] == '"v1"'
    assert dest.read_bytes() == new, "the restart appended instead of rewriting"
    assert size == 3000
    assert fetched == 3000, (
        "bytes_fetched is an on-disk delta, not the streamed count: 3000 bytes "
        "crossed the wire and history was told 0, which db.py's contract reads "
        "as 'nothing was transferred'")
    assert any(kind == "resume" for kind, _m in h.events)


def test_a_200_answer_to_a_resume_is_not_undercounted_by_the_old_part(
        origin, monkeypatch, tmp_path):
    """The other direction: a larger new file was undercounted by resume_from."""
    _isolate(monkeypatch, tmp_path)
    new = b"B" * 4000
    base, handler = origin({"/big.bin": {"body": new, "etag": '"v2"'}})
    dest, _part, _m = _prepare_part(tmp_path, "big.bin", b"A" * 1000,
                                     meta={"etag": '"v1"'})

    h = _harness()
    size, fetched = h._http_download(base + "/big.bin", None, _Ctx(),
                                      base + "/big.bin", dest)
    assert handler.requests[0]["range"] == "bytes=1000-"
    assert dest.read_bytes() == new
    assert size == 4000
    assert fetched == 4000, "M, not M - resume_from: the whole file was streamed"


def test_a_bare_range_restart_reports_the_whole_stream(
        origin, monkeypatch, tmp_path):
    """A server that ignores Range entirely -- no If-Range needed, and the
    legacy sidecar-less .part that row 428 names is exactly this shape."""
    _isolate(monkeypatch, tmp_path)
    new = b"C" * 2500
    base, handler = origin({"/ig.bin": {"body": new, "ignore_range": True}})
    dest, _part, _m = _prepare_part(tmp_path, "ig.bin", b"A" * 4000, meta=None)

    h = _harness()
    size, fetched = h._http_download(base + "/ig.bin", None, _Ctx(),
                                      base + "/ig.bin", dest)
    assert handler.requests[0]["range"] == "bytes=4000-"
    assert (size, fetched) == (2500, 2500)
    assert dest.read_bytes() == new


def test_a_genuine_206_resume_reports_only_the_new_bytes(
        origin, monkeypatch, tmp_path):
    """NEGATIVE CONTROL: a real resume must still resume, and must report the
    RESUMED bytes only -- not the whole file."""
    _isolate(monkeypatch, tmp_path)
    payload = bytes(random.Random(11).randrange(1, 256) for _ in range(4000))
    base, handler = origin({"/r.bin": {"body": payload, "etag": '"v1"'}})
    dest, part, _m = _prepare_part(tmp_path, "r.bin", payload[:1000],
                                    meta={"etag": '"v1"'})

    h = _harness()
    size, fetched = h._http_download(base + "/r.bin", None, _Ctx(),
                                      base + "/r.bin", dest)
    assert handler.requests[0]["range"] == "bytes=1000-"
    assert handler.requests[0]["if_range"] == '"v1"'
    assert dest.read_bytes() == payload, "the 206 did not glue on correctly"
    assert size == 4000
    assert fetched == 3000, "a 206 transfers total - resume_from, exactly"


def test_a_fresh_download_reports_every_byte(origin, monkeypatch, tmp_path):
    """NEGATIVE CONTROL: no .part at all."""
    _isolate(monkeypatch, tmp_path)
    payload = b"F" * 7000
    base, handler = origin({"/n.bin": {"body": payload}})
    dest = tmp_path / "n.bin"
    assert not dest.with_suffix(".bin.part").exists()

    h = _harness()
    size, fetched = h._http_download(base + "/n.bin", None, _Ctx(),
                                      base + "/n.bin", dest)
    assert handler.requests[0]["range"] is None
    assert (size, fetched) == (7000, 7000)


def test_an_unmeasurable_final_size_refuses_instead_of_claiming_zero(
        origin, monkeypatch, tmp_path):
    """A7's third state on this path.

    The transferred count must not be derived from a stat that can fail: when
    the first stat of the promoted file raises, the pre-fix code swallowed it,
    left ``transferred`` at its initialised 0, and returned a done-shaped
    ``(size, 0)`` for a 3000-byte transfer.  After the fix the count comes from
    the stream itself, so a failed stat can no longer manufacture a zero -- it
    refuses, which is the honest outcome for an unmeasurable file.
    """
    _isolate(monkeypatch, tmp_path)
    new = b"B" * 3000
    base, _handler = origin({"/x.bin": {"body": new, "etag": '"v2"'}})
    dest, _part, _m = _prepare_part(tmp_path, "x.bin", b"A" * 5000,
                                     meta={"etag": '"v1"'})

    real_stat = Path.stat
    state = {"raised": 0}

    def flaky_stat(self, *a, **kw):
        if str(self) == str(dest) and state["raised"] == 0:
            state["raised"] += 1
            raise OSError(5, "simulated stat failure")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    h = _harness()
    outcome: tuple
    try:
        outcome = ("returned", h._http_download(
            base + "/x.bin", None, _Ctx(), base + "/x.bin", dest))
    except OSError as e:
        outcome = ("raised", e)

    assert state["raised"] == 1, (
        "the simulated stat failure never fired, so this control measured "
        "nothing")
    assert outcome[0] == "raised", (
        f"the transport returned {outcome[1]!r}: a swallowed stat failure was "
        "reported as 'nothing was transferred' for a 3000-byte download")


# ═════════════════════════════════════════════════════════════════════════════
# ROW 431 -- the parallel checkpoint may claim only what the OS can see
# ═════════════════════════════════════════════════════════════════════════════

_TOTAL = 8 * 1024 * 1024          # > the monitor's 5 MB checkpoint threshold
_N_CHUNKS = 4
_SLICE = _TOTAL // _N_CHUNKS
_PIECE = 1000                     # sub-buffer yields: the row's condition
_GATE_AT = 1_200_000              # where chunks 1..3 freeze (a whole
                                  # number of pieces, so the freeze
                                  # lands on an exact byte count, and
                                  # 3 x _GATE_AT + one whole slice is
                                  # above the monitor's 5 MB save
                                  # threshold)


def _payload(n: int) -> bytes:
    """Random bytes with NO zero byte, so a zero in the .part is unambiguously
    a hole rather than data that happened to be 0x00."""
    raw = random.Random(2026).randbytes(n)
    return raw.translate(bytes([1]) + bytes(range(1, 256)))


class _ShimOrigin:
    """An in-process origin for the parallel path.

    It exists for two things production's socket cannot give a test
    deterministically: yields SMALLER than the write buffer (curl_cffi's
    documented behaviour, and the condition the defect needs), and freezing a
    worker mid-slice on an Event rather than a sleep.  Range semantics, the 206
    status, the response object and the iteration protocol are the real ones.
    """

    def __init__(self, body, slice_size, piece=_PIECE):
        import httpx as _real_httpx
        self.body = body
        self.slice_size = slice_size
        self.piece = piece
        self.Timeout = _real_httpx.Timeout
        self.HTTPError = _real_httpx.HTTPError
        self.served = 0
        self.requests: list[tuple[int, int]] = []
        self._lock = threading.Lock()
        self.hold_after: dict[int, int] = {}
        self.release: dict[int, threading.Event] = {}
        self.held: set[int] = set()
        self.on_hold = None            # callback(idx) under the lock

    def _note_held(self, idx):
        with self._lock:
            self.held.add(idx)
            cb = self.on_hold
        if cb is not None:
            cb(idx)

    def stream(self, method, url, headers=None, cookies=None,
               follow_redirects=True, **_kw):
        rng = (headers or {}).get("Range")
        assert rng is not None, "the parallel worker must send a Range"
        first, last = rng.split("=", 1)[1].split("-")
        start, end = int(first), int(last)
        idx = start // self.slice_size
        with self._lock:
            self.requests.append((start, end))
        data = self.body[start:end + 1]
        shim = self

        class _Resp:
            status_code = 206
            headers = {"Content-Length": str(len(data))}

            def iter_bytes(self, chunk_size=None):
                sent = 0
                held_once = False
                hold = shim.hold_after.get(idx)
                while sent < len(data):
                    if hold is not None and not held_once and sent >= hold:
                        held_once = True
                        shim._note_held(idx)
                        ev = shim.release.get(idx)
                        assert ev is not None, f"no release event for {idx}"
                        assert ev.wait(timeout=60), (
                            f"chunk {idx} was never released")
                    piece = data[sent:sent + shim.piece]
                    sent += len(piece)
                    with shim._lock:
                        shim.served += len(piece)
                    yield piece

            def iter_content(self, chunk_size=None):   # curl_cffi spelling
                return self.iter_bytes(chunk_size)

        class _Ctxm:
            def __enter__(self_inner):
                return _Resp()

            def __exit__(self_inner, *a):
                return False

        return _Ctxm()


def _visible_prefix(part: Path, start: int, claimed: int, body: bytes):
    """Bytes of [start, start+claimed) that an INDEPENDENT descriptor can see.

    This is the post-SIGKILL view: a BufferedWriter's residue is process
    memory; everything it has written is in the shared page cache.
    """
    fd = os.open(str(part), os.O_RDONLY)
    try:
        region = os.pread(fd, claimed, start)
    finally:
        os.close(fd)
    assert len(region) == claimed, (
        f"the .part is short: read {len(region)} of {claimed} at {start}")
    hole = region.find(b"\x00")
    visible = claimed if hole < 0 else hole
    assert region[:visible] == body[start:start + visible], (
        "the visible bytes are not this chunk's source bytes")
    return visible


def _parallel_harness(monkeypatch, shim):
    from bulk_downloader import resume as _resume
    from bulk_downloader import runner_transport as _rt

    monkeypatch.setattr(_rt, "httpx", shim)
    # The HEAD probe uses `import httpx as _hx` -- the real module, unreachable
    # from the attribute patch above.  It is not this row's subject, and a live
    # connection attempt has no place in a unit lane, so it is stubbed to the
    # no-validator answer a CDN without ETag gives.  is_resumable compares
    # validators only when BOTH sides carry one, so resume still engages.
    monkeypatch.setattr(_resume, "head_probe",
                        lambda *_a, **_k: {"etag": None, "last_modified": None})
    return _harness(use_curl_cffi=False, parallel_chunks=_N_CHUNKS), _resume, _rt


def test_a_small_write_to_a_real_buffered_handle_is_invisible_to_the_os(
        tmp_path):
    """PRECONDITION for everything below: the seam is real on this filesystem.

    If a 1000-byte write were already visible, the checkpoint tests would pass
    vacuously and prove nothing.
    """
    p = tmp_path / "buffered.bin"
    p.write_bytes(b"\x00" * 4096)
    with open(p, "r+b") as f:
        assert f.write(b"Z" * _PIECE) == _PIECE
        fd = os.open(str(p), os.O_RDONLY)
        try:
            seen = os.pread(fd, _PIECE, 0)
        finally:
            os.close(fd)
        assert seen == b"\x00" * _PIECE, (
            "writes of 1000 bytes reach the OS immediately here, so the "
            "buffered-residue condition row 431 describes cannot be "
            "reproduced -- this is UNKNOWN, not a pass")
        f.flush()
        fd = os.open(str(p), os.O_RDONLY)
        try:
            assert os.pread(fd, _PIECE, 0) == b"Z" * _PIECE
        finally:
            os.close(fd)


@pytest.fixture
def frozen_run(monkeypatch, tmp_path):
    """Run the real parallel download with chunks 1..3 frozen at a known byte
    count, capture the checkpoint save that happens while they are frozen, and
    snapshot the exact state a SIGKILL would leave behind.

    Sequencing, all by Event, no sleeps:
      * chunk 0 is held at byte 0 until chunks 1..3 have each streamed exactly
        _GATE_AT bytes and are parked;
      * chunk 0 then streams its whole 2 MB slice, which is the only way total
        progress can reach the monitor's 5 MB save threshold (3 x 1 MB + 2 MB);
      * so the save fires with chunks 1..3 frozen at exactly _GATE_AT and
        chunk 0 finished and closed -- no racing writer, exact counts;
      * the save observer measures, snapshots, and releases.
    """
    _isolate(monkeypatch, tmp_path)
    body = _payload(_TOTAL)
    shim = _ShimOrigin(body, _SLICE)
    h, _resume, _rt = _parallel_harness(monkeypatch, shim)

    rest = threading.Event()
    chunk0 = threading.Event()
    shim.hold_after = {0: 0, 1: _GATE_AT, 2: _GATE_AT, 3: _GATE_AT}
    shim.release = {0: chunk0, 1: rest, 2: rest, 3: rest}

    def _on_hold(_idx):
        if {1, 2, 3} <= shim.held:
            chunk0.set()

    shim.on_hold = _on_hold

    dest = tmp_path / "par.bin"
    part = dest.with_suffix(dest.suffix + ".part")
    saves: list[dict] = []
    snapshot: dict = {}
    real_save = _resume.save

    def observing_save(final_path, checkpoint):
        ok = real_save(final_path, checkpoint)
        qualifying = False
        try:
            claims = [(int(c["start"]), int(c["done_bytes"]))
                      for c in checkpoint["chunks"]]
            # QUALIFYING == the save this fixture exists to observe: chunks
            # 1..3 parked at exactly _GATE_AT. Only then is `progress` frozen,
            # so claimed and visible describe the same instant. `initialize()`
            # persists an all-zero checkpoint before any worker starts, and
            # releasing on that one would dissolve the whole sequence.
            qualifying = all(claims[i][1] == _GATE_AT for i in (1, 2, 3))
            rec = {"claims": claims, "qualifying": qualifying, "visible": []}
            for start, claimed in claims:
                rec["visible"].append(
                    _visible_prefix(part, start, claimed, body) if claimed
                    else 0)
            saves.append(rec)
            if qualifying and not snapshot:
                fd = os.open(str(part), os.O_RDONLY)
                try:
                    snapshot["part"] = os.pread(fd, _TOTAL, 0)
                finally:
                    os.close(fd)
                snapshot["sidecar"] = _resume.sidecar_path(dest).read_bytes()
        except Exception as e:                     # recorded, never hidden
            saves.append({"error": f"{type(e).__name__}: {e}", "qualifying": False})
        finally:
            if qualifying:
                rest.set()
        return ok

    monkeypatch.setattr(_resume, "save", observing_save)

    result = h._http_download_parallel(str(dest), _Ctx(),
                                       "http://origin.invalid/par.bin",
                                       dest, total=_TOTAL, n_chunks=_N_CHUNKS)
    rest.set()
    return {"body": body, "dest": dest, "result": result, "saves": saves,
            "snapshot": snapshot, "shim": shim, "tmp": tmp_path}


def test_the_checkpoint_never_claims_more_than_the_os_can_see(frozen_run):
    """RED (row 431): the save claims _GATE_AT bytes for each frozen chunk
    while the OS holds fewer -- the difference is sitting in the worker's
    BufferedWriter and dies with the process."""
    saves = frozen_run["saves"]
    assert saves, "no checkpoint save was observed -- nothing was measured"
    errors = [s for s in saves if "error" in s]
    assert not errors, errors

    qualifying = [s for s in saves if s.get("qualifying")]
    assert len(qualifying) == 1, (
        "the fixture's sequencing did not produce exactly one save with the "
        f"workers parked; saves seen: {saves}")
    rec = qualifying[0]

    frozen = []
    for idx in (1, 2, 3):
        start, claimed = rec["claims"][idx]
        frozen.append((idx, start, claimed, rec["visible"][idx]))
    # the precondition, asserted: each frozen chunk had streamed exactly
    # _GATE_AT bytes and nothing was moving when the checkpoint was written
    assert [c for _i, _s, c, _v in frozen] == [_GATE_AT] * 3, frozen

    over = [(idx, claimed, visible, claimed - visible)
            for idx, _s, claimed, visible in frozen if claimed > visible]
    assert not over, (
        "the checkpoint claims bytes the OS has never seen "
        "(chunk, claimed, visible, deficit): " + repr(over) +
        " -- after a SIGKILL the resume skips that region and the promoted "
        "file carries zero-filled holes")


def test_a_kill_at_a_checkpoint_resumes_without_holes(frozen_run, monkeypatch,
                                                       tmp_path):
    """The consequence, measured: restore the exact bytes and checkpoint a
    SIGKILL would have left, resume with the real code, and compare the
    finished file with its source."""
    snapshot = frozen_run["snapshot"]
    assert snapshot, "nothing was snapshotted, so nothing is being proven"
    body = frozen_run["body"]

    room = tmp_path / "afterkill"
    room.mkdir()
    dest = room / "par.bin"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(snapshot["part"])
    from bulk_downloader import resume as _resume
    _resume.sidecar_path(dest).write_bytes(snapshot["sidecar"])

    cp = json.loads(snapshot["sidecar"])
    claimed_total = sum(int(c["done_bytes"]) for c in cp["chunks"])
    assert 0 < claimed_total < _TOTAL, cp["chunks"]
    assert part.stat().st_size == _TOTAL, "the .part is pre-allocated sparse"

    shim = _ShimOrigin(body, _SLICE)
    h, _res, _rt = _parallel_harness(monkeypatch, shim)
    size, fetched = h._http_download_parallel(
        str(dest), _Ctx(), "http://origin.invalid/par.bin", dest,
        total=_TOTAL, n_chunks=_N_CHUNKS)

    assert size == _TOTAL
    assert fetched == _TOTAL - claimed_total, (
        "the resume must fetch exactly the bytes the checkpoint did not claim")
    assert 0 < fetched < _TOTAL, "this run neither restarted nor no-opped"
    got = dest.read_bytes()
    holes = got.count(b"\x00")
    # The mechanism, stated as arithmetic rather than as prose: every hole in
    # the finished file is a byte the checkpoint claimed and the OS never had.
    deficit = sum(max(0, claimed - visible)
                  for rec in frozen_run["saves"] if rec.get("qualifying")
                  for (_st, claimed), visible in zip(rec["claims"],
                                                      rec["visible"]))
    assert holes == deficit, (
        f"{holes} zero bytes in the finished file against a measured "
        f"checkpoint over-claim of {deficit} -- if these differ, the harness "
        "is measuring something other than the defect")
    assert holes == 0, (
        f"the resumed file carries {holes} zero bytes the source never had: "
        "the checkpoint claimed buffered bytes and the resume skipped them")
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(body).hexdigest()


def test_the_frozen_run_itself_still_completes_correctly(frozen_run):
    """NEGATIVE CONTROL: a genuine parallel download -- with every worker
    released and closed -- still produces the whole file and reports every
    byte it fetched."""
    size, fetched = frozen_run["result"]
    dest = frozen_run["dest"]
    assert (size, fetched) == (_TOTAL, _TOTAL)
    assert dest.read_bytes() == frozen_run["body"]
    assert not dest.with_suffix(dest.suffix + ".part").exists()
    from bulk_downloader import resume as _resume
    assert not _resume.sidecar_path(dest).exists(), (
        "the checkpoint sidecar outlived the completed download")


def test_a_clean_parallel_download_completes_untouched(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: no gates, no observers -- the ordinary path."""
    _isolate(monkeypatch, tmp_path)
    body = _payload(_TOTAL)
    shim = _ShimOrigin(body, _SLICE)
    h, _res, _rt = _parallel_harness(monkeypatch, shim)
    dest = tmp_path / "clean.bin"
    size, fetched = h._http_download_parallel(
        str(dest), _Ctx(), "http://origin.invalid/clean.bin", dest,
        total=_TOTAL, n_chunks=_N_CHUNKS)
    assert (size, fetched) == (_TOTAL, _TOTAL)
    assert dest.read_bytes() == body
    assert shim.served == _TOTAL
    assert len(shim.requests) == _N_CHUNKS
