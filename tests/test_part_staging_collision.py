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

import hashlib
import json
import importlib
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

# The CI classifier parses a module-level ASSIGNMENT, not a docstring line.
# This gate's subject is the transport module's staging path, so the affected
# band selects it whenever that module changes.
BD_GATE_SCOPE = "module"


def _load_bd_module(name):
    """Load an optional production sibling without changing its import mode."""
    return importlib.import_module(f"bulk_downloader.{name}")

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


def _resource_identity_for_test(url: str) -> str:
    """Derive the expected identity independently of staging_claim."""
    parsed = urlsplit(url)
    canonical = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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


# ── Row 483/575: one job identity can resolve to a different resource ────────

@pytest.fixture(scope="module")
def changed_resource_resume(origin, tmp_path_factory):
    """Interrupt resource A, then resolve the same page job to resource B."""
    from bulk_downloader import staging_claim as sc

    out = {"unknown": None}
    try:
        origin["hold"].set()
        with origin["handler"].lock:
            request_start = len(origin["handler"].requests)

        dl_dir = Path(tmp_path_factory.mktemp("changed-resource-resume"))
        page_url = "https://example.invalid/one-page"
        identity = sc.job_identity(page_url)
        final = dl_dir / "Changing-tier.mp4"
        staging = sc.staging_path_for(final)
        owner = sc.owner_path_for(staging)

        def _stop_after_first(harness):
            harness._stop.set()

        first = _harness("changed-resource-first", after_write=_stop_after_first)
        try:
            first._http_download(page_url, None, _Ctx(),
                                 origin["base"] + "/scene-a.mp4", final)
            out["first_error"] = "the interrupted attempt completed"
        except BaseException as exc:              # noqa: BLE001 - measured
            out["first_error"] = f"{type(exc).__name__}: {exc}"

        owners = list(dl_dir.glob("*.owner"))
        out.update({
            "staged_bytes_before_second": (
                staging.stat().st_size if staging.exists() else None),
            "staged_census_before_second": (
                _byte_census(staging.read_bytes()) if staging.exists() else None),
            "owner_count_before_second": len(owners),
            "owner_record_before_second": (
                json.loads(owner.read_text(encoding="utf-8"))
                if owner.is_file() else None),
            "meta_count_before_second": len(list(dl_dir.glob("*.part.meta"))),
        })

        # The page-level reservation really matched rather than diverting. It
        # intentionally cannot prove which media URL produced the staged bytes.
        out["matched_reserve"] = sc.reserve(final, identity) == (final, staging)

        second = _harness("changed-resource-second")
        try:
            out["second_result"] = second._http_download(
                page_url, None, _Ctx(), origin["base"] + "/scene-b.mp4", final)
            out["second_outcome"] = "completed"
        except BaseException as exc:              # noqa: BLE001 - measured
            out["second_outcome"] = "refused"
            out["second_error_type"] = type(exc).__name__
            out["second_error"] = str(exc)

        with origin["handler"].lock:
            requests = list(origin["handler"].requests[request_start:])
        out["requests"] = requests
        out["first_resource_requests"] = [
            r for r in requests if r["path"] == "/scene-a.mp4"]
        out["second_resource_ranges"] = [
            r for r in requests
            if r["path"] == "/scene-b.mp4" and r["range"] is not None]
        out["final_exists"] = final.exists()
        if final.exists():
            blob = final.read_bytes()
            out["final_size"] = len(blob)
            out["final_digest"] = hashlib.sha256(blob).hexdigest()
        out["source_digests"] = {
            hashlib.sha256(BODY_A).hexdigest(),
            hashlib.sha256(BODY_B).hexdigest(),
        }
        out["first_resource_identity"] = _resource_identity_for_test(
            origin["base"] + "/scene-a.mp4")
        out["staging_exists_after_second"] = staging.exists()
        out["staging_census_after_second"] = (
            _byte_census(staging.read_bytes()) if staging.exists() else None)
        out["owner_count_after_second"] = len(list(dl_dir.glob("*.owner")))
    except BaseException as exc:                  # noqa: BLE001 - recorded
        out["unknown"] = f"{type(exc).__name__}: {exc}"
    return out


