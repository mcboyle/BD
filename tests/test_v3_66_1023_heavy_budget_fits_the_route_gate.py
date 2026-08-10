"""@1023. The heavy-collector budget was 2.5x the gate it has to fit inside.

@1015 gave `collect_capture_analytics` the two bounds its sibling already had,
after the route failed L34 by exceeding 8s when probed alone on a quiet app.
The bound it was given is a WALL-TIME budget of 20 seconds
(`app_data_layer._HEAVY_BUDGET_S`), and L34 fails any operator route that does
not answer within 8 (`live_tests/checks._L34_ROUTE_BUDGET_S`).

So the bound guarantees the route TERMINATES. It cannot guarantee the route
answers in time, which is the property the gate actually tests -- a collector
that spends its full budget still fails L34 by 12 seconds. What made capture 3
pass was `max_bytes` skipping an oversized capture JSON, not the wall clock.

TWO NUMBERS IN TENSION AND NOTHING RELATING THEM. That is the whole defect:
each is individually defensible, they live in different trees, and no test
mentions both. This file is that test. It reads BOTH constants and asserts the
budget leaves room for the worst single-file overrun -- so if either number
moves, the one that breaks the relationship fails here rather than on the box.

WHY THE OVERRUN IS ONE FILE AND NOT MORE. `capture_analytics._artifacts`
checks the budget BEFORE each parse and `continue`s past the file when it is
spent, so the worst case is `budget + one file's parse`. Measured at the
`max_bytes` ceiling: a 25.1 MB capture JSON with 220k network_log entries
parses in **0.233s**. `_MARGIN_S` below is 1.0s -- four times that, and still
leaving room for request handling and serialisation.

LOWERING THE BUDGET TRUNCATES ON A LARGE STORE, AND THAT IS THE POINT.
The sibling `collect_capture_diagnostics`, which has had all three bounds all
along, took **6181ms serial** on the operator's real store in the capture at
`fe88b5a` -- under 8s, at 77% of it. A 5s budget will start skipping artifacts
there. That is the correct trade for a ROUTE, and only because @1015 built the
reporting for it: anything skipped is still counted, still listed, and carries
`unparsed: "budget_s"`, with `unparsed_artifacts` on the report. A bounded,
LABELLED answer inside the gate beats a complete answer that intermittently
blows it. The CLI keeps the complete answer -- all three bounds default to
None, which is its contract, and nothing here changes that.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
for p in (str(REPO), str(REPO / "live_tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

# Four times the measured worst-case single-file parse (0.233s at the
# max_bytes ceiling), leaving the rest of the 8s for request handling.
_MARGIN_S = 1.0


def _route_budget() -> float:
    """L34's per-route budget, read from the check itself.

    Imported rather than restated: a copy here would be a second source of
    truth for the number this file exists to relate, and the copy nobody
    updated is the one that runs.
    """
    from live_tests import checks
    return float(checks._L34_ROUTE_BUDGET_S)


def _heavy():
    from bulk_downloader import app_data_layer as A
    return A


def test_both_constants_are_readable_before_anything_is_compared():
    """Non-empty denominator. If either import or attribute goes away this
    file must FAIL rather than silently compare nothing -- the shape where a
    check reports OK over a subject it cannot see."""
    assert _route_budget() > 0
    assert _heavy()._HEAVY_BUDGET_S > 0
    assert _heavy()._HEAVY_MAX_BYTES > 0


def test_the_heavy_budget_leaves_room_inside_L34s_route_budget():
    """THE DEFECT. 20s of wall-time budget cannot fit inside an 8s gate."""
    budget = float(_heavy()._HEAVY_BUDGET_S)
    route = _route_budget()
    assert budget + _MARGIN_S <= route, (
        "the heavy-collector budget is %.1fs against L34's %.1fs route budget. "
        "A collector that spends its budget fails the gate by %.1fs, so the "
        "bound guarantees the route TERMINATES and not that it ANSWERS. "
        "Lower _HEAVY_BUDGET_S, or if the gate's budget moved, re-derive both."
        % (budget, route, (budget + _MARGIN_S) - route))


def test_the_budget_is_checked_BEFORE_the_parse_so_the_overrun_is_one_file():
    """The margin above is only defensible while this holds. If the check
    moved after the parse, the overrun would be unbounded by file COUNT and a
    fixed margin would be arithmetic about the wrong thing."""
    import ast
    src = (REPO / "tools" / "capture_analytics.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_artifacts")
    body = ast.unparse(fn)
    guard = body.find("budget_s is not None")
    parse = body.find("json.load")
    assert guard != -1 and parse != -1, "re-derive this test; the shape moved"
    assert guard < parse, (
        "the budget check no longer precedes the per-file parse, so the "
        "overrun past the budget is not bounded by a single file")


def test_a_spent_budget_still_REPORTS_what_it_skipped():
    """What licenses a smaller budget at all. A bound that silently shrinks
    the denominator is worse than no bound -- @1015's own words -- so the
    truncation must be visible in the report, not inferred from a short list.
    """
    import tempfile, json
    sys.path.insert(0, str(REPO / "tools"))
    import capture_analytics as CA

    root = pathlib.Path(tempfile.mkdtemp())
    d = root / "captures"
    d.mkdir()
    for i in range(3):
        (d / ("capture_%d.json" % i)).write_text(
            json.dumps({"captured_at": "2026-08-10T00:00:00Z",
                        "network_log": [{"url": "https://x.test/%d" % i}]}))

    # budget_s=0 spends the budget before the first file, so every artifact is
    # skipped for the budget reason and none for any other.
    out = CA.analyze(str(root), dirs=["captures"], budget_s=0)
    assert out.get("unparsed_artifacts", 0) >= 1, (
        "a spent budget skipped files without saying so: %r"
        % {k: v for k, v in out.items() if "unparsed" in k or "count" in k})

    # and the unbounded control, so the assertion above is not passing because
    # the fixture is broken
    full = CA.analyze(str(root), dirs=["captures"])
    assert full.get("unparsed_artifacts", 0) == 0, (
        "the unbounded control reported skips, so the test above proves "
        "nothing about the budget: %r" % full.get("unparsed_artifacts"))
