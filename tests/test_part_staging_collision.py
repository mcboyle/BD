"""part-staging-collision -- two downloads, one .part, one spliced file marked done.

MEASURED at v3.66.1362. ``runner_transport._http_download`` derived its staging
path from the final path alone::

    tmp_path = final_path.with_suffix(final_path.suffix + ".part")

and the only collision handling on the whole path was ``detect.safe_dest``,
which probes for an existing FINAL file and hands back a free name. Two workers
asking that question a millisecond apart both get the same answer, because the
first one has not promoted anything yet. A site runs ``max_concurrent`` worker
threads (``DEFAULT_MAX_CONCURRENT`` = 2, one thread per slot) and every site
without its own ``download_dir`` resolves to the shared deployment default, so
two jobs whose filename templates render the same string is routine.

THE FAILURE, byte for byte:

    worker A  opens X.mp4.part "wb" and streams scene A
    worker B  computes the same X.mp4.part (X.mp4 does not exist yet),
              reads stat(X.mp4.part).st_size > 0, calls that a resume,
              sends Range: bytes=N- for SCENE B's url, is answered 206,
              opens the SAME X.mp4.part in "ab",
              appends scene B's bytes onto scene A's, and promotes the
              concatenation to `done`.

The ``.part.meta`` validator sidecar is not a defence. It only exists when the
origin sent an ETag or Last-Modified, and this gate serves neither -- which is
the ordinary case for a plain byte-range CDN and reproduces the clean
206-then-append shape above. When the sidecar IS present the If-Range mismatch
turns the append into a ``wb`` TRUNCATE of a file another worker still holds an
open descriptor on, which is the same corruption wearing a different hat.

WHAT THIS GATE ASSERTS

  1. The precondition is real: two colliding jobs derive the SAME staging path,
     and ``safe_dest`` hands both of them the same final name.
  2. A second job may not stage into a ``.part`` a live download owns. It is
     refused, with a distinctive diagnostic, and NOT ONE BYTE of scene B
     reaches scene A's staging file. Scene A then completes intact.
  3. THE NEGATIVE CONTROL, and it is load-bearing: a genuinely interrupted
     download of the SAME job must still resume from its own ``.part``. Fixing
     the collision by forcing every download to restart would trade one defect
     for another, so the control pins exact byte counts -- N staged, LEN-N
     transferred on the second attempt, LEN on disk, one Range request.
  4. UNKNOWN is not permission: ownership that cannot be MEASURED refuses the
     transfer instead of proceeding against an unreserved path.

THE RACE IS DRIVEN BY A REAL SYNCHRONISATION SEAM, never by sleeps. The fixture
origin holds scene A's response open on a ``threading.Event`` after a known
number of bytes, and the harness signals the main thread from
``_flush_after_interrupted_write`` -- the production hook the transfer loop
calls after every ``f.write`` -- so "A has exactly N bytes staged" is a measured
fact at the moment B starts, not a hope about timing.

NO LIVE NETWORK: every byte comes from an ephemeral loopback origin, and the
transfer's own proxy resolution is asserted to be None so nothing can leave it.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

# The CI classifier parses a module-level ASSIGNMENT, not a docstring line.
# This gate's subject is the transport module's staging path, so the affected
# band selects it whenever that module changes.
BD_GATE_SCOPE = "module"

# One chunk of the transfer loop. `chunk_size_mb` is set to match, so httpx
# yields exactly this much per iteration and every write is far larger than the
# 8 KiB BufferedWriter buffer -- which is why the on-disk size at the hold
# point is an exact number rather than a buffering accident. The test asserts
# that exact size, so a buffering surprise fails loudly instead of silently
# weakening the reproduction.
CHUNK = 1024 * 1024

SCENE_A_BYTE = 0xAA
SCENE_B_BYTE = 0xBB
LEN_A = 3 * CHUNK
LEN_B = 2 * CHUNK
HOLD = CHUNK          # bytes scene A has staged when scene B starts

BODY_A = bytes([SCENE_A_BYTE]) * LEN_A
BODY_B = bytes([SCENE_B_BYTE]) * LEN_B


# ── the fixture origin: byte ranges, no validators, on loopback only ─────────

class _Origin(BaseHTTPRequestHandler):
    """Serves two bodies with Range support and NO ETag / Last-Modified.

    The absent validators are deliberate and are asserted by the tests: they
    are what makes a resume optimistic, which is the exact shape of the
    measured defect.
    """
    protocol_version = "HTTP/1.1"

    bodies: dict = {}
    holds: dict = {}
    requests: list = []
    lock = threading.Lock()

    def do_GET(self):  # noqa: N802 - stdlib handler API
        path = urlsplit(self.path).path
        body = self.bodies.get(path)
        rng = self.headers.get("Range")
        if body is None:
            with self.lock:
                self.requests.append({"path": path, "range": rng, "status": 404})
            self.send_error(404)
            return
        start = 0
        status = 200
        if rng and rng.startswith("bytes="):
            start = int(rng[len("bytes="):].split("-")[0])
            status = 206
        payload = body[start:]
        with self.lock:
            self.requests.append({"path": path, "range": rng, "status": status,
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
        hold = self.holds.get(path)
        if hold is None:
            self.wfile.write(payload)
            return
        after, event = hold
        self.wfile.write(payload[:after])
        self.wfile.flush()
        # The seam. No sleep: the main thread releases this when it has
        # finished measuring.
        event.wait(timeout=60)
        self.wfile.write(payload[after:])

    def log_message(self, _fmt, *_args):
        return


# ── the transfer harness ─────────────────────────────────────────────────────

class _Ctx:
    """The only thing the sequential transfer asks of a Playwright context."""

    def cookies(self):
        return []


def _harness(site_id, after_write=None):
    """A TransportMixin the transfer runs against unmodified.

    ``_start_daily_byte_accumulator`` is neutralised because a daily-budget
    accumulator would reach the real database, and None is a state production
    itself produces whenever ``daily_budget`` is unavailable. Nothing on the
    staging path is stubbed.
    """
    from bulk_downloader.runner_transport import TransportMixin

    class _H(TransportMixin):
        def __init__(self):
            self.site_id = site_id
            self.config = {
                # httpx rather than curl_cffi so the chunking is the exact
                # documented iter_bytes contract this gate's byte counts rely
                # on. The defect is transport-agnostic; the determinism is not.
                "use_curl_cffi": False,
                "chunk_size_mb": CHUNK // (1024 * 1024),
                "auto_chunk_size": False,
                "parallel_chunks": 1,
                "use_ramdisk_stage": False,
                "max_mbps": 0,
            }
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()
            self._lock = threading.RLock()
            self.jobs = {}
            self.log = logging.getLogger("part-staging-collision")
            self._throughput_samples = 0
            self._throughput_ewma_bps = 0.0
            self.events = []
            self.job_updates = []

        def log_event(self, kind, message, url=None, extra=None):
            self.events.append((kind, message))

        def _update_job(self, url, status, message, **extra):
            self.job_updates.append((url, status, message))

        def _pick_fastest_mirror(self, file_url):
            return file_url

        def _start_daily_byte_accumulator(self):
            return None

        def _finish_daily_byte_accumulator(self, accumulator):
            return None

        def _flush_after_interrupted_write(self, accumulator, local_stop=None):
            keep_going = TransportMixin._flush_after_interrupted_write(
                self, accumulator, local_stop)
            if after_write is not None:
                after_write(self)
            return keep_going and not self._stop.is_set()

    return _H()


def _byte_census(blob):
    """{byte value: count} -- the whole point is that exactly one value appears."""
    return {b: blob.count(bytes([b])) for b in sorted(set(blob))}


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    hold_event = threading.Event()
    handler = type("_CollisionOrigin", (_Origin,), {
        "bodies": {"/scene-a.mp4": BODY_A, "/scene-b.mp4": BODY_B},
        "holds": {"/scene-a.mp4": (HOLD, hold_event)},
        "requests": [],
        "lock": threading.Lock(),
    })
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"base": base, "handler": handler, "hold": hold_event}
    finally:
        hold_event.set()
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=10)


# ── 1. the precondition: the collision is real, not exotic ───────────────────

def test_two_colliding_jobs_derive_one_staging_path(tmp_path):
    """The race needs two jobs to agree on one final name AND one .part name.

    ``safe_dest`` answers "is this final name free right now", and while the
    first download is still staging, the answer for the second one is yes.
    """
    from bulk_downloader.detect import safe_dest
    from bulk_downloader.staging_claim import staging_path_for

    final = tmp_path / "Scene 12 [1080p].mp4"
    assert not final.exists(), "precondition: nothing has been promoted yet"
    assert safe_dest(final) == final
    assert safe_dest(final) == final, (
        "safe_dest is check-then-act: asked twice with no file created "
        "between, it hands the same name to both callers")

    # And the staging derivation is a pure function of that name, so both
    # callers land on one .part.
    assert staging_path_for(safe_dest(final)) == staging_path_for(final)
    assert staging_path_for(final).name == "Scene 12 [1080p].mp4.part"


def test_the_staging_derivation_matches_the_transport_source(tmp_path):
    """One definition, or the reservation guards a path the writer never uses.

    ``staging_path_for`` must reproduce the transport's historical expression
    exactly, including on names the ``with_suffix`` form treats oddly.
    """
    from bulk_downloader.staging_claim import staging_path_for

    for name in ("a.mp4", "a.b.mp4", "noext", ".hidden", "a .mp4", "a_1.mp4"):
        final = tmp_path / name
        assert staging_path_for(final) == final.with_suffix(
            final.suffix + ".part"), name


# ── 2. two live downloads, one staging path ──────────────────────────────────

@pytest.fixture(scope="module")
def collision(origin, tmp_path_factory):
    """Run the race once and hand back measurements. Never raises: a failure
    is recorded as UNKNOWN and the tests fail on it by name."""
    from bulk_downloader.constants import _HTTPDownloadFailed  # noqa: F401
    from bulk_downloader.detect import safe_dest
    from bulk_downloader.staging_claim import staging_path_for

    out = {
        "unknown": None, "final_a": None, "final_b": None, "staging": None,
        "meta_existed": None, "part_bytes_when_b_started": None,
        "part_census_when_b_started": None, "b_outcome": None,
        "b_error_type": None, "b_error": None,
        "part_bytes_after_b": None, "part_census_after_b": None,
        "final_exists_after_b": None, "final_bytes_after_b": None,
        "final_census_after_b": None, "a_result": None,
        "final_a_bytes": None, "final_a_census": None, "proxy_a": "unset",
        "requests": None,
    }
    try:
        dl_dir = Path(tmp_path_factory.mktemp("collision"))
        # Both jobs render the same template output -- the whole premise.
        final_a = safe_dest(dl_dir / "Scene 12 [1080p].mp4")
        staging = staging_path_for(final_a)
        out["final_a"] = str(final_a)
        out["staging"] = str(staging)

        wrote_first_chunk = threading.Event()

        def _signal(_h):
            wrote_first_chunk.set()

        h_a = _harness("scene-a", after_write=_signal)
        out["proxy_a"] = h_a._download_proxy_url()
        a_box = {}

        def _run_a():
            try:
                a_box["result"] = h_a._http_download(
                    "https://example.invalid/scene-a", None, _Ctx(),
                    origin["base"] + "/scene-a.mp4", final_a)
            except BaseException as exc:            # noqa: BLE001 - recorded
                a_box["error"] = f"{type(exc).__name__}: {exc}"

        t_a = threading.Thread(target=_run_a, daemon=True)
        t_a.start()
        if not wrote_first_chunk.wait(timeout=60):
            out["unknown"] = "scene A never reached its first buffered write"
            return out

        # A is now blocked inside the transfer loop with HOLD bytes staged.
        out["part_bytes_when_b_started"] = staging.stat().st_size
        out["part_census_when_b_started"] = _byte_census(staging.read_bytes())
        out["meta_existed"] = Path(str(staging) + ".meta").exists()

        # Worker B asks the same question A asked and gets the same answer.
        final_b = safe_dest(dl_dir / "Scene 12 [1080p].mp4")
        out["final_b"] = str(final_b)
        h_b = _harness("scene-b")
        try:
            h_b._http_download(
                "https://example.invalid/scene-b", None, _Ctx(),
                origin["base"] + "/scene-b.mp4", final_b)
            out["b_outcome"] = "completed"
        except BaseException as exc:                # noqa: BLE001 - recorded
            out["b_outcome"] = "refused"
            out["b_error_type"] = type(exc).__name__
            out["b_error"] = str(exc)

        out["part_bytes_after_b"] = (
            staging.stat().st_size if staging.exists() else None)
        out["part_census_after_b"] = (
            _byte_census(staging.read_bytes()) if staging.exists() else None)
        out["final_exists_after_b"] = final_a.exists()
        if final_a.exists():
            blob = final_a.read_bytes()
            out["final_bytes_after_b"] = len(blob)
            out["final_census_after_b"] = _byte_census(blob)

        # Only now let scene A finish, so nothing above raced the measurement.
        origin["hold"].set()
        t_a.join(timeout=60)
        out["a_result"] = a_box.get("result") or a_box.get("error")
        if final_a.exists():
            blob = final_a.read_bytes()
            out["final_a_bytes"] = len(blob)
            out["final_a_census"] = _byte_census(blob)
        with origin["handler"].lock:
            out["requests"] = list(origin["handler"].requests)
    except BaseException as exc:                    # noqa: BLE001 - recorded
        out["unknown"] = f"{type(exc).__name__}: {exc}"
    return out


def test_the_collision_run_measured_its_preconditions(collision):
    """UNKNOWN is not a pass: the race must be proved to have happened."""
    c = collision
    assert c["unknown"] is None, c["unknown"]
    assert c["proxy_a"] is None, (
        "the transfer resolved a proxy; this gate must stay on loopback")
    assert c["final_a"] == c["final_b"], (
        "the two jobs did not resolve the same final path, so nothing "
        f"collided: {c['final_a']} vs {c['final_b']}")
    assert c["part_bytes_when_b_started"] == HOLD, (
        f"scene A had {c['part_bytes_when_b_started']} bytes staged when "
        f"scene B started, not {HOLD}; the reproduction is not the measured "
        "one")
    assert c["part_census_when_b_started"] == {SCENE_A_BYTE: HOLD}
    assert c["meta_existed"] is False, (
        "a .part.meta sidecar existed, so scene B's resume would have been "
        "validated by If-Range rather than optimistic; that is a different "
        "case from the measured defect")


def test_a_second_job_cannot_stage_into_a_live_part_file(collision):
    """THE contract. Not one byte of scene B may reach scene A's .part."""
    c = collision
    assert c["unknown"] is None, c["unknown"]
    assert c["b_outcome"] == "refused", (
        "scene B completed a transfer into scene A's staging file. Promoted "
        f"file: {c['final_bytes_after_b']} bytes, census "
        f"{c['final_census_after_b']} -- two scenes spliced into one file "
        "and recorded as done")
    assert c["b_error_type"] == "_StagingUnavailable", (
        f"scene B failed as {c['b_error_type']}, which is not the staging "
        "refusal; a refusal for an unrelated reason would launder this result")
    assert "claimed by a different download" in (c["b_error"] or ""), (
        f"the refusal did not name its cause: {c['b_error']!r}")
    assert c["part_bytes_after_b"] == HOLD, (
        f"scene A's staging file is {c['part_bytes_after_b']} bytes after "
        f"scene B ran, not the {HOLD} it had staged")
    assert c["part_census_after_b"] == {SCENE_A_BYTE: HOLD}, (
        f"scene B's bytes are in scene A's staging file: "
        f"{c['part_census_after_b']}")
    assert c["final_exists_after_b"] is False, (
        "something was promoted while scene A was still streaming")


