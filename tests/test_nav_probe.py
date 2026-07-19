"""nav_probe pure-logic tests (no Playwright, no network).

The probe's browser-driving layer (``probe``/``_launch``) is stash-only — it
needs a real browser and a real target. What IS provable in the sandbox is the
decision logic that turns observed navigation outcomes into a verdict:
``is_usable`` (the guard-identical predicate), ``attempt_strategy`` (one
strategy's outcome, with injected goto/read callables), ``classify`` (attempt
list -> verdict), and ``signals_from``. These are exactly the parts that decide
which guard fix to land, so they get full coverage.

Plain zero-arg test functions + a unittest TestCase — no pytest builtin
fixtures — so this runs identically under run_tests.py and a pytest sweep.
"""
import io
import json
import unittest
from contextlib import redirect_stderr

from tools import nav_probe as np


# ── fakes ─────────────────────────────────────────────────────────────

class _Timeout(Exception):
    """Stand-in whose str carries 'Timeout' (matches Playwright TimeoutError)."""
    def __init__(self, ms=30000):
        super().__init__("Page.goto: Timeout %dms exceeded" % ms)


class _NetErr(Exception):
    def __init__(self, marker="net::ERR_CONNECTION_REFUSED"):
        super().__init__("Page.goto: %s at https://x/" % marker)


def _goto_ok(strategy, timeout_ms):
    return None


def _goto_timeout(strategy, timeout_ms):
    raise _Timeout(timeout_ms)


def _goto_neterr(strategy, timeout_ms):
    raise _NetErr()


def _read(rs="complete", body_len=120, title="T", url="https://app.reptyle.com/"):
    def _r():
        return rs, body_len, title, url
    return _r


def _read_sequence(seq):
    """Returns successive (rs, body_len, title, url) tuples; repeats the last."""
    box = {"i": 0}

    def _r():
        i = min(box["i"], len(seq) - 1)
        box["i"] += 1
        return seq[i]
    return _r


def _no_sleep(_s):
    return None


# ── is_usable: byte-identical to the guard's D2(a) predicate ──────────

def test_is_usable_complete_and_interactive_with_body():
    assert np.is_usable("complete", 0) is True
    assert np.is_usable("interactive", 415) is True


def test_is_usable_rejects_loading_and_negative_body_and_none():
    assert np.is_usable("loading", 100) is False
    assert np.is_usable("complete", -1) is False
    assert np.is_usable(None, 100) is False
    assert np.is_usable("complete", "100") is False  # non-int body


def test_navigated_off_blank_gate():
    assert np._navigated_off_blank("https://app.reptyle.com/") is True
    assert np._navigated_off_blank("about:blank") is False
    assert np._navigated_off_blank("") is False
    assert np._navigated_off_blank(None) is False
    assert np._navigated_off_blank("chrome-error://chromewebdata/") is False


# ── attempt_strategy: resolution semantics ───────────────────────────

def test_load_resolves_usable():
    rec = np.attempt_strategy("load", _goto_ok, _read(), timeout_ms=1000)
    assert rec["outcome"] == "usable"
    assert rec["resolved"] is True


def test_load_timeout_with_usable_dom_is_NOT_usable():
    # The crucial case: load timed out but the DOM happens to be usable. This is
    # the blocker, NOT LOAD_OK — the wait condition did not resolve.
    rec = np.attempt_strategy("load", _goto_timeout,
                              _read(rs="interactive", body_len=50),
                              timeout_ms=1000)
    assert rec["outcome"] == "timeout_unusable"
    assert rec["resolved"] is False
    assert "Timeout" in rec["error"]


def test_about_blank_after_resolve_is_not_usable():
    # goto "resolved" but the page never left about:blank -> not usable-for-target.
    rec = np.attempt_strategy("load", _goto_ok,
                              _read(rs="complete", body_len=0, url="about:blank"),
                              timeout_ms=1000)
    assert rec["outcome"] == "timeout_unusable"


def test_dcl_resolves_usable():
    rec = np.attempt_strategy("domcontentloaded", _goto_ok,
                              _read(rs="interactive", body_len=10),
                              timeout_ms=1000)
    assert rec["outcome"] == "usable"


def test_neterr_is_decisive():
    rec = np.attempt_strategy("load", _goto_neterr, _read(), timeout_ms=1000)
    assert rec["outcome"] == "net_error"
    assert "net::ERR_CONNECTION_REFUSED" in rec["error"]


def test_commit_usable_immediately():
    rec = np.attempt_strategy("commit", _goto_ok,
                              _read(rs="complete", body_len=300),
                              timeout_ms=1000, poll_ms=2000, sleep_fn=_no_sleep)
    assert rec["outcome"] == "usable"
    assert rec["polls"] == 0


def test_commit_self_poll_becomes_usable():
    # commit resolves while DOM is still 'loading'; readyState completes on poll 2.
    seq = [
        ("loading", -1, "T", "https://app.reptyle.com/"),
        ("loading", -1, "T", "https://app.reptyle.com/"),
        ("interactive", 64, "T", "https://app.reptyle.com/"),
    ]
    rec = np.attempt_strategy("commit", _goto_ok, _read_sequence(seq),
                              timeout_ms=1000, poll_ms=5000, sleep_fn=_no_sleep,
                              poll_interval_ms=10)
    assert rec["outcome"] == "usable"
    assert rec["polls"] >= 1


