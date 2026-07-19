"""v3.43.37 regression tests — per-URL retry policy with failure
classification + exponential backoff + jitter.

Coverage:
  - classify_failure: status codes, message patterns, defaults
  - compute_next_delay: exponential growth, max clamp, jitter
    bounds, attempt budget enforcement, Retry-After honoring
  - should_retry: cap enforcement
  - parse_retry_after: integer + HTTP-date forms, bounds
  - Runner integration: classify path in _auto_retry_scan,
    legacy fallback when disabled
  - App wiring: CFG_FIELDS, defaults, endpoint
  - UI: toggle + load/save + help modal
  - Adversarial: malformed inputs don't crash
"""
from __future__ import annotations

import random
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_RP_PY = _REPO_ROOT / "bulk_downloader" / "retry_policy.py"
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
class _AggregateSrc:
    """runner.py + every runner_*.py mixin module (PHASE 3 decomposition v3.66.397+
    moved SiteRunner methods into siblings); glob keeps source-coupled guards green
    across all current and future runner cuts."""
    def __init__(self, pkg_dir):
        self._paths = [pkg_dir / "runner.py"] + sorted(pkg_dir.glob("runner_*.py"))
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader")


# ── Module surface ────────────────────────────────────────────────────

def test_retry_policy_module_importable():
    import bulk_downloader.retry_policy as rp  # noqa: F401


def test_get_all_classes():
    from bulk_downloader.retry_policy import get_all_classes
    classes = get_all_classes()
    assert "transient" in classes
    assert "rate_limited" in classes
    assert "auth" in classes
    assert "permanent" in classes


def test_get_class_config_returns_copy():
    """Mutating the returned config must NOT affect the module state.
    Without copying, callers could break the global policy."""
    from bulk_downloader.retry_policy import get_class_config
    cfg1 = get_class_config("transient")
    cfg1["max_attempts"] = 999
    cfg2 = get_class_config("transient")
    assert cfg2["max_attempts"] != 999


# ── classify_failure ──────────────────────────────────────────────────

def test_classify_status_404_permanent():
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(status_code=404) == "permanent"
    assert classify_failure(status_code=410) == "permanent"


def test_classify_status_401_403_auth():
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(status_code=401) == "auth"
    assert classify_failure(status_code=403) == "auth"


def test_classify_status_429_rate_limited():
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(status_code=429) == "rate_limited"


def test_classify_status_5xx_transient():
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(status_code=500) == "transient"
    assert classify_failure(status_code=502) == "transient"
    assert classify_failure(status_code=503) == "transient"
    assert classify_failure(status_code=504) == "transient"
    # Cloudflare-style 5xx variants
    assert classify_failure(status_code=522) == "transient"
    assert classify_failure(status_code=524) == "transient"


def test_classify_unrecognized_4xx_permanent():
    """Non-auth, non-rate-limit 4xx codes (e.g. 400, 451) are
    treated as permanent — retrying a bad request is pointless."""
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(status_code=400) == "permanent"
    assert classify_failure(status_code=451) == "permanent"


def test_classify_unrecognized_5xx_transient():
    """Unknown 5xx codes get the benefit of the doubt — treated as
    transient. Server's problem, probably temporary."""
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(status_code=599) == "transient"


def test_classify_message_patterns_permanent():
    from bulk_downloader.retry_policy import classify_failure
    for msg in [
        "video not found", "404 not found", "page not found",
        "this video is private", "no such file",
        "removed by uploader",
    ]:
        assert classify_failure(message=msg) == "permanent", f"missed: {msg}"


def test_classify_message_patterns_auth():
    from bulk_downloader.retry_policy import classify_failure
    for msg in [
        "401 unauthorized", "login required", "session expired",
        "please log in", "must be a member",
        "Cookie validation failed",
    ]:
        assert classify_failure(message=msg) == "auth", f"missed: {msg}"


def test_classify_message_patterns_rate_limited():
    from bulk_downloader.retry_policy import classify_failure
    for msg in [
        "429 too many requests", "rate limit exceeded",
        "Rate-limit response from CDN", "slow down",
        "you are being throttled",
    ]:
        assert classify_failure(message=msg) == "rate_limited", f"missed: {msg}"


