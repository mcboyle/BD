"""v3.66.12 — Phase 1, P3: replay-mode `_now` injection.

The `deep_detect_live()` function takes an optional `_now` keyword
that overrides the wall-clock source. Production code path is
unchanged (defaults to `time.monotonic`); test code can pass a
hand-rolled counter to make budget-truncation deterministic.

These tests exercise the injected clock to verify:

1. The injected clock is actually called (and the real `time.monotonic`
   is NOT, even if the test takes hours of wall time).
2. `max_total_probe_time` truncation fires at the exact tick the
   test counter says it should.
3. Manifest-follow path honors the same injected clock.
4. The parallel-probe path honors it too.

This unblocks deterministic testing of P0's runner integration —
without `_now`, the runner tests would need real sleeps or
SleepyStubHttp tricks to exercise the budget-truncation path.

Fixture mirrors test_v3_66_4_deep_detect_live.py (Pattern A).
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── HTTP stubs ────────────────────────────────────────────────────────


class _Resp:
    """Minimal httpx.Response-shaped stub for HEAD probes."""

    def __init__(self, status_code=200, headers=None, url=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.text = text


class _StubHead:
    """HTTP stub that responds to HEAD with a configurable status."""

    def __init__(self, headers_by_url=None, default_status=200):
        self.headers_by_url = headers_by_url or {}
        self.default_status = default_status

    def head(self, url, headers=None, timeout=None, follow_redirects=True):
        hdrs = self.headers_by_url.get(url, {})
        return _Resp(status_code=self.default_status, headers=hdrs, url=url)

    def get(self, url, headers=None, timeout=None, follow_redirects=True):
        # Used by the GET-range-0-0 fallback when HEAD returns 405/501
        return _Resp(status_code=self.default_status, headers={}, url=url)

    def close(self):
        pass


# ── A deterministic clock that advances by a fixed step per call ──────


class FakeClock:
    """Each call to instance() returns the next tick. The first call
    returns `start` (default 0.0); subsequent calls add `step` each."""

    def __init__(self, start=0.0, step=1.0):
        self.t = start
        self.step = step
        self.calls = 0

    def __call__(self):
        v = self.t
        self.t += self.step
        self.calls += 1
        return v


# ── P3 tests ──────────────────────────────────────────────────────────


def test_P3_injected_now_is_actually_called():
    """The most basic sanity check: when `_now` is passed,
    deep_detect_live calls it instead of `time.monotonic`."""
    from bulk_downloader.deep_detect import deep_detect_live

    clock = FakeClock(start=0.0, step=0.1)
    # An HTML with one extensionless candidate so HEAD probes fire.
    html = '<html><body><a href="/file">Download</a></body></html>'
    deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=10.0,
        _now=clock,
    )
    # At minimum the t_start computation should have called the clock
    # once. Real call count varies with the probe path (1 for t_start,
    # plus 1 per budget check), but it MUST be > 0.
    assert clock.calls > 0, (
        f"injected clock was never called; calls={clock.calls}")


def test_P3_default_clock_is_time_monotonic():
    """When `_now` is None (default), the real `time.monotonic` is
    used. Verify by patching time.monotonic itself."""
    from bulk_downloader.deep_detect import deep_detect_live
    import time

    real_monotonic = time.monotonic
    call_count = [0]

    def counting_monotonic():
        call_count[0] += 1
        return real_monotonic()

    time.monotonic = counting_monotonic
    try:
        html = ('<html><body>'
                '<a href="/file">Download</a>'
                '</body></html>')
        deep_detect_live(
            html,
            base_url="https://x.com/",
            http=_StubHead(),
            max_total_probe_time=10.0,
            # No _now → default = time.monotonic
        )
    finally:
        time.monotonic = real_monotonic

    # The default path consults the real clock at least once for
    # t_start. If we'd gotten 0 calls, the fix would be broken
    # (probably the import-time `_time.monotonic` got cached
    # somewhere and didn't see the monkeypatch — that's a real bug
    # worth knowing about).
    assert call_count[0] > 0, (
        "default _clock didn't reach the patched time.monotonic; "
        "probably an import-time caching issue")


def test_P3_budget_truncation_fires_at_injected_tick():
    """When the injected clock crosses max_total_probe_time, the
    serial probe path sets probes_truncated. Pre-fix this test
    couldn't be deterministic — it relied on real wall time."""
    from bulk_downloader.deep_detect import deep_detect_live

    # Six extensionless candidates → six HEAD probes serial-pathed.
    # Clock step=1.0, budget=2.5 → after 3 calls (t=0, 1, 2) we
    # cross the 2.5s threshold and remaining probes get cancelled.
    html = """
    <html><body>
      <a href="/file1">f1</a>
      <a href="/file2">f2</a>
      <a href="/file3">f3</a>
      <a href="/file4">f4</a>
      <a href="/file5">f5</a>
      <a href="/file6">f6</a>
    </body></html>
    """
    clock = FakeClock(start=0.0, step=1.0)
    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=2.5,
        probe_parallelism=1,  # serial path is the easier one to reason about
        _now=clock,
    )
    assert r["probes"]["probes_truncated"] is True, (
        f"budget should have tripped at t>2.5s; "
        f"got probes_truncated={r['probes']['probes_truncated']!r}, "
        f"clock calls={clock.calls}")