def test_the_held_download_completes_intact(collision):
    """The refusal must not have cost scene A its own download."""
    c = collision
    assert c["unknown"] is None, c["unknown"]
    assert c["a_result"] == (LEN_A, LEN_A), (
        f"scene A returned {c['a_result']!r}, not ({LEN_A}, {LEN_A})")
    assert c["final_a_bytes"] == LEN_A
    assert c["final_a_census"] == {SCENE_A_BYTE: LEN_A}, (
        f"scene A's promoted file is not pure scene A: {c['final_a_census']}")


def test_the_origin_never_served_a_range_for_the_second_scene(collision):
    """The wire-level statement of the same contract."""
    c = collision
    assert c["unknown"] is None, c["unknown"]
    reqs = c["requests"] or []
    assert reqs, "UNKNOWN: the origin recorded no requests at all"
    a_reqs = [r for r in reqs if r["path"] == "/scene-a.mp4"]
    b_reqs = [r for r in reqs if r["path"] == "/scene-b.mp4"]
    assert len(a_reqs) == 1 and a_reqs[0]["range"] is None, (
        f"scene A should have been fetched once, without a Range: {a_reqs}")
    assert not [r for r in b_reqs if r["range"]], (
        "scene B sent a Range request, which is the resume branch taken "
        f"against another job's staging file: {b_reqs}")


