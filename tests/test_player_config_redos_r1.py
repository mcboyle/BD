"""player-config-redos r1: player_config.extract_player_configs in linear time.

PM row (A2, R205-D on detect-misc-fix r1): _JWPLAYER_SETUP_RE
r'jwplayer\\s*\\([^)]*\\)\\s*\\.\\s*setup\\s*\\(' restarted its [^)]* run at every
head, so "jwplayer(" x n with no ")" was quadratic (144 KB: 1.5 s on BASE).
The sweep of player_config.py found the same shape in _VIDEOJS_CALL_RE
([^,]+ then ",") and, worse, in _find_balanced_object: every head rescanned
from its "{" to the end of an unclosed object ("jwplayer().setup({" x n,
144 KB: 89 s). Both heads are now an exact-semantics scan; the brace scan is
a jump scan charged to one budget per call (dispatch ANSWER A: <= 4 x len +
floor, heads after it is spent are skipped).

Each timing case runs under an alarm (BASE would spin for minutes) and takes
the best of 3 trials: 144 KB against the 0.1 s bound, and 36 KB -> 144 KB and
144 KB -> 1 MB against a linear growth ratio (the 1 MB pages catch the
str.find-per-head shapes that memchr hides at 144 KB).
"""

# An ordinary module test: its subject is the module under test, not the
# tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import signal
import time

import pytest

from bulk_downloader import player_config
from bulk_downloader.player_config import extract_player_configs


class _Timeout(Exception):
    pass


def _run_under(budget_s, fn, *a):
    """Run fn under a wall-clock alarm; raise _Timeout on overrun (same
    pattern as test_deep_detect_redos)."""

    def _boom(*_):
        raise _Timeout

    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, budget_s)
    try:
        return fn(*a)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


_ALARM_S = 2.0
_BOUND_S = 0.1


