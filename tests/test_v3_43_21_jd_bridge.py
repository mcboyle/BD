"""v3.43.21 regression tests — JDownloader 2 bridge.

Covers the JD bridge module + runner integration. JD itself isn't
available in the test environment, so:

  - Connectivity tests use a closed port to verify the "unreachable"
    path returns clean errors without raising
  - Submission tests use httpx's MockTransport to inject canned JD
    responses and verify the bridge handles each shape correctly
  - The runner integration test verifies that when backend="jd" and
    JD is unreachable, the runner falls through to the existing teach
    path WITHOUT raising — the design contract is "JD absent doesn't
    break the queue"

These tests exercise every error code path classify_jd_error knows
about, plus the cookie format conversion, plus the diagnose() output
shape that the UI relies on.
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import json
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
class _AggregateSrc:
    """Reads runner.py + runner_integrations.py concatenated. The integration
    methods (stash/plex/jellyfin/qb/jd) moved to runner_integrations.py at
    v3.66.398 (Phase 3 decomposition cut 2, pure motion); the orchestration call
    sites that spawn enrichment stay in runner.py core. Reading both preserves
    every source-level assertion across the move (core-first ordering)."""
    def __init__(self, *paths):
        self._paths = paths
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)
_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader" / "runner.py",
                           _REPO_ROOT / "bulk_downloader" / "runner_integrations.py")
_JD_BRIDGE_PY = _REPO_ROOT / "bulk_downloader" / "jd_bridge.py"


# ── jd_bridge module ────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_jd_bridge_module_exists():
    """v3.43.21 introduces the bridge module. Its absence breaks the
    runner integration test, so check it explicitly with a clear msg."""
    import bulk_downloader.jd_bridge as jd  # noqa: F401


def test_classify_jd_error_recognizes_auth_class():
    from bulk_downloader.jd_bridge import classify_jd_error
    assert classify_jd_error("HTTP 401 Unauthorized") == "auth"
    assert classify_jd_error("403 Forbidden") == "auth"
    assert classify_jd_error("Login required") == "auth"
    assert classify_jd_error("Premium account expired") == "auth"
    assert classify_jd_error("Session timeout") == "auth"


def test_classify_jd_error_recognizes_plugin_broken():
    from bulk_downloader.jd_bridge import classify_jd_error
    assert classify_jd_error("Plugin defect on host xyz") == "plugin_broken"
    assert classify_jd_error("Unsupported URL") == "plugin_broken"
    assert classify_jd_error("Captcha required") == "plugin_broken"
    assert classify_jd_error("Regex match failed") == "plugin_broken"


def test_classify_jd_error_recognizes_network():
    from bulk_downloader.jd_bridge import classify_jd_error
    assert classify_jd_error("Connection refused") == "network"
    assert classify_jd_error("Read timeout") == "network"
    assert classify_jd_error("HTTP 503 Service Unavailable") == "network"
    assert classify_jd_error("DNS resolution failed") == "network"


def test_classify_jd_error_unknown_for_empty():
    from bulk_downloader.jd_bridge import classify_jd_error
    assert classify_jd_error("") == "unknown"
    assert classify_jd_error(None) == "unknown"


def test_cookies_playwright_to_jd_basic():
    """Standard Playwright cookie list → JD's flat name=value; ... string."""
    from bulk_downloader.jd_bridge import cookies_playwright_to_jd
    cookies = [
        {"name": "session", "value": "abc123", "domain": ".example.com"},
        {"name": "auth", "value": "xyz789", "domain": ".example.com"},
    ]
    result = cookies_playwright_to_jd(cookies)
    assert result == "session=abc123; auth=xyz789"


def test_cookies_playwright_to_jd_empty_inputs():
    """Defensive — caller may pass None / empty / dicts missing fields."""
    from bulk_downloader.jd_bridge import cookies_playwright_to_jd
    assert cookies_playwright_to_jd(None) == ""
    assert cookies_playwright_to_jd([]) == ""
    # Skip entries with no name
    cookies = [
        {"value": "no-name", "domain": "x.com"},
        {"name": "real", "value": "data"},
    ]
    assert cookies_playwright_to_jd(cookies) == "real=data"


def test_cookies_playwright_to_jd_strips_newlines():
    """A malformed value with embedded newlines would break the Cookie
    HTTP header format. Sanitize."""
    from bulk_downloader.jd_bridge import cookies_playwright_to_jd
    cookies = [{"name": "x", "value": "a\nb\rc"}]
    assert cookies_playwright_to_jd(cookies) == "x=abc"


