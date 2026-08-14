"""U39 — L18 (vision-call-roundtrip), a single live test.

L18 sends a real screenshot to the vision model via the deployment's
detect_login endpoint and confirms structured output returns. It
needs the deployment + a configured site + Ollama; unit-testable here
is registration, the embedded image payload's validity, and graceful
degradation when the AI backend is unreachable/disabled.
"""
import base64
import urllib.request

import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h


# Read the harness tuple rather than restating it: a local copy goes stale
# the moment the verdict vocabulary grows (it did -- h.NA was added and every
# copy of this line silently began rejecting a valid level).
_LEVELS = h._LEVELS


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


class _StubContext(h.Context):
    """Context returning a canned /api/ai/status body, so L18's
    pre-flight branches can be exercised offline."""

    def __init__(self, ai_body):
        super().__init__("http://localhost:1", "/tmp")
        self._ai_body = ai_body

    def get(self, path, timeout=15):
        if path == "/api/ai/status":
            if self._ai_body is None:
                return False, None, "unreachable", 1.0
            return True, 200, self._ai_body, 5.0
        # /api/status etc. -> behave as unreachable
        return False, None, "unreachable", 1.0


class _TopLevelSiteContext(_StubContext):
    def get(self, path, timeout=15):
        if path == "/api/status":
            return True, 200, {"site-a": {"state": "idle"}}, 1.0
        return super().get(path, timeout=timeout)


# ── registration ───────────────────────────────────────────────────

def test_l18_is_registered():
    assert _get_test("L18") is not None


def test_l18_is_not_disruptive():
    assert _get_test("L18").disruptive is False


# ── the embedded image payload is a real, valid PNG ────────────────

def test_embedded_png_is_valid():
    raw = base64.b64decode(checks._TINY_PNG_B64)
    # PNG magic signature
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(raw) > 0


# ── graceful degradation / pre-flight branches ─────────────────────

def test_l18_unreachable_status_is_warn():
    level, detail = _get_test("L18").fn(_StubContext(None))
    assert level == h.WARN
    assert "not testable" in detail or "unreachable" in detail


def test_l18_ai_disabled_is_pass():
    """CHANGED AT v3.66.1135, DELIBERATELY. This asserted WARN.

    The tree contained both answers for one input: test_u37's
    `test_l17_ai_disabled_is_pass` pinned PASS while this pinned WARN,
    for the identical `{"enabled": False}` stub. That is drift, not a
    decision -- and note which of the two carried a reason. The sibling
    below (`ai_backend_down_is_warn`) explains itself in a comment; this
    one only pinned a level, and the file's docstring describes its own
    purpose as "graceful degradation", not as WARN specifically.

    Measured on a six-host capture round at v3.66.1134: three hosts have
    no GPU, so this branch fired on every run and put two permanent
    warns on each -- which is how a real AI regression there stops being
    readable. AI off by config is an intentional deployment state, and
    BD supports it explicitly (install_ai_ollama.sh is opt-in).

    The NEXT test is untouched on purpose: enabled-but-unreachable is an
    outage and must stay WARN.
    """
    level, detail = _get_test("L18").fn(
        _StubContext({"enabled": False}))
    assert level == h.PASS
    assert "disabled" in detail, (
        "a PASS that does not say it skipped the work reads as evidence "
        "the work succeeded")


def test_l18_ai_backend_down_is_warn():
    # AI enabled but the backend is unreachable -> WARN (L17's job to
    # FAIL on that); L18 cannot roundtrip so it defers
    level, detail = _get_test("L18").fn(
        _StubContext({"enabled": True, "ok": False,
                      "error": "connection refused"}))
    assert level == h.WARN
    assert "backend" in detail.lower()


def test_l18_no_site_is_warn():
    # AI ready but /api/status returns no site -> WARN, not FAIL
    level, detail = _get_test("L18").fn(
        _StubContext({"enabled": True, "ok": True,
                      "installed_models": ["qwen2.5vl:7b"]}))
    assert level == h.WARN
    assert "no site" in detail


def test_l18_accepts_deployed_top_level_status_schema(monkeypatch):
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"ok": true, "selectors": {}}'

    def _urlopen(request, timeout=90):
        assert request.full_url.endswith(
            "/api/sites/site-a/ai/detect_login")
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    level, detail = _get_test("L18").fn(_TopLevelSiteContext({
        "enabled": True,
        "ok": True,
        "installed_models": ["qwen2.5vl:7b"],
    }))
    assert level == h.PASS
    assert "structured response" in detail


def test_l18_returns_a_valid_tuple():
    res = _get_test("L18").fn(_StubContext(None))
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] in _LEVELS


# ── harness integration ────────────────────────────────────────────

def test_l18_runs_via_harness(tmp_path):
    rdir = tmp_path / "results"
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L18"], results_dir=rdir)
    assert code == 0  # WARN against a dead target
    assert (rdir / "L18.log").is_file()
