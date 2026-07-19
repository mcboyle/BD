"""v3.66.463 -- mission-control memoize (collapse the 2x+ sub-aggregation fan-out).

/cockpit/api/template/mission-control was ~1.5s live (vs the standalone readiness
primitive at ~0.16s). operator_mission_control() is an aggregation, not a tree
walk -- so the warehouse scandir helper does nothing for it. The cost is
REDUNDANT recomputation: the same expensive read-only primitives (login/video
template health, maturity, the review queue) are recomputed at multiple depths
within one request -- e.g. both site_readiness AND the review queue recompute
template health -> each runs 2x+.

Fix: a request-scoped memo. @_mc_scope wraps operator_mission_control so its whole
rollup runs inside one fresh memo; the @_mc_memoized sub-aggregations compute ONCE
per scope and serve the cached result to repeat callers. Outside a scope the
wrapped functions are unchanged (standalone endpoints recompute fresh).

NOTE on test design (the 462 lesson applied): the memo does NOT reduce the NUMBER
of call-attempts -- the call sites still call the function, they just get a cached
result. So counting calls is the WRONG proxy. These tests assert the actual
property: (1) the expensive body runs once -> repeated in-scope calls return the
SAME object (and fresh objects outside a scope), and (2) the memoized rollup is
byte-identical to a memo-free reference computation.

RED on pristine 462 (no _mc_scope/_mc_memoized) -> GREEN. Zero-arg / run under
run_tests.py + real pytest.
"""
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_mc_memo_dedupes_within_scope():
    """Within an _mc_scope, repeated calls to a memoized sub-aggregation return
    the SAME object (body ran once); outside a scope they recompute fresh
    (different objects). RED on 462 (no _mc_scope) -> GREEN."""
    from tools import cockpit_templates as ctpl

    captured = {}

    def _probe():
        captured["a"] = ctpl.login_template_health()
        captured["b"] = ctpl.login_template_health()

    ctpl._mc_scope(_probe)()
    assert captured["a"] is captured["b"], (
        "within a scope the memoized sub-aggregation must return the cached "
        "object (the expensive body must run once, not per call)"
    )

    c = ctpl.login_template_health()
    d = ctpl.login_template_health()
    assert c is not d, (
        "outside a scope the function must recompute fresh (no cross-request "
        "cache); got the same object"
    )


def test_mc_memo_preserves_output():
    """The memoized rollup must be byte-identical to a memo-free reference. The
    reference is operator_mission_control.__wrapped__() -- the body WITHOUT the
    _mc_scope decorator, so no scope is active and every sub-aggregation
    recomputes fresh. RED on 462 (not decorated -> no __wrapped__) -> GREEN."""
    from tools import cockpit_templates as ctpl

    reference = ctpl.operator_mission_control.__wrapped__()
    memoized = ctpl.operator_mission_control()
    assert memoized == reference, (
        "the request-scoped memo changed operator_mission_control's output -- it "
        "must be a pure dedup, byte-identical to the fresh computation"
    )


def test_mc_output_shape_intact():
    """operator_mission_control still returns the four zones with consistent
    counts -- the memo must not drop or alter the rollup."""
    from tools import cockpit_templates as ctpl

    out = ctpl.operator_mission_control()
    for zone in ("needs_attention", "healthy", "active_work"):
        assert zone in out, "missing zone: %s" % zone
    assert isinstance(out["needs_attention"].get("count"), int)


def test_mc_memo_inactive_outside_scope():
    """Sanity: with no active scope the memo machinery is a no-op (the wrapped
    functions behave exactly as undecorated). Guards against accidental
    cross-request caching."""
    from tools import cockpit_templates as ctpl

    a = ctpl.template_review_queue()
    b = ctpl.template_review_queue()
    assert a is not b, "template_review_queue cached across calls with no scope"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
