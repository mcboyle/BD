"""dat-retry-class-caller (O1789 deep-adapters-transport, R165-D F1): the attempt loop's own veto.

dat-retry-class r1 made _is_transient_download_error veto stop / rate-limit as whole words, so "separate-cdn",
"accelerate" and "/generated/" in a message are not a rate limit. But _run_http_attempts_with_resume rejected the
error first with a substring test ("stopped" in s or "rate" in s) and re-raised it. The fixed helper never ran: a
connection reset on a .../separate-cdn/... url was not retried on the same url, and a 404 on a .../generated/... url
never reached the next mirror.

Every test here goes through _run_http_attempts_with_resume, the product path, not the helper alone. The controls keep
a real stop / rate limit (bare, joined or camelCase) raising at the first attempt with a mirror available.
"""

import threading
from pathlib import Path

import pytest

from bulk_downloader.runner_transport import TransportMixin, _HTTPDownloadFailed

BD_GATE_SCOPE = "module"
DIAG = "DAT_RETRY_CLASS_CALLER_SUBSTRING_VETO"
URL = "https://a.test/f.bin"
MIRROR = "https://b.test/f.bin"


class _Host(TransportMixin):
    """Only what _run_http_attempts_with_resume touches (the row903 shape)."""

    def __init__(self, failures):
        self.config = {}
        self.site_id = "dat_retry_class_caller"
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self.failures = list(failures)
        self.calls = []

    def _update_job(self, *a, **kw):
        pass

    def log_event(self, *a, **kw):
        pass

    def _extract_host(self, url):
        return url

    def _http_download(self, page_url, page, ctx, attempt_url, final_path, **kw):
        self.calls.append(attempt_url)
        if self.failures:
            raise _HTTPDownloadFailed(self.failures.pop(0))
        return (100, 100)


def _run(host, urls):
    return host._run_http_attempts_with_resume("page", None, None, URL, urls, Path("unused.bin"))


@pytest.mark.parametrize("msg", [
    "unexpected: [Errno 104] Connection reset by peer (https://separate-cdn.example/f.bin)",
    "http error: ReadTimeout while fetching https://cdn.example/accelerate/f.bin",
    "http error: RemoteProtocolError: peer closed connection (https://x.example/generated/f.bin)",
], ids=["separate-cdn", "accelerate", "generated"])
def test_a_transient_failure_whose_text_contains_rate_is_retried_on_the_same_url(msg):
    host = _Host([msg])
    try:
        result = _run(host, [URL])
    except _HTTPDownloadFailed as e:
        pytest.fail(f"{DIAG}: transient failure re-raised after {len(host.calls)} call(s), never retried: {e}")
    assert result == (100, 100)
    assert host.calls == [URL, URL]


def test_a_permanent_failure_whose_text_contains_rate_still_reaches_the_next_mirror():
    host = _Host(["HTTP 404 https://x.example/generated/f.bin"])
    try:
        result = _run(host, [URL, MIRROR])
    except _HTTPDownloadFailed as e:
        pytest.fail(f"{DIAG}: 404 re-raised after {len(host.calls)} call(s), mirror never tried: {e}")
    assert result == (100, 100)
    assert host.calls == [URL, MIRROR]


@pytest.mark.parametrize("msg", [
    "stopped",
    "rate limited by domain policy",
    "HTTP 429 rate-limit exceeded",
    "RateLimitError: slow down",
    "ratelimit",
    "http_rate_limited",
    "download_stopped",
    "DownloadStopped",
    "connection reset by peer; rate limited",
])
def test_control_a_stop_or_rate_limit_raises_at_the_first_attempt(msg):
    host = _Host([msg])
    with pytest.raises(_HTTPDownloadFailed):
        _run(host, [URL, MIRROR])
    assert host.calls == [URL], f"stop/rate-limit {msg!r} was retried or walked to a mirror: {host.calls}"


def test_control_a_plain_connection_reset_is_retried():
    host = _Host(["unexpected: [Errno 104] Connection reset by peer"])
    assert _run(host, [URL]) == (100, 100)
    assert host.calls == [URL, URL]