# ── 3. THE NEGATIVE CONTROL: a genuine resume must still resume ──────────────

@pytest.fixture(scope="module")
def resumed(origin, tmp_path_factory):
    """Interrupt one download for real, restart the SAME job, measure bytes."""
    from bulk_downloader.detect import safe_dest
    from bulk_downloader.staging_claim import staging_path_for

    out = {"unknown": None, "first_error": None, "staged_bytes": None,
           "staged_census": None, "owner_existed": None, "second_result": None,
           "final_path": None, "final_bytes": None, "final_census": None,
           "range_requests": None}
    try:
        dl_dir = Path(tmp_path_factory.mktemp("resume"))
        page_url = "https://example.invalid/scene-a"
        final = safe_dest(dl_dir / "Interrupted.mp4")
        staging = staging_path_for(final)

        # Attempt one: the operator presses stop after the first chunk. This
        # is a production interruption path, not a fabricated .part.
        def _stop_after_first(h):
            h._stop.set()

        h1 = _harness("resume-1", after_write=_stop_after_first)
        try:
            h1._http_download(page_url, None, _Ctx(),
                              origin["base"] + "/scene-b.mp4", final)
            out["first_error"] = "the interrupted attempt completed"
        except BaseException as exc:                # noqa: BLE001 - recorded
            out["first_error"] = f"{type(exc).__name__}: {exc}"
        out["staged_bytes"] = staging.stat().st_size if staging.exists() else None
        out["staged_census"] = (
            _byte_census(staging.read_bytes()) if staging.exists() else None)
        out["owner_existed"] = Path(str(staging) + ".owner").exists()

        before = len([r for r in origin["handler"].requests if r["range"]])

        # Attempt two: same job, same url, same destination.
        h2 = _harness("resume-2")
        out["second_result"] = h2._http_download(
            page_url, None, _Ctx(), origin["base"] + "/scene-b.mp4", final)
        out["final_path"] = str(final)
        if final.exists():
            blob = final.read_bytes()
            out["final_bytes"] = len(blob)
            out["final_census"] = _byte_census(blob)
        with origin["handler"].lock:
            after = [r for r in origin["handler"].requests if r["range"]]
        out["range_requests"] = after[before:]
    except BaseException as exc:                    # noqa: BLE001 - recorded
        out["unknown"] = f"{type(exc).__name__}: {exc}"
    return out


