"""Row 716 -- MIRROR-RESOURCE-MISMATCH-ABANDONS-CHAIN.

THE DEFECT, stated the way the code does it. ``_do_download`` builds a mirror
chain whose alternates come from ``_build_mirror_urls``, which replaces the
URL's NETLOC and nothing else -- so every alternate differs from the original
in host alone, by construction. The loop retries on ``_HTTPDownloadFailed``
and on nothing else. Each attempt then claims the staging path under the URL
IT is attempting::

    downloaded_size, bytes_fetched = self._http_download(
        page_url, page, ctx, attempt_url, final_path)
    ...
    tmp_path = staging_claim.claim(final_path, identity, resource_url=file_url)

``resource_identity`` deliberately treats a host change as a different
resource -- "a safe false mismatch costs a resume, while a false match splices
bytes from two objects" -- so the SECOND attempt, finding its predecessor's
bytes staged under the first host's identity, raises
``StagingResourceMismatch``. That collapses to ``_StagingUnavailable``, which
the mirror loop does not catch, so it escapes past every remaining mirror to
the handler outside the loop and the whole chain ends in ``needs_review``.
One CDN host going down abandons a download that had five good mirrors left
and a megabyte already on disk.

WHAT IS NOT THE DEFECT, and must not be "fixed" here. The provenance guard is
correct: a job that really does resolve to a different media object must never
append to the old object's bytes. The bug is that the TRANSPORT hands the
claim layer the ATTEMPT url when it knows -- by construction of its own mirror
builder -- that the resource is the canonical one the chain started from.

THE TWO HALVES OF THE ACCEPTANCE, proven together in this file and against the
same ``_do_download`` entry point:
  * a same-bytes host-only mirror swap RESUMES and completes the chain;
  * a live ``.part`` owned by a different job still raises, still routes to
    ``needs_review``, and still never reaches the Playwright fallback.
"""

import hashlib
import logging
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

# The CI classifier parses a module-level ASSIGNMENT, not a docstring line.
# The subject is the transport module's mirror chain and the staging claim it
# makes of it, so the affected band selects this gate whenever either moves.
BD_GATE_SCOPE = "module"


CHUNK = 1024 * 1024
BODY_BYTE = 0xAA
OTHER_BYTE = 0xBB
LEN_BODY = 3 * CHUNK
HOLD = CHUNK              # bytes the dying primary host leaves staged

BODY = bytes([BODY_BYTE]) * LEN_BODY
OTHER_BODY = bytes([OTHER_BYTE]) * (2 * CHUNK)


def _resource_identity_for_test(url):
    """Derive the expected identity independently of ``staging_claim``."""
    parsed = urlsplit(url)
    canonical = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _byte_census(blob):
    """{byte value: count} -- the point is that exactly one value appears."""
    return {b: blob.count(bytes([b])) for b in sorted(set(blob))}


def _discriminator(exc):
    """The raise-site cause the row requires be retained, by name."""
    cause = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return type(cause).__name__


# --- the fixture origin -----------------------------------------------------

class _Origin(BaseHTTPRequestHandler):
    """Serves the body with Range support and NO validators.

    Absent ETag / Last-Modified is deliberate: it is what makes the resume
    optimistic, so the staging claim -- not an ``If-Range`` header -- has to be
    the thing that protects the bytes.
    """

    protocol_version = "HTTP/1.1"

    bodies = {}
    requests = []
    lock = threading.Lock()

    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        body = self.bodies.get(path)
        rng = self.headers.get("Range")
        host = self.headers.get("Host", "")
        if body is None:
            with self.lock:
                self.requests.append(
                    {"path": path, "range": rng, "status": 404, "host": host,
                     "start": None})
            self.send_error(404)
            return
        start = 0
        status = 200
        if rng and rng.startswith("bytes="):
            start = int(rng[len("bytes="):].split("-")[0])
            status = 206
        payload = body[start:]
        with self.lock:
            self.requests.append(
                {"path": path, "range": rng, "status": status, "host": host,
                 "start": start, "length": len(payload)})
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(payload)))
        if status == 206:
            self.send_header(
                "Content-Range",
                f"bytes {start}-{len(body) - 1}/{len(body)}")
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture(scope="module")
def origin():
    handler = type("_Row716Origin", (_Origin,), {
        "bodies": {"/scene.mp4": BODY, "/other-scene.mp4": OTHER_BODY},
        "requests": [],
        "lock": threading.Lock(),
    })
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "port": port,
            # Two spellings of ONE origin. `localhost` resolves to the same
            # loopback address the server is bound to, so this is a real
            # host-only mirror swap that really connects and really serves the
            # same bytes -- not a simulation of one.
            "primary": f"http://127.0.0.1:{port}",
            "mirror": f"http://localhost:{port}",
            "handler": handler,
        }
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)