def _assert_changed_resource_preconditions(r):
    assert r["unknown"] is None, r["unknown"]
    assert "stopped" in r["first_error"], r["first_error"]
    assert r["staged_bytes_before_second"] == CHUNK
    assert r["staged_census_before_second"] == {SCENE_A_BYTE: CHUNK}
    assert r["owner_count_before_second"] == 1
    assert r["owner_record_before_second"]["job"] == hashlib.sha256(
        b"https://example.invalid/one-page").hexdigest()
    assert r["meta_count_before_second"] == 0
    assert r["matched_reserve"] is True
    assert len(r["source_digests"]) == 2
    first = r["first_resource_requests"]
    assert len(first) == 1, f"expected one first-resource request: {first}"
    assert first[0]["range"] is None, (
        f"a first run with no .part sent a Range request: {first}")
    assert first[0]["status"] == 200


def test_changed_resource_resume_measured_its_preconditions(
        changed_resource_resume):
    _assert_changed_resource_preconditions(changed_resource_resume)


def test_changed_resource_is_never_appended_to_the_old_resources_bytes(
        changed_resource_resume):
    r = changed_resource_resume
    _assert_changed_resource_preconditions(r)
    assert r["second_outcome"] == "refused", (
        "the same page identity resumed a different media resource and "
        f"promoted digest {r.get('final_digest')}; source digests are "
        f"{sorted(r['source_digests'])}, Range requests were "
        f"{r['second_resource_ranges']}")
    assert r["second_error_type"] == "_StagingUnavailable"
    assert "resource mismatch" in r["second_error"].lower()
    assert (r["owner_record_before_second"]["resource"] ==
            r["first_resource_identity"])
    assert r["second_resource_ranges"] == []
    assert r["final_exists"] is False
    assert r["staging_exists_after_second"] is True
    assert r["staging_census_after_second"] == {SCENE_A_BYTE: CHUNK}
    assert r["owner_count_after_second"] == 1


def test_parallel_changed_resource_refuses_before_any_range(
        origin, tmp_path, monkeypatch):
    """The segmented path has its own resource-bound claim before workers."""
    from bulk_downloader import staging_claim as sc

    page_url = "https://example.invalid/one-parallel-page"
    first_url = origin["base"] + "/scene-a.mp4"
    second_url = origin["base"] + "/scene-b.mp4"
    final = tmp_path / "Parallel-changing-tier.mp4"
    identity = sc.job_identity(page_url)
    staging = sc.claim(final, identity, resource_url=first_url)
    staging.write_bytes(BODY_A[:CHUNK])
    owner = sc.owner_path_for(staging)
    owner_before = owner.read_bytes()
    census_before = _byte_census(staging.read_bytes())
    assert census_before == {SCENE_A_BYTE: CHUNK}
    assert json.loads(owner_before)["resource"] == _resource_identity_for_test(
        first_url)

    with origin["handler"].lock:
        request_start = len(origin["handler"].requests)
    real_claim = sc.claim
    parallel_claims = []

    def _observed_claim(final_path, holder, *, resource_url=None):
        parallel_claims.append((final_path, holder, resource_url))
        return real_claim(final_path, holder, resource_url=resource_url)

    monkeypatch.setattr(sc, "claim", _observed_claim)
    harness = _harness("parallel-changed-resource")
    chunk_count = 2
    outcome = "completed"
    error_type = error = None
    try:
        harness._http_download_parallel(
            page_url, _Ctx(), second_url, final,
            total=LEN_B, n_chunks=chunk_count)
    except BaseException as exc:                 # noqa: BLE001 - measured
        outcome = "refused"
        error_type = type(exc).__name__
        error = str(exc)

    with origin["handler"].lock:
        requests = list(origin["handler"].requests[request_start:])
    ranges = [
        row for row in requests
        if row["path"] == "/scene-b.mp4" and row["range"] is not None
    ]

    assert chunk_count == 2 and chunk_count > 1
    assert parallel_claims == [(final, identity, second_url)], (
        f"parallel claim seam fired {len(parallel_claims)} times: "
        f"{parallel_claims!r}")
    assert outcome == "refused", (
        "parallel transfer crossed a changed-resource claim; "
        f"Range requests were {ranges}")
    assert error_type == "_StagingUnavailable", error
    assert "resource mismatch" in (error or "").lower()
    assert ranges == []
    assert final.exists() is False
    assert staging.is_file()
    assert _byte_census(staging.read_bytes()) == census_before
    assert owner.read_bytes() == owner_before


