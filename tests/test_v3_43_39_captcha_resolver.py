"""v3.43.39 regression tests — deep captcha integration.

Coverage:
  - Module surface
  - detect_captcha_type: returns the right string for each
    captcha kind based on DOM signatures
  - extract_sitekey: pulls the sitekey for each type from the
    typical DOM placement
  - submit_2captcha + submit_capsolver: shape, error paths
    (without actually hitting the network — mocked)
  - solve(): dispatches to the right provider
  - inject_token: chooses the right JS for each type
  - CaptchaStats: record_submission / solved / failed; snapshot
    derived fields
  - Runner integration: _try_captcha_solve replaces _try_turnstile_solve;
    _handle_captcha_check records type
  - App wiring: by_type field in /captcha/stats
  - UI: badge for captcha_type on rows; per-type breakdown in
    stats modal
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_CR_PY = _REPO_ROOT / "bulk_downloader" / "captcha_resolver.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"


class _AggregateSrc:
    """Reads runner.py + runner_challenge.py concatenated. The captcha/turnstile
    methods (_handle_captcha_check/_has_captcha/_try_*) moved to
    runner_challenge.py at v3.66.399 (PHASE 3 runner cut 3); structural source
    assertions read the aggregate so they still find the moved bodies."""
    def __init__(self, *paths):
        self._paths = paths
    def read_text(self, encoding="utf-8"):
        return "\n".join(p.read_text(encoding=encoding) for p in self._paths)


_RUNNER_PY = _AggregateSrc(_REPO_ROOT / "bulk_downloader" / "runner.py",
                           _REPO_ROOT / "bulk_downloader" / "runner_challenge.py")
_APP_JS = _REPO_ROOT / "bulk_downloader" / "static" / "app.js"


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_captcha_resolver_module_importable():
    import bulk_downloader.captcha_resolver as cr  # noqa: F401


def test_required_exports():
    """The module exposes the public API the runner depends on."""
    from bulk_downloader import captcha_resolver as cr
    for name in ("detect_captcha_type", "extract_sitekey",
                  "submit_2captcha", "submit_capsolver", "solve",
                  "inject_token", "CaptchaStats"):
        assert hasattr(cr, name), f"missing export: {name}"


# ── detect_captcha_type ──────────────────────────────────────────────

class _FakeLocator:
    """Stand-in for Playwright Locator — configurable count + visibility."""
    def __init__(self, count, visible=True):
        self._count = count
        self._visible = visible
    @property
    def first(self): return self
    def count(self): return self._count
    def is_visible(self, timeout=300): return self._visible


class _FakePage:
    """Stand-in for Playwright Page. Maps selector → (count, visible)."""
    def __init__(self, selector_map=None):
        self._map = selector_map or {}
    def locator(self, sel):
        entry = self._map.get(sel, (0, False))
        if isinstance(entry, tuple):
            return _FakeLocator(*entry)
        return _FakeLocator(entry, True)


def test_detect_turnstile():
    """Turnstile detection via iframe src match."""
    from bulk_downloader.captcha_resolver import detect_captcha_type
    page = _FakePage({"iframe[src*='challenges.cloudflare.com']": (1, True)})
    assert detect_captcha_type(page) == "turnstile"


def test_detect_hcaptcha():
    from bulk_downloader.captcha_resolver import detect_captcha_type
    page = _FakePage({"iframe[src*='hcaptcha.com']": (1, True)})
    assert detect_captcha_type(page) == "hcaptcha"


def test_detect_recaptcha_v2():
    from bulk_downloader.captcha_resolver import detect_captcha_type
    page = _FakePage({
        ".g-recaptcha[data-sitekey]:not([data-size='invisible'])": (1, True),
    })
    assert detect_captcha_type(page) == "recaptcha2"


def test_detect_recaptcha_v3():
    """v3 is invisible — needs the script-tag signature."""
    from bulk_downloader.captcha_resolver import detect_captcha_type
    page = _FakePage({
        "script[src*='recaptcha/api.js?render=']": (1, True),
    })
    # recaptcha3 doesn't require visibility (the widget is invisible)
    assert detect_captcha_type(page) == "recaptcha3"


def test_detect_no_captcha_returns_none():
    from bulk_downloader.captcha_resolver import detect_captcha_type
    page = _FakePage({})
    assert detect_captcha_type(page) is None


def test_detect_handles_page_exceptions():
    """A flaky page where locator() raises must NOT crash detection."""
    from bulk_downloader.captcha_resolver import detect_captcha_type

    class _BrokenPage:
        def locator(self, sel): raise RuntimeError("page closed")

    assert detect_captcha_type(_BrokenPage()) is None


def test_detect_invisible_widget_counts():
    """When count > 0 but is_visible raises (e.g. for <script> tags),
    we treat presence as enough."""
    from bulk_downloader.captcha_resolver import detect_captcha_type

    class _WeirdLocator:
        @property
        def first(self): return self
        def count(self): return 1
        def is_visible(self, timeout=300):
            raise RuntimeError("not a visible element")

    class _Page:
        def locator(self, sel):
            return _WeirdLocator()

    # Any matching selector should still result in a detection
    result = detect_captcha_type(_Page())
    assert result is not None


# ── extract_sitekey ──────────────────────────────────────────────────

class _EvalPage:
    """Stand-in for a Playwright page that just runs `evaluate` against
    a stub mapping."""
    def __init__(self, sitekey: str | None):
        self._sitekey = sitekey
    def evaluate(self, js, *args):
        return self._sitekey


def test_extract_sitekey_returns_value():
    from bulk_downloader.captcha_resolver import extract_sitekey
    page = _EvalPage("0x4AAAA-test-key")
    for cap_type in ("turnstile", "hcaptcha", "recaptcha2", "recaptcha3"):
        assert extract_sitekey(page, cap_type) == "0x4AAAA-test-key"


def test_extract_sitekey_unknown_type_returns_none():
    from bulk_downloader.captcha_resolver import extract_sitekey
    page = _EvalPage("ignored")
    assert extract_sitekey(page, "unknown_kind") is None
    assert extract_sitekey(page, "") is None
    assert extract_sitekey(page, None) is None


def test_extract_sitekey_handles_evaluate_failure():
    """Defensive — page.evaluate raising must produce None, not crash."""
    from bulk_downloader.captcha_resolver import extract_sitekey

    class _BrokenPage:
        def evaluate(self, js, *args):
            raise RuntimeError("eval failed")

    assert extract_sitekey(_BrokenPage(), "turnstile") is None


# ── submit_2captcha / submit_capsolver (mocked) ──────────────────────

def test_submit_2captcha_rejects_unknown_type():
    """A type not in _2CAPTCHA_METHODS must raise _ProviderError
    immediately — don't waste an API call."""
    from bulk_downloader.captcha_resolver import submit_2captcha, _ProviderError
    try:
        submit_2captcha("api-key", "made_up_type", "site-key", "url")
    except _ProviderError as e:
        assert "unsupported" in str(e).lower()


