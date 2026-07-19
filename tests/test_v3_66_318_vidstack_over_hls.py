"""v3.66.318 — vidstack-over-hls recognition (miruro.tv).

RED-first regression for the real-world vidstack-over-hls shape: a genuine
vidstack player (vds- class density 950-1136, the vidstack-*.js bundle, and
``vds-player:`` localStorage keys) that uses hls.js as its HLS engine and ships
the DEFAULT layout — NO literal ``<media-player>`` element in the DOM snapshot.

Before the fix this misclassified on the 317 tree:
  * ``mirurow`` scored plyr 1.0 (vidstack's default skin reuses plyr-ish control
    classes — ``plyr__controls``/``data-plyr``) over vidstack 0.9, so plyr won.
  * the ``vds-player:`` storage prefix never confirmed vidstack — the marker
    only matched ``^vidstack[:_-]`` / ``vidstack::``.

The fix (``tools/player_recognition.py``, non-guard) is two-part:
  1. extend the vidstack storage marker to ``^vds-player[:_-]`` so the real
     runtime keys confirm the engine via the storage channel.
  2. promote vidstack out of the weak-brand floor on STRONG evidence (high
     vds- class density AND its own script and/or ``vds-player:`` storage), so
     it outranks the underlying hls.js engine and plyr's confusable markers.

Fixtures are the two operator-confirmed redacted miruro.tv captures
(``tests/fixtures/vidstack/{miruro,mirurow}.redacted.wacz``). Both MUST classify
vidstack, and the confirm must rest on the ``vds-player:`` storage / vidstack
script — never on a ``<media-player>`` element (absent here).
"""
import os
import re
import sys
import json
import zipfile
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import player_recognition as pr  # noqa: E402
import build_template_from_wacz as btw  # noqa: E402

_FIX = Path(_REPO) / "tests" / "fixtures" / "vidstack"
_WACZ = {
    "miruro": _FIX / "miruro.redacted.wacz",
    "mirurow": _FIX / "mirurow.redacted.wacz",
}


def _inputs(wacz_path):
    """Mirror build_template_from_wacz's recognizer-input extraction exactly
    (combined DOM html + iframe hosts + storage key NAMES + script srcs)."""
    with zipfile.ZipFile(wacz_path) as z:
        cap = json.loads(z.read("archive/capture.json").decode("utf-8", "replace"))
    dom_log = cap.get("dom_log") or []
    network_log = cap.get("network_log") or []
    fulls = [e for e in dom_log
             if isinstance(e, dict) and e.get("type") == "full_snapshot"
             and isinstance(e.get("html"), str)]
    html = "\n".join(e.get("html", "") for e in fulls)
    html = (html + "\n" + btw._nodes_to_html(dom_log)).strip()
    iframe_hosts = re.findall(r'<iframe[^>]+src=["\']https?://([^/"\']+)', html, re.I)
    ss = cap.get("storage_snapshot") or {}
    storage_keys = (list((ss.get("local_storage") or {}).keys())
                    + list((ss.get("session_storage") or {}).keys()))
    script_srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)', html, re.I)
    script_srcs += [e.get("url") for e in network_log
                    if isinstance(e, dict) and isinstance(e.get("url"), str)
                    and e["url"].split("?", 1)[0].endswith(".js")]
    return html, script_srcs, iframe_hosts, network_log, storage_keys


def test_fixtures_present():
    for name, p in _WACZ.items():
        assert p.exists(), f"missing fixture {name}: {p}"


def test_vds_player_storage_confirms_vidstack():
    # Part 1: the real ``vds-player:`` localStorage prefix must confirm vidstack.
    assert "vidstack" in pr._storage_confirmed(
        ["vds-player:display-bg", "vds-player:font-size"])
    # the ``vds-player:`` keys as redacted (NAME has a trailing " [v]" marker)
    # must still match — the prefix anchor does not care about the suffix.
    assert "vidstack" in pr._storage_confirmed(["vds-player:font-family [v]"])
    # the OLD-style markers must still confirm (no regression).
    assert "vidstack" in pr._storage_confirmed(["vidstack:foo"])
    assert "vidstack" in pr._storage_confirmed(["x", "vidstack::y"])
    # an unrelated miruro app key must NOT confirm vidstack.
    assert "vidstack" not in pr._storage_confirmed(["miruro:settings:theme"])