def test_an_interrupted_download_leaves_exactly_one_staged_chunk(resumed):
    """Precondition for the control -- and proof the interruption was real."""
    r = resumed
    assert r["unknown"] is None, r["unknown"]
    assert "stopped" in (r["first_error"] or ""), (
        f"the first attempt did not stop: {r['first_error']!r}")
    assert r["staged_bytes"] == CHUNK, (
        f"the interrupted attempt left {r['staged_bytes']} staged bytes, "
        f"not {CHUNK}")
    assert r["staged_census"] == {SCENE_B_BYTE: CHUNK}


def test_the_same_job_resumes_its_own_part_and_is_not_restarted(resumed):
    """Load-bearing. Forcing a restart here would trade one defect for another.

    The exact counts are the evidence: CHUNK bytes were already on disk, so a
    correct resume transfers LEN_B - CHUNK and lands LEN_B on disk, under the
    ORIGINAL filename. A forced restart would transfer LEN_B, and a diverted
    reservation would write Interrupted_1.mp4.
    """
    r = resumed
    assert r["unknown"] is None, r["unknown"]
    assert r["owner_existed"] is True, (
        "the interrupted attempt released its claim, so the restart could "
        "not have proved the .part was its own")
    assert r["second_result"] == (LEN_B, LEN_B - CHUNK), (
        f"the resumed attempt returned {r['second_result']!r}; "
        f"({LEN_B}, {LEN_B - CHUNK}) is a resume, "
        f"({LEN_B}, {LEN_B}) is a restart")
    assert Path(r["final_path"]).name == "Interrupted.mp4", (
        f"the resume was diverted to {Path(r['final_path']).name}")
    assert r["final_bytes"] == LEN_B
    assert r["final_census"] == {SCENE_B_BYTE: LEN_B}
    ranges = r["range_requests"] or []
    assert len(ranges) == 1, (
        f"expected exactly one Range request on the resume: {ranges}")
    assert ranges[0]["range"] == f"bytes={CHUNK}-"
    assert ranges[0]["status"] == 206