def test_submit_2captcha_rejects_empty_key():
    from bulk_downloader.captcha_resolver import submit_2captcha, _ProviderError
    try:
        submit_2captcha("", "turnstile", "site-key", "url")
    except _ProviderError as e:
        assert "api_key" in str(e).lower()


def test_submit_capsolver_rejects_unknown_type():
    from bulk_downloader.captcha_resolver import submit_capsolver, _ProviderError
    try:
        submit_capsolver("api-key", "made_up", "site-key", "url")
    except _ProviderError as e:
        assert "unsupported" in str(e).lower()


def test_solve_routes_to_2captcha():
    """solve() must call submit_2captcha when provider=2captcha."""
    from bulk_downloader import captcha_resolver as cr
    with mock.patch.object(cr, "submit_2captcha",
                             return_value="tok") as m:
        result = cr.solve("2captcha", "key", "turnstile", "sk", "url")
        assert result == "tok"
        m.assert_called_once()


def test_solve_routes_to_capsolver():
    from bulk_downloader import captcha_resolver as cr
    with mock.patch.object(cr, "submit_capsolver",
                             return_value="tok") as m:
        result = cr.solve("capsolver", "key", "hcaptcha", "sk", "url")
        assert result == "tok"
        m.assert_called_once()


def test_solve_unknown_provider_raises():
    from bulk_downloader import captcha_resolver as cr
    try:
        cr.solve("turbosolver", "key", "turnstile", "sk", "url")
    except cr._ProviderError as e:
        assert "unknown provider" in str(e).lower()


# ── inject_token ─────────────────────────────────────────────────────

def test_inject_token_returns_true_when_field_found():
    """inject_token returns True when page.evaluate reports at least
    one field was found."""
    from bulk_downloader.captcha_resolver import inject_token

    class _Page:
        def __init__(self, count): self._count = count
        def evaluate(self, js, *args): return self._count

    for cap_type in ("turnstile", "hcaptcha", "recaptcha2", "recaptcha3"):
        assert inject_token(_Page(1), cap_type, "tok") is True
        assert inject_token(_Page(0), cap_type, "tok") is False


