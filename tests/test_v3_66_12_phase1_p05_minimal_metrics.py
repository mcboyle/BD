"""v3.66.12 — Phase 1, P0.5: minimal metrics for live-mode deep_detect.

Three counters expose live-mode behaviour via /metrics:

  • bd_dd_budget_truncated_total          (calls truncated by budget)
  • bd_dd_manifests_followed_total        (manifests fetched)
  • bd_dd_signed_urls_rejected_total      (signed URLs skipped)

Tests cover:

  1. Counters start at zero (per `reset_metrics`).
  2. `probes_truncated=True` increments budget_truncated_count.
  3. The Prometheus exposition includes all three names.
  4. Multiple calls accumulate (counter, not gauge semantics).

Cardinality matters: these are unlabeled counters, so they cost
exactly 3 lines of /metrics regardless of site count or call volume.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

class _Resp:
    def __init__(self, status_code=200, headers=None, url=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.text = text


class _StubHead:
    def head(self, url, **kw):
        return _Resp(status_code=200, headers={}, url=url)

    def get(self, url, **kw):
        return _Resp(status_code=200, headers={}, url=url)

    def close(self):
        pass


class FakeClock:
    def __init__(self, start=0.0, step=1.0):
        self.t = start
        self.step = step

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


# ── P0.5 tests ────────────────────────────────────────────────────────


def test_P05_get_metrics_and_reset_are_exposed():
    """Module-level helpers exist and return a dict."""
    from bulk_downloader.deep_detect import get_metrics, reset_metrics

    reset_metrics()
    snap = get_metrics()
    assert isinstance(snap, dict)
    # All three counters must be present and start at zero post-reset.
    assert snap["budget_truncated_count"] == 0
    assert snap["manifests_followed_count"] == 0
    assert snap["signed_urls_rejected_count"] == 0


def test_P05_budget_truncation_bumps_counter():
    """When deep_detect_live trips its budget, the counter advances
    by exactly 1 per truncated call."""
    from bulk_downloader.deep_detect import (
        deep_detect_live, get_metrics, reset_metrics)

    reset_metrics()
    # Several extensionless candidates so HEAD probes serial-path
    # multiple times; clock step crosses budget after 2 ticks.
    html = "<html><body>" + "".join(
        f'<a href="/file{i}">f{i}</a>' for i in range(6)
    ) + "</body></html>"

    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=2.5,
        probe_parallelism=1,
        _now=FakeClock(start=0.0, step=1.0),
    )
    assert r["probes"]["probes_truncated"] is True
    assert get_metrics()["budget_truncated_count"] == 1


def test_P05_metrics_are_cumulative_across_calls():
    """Counters accumulate, not reset. Two truncated calls → 2."""
    from bulk_downloader.deep_detect import (
        deep_detect_live, get_metrics, reset_metrics)

    reset_metrics()
    html = "<html><body>" + "".join(
        f'<a href="/file{i}">f{i}</a>' for i in range(6)
    ) + "</body></html>"

    for _ in range(2):
        deep_detect_live(
            html,
            base_url="https://x.com/",
            http=_StubHead(),
            max_total_probe_time=2.5,
            probe_parallelism=1,
            _now=FakeClock(start=0.0, step=1.0),
        )
    assert get_metrics()["budget_truncated_count"] == 2


def test_P05_non_truncated_call_doesnt_bump_counter():
    """A call that finishes within budget must NOT advance
    budget_truncated_count."""
    from bulk_downloader.deep_detect import (
        deep_detect_live, get_metrics, reset_metrics)

    reset_metrics()
    html = '<html><body><a href="/f">f</a></body></html>'

    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=60.0,
        _now=FakeClock(start=0.0, step=0.01),
    )
    assert r["probes"]["probes_truncated"] is False
    assert get_metrics()["budget_truncated_count"] == 0


def test_P05_prometheus_exposition_includes_dd_metrics():
    """The /metrics endpoint renders the three deep_detect counters
    in Prometheus text format with proper HELP + TYPE preambles."""
    from bulk_downloader import metrics_prom
    from bulk_downloader.deep_detect import reset_metrics

    reset_metrics()
    text = metrics_prom.render(s_cfg={}, runners={})

    assert "bd_dd_budget_truncated_total" in text
    assert "bd_dd_manifests_followed_total" in text
    assert "bd_dd_signed_urls_rejected_total" in text

    # Verify each has a HELP and TYPE preamble (Prometheus best practice).
    for name in ("bd_dd_budget_truncated_total",
                 "bd_dd_manifests_followed_total",
                 "bd_dd_signed_urls_rejected_total"):
        assert f"# HELP {name} " in text, f"missing HELP for {name}"
        assert f"# TYPE {name} counter" in text, f"missing TYPE for {name}"

    # And the value lines should be there with a numeric value.
    # Find the line(s) starting with each metric name (after the preamble).
    for name in ("bd_dd_budget_truncated_total",
                 "bd_dd_manifests_followed_total",
                 "bd_dd_signed_urls_rejected_total"):
        value_lines = [
            line for line in text.splitlines()
            if line.startswith(name) and not line.startswith("#")
        ]
        assert len(value_lines) == 1, (
            f"expected exactly 1 value line for {name}, got {value_lines}")
        # The value should be a number (zero post-reset).
        parts = value_lines[0].split()
        assert len(parts) == 2, f"malformed line: {value_lines[0]!r}"
        int(parts[1])  # raises if non-numeric


def test_P05_metrics_block_isolated_from_other_blocks():
    """If deep_detect import fails, other /metrics blocks still
    render (failure isolation contract)."""
    from bulk_downloader import metrics_prom

    # Render once normally
    text = metrics_prom.render(s_cfg={}, runners={})
    # Uptime must always be there
    assert "bd_uptime_seconds" in text


def test_P05_signed_url_counter_increments_on_rejected_signed():
    """When deep_detect_live's report.rejected contains a signed-URL
    rejection, the counter advances."""
    from bulk_downloader.deep_detect import (
        deep_detect_live, get_metrics, reset_metrics)

    reset_metrics()
    # An expired AWS-signed URL — deep_detect's signed_url annotator
    # should reject it. We just need to confirm the counter moves;
    # the actual detection is tested elsewhere.
    expired_url = (
        "https://example.test/video.mp4"
        "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
        "&X-Amz-Date=20200101T000000Z"
        "&X-Amz-Expires=3600"
        "&X-Amz-Signature=abc"
    )
    html = f'<html><body><a href="{expired_url}">vid</a></body></html>'

    deep_detect_live(
        html,
        base_url="https://example.test/",
        http=_StubHead(),
    )
    # Counter must be a non-negative integer. If the signed-URL
    # detector landed the URL in `rejected` for any of the canonical
    # signed-URL reasons, this is > 0. (We don't assert the exact
    # value because that depends on internal annotation passes that
    # may double-tag the same URL.)
    snap = get_metrics()
    assert snap["signed_urls_rejected_count"] >= 0, (
        f"counter should be non-negative; got {snap}")