def test_resource_binding_ignores_rotating_signatures_but_not_a_new_path(
        tmp_path):
    """Negative control: the guard preserves signed-URL resumes."""
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "Signed.mp4"
    identity = sc.job_identity("https://example.invalid/one-page")
    staging = sc.claim(
        final, identity,
        resource_url="https://cdn.example/video.mp4?token=first")
    staging.write_bytes(bytes([SCENE_A_BYTE]) * CHUNK)

    matched = sc.claim(
        final, identity,
        resource_url="https://cdn.example/video.mp4?token=rotated")
    assert matched == staging
    assert matched.stat().st_size == CHUNK
    assert matched.read_bytes() == bytes([SCENE_A_BYTE]) * CHUNK

    with pytest.raises(sc.StagingResourceMismatch, match="resource mismatch"):
        sc.claim(final, identity,
                 resource_url="https://cdn.example/higher-tier.mp4?token=rotated")
    assert staging.read_bytes() == bytes([SCENE_A_BYTE]) * CHUNK, (
        "a resource mismatch altered the staged bytes it refused")


def test_a_proven_legacy_claim_binds_without_discarding_its_resume(tmp_path):
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "Legacy.mp4"
    identity = sc.job_identity("https://example.invalid/legacy-page")
    staging = sc.claim(final, identity)
    staging.write_bytes(bytes([SCENE_A_BYTE]) * CHUNK)

    rebound = sc.claim(final, identity,
                       resource_url="https://cdn.example/legacy.mp4")
    assert rebound == staging
    assert rebound.stat().st_size == CHUNK
    assert staging.read_bytes() == bytes([SCENE_A_BYTE]) * CHUNK
    owner = sc.owner_path_for(staging)
    record = json.loads(owner.read_text(encoding="utf-8"))
    assert record["resource"] == _resource_identity_for_test(
        "https://cdn.example/legacy.mp4")


def test_an_empty_proven_claim_measures_zero_before_rebinding(tmp_path):
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "Empty.mp4"
    identity = sc.job_identity("https://example.invalid/empty-page")
    staging = sc.claim(
        final, identity, resource_url="https://cdn.example/empty-old.mp4")
    staging.touch()
    assert staging.stat().st_size == 0

    rebound = sc.claim(final, identity,
                       resource_url="https://cdn.example/empty-new.mp4")
    assert rebound == staging
    assert rebound.stat().st_size == 0
    record = json.loads(
        sc.owner_path_for(staging).read_text(encoding="utf-8"))
    assert record["resource"] == _resource_identity_for_test(
        "https://cdn.example/empty-new.mp4")


def test_v2_still_requires_the_proof_field_after_the_resource_format_bump(
        tmp_path):
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "Malformed-v2.mp4"
    identity = sc.job_identity("https://example.invalid/malformed-v2")
    owner = sc.owner_path_for(sc.staging_path_for(final))
    owner.write_text(json.dumps({"v": 2, "job": identity}), encoding="utf-8")

    with pytest.raises(sc.StagingUnavailable, match="no 'proven' proof field"):
        sc.claim(final, identity)


def test_resource_transform_control_only_imports_the_module():
    """Mutation control: importability says nothing about provenance."""
    from bulk_downloader import staging_claim as sc

    assert callable(sc.claim)


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


# Rows 523 and 501: the top-level reservation owns every exit, and a RAM
# staging path owns the bytes written under that path.

class _Row523Locator:
    def __init__(self, href):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):
        return None


class _Row523Page:
    def __init__(self, href):
        self.url = "https://example.invalid/page"
        self.href = href

    def title(self):
        return "Scene"

    def expect_download(self, timeout):
        from bulk_downloader.runner_transport import PWTimeout
        raise PWTimeout(f"synthetic fallback timeout after {timeout}")


def _row523_best(href):
    return {
        "locator": _Row523Locator(href),
        "score": 1080,
        "text": "1080p",
        "size": 0,
        "_via_learned": False,
        "_all_candidates": [],
    }