def test_a_completed_download_leaves_no_claim_behind(resumed):
    """The claim's lifetime is the .part's lifetime."""
    r = resumed
    assert r["unknown"] is None, r["unknown"]
    from bulk_downloader.staging_claim import owner_path_for, staging_path_for
    final = Path(r["final_path"])
    staging = staging_path_for(final)
    assert not staging.exists(), "the .part outlived its promotion"
    assert not owner_path_for(staging).exists(), (
        "the staging claim outlived the .part it guarded")


# ── 4. UNKNOWN is not permission ─────────────────────────────────────────────

def test_an_unreadable_claim_is_unknown_not_free(tmp_path):
    """A claim that cannot be read must not be treated as absent."""
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "x.mp4"
    owner = sc.owner_path_for(sc.staging_path_for(final))
    owner.mkdir()          # exists, and reading it raises IsADirectoryError
    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.claim(final, sc.job_identity("https://example.invalid/x"))
    assert str(owner) in str(exc.value)
    assert not sc.staging_path_for(final).exists(), (
        "the refused claim created a staging file anyway")


def test_a_malformed_claim_is_unknown_not_free(tmp_path):
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "x.mp4"
    owner = sc.owner_path_for(sc.staging_path_for(final))
    owner.write_text("not json at all", encoding="utf-8")
    with pytest.raises(sc.StagingUnavailable):
        sc.claim(final, sc.job_identity("https://example.invalid/x"))

    owner.write_text(json.dumps({"v": 1, "job": "short"}), encoding="utf-8")
    with pytest.raises(sc.StagingUnavailable):
        sc.claim(final, sc.job_identity("https://example.invalid/x"))


def test_a_claim_that_cannot_be_created_is_unknown_not_free(tmp_path,
                                                            monkeypatch):
    """Anything other than "it already exists" is unmeasurable, so it refuses.

    Driven through ``os.open`` rather than a chmod so the result does not
    depend on whether the lane runs as root.
    """
    from bulk_downloader import staging_claim as sc

    real_open = os.open

    def _deny(path, flags, *a, **kw):
        # The claim is written to a private temp name before it is published,
        # so the seam is every path carrying the owner suffix, not just the
        # published one.
        if sc.OWNER_SUFFIX in str(path):
            raise PermissionError(13, "Permission denied")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(sc.os, "open", _deny)
    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.claim(tmp_path / "x.mp4", sc.job_identity("https://e.invalid/x"))
    assert "PermissionError" in str(exc.value)
    assert not list(tmp_path.iterdir()), (
        f"the refused claim left files behind: {list(tmp_path.iterdir())}")


