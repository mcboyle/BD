"""Bug-hunt dl-retry: retry_policy findings 4, 6 and the non-finite status.

4  408 Request Timeout / 425 Too Early fell through the blanket 4xx rule to
   "permanent", so the auto-retry scan marked them never-retry.
6  A Retry-After delay was not a floor on the default jitter path (600 s
   could come back as 510 s), and an HTTP-date Retry-After was dropped for
   the class default.
-  classify_failure / compute_next_delay raised OverflowError on an infinite
   number (POST /api/retry_policy/classify answered 500).
"""

from __future__ import annotations

import random
from email.utils import formatdate
import time

import pytest

BD_GATE_SCOPE = "module"


def _lowest_jitter(monkeypatch):
    monkeypatch.setattr(random, "uniform", lambda a, b: min(a, b))


@pytest.mark.parametrize(
    "code", [408, 425, "408", "425"], ids=["408", "425", "str408", "str425"]
)
def test_timeout_and_too_early_are_transient(code):
    from bulk_downloader.retry_policy import classify_failure

    assert classify_failure("x", code) == "transient"


def test_other_4xx_stay_permanent():
    from bulk_downloader.retry_policy import classify_failure

    for code in (400, 404, 410, 451):
        assert classify_failure("x", code) == "permanent", code


def test_transient_408_gets_a_retry_delay():
    from bulk_downloader.retry_policy import classify_failure, compute_next_delay

    cls = classify_failure("Request Timeout", 408)
    assert compute_next_delay(cls, 0, apply_jitter=False) == 30


def test_retry_after_is_a_floor_under_default_jitter(monkeypatch):
    from bulk_downloader.retry_policy import compute_next_delay

    _lowest_jitter(monkeypatch)
    assert compute_next_delay("rate_limited", 0, retry_after_header=600) == 600


def test_class_backoff_still_jitters_down_without_retry_after(monkeypatch):
    from bulk_downloader.retry_policy import compute_next_delay

    _lowest_jitter(monkeypatch)
    assert compute_next_delay("rate_limited", 0) == 255


def test_retry_after_http_date_is_honoured():
    from bulk_downloader.retry_policy import compute_next_delay

    header = formatdate(time.time() + 3600, usegmt=True)
    d = compute_next_delay(
        "rate_limited", 0, retry_after_header=header, apply_jitter=False
    )
    assert 3500 <= d <= 3600, d


def test_retry_after_numeric_string_unchanged():
    from bulk_downloader.retry_policy import compute_next_delay

    assert (
        compute_next_delay(
            "rate_limited", 0, retry_after_header="420", apply_jitter=False
        )
        == 420
    )


@pytest.mark.parametrize(
    "value",
    [float("inf"), float("-inf"), "inf", float("nan")],
    ids=["inf", "neginf", "strinf", "nan"],
)
def test_non_finite_status_is_not_a_crash(value):
    from bulk_downloader.retry_policy import classify_failure

    assert classify_failure("x", value) == "transient"


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_retry_after_uses_class_backoff(value):
    from bulk_downloader.retry_policy import compute_next_delay

    assert (
        compute_next_delay(
            "rate_limited", 0, retry_after_header=value, apply_jitter=False
        )
        == 300
    )


def test_garbage_status_still_coerced():
    from bulk_downloader.retry_policy import classify_failure

    assert classify_failure("x", "abc") == "transient"
    assert classify_failure("x", "503") == "transient"