# --- the runner that drives the real _do_download ---------------------------

class _Ctx:
    def cookies(self):
        return []


class _Locator:
    def __init__(self, href):
        self.href = href
        self.clicks = 0

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):
        self.clicks += 1


class _Page:
    def __init__(self, href):
        self.url = "https://example.invalid/page"
        self.href = href

    def title(self):
        return "Scene"

    def expect_download(self, timeout):
        from bulk_downloader.runner_transport import PWTimeout
        raise PWTimeout(f"synthetic fallback timeout after {timeout}")


def _best(href):
    return {"locator": _Locator(href), "score": 1080, "text": "1080p",
            "size": 0, "_via_learned": False, "_all_candidates": []}


def _runner(mirrors=(), first_attempt=None):
    """A real ``TransportMixin``; nothing on the staging path is stubbed.

    ``first_attempt`` models a CDN host that dies mid-transfer: it is called
    instead of the real transfer for the FIRST url only, and every later
    attempt runs the genuine, unstubbed ``_http_download``.
    """
    from bulk_downloader import runner_transport as rt
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class _R(rt.TransportMixin, TelemetryMixin):
        def __init__(self):
            self.site_id = "row716"
            self.config = {
                "name": "Row 716",
                "filename_template": "{filename}",
                "skip_if_exists": False,
                "use_http_dl": True,
                "use_curl_cffi": False,
                "parallel_chunks": 1,
                "chunk_size_mb": CHUNK // (1024 * 1024),
                "auto_chunk_size": False,
                "use_ramdisk_stage": False,
                "verify_integrity": False,
                "verify_hash": False,
                "max_mbps": 0,
            }
            self.jobs = {}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()
            self.log = logging.getLogger("row716")
            self._throughput_samples = 0
            self._throughput_ewma_bps = 0.0
            self.job_updates = []
            self.failures = []
            self.events = []
            self.attempts = []
            self.pw_saves = 0
            self._recent_completions = deque()
            self._recent_per_min = 0.0

        def log_event(self, kind, message, url=None, extra=None):
            self.events.append((kind, message))

        def _update_job(self, url, status, message, **extra):
            self.job_updates.append((url, status, message, extra))

        def _handle_failure(self, url, message, screenshot=""):
            self.failures.append((url, message))
            self._update_job(url, "failed", message)

        def _screenshot(self, page, url):
            return ""

        def _probe_for_higher_tier(self, file_url, referer=""):
            return file_url

        def _build_mirror_urls(self, file_url):
            return list(mirrors)

        def _pick_fastest_mirror(self, file_url):
            return file_url

        def _download_proxy_url(self):
            return None

        def _recommended_chunk_bytes(self):
            return CHUNK

        def _current_cap_mbps(self):
            return 0

        def _start_daily_byte_accumulator(self):
            return None

        def _finish_daily_byte_accumulator(self, accumulator):
            return None

        def _http_download(self, page_url, page, ctx, file_url, final_path,
                           **kwargs):
            self.attempts.append((file_url, dict(kwargs)))
            if first_attempt is not None and len(self.attempts) == 1:
                return first_attempt(self, page_url, file_url, final_path)
            return super()._http_download(page_url, page, ctx, file_url,
                                          final_path, **kwargs)

        def _pw_save(self, dl, final_path):
            self.pw_saves += 1
            Path(final_path).write_bytes(b"browser-ok")
            return len(b"browser-ok"), len(b"browser-ok")

        def _embed_metadata_if_mp4(self, *args, **kwargs):
            return None

        def _size_on_disk_after_tagging(self, final_path, fallback):
            return Path(final_path).stat().st_size

    return _R()


def _patch_boundaries(monkeypatch, logs):
    import importlib
    from bulk_downloader import runner_transport as rt

    hooks = importlib.import_module("bulk_downloader.hooks")
    monkeypatch.setattr(
        rt, "gate_candidate_url",
        lambda locator, page_url, **kwargs: (locator.href, None))
    monkeypatch.setattr(rt, "db_skip_identity",
                        lambda page_url, final_path: ("different", ""))
    monkeypatch.setattr(rt, "history_title_kwargs", lambda runner, url: {})
    monkeypatch.setattr(rt, "db_log", lambda *a, **k: logs.append(a))
    monkeypatch.setattr(hooks, "fire_event", lambda *a, **k: None)


