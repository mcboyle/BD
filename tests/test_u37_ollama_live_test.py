"""U37 — L17 (ollama-reachable), a single live test.

L17 reads /api/ai/status on the deployment and confirms the AI
backend is reachable with its configured models installed. It cannot
truly run without the deployment; unit-testable here is registration,
graceful degradation, and the verdict logic against synthetic
/api/ai/status payloads (the four outcome branches).
"""
import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


class _StubContext(h.Context):
    """A Context whose .get() returns a canned /api/ai/status body —
    lets the four L17 verdict branches be exercised offline."""

    def __init__(self, ai_body):
        super().__init__("http://localhost:1", "/tmp")
        self._ai_body = ai_body

    def get(self, path, timeout=15):
        if path == "/api/ai/status":
            if self._ai_body is None:
                return False, None, "unreachable", 1.0
            return True, 200, self._ai_body, 5.0
        return super().get(path, timeout)


# ── registration ───────────────────────────────────────────────────

def test_l17_is_registered():
    assert _get_test("L17") is not None


def test_l17_is_not_disruptive():
    assert _get_test("L17").disruptive is False


# ── graceful degradation ───────────────────────────────────────────

def test_l17_unreachable_status_is_warn():
    level, detail = _get_test("L17").fn(_StubContext(None))
    assert level == h.WARN
    assert "unreachable" in detail


def test_l17_returns_a_valid_tuple():
    res = _get_test("L17").fn(_StubContext(None))
    assert isinstance(res, tuple) and len(res) == 2
    assert res[0] in _LEVELS


# ── the four outcome branches ──────────────────────────────────────

def test_l17_ai_disabled_is_pass():
    # AI disabled by config is a valid intentional state
    res = _get_test("L17").fn(_StubContext({"enabled": False}))
    assert res[0] == h.PASS
    assert "disabled" in res[1]


def test_l17_reachable_all_models_present_is_pass():
    body = {"enabled": True, "ok": True,
            "installed_models": ["qwen2.5vl:7b", "qwen2.5:7b"],
            "missing_models": [], "latency_ms": 42}
    res = _get_test("L17").fn(_StubContext(body))
    assert res[0] == h.PASS
    assert "all" in res[1]


def test_l17_reachable_missing_models_is_warn():
    body = {"enabled": True, "ok": True,
            "installed_models": ["qwen2.5:7b"],
            "missing_models": ["qwen2.5vl:7b"], "latency_ms": 30}
    res = _get_test("L17").fn(_StubContext(body))
    assert res[0] == h.WARN
    assert "qwen2.5vl:7b" in res[1]


def test_l17_enabled_but_unreachable_is_fail():
    # AI configured ON but the backend is down -> a real FAIL
    body = {"enabled": True, "ok": False,
            "error": "connection refused"}
    res = _get_test("L17").fn(_StubContext(body))
    assert res[0] == h.FAIL
    assert "unreachable" in res[1] or "connection" in res[1]


# ── harness integration ────────────────────────────────────────────

def test_l17_runs_via_harness(tmp_path):
    rdir = tmp_path / "results"
    # against a dead target L17 WARNs -> exit 0
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L17"], results_dir=rdir)
    assert code == 0
    assert (rdir / "L17.log").is_file()
