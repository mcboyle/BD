"""ReDoS guard for the deep_detect capture-content parsers (RDS-AUD follow-up).

The v3.66.291 fix bounded the greedy runs in the capture export-boundary
redactor after a real 8K capture pinned ``write_wacz`` at 100% CPU. The RDS-AUD
carry asked us to audit the OTHER capture-content parsers for the same shape. An
empirical sweep (every compiled regex in the capture/redaction modules, timed in
a hard-killed subprocess against pathological delimiter-free inputs) surfaced two
more quadratic backtrackers, both fed real captured content:

  * ``deep_detect._HLS_ATTR_RE`` — ``([A-Za-z0-9-]+)=…`` parses HLS manifest tag
    lines (captured network bodies). The unbounded attribute-name run backtracks
    O(n²) on a long ``=``-less run (a malformed / oversized #EXT-X-STREAM-INF line).
  * ``deep_detect._RULE_BLOCK_RE`` — ``([^{}]+)\\{([^{}]*)\\}`` walks captured CSS
    for honeypot-hidden inputs. The unbounded selector / body runs backtrack
    O(n²) on a long brace-free CSS region (minified/junk stylesheet).

Both are reachable from the capture pipeline (HLS variant parsing + honeypot
input detection over captured DOM/CSS) — the same failure class as the field
incident behind 291. The fix BOUNDS each greedy run; HLS attribute names are
short/known and real CSS selector-lists/bodies are well under the caps, so
matching on real input is unchanged and only the worst case becomes linear.

These tests prove BOTH halves: linear-time on pathological input (no hang) and
identical parsing of real input. RED on pristine (timing tests overrun); GREEN
after the bounds land.
"""

import signal

from bulk_downloader.deep_detect import _parse_hls_attrs, _is_visible_input


class _Timeout(Exception):
    pass


def _run_under(budget_s, fn, *a, **k):
    """Run ``fn`` under a wall-clock alarm; raise ``_Timeout`` on overrun.

    Mirrors test_v3_66_291_redaction_redos: a pure-CPU ``re`` spin is
    interrupted by SIGALRM between interpreter checkpoints. Restores the prior
    handler/timer unconditionally.
    """
    def _boom(*_):
        raise _Timeout

    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn(*a, **k)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


# A 2s budget is generous headroom for a sub-50ms parse but fails loudly on the
# old O(n²) blowup (which ran many seconds at these sizes).
_BUDGET = 2.0


# ── HLS attribute parser ─────────────────────────────────────────────────────

def test_hls_attr_parse_is_linear_on_pathological_line():
    # A long #EXT-X-STREAM-INF tag whose attribute "name" run never reaches an
    # '=' — the shape that drove the unbounded name run into O(n²) backtracking.
    patho = "#EXT-X-STREAM-INF:" + "A" * 60000
    try:
        _run_under(_BUDGET, _parse_hls_attrs, patho)
    except _Timeout:
        raise AssertionError(
            "_parse_hls_attrs is super-linear on a long '='-less HLS line "
            "(ReDoS) — bound the attribute-name run")


def test_hls_attr_parse_real_line_unchanged():
    line = ('#EXT-X-STREAM-INF:BANDWIDTH=18000000,RESOLUTION=3840x2160,'
            'CODECS="hvc1.2.4.L150.B0",AUDIO="aac"')
    out = _parse_hls_attrs(line)
    assert out.get("BANDWIDTH") == "18000000", out
    assert out.get("RESOLUTION") == "3840x2160", out
    assert out.get("CODECS") == "hvc1.2.4.L150.B0", out
    assert out.get("AUDIO") == "aac", out


def test_hls_attr_quoted_value_with_comma_preserved():
    # Comma inside a quoted CODECS value must not split the attribute.
    line = '#EXT-X-STREAM-INF:CODECS="hvc1.2.4.L150.B0,mp4a.40.2",AUDIO="aac"'
    out = _parse_hls_attrs(line)
    assert out.get("CODECS") == "hvc1.2.4.L150.B0,mp4a.40.2", out
    assert out.get("AUDIO") == "aac", out


# ── CSS rule-block walker (honeypot detection) ───────────────────────────────

def test_css_rule_walker_is_linear_on_brace_free_css():
    el = {"class": ["hp"], "id": "field-1"}
    patho = "a" * 200000  # long CSS region with no '{' or '}'
    try:
        _run_under(_BUDGET, _is_visible_input, el, patho)
    except _Timeout:
        raise AssertionError(
            "_is_visible_input is super-linear on long brace-free CSS (ReDoS) "
            "— bound the selector/body runs in _RULE_BLOCK_RE")


def test_css_rule_walker_detects_hidden_and_visible():
    # A hiding rule targeting the element's class -> treated as hidden honeypot.
    hidden = _is_visible_input({"class": ["hp"]}, ".hp{display:none}")
    assert hidden is False, "hidden honeypot rule should mark the input hidden"
    # A benign rule -> still visible.
    visible = _is_visible_input({"class": ["vis"]}, ".vis{color:red}")
    assert visible is True, "benign CSS rule should leave the input visible"


# ── dom_honeypot carries an independent copy of the same walk + an at-rule
#    prelude regex; both run on captured CSS and must be linear too ───────────

def test_dom_honeypot_css_walk_is_linear():
    from bulk_downloader.dom_honeypot import _bs_css_class_hidden
    el = {"class": ["hp"], "id": "f1"}
    patho = "a" * 200000
    try:
        _run_under(_BUDGET, _bs_css_class_hidden, el, patho)
    except _Timeout:
        raise AssertionError(
            "dom_honeypot._bs_css_class_hidden is super-linear on brace-free CSS")


def test_dom_honeypot_css_walk_parity_and_at_rule():
    from bulk_downloader.dom_honeypot import _bs_css_class_hidden
    # top-level hide
    assert _bs_css_class_hidden({"class": ["hp"]}, ".hp{display:none}") is True
    # benign
    assert _bs_css_class_hidden({"class": ["ok"]}, ".ok{color:red}") is False
    # @media-nested hide is unwrapped and still detected (F3 behavior preserved)
    nested = "@media (max-width: 600px) { .hp{display:none} }"
    assert _bs_css_class_hidden({"class": ["hp"]}, nested) is True


def test_dom_honeypot_flatten_at_rules_is_linear():
    from bulk_downloader.dom_honeypot import _flatten_at_rules
    # many @media openers with no '{' — the @-gated quadratic shape
    patho = ("@media screenx " * 30000)
    try:
        _run_under(_BUDGET, _flatten_at_rules, patho)
    except _Timeout:
        raise AssertionError(
            "_flatten_at_rules is super-linear on @media-heavy brace-free CSS "
            "— bound the prelude run in _AT_RULE_OPENER_RE")