# --- 1. the fixture really built the shape, measured before any verdict -----

def test_the_swap_is_host_only_and_the_two_spellings_are_one_origin(origin):
    """The precondition the whole row rests on, measured, not assumed."""
    primary = urlsplit(origin["primary"] + "/scene.mp4")
    mirror = urlsplit(origin["mirror"] + "/scene.mp4")

    assert primary.scheme == mirror.scheme
    assert primary.path == mirror.path == "/scene.mp4"
    assert primary.query == mirror.query == ""
    assert primary.netloc != mirror.netloc, (
        "the two URLs are identical, so nothing was swapped and this file "
        f"measures nothing: {primary.netloc}")
    assert primary.port == mirror.port == origin["port"], (
        "the swap moved the port as well as the host, so it is not the "
        "host-only swap `_build_mirror_urls` produces")

    # ... and the provenance layer calls them DIFFERENT resources. That is the
    # mechanism, not a flaw in this test: it is why the chain breaks today.
    assert (_resource_identity_for_test(origin["primary"] + "/scene.mp4")
            != _resource_identity_for_test(origin["mirror"] + "/scene.mp4")), (
        "the two hosts hash to one resource identity, so no mismatch could "
        "ever fire and the defect this row names is not reproducible here")


def test_the_mirror_builder_only_ever_changes_the_host():
    """The fix's whole licence: the alternates differ in netloc ALONE.

    If this ever stops holding, carrying the canonical resource url down the
    chain would start claiming a DIFFERENT media object under the first url's
    provenance -- which is the splice the guard exists to stop.
    """
    from bulk_downloader.runner_telemetry import TelemetryMixin

    class _M(TelemetryMixin):
        config = {"mirror_subdomains": "cdn2,cdn3,media.other.invalid"}

    source = "https://cdn1.example.invalid/a/b/scene.mp4?token=abc#frag"
    alternates = _M()._build_mirror_urls(source)
    assert len(alternates) == 3, alternates

    src = urlsplit(source)
    for alt in alternates:
        got = urlsplit(alt)
        assert got.netloc != src.netloc, alt
        assert (got.scheme, got.path, got.query, got.fragment) == (
            src.scheme, src.path, src.query, src.fragment), (
            f"the mirror builder changed more than the host: {alt}")


# --- 2. THE CONTRACT: a host-only swap resumes instead of abandoning --------

@pytest.fixture
def host_swap_chain(origin, tmp_path, monkeypatch):
    """Primary host dies with a megabyte staged; the chain swaps hosts."""
    from bulk_downloader import runner_transport as rt
    from bulk_downloader import staging_claim as sc

    out = {"unknown": None}
    try:
        with origin["handler"].lock:
            start = len(origin["handler"].requests)

        canonical = origin["primary"] + "/scene.mp4"
        swapped = origin["mirror"] + "/scene.mp4"
        page_url = "https://example.invalid/mirror-swap"
        final = tmp_path / "scene.mp4"

        def _die_after_a_chunk(runner, page, file_url, final_path):
            # A CDN host that accepted the transfer, staged a chunk under its
            # own url, and then dropped the connection.
            staging = sc.claim(final_path, sc.job_identity(page),
                               resource_url=file_url)
            staging.write_bytes(BODY[:HOLD])
            raise rt._HTTPDownloadFailed(
                "synthetic primary-host transfer failure")

        logs = []
        _patch_boundaries(monkeypatch, logs)
        runner = _runner(mirrors=(swapped,), first_attempt=_die_after_a_chunk)
        staging = sc.staging_path_for(final)

        try:
            runner._do_download(_Page(canonical), _Ctx(), page_url,
                                _best(canonical), tmp_path, "1080p")
            out["raised"] = None
        except BaseException as exc:              # noqa: BLE001 - measured
            out["raised"] = f"{type(exc).__name__}: {exc}"

        with origin["handler"].lock:
            requests = list(origin["handler"].requests[start:])
        out["attempts"] = [a[0] for a in runner.attempts]
        out["attempt_kwargs"] = [a[1] for a in runner.attempts]
        out["swap_ranges"] = [
            r for r in requests
            if r["range"] is not None and r["host"].startswith("localhost")]
        out["statuses"] = [u[1] for u in runner.job_updates]
        out["notes"] = [u[2] for u in runner.job_updates]
        out["pw_saves"] = runner.pw_saves
        out["staged_left"] = staging.exists()
        promoted = sorted(p for p in tmp_path.iterdir() if p.suffix == ".mp4")
        out["promoted"] = [p.name for p in promoted]
        if promoted:
            blob = promoted[0].read_bytes()
            out["size"] = len(blob)
            out["digest"] = hashlib.sha256(blob).hexdigest()
            out["census"] = _byte_census(blob)
    except BaseException as exc:                  # noqa: BLE001 - recorded
        out["unknown"] = f"{type(exc).__name__}: {exc}"
    return out