def test_classify_message_patterns_transient():
    from bulk_downloader.retry_policy import classify_failure
    for msg in [
        "connection reset", "timeout reading response",
        "broken pipe", "503 service unavailable",
        "bad gateway", "SSL handshake failed",
        "network error: dial tcp",
    ]:
        assert classify_failure(message=msg) == "transient", f"missed: {msg}"


def test_classify_unknown_defaults_transient():
    """Unrecognized errors default to transient — better to retry an
    unknown error a few times than to silently lose URLs."""
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(message="some weird thing") == "transient"
    assert classify_failure() == "transient"  # no info at all


def test_classify_status_takes_precedence_over_message():
    """A 404 with a "rate limit" message should still be permanent —
    the explicit status code wins."""
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(
        message="rate limit exceeded", status_code=404) == "permanent"


def test_classify_case_insensitive():
    from bulk_downloader.retry_policy import classify_failure
    assert classify_failure(message="VIDEO NOT FOUND") == "permanent"
    assert classify_failure(message="Too Many Requests") == "rate_limited"


# ── compute_next_delay ────────────────────────────────────────────────

def test_compute_delay_exponential():
    """Transient: 30s, 60s, 120s, 240s, 480s, 900s (clamped)."""
    from bulk_downloader.retry_policy import compute_next_delay
    delays = [compute_next_delay("transient", a, apply_jitter=False)
              for a in range(6)]
    assert delays == [30, 60, 120, 240, 480, 900]


def test_compute_delay_clamps_at_max():
    """Past the max_delay_s threshold, all delays clamp."""
    from bulk_downloader.retry_policy import compute_next_delay
    # Transient max is 900 (15 min); attempt=5 already hits it
    d5 = compute_next_delay("transient", 5, apply_jitter=False)
    assert d5 == 900


def test_compute_delay_returns_zero_past_max_attempts():
    """attempt >= max_attempts → 0 means 'give up'."""
    from bulk_downloader.retry_policy import compute_next_delay
    # Transient max_attempts=6; attempt=6 should be 0
    assert compute_next_delay("transient", 6, apply_jitter=False) == 0
    assert compute_next_delay("transient", 100, apply_jitter=False) == 0


def test_compute_delay_permanent_always_zero():
    """Permanent class never retries, regardless of attempt."""
    from bulk_downloader.retry_policy import compute_next_delay
    assert compute_next_delay("permanent", 0, apply_jitter=False) == 0
    assert compute_next_delay("permanent", 5, apply_jitter=False) == 0


def test_compute_delay_jitter_within_bounds():
    """Jitter is ±15% — verify the bounds. Run many trials."""
    from bulk_downloader.retry_policy import compute_next_delay
    base = 60  # attempt=1 transient
    samples = [compute_next_delay("transient", 1, apply_jitter=True)
                for _ in range(100)]
    min_expected = int(base * 0.85)
    max_expected = int(base * 1.15)
    for s in samples:
        assert min_expected - 1 <= s <= max_expected + 1, (
            f"jitter out of bounds: {s} not in [{min_expected}, {max_expected}]"
        )
    # And we got variance
    assert len(set(samples)) > 10, "jitter not producing variance"


def test_compute_delay_jitter_floors_at_one():
    """A small base + negative jitter could produce 0, which means
    'no retry' — we must floor at 1."""
    from bulk_downloader.retry_policy import compute_next_delay
    # Force min jitter: monkey-patch random just for this test
    random.seed(42)
    for _ in range(20):
        d = compute_next_delay("transient", 0, apply_jitter=True)
        assert d >= 1, f"jitter produced {d}; must floor at 1"


def test_compute_delay_honors_retry_after_when_rate_limited():
    """Server-provided Retry-After overrides class backoff for
    rate-limited responses."""
    from bulk_downloader.retry_policy import compute_next_delay
    d = compute_next_delay("rate_limited", attempt=0,
                            retry_after_header=420,
                            apply_jitter=False)
    assert d == 420


def test_compute_delay_clamps_retry_after_to_max():
    """A malicious / broken server saying 'retry in 7 days' shouldn't
    get to dictate our policy — clamp at max_delay_s."""
    from bulk_downloader.retry_policy import (
        compute_next_delay, get_class_config,
    )
    cfg = get_class_config("rate_limited")
    huge = cfg["max_delay_s"] * 100
    d = compute_next_delay("rate_limited", attempt=0,
                            retry_after_header=huge,
                            apply_jitter=False)
    assert d == cfg["max_delay_s"]