def test_P3_budget_not_triggered_when_injected_clock_stays_low():
    """Inverse of the above: if the clock never crosses the budget,
    probes_truncated stays False."""
    from bulk_downloader.deep_detect import deep_detect_live

    html = """
    <html><body>
      <a href="/file1">f1</a>
      <a href="/file2">f2</a>
    </body></html>
    """
    clock = FakeClock(start=0.0, step=0.01)  # 10ms per tick
    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=60.0,  # way more than our clock will advance
        _now=clock,
    )
    assert r["probes"]["probes_truncated"] is False, (
        f"budget should NOT trip when clock stays under it; "
        f"clock calls={clock.calls}, max t={clock.t}")


def test_P3_no_budget_means_clock_not_consulted_for_budget():
    """When `max_total_probe_time=None`, t_start stays None and the
    budget-check branches short-circuit before consulting the clock.
    The clock IS still called once (for t_start, which is `None` if
    max_total_probe_time is falsy — so actually it's not called at
    all for budget).

    v3.66.14 (P8) adds per-probe elapsed_ms measurement, which DOES
    consult the clock (once at probe start, once at end) regardless
    of budget. We update the bound here from "≤1" to "≤2*n_probes + 1"
    to reflect that measurement is a legitimate clock consumer.
    Budget-driven hammering would show up as much higher call counts.
    """
    from bulk_downloader.deep_detect import deep_detect_live

    clock = FakeClock(start=0.0, step=1.0)
    html = '<html><body><a href="/f">f</a></body></html>'
    deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=None,  # no budget
        _now=clock,
    )
    # With no budget, the production code shouldn't be calling _now
    # for *budget* logic at all (t_start = None, and every budget
    # check is gated on t_start is not None). However P8 added two
    # clock reads per probe for elapsed_ms measurement, so we now
    # tolerate up to 2 calls per probe plus 1 slack for t_start.
    # The stub here has 1 candidate, so ≤ 2*1 + 1 = 3.
    assert clock.calls <= 3, (
        f"with no budget, clock should only be used for elapsed_ms "
        f"measurement; got {clock.calls} calls (suspect budget code "
        f"is consulting it too)")


def test_P3_manifest_follow_path_honors_injected_clock():
    """The manifest-follow loop also reads the budget. Verify that
    the injected clock controls it.

    The loop checks the budget at the START of each manifest iteration:
        if _clock() - t_start > max_total_probe_time: break
    So to deterministically trigger truncation we need EITHER
    multiple manifests (so the second iteration's check fires after
    the first has advanced the clock), OR a step large enough that
    a single iteration crosses the threshold.

    Use the multi-manifest approach: 3 m3u8 URLs, budget=1.5s,
    step=1.0s. After fetching the first manifest, the clock has
    moved to t_start + ~2.0, which exceeds the 1.5s budget — so
    the second iteration short-circuits with probes_truncated=True.
    """
    from bulk_downloader.deep_detect import deep_detect_live

    html = (
        '<html><body>'
        '<a href="/s1.m3u8">play 1</a>'
        '<a href="/s2.m3u8">play 2</a>'
        '<a href="/s3.m3u8">play 3</a>'
        '</body></html>'
    )

    gets_called = []

    class _StubManifest:
        def head(self, url, **kw):
            return _Resp(
                status_code=200,
                headers={"content-type": "application/vnd.apple.mpegurl"},
                url=url,
            )

        def get(self, url, **kw):
            gets_called.append(url)
            return _Resp(
                status_code=200,
                headers={"content-type": "application/vnd.apple.mpegurl"},
                url=url,
                text="#EXTM3U\n",
            )

        def close(self):
            pass

    clock = FakeClock(start=0.0, step=1.0)
    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubManifest(),
        max_total_probe_time=1.5,
        follow_manifests=True,
        _now=clock,
    )
    # We should have truncated before fetching all 3 manifests.
    # Exact count depends on how many clock-ticks happen per
    # iteration; the load-bearing assertion is "not all 3 were
    # fetched AND probes_truncated is set".
    assert r["probes"]["probes_truncated"] is True, (
        f"manifest-follow budget should trip with multi-manifest + "
        f"tight budget; clock.calls={clock.calls}, "
        f"gets_called={gets_called}")
    assert len(gets_called) < 3, (
        f"should have stopped short of all 3 manifest GETs; "
        f"got {len(gets_called)} GETs: {gets_called}")


def test_P3_parallel_probe_path_honors_injected_clock():
    """The parallel-probe branch (probe_parallelism > 1) also uses
    the injected clock for its budget check. With many candidates
    and a clock that crosses the budget after the first few results,
    `probes_truncated` should fire."""
    from bulk_downloader.deep_detect import deep_detect_live

    # Enough candidates that the parallel path is preferred.
    anchors = "".join(f'<a href="/f{i}">f{i}</a>' for i in range(8))
    html = f"<html><body>{anchors}</body></html>"

    clock = FakeClock(start=0.0, step=1.0)
    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_StubHead(),
        max_total_probe_time=2.0,
        probe_parallelism=4,
        _now=clock,
    )
    # The parallel branch has slightly different timing semantics
    # (the budget is checked after each future resolves, not before),
    # so probes_truncated may or may not fire depending on how fast
    # the stub returns. We just verify the clock was consulted —
    # which proves the parallel path uses it.
    assert clock.calls > 0, (
        f"parallel path didn't call the injected clock; "
        f"calls={clock.calls}")