def test_commit_self_poll_never_usable():
    rec = np.attempt_strategy("commit", _goto_ok,
                              _read(rs="loading", body_len=-1),
                              timeout_ms=1000, poll_ms=120, sleep_fn=_no_sleep,
                              poll_interval_ms=10)
    assert rec["outcome"] == "timeout_unusable"


def test_safe_read_tolerates_evaluate_failure():
    def _boom():
        raise RuntimeError("evaluate failed")
    rec = np.attempt_strategy("load", _goto_ok, _boom, timeout_ms=1000)
    assert rec["outcome"] == "timeout_unusable"  # unusable read -> not usable


# ── classify: attempt list -> verdict ────────────────────────────────

def _att(strategy, outcome, **kw):
    base = {"strategy": strategy, "outcome": outcome, "resolved": True,
            "readyState": "complete", "body_len": 100, "title": "",
            "current_url": "https://x/", "error": "", "elapsed_ms": 1, "polls": 0}
    base.update(kw)
    return base


def test_classify_load_ok():
    assert np.classify([_att("load", "usable")]) == "LOAD_OK"


def test_classify_n1_dcl():
    atts = [_att("load", "timeout_unusable"), _att("domcontentloaded", "usable")]
    assert np.classify(atts) == "N1_DCL"


def test_classify_n3_commit():
    atts = [_att("load", "timeout_unusable"),
            _att("domcontentloaded", "timeout_unusable"),
            _att("commit", "usable", polls=3)]
    assert np.classify(atts) == "N3_COMMIT_SELFPOLL"


def test_classify_unreachable_all_timeout():
    atts = [_att("load", "timeout_unusable"),
            _att("domcontentloaded", "timeout_unusable"),
            _att("commit", "timeout_unusable")]
    assert np.classify(atts) == "UNREACHABLE"


def test_classify_unreachable_first_neterr():
    # A transport failure on the FIRST attempt is decisive even with no later ones.
    atts = [_att("load", "net_error", resolved=False,
                 error="net::ERR_NAME_NOT_RESOLVED")]
    assert np.classify(atts) == "UNREACHABLE"


def test_exit_codes():
    assert np._exit_code("LOAD_OK") == 0
    assert np._exit_code("N1_DCL") == 0
    assert np._exit_code("N3_COMMIT_SELFPOLL") == 0
    assert np._exit_code("UNREACHABLE") == 3


# ── F2 posture: the report carries lengths/title only, never body text ─

def test_report_shape_no_body_text_leak():
    report = {
        "tool": "nav_probe", "url": "https://x/", "verdict": "N1_DCL",
        "recommended_fix": np._VERDICT_FIX["N1_DCL"],
        "signals": np.signals_from([_att("load", "timeout_unusable"),
                                    _att("domcontentloaded", "usable",
                                         title="Reptyle")]),
        "attempts": [_att("domcontentloaded", "usable", title="Reptyle")],
    }
    blob = json.dumps(report)
    # Only benign signals are present; no raw page text field exists.
    assert "body_len" in blob
    assert "body_text" not in blob and "innerText" not in blob and "html" not in blob
    assert report["signals"]["readyState"] == "complete"


def test_human_summary_renders_without_crash():
    report = {
        "url": "https://x/", "verdict": "N1_DCL", "backend": "cloakbrowser",
        "recorder_attached": True,
        "recommended_fix": np._VERDICT_FIX["N1_DCL"],
        "signals": {"readyState": "interactive", "body_len": 10, "title": "T",
                    "final_url": "https://x/", "net_error": ""},
        "attempts": [_att("load", "timeout_unusable", error="Timeout 30000ms"),
                     _att("domcontentloaded", "usable")],
    }
    buf = io.StringIO()
    with redirect_stderr(buf):
        np._print_human(report)
    out = buf.getvalue()
    assert "N1_DCL" in out and "domcontentloaded" in out


class NavProbeParserTests(unittest.TestCase):
    def test_parser_defaults(self):
        args = np._build_parser().parse_args(["https://app.reptyle.com/"])
        self.assertEqual(args.url, "https://app.reptyle.com/")
        self.assertEqual(args.timeout_ms, 30000)
        self.assertEqual(args.poll_ms, 15000)
        self.assertFalse(args.headed)
        self.assertFalse(args.no_recorder)

    def test_parser_flags(self):
        args = np._build_parser().parse_args(
            ["https://x/", "--timeout-ms", "5000", "--headed",
             "--no-recorder", "--json", "--chrome", "/p/chrome"])
        self.assertEqual(args.timeout_ms, 5000)
        self.assertTrue(args.headed)
        self.assertTrue(args.no_recorder)
        self.assertTrue(args.json)
        self.assertEqual(args.chrome, "/p/chrome")


if __name__ == "__main__":
    unittest.main()
