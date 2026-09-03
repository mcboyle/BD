"""v3.43.31 regression tests — per-domain rate limiting.

Coverage:
  - Module surface
  - DomainRateLimiter: fast path (no caps), concurrent cap, per-sec
    cap, override mechanics
  - Acquire/release context manager + leak protection on exception
  - Domain extraction (uses extension_vault's eTLD+1 helper)
  - Status snapshot for UI
  - configure_from_app_config + endpoint integration
  - Runner integration: acquire wraps stream open in both single +
    parallel downloaders
  - UI hooks
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.rate_limit_seam import run_sequential_transfer  # noqa: E402


_REPO_ROOT = Path(__file__).resolve().parent.parent
class _AppAggregateSrc:
    """app.py + every app_*.py blueprint module (PHASE 4 decomposition: route
    groups moved onto Flask blueprints); glob keeps source-coupled guards green
    across all current and future app.py route-group cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "app.py"] + sorted(pkg_dir.glob("app_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_APP_PY = _AppAggregateSrc(_REPO_ROOT / "bulk_downloader")

def _APP_SRC():
    """app.py + extracted app_*.py modules (Phase 4 thin-core-shell; DECOMP-R2a kernels)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


def _bd_runner_src():
    """v3.66.404: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))
_RL_PY = _REPO_ROOT / "bulk_downloader" / "rate_limit.py"
_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"


# ── Module surface ────────────────────────────────────────────────────

def test_rate_limit_module_importable():
    import bulk_downloader.rate_limit as rl  # noqa: F401


def test_get_limiter_returns_singleton():
    """Multiple calls to get_limiter() must return the same instance
    so all callers share state. Without this, each module would have
    its own limiter and the rate limit wouldn't apply across them."""
    from bulk_downloader.rate_limit import get_limiter
    a = get_limiter()
    b = get_limiter()
    assert a is b


# ── Fast path (no caps) ───────────────────────────────────────────────

def test_acquire_is_fast_path_when_no_caps():
    """With max_concurrent=0 AND max_per_sec=0, acquire must return
    immediately without creating per-domain state — keeps overhead
    near zero in the common case.

    De-flaked: a single wall-clock sample flakes under a loaded parallel
    suite (one scheduler preemption > 50ms). Best-of-N instead: the MINIMUM
    over N acquires must be fast. A real fast-path regression slows ALL
    samples (min still catches it); preemption only inflates some (min is
    immune). perf_counter for a monotonic high-resolution clock."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    # Both defaults are 0; this should return without blocking
    best = float("inf")
    for _ in range(15):
        t0 = time.perf_counter()
        slot = rl.acquire("https://example.com/video.mp4", timeout=0.5)
        elapsed = time.perf_counter() - t0
        slot.release()
        best = min(best, elapsed)
        if best < 0.005:
            break  # clearly on the fast path; no need to keep sampling
    assert best < 0.05, f"fast-path acquire best-of-N took {best}s with no caps set"


def test_acquire_returns_context_manager():
    """The slot must work as a context manager (with-statement) so
    callers can use the natural Python pattern."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    with rl.acquire("https://example.com/v") as slot:
        # Inside: slot exists, not released
        assert slot.released is False
    # After: released
    assert slot.released is True


def test_release_is_idempotent():
    """Calling release() twice must not break — context-managers
    sometimes release in __exit__ AND have callers release manually."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    slot = rl.acquire("https://example.com/v")
    slot.release()
    slot.release()  # second call — should not raise


# ── Concurrent cap ────────────────────────────────────────────────────

def test_concurrent_cap_blocks_third_request():
    """With max_concurrent=2, the third acquire must block until one
    of the first two releases."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=2, max_per_sec=0)
    url = "https://example.com/v"
    s1 = rl.acquire(url, timeout=0.5)
    s2 = rl.acquire(url, timeout=0.5)
    # Third must block; do it in a thread so the test doesn't hang
    third_acquired = threading.Event()
    third_slot = [None]
    def try_third():
        third_slot[0] = rl.acquire(url, timeout=5.0)
        third_acquired.set()
    t = threading.Thread(target=try_third)
    t.start()
    # Give it ~100ms to confirm it's blocked
    time.sleep(0.1)
    assert not third_acquired.is_set(), (
        "Third acquire should be blocked while two slots are held"
    )
    # Release one — third should unblock
    s1.release()
    assert third_acquired.wait(timeout=2.0), "Third acquire never unblocked"
    s2.release()
    third_slot[0].release()
    t.join(timeout=1)