def test_inject_token_unknown_type_returns_false():
    from bulk_downloader.captcha_resolver import inject_token

    class _Page:
        def evaluate(self, js, *args): return 1

    assert inject_token(_Page(), "unknown", "tok") is False
    assert inject_token(_Page(), "turnstile", "") is False
    assert inject_token(_Page(), "", "tok") is False


def test_inject_token_handles_evaluate_failure():
    from bulk_downloader.captcha_resolver import inject_token

    class _Page:
        def evaluate(self, js, *args):
            raise RuntimeError("eval failed")

    assert inject_token(_Page(), "turnstile", "tok") is False


# ── CaptchaStats ─────────────────────────────────────────────────────

def test_stats_records_submission():
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    s.record_submission("turnstile", "2captcha")
    snap = s.snapshot()
    assert snap["turnstile"]["2captcha"]["submitted"] == 1


def test_stats_records_solved_with_timing():
    """record_solved must update the solved counter AND track the
    average solve time."""
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    s.record_submission("hcaptcha", "capsolver")
    s.record_solved("hcaptcha", "capsolver", elapsed_s=25.0)
    s.record_submission("hcaptcha", "capsolver")
    s.record_solved("hcaptcha", "capsolver", elapsed_s=35.0)
    snap = s.snapshot()
    entry = snap["hcaptcha"]["capsolver"]
    assert entry["solved"] == 2
    assert entry["avg_solve_time_s"] == 30.0  # (25+35)/2
    assert entry["success_rate"] == 100.0


def test_stats_records_failed():
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    s.record_submission("recaptcha2", "2captcha")
    s.record_failed("recaptcha2", "2captcha", "bad key")
    snap = s.snapshot()
    entry = snap["recaptcha2"]["2captcha"]
    assert entry["failed"] == 1
    assert entry["last_failure"] == "bad key"
    assert entry["success_rate"] == 0.0


def test_stats_partial_success_rate():
    """3 solved / 5 submitted → 60% success_rate."""
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    for _ in range(5):
        s.record_submission("turnstile", "2captcha")
    for _ in range(3):
        s.record_solved("turnstile", "2captcha", elapsed_s=10)
    snap = s.snapshot()
    assert snap["turnstile"]["2captcha"]["success_rate"] == 60.0


def test_stats_separate_per_type_and_provider():
    """Stats for different (type, provider) combinations don't leak."""
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    s.record_submission("turnstile", "2captcha")
    s.record_submission("hcaptcha", "capsolver")
    s.record_solved("turnstile", "2captcha", elapsed_s=10)
    snap = s.snapshot()
    assert snap["turnstile"]["2captcha"]["solved"] == 1
    # hcaptcha was submitted but never solved → solved counter absent
    # (record_solved is what creates the key). Use .get() defensively.
    assert snap["hcaptcha"]["capsolver"].get("solved", 0) == 0


def test_stats_snapshot_drops_running_accumulator():
    """The internal total_solve_time_s shouldn't appear in the
    UI-facing snapshot — only the derived avg."""
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    s.record_submission("turnstile", "2captcha")
    s.record_solved("turnstile", "2captcha", elapsed_s=10)
    snap = s.snapshot()
    entry = snap["turnstile"]["2captcha"]
    assert "total_solve_time_s" not in entry
    assert "avg_solve_time_s" in entry


# ── Runner integration ───────────────────────────────────────────────

def test_runner_has_try_captcha_solve():
    """The new type-aware method must exist."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    assert "def _try_captcha_solve" in src


def test_legacy_turnstile_solve_kept_as_alias():
    """For backward compat, _try_turnstile_solve must still exist
    and delegate to the new method."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    pos = src.find("def _try_turnstile_solve")
    assert pos > 0
    # Find the body — first 1000 chars after signature
    body = src[pos:pos + 1000]
    # Must delegate to _try_captcha_solve
    assert "_try_captcha_solve" in body


def _fn_body(src: str, name: str) -> str:
    """The FULL source of a function, by AST rather than a character window.

    Three tests in this repo sliced `src[pos:pos + N]` for a magic N, and all
    three broke when unrelated lines were added above the thing they assert on
    -- the assertion was right and the denominator shifted. Measured here:
    `captcha_type=` is present in _handle_captcha_check (grep: 1 occurrence) but
    sat past `pos + 3000` once two lines were added, so the test failed about a
    property that had not changed.
    """
    import ast as _ast
    tree = _ast.parse(src)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name == name:
            return _ast.unparse(node)
    raise AssertionError(f"function {name!r} not found")