def test_a_reservation_that_cannot_be_measured_never_silently_diverts(
        tmp_path, monkeypatch):
    """``reserve`` walks past a name another job owns, but not past UNKNOWN."""
    from bulk_downloader import staging_claim as sc

    real_open = os.open
    calls = {"n": 0}

    def _deny_second(path, flags, *a, **kw):
        if sc.OWNER_SUFFIX in str(path):
            calls["n"] += 1
            if calls["n"] > 1:
                raise OSError(28, "No space left on device")
        return real_open(path, flags, *a, **kw)

    # First candidate: taken by somebody else. That is a DETERMINATE answer --
    # the claim is read, it names another identity -- so reserve walks past it.
    owner = sc.owner_path_for(sc.staging_path_for(tmp_path / "x.mp4"))
    owner.write_text(json.dumps({"v": 1, "job": "b" * 64}), encoding="utf-8")
    monkeypatch.setattr(sc.os, "open", _deny_second)
    with pytest.raises(sc.StagingUnavailable) as exc:
        sc.reserve(tmp_path / "x.mp4", sc.job_identity("https://e.invalid/x"))
    assert "No space left" in str(exc.value)
    assert calls["n"] == 2, (
        f"reserve made {calls['n']} claim attempts; it must try the taken "
        "name, learn it is taken, and stop at the unmeasurable one rather "
        "than walking on to x_2.mp4")
    assert not (tmp_path / "x_1.mp4.part.owner").exists(), (
        "an unmeasurable candidate was diverted past instead of refused")


def test_the_no_hardlink_fallback_is_still_exclusive(tmp_path, monkeypatch):
    """Prove the second write path, not just the first.

    ``os.link`` is unavailable on a few filesystems a self-hosted download
    directory can sit on. The fallback must still hand exactly one of N racing
    workers the claim, or the fix works only on ext4.
    """
    from bulk_downloader import staging_claim as sc

    def _no_link(_src, _dst, **_kw):
        raise OSError(38, "Function not implemented")

    monkeypatch.setattr(sc.os, "link", _no_link)

    n = 12
    start = threading.Barrier(n)
    results = [None] * n

    def _worker(i):
        start.wait(timeout=30)
        try:
            results[i] = sc.claim(tmp_path / "Scene.mp4", sc.job_identity(
                f"https://example.invalid/{i}"))
        except sc.StagingClaimedByAnotherJob:
            results[i] = "lost"
        except BaseException as exc:                # noqa: BLE001
            results[i] = f"UNKNOWN: {type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    unknown = [r for r in results if isinstance(r, str) and r.startswith("UNKNOWN")]
    assert not unknown, unknown
    winners = [r for r in results if not isinstance(r, str)]
    assert len(winners) == 1, (
        f"{len(winners)} of {n} threads won one staging path on the fallback "
        "write path")
    assert results.count("lost") == n - 1
    # And no temporary residue survived the race.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert not leftovers, leftovers


# ── 5. the reservation itself ────────────────────────────────────────────────

def test_reserve_hands_two_jobs_two_names(tmp_path):
    from bulk_downloader import staging_claim as sc

    a = sc.job_identity("https://example.invalid/a")
    b = sc.job_identity("https://example.invalid/b")
    assert a != b
    final_a, stage_a = sc.reserve(tmp_path / "Scene.mp4", a)
    final_b, stage_b = sc.reserve(tmp_path / "Scene.mp4", b)
    assert final_a.name == "Scene.mp4"
    assert final_b.name == "Scene_1.mp4"
    assert stage_a != stage_b
    assert stage_a.name == "Scene.mp4.part"
    assert stage_b.name == "Scene_1.mp4.part"


def test_reserve_is_idempotent_for_one_job(tmp_path):
    """A restart reclaims its own name rather than drifting to Scene_1."""
    from bulk_downloader import staging_claim as sc

    ident = sc.job_identity("https://example.invalid/a")
    first = sc.reserve(tmp_path / "Scene.mp4", ident)
    second = sc.reserve(tmp_path / "Scene.mp4", ident)
    assert first == second


def test_the_claim_is_won_exactly_once_under_concurrency(tmp_path):
    """The mechanism, exercised directly: N threads, one winner per name."""
    from bulk_downloader import staging_claim as sc

    n = 16
    start = threading.Barrier(n)
    results = [None] * n

    def _worker(i):
        start.wait(timeout=30)
        try:
            results[i] = sc.claim(tmp_path / "Scene.mp4", sc.job_identity(
                f"https://example.invalid/{i}"))
        except sc.StagingClaimedByAnotherJob:
            results[i] = "lost"
        except BaseException as exc:                # noqa: BLE001
            results[i] = f"UNKNOWN: {type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    unknown = [r for r in results if isinstance(r, str) and r.startswith("UNKNOWN")]
    assert not unknown, unknown
    winners = [r for r in results if not isinstance(r, str)]
    assert len(winners) == 1, (
        f"{len(winners)} of {n} threads were told they own one staging path")
    assert results.count("lost") == n - 1


