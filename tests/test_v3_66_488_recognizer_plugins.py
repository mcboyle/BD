"""v3.66.488 K1 (plugin-v3, first new KIND): recognizer plugins.

Review-only custom player/site detectors that ride detect.py's merge layer --
avoids the extraction_core guard by design. A recognizer plugin's verdict is
ADVISORY: it is folded into the merged scorecard but can NEVER auto-enable a
template (posture invariant), low-confidence verdicts are DEMOTED (not trusted),
the plugin pass is exception-isolated, and adding a recognizer plugin does NOT
perturb the existing builtin verdicts (corpus-regression invariant).

Contract:
  recognize(dom_excerpt, network_summary, ctx) -> {player_family, confidence, evidence}

K1 raises PLUGIN_API_MAX to 3 under R5's range model so api=2 plugins keep
loading.

Runner-safe: zero-arg test fns, no pytest builtins, paths from __file__,
module globals restored in try/finally.
"""
import copy
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P   # noqa: E402
from bulk_downloader import detect as D    # noqa: E402


# ── (api) range model: K1 raises PLUGIN_API_MAX to 3 ──────────────────
def test_api_max_raised_to_3_keeps_api2_compatible():
    assert P.PLUGIN_API_MAX >= 3, "K1 must raise PLUGIN_API_MAX to 3"
    ok2, _ = P.api_compatible({"api_version": 2})
    ok3, _ = P.api_compatible({"api_version": 3})
    assert ok2, "api=2 plugins must keep loading (range model)"
    assert ok3, "api=3 (recognizer kind) plugins must load"


def test_recognizer_capability_documented():
    assert getattr(P, "CAP_RECOGNIZER", None) == "recognizer"
    ke = P.known_events()
    assert P.CAP_RECOGNIZER in ke["capabilities"]
    assert ke["api_max"] >= 3


# ── registry / introspection ──────────────────────────────────────────
def test_register_list_and_status_expose_recognizers():
    P.reset()
    try:
        def custom(dom, net, ctx):
            return {}
        P.register_recognizer(custom, name="c1", priority=50)
        names = [r["name"] for r in P.list_recognizers()]
        assert "c1" in names
        st = P.status()
        assert "recognizers" in st
        assert any(r["name"] == "c1" for r in st["recognizers"])
    finally:
        P.reset()


def test_reset_clears_recognizers():
    P.reset()
    try:
        P.register_recognizer(lambda d, n, c: {}, name="c1")
        assert P.list_recognizers()
        P.reset()
        assert P.list_recognizers() == []
    finally:
        P.reset()


# ── (a) verdict appears in the merged scorecard ───────────────────────
def test_recognizer_verdict_appears_in_merged_scorecard():
    P.reset()
    try:
        def acme(dom, net, ctx):
            return {"player_family": "acme", "confidence": 0.9,
                    "evidence": ["data-acme"]}
        P.register_recognizer(acme, name="acme")
        builtin = [{"player_family": "videojs", "confidence": 0.8,
                    "source": "builtin"}]
        merged = D.merge_plugin_recognitions(
            builtin, dom_excerpt="<div data-acme>", network_summary={}, ctx={})
        fams = [v.get("player_family") for v in merged]
        assert "acme" in fams
        v = [x for x in merged if x.get("player_family") == "acme"][0]
        assert v.get("source") == "plugin"
        assert v.get("name") == "acme"
    finally:
        P.reset()


# ── (b) posture invariant: a verdict can NEVER enable a template ──────
def test_recognizer_can_never_enable_template():
    P.reset()
    try:
        def evil(dom, net, ctx):
            # A hostile plugin tries to smuggle an enable signal.
            return {"player_family": "x", "confidence": 1.0,
                    "enabled": True, "auto_enable": True, "enable": True,
                    "enable_template": True, "action": "enable"}
        P.register_recognizer(evil, name="evil")
        merged = D.merge_plugin_recognitions([], ctx={})
        v = [x for x in merged if x.get("player_family") == "x"][0]
        for k in ("enabled", "auto_enable", "enable",
                  "enable_template", "action"):
            assert k not in v, f"enable signal {k!r} must be stripped"
        # The verdict is explicitly review-only.
        assert v.get("review_only") is True
    finally:
        P.reset()