def test_handle_captcha_check_tracks_type():
    """_handle_captcha_check must detect the captcha type and pass
    it to _update_job for needs_review jobs."""
    body = _fn_body(_RUNNER_PY.read_text(encoding="utf-8"),
                    "_handle_captcha_check")
    assert "captcha_resolver" in body
    assert "detect_captcha_type" in body
    assert "captcha_type=" in body


def test_runner_imports_resolver_lazily():
    """The captcha_resolver import must be inside a function body, not
    module-level — keeps startup fast and the dependency optional."""
    src = _RUNNER_PY.read_text(encoding="utf-8")
    # No module-level `import captcha_resolver` at the top
    lines = src.splitlines()[:200]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("import") or stripped.startswith("from"):
            assert "captcha_resolver" not in line, (
                "captcha_resolver imported at module level — "
                "should be lazy")


# ── App.py integration ───────────────────────────────────────────────

def test_captcha_stats_endpoint_has_by_type():
    """The /api/sites/<sid>/captcha/stats endpoint must include the
    new by_type breakdown."""
    src = _APP_SRC()
    pos = src.find("def api_captcha_stats")
    assert pos > 0
    body = src[pos:pos + 2500]
    assert "_captcha_resolver_stats" in body
    assert '"by_type"' in body


# ── UI ────────────────────────────────────────────────────────────────

# ── Adversarial input regression tests ──────────────────────────────

def test_detect_handles_none_page():
    """detect_captcha_type must not crash when given None or a page
    that's been closed/disposed."""
    from bulk_downloader.captcha_resolver import detect_captcha_type

    class _ClosedPage:
        def locator(self, sel):
            raise RuntimeError("page closed")

    assert detect_captcha_type(_ClosedPage()) is None


def test_stats_handles_none_type():
    """CaptchaStats falls back to 'unknown' bucket for missing type."""
    from bulk_downloader.captcha_resolver import CaptchaStats
    s = CaptchaStats()
    s.record_submission(None, None)
    snap = s.snapshot()
    assert "unknown" in snap


def test_inject_token_handles_none_inputs():
    """inject_token with None token or page must return False, not crash."""
    from bulk_downloader.captcha_resolver import inject_token

    class _Page:
        def evaluate(self, js, *args): return 1

    assert inject_token(_Page(), "turnstile", None) is False
    assert inject_token(_Page(), None, "tok") is False


# ── Adversarial regression locks (from build-time audit) ─────────────

def test_submit_2captcha_handles_non_string_type():
    """Audit catch: passing a list/dict as captcha_type crashed
    `captcha_type in _2CAPTCHA_METHODS` with TypeError (unhashable).
    Must raise _ProviderError instead."""
    from bulk_downloader.captcha_resolver import submit_2captcha, _ProviderError
    for bad in ([], {}, 123, None):
        try:
            submit_2captcha("key", bad, "sk", "url")
        except _ProviderError as e:
            assert "unsupported" in str(e).lower()
        except Exception as e:
            raise AssertionError(
                f"submit_2captcha({bad!r}) raised wrong type: "
                f"{type(e).__name__}: {e}")


def test_submit_capsolver_handles_non_string_type():
    """Same defense for capsolver."""
    from bulk_downloader.captcha_resolver import submit_capsolver, _ProviderError
    for bad in ([], {}, 123, None):
        try:
            submit_capsolver("key", bad, "sk", "url")
        except _ProviderError as e:
            assert "unsupported" in str(e).lower()
        except Exception as e:
            raise AssertionError(
                f"submit_capsolver({bad!r}) raised wrong type: "
                f"{type(e).__name__}: {e}")


def test_solve_handles_non_string_provider():
    """Audit catch: provider=123 crashed `.strip()` with AttributeError.
    Must normalize to '2captcha' (or raise _ProviderError) rather
    than crash."""
    from bulk_downloader.captcha_resolver import solve, _ProviderError
    # We need to mock submit_2captcha so the test doesn't actually
    # try to hit the network
    from bulk_downloader import captcha_resolver as cr
    with mock.patch.object(cr, "submit_2captcha", return_value="tok"):
        for bad in (None, 123, ["list"], {}):
            try:
                cr.solve(bad, "key", "turnstile", "sk", "url")
            except _ProviderError:
                pass  # acceptable
            except AttributeError as e:
                raise AssertionError(
                    f"solve(provider={bad!r}) raised AttributeError: {e}")