def test_release_drops_the_claim(tmp_path):
    from bulk_downloader import staging_claim as sc

    ident = sc.job_identity("https://example.invalid/a")
    _final, staging = sc.reserve(tmp_path / "Scene.mp4", ident)
    assert sc.owner_path_for(staging).exists()
    # Row 492: release() proves ownership now, so it is called with the identity
    # that HOLDS the claim. A different job's identity is refused, which the
    # sibling test test_release_refuses_a_claim_another_job_owns pins.
    assert sc.release(staging, ident) is True
    assert not sc.owner_path_for(staging).exists()
    assert sc.release(staging, ident) is True  # idempotent
    other = sc.job_identity("https://example.invalid/b")
    final_b, _ = sc.reserve(tmp_path / "Scene.mp4", other)
    assert final_b.name == "Scene.mp4", (
        "a released name must become available again")


# ── Row 481: bytes nobody claimed are not this job's bytes ──────────────────
#
# claim() returned the staging path the instant _create_owner succeeded, and
# the module's only two exists() probes both tested the FINAL candidate: the
# .part's presence, size and provenance were never measured. reserve() skips a
# candidate only when the FINAL file exists, and an abandoned .part is by
# definition one whose final file does not. So the next unrelated job whose
# template renders that name was handed the base name, reclaimed the foreign
# .part, took resume_from from its st_size, sent no If-Range (the .part.meta
# sidecar is absent whenever the origin sent no ETag or Last-Modified), got a
# 206, opened in mode 'ab', and promoted the concatenation as done under its
# own title -- the 2026-08-29 wrong-file-right-title shape, on the DEFAULT
# sequential path.
#
# Three populations carry ownerless bytes: every .part written before
# v3.66.1370, when .owner did not exist; any path that drops the claim while
# the bytes survive; and bytes an operator placed. Nothing reaps them --
# crash_recovery.scan_for_orphans only LISTS, and delete_orphan runs on
# operator command -- so such a .part persists indefinitely.
#
# Every one of the 18 tests above builds its .part THROUGH the protocol, so the
# hazardous state is outside that fixture population by construction. These
# write it directly to disk.

def _foreign_part(tmp_path, name="Interrupted.mp4", size=CHUNK):
    from bulk_downloader import staging_claim as sc

    final = tmp_path / name
    staging = sc.staging_path_for(final)
    staging.write_bytes(bytes([SCENE_A_BYTE]) * size)
    return final, staging


def test_a_claim_minted_over_foreign_bytes_does_not_adopt_them(tmp_path):
    """RED on the defective parent.

    Preconditions asserted before the verdict, because the defect is a verdict
    reached over a state nobody measured.
    """
    from bulk_downloader import staging_claim as sc

    final, staging = _foreign_part(tmp_path)
    assert staging.is_file() and staging.stat().st_size == CHUNK
    assert not final.exists(), "the final file must be absent, or reserve skips it"
    assert list(tmp_path.glob("*.owner")) == [], (
        "no claim may exist anywhere, or these are not ownerless bytes")

    got = sc.claim(final, sc.job_identity("https://example.test/scene-b"))

    assert got == staging
    # The resource this module protects is the .part's BYTES. A freshly minted
    # claim owns none of them.
    assert got.stat().st_size == 0 if got.exists() else True
    assert not got.exists() or got.read_bytes() == b"", (
        f"the new claim adopted {got.stat().st_size} foreign bytes; the next "
        "transfer would take that as its resume offset and append onto another "
        "scene's file")


def test_the_foreign_bytes_are_set_aside_not_destroyed(tmp_path):
    """Nothing is deleted. The bytes move to a name the orphan scan can still
    see, so an operator can inspect or reap them deliberately."""
    from bulk_downloader import staging_claim as sc

    final, staging = _foreign_part(tmp_path)
    sc.claim(final, sc.job_identity("https://example.test/scene-b"))

    setaside = [p for p in tmp_path.iterdir()
                if p.name.endswith(".part") and p != staging]
    assert len(setaside) == 1, (
        f"expected exactly 1 set-aside .part, found {[p.name for p in tmp_path.iterdir()]}")
    assert setaside[0].read_bytes() == bytes([SCENE_A_BYTE]) * CHUNK, (
        "the foreign bytes were altered or destroyed")
    assert setaside[0].name.endswith(".part"), (
        "crash_recovery.scan_for_orphans globs *.part; a set-aside name that "
        "does not end in .part is invisible to the only thing that could reap it")


def test_reserve_still_returns_the_base_name_over_an_ownerless_part(tmp_path):
    """The divert-to-_1 path is for a claim held by someone else, not for
    unowned bytes. An ownerless .part must not cost the job its filename."""
    from bulk_downloader import staging_claim as sc

    final, staging = _foreign_part(tmp_path)
    candidate, got = sc.reserve(final, sc.job_identity("https://example.test/scene-b"))
    assert candidate == final, f"an ownerless .part diverted the name to {candidate}"
    assert not got.exists() or got.read_bytes() == b""