def _row523_runner(http_behavior="real"):
    from collections import deque
    from types import SimpleNamespace
    from bulk_downloader import runner_transport as rt

    class _Runner(rt.TransportMixin):
        def __init__(self):
            self.site_id = "row523"
            self.config = {
                "name": "Row 523",
                "filename_template": "{filename}",
                "skip_if_exists": False,
                "use_http_dl": True,
                "use_curl_cffi": False,
                "parallel_chunks": 1,
                "verify_integrity": False,
                "verify_hash": False,
                "max_mbps": 0,
            }
            self.jobs = {}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()
            self.log = logging.getLogger("row523")
            self.job_updates = []
            self.failures = []
            self._recent_completions = deque()
            self._recent_per_min = 0.0
            self.http_behavior = http_behavior

        def _update_job(self, url, status, message, **extra):
            self.job_updates.append((url, status, message, extra))

        def _handle_failure(self, url, message, screenshot=""):
            self.failures.append((url, message))
            self._update_job(url, "failed", message, screenshot=screenshot)
            rt.db_log(self.site_id, self.config["name"], url, "failed",
                      "", 0, message)

        def _screenshot(self, page, url):
            return ""

        def _probe_for_higher_tier(self, file_url, referer=""):
            return file_url

        def _build_mirror_urls(self, file_url):
            return []

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

        def _http_download(self, page_url, page, ctx, file_url, final_path):
            if self.http_behavior == "success":
                Path(final_path).write_bytes(b"http-ok")
                return len(b"http-ok"), len(b"http-ok")
            if self.http_behavior == "partial":
                from bulk_downloader import staging_claim as sc
                staging = sc.claim(final_path, sc.job_identity(page_url))
                staging.write_bytes(b"partial-bytes")
                raise rt._HTTPDownloadFailed("synthetic interrupted transfer")
            return super()._http_download(page_url, page, ctx, file_url,
                                          final_path)

        def _pw_save(self, dl, final_path):
            Path(final_path).write_bytes(b"browser-ok")
            return len(b"browser-ok"), len(b"browser-ok")

        def _hls_download_guarded(self, module, url, final_path, **kwargs):
            Path(final_path).write_bytes(b"segmented-ok")
            return SimpleNamespace(ok=True, bytes_written=len(b"segmented-ok"),
                                   error="", error_detail="")

        def _embed_metadata_if_mp4(self, *args, **kwargs):
            return None

        def _size_on_disk_after_tagging(self, final_path, fallback):
            return Path(final_path).stat().st_size

    return _Runner()


def _row523_patch_boundaries(monkeypatch, logs):
    from bulk_downloader import runner_transport as rt
    hooks = _load_bd_module("hooks")

    monkeypatch.setattr(rt, "gate_candidate_url",
                        lambda locator, page_url, **kwargs: (locator.href, None))
    monkeypatch.setattr(rt, "db_skip_identity",
                        lambda page_url, final_path: ("different", ""))
    monkeypatch.setattr(rt, "history_title_kwargs", lambda runner, url: {})
    monkeypatch.setattr(rt, "db_log", lambda *args, **kwargs: logs.append(args))
    monkeypatch.setattr(hooks, "fire_event", lambda *args, **kwargs: None)


def _row523_orphans(monkeypatch, download_dir):
    crash_recovery = _load_bd_module("crash_recovery")
    monkeypatch.setattr(crash_recovery, "_ignored_paths", lambda: set())
    return crash_recovery.scan_for_orphans(
        s_cfg={"row523": {"download_dir": str(download_dir)}},
        runners={}, age_threshold_s=0)


def test_do_download_staging_unavailable_exit_drops_its_empty_owned_claim(
        tmp_path, monkeypatch):
    from bulk_downloader import staging_claim as sc

    logs = []
    _row523_patch_boundaries(monkeypatch, logs)
    runner = _row523_runner()
    page_url = "https://example.invalid/staging-unavailable"
    final = tmp_path / "scene.mp4"
    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    real_claim = sc.claim
    observed = []

    def _fail_the_inner_claim(final_path, identity, *, resource_url=None):
        if observed:
            raise AssertionError("inner claim injection fired more than once")
        if owner.exists():
            observed.append({
                "owners": len(list(tmp_path.glob("*.owner"))),
                "holder": sc._read_owner_identity(owner),
                "parts": len(list(tmp_path.glob("*.part"))),
            })
            raise sc.StagingUnavailable("synthetic inner claim ENOSPC")
        return real_claim(final_path, identity, resource_url=resource_url)

    monkeypatch.setattr(sc, "claim", _fail_the_inner_claim)
    runner._do_download(
        _Row523Page("https://cdn.invalid/scene.mp4"), _Ctx(), page_url,
        _row523_best("https://cdn.invalid/scene.mp4"), tmp_path, "1080p")

    identity = sc.job_identity(page_url)
    assert observed == [{"owners": 1, "holder": identity, "parts": 0}], (
        f"the injection missed the one-owner/no-part precondition: {observed}")
    assert _row523_orphans(monkeypatch, tmp_path) == [], (
        "the owner-only residue unexpectedly appeared in the *.part scan")
    owners = list(tmp_path.glob("*.owner"))
    assert owners == [], (
        f"staging-unavailable exit left exactly {len(owners)} owner file: {owners}")
    assert [u[1] for u in runner.job_updates].count("needs_review") == 1
    assert [row[3] for row in logs].count("needs_review") == 1