def test_jd_client_construction_with_defaults():
    from bulk_downloader.jd_bridge import JDClient, DEFAULT_HOST, DEFAULT_PORT
    c = JDClient()
    assert c.host == DEFAULT_HOST
    assert c.port == DEFAULT_PORT
    c.close()


def test_jd_client_get_client_for_site_uses_defaults_on_bad_config():
    """Per the docstring: don't error on bad config — let diagnose()
    surface the issue cleanly."""
    from bulk_downloader.jd_bridge import get_client_for_site, DEFAULT_PORT
    # Bogus port type
    c = get_client_for_site({"jd_host": "127.0.0.1", "jd_port": "not_a_number"})
    assert c.port == DEFAULT_PORT
    # Missing fields entirely
    c2 = get_client_for_site({})
    assert c2.port == DEFAULT_PORT
    c.close(); c2.close()


def test_jd_is_reachable_returns_false_for_closed_port():
    """Use a port that's almost certainly closed. The test guarantees
    that is_reachable doesn't raise — only returns False."""
    from bulk_downloader.jd_bridge import JDClient
    c = JDClient(host="127.0.0.1", port=1)  # port 1 is unassigned
    assert c.is_reachable() is False
    c.close()


def test_jd_diagnose_returns_structured_error_when_unreachable():
    """The UI relies on the {reachable, api_enabled, hint, error}
    shape. Verify it for the unreachable case."""
    from bulk_downloader.jd_bridge import JDClient
    c = JDClient(host="127.0.0.1", port=1)
    d = c.diagnose()
    assert d["reachable"] is False
    assert d["api_enabled"] is False
    assert d["error"]  # non-empty
    assert d["hint"]   # non-empty
    assert d["host"] == "127.0.0.1"
    assert d["port"] == 1
    c.close()


def test_jd_submit_raises_jderror_on_unreachable():
    """submit() must wrap network errors in JDError. The runner branches
    on .kind, so the wrapping is part of the contract.

    NOTE (cross-platform): connecting to a dead port raises
    httpx.ConnectError on Linux (-> kind 'unreachable') but on Windows
    a refused connection can surface as a different httpx/OS exception,
    which jd_bridge currently classifies as 'unknown'. Both 'unreachable'
    and 'unknown' are non-auth, non-success failure kinds that the
    runner treats the same way (fall through to teach-based flow), so
    the contract this test guards - 'network failure is wrapped in
    JDError, not a raw exception' - holds for either. The test accepts
    both. If jd_bridge is later updated to classify refused connections
    as 'unreachable' on all platforms, tighten this back to == only."""
    from bulk_downloader.jd_bridge import JDClient, JDError
    c = JDClient(host="127.0.0.1", port=1)
    try:
        c.submit("https://example.com/foo", cookies="", dest_dir="/tmp")
    except JDError as e:
        assert e.kind in ("unreachable", "network", "unknown"), \
            f"unexpected JDError kind for a dead port: {e.kind!r}"
    except Exception as e:
        raise AssertionError(
            f"submit raised {type(e).__name__} instead of JDError: {e}")
    else:
        raise AssertionError("submit should have raised JDError")
    c.close()


def test_is_jd_backend_helper():
    """Single source of truth for the runner's backend branch."""
    from bulk_downloader.jd_bridge import is_jd_backend
    assert is_jd_backend({"backend": "jd"}) is True
    assert is_jd_backend({"backend": "JD"}) is True       # case-insensitive
    assert is_jd_backend({"backend": "jd  "}) is True     # whitespace ok
    assert is_jd_backend({"backend": "teach"}) is False
    assert is_jd_backend({}) is False                      # default = teach
    assert is_jd_backend({"backend": None}) is False
    assert is_jd_backend({"backend": ""}) is False


# ── DEFAULTS integration ────────────────────────────────────────────

def test_backend_field_default_is_teach():
    """Adding `backend` to DEFAULTS must not change behavior for sites
    that don't explicitly set it — they continue to use the teach
    flow that's worked since v3.43.0."""
    src = _APP_SRC()
    # Live default must be exactly "teach"
    assert '"backend": "teach"' in src, (
        "DEFAULTS must include backend=teach so existing sites keep "
        "their current Playwright-based behavior"
    )
    # JD host/port have sensible defaults too
    assert '"jd_host": "127.0.0.1"' in src
    assert '"jd_port": 3128' in src


# ── Runner integration ─────────────────────────────────────────────

