"""Run the real sequential HTTP transfer and record what its rate-limit seam did.

WHY THIS EXISTS. `tests/test_v3_43_31_rate_limit.py` used to prove the
per-domain rate limiter by SOURCE TEXT: find ``def _http_download``, take the
body, and assert the literals ``from . import rate_limit`` and
``_rl.acquire(file_url)`` appear in it. That is a gate that reads the shape of
the code rather than the behaviour it names, and on 2026-09-03 it was measured
failing exactly the way CLAUDE.md A7 predicts. A staging cut split the transfer
into a thin ``_http_download`` wrapper holding three closures and a
``_http_download_claimed`` body doing the work. The literals stayed in the
wrapper's closures; the one live ``acquire_rate_limit(file_url)`` call moved
into the body the gate does not read. Deleting that call -- switching the rate
limiter off entirely -- left ``test_runner_acquires_slot_in_http_download``
GREEN. A gate must see the subject it claims to judge.

The replacement runs the transfer. `bulk_downloader.rate_limit.acquire` is
replaced with a recorder, `httpx.stream` with a scripted response, and the
resulting event log names, in order, every acquire, the stream open, every
progress tick and every slot release. An exact count over that log cannot be
satisfied by a literal sitting in an unreachable closure, and it cannot be
broken by moving code between functions -- which is the other half of the
defect this replaces, since the old window had already been widened twice for
text that was never its subject.

Deliberately a plain helper module and not a `tests/test*.py` file: it declares
no gate of its own and owns no CI shard. Its callers are the gates.
"""
from __future__ import annotations

import threading
from pathlib import Path

# The transfer reports progress at most once a second off `time.time()`. A
# clock that advances 1.1s per read makes every chunk cross that threshold, so
# the tick count is a function of the scripted chunks rather than of how fast
# the host happens to be. A real clock would make "exactly N ticks" a race.
_TICK_SECONDS = 1.1


class ScriptedResponse:
    """A stand-in for the httpx streaming response context manager."""

    def __init__(self, chunks, *, status_code=200, content_length=None,
                 raise_after=None, events=None):
        self.status_code = status_code
        self.headers = {
            "Content-Length": str(
                sum(len(c) for c in chunks)
                if content_length is None else content_length)
        }
        self._chunks = list(chunks)
        self._raise_after = raise_after
        self._events = events if events is not None else []

    def __enter__(self):
        self._events.append("stream-open")
        return self

    def __exit__(self, *args):
        self._events.append("stream-close")
        return False

    def iter_bytes(self, chunk_size=None):
        for index, chunk in enumerate(self._chunks):
            if self._raise_after is not None and index == self._raise_after:
                raise self._raise_after_error()
            yield chunk
        if self._raise_after is not None and self._raise_after >= len(self._chunks):
            raise self._raise_after_error()

    def _raise_after_error(self):
        self._events.append("stream-error")
        return OSError("synthetic mid-stream transport failure")


class SeamRun:
    """What one drive of the real `_http_download` did at its rate-limit seam."""

    def __init__(self):
        self.events: list[str] = []
        self.acquired_urls: list[str] = []
        self.progress_sizes: list[int] = []
        self.result = None
        self.error = None

    @property
    def acquires(self) -> int:
        return self.events.count("acquire")

    @property
    def releases(self) -> int:
        return self.events.count("release")

    @property
    def stream_opens(self) -> int:
        return self.events.count("stream-open")

    def milestones(self) -> list[str]:
        """The event log with progress ticks collapsed away.

        Tick COUNT is asserted separately; ordering questions ("the slot is
        taken before the stream opens, and given back after it closes") are
        about the milestones, and folding N ticks into the sequence would make
        the expected list depend on the scripted chunk count.
        """
        return [e for e in self.events if e != "progress"]


def run_sequential_transfer(monkeypatch, tmp_path, *, page_url,
                            chunks=(b"abcd", b"efgh"), status_code=200,
                            content_length=None, raise_after=None,
                            resume_bytes=None, config=None):
    """Drive the REAL `_http_download` once against a scripted response.

    Returns a :class:`SeamRun`. Nothing here reads `runner_transport` source
    text: every number in it was produced by code that ran.
    """
    import httpx
    from bulk_downloader import rate_limit
    from bulk_downloader import runner as runner_mod
    from bulk_downloader import runner_transport as transport
    from bulk_downloader import staging_claim

    run = SeamRun()

    runner = runner_mod.SiteRunner.__new__(runner_mod.SiteRunner)
    runner.site_id = "rate-limit-seam"
    runner.config = dict({"parallel_chunks": 1, "use_curl_cffi": False},
                         **(config or {}))
    runner._stop = threading.Event()
    runner._pause = threading.Event()
    runner._pause.set()
    runner._pick_fastest_mirror = lambda url: url
    runner._recommended_chunk_bytes = lambda: 1024
    runner._current_cap_mbps = lambda: 0
    runner._download_proxy_url = lambda: None
    runner._observe_throughput = lambda *args: None
    runner.log_event = lambda *args, **kwargs: None
    runner.log = type("Log", (), {"warning": lambda *a, **k: None})()

    def _update_job(*args, **extra):
        run.events.append("progress")
        run.progress_sizes.append(extra["file_size"])

    runner._update_job = _update_job

    download_dir = Path(tmp_path)
    # Callers give each drive its own subdirectory so two runs in one test
    # cannot see each other's claim files; create it rather than making the
    # transfer refuse for a reason that has nothing to do with rate limiting.
    download_dir.mkdir(parents=True, exist_ok=True)
    final_path = download_dir / "seam.mp4"
    if resume_bytes:
        # Staged THROUGH the claim protocol, never written raw: ownerless
        # bytes are set aside by staging_claim and the transfer would restart
        # at zero, which would quietly change what this fixture measures.
        part_path = staging_claim.claim(
            final_path, staging_claim.job_identity(page_url))
        part_path.write_bytes(resume_bytes)

    class _Slot:
        def release(self):
            run.events.append("release")

    def _acquire(url):
        run.events.append("acquire")
        run.acquired_urls.append(url)
        return _Slot()

    response = ScriptedResponse(chunks, status_code=status_code,
                                content_length=content_length,
                                raise_after=raise_after, events=run.events)

    monkeypatch.setattr(rate_limit, "acquire", _acquire)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: response)
    monkeypatch.setattr(transport, "record_bandwidth", lambda delta: None)
    monkeypatch.setattr(transport.time, "time", _clock())

    try:
        run.result = runner._http_download(
            page_url, object(),
            type("Ctx", (), {"cookies": lambda self: []})(),
            "https://cdn.test/seam.mp4", final_path)
    except Exception as e:   # recorded, not swallowed: callers assert on it
        run.error = e
    return run


def _clock():
    value = [0.0]

    def tick():
        value[0] += _TICK_SECONDS
        return value[0]

    return tick