def test_do_download_http_then_timeout_exit_drops_its_empty_owned_claim(
        tmp_path, monkeypatch):
    from bulk_downloader import runner_transport as rt
    from bulk_downloader import staging_claim as sc
    rate_limit = _load_bd_module("rate_limit")

    logs = []
    _row523_patch_boundaries(monkeypatch, logs)
    page_url = "https://example.invalid/http-404"
    final = tmp_path / "scene.mp4"
    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    observed = []

    class _Slot:
        def release(self):
            return None

    class _Response:
        status_code = 404
        headers = {}

        def __enter__(self):
            observed.append({
                "owners": len(list(tmp_path.glob("*.owner"))),
                "holder": sc._read_owner_identity(owner),
                "parts": len(list(tmp_path.glob("*.part"))),
            })
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rate_limit, "acquire", lambda url: _Slot())
    monkeypatch.setattr(rt.httpx, "stream", lambda *args, **kwargs: _Response())
    runner = _row523_runner()
    runner._do_download(
        _Row523Page("https://cdn.invalid/scene.mp4"), _Ctx(), page_url,
        _row523_best("https://cdn.invalid/scene.mp4"), tmp_path, "1080p")

    identity = sc.job_identity(page_url)
    assert observed == [{"owners": 1, "holder": identity, "parts": 0}], (
        f"the 404 injection missed the one-owner/no-part precondition: {observed}")
    assert len(runner.failures) == 1
    assert "HTTP 404" in runner.failures[0][1]
    assert [row[3] for row in logs].count("failed") == 1
    assert _row523_orphans(monkeypatch, tmp_path) == []
    owners = list(tmp_path.glob("*.owner"))
    assert owners == [], (
        f"HTTP-then-timeout exit left exactly {len(owners)} owner file: {owners}")


def test_do_download_scope_will_not_delete_a_different_jobs_claim(
        tmp_path, monkeypatch):
    from bulk_downloader import staging_claim as sc

    logs = []
    _row523_patch_boundaries(monkeypatch, logs)
    runner = _row523_runner()
    page_url = "https://example.invalid/losing-owner"
    other = sc.job_identity("https://example.invalid/other-job")
    final = tmp_path / "scene.mp4"
    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    real_claim = sc.claim
    injected = []

    def _replace_before_refusal(final_path, identity, *, resource_url=None):
        if owner.exists():
            owner.write_text(json.dumps({"v": 1, "job": other}),
                             encoding="utf-8")
            injected.append(sc._read_owner_identity(owner))
            raise sc.StagingUnavailable("synthetic ownership changed")
        return real_claim(final_path, identity, resource_url=resource_url)

    monkeypatch.setattr(sc, "claim", _replace_before_refusal)
    runner._do_download(
        _Row523Page("https://cdn.invalid/scene.mp4"), _Ctx(), page_url,
        _row523_best("https://cdn.invalid/scene.mp4"), tmp_path, "1080p")

    assert injected == [other], "the foreign-owner injection did not fire once"
    owners = list(tmp_path.glob("*.owner"))
    assert owners == [owner]
    assert sc._read_owner_identity(owner) == other, (
        "the scope guard blind-unlinked another worker's claim")


def test_interrupted_do_download_keeps_its_part_claimed_for_resume(
        tmp_path, monkeypatch):
    from bulk_downloader import staging_claim as sc

    logs = []
    _row523_patch_boundaries(monkeypatch, logs)
    runner = _row523_runner("partial")
    page_url = "https://example.invalid/interrupted"
    final = tmp_path / "scene.mp4"
    runner._do_download(
        _Row523Page("https://cdn.invalid/scene.mp4"), _Ctx(), page_url,
        _row523_best("https://cdn.invalid/scene.mp4"), tmp_path, "1080p")

    staging = sc.staging_path_for(final)
    owner = sc.owner_path_for(staging)
    identity = sc.job_identity(page_url)
    assert staging.read_bytes() == b"partial-bytes"
    assert list(tmp_path.glob("*.part")) == [staging]
    assert list(tmp_path.glob("*.owner")) == [owner]
    assert sc._read_owner_identity(owner) == identity
    assert sc.claim(final, identity) == staging
    assert staging.read_bytes() == b"partial-bytes", (
        "the same job did not resume the exact bytes its claim protects")
    sc.release(staging, force=True)


