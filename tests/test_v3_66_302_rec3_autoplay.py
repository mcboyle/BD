"""v3.66.302 — REC-3 autoplay-window guard.

The action_timeline download-trigger derivation trusts a click's
``effect.direct_media >= 1``. On an autoplay/streaming site the player's media
is already in flight when the operator clicks a download affordance, so the
2500 ms correlation window credits that click with direct media it did not
cause → a false download trigger.

Fork B (capture-side, inspect_pick): each correlated action carries the
effect-attribution the builder needs — ``effect.autoplay`` (media was already in
flight before this click) and ``effect.fresh_download`` (a NEW download-shaped
media/manifest appeared at/after the click that was not already streaming).
Pure, from network-log timing; F2 (counts/booleans, no URLs cross the boundary).

Fork A (builder-side, build_template_from_wacz): the timeline trigger derivation
rejects a download-affordance click whose direct media is autoplay-explained
(``autoplay`` and not ``fresh_download``), and — for legacy captures whose
effect predates the attribution — back-computes it from a passed network_log +
first-click ts. A genuine download click (fresh download signal after it)
survives.
"""
import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))


def _media(ts, url, ctype="video/mp4"):
    return {"timestamp": ts, "url": url, "content_type": ctype,
            "resource_type": "media"}


# ── Fork B: inspect_pick attribution ──────────────────────────────────────
def test_forkb_autoplay_stream_is_flagged():
    import bulk_downloader.inspect_pick as ip
    # media streaming from t=0; the only click is at t=2000 — media was already
    # in flight (autoplay) and nothing NEW download-shaped appears after it.
    nl = [_media(0, "https://cdn.x/stream/seg0.ts", "video/mp2t"),
          _media(500, "https://cdn.x/stream/seg1.ts", "video/mp2t"),
          _media(2200, "https://cdn.x/stream/seg2.ts", "video/mp2t")]
    picks = [{"descriptor": {"tag": "a", "outer_html": "<a download>x</a>",
                             "attrs": {"download": ""}}, "ts": 2000}]
    tl = ip.correlate_timeline(picks, nl)
    eff = tl[0]["effect"]
    assert "autoplay" in eff and "fresh_download" in eff, eff
    assert eff["autoplay"] is True, eff
    assert eff["fresh_download"] is False, eff


def test_forkb_genuine_download_is_fresh():
    import bulk_downloader.inspect_pick as ip
    # click at t=2000 followed by a NEW signed mp4 (shape unseen before) — a
    # real download trigger, even if a preview stream was already playing.
    nl = [_media(0, "https://cdn.x/preview/seg0.ts", "video/mp2t"),
          _media(2100, "https://dl.x/files/movie.mp4?token=abc", "video/mp4")]
    picks = [{"descriptor": {"tag": "a", "outer_html": "<a download>x</a>",
                             "attrs": {"download": ""}}, "ts": 2000}]
    tl = ip.correlate_timeline(picks, nl)
    eff = tl[0]["effect"]
    assert eff["fresh_download"] is True, eff


# ── Fork A: builder rejects autoplay-explained trigger ────────────────────
def _btf():
    spec = importlib.util.spec_from_file_location(
        "build_template_from_wacz", _REPO / "tools" / "build_template_from_wacz.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_forka_rejects_autoplay_contaminated_click():
    btf = _btf()
    # download-affordance click whose direct_media is the autoplay stream only
    at = [{"selector": "a.dl[download]", "role": "download", "tag": "a",
           "ts": 2000,
           "effect": {"direct_media": 1, "autoplay": True,
                      "fresh_download": False, "manifest": 0}}]
    assert btf._download_trigger_from_timeline(at) is None


def test_forka_keeps_genuine_download_click():
    btf = _btf()
    at = [{"selector": "a.dl[download]", "role": "download", "tag": "a",
           "ts": 2000,
           "effect": {"direct_media": 1, "autoplay": True,
                      "fresh_download": True, "manifest": 0}}]
    out = btf._download_trigger_from_timeline(at)
    assert out is not None and out[1] == "a", out


def test_forka_legacy_effect_backcomputed_from_network_log():
    """A pre-302 capture's effect lacks autoplay/fresh_download. When the caller
    passes network_log + first-click ts, the guard back-computes attribution so
    the autoplay stream is still rejected."""
    btf = _btf()
    at = [{"selector": "a.dl[download]", "role": "download", "tag": "a",
           "ts": 2000, "effect": {"direct_media": 1}}]  # legacy effect
    nl = [_media(0, "https://cdn.x/stream/seg0.ts", "video/mp2t"),
          _media(2200, "https://cdn.x/stream/seg1.ts", "video/mp2t")]
    assert btf._download_trigger_from_timeline(at, network_log=nl) is None


def test_forka_unaffected_when_no_autoplay_signal():
    """Back-compat: a click with direct_media and no autoplay signal at all
    (neither stamped nor derivable) is still accepted — the guard never
    fabricates a rejection."""
    btf = _btf()
    at = [{"selector": "a.dl[download]", "role": "download", "tag": "a",
           "ts": 2000, "effect": {"direct_media": 1}}]
    out = btf._download_trigger_from_timeline(at)  # no network_log
    assert out is not None, out