def _assert_chain_preconditions(r):
    assert r["unknown"] is None, r["unknown"]
    assert len(r["attempts"]) == 2, (
        "the chain did not reach the mirror at all, so this fixture never "
        f"built the shape it measures: attempts={r['attempts']}")
    assert r["attempts"][0].startswith("http://127.0.0.1:"), r["attempts"]
    assert r["attempts"][1].startswith("http://localhost:"), r["attempts"]


def test_the_mirror_chain_measured_its_preconditions(host_swap_chain):
    _assert_chain_preconditions(host_swap_chain)


def test_a_host_only_mirror_swap_resumes_instead_of_abandoning_the_chain(
        host_swap_chain):
    """THE contract: the same bytes, fetched from a second spelling of one
    origin, finish the download the first host left a megabyte into."""
    r = host_swap_chain
    _assert_chain_preconditions(r)

    assert "needs_review" not in r["statuses"], (
        "a same-bytes host-only mirror swap was refused and the chain was "
        f"abandoned with {HOLD} good bytes already staged: {r['notes']}")
    assert r["promoted"] == ["scene.mp4"], (
        f"the mirror attempt promoted nothing: {r['promoted']}")
    assert r["digest"] == hashlib.sha256(BODY).hexdigest(), (
        f"the promoted file is not the resource requested: {r.get('census')}")
    assert r["census"] == {BODY_BYTE: LEN_BODY}, r.get("census")
    assert r["staged_left"] is False, "the completed chain left its .part"


def test_the_swap_resumes_from_the_staged_offset_exactly_once(
        host_swap_chain):
    """The exact-count assertion.

    A swap that restarted at byte 0 would also end in a correct file, so the
    count and the offset are what separate a RESUME from a silent re-download
    of the megabyte already on disk.
    """
    r = host_swap_chain
    _assert_chain_preconditions(r)
    assert len(r["swap_ranges"]) == 1, (
        f"expected exactly one Range request on the swapped host, found "
        f"{len(r['swap_ranges'])}: {r['swap_ranges']}")
    assert r["swap_ranges"][0]["start"] == HOLD, r["swap_ranges"][0]
    assert r["swap_ranges"][0]["status"] == 206, r["swap_ranges"][0]


# --- 3. the counterpart, proven together: a live collision still refuses ----

def test_a_live_part_owned_by_another_job_still_refuses(
        origin, tmp_path, monkeypatch):
    """The half that must NOT change. A different JOB, not a different host."""
    from bulk_downloader import staging_claim as sc

    canonical = origin["primary"] + "/scene.mp4"
    page_url = "https://example.invalid/contended"
    final = tmp_path / "scene.mp4"

    other = sc.job_identity("https://example.invalid/some-other-page")
    staging = sc.claim(final, other, resource_url=canonical)
    staging.write_bytes(bytes([OTHER_BYTE]) * HOLD)
    census_before = _byte_census(staging.read_bytes())

    logs = []
    _patch_boundaries(monkeypatch, logs)
    # reserve() is a different, pre-existing mechanism (row 481/541: atomic
    # filename-collision avoidance, replacing safe_dest's TOCTOU race): on
    # ANY other-job claim it renames to the next numbered candidate, and only
    # raises once every candidate -- X, X_1 .. X_999, the random suffix -- is
    # also taken. That is reserve()'s own real terminal path (its docstring's
    # "no free staging path" branch); simulate landing there directly rather
    # than actually exhausting 1000 candidates, so this test reaches the
    # collision it is about (the deeper, resource-aware claim() row 716
    # covers) without asserting anything reserve() itself does not already
    # document.
    def _reserve_no_room(final_path, identity):
        try:
            return Path(final_path), sc.claim(final_path, identity)
        except sc.StagingClaimedByAnotherJob:
            raise sc.StagingUnavailable(
                f"no free staging path for {final_path}: every candidate "
                "name is claimed by another download, so this transfer "
                "refuses rather than share a staging file")
    monkeypatch.setattr(sc, "reserve", _reserve_no_room)
    runner = _runner(mirrors=(origin["mirror"] + "/scene.mp4",))
    runner._do_download(_Page(canonical), _Ctx(), page_url, _best(canonical),
                        tmp_path, "1080p")

    needs_review = [u for u in runner.job_updates if u[1] == "needs_review"]
    assert len(needs_review) == 1, (
        f"a live collision produced {len(needs_review)} needs_review rows: "
        f"{runner.job_updates}")
    assert "staging unavailable" in needs_review[0][2], needs_review[0]
    assert "claimed by another download" in needs_review[0][2], (
        "the operator's note does not name WHY the staging path was "
        f"unavailable: {needs_review[0][2]}")
    assert [row[3] for row in logs].count("needs_review") == 1, logs
    assert runner.pw_saves == 0, (
        "the refusal fell through to the Playwright fallback, which would "
        "write the browser's bytes to the very path the refusal protects")
    assert _byte_census(staging.read_bytes()) == census_before, (
        "the refusal altered the other job's staged bytes")
    assert not (tmp_path / "scene.mp4").exists()