@pytest.mark.parametrize("mode,href,payload", [
    ("segmented", "https://cdn.invalid/scene.m3u8", b"segmented-ok"),
    ("http", "https://cdn.invalid/scene.mp4", b"http-ok"),
    ("browser", "https://cdn.invalid/scene.mp4", b"browser-ok"),
])
def test_successful_do_download_paths_leave_no_staging_residue(
        mode, href, payload, tmp_path, monkeypatch):
    logs = []
    _row523_patch_boundaries(monkeypatch, logs)
    runner = _row523_runner("success" if mode == "http" else "real")
    if mode == "browser":
        runner.config["use_http_dl"] = False
    page_url = f"https://example.invalid/{mode}"

    runner._do_download(_Row523Page(href), _Ctx(), page_url,
                        _row523_best(href), tmp_path, "1080p")

    finals = [p for p in tmp_path.iterdir()
              if not p.name.endswith((".part", ".owner", ".meta"))]
    assert len(finals) == 1 and finals[0].read_bytes() == payload
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.owner")) == []
    assert [u[1] for u in runner.job_updates].count("done") == 1
    assert [row[3] for row in logs].count("done") == 1


def test_every_post_reservation_return_is_inside_one_ownership_scope():
    """A future return cannot escape merely because nobody enumerated it."""
    import ast
    import inspect
    import textwrap
    from bulk_downloader.runner_transport import TransportMixin

    fn = ast.parse(textwrap.dedent(
        inspect.getsource(TransportMixin._do_download))).body[0]
    reserves = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "staging_claim"
                and n.func.attr == "reserve"]
    assert len(reserves) == 1, f"expected one reservation, found {len(reserves)}"
    reserve = reserves[0]

    def _contains(root, target):
        return any(node is target for node in ast.walk(root))

    reserve_statement_indexes = [i for i, statement in enumerate(fn.body)
                                 if _contains(statement, reserve)]
    assert len(reserve_statement_indexes) == 1
    post_reservation = fn.body[reserve_statement_indexes[0] + 1:]
    population = [n for statement in post_reservation
                  for n in ast.walk(statement) if isinstance(n, ast.Return)]
    assert population, "the post-reservation return denominator is empty"

    def _has_owned_release(node):
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "staging_claim"
                 and n.func.attr == "release"]
        return any(len(call.args) >= 2 for call in calls)

    guards = [n for statement in post_reservation for n in ast.walk(statement)
              if isinstance(n, ast.Try)
              and n.finalbody and _has_owned_release(
                  ast.Module(body=n.finalbody, type_ignores=[]))]
    assert len(guards) == 1, (
        f"expected one ownership finally, found {len(guards)} for "
        f"{len(population)} post-reservation returns")
    guarded_returns = {id(n) for n in ast.walk(
        ast.Module(body=guards[0].body, type_ignores=[]))
                       if isinstance(n, ast.Return)}
    unguarded = [n.lineno for n in population if id(n) not in guarded_returns]
    assert unguarded == [], (
        f"post-reservation returns outside the ownership scope: {unguarded}; "
        f"denominator={len(population)}")
    assert fn.body[-1] is guards[0], (
        "the ownership scope is not the final top-level statement; a return "
        "can be appended after it and strand the reservation")