# ── (c) low-confidence verdicts are demoted, not trusted ──────────────
def test_low_confidence_verdict_demoted():
    P.reset()
    try:
        def weak(dom, net, ctx):
            return {"player_family": "weak", "confidence": 0.1}
        P.register_recognizer(weak, name="weak")
        merged = D.merge_plugin_recognitions([], confidence_floor=0.5)
        v = [x for x in merged if x.get("player_family") == "weak"][0]
        assert v.get("demoted") is True
    finally:
        P.reset()


def test_high_confidence_not_demoted():
    P.reset()
    try:
        def strong(dom, net, ctx):
            return {"player_family": "strong", "confidence": 0.95}
        P.register_recognizer(strong, name="strong")
        merged = D.merge_plugin_recognitions([], confidence_floor=0.5)
        v = [x for x in merged if x.get("player_family") == "strong"][0]
        assert v.get("demoted") is False
    finally:
        P.reset()


def test_confidence_clamped_to_unit_interval():
    P.reset()
    try:
        def wild(dom, net, ctx):
            return {"player_family": "wild", "confidence": 9.0}
        P.register_recognizer(wild, name="wild")
        merged = D.merge_plugin_recognitions([])
        v = [x for x in merged if x.get("player_family") == "wild"][0]
        assert 0.0 <= v["confidence"] <= 1.0
    finally:
        P.reset()


# ── (d) exception isolation ───────────────────────────────────────────
def test_recognizer_exception_isolated():
    P.reset()
    try:
        def boom(dom, net, ctx):
            raise RuntimeError("nope")

        def good(dom, net, ctx):
            return {"player_family": "good", "confidence": 0.9}
        P.register_recognizer(boom, name="boom")
        P.register_recognizer(good, name="good")
        # Must not raise.
        merged = D.merge_plugin_recognitions([], ctx={})
        fams = [v.get("player_family") for v in merged]
        assert "good" in fams
        # The throwing plugin contributes NO trusted verdict.
        assert "boom" not in [v.get("name") for v in merged
                              if v.get("player_family")]
    finally:
        P.reset()


# ── (e) corpus regression: builtin verdicts are not perturbed ─────────
def test_builtin_verdicts_unperturbed():
    P.reset()
    try:
        builtin = [{"player_family": "videojs", "confidence": 0.8,
                    "source": "builtin", "evidence": ["vjs"]}]
        before = copy.deepcopy(builtin)
        P.register_recognizer(
            lambda d, n, c: {"player_family": "acme", "confidence": 0.9},
            name="acme")
        merged = D.merge_plugin_recognitions(builtin, ctx={})
        builtin_out = [v for v in merged if v.get("source") == "builtin"]
        assert builtin_out == before, "builtin verdicts must be preserved"
        assert builtin == before, "input list must not be mutated"
    finally:
        P.reset()


def test_no_plugins_returns_builtin_unchanged():
    P.reset()
    try:
        builtin = [{"player_family": "videojs", "confidence": 0.8,
                    "source": "builtin"}]
        merged = D.merge_plugin_recognitions(list(builtin), ctx={})
        assert merged == builtin
    finally:
        P.reset()


# ── decorator parity with the programmatic register ───────────────────
def test_recognizer_decorator_registers():
    P.reset()
    try:
        @P.recognizer(priority=10, name="deco")
        def _r(dom, net, ctx):
            return {"player_family": "deco", "confidence": 0.7}
        merged = D.merge_plugin_recognitions([])
        assert "deco" in [v.get("player_family") for v in merged]
    finally:
        P.reset()
