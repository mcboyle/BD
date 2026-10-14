"""bughunt ddt-fix -- the sequential HTTP transfer path (deep-dl-transport).

Source: harness-work/O1789/bughunt/deep-dl-transport/FINDINGS.md (finder
bd-worker-S40-A, BASE b0ea77a6). Each FOUND row is reproduced here from the
scenario FINDINGS names (T-ids from SCENARIOS.md) against the REAL
``TransportMixin._http_download`` / ``_run_http_attempts_with_resume`` and a
loopback origin, on BOTH clients wherever FINDINGS ran both (curl_cffi, the
default, and httpx). The FOUND NONE scenarios FINDINGS names as controls
(T01-T04, T06, T11-T16) run beside them and must hold before and after.

  F1  a short 206 is promoted as the complete file (T08, T09, T10)
  F2  curl_cffi's mid-body / stall messages never trigger the same-URL resume
      retry (T35 end to end, T35c / T29 classifier)
  F3  a 200 with Content-Length 0 is promoted as a 0-byte file (T33)
  F4  duplicate identical Content-Length joined by curl_cffi -> int() raises (T20)
  F5  a weak ETag is sent as If-Range, so every resume re-downloads (T17)
  F6  the parallel->sequential fallback lets ENOSPC through (no "disk" in it)

Hermetic: tmp_path (conftest sets BD_HOME), 127.0.0.1 origins only, no AI.
"""
from __future__ import annotations

import http.server
import json
import logging
import threading
from pathlib import Path

import pytest

from bulk_downloader import runner_transport as RT
from bulk_downloader import staging_claim

BD_GATE_SCOPE = "module"