def test_ramdisk_writer_path_has_its_own_claim_during_transfer(
        origin, tmp_path, monkeypatch):
    from bulk_downloader import staging_claim as sc
    rd = _load_bd_module("ramdisk_stage")

    ramdisk = tmp_path / "ramdisk"
    download_dir = tmp_path / "downloads"
    ramdisk.mkdir()
    download_dir.mkdir()
    page_url = "https://example.invalid/ramdisk-interrupted"
    final = download_dir / "RamScene.mp4"
    disk_staging = sc.staging_path_for(final)
    disk_owner = sc.owner_path_for(disk_staging)
    observed = []

    def _measure_and_stop(harness):
        written = list((ramdisk / "staging").glob("*.part"))
        observed.append({
            "written": written,
            "sizes": [p.stat().st_size for p in written],
            "disk_part": disk_staging.exists(),
            "disk_owners": len(list(download_dir.glob("*.owner"))),
            "disk_holder": (sc._read_owner_identity(disk_owner)
                            if disk_owner.exists() else None),
            "written_owners": [sc.owner_path_for(p).exists() for p in written],
            "written_holders": [sc._read_owner_identity(sc.owner_path_for(p))
                                for p in written
                                if sc.owner_path_for(p).exists()],
        })
        harness._stop.set()

    rd._reservations.clear()
    harness = _harness("ramdisk-interrupted", after_write=_measure_and_stop)
    harness.config.update({
        "use_ramdisk_stage": True,
        "ramdisk_path": str(ramdisk),
        "ramdisk_capacity_gb": 0.05,
        "ramdisk_max_file_gb": 0.01,
    })
    try:
        with pytest.raises(Exception, match="stopped"):
            harness._http_download(page_url, None, _Ctx(),
                                   origin["base"] + "/scene-b.mp4", final)
        identity = sc.job_identity(page_url)
        assert len(observed) == 1, f"write seam fired {len(observed)} times"
        row = observed[0]
        assert len(row["written"]) == 1, row
        assert row["written"][0].parent == ramdisk / "staging"
        assert row["written"][0].parent != download_dir
        assert row["sizes"] == [CHUNK], (
            f"transfer moved {row['sizes']}, not exact nonzero N={CHUNK}")
        assert row["disk_part"] is False, (
            "claim() unexpectedly created the on-disk .part")
        assert row["disk_owners"] == 1
        assert row["disk_holder"] == identity
        assert row["written_owners"] == [True], (
            f"the {CHUNK}-byte writer path has no claim: {row}")
        assert row["written_holders"] == [identity]
    finally:
        for part in (ramdisk / "staging").glob("*.part"):
            part.unlink(missing_ok=True)
            sc.release(part, page_url, force=True)
        sc.release(disk_staging, page_url, force=True)
        rd._reservations.clear()


def test_ramdisk_cross_process_collision_gets_two_claimed_paths(
        tmp_path):
    from bulk_downloader import staging_claim as sc
    rd = _load_bd_module("ramdisk_stage")

    ramdisk = tmp_path / "ramdisk"
    ramdisk.mkdir()
    config = {
        "ramdisk_path": str(ramdisk),
        "ramdisk_capacity_gb": 0.05,
        "ramdisk_max_file_gb": 0.01,
    }
    final_a = tmp_path / "site-a" / "Same.mp4"
    final_b = tmp_path / "site-b" / "Same.mp4"
    final_a.parent.mkdir()
    final_b.parent.mkdir()
    rd._reservations.clear()
    first = rd.reserve_staging_path(
        str(final_a), 4096, config,
        identity=sc.job_identity(str(final_a.resolve())),
        claim_reserver=sc.reserve)
    assert first is not None, "first reserve failed open to the disk path"
    assert Path(first).parent == ramdisk / "staging"
    rd._reservations.clear()  # the second process has no copy of the first table
    second = rd.reserve_staging_path(
        str(final_b), 4096, config,
        identity=sc.job_identity(str(final_b.resolve())),
        claim_reserver=sc.reserve)
    assert second is not None, "second reserve failed open to the disk path"
    assert Path(second).parent == ramdisk / "staging"
    try:
        paths = {Path(first), Path(second)}
        owners = list((ramdisk / "staging").glob("*.owner"))
        assert len(paths) == 2, (
            f"two process tables yielded {len(paths)} distinct staging path; "
            f"owner files under the written namespace={len(owners)}")
        assert len(owners) == 2
        assert all(sc.owner_path_for(path).is_file() for path in paths)
    finally:
        for path in {Path(first), Path(second)}:
            sc.release(path, force=True)
        rd._reservations.clear()


def test_unmeasurable_ramdisk_ownership_refuses_before_any_write(
        tmp_path, monkeypatch):
    from bulk_downloader import runner_transport as rt
    from bulk_downloader import staging_claim as sc
    rd = _load_bd_module("ramdisk_stage")

    logs = []
    _row523_patch_boundaries(monkeypatch, logs)
    ramdisk = tmp_path / "ramdisk"
    ramdisk.mkdir()
    runner = _row523_runner()
    runner.config.update({
        "use_ramdisk_stage": True,
        "ramdisk_path": str(ramdisk),
        "ramdisk_capacity_gb": 0.05,
        "ramdisk_max_file_gb": 0.01,
    })
    calls = []

    def _unknown(*args, **kwargs):
        calls.append((args, kwargs))
        raise sc.StagingUnavailable("RAM ownership is UNKNOWN")

    monkeypatch.setattr(rd, "reserve_staging_path", _unknown)
    monkeypatch.setattr(
        rt.httpx, "stream",
        lambda *args, **kwargs: pytest.fail(
            "HTTP opened after RAM ownership became UNKNOWN"))
    page_url = "https://example.invalid/ramdisk-unknown"
    runner._do_download(
        _Row523Page("https://cdn.invalid/scene.mp4"), _Ctx(), page_url,
        _row523_best("https://cdn.invalid/scene.mp4"), tmp_path, "1080p")

    assert len(calls) == 1, f"RAM ownership was measured {len(calls)} times"
    assert calls[0][1]["identity"] == sc.job_identity(page_url)
    needs_review = [u for u in runner.job_updates if u[1] == "needs_review"]
    assert len(needs_review) == 1
    assert "RAM ownership is UNKNOWN" in needs_review[0][2]
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.owner")) == []