def test_concurrent_cap_does_not_cross_domains():
    """Two slots on example.com must NOT count against my-cdn.com.
    The whole point of per-domain is that domains are isolated."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=1, max_per_sec=0)
    # Slot on example.com
    s1 = rl.acquire("https://example.com/v", timeout=0.5)
    # Slot on different domain — should NOT block
    t0 = time.time()
    s2 = rl.acquire("https://other-cdn.com/v", timeout=0.5)
    elapsed = time.time() - t0
    assert elapsed < 0.1, (
        f"acquire on different domain took {elapsed}s; should be ~immediate"
    )
    s1.release()
    s2.release()


def test_acquire_timeout_raises():
    """When the timeout elapses with no available slot, raise
    TimeoutError. Otherwise a wedged limiter would deadlock the
    entire worker pool."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=1, max_per_sec=0)
    s1 = rl.acquire("https://example.com/v")
    # No second slot will open — confirm timeout
    try:
        rl.acquire("https://example.com/v", timeout=0.2)
    except TimeoutError as e:
        assert "example.com" in str(e)
    else:
        s1.release()
        raise AssertionError("acquire didn't time out")
    s1.release()


# ── Per-second cap ────────────────────────────────────────────────────

def test_per_sec_cap_throttles_burst():
    """With max_per_sec=2, three rapid acquires must take >0.5s total
    (the third has to wait for the bucket to refill)."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=0, max_per_sec=2)
    url = "https://example.com/v"
    t0 = time.time()
    # Acquire-and-immediately-release three times.
    # The third should wait until ~1s has passed since the first.
    for _ in range(3):
        s = rl.acquire(url, timeout=5.0)
        s.release()
    elapsed = time.time() - t0
    # At 2/sec, 3 requests take at least ~1 second (the third waits
    # for the bucket to drain the first request out at t=1.0).
    # Allow some slack: at minimum ~0.5s
    assert elapsed >= 0.5, (
        f"per-sec cap didn't throttle: 3 requests in {elapsed:.2f}s"
    )


# ── Overrides ────────────────────────────────────────────────────────

def test_per_domain_override_supersedes_global():
    """When example.com has a {max_concurrent:1} override, the global
    cap of 10 doesn't help — the third request blocks."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=10, max_per_sec=0)
    rl.set_domain_limits("example.com", max_concurrent=1, max_per_sec=0)
    s1 = rl.acquire("https://example.com/v")
    try:
        rl.acquire("https://example.com/v", timeout=0.2)
    except TimeoutError:
        pass  # Expected — override caps at 1
    else:
        raise AssertionError("override didn't supersede global cap")
    s1.release()


def test_remove_domain_limits_reverts_to_global():
    """After removing an override, the global cap applies again."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=10, max_per_sec=0)
    rl.set_domain_limits("example.com", max_concurrent=1, max_per_sec=0)
    rl.remove_domain_limits("example.com")
    # Two acquires should now work (global cap = 10)
    s1 = rl.acquire("https://example.com/v", timeout=0.5)
    s2 = rl.acquire("https://example.com/v", timeout=0.5)
    s1.release(); s2.release()


def test_get_effective_limits():
    """get_effective_limits must return per-domain override when set,
    otherwise global default."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=10, max_per_sec=5.0)
    # No override — returns global
    g = rl.get_effective_limits("anywhere.com")
    assert g["max_concurrent"] == 10
    assert g["max_per_sec"] == 5.0
    # With override — returns override
    rl.set_domain_limits("special.com", max_concurrent=2, max_per_sec=1.0)
    s = rl.get_effective_limits("special.com")
    assert s["max_concurrent"] == 2
    assert s["max_per_sec"] == 1.0


# ── Domain extraction ────────────────────────────────────────────────

def test_extract_domain_uses_etld_plus_one():
    """Domain extraction must reuse extension_vault's eTLD+1 helper
    so .co.uk and other multi-label TLDs are handled correctly."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    # subdomain.example.com -> example.com
    assert DomainRateLimiter._extract_domain(
        "https://www.example.com/path") == "example.com"
    # .co.uk handled
    assert DomainRateLimiter._extract_domain(
        "https://www.example.co.uk/path") == "example.co.uk"


def test_extract_domain_handles_garbage():
    """Non-URL inputs (None, ints, malformed strings) must return
    empty string, not crash."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    assert DomainRateLimiter._extract_domain(None) == ""
    assert DomainRateLimiter._extract_domain("") == ""
    assert DomainRateLimiter._extract_domain(12345) == ""
    assert DomainRateLimiter._extract_domain("not a url at all") == ""


