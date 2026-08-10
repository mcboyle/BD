"""@1015. capture_analytics gets the two bounds its sibling already had.

MEASURED ON THE BOX, and the two captures are the whole argument. Both at
commit `fe88b5a` (v3.66.1013) -- the SAME TREE:

    capture 7   L34 FAIL -- /api/data/capture_analytics > 8s WHEN PROBED ALONE
    capture 8   L34 PASS -- 36/36

Identical source, opposite verdicts. `app_data_layer._cached` holds results for
`_HEAVY_TTL_S = 600`, so a probe inside that window reads a cached answer and a
probe outside it pays the full scan. **The route is borderline, not repaired,
and the pass was about cache warmth rather than about the code.** A gate that
passes on cache warmth is the kind of green that teaches an operator to ignore
it later.

THE GAP IS EXACT. `app_data_layer.py` carries one comment covering both heavy
collectors, and it names the bound set as "count + wall-time budget + per-file
size cap". Its two callers do not agree:

    collect_capture_diagnostics -> CD.collect(root, limit=, budget_s=, max_bytes=)
    collect_capture_analytics   -> CA.analyze(root, limit=)

and `analyze()` could not have taken the other two -- its signature is
`analyze(root=".", dirs=None, limit=None)`. So the caller was not careless; the
bound did not exist to pass. On a 4 GB store with wacz over 100MB, 50 files with
no size cap and no wall-clock budget is a coin flip against an 8s gate.

WHERE THE COST ACTUALLY IS, read from source rather than assumed: `_artifacts`
applies `limit` BEFORE the per-file work (correct), and then does
`json.load(fh)` on every surviving `capture_*.json`. A capture result carries
its `network_log`, so one of those files can be enormous. That parse is the
unbounded step.

A BOUND THAT SILENTLY SHRINKS THE DENOMINATOR IS WORSE THAN NO BOUND. Anything
skipped for size or budget is still COUNTED and still reported, with the reason
-- otherwise the report claims a completeness it does not have, which is the
failure this whole codebase is organised against.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))


def _ca():
    import capture_analytics as CA
    return CA


@pytest.fixture
def store(tmp_path):
    """A capture store with one small and one large capture_*.json.

    The large one carries a real `network_log` so its cost is the json.load,
    which is the step being bounded -- a file padded with whitespace would be
    large without being expensive to parse into the shape the analyser reads.
    """
    d = tmp_path / "captures"
    d.mkdir()
    (d / "capture_small.json").write_text(json.dumps({
        "capture_kind": "network", "network_log": [{"url": "https://x.invalid/1"}],
    }), encoding="utf-8")
    big = {"capture_kind": "dom+network",
           "network_log": [{"url": "https://x.invalid/%d" % i} for i in range(20000)],
           "dom_log": [{"t": i} for i in range(20000)]}
    (d / "capture_big.json").write_text(json.dumps(big), encoding="utf-8")
    return tmp_path


# ── the signature gained the bounds ───────────────────────────────

def test_analyze_accepts_the_same_bounds_as_its_sibling():
    """The caller could not pass what the callee did not accept."""
    params = inspect.signature(_ca().analyze).parameters
    for name in ("limit", "budget_s", "max_bytes"):
        assert name in params, "analyze() has no %s parameter" % name
        assert params[name].default is None, (
            "%s must default to None -- unbounded is the CLI's contract and "
            "changing it silently would change what every existing caller does"
            % name)


def test_the_route_passes_all_three():
    """The gap that produced the box failure: the sibling passed three bounds
    and this one passed one. AST over the caller, not a grep, so a mention in a
    comment cannot satisfy it."""
    import ast
    src = (REPO / "bulk_downloader" / "app_data_layer.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef)
              and n.name == "collect_capture_analytics")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "analyze"]
    assert calls, "collect_capture_analytics no longer calls analyze()"
    kw = {k.arg for c in calls for k in c.keywords}
    assert {"limit", "budget_s", "max_bytes"} <= kw, (
        "the route passes only %r -- the comment above it names the bound set "
        "as count + wall-time budget + per-file size cap" % sorted(kw))


# ── max_bytes ─────────────────────────────────────────────────────

def test_max_bytes_skips_the_PARSE_of_an_oversized_capture(store):
    """The bound has to stop the json.load, which is the cost."""
    CA = _ca()
    small = (store / "captures" / "capture_small.json").stat().st_size
    big = (store / "captures" / "capture_big.json").stat().st_size
    assert big > small * 10, (small, big)     # the fixture is doing its job

    out = CA.analyze(str(store), max_bytes=small + 1)
    by = {a["path"].split("/")[-1]: a for a in out["artifacts"]["items"]}
    assert by["capture_small.json"]["network_log_count"] == 1, (
        "the small capture should still be parsed")
    assert by["capture_big.json"]["network_log_count"] is None, (
        "the oversized capture was parsed anyway -- the bound did nothing")


def test_an_oversized_capture_is_still_COUNTED_and_says_WHY(store):
    """A bound that shrinks the denominator silently is worse than no bound:
    the report would claim a completeness it does not have."""
    CA = _ca()
    small = (store / "captures" / "capture_small.json").stat().st_size
    out = CA.analyze(str(store), max_bytes=small + 1)
    paths = {a["path"].split("/")[-1] for a in out["artifacts"]["items"]}
    assert "capture_big.json" in paths, "the skipped capture vanished from the report"
    by = {a["path"].split("/")[-1]: a for a in out["artifacts"]["items"]}
    assert by["capture_big.json"]["bytes"] > 0, "its size was not recorded"
    assert by["capture_big.json"].get("unparsed") == "max_bytes", (
        "nothing says WHY it has no counts: %r" % by["capture_big.json"])


def test_without_max_bytes_everything_is_parsed(store):
    """THE OTHER DIRECTION. A bound that always fires satisfies the tests above
    and turns the analyser into a file lister. Unbounded is the CLI's contract
    and must survive."""
    out = _ca().analyze(str(store))
    by = {a["path"].split("/")[-1]: a for a in out["artifacts"]["items"]}
    assert by["capture_big.json"]["network_log_count"] == 20000
    assert by["capture_small.json"]["network_log_count"] == 1
    assert all(a.get("unparsed") is None for a in out["artifacts"]["items"])


# ── budget_s ──────────────────────────────────────────────────────

def test_an_exhausted_budget_stops_parsing_and_says_so(store):
    """Zero budget is the deterministic form of "the wall ran out". Nothing is
    dropped; everything is listed, and what was not parsed says why."""
    out = _ca().analyze(str(store), budget_s=0.0)
    items = out["artifacts"]["items"]
    assert len(items) == 2, "artifacts vanished when the budget ran out"
    js = [a for a in items if a["type"] == "capture_json"]
    assert js and all(a.get("unparsed") == "budget_s" for a in js), (
        "a spent budget did not stop the parse, or did not say why: %r" % js)


def test_a_generous_budget_parses_everything(store):
    """The over-sensitive direction: a budget that fires immediately would pass
    the test above while making the analyser useless."""
    out = _ca().analyze(str(store), budget_s=600.0)
    by = {a["path"].split("/")[-1]: a for a in out["artifacts"]["items"]}
    assert by["capture_big.json"]["network_log_count"] == 20000
    assert all(a.get("unparsed") is None for a in out["artifacts"]["items"])


# ── the report stays honest ───────────────────────────────────────

def test_the_report_counts_what_it_could_not_parse(store):
    """A reader must be able to tell a complete pass from a bounded one without
    inspecting every row."""
    CA = _ca()
    small = (store / "captures" / "capture_small.json").stat().st_size
    out = CA.analyze(str(store), max_bytes=small + 1)
    assert out.get("unparsed_artifacts") == 1, (
        "the bounded pass does not report how much it skipped: %r"
        % out.get("unparsed_artifacts"))
    full = CA.analyze(str(store))
    assert full.get("unparsed_artifacts") == 0


def test_the_existing_shape_is_unchanged(store):
    """Additive. Every key the report published before must still be there --
    the Capture Reports view and capture_diagnostics both read this."""
    out = _ca().analyze(str(store))
    assert {"root", "searched_dirs", "artifacts"} <= set(out)
    assert {"count", "total_bytes", "by_host", "items"} <= set(out["artifacts"])