def test_uncontended_ramdisk_transfer_promotes_exact_bytes_and_releases_claims(
        origin, tmp_path):
    rd = _load_bd_module("ramdisk_stage")

    ramdisk = tmp_path / "ramdisk"
    download_dir = tmp_path / "downloads"
    ramdisk.mkdir()
    download_dir.mkdir()
    final = download_dir / "RamSuccess.mp4"
    harness = _harness("ramdisk-success")
    harness.config.update({
        "use_ramdisk_stage": True,
        "ramdisk_path": str(ramdisk),
        "ramdisk_capacity_gb": 0.05,
        "ramdisk_max_file_gb": 0.01,
    })
    rd._reservations.clear()

    result = harness._http_download(
        "https://example.invalid/ramdisk-success", None, _Ctx(),
        origin["base"] + "/scene-b.mp4", final)

    assert result == (LEN_B, LEN_B), "the exact nonzero transfer count changed"
    assert final.read_bytes() == BODY_B
    assert list(tmp_path.rglob("*.part")) == []
    assert list(tmp_path.rglob("*.owner")) == []
    assert len(list(download_dir.iterdir())) == 1
    rd._reservations.clear()


def test_ramdisk_http_failure_releases_its_empty_written_path_claim(
        origin, tmp_path, monkeypatch):
    from bulk_downloader import staging_claim as sc
    from bulk_downloader.constants import _HTTPDownloadFailed
    rd = _load_bd_module("ramdisk_stage")

    ramdisk = tmp_path / "ramdisk"
    download_dir = tmp_path / "downloads"
    ramdisk.mkdir()
    download_dir.mkdir()
    final = download_dir / "RamMissing.mp4"
    page_url = "https://example.invalid/ramdisk-missing"
    identity = sc.job_identity(page_url)
    harness = _harness("ramdisk-missing")
    harness.config.update({
        "use_ramdisk_stage": True,
        "ramdisk_path": str(ramdisk),
        "ramdisk_capacity_gb": 0.05,
        "ramdisk_max_file_gb": 0.01,
    })
    observed = []
    real_reserve = rd.reserve_staging_path

    def _record_claim(*args, **kwargs):
        path_text = real_reserve(*args, **kwargs)
        assert path_text is not None, "RAM reservation failed open to disk"
        path = Path(path_text)
        owner = sc.owner_path_for(path)
        observed.append({
            "path": path,
            "parent": path.parent,
            "part_exists": path.exists(),
            "owner_count": len(list(path.parent.glob("*.owner"))),
            "holder": sc._read_owner_identity(owner),
        })
        return path_text

    rd._reservations.clear()
    monkeypatch.setattr(rd, "reserve_staging_path", _record_claim)
    try:
        with pytest.raises(_HTTPDownloadFailed, match="HTTP 404"):
            harness._http_download(
                page_url, None, _Ctx(), origin["base"] + "/missing.mp4", final)

        assert len(observed) == 1, (
            f"RAM ownership seam fired {len(observed)} times")
        row = observed[0]
        assert row["parent"] == ramdisk / "staging"
        assert row["parent"] != download_dir
        assert row["part_exists"] is False, (
            "404 fixture unexpectedly staged bytes before refusing")
        assert row["owner_count"] == 1
        assert row["holder"] == identity
        assert list((ramdisk / "staging").glob("*.part")) == []
        owners = list((ramdisk / "staging").glob("*.owner"))
        assert owners == [], (
            f"HTTP 404 left exactly {len(owners)} RAM owner file: {owners}")
    finally:
        for owner in (ramdisk / "staging").glob("*.owner"):
            sc.release(owner.with_suffix(""), force=True)
        rd._reservations.clear()


def test_transform_control_only_imports_runner_transport():
    """Mutation transform control: importability is not staging behavior."""
    from bulk_downloader import runner_transport
    assert runner_transport.TransportMixin is not None