def test_miruro_classifies_vidstack_over_hls():
    for name, wacz in _WACZ.items():
        html, sc, ifr, net, sk = _inputs(wacz)
        # the real-world vidstack-over-hls shape: default layout, no element.
        assert "<media-player" not in html, \
            f"{name}: fixture unexpectedly contains a <media-player> element"
        # the evidence the confirm must rest on (NOT the absent element):
        vidstack_script = any("vidstack" in str(s).lower() for s in sc)
        vds_player_storage = "vidstack" in pr._storage_confirmed(sk)
        assert vidstack_script or vds_player_storage, \
            f"{name}: no vidstack script / vds-player storage evidence in fixture"
        rec = pr.detect(html, script_srcs=sc, iframe_hosts=ifr,
                        network=net, storage_keys=sk)
        cands = [(c["family"], c["score"]) for c in (rec.get("candidates") or [])[:5]]
        assert rec.get("player_family") == "vidstack", \
            f"{name}: expected vidstack, got {rec.get('player_family')} (candidates={cands})"
        # once Part 1 lands, the engine is storage-confirmed via vds-player:.
        assert "vidstack" in (rec.get("storage_confirmed") or []), \
            f"{name}: vidstack not storage-confirmed (vds-player: marker missing?)"


def test_miruro_pipeline_path_agrees():
    # The full production build_template path must agree with detect().
    for name, wacz in _WACZ.items():
        draft = btw.build_template(wacz)
        fam = (draft.get("recognition") or {}).get("player_family")
        assert fam == "vidstack", \
            f"{name}: build_template gave {fam}, expected vidstack"


# ── Part 2 isolation units (synthetic; no fixture dependency) ───────────────
def test_strong_vidstack_evidence_outranks_plyr_and_hlsjs():
    """A synthetic default-layout vidstack page: high vds- class density + the
    vidstack bundle, dressed in plyr-confusable skin classes (which score plyr to
    1.0) and sitting on hls.js. Strong vidstack evidence must outrank BOTH the
    underlying engine and the confusable markers — this is the part 2 blocker that
    extending the storage marker alone does not fix."""
    vds_blob = " ".join(f'<div class="vds-c{i} vds-button">x</div>' for i in range(150))
    html = (
        '<div class="plyr plyr__controls plyr__menu" data-plyr="x">'
        + vds_blob
        + '<script src="https://cdn.example/vidstack-abc123.js"></script>'
        + '<script src="https://cdn.example/plyr.min.js"></script>'
        + '<script src="https://cdn.example/hls.min.js"></script>'
        + '</div>'
    )
    script_srcs = ["https://cdn.example/vidstack-abc123.js",
                   "https://cdn.example/plyr.min.js",
                   "https://cdn.example/hls.min.js"]
    network = [{"url": "https://cdn.example/master.m3u8"}]
    rec = pr.detect(html, script_srcs=script_srcs, network=network)
    cands = [(c["family"], c["score"]) for c in (rec.get("candidates") or [])[:5]]
    assert rec.get("player_family") == "vidstack", \
        f"strong vidstack evidence lost to {rec.get('player_family')} (candidates={cands})"


def test_faint_vds_trace_without_script_or_storage_does_not_promote():
    """Guard the AND-gate: a faint incidental vds- trace WITHOUT the vidstack script
    and WITHOUT vds-player: storage must NOT promote vidstack over a genuine player.
    Protects sites like optimizely.com that carry a small incidental vds- count with
    no vidstack lib and no vds-player storage."""
    vds_blob = " ".join(f'<span class="vds-x{i}">.</span>' for i in range(40))
    html = ('<div class="video-js vjs-default-skin" data-vjs-player>'
            + vds_blob
            + '<script src="https://cdn.example/video.min.js"></script></div>')
    script_srcs = ["https://cdn.example/video.min.js"]
    rec = pr.detect(html, script_srcs=script_srcs)
    assert rec.get("player_family") != "vidstack", \
        f"faint vds- trace wrongly promoted vidstack (candidates={rec.get('candidates')})"