def test_the_same_job_reclaiming_its_own_part_keeps_every_byte(tmp_path):
    """NEGATIVE CONTROL, and the one that separates this fix from a restart-
    everything fix: a claim that already exists for this identity is a reclaim,
    not a mint, and its bytes are its own."""
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "Interrupted.mp4"
    staging = sc.claim(final, sc.job_identity("https://example.test/scene-a"))
    staging.write_bytes(bytes([SCENE_B_BYTE]) * CHUNK)

    again = sc.claim(final, sc.job_identity("https://example.test/scene-a"))
    assert again == staging
    assert again.read_bytes() == bytes([SCENE_B_BYTE]) * CHUNK, (
        "a job's own staged bytes were discarded; every resume would become a "
        "restart")
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")] == [
        staging.name], "a reclaim set bytes aside as if they were foreign"


def test_a_part_owned_by_another_job_is_still_refused_untouched(tmp_path):
    """NEGATIVE CONTROL: the existing refusal is unchanged, and the other job's
    bytes are not moved by the refusing worker."""
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "Interrupted.mp4"
    staging = sc.claim(final, sc.job_identity("https://example.test/scene-a"))
    staging.write_bytes(bytes([SCENE_A_BYTE]) * CHUNK)

    with pytest.raises(sc.StagingClaimedByAnotherJob):
        sc.claim(final, sc.job_identity("https://example.test/scene-b"))
    assert staging.read_bytes() == bytes([SCENE_A_BYTE]) * CHUNK

    candidate, got = sc.reserve(final, sc.job_identity("https://example.test/scene-b"))
    assert candidate.name == "Interrupted_1.mp4", candidate
    assert staging.read_bytes() == bytes([SCENE_A_BYTE]) * CHUNK, (
        "the diverted worker touched the other job's staged bytes")


def test_an_empty_ownerless_part_is_not_set_aside(tmp_path):
    """Zero bytes are no resume offset and no hazard; moving them would leave
    an empty file for the orphan scan to report forever."""
    from bulk_downloader import staging_claim as sc

    final, staging = _foreign_part(tmp_path, size=0)
    assert staging.stat().st_size == 0
    sc.claim(final, sc.job_identity("https://example.test/scene-b"))
    setaside = [p for p in tmp_path.iterdir()
                if p.name.endswith(".part") and p != staging]
    assert setaside == [], f"an empty .part was set aside: {setaside}"


def test_browser_fallback_writes_the_path_the_transport_reserved(tmp_path):
    """A browser fallback may not replace a reserved name with a fresh probe.

    ``_do_download`` reserves ``final_path`` and later records that same path
    in its done row.  The browser fallback used to call ``safe_dest`` again;
    an arrival between those two operations therefore made Playwright write
    ``Scene_1.mp4`` while the job and history still named ``Scene.mp4``.
    """
    from bulk_downloader import staging_claim as sc
    from bulk_downloader.runner_browser import BrowserMixin

    class _Download:
        def __init__(self):
            self.calls = []

        def save_as(self, path):
            self.calls.append(Path(path))
            Path(path).write_bytes(b"browser-bytes")

    identity = sc.job_identity("https://example.test/browser-race")
    final, staging = sc.reserve(tmp_path / "Scene.mp4", identity)
    dl = _Download()
    try:
        # Preconditions: the transport made an exclusive reservation, then a
        # competing writer arrived exactly once before the browser save.
        owner = sc.owner_path_for(staging)
        assert owner.exists(), "precondition: the transport did not reserve a staging path"
        final.write_bytes(b"competing-final")
        assert final.read_bytes() == b"competing-final"
        assert len(dl.calls) == 0, "precondition: browser save fired before the injection"

        size, transferred = BrowserMixin._pw_save(object(), dl, final)

        assert dl.calls == [final], (
            "browser fallback probed a new destination after transport reserved "
            f"{final}; it wrote {dl.calls!r}, while the caller records {final}")
        assert size == transferred == len(b"browser-bytes")
        assert final.read_bytes() == b"browser-bytes"

        # Negative control: with no intervening file, the same production path
        # still writes precisely the uncontended reservation.
        uncontended, uncontended_stage = sc.reserve(
            tmp_path / "Uncontended.mp4", sc.job_identity("https://example.test/browser-ok"))
        clean = _Download()
        try:
            clean_size, clean_transferred = BrowserMixin._pw_save(object(), clean, uncontended)
            assert clean.calls == [uncontended]
            assert clean_size == clean_transferred == len(b"browser-bytes")
        finally:
            sc.release(uncontended_stage, sc.job_identity("https://example.test/browser-ok"), force=True)
    finally:
        sc.release(staging, identity, force=True)