def _page(unit: str, kb: int, tail: str = "") -> str:
    return unit * (kb * 1024 // len(unit)) + tail


def _best_of_3(text: str, diag: str, alarm_s: float = _ALARM_S) -> float:
    best = None
    for _ in range(3):
        t0 = time.perf_counter()
        try:
            _run_under(alarm_s, extract_player_configs, text)
        except _Timeout:
            raise AssertionError(
                f"{diag}: over {alarm_s} s on {len(text)} chars"
            ) from None
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


@pytest.mark.parametrize(
    "case,unit,tail",
    [
        ("jw-head-no-close", "jwplayer(", ""),
        ("jw-head-close-at-end", "jwplayer(", ")"),
        ("vjs-head-no-comma", "videojs(", ""),
        ("vjs-head-comma-at-end", "videojs(", ","),
        ("jw-unclosed-object", "jwplayer().setup({", ""),
        ("vjs-unclosed-object", "videojs(a,{", ""),
        ("state-unclosed-object", "__INITIAL_STATE__={", ""),
    ],
)
def test_pathological_144kb_page_under_bound(case, unit, tail):
    diag = f"PCR1-SUPERLINEAR-{case}"
    best = _best_of_3(_page(unit, 144, tail), diag)
    assert best < _BOUND_S, (
        f"{diag}: best of 3 = {best:.3f} s at 144 KB (bound {_BOUND_S} s)"
    )


def test_unclosed_object_cost_grows_linearly():
    # Load-independent half of the claim: 4x the page is ~4x the time
    # (BASE: ~16x, 36 KB 5.5 s -> 144 KB 89 s).
    small = _best_of_3(_page("jwplayer().setup({", 36), "PCR1-SUPERLINEAR-ratio-36")
    big = _best_of_3(_page("jwplayer().setup({", 144), "PCR1-SUPERLINEAR-ratio-144")
    assert big / max(small, 1e-4) <= 8, (
        f"PCR1-RATIO: 36 KB {small:.4f} s -> 144 KB {big:.4f} s"
    )


@pytest.mark.parametrize(
    "case,unit,tail",
    [
        ("jw-head-close-at-end", "jwplayer(", ")"),
        ("jw-heads-no-brace", "jwplayer().setup(", ""),
        ("vjs-heads-no-brace", "videojs(a,", ""),
        ("state-no-brace", "__INITIAL_STATE__=", ""),
    ],
)
def test_head_pages_grow_linearly_to_1mb(case, unit, tail):
    # Quadratic shapes that str.find/memchr keeps fast at 144 KB: a stop or a
    # "{" searched again from every head. 7.1x the page must cost ~7x, not
    # ~25x (BASE jw-head-close-at-end: 1.9 s -> 94 s).
    small = _best_of_3(_page(unit, 144, tail), f"PCR1-SUPERLINEAR-{case}-144")
    big = _best_of_3(
        _page(unit, 1024, tail), f"PCR1-SUPERLINEAR-{case}-1024", alarm_s=5.0
    )
    assert big / max(small, 1e-3) <= 14, (
        f"PCR1-RATIO-1MB-{case}: 144 KB {small:.4f} s -> 1 MB {big:.4f} s"
    )


# A normal page: two jwplayer forms (one mixed case with spaces), videojs with
# a call in its first argument, a data-setup attribute and __INITIAL_STATE__.
# "videojs(, {...})" has an empty first argument, which the old [^,]+ never
# matched, so it yields nothing.
# Two string values hold an escaped quote and a "}" -- the only places the
# jump scan could leave the old per-character answer.
_NORMAL_PAGE = (
    "<html><body><script>\n"
    'jwplayer("main").setup({playlist: [{sources: [{file: "https://cdn.example/a/720.m3u8", label: "720p"},'
    ' {file: "https://cdn.example/a/1080.mp4", label: "1080p"}]}], title: "say \\"hi\\" {not a brace}"});\n'
    'JWPlayer ( "alt" ) . SETUP ( {file: "https://cdn.example/b.mp4?token=TOKJW1"} );\n'
    'videojs(document.getElementById("vid"), {sources: [{src: "https://cdn.example/c.m3u8", type: "application/x-mpegURL"}],'
    " note: 'it\\'s } ok'});\n"
    'videojs(, {sources: [{src: "https://cdn.example/f.mp4"}]});\n'
    "</script>\n"
    '<video data-setup=\'{"sources": [{"src": "https://cdn.example/d.mp4", "type": "video/mp4"}]}\'></video>\n'
    '<script>window.__INITIAL_STATE__ = {stream: {qualities: [{url: "https://cdn.example/e/480.mp4", height: 480}]}};</script>\n'
    "</body></html>\n"
)

# What BASE 7927017d extracts from _NORMAL_PAGE (measured), in order.
_NORMAL_EXPECTED = [
    (
        "jwplayer",
        [
            ("https://cdn.example/a/720.m3u8", 720),
            ("https://cdn.example/a/1080.mp4", 1080),
        ],
    ),
    ("jwplayer", [("https://cdn.example/b.mp4?token=TOKJW1", None)]),
    ("videojs", [("https://cdn.example/d.mp4", None)]),
    ("videojs", [("https://cdn.example/c.m3u8", None)]),
    ("generic", [("https://cdn.example/e/480.mp4", 480)]),
]


def test_control_normal_multi_player_page_unchanged():
    got = extract_player_configs(_NORMAL_PAGE)
    shape = [(c.player_type, [(s.url, s.height) for s in c.sources]) for c in got]
    assert shape == _NORMAL_EXPECTED, f"PCR1-NORMAL-PAGE-CHANGED: {shape}"
    assert got[0].raw_config["title"] == 'say "hi" {not a brace}', (
        "PCR1-ESCAPED-QUOTE-SCAN"
    )
    assert got[3].raw_config["note"] == "it's } ok", "PCR1-STRING-BRACE-SCAN"
    assert got[1].sources[0].auth_token == "TOKJW1"


def test_control_head_scans_keep_regex_answers():
    # videojs needs a non-empty first argument; jwplayer accepts an empty one.
    assert player_config._call_heads(
        "videojs(, {}) videojs('v', {",
        player_config._VIDEOJS_HEAD_RE,
        ",",
        player_config._VIDEOJS_TAIL_RE,
        1,
    ) == [len("videojs(, {}) videojs('v', ")], "PCR1-VJS-EMPTY-ARG"
    text = "jwplayer().setup({}) jwplayer(x) .setup ({} jwplayer(y).play("
    assert player_config._call_heads(
        text, player_config._JWPLAYER_HEAD_RE, ")", player_config._JWPLAYER_TAIL_RE, 0
    ) == [len("jwplayer().setup("), len("jwplayer().setup({}) jwplayer(x) .setup (")], (
        "PCR1-JW-HEADS"
    )


def test_budget_spent_skips_later_heads_keeps_earlier():
    # Disclosed behaviour of the budget: thousands of unclosed heads spend it,
    # so well-formed configs AFTER them are skipped; one before them is kept.
    # The budget is shared by the call, so the videojs pass is skipped too.
    first = 'jwplayer("a").setup({file: "https://cdn.example/first.mp4"});'
    last = 'jwplayer("b").setup({file: "https://cdn.example/last.mp4"});'
    vjs = 'videojs("v", {sources: [{src: "https://cdn.example/vjs-last.mp4"}]});'
    text = first + "jwplayer().setup({" * 8000 + "}" + last + vjs
    got = _run_under(_ALARM_S, extract_player_configs, text)
    urls = [s.url for c in got for s in c.sources]
    assert urls == ["https://cdn.example/first.mp4"], f"PCR1-BUDGET: {urls}"