def test_compute_delay_ignores_retry_after_for_other_classes():
    """The Retry-After hint only applies when the class is
    rate_limited. A transient 500 with a Retry-After header still
    uses the transient backoff curve."""
    from bulk_downloader.retry_policy import compute_next_delay
    d = compute_next_delay("transient", attempt=0,
                            retry_after_header=3600,
                            apply_jitter=False)
    # Transient base is 30s; should ignore the 3600
    assert d == 30


# ── should_retry ─────────────────────────────────────────────────────

def test_should_retry_within_budget():
    from bulk_downloader.retry_policy import should_retry
    assert should_retry("transient", 0) is True
    assert should_retry("transient", 5) is True
    assert should_retry("transient", 6) is False
    assert should_retry("permanent", 0) is False
    assert should_retry("auth", 2) is True
    assert should_retry("auth", 3) is False


def test_should_retry_unknown_class_defaults():
    """An unknown class falls back to transient policy (be optimistic)."""
    from bulk_downloader.retry_policy import should_retry
    assert should_retry("unknown_class", 0) is True


# ── parse_retry_after ────────────────────────────────────────────────

def test_parse_retry_after_integer():
    from bulk_downloader.retry_policy import parse_retry_after
    assert parse_retry_after("120") == 120
    assert parse_retry_after("0") == 0
    assert parse_retry_after("3600") == 3600


def test_parse_retry_after_rejects_garbage():
    from bulk_downloader.retry_policy import parse_retry_after
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("not a number") is None
    # Bounds: huge values become None
    assert parse_retry_after("9999999") is None
    # Negative
    assert parse_retry_after("-100") is None


def test_parse_retry_after_http_date():
    """The HTTP-date form computes (header_date - now)."""
    from bulk_downloader.retry_policy import parse_retry_after
    from datetime import datetime, timedelta, timezone
    # ~2 minutes from now, formatted as HTTP-date
    future = datetime.now(timezone.utc) + timedelta(minutes=2)
    hdr = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    result = parse_retry_after(hdr)
    assert result is not None
    # Allow 10s tolerance for test timing
    assert 110 <= result <= 130


def test_parse_retry_after_past_date_becomes_zero():
    """A date in the past — server is just saying 'right now'.
    We use 0 (no wait)."""
    from bulk_downloader.retry_policy import parse_retry_after
    from datetime import datetime, timedelta, timezone
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    hdr = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
    result = parse_retry_after(hdr)
    # 0 (clamped to non-negative) OR None — both acceptable
    assert result is None or result == 0


# ── Runner integration ───────────────────────────────────────────────

def test_runner_uses_retry_policy():
    """_auto_retry_scan must import + use retry_policy when classify
    is on."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _auto_retry_scan")
    assert pos > 0
    body = src[pos:pos + 8000]
    assert "from . import retry_policy" in body
    assert "auto_retry_classify" in body
    assert "classify_failure" in body
    assert "compute_next_delay" in body


def test_runner_falls_back_to_legacy_schedule():
    """When auto_retry_classify=False, the legacy uniform-schedule
    path must still work — backward compat."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _auto_retry_scan")
    body = src[pos:pos + 8000]
    # Both paths visible: legacy uses `schedule[min(...)]`,
    # new uses `compute_next_delay`
    assert "Legacy uniform-schedule path" in body or "uniform" in body.lower()
    assert "schedule[min(" in body  # legacy index lookup


def test_runner_per_class_max_attempts_caps_below_site_max():
    """A permanent failure class has max_attempts=0; the runner must
    respect that even when the site_wide auto_retry_max_attempts is
    higher. Otherwise permanent 404s would keep retrying."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _auto_retry_scan")
    body = src[pos:pos + 8000]
    assert "effective_max" in body
    assert "min(max_attempts" in body


def test_runner_permanent_failure_logs_once():
    """First-time encounter with a permanent failure should log it
    (so the user can see why), but subsequent scans must skip
    silently to avoid log spam."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _auto_retry_scan")
    body = src[pos:pos + 8000]
    # Look for the sentinel
    assert "next_auto_retry_at" in body
    assert "-1" in body  # sentinel "never retry"


# ── App wiring ───────────────────────────────────────────────────────