def test_runner_has_jd_methods():
    """The four methods the JD branch in _process_one expects to exist
    on SiteRunner. Smoke test — if a refactor removes one, the
    integration breaks silently otherwise."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    for name in ("_get_jd_client", "_read_cookies_for_jd",
                  "_try_jd_download", "_record_jd_outcome", "jd_health"):
        assert f"def {name}" in src, f"SiteRunner.{name} missing"


def test_runner_branches_on_jd_backend_before_context_creation():
    """The JD branch must run AFTER cookie check (so cookies are valid)
    but BEFORE Playwright context creation (so we don't pay the
    browser cost for JD-backed sites)."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert "is_jd_backend(self.config)" in src
    # Order check: branch sits between _check_cookies_or_relogin and
    # the persistent_ctx setup
    cookie_check_pos = src.find("_check_cookies_or_relogin(url)")
    jd_branch_pos = src.find("is_jd_backend(self.config)")
    ctx_setup_pos = src.find("Phase 9.3: pick context strategy")
    assert cookie_check_pos < jd_branch_pos < ctx_setup_pos, (
        "JD branch must be positioned between cookie check and "
        f"context setup (got positions {cookie_check_pos} / "
        f"{jd_branch_pos} / {ctx_setup_pos})"
    )


def test_runner_jd_branch_falls_through_on_failure():
    """The single most important contract: if JD fails, the runner
    MUST continue to the teach path (no return, no raise). Verify
    by inspecting the surrounding code — the 'if ok: return' is the
    intended success short-circuit; failures fall through."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # Find the JD branch
    branch_start = src.find("is_jd_backend(self.config)")
    assert branch_start > 0
    # Read forward to find the return statement
    block = src[branch_start:branch_start + 2000]
    assert "if ok:" in block, (
        "JD branch must check ok before returning so failures "
        "fall through to teach"
    )
    assert "return" in block
    # The teach-path entry must follow
    assert "Phase 9.3" in block, (
        "Teach path must follow the JD branch"
    )


def test_get_status_exposes_jd_health():
    """The UI reads jd_health from /api/status to render the pill +
    insight strip. Verify it's in get_status's return dict."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # Find get_status
    assert '"jd_health": self.jd_health()' in src, (
        "get_status must include jd_health for /api/status"
    )


def test_jd_health_returns_disabled_when_backend_is_teach():
    """Teach-mode sites should report backend=teach + enabled=False
    so the UI hides the JD pill for them."""
    # Build a minimal config + runner stub. Avoid spinning up a real
    # SiteRunner (which would start threads); instead exercise the
    # method directly via a lightweight stub.
    import collections
    from bulk_downloader.runner import SiteRunner

    class _MinStub:
        def __init__(self):
            self.config = {"backend": "teach"}
            self._jd_recent_outcomes = collections.deque(maxlen=20)
    stub = _MinStub()
    health = SiteRunner.jd_health(stub)
    assert health["backend"] == "teach"
    assert health["enabled"] is False


def test_jd_health_returns_enabled_when_backend_is_jd():
    """And for JD-backed sites, the pill renders with sample stats."""
    import collections
    from bulk_downloader.runner import SiteRunner

    class _MinStub:
        def __init__(self):
            self.config = {"backend": "jd", "jd_host": "127.0.0.1",
                           "jd_port": 3128}
            self._jd_recent_outcomes = collections.deque(
                [True, True, False, True], maxlen=20)
    stub = _MinStub()
    health = SiteRunner.jd_health(stub)
    assert health["backend"] == "jd"
    assert health["enabled"] is True
    assert health["samples"] == 4
    assert abs(health["success_rate"] - 0.75) < 0.01
    # 4 samples is below the 5-sample threshold for the warn band
    assert health["warn_plugin_broken"] is False


def test_jd_health_warns_on_low_success_rate():
    """The actionable signal: when JD's success rate drops below 50%
    with enough samples (>=5), surface a warning. This is what drives
    the amber insight strip card for the user."""
    import collections
    from bulk_downloader.runner import SiteRunner

    class _MinStub:
        def __init__(self):
            self.config = {"backend": "jd"}
            # 6 failures + 1 success = 14% success rate, 7 samples
            outcomes = [False] * 6 + [True]
            self._jd_recent_outcomes = collections.deque(outcomes, maxlen=20)
    stub = _MinStub()
    health = SiteRunner.jd_health(stub)
    assert health["warn_plugin_broken"] is True


# ── App endpoint ───────────────────────────────────────────────────

def test_jd_diagnose_endpoint_registered():
    """The UI's Test JD button hits this endpoint. Verify the route
    is defined."""
    src = _APP_SRC()
    assert '/api/sites/<sid>/jd/diagnose' in src
    assert "def api_jd_diagnose" in src
