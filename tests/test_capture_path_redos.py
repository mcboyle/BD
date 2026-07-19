"""ReDoS guard for two more capture/resolution parsers (RDS-AUD follow-up).

The expanded empirical sweep (every compiled regex across the capture /
redaction / detection / resolution modules, timed in a hard-killed subprocess
against pathological delimiter-free inputs) surfaced quadratic backtrackers
beyond deep_detect / dom_honeypot:

  * ``inspect_pick._HASHED_CLASS`` — runs per CAPTURED-DOM class token (via
    ``_stable_classes``); ``_CSS_IDENT`` upstream is unbounded, so a single
    oversized class string reaches the unanchored ``[a-f0-9]{6,}$`` alternative
    and backtracks O(n²).
  * ``provider_resolve._YT_DECIPHER_FN_RE`` / ``_YT_DECIPHER_STMT_RE`` /
    ``_YT_TRANSFORM_METHOD_RE`` — scan YouTube ``base.js`` (~1-2 MB minified);
    each leads with an unbounded ``[a-zA-Z0-9$_]+`` identifier run that
    backtracks O(n²) on a long run lacking the following delimiter.

The fix BOUNDS the leading identifier/charset runs (JS identifiers and hashed
class names are short — the caps are far beyond any real value, so matching is
unchanged). These tests prove linearity; behavior parity is covered by the
existing test_inspect_pick / test_v3_66_*_youtube* suites. RED on pristine
(timing tests overrun), GREEN after the bounds land.
"""

import signal

from bulk_downloader.inspect_pick import _stable_classes
from bulk_downloader.provider_resolve import (
    _YT_DECIPHER_FN_RE, _YT_DECIPHER_STMT_RE, _YT_TRANSFORM_METHOD_RE,
    _build_yt_decipher_ops)


class _Timeout(Exception):
    pass


def _run_under(budget_s, fn, *a, **k):
    def _boom(*_):
        raise _Timeout
    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn(*a, **k)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


_BUDGET = 2.0


# ── inspect_pick hashed-class detector ───────────────────────────────────────

def test_stable_classes_linear_on_oversized_token():
    # A valid CSS identifier (passes _CSS_IDENT) that is a long [a-f0-9] run
    # ending in a non-hex letter: the unanchored "[a-f0-9]{6,}$" alternative
    # can never satisfy '$' after the run, so it backtracks O(n²) per offset.
    classes = ["btn", "a" * 60000 + "g", "header"]
    try:
        _run_under(_BUDGET, _stable_classes, classes)
    except _Timeout:
        raise AssertionError(
            "_stable_classes is super-linear on an oversized class token "
            "(ReDoS in _HASHED_CLASS) — bound the hex/charset runs")


def test_stable_classes_parity():
    out = _stable_classes(["btn", "css-1a2b3c", "x9f3e2d1", "nav-main"])
    # stable, human-authored classes survive; hashed/minted ones are dropped.
    assert "btn" in out and "nav-main" in out, out
    assert "css-1a2b3c" not in out and "x9f3e2d1" not in out, out


# ── provider_resolve YouTube base.js parsers ─────────────────────────────────

def test_yt_decipher_regexes_linear():
    blob = "a" * 80000
    for name, fn in (
        ("_YT_DECIPHER_FN_RE", lambda: _YT_DECIPHER_FN_RE.search(blob)),
        ("_YT_DECIPHER_STMT_RE", lambda: list(_YT_DECIPHER_STMT_RE.finditer(blob))),
        ("_YT_TRANSFORM_METHOD_RE", lambda: list(_YT_TRANSFORM_METHOD_RE.finditer(blob))),
    ):
        try:
            _run_under(_BUDGET, fn)
        except _Timeout:
            raise AssertionError(
                f"{name} is super-linear on a long identifier-char run (ReDoS) "
                "— bound the leading [a-zA-Z0-9$_] run")


def test_build_yt_decipher_ops_linear_on_junk_player_js():
    # The whole base.js parse path must stay linear on a large junk script.
    patho = "var " + ("a" * 200000)
    try:
        _run_under(_BUDGET, _build_yt_decipher_ops, patho)
    except _Timeout:
        raise AssertionError(
            "_build_yt_decipher_ops is super-linear on a large junk player_js")