def test_cfg_fields_has_auto_retry_classify():
    src = _APP_SRC()
    import re
    m = re.search(r"CFG_FIELDS\s*=\s*\[", src)
    start = m.end()
    depth = 1
    pos = start
    while pos < len(src) and depth > 0:
        if src[pos] == "[":
            depth += 1
        elif src[pos] == "]":
            depth -= 1
        pos += 1
    fields = src[start:pos - 1]
    assert '"auto_retry_classify"' in fields


def test_auto_retry_classify_default_true():
    """Default ON for new sites — they get smart retries automatically."""
    src = _APP_SRC()
    assert '"auto_retry_classify": True' in src


def test_retry_policy_endpoints_registered():
    src = _APP_PY.read_text(encoding="utf-8")
    assert "/api/retry_policy" in src
    assert "def api_retry_policy" in src
    assert "/api/retry_policy/classify" in src
    assert "def api_retry_policy_classify" in src


# ── UI ────────────────────────────────────────────────────────────────





# ── Adversarial ──────────────────────────────────────────────────────

def test_classify_handles_none_message():
    """classify_failure must accept None for message without crashing."""
    from bulk_downloader.retry_policy import classify_failure
    result = classify_failure(message=None, status_code=None)
    assert result == "transient"  # default


def test_classify_handles_non_string_message():
    """If the caller passes a non-string (e.g. a dict from a JSON
    error response), classify must not crash."""
    from bulk_downloader.retry_policy import classify_failure
    for bad in [None, 12345, [], {}, 3.14, b"bytes"]:
        try:
            result = classify_failure(message=bad)
            assert isinstance(result, str)
        except (TypeError, AttributeError):
            # Acceptable: we lowercase the message; non-string types
            # could fail at .lower(). If so, the impl needs a guard.
            # Verify the impl handles it:
            raise AssertionError(
                f"classify_failure({bad!r}) crashed — needs defensive guard")


def test_compute_delay_handles_unknown_class():
    """Unknown class falls back to transient defaults (defensive)."""
    from bulk_downloader.retry_policy import compute_next_delay
    d = compute_next_delay("nonexistent_class", attempt=0,
                            apply_jitter=False)
    # Transient base = 30; if unknown maps to transient, result is 30
    assert d == 30


def test_compute_delay_handles_negative_attempt():
    """Negative attempt could happen from a corrupted job dict.
    Must not crash."""
    from bulk_downloader.retry_policy import compute_next_delay
    # Behavior is implementation-defined but must not raise
    try:
        result = compute_next_delay("transient", attempt=-1,
                                      apply_jitter=False)
        assert isinstance(result, int)
    except Exception as e:
        raise AssertionError(
            f"compute_next_delay with negative attempt crashed: {e}")


# ── Adversarial regression locks (build-time audit catches) ─────────

def test_classify_handles_non_int_status_code():
    """A JSON-deserialized error response could have status_code as
    a string. The 400 <= sc < 500 comparison would crash on string.
    Lock in the isinstance + int() coercion fix."""
    from bulk_downloader.retry_policy import classify_failure
    # Non-int status_code values that should not crash
    for sc in ["404", "string", [], {}, 3.14]:
        try:
            result = classify_failure(message="", status_code=sc)
            assert isinstance(result, str)
        except (TypeError, ValueError) as e:
            raise AssertionError(
                f"classify_failure(status_code={sc!r}) crashed: {e}")


def test_compute_delay_handles_non_int_attempt():
    """A corrupted job dict could have attempt as None or string.
    The attempt >= max_attempts comparison would crash."""
    from bulk_downloader.retry_policy import compute_next_delay
    for attempt in [None, "string", "5", [], {}]:
        try:
            result = compute_next_delay("transient", attempt,
                                          apply_jitter=False)
            assert isinstance(result, int)
        except (TypeError, ValueError) as e:
            raise AssertionError(
                f"compute_next_delay(attempt={attempt!r}) crashed: {e}")


def test_compute_delay_handles_non_int_retry_after():
    """Header values from urllib can arrive as strings. The
    `retry_after_header > 0` comparison would crash."""
    from bulk_downloader.retry_policy import compute_next_delay
    for ra in ["120", "garbage", [], {}]:
        try:
            result = compute_next_delay("rate_limited", 0,
                                          retry_after_header=ra,
                                          apply_jitter=False)
            assert isinstance(result, int)
        except (TypeError, ValueError) as e:
            raise AssertionError(
                f"compute_next_delay(retry_after_header={ra!r}) crashed: {e}")