def test_the_live_collision_keeps_its_raise_site_discriminator(
        origin, tmp_path):
    """``_StagingUnavailable`` must still say which staging fault it was."""
    from bulk_downloader import staging_claim as sc

    canonical = origin["primary"] + "/scene.mp4"
    final = tmp_path / "Scene.mp4"
    other = sc.job_identity("https://example.invalid/some-other-page")
    sc.claim(final, other, resource_url=canonical).write_bytes(
        bytes([OTHER_BYTE]) * HOLD)

    runner = _runner()
    raised = None
    try:
        runner._http_download("https://example.invalid/contended", None,
                              _Ctx(), canonical, final)
    except BaseException as exc:                  # noqa: BLE001 - measured
        raised = exc

    assert raised is not None, "a second job transferred into a live .part"
    assert type(raised).__name__ == "_StagingUnavailable", repr(raised)
    assert _discriminator(raised) == "StagingClaimedByAnotherJob", (
        "the raise-site discriminator was not retained: the caller cannot "
        f"tell a live collision from a resource mismatch, it saw "
        f"{_discriminator(raised)}")


# --- 4. the negative control: the provenance guard is NOT disabled ----------

def test_a_genuinely_different_resource_on_one_host_still_refuses(
        origin, tmp_path):
    """Negative control, failing for the intended reason.

    Same host, DIFFERENT path -- a real second media object. Had row 716 been
    "fixed" by relaxing ``resource_identity`` or by retrying through every
    mismatch, this would pass a transfer that splices two files together.
    """
    from bulk_downloader import staging_claim as sc

    page_url = "https://example.invalid/changed-resource"
    identity = sc.job_identity(page_url)
    final = tmp_path / "Scene.mp4"
    first_url = origin["primary"] + "/scene.mp4"
    second_url = origin["primary"] + "/other-scene.mp4"

    staging = sc.claim(final, identity, resource_url=first_url)
    staging.write_bytes(BODY[:HOLD])
    census_before = _byte_census(staging.read_bytes())

    runner = _runner()
    raised = None
    try:
        runner._http_download(page_url, None, _Ctx(), second_url, final)
    except BaseException as exc:                  # noqa: BLE001 - measured
        raised = exc

    assert raised is not None, (
        "a different media object was appended to the first resource's bytes")
    assert type(raised).__name__ == "_StagingUnavailable", repr(raised)
    assert "resource mismatch" in str(raised).lower(), str(raised)
    assert _discriminator(raised) == "StagingResourceMismatch", (
        f"the raise-site discriminator was not retained: "
        f"{_discriminator(raised)}")
    assert _byte_census(staging.read_bytes()) == census_before, (
        "a resource mismatch altered the staged bytes it refused")
    assert not final.exists()


def test_the_control_really_names_a_second_resource(origin):
    """The control's own positive control: the two urls differ by the shipped
    rule, not by this test's opinion, and they differ in PATH not host."""
    first = origin["primary"] + "/scene.mp4"
    second = origin["primary"] + "/other-scene.mp4"
    assert urlsplit(first).netloc == urlsplit(second).netloc, (
        "the control changed the host, so it is testing the mirror case")
    assert (_resource_identity_for_test(first)
            != _resource_identity_for_test(second))
