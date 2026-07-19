"""F-COREBD05-01 -- the plugin-verdict confidence fold must reject non-finite
(NaN / inf) confidences so a NaN cannot evade the low-confidence demotion.

detect.merge_plugin_recognitions clamps a plugin recognizer's confidence with
``conf = 0.0 if conf < 0.0 else (1.0 if conf > 1.0 else conf)`` and then marks
``demoted = conf < floor``. ``float('nan')`` passes the float() coercion and
evades BOTH clamp comparisons (``nan < 0.0`` and ``nan > 1.0`` are each False),
so the stored confidence stays NaN and ``nan < floor`` is False -- the verdict
is folded in NON-demoted, violating the module posture that a verdict below the
confidence floor is advisory (demoted), not trusted.

RED on pinned source: a NaN (or 'nan') plugin confidence folds in with a
non-finite stored confidence and demoted=False. GREEN once a math.isfinite
backstop rejects the non-finite value (conf -> 0.0 -> demoted=True). The finite
controls (0.1 demoted, 0.9 not demoted) must keep working either way.
"""
import math

import bulk_downloader.detect as detect
import bulk_downloader.plugins as plugins


def _fold_one(monkeypatch, raw_confidence, floor=None):
    """Drive merge_plugin_recognitions with a single plugin verdict carrying
    ``raw_confidence`` and return the folded (source='plugin') verdict dict."""

    def fake_run(dom_excerpt, network_summary, ctx):
        return [{
            "ok": True,
            "verdict": {"player_family": "hls", "confidence": raw_confidence},
            "name": "test-plugin",
        }]

    # merge_plugin_recognitions does a lazy ``from . import plugins`` each call,
    # so patch the real module function.
    monkeypatch.setattr(plugins, "run_recognizers", fake_run)
    out = detect.merge_plugin_recognitions([], dom_excerpt="x", confidence_floor=floor)
    folded = [v for v in out if v.get("source") == "plugin"]
    assert len(folded) == 1, f"expected exactly one folded plugin verdict, got {folded!r}"
    return folded[0]


def test_nan_confidence_is_finite_and_demoted(monkeypatch):
    v = _fold_one(monkeypatch, float("nan"))
    assert math.isfinite(v["confidence"]), (
        f"NaN plugin confidence leaked through the fold: {v['confidence']!r}")
    assert v["demoted"] is True, (
        "a NaN-confidence plugin verdict must be demoted (posture bypass otherwise)")


def test_string_nan_confidence_is_finite_and_demoted(monkeypatch):
    v = _fold_one(monkeypatch, "nan")
    assert math.isfinite(v["confidence"]), (
        f"string-'nan' plugin confidence leaked through the fold: {v['confidence']!r}")
    assert v["demoted"] is True


def test_inf_confidence_is_finite_and_demoted(monkeypatch):
    v = _fold_one(monkeypatch, float("inf"))
    assert math.isfinite(v["confidence"]), (
        f"inf plugin confidence leaked through the fold: {v['confidence']!r}")
    assert v["demoted"] is True


def test_finite_low_confidence_still_demoted(monkeypatch):
    # control: a genuinely low finite confidence IS demoted, unchanged by the fix.
    v = _fold_one(monkeypatch, 0.1)
    assert v["confidence"] == 0.1
    assert v["demoted"] is True


def test_finite_high_confidence_not_demoted(monkeypatch):
    # control: a high finite confidence is folded in NON-demoted, unchanged.
    v = _fold_one(monkeypatch, 0.9)
    assert v["confidence"] == 0.9
    assert v["demoted"] is False