T = 64 * 1024
BODY = bytes((i * 7 + i // 251) % 256 for i in range(T))
BODY2 = bytes((i * 13 + 5) % 256 for i in range(T))
CLIENTS = ("cffi", "httpx")


class Stub(RT.TransportMixin):
    """The real mixin on the smallest host the sequential path needs."""

    def __init__(self, client, **config):
        self.site_id = "ddtfix"
        self.config = {"use_curl_cffi": client == "cffi",
                       "dedup_header_preflight": False,
                       "parallel_chunks": 1, **config}
        self.events = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self.log = logging.getLogger("ddt-fix")

    def log_event(self, *a, **k):
        self.events.append(" ".join(str(x) for x in a))

    def _update_job(self, *a, **k):
        return True

    def _pick_fastest_mirror(self, u):
        return u

    def _recommended_chunk_bytes(self):
        return 4096

    def _current_cap_mbps(self):
        return 0

    def _download_proxy_url(self):
        return None


@pytest.fixture(autouse=True)
def _no_cookies(monkeypatch):
    monkeypatch.setattr(RT, "_extract_scoped_cookies", lambda ctx, url: {})


class _Origin(http.server.BaseHTTPRequestHandler):
    """HTTP/1.0 (close-delimited) loopback origin; one path per scenario."""

    protocol_version = "HTTP/1.0"
    seen: dict = {}

    def log_message(self, *a, **k):
        pass

    def _send(self, code, hdrs, body=b""):
        self.send_response(code)
        for k, v in hdrs:
            self.send_header(k, v)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except OSError:
                pass

    def do_GET(self):
        p = self.path.split("?")[0].strip("/")
        self.seen.setdefault(p, []).append(
            {"range": self.headers.get("Range"),
             "if_range": self.headers.get("If-Range")})
        r = self.headers.get("Range")
        R = int(r[6:-1]) if r and r.startswith("bytes=") and r.endswith("-") else None
        ct = ("Content-Type", "video/mp4")
        if p == "full":
            if R is not None:
                return self._send(206, [ct, ("Content-Range", f"bytes {R}-{T-1}/{T}"),
                                        ("Content-Length", str(T - R))], BODY[R:])
            return self._send(200, [ct, ("Content-Length", str(T))], BODY)
        if p == "trunc":
            return self._send(200, [ct, ("Content-Length", str(T))], BODY[: T // 2])
        if p == "cap":  # CDN-style cap: every ranged answer is at most 1000 bytes
            s = R or 0
            e = min(s + 999, T - 1)
            return self._send(206, [ct, ("Content-Range", f"bytes {s}-{e}/{T}"),
                                    ("Content-Length", str(e - s + 1))], BODY[s:e + 1])
        if p == "206_nocl_short":
            s = R or 0
            return self._send(206, [ct, ("Content-Range", f"bytes {s}-{T-1}/{T}")],
                              BODY[s:s + (T - s) // 2])
        if p == "206_wrongstart":
            s = (R or 0) + 1
            return self._send(206, [ct, ("Content-Range", f"bytes {s}-{T-1}/{T}"),
                                    ("Content-Length", str(T - s))], BODY[s:])
        if p == "206_garbage":
            return self._send(206, [ct, ("Content-Range", f"bytes */{T}"),
                                    ("Content-Length", str(T - (R or 0)))], BODY[R or 0:])
        if p == "416_ok":
            return self._send(416, [("Content-Range", f"bytes */{T}")])
        if p == "416_bad":
            return self._send(416, [("Content-Range", f"bytes */{T + 5}")])
        if p == "416_nocr":
            return self._send(416, [])
        if p == "etag_changed":  # resource is now BODY2 with ETag "v2"
            if R is not None and self.headers.get("If-Range") == '"v2"':
                return self._send(206, [ct, ("ETag", '"v2"'),
                                        ("Content-Range", f"bytes {R}-{T-1}/{T}"),
                                        ("Content-Length", str(T - R))], BODY2[R:])
            return self._send(200, [ct, ("ETag", '"v2"'), ("Content-Length", str(T))], BODY2)
        if p == "weak":
            # RFC 9110 13.1.5: a weak entity tag in If-Range never matches, so
            # a conforming origin answers 200. A plain Range is honoured.
            ir = self.headers.get("If-Range")
            if R is not None and ir is None:
                return self._send(206, [ct, ("ETag", 'W/"w1"'),
                                        ("Content-Range", f"bytes {R}-{T-1}/{T}"),
                                        ("Content-Length", str(T - R))], BODY[R:])
            return self._send(200, [ct, ("ETag", 'W/"w1"'), ("Content-Length", str(T))], BODY)
        if p in ("weak_changed", "weak_lm_changed"):
            # F5-REG (R129-A): the resource was replaced (BODY2, W/"w2") and a
            # conforming origin honours a plain Range on the new bytes.
            hd = [ct, ("ETag", 'W/"w2"')]
            if p == "weak_lm_changed":
                hd.append(("Last-Modified", "Wed, 14 Oct 2026 06:00:00 GMT"))
            if R is not None and self.headers.get("If-Range") is None:
                return self._send(206, hd + [("Content-Range", f"bytes {R}-{T-1}/{T}"),
                                             ("Content-Length", str(T - R))], BODY2[R:])
            return self._send(200, hd + [("Content-Length", str(T))], BODY2)
        if p == "weak_fresh206":  # 206 0-(T-1)/T to any request, replaced resource
            return self._send(206, [ct, ("ETag", 'W/"w2"'),
                                    ("Content-Range", f"bytes 0-{T-1}/{T}"),
                                    ("Content-Length", str(T))], BODY2)
        if p == "strong_same":  # unchanged resource, strong ETag "s1"
            if R is not None and self.headers.get("If-Range") == '"s1"':
                return self._send(206, [ct, ("ETag", '"s1"'),
                                        ("Content-Range", f"bytes {R}-{T-1}/{T}"),
                                        ("Content-Length", str(T - R))], BODY[R:])
            return self._send(200, [ct, ("ETag", '"s1"'), ("Content-Length", str(T))], BODY)
        if p == "cl_abc":
            return self._send(200, [ct, ("Content-Length", "abc")], BODY)
        if p == "cl_dup":
            return self._send(200, [ct, ("Content-Length", str(T)),
                                    ("Content-Length", str(T))], BODY)
        if p == "cl_neg":
            return self._send(200, [ct, ("Content-Length", "-5")], BODY)
        if p == "empty":
            return self._send(200, [ct, ("Content-Length", "0")])
        if p == "flaky":  # first answer dies half-way; a ranged retry gets the rest
            if R is None:
                return self._send(200, [ct, ("Content-Length", str(T))], BODY[: T // 2])
            return self._send(206, [ct, ("Content-Range", f"bytes {R}-{T-1}/{T}"),
                                    ("Content-Length", str(T - R))], BODY[R:])
        self._send(404, [("Content-Length", "0")])


class _Chunked(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a, **k):
        pass

    def do_GET(self):
        p = self.path.strip("/")
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        step = 4096
        pieces = [BODY[i:i + step] for i in range(0, T, step)]
        if p == "chunked_trunc":
            pieces = pieces[: len(pieces) // 2]
        for c in pieces:
            self.wfile.write(b"%x\r\n%s\r\n" % (len(c), c))
        if p == "chunked":
            self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


@pytest.fixture
def origin():
    servers = []

    def start(handler=_Origin):
        handler.seen = {}
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv, f"http://127.0.0.1:{srv.server_address[1]}"

    yield start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _run(tmp_path, base, path, client, part=None, meta=None, **config):
    """One sequential transfer. Returns (ret_or_exc, final_bytes, stub)."""
    d = tmp_path / f"{client}-{path}"
    d.mkdir(parents=True, exist_ok=True)
    final = d / "scene.mp4"
    url = f"{base}/{path}"
    page = f"https://ddtfix.example/{d.name}"
    if part is not None:
        tp = staging_claim.claim(final, staging_claim.job_identity(page),
                                 resource_url=url)
        Path(tp).write_bytes(part)
        if meta is not None:
            Path(str(tp) + ".meta").write_text(meta)
    st = Stub(client, **config)
    try:
        out = st._http_download(page, None, None, url, final)
    except Exception as e:  # the refusal IS the result under test
        out = e
    return out, (final.read_bytes() if final.exists() else None), st


def _refused(out):
    return isinstance(out, (RT._HTTPDownloadFailed, RT._DownloadTruncated))


# ── F1: a short 206 is not the whole file ───────────────────────────────────


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("sid,path,staged", [
    ("T08", "cap", 20000),            # resume capped at 1000 B
    ("T09", "cap", None),             # fresh request answered 206 0-999/N
    ("T10", "206_nocl_short", 20000),  # no Content-Length, closed half-way
])
def test_f1_short_206_is_never_promoted(tmp_path, origin, client, sid, path, staged):
    _srv, base = origin()
    part = None if staged is None else BODY[:staged]
    out, final, _st = _run(tmp_path, base, path, client, part=part)
    assert final is None and _refused(out), (
        f"F1 {sid} {client}: a 206 short of the Content-Range complete-length "
        f"was promoted: ret={out!r} final={None if final is None else len(final)} B of {T}")


@pytest.mark.parametrize("client", CLIENTS)
def test_f1_capped_resume_keeps_the_part(tmp_path, origin, client):
    """The short range is refused BEFORE a byte is appended, so the 20000 B
    already staged survive for a later attempt."""
    _srv, base = origin()
    out, final, _st = _run(tmp_path, base, "cap", client, part=BODY[:20000])
    parts = [p for p in (tmp_path / f"{client}-cap").iterdir() if p.name.endswith(".part")]
    assert final is None and "short range" in str(out), f"F1 {client}: {out!r}"
    assert [p.read_bytes() for p in parts] == [BODY[:20000]], (
        f"F1 {client}: the staged .part was altered by a refused short 206")


# ── F2: curl_cffi's disconnect / stall texts are transient ──────────────────


@pytest.mark.parametrize("msg", [
    "unexpected: Failed to perform, curl: (18) end of response with 32768 bytes missing.",
    "unexpected: Failed to perform, curl: (18) transfer closed with outstanding read data remaining.",
    "unexpected: Failed to perform, curl: (28) Operation too slow. Less than 1 bytes/sec "
    "transferred the last 300 seconds. See https://curl.se/libcurl/c/li",
])
def test_f2_curl_cffi_mid_body_failure_is_transient(msg):
    assert RT._is_transient_download_error(RT._HTTPDownloadFailed(msg)), (
        f"F2: curl_cffi mid-transfer failure not classified transient: {msg!r}")


@pytest.mark.parametrize("msg,want", [
    ("http error: peer closed connection without sending complete message body "
     "(received 32768 bytes, expected 65536)", True),
    ("unexpected: Failed to perform, curl: (56) Recv failure: Connection reset by peer", True),
    ("http error: timed out", True),
    ("HTTP 404", False),
    ("unexpected: Failed to perform, curl: (6) Could not resolve host: x.invalid", False),
    ("stopped", False),
])
def test_f2_control_classifier_unchanged(msg, want):
    assert RT._is_transient_download_error(RT._HTTPDownloadFailed(msg)) is want


@pytest.mark.parametrize("client", CLIENTS)
def test_f2_t35_same_url_resume_fires(tmp_path, origin, client):
    _srv, base = origin()
    d = tmp_path / client
    d.mkdir()
    url = f"{base}/flaky"
    st = Stub(client)
    try:
        out = st._run_http_attempts_with_resume(
            f"https://ddtfix.example/t35-{client}", None, None, url, [url], d / "scene.mp4")
    except Exception as e:
        out = e
    reqs = [x["range"] for x in _Origin.seen.get("flaky", [])]
    final = (d / "scene.mp4").read_bytes() if (d / "scene.mp4").exists() else None
    assert reqs == [None, f"bytes={T // 2}-"] and final == BODY, (
        f"F2 T35 {client}: same-URL resume did not fire: ret={out!r} requests={reqs}")


# ── F3: an empty 200 is not a file ──────────────────────────────────────────


@pytest.mark.parametrize("client", CLIENTS)
def test_f3_empty_200_is_refused(tmp_path, origin, client):
    _srv, base = origin()
    out, final, _st = _run(tmp_path, base, "empty", client)
    left = sorted(p.name for p in (tmp_path / f"{client}-empty").iterdir())
    assert final is None and _refused(out) and "empty response body" in str(out), (
        f"F3 T33 {client}: a 200 Content-Length 0 was promoted: ret={out!r}")
    assert not [n for n in left if n.endswith(".part")], f"F3 {client}: 0 B .part left {left}"


# ── F4: duplicate identical Content-Length is one length ────────────────────


@pytest.mark.parametrize("client", CLIENTS)
def test_f4_duplicate_identical_content_length(tmp_path, origin, client):
    _srv, base = origin()
    out, final, _st = _run(tmp_path, base, "cl_dup", client)
    assert final == BODY and out == (T, T), (
        f"F4 T20 {client}: duplicate identical Content-Length refused: {out!r}")


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("sid,path", [("T19", "cl_abc"), ("T21", "cl_neg")])
def test_f4_control_bad_content_length_still_refused(tmp_path, origin, client, sid, path):
    _srv, base = origin()
    out, final, _st = _run(tmp_path, base, path, client)
    assert final is None and _refused(out), f"{sid} {client}: {out!r}"


def test_f4_content_length_parser():
    assert RT._content_length("65536, 65536") == 65536
    assert RT._content_length("65536") == 65536
    assert RT._content_length(None) == 0
    with pytest.raises(RT._HTTPDownloadFailed, match="conflicting Content-Length"):
        RT._content_length("100, 200")


# ── F5: a weak ETag is never sent as If-Range ───────────────────────────────
# r2 (F5-REG, R129-A): nor does it vouch for a 206. r1's T17 asserted a plain
# Range stitch (out == (T, T - 20000)); that stitch is the regression, so a
# weak-only .part is discarded and the transfer starts at byte 0.


@pytest.mark.parametrize("client", CLIENTS)
def test_f5_weak_etag_restarts_from_zero(tmp_path, origin, client):
    _srv, base = origin()
    out, final, st = _run(tmp_path, base, "weak", client, part=BODY[:20000],
                          meta=json.dumps({"etag": 'W/"w1"'}))
    sent = [(x["range"], x["if_range"]) for x in _Origin.seen.get("weak", [])]
    ok = sent == [(None, None)] and final == BODY and out == (T, T)
    assert ok, (
        f"F5 T17 {client}: weak ETag .part: sent={sent} ret={out!r} "
        f"events={st.events[-1:]}")


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("path,meta", [
    ("weak_changed", {"etag": 'W/"w1"'}),
    ("weak_lm_changed", {"etag": 'W/"w1"', "last_modified": "Tue, 13 Oct 2026 06:00:00 GMT"}),
    ("weak_fresh206", {"etag": 'W/"w1"'}),  # 206 from byte 0: discard, never append
])
def test_f5reg_weak_tag_part_never_stitched(tmp_path, origin, client, path, meta):
    _srv, base = origin()
    out, final, st = _run(tmp_path, base, path, client, part=BODY[:20000],
                          meta=json.dumps(meta))
    sent = [(x["range"], x["if_range"]) for x in _Origin.seen.get(path, [])]
    ok = final == BODY2 and sent == [(None, None)] and out == (T, T)
    assert ok, (
        f"F5-REG {path} {client}: weak-tag .part stitched onto a replaced resource: "
        f"sent={sent} ret={out!r} final==BODY2={final == BODY2} "
        f"stitched={final == BODY[:20000] + BODY2[20000:]} events={st.events[-1:]}")


@pytest.mark.parametrize("client", CLIENTS)
def test_f5reg_weak_part_undiscardable_is_refused(tmp_path, origin, client, monkeypatch):
    _srv, base = origin()
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self.name.endswith(".part"):
            raise PermissionError(13, "denied", str(self))
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", unlink)
    out, final, _st = _run(tmp_path, base, "weak_fresh206", client, part=BODY[:20000],
                           meta=json.dumps({"etag": 'W/"w1"'}))
    sent = _Origin.seen.get("weak_fresh206", [])
    ok = _refused(out) and "could not be discarded" in str(out) and not sent \
        and final is None
    assert ok, (
        f"F5-REG discard {client}: undiscardable weak .part not refused: "
        f"ret={out!r} sent={sent} final={None if final is None else len(final)}")


@pytest.mark.parametrize("client", CLIENTS)
def test_f5_control_strong_etag_match_resumes(tmp_path, origin, client):
    _srv, base = origin()
    out, final, _st = _run(tmp_path, base, "strong_same", client, part=BODY[:20000],
                           meta=json.dumps({"etag": '"s1"'}))
    sent = [(x["range"], x["if_range"]) for x in _Origin.seen.get("strong_same", [])]
    ok = sent == [("bytes=20000-", '"s1"')] and final == BODY and out == (T, T - 20000)
    assert ok, (
        f"F5 control {client}: strong ETag resume: sent={sent} ret={out!r}")


@pytest.mark.parametrize("client", CLIENTS)
def test_f5_control_strong_etag_still_sent(tmp_path, origin, client):
    """T16: a strong validator still goes out as If-Range and a changed
    resource restarts to the new body."""
    _srv, base = origin()
    out, final, _st = _run(tmp_path, base, "etag_changed", client, part=BODY[:20000],
                           meta=json.dumps({"etag": '"v1"'}))
    sent = [x["if_range"] for x in _Origin.seen.get("etag_changed", [])]
    assert sent == ['"v1"'] and final == BODY2, f"T16 {client}: {sent} {out!r}"


# ── F6: a full disk in the parallel path is not retried sequentially ────────


def _enospc_text():
    """The exact text the OS gives a write on a full device (/dev/full)."""
    try:
        with open("/dev/full", "wb", buffering=0) as f:
            f.write(b"x")
    except OSError as e:
        return str(e)
    pytest.skip("/dev/full did not refuse the write")


@pytest.mark.parametrize("kind", ["enospc", "control-reset"])
def test_f6_parallel_disk_full_propagates(tmp_path, origin, monkeypatch, kind):
    _srv, base = origin()
    msg = (f"parallel: chunk 0: {_enospc_text()}" if kind == "enospc"
           else "parallel: chunk 0: Connection reset by peer")
    monkeypatch.setattr(RT.TransportMixin, "_probe_size",
                        lambda self, *a, **k: 10 ** 12)

    def _parallel(self, *a, **k):
        raise RT._HTTPDownloadFailed(msg)

    monkeypatch.setattr(RT.TransportMixin, "_http_download_parallel", _parallel)
    out, final, _st = _run(tmp_path, base, "full", "httpx", parallel_chunks=2)
    seen = _Origin.seen.get("full", [])
    if kind == "enospc":
        assert isinstance(out, RT._HTTPDownloadFailed) and "No space left" in str(out) \
            and seen == [] and final is None, (
                f"F6: ENOSPC in the parallel path fell back to sequential: "
                f"requests={len(seen)} ret={out!r}")
    else:
        assert final == BODY and len(seen) == 1, f"F6 control: {out!r} {seen}"


# ── Controls FINDINGS names as FOUND NONE (T01-T04, T06, T11-T16) ───────────


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("sid,path,staged,lands", [
    ("T01", "full", None, True),
    ("T02", "trunc", None, False),
    ("T06", "full", 20000, True),
    ("T11", "206_wrongstart", 20000, False),
    ("T12", "206_garbage", 20000, False),
    ("T13", "416_ok", T, True),
    ("T14", "416_bad", T, False),
    ("T15", "416_nocr", T, False),
])
def test_control_found_none(tmp_path, origin, client, sid, path, staged, lands):
    _srv, base = origin()
    part = None if staged is None else BODY[:staged]
    out, final, _st = _run(tmp_path, base, path, client, part=part)
    assert final == (BODY if lands else None), f"control {sid} {client}: ret={out!r}"
    if not lands:
        assert _refused(out), f"control {sid} {client}: not refused: {out!r}"


@pytest.mark.parametrize("client", CLIENTS)
@pytest.mark.parametrize("sid,path,lands", [("T03", "chunked", True),
                                            ("T04", "chunked_trunc", False)])
def test_control_chunked(tmp_path, origin, client, sid, path, lands):
    _srv, base = origin(_Chunked)
    out, final, _st = _run(tmp_path, base, path, client)
    assert final == (BODY if lands else None), f"control {sid} {client}: ret={out!r}"