def test_unextractable_url_doesnt_block():
    """If we can't extract a domain (file://, magnet:, weird input),
    the limiter must NOT block the request — fall through as no-op."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=1, max_per_sec=0)
    # Even though concurrent is 1, two magnet links should both
    # acquire instantly (no extractable domain)
    s1 = rl.acquire("magnet:?xt=urn:btih:abc")
    t0 = time.time()
    s2 = rl.acquire("magnet:?xt=urn:btih:def", timeout=0.5)
    elapsed = time.time() - t0
    assert elapsed < 0.1, (
        f"magnet links blocked at {elapsed}s; should pass through"
    )
    s1.release(); s2.release()


# ── Status snapshot ───────────────────────────────────────────────────

def test_get_status_shape():
    """The status dict must have the keys the UI expects."""
    from bulk_downloader.rate_limit import DomainRateLimiter
    rl = DomainRateLimiter()
    rl.set_global_limits(max_concurrent=4, max_per_sec=2.0)
    rl.set_domain_limits("special.com", max_concurrent=1, max_per_sec=0.5)
    # Drive a request through so a domain is registered
    with rl.acquire("https://special.com/v"):
        status = rl.get_status()
        assert status["global_max_concurrent"] == 4
        assert status["global_max_per_sec"] == 2.0
        assert "special.com" in status["domain_overrides"]
        assert any(d["domain"] == "special.com" for d in status["domains"])
        active_domain = next(d for d in status["domains"]
                              if d["domain"] == "special.com")
        assert active_domain["active"] == 1
        assert active_domain["has_override"] is True


# ── configure_from_app_config ─────────────────────────────────────────

def test_configure_from_app_config_applies_globals():
    """The startup config loader must read both global fields and
    per-domain overrides from the app_config dict."""
    from bulk_downloader.rate_limit import configure_from_app_config, get_limiter
    cfg = {
        "rate_limit_global_concurrent": 4,
        "rate_limit_global_per_sec": 2.0,
        "rate_limit_domain_overrides": {
            "shared-cdn.com": {"max_concurrent": 1, "max_per_sec": 0.5},
        },
    }
    configure_from_app_config(cfg)
    lim = get_limiter()
    # Global applied
    assert lim.get_effective_limits("any.com") == {
        "max_concurrent": 4, "max_per_sec": 2.0,
    }
    # Override applied
    assert lim.get_effective_limits("shared-cdn.com") == {
        "max_concurrent": 1, "max_per_sec": 0.5,
    }


def test_configure_handles_malformed_inputs():
    """Bad types in app_config shouldn't crash the limiter — silently
    skip malformed entries."""
    from bulk_downloader.rate_limit import configure_from_app_config
    # None instead of dict
    configure_from_app_config(None)
    # Non-numeric global
    configure_from_app_config({
        "rate_limit_global_concurrent": "garbage",
        "rate_limit_domain_overrides": "also garbage",
    })
    # Per-domain with bad inner type
    configure_from_app_config({
        "rate_limit_domain_overrides": {
            "bad.com": "not a dict",
            "good.com": {"max_concurrent": 2, "max_per_sec": 1.0},
        },
    })


# ── Endpoint registered ──────────────────────────────────────────────

def test_rate_limit_status_endpoint_registered():
    src = _APP_PY.read_text(encoding="utf-8")
    assert "/api/rate_limit/status" in src
    assert "def api_rate_limit_status" in src


def test_app_config_handler_validates_rate_limit_fields():
    """The /api/global_config POST path must validate the new fields
    instead of silently accepting garbage."""
    src = _APP_PY.read_text(encoding="utf-8")
    # The handler is far past the defaults dict, so find the actual
    # validation block (look for the error message specifically)
    pos = src.find('"rate_limit_global_concurrent must be')
    assert pos > 0, "rate_limit_global_concurrent validation error not found"
    # Validation happens here — verify the surrounding context
    nearby = src[pos:pos + 3000]
    assert "non-negative" in nearby
    # 400 status code returned
    assert ", 400" in nearby


def test_rate_limit_defaults_in_app_cfg():
    """The default _app_cfg dict must include the three new fields
    so /api/global_config GETs return them on first run."""
    src = _APP_SRC()
    assert '"rate_limit_global_concurrent"' in src
    assert '"rate_limit_global_per_sec"' in src
    assert '"rate_limit_domain_overrides"' in src


# ── Runner integration ───────────────────────────────────────────────

# THE SOURCE-TEXT ERA OF THESE TWO GATES IS OVER, AND IT ENDED BY MEASUREMENT.
# Both used to locate `def _http_download` and assert that literals appeared
# inside its body -- `from . import rate_limit`, `_rl.acquire(file_url)`,
# `_rl_slot.release()`. On 2026-09-03 a staging cut split the transfer into a
# thin `_http_download` wrapper holding three closures and an
# `_http_download_claimed` body that does the work. The literals stayed in the
# wrapper's closures. The single live `acquire_rate_limit(file_url)` call moved
# into the body neither gate reads, and DELETING IT -- switching per-domain
# rate limiting off completely -- left both gates GREEN. CLAUDE.md A7: a gate
# must see the subject it claims to judge.
#
# The replacements RUN the transfer through `tests/rate_limit_seam.py` and
# count what the seam actually did. That is immune to both halves of the old
# failure: a literal in an unreachable closure cannot satisfy an exact count,
# and moving the call between functions cannot break one. See
# `tests/mutants/v3_66_1453_w2_staginge_rate_limit_seam.json` for the mutants
# that hold this line.

def test_runner_acquires_slot_in_http_download(monkeypatch, tmp_path):
    """The single-stream downloader must take exactly one slot, and must take
    it BEFORE the httpx stream opens. Otherwise the rate limit has no effect."""
    run = run_sequential_transfer(
        monkeypatch, tmp_path, page_url="https://page.test/acquire-seam")

    # Preconditions first. A refusal before the transport would make every
    # count below trivially satisfiable, so the transfer is proved to have
    # happened before its rate-limit behaviour is judged.
    assert run.error is None, f"the probe transfer did not complete: {run.error!r}"
    assert run.result == (8, 8), (
        f"the fixture did not move its 8 scripted bytes; got {run.result!r}, "
        f"so nothing below is a statement about a real transfer")
    assert run.stream_opens == 1, (
        f"the httpx stream opened {run.stream_opens} times, not once")

    assert run.acquires == 1, (
        f"rate_limit.acquire fired {run.acquires} times across one sequential "
        f"transfer, expected exactly 1. Event log: {run.events}")
    assert run.acquired_urls == ["https://cdn.test/seam.mp4"], (
        f"the slot was taken against {run.acquired_urls}, not the file URL "
        f"whose domain the limiter is keyed on")
    assert run.milestones() == [
        "acquire", "stream-open", "stream-close", "release"], (
        f"the slot must be held ACROSS the stream, so acquire precedes the "
        f"open and release follows the close; got {run.milestones()}")


def test_runner_releases_slot_in_finally(monkeypatch, tmp_path):
    """Without the finally, an exception mid-stream would leak the slot.

    The behavioural form of that claim: inject a transport failure partway
    through the byte stream and require the slot to come back anyway, exactly
    once. A `finally:` keyword in the right function is what used to be
    asserted; this asserts the thing the keyword is there to guarantee.
    """
    from bulk_downloader.runner_transport import _HTTPDownloadFailed

    # Control: the ordinary success path gives the slot back exactly once, so
    # a "released once" verdict on the failure path is not the ambient state.
    ok = run_sequential_transfer(
        monkeypatch, tmp_path / "ok", page_url="https://page.test/release-ok")
    assert ok.error is None and ok.result == (8, 8), (
        f"the success control did not transfer: {ok.result!r} {ok.error!r}")
    assert (ok.acquires, ok.releases) == (1, 1), (
        f"success path acquired {ok.acquires} and released {ok.releases}, "
        f"expected 1 and 1. Event log: {ok.events}")

    broken = run_sequential_transfer(
        monkeypatch, tmp_path / "broken",
        page_url="https://page.test/release-on-error", raise_after=1)
    assert isinstance(broken.error, _HTTPDownloadFailed), (
        f"the mid-stream injection did not reach the transfer's error path; "
        f"got {broken.error!r} with log {broken.events}")
    assert broken.acquires == 1, (
        f"the failing transfer took {broken.acquires} slots, expected 1 -- "
        f"a leak test over an unacquired slot proves nothing")
    assert broken.releases == 1, (
        f"a transfer that failed mid-stream released its slot "
        f"{broken.releases} times, expected exactly 1. Anything less leaks "
        f"the slot and eventually deadlocks the domain. Log: {broken.events}")
    assert broken.milestones() == [
        "acquire", "stream-open", "stream-error", "stream-close", "release"], (
        f"the release must follow the failure, not precede it; got "
        f"{broken.milestones()}")


def test_parallel_downloader_acquires_per_worker():
    """In the parallel downloader, each worker thread must acquire
    its own slot — otherwise N-chunk downloads consume only one slot
    while making N concurrent requests, defeating the rate limit."""
    src = _bd_runner_src()
    fn_pos = src.find("def _http_download_parallel(")
    assert fn_pos > 0
    # v3.66.391: boundary-isolate the method body (slice to the next
    # top-level "    def ") rather than a fixed byte window. The Track-K
    # (v3.66.390) fail-closed VPN-proxy block grew _http_download_parallel
    # and pushed the still-present release() past the over-fit 12000-char
    # cutoff (the call is intact and still in its guarded finally; only the
    # magic window broke). See KB_FAILURE_ANNOTATION_v3_66_390_rate_limit.md.
    end = src.find("\n    def ", fn_pos + 10)
    body = src[fn_pos:end if end > 0 else fn_pos + 20000]
    assert "_rl_worker.acquire" in body
    # And releases in a finally so chunk errors don't leak
    assert "_rl_worker_slot.release()" in body
    assert "finally:" in body


# ── UI ────────────────────────────────────────────────────────────────

