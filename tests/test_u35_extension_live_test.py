"""U35 — L3 (mv3-extension-service-worker), a single live test.

L3 is the deployment-side counterpart of the 3 test_extension_live.py
skips: it loads the real extension into Chromium and checks the MV3
service worker registers. In this sandbox the worker does NOT register
headless (the very condition the unit-suite skips exist for), so L3
WARNs here — and that graceful WARN is exactly what is unit-tested.
"""
import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l3_is_registered():
    assert _get_test("L3") is not None


def test_l3_is_not_disruptive():
    # L3 launches its own Chromium; it touches nothing on the
    # deployment, so it is safe to run any time
    assert _get_test("L3").disruptive is False


def test_l3_has_a_descriptive_name():
    assert "extension" in _get_test("L3").name


# ── behaviour ──────────────────────────────────────────────────────

def test_l3_returns_a_valid_tuple():
    # the sandbox has Playwright + the extension dir, so L3 runs the
    # real path; it must return a valid (level, detail) tuple, never
    # raise (the harness would record a raise as a hard FAIL)
    ctx = h.Context("http://localhost:1", "/tmp")
    res = _get_test("L3").fn(ctx)
    assert isinstance(res, tuple) and len(res) == 2
    level, detail = res
    assert level in _LEVELS
    assert isinstance(detail, str) and detail


def test_l3_does_not_fail_on_the_known_skip_condition():
    # the headless sandbox Chromium will not register an MV3 service
    # worker — that is an environment gap, not an extension defect, so
    # L3 must WARN here, never FAIL
    ctx = h.Context("http://localhost:1", "/tmp")
    level, detail = _get_test("L3").fn(ctx)
    assert level in (h.PASS, h.WARN)
    # if it WARNed, the detail should explain it is an environment gap
    if level == h.WARN:
        assert ("service worker" in detail.lower()
                or "playwright" in detail.lower()
                or "chromium" in detail.lower())


# ── harness integration ────────────────────────────────────────────

def test_l3_runs_via_harness_and_records_artifacts(tmp_path):
    rdir = tmp_path / "results"
    # L3 is non-disruptive -> runs on a default run; WARN/PASS -> exit 0
    code = h.run_all("http://localhost:1", str(tmp_path),
                     only=["L3"], results_dir=rdir)
    assert code == 0
    assert (rdir / "L3.log").is_file()
    summary = (rdir / "SUMMARY.txt").read_text()
    assert "L3" in summary
    # no spurious failure artifact
    assert not (rdir / "L3.fail.txt").exists()
