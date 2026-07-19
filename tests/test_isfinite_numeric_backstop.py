"""Numeric isfinite backstop -- reject a non-finite (NaN/inf) config-sourced
float before it silently defeats a safety gate.

Bundled findings (both fixed by a ``math.isfinite`` guard after the ``float()``):
  F-COREBD18-01  admission._disk_hold: a NaN ``disk_threshold_gb`` makes
                 ``free < NaN`` always False -> the low-disk admission hold is
                 silently disabled.
  F-REC03-03     _common._honeypot_score_threshold: a NaN threshold passes both
                 bounds checks (nan<=0 and nan>1 are each False) and is returned,
                 so ``score < NaN`` is always False -> honeypot dropping disabled.

Pure/deterministic unit tests: an injected free-space fn and an env var, no I/O.
"""
import os

from bulk_downloader import admission
from bulk_downloader.provider_resolve_impl import _common


def test_disk_hold_rejects_nan_threshold():
    # free space 0.0 is below any sane default. With a NaN threshold the
    # UNGUARDED code computes `0.0 < NaN` -> False -> returns None (no hold).
    # The isfinite guard must fall back to the default so the hold fires.
    cfg = {"download_dir": "/tmp", "disk_threshold_gb": "nan"}
    res = admission._disk_hold(cfg, disk_free_fn=lambda _d: 0.0)
    assert res == "low_disk", (
        "a NaN disk_threshold_gb must not disable the low-disk hold "
        "(F-COREBD18-01)"
    )
    # a finite threshold with ample free space still behaves normally
    # (no over-block regression):
    cfg_ok = {"download_dir": "/tmp", "disk_threshold_gb": "1.0"}
    assert admission._disk_hold(cfg_ok, disk_free_fn=lambda _d: 5.0) is None


def test_honeypot_score_threshold_rejects_nan():
    prev = os.environ.get("BD_HONEYPOT_SCORE_THRESHOLD")
    os.environ["BD_HONEYPOT_SCORE_THRESHOLD"] = "nan"
    try:
        assert _common._honeypot_score_threshold() is None, (
            "a NaN BD_HONEYPOT_SCORE_THRESHOLD must be rejected, not returned "
            "as the threshold (F-REC03-03)"
        )
        # a valid in-range value still parses:
        os.environ["BD_HONEYPOT_SCORE_THRESHOLD"] = "0.5"
        assert _common._honeypot_score_threshold() == 0.5
    finally:
        if prev is None:
            os.environ.pop("BD_HONEYPOT_SCORE_THRESHOLD", None)
        else:
            os.environ["BD_HONEYPOT_SCORE_THRESHOLD"] = prev
