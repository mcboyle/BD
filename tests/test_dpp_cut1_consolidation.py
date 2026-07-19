"""D++ cut 1 (Layer A) — β consolidation of the inline ``player={}`` block.

`build_template_from_wacz._html_selectors` carried an inline player-selector
if/elif chain that DUPLICATED the registry (`player_recognition` +
`player_families`), which the builder already merges in. The two diverged:
video-js emitted both ``play`` (registry) and ``play_button`` (inline) for the
same selector, and the jwplayer ``.jw-icon-display`` button + the aria
``title="Play Video"`` button existed ONLY inline. This cut canonicalizes the
registry on ``play_button`` (the sole key any consumer reads — ``play`` was an
orphan), ports the two inline-only buttons into the registry, then deletes the
inline block so there is ONE source of player selectors.

Two kinds of assertion here:
  * CHARACTERIZATION (green before AND after): the merged ``selectors.player``
    container + play_button for each framework are byte-for-byte what the inline
    block produced — behaviour preserved.
  * OUTCOME (RED before, GREEN after): no orphan ``play`` key survives, and the
    inline block is gone from ``_html_selectors``.

SYNTHETIC only. Non-guard (builder + recognizer surface).
"""
import os
import sys
import tempfile
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "tools"))
import build_template_from_wacz as b  # noqa: E402

# _sha256_file needs a real path; reuse one tiny temp file for every drive().
_TMP = tempfile.NamedTemporaryFile(suffix=".wacz", delete=False)
_TMP.write(b"synthetic")
_TMP.close()

_VJS = '<div class="video-js"><button class="vjs-big-play-button"></button></div>'
_THEO = '<div class="theoplayer-skin"></div>'
_JW = '<div id="jwplayer-0" class="jwplayer jw-flag-x"><div class="jw-icon-display"></div></div>'
_ARIA = '<div aria-label="video player"><button title="Play Video"></button></div>'


def _player(html):
    """Merged build_template player selectors for synthetic single-snapshot HTML."""
    orig = b._load_capture
    b._load_capture = lambda p: {
        "dom_log": [{"type": "full_snapshot", "html": html}],
        "network_log": [],
        "action_timeline": [],
    }
    try:
        tmpl = b.build_template(Path(_TMP.name))
    finally:
        b._load_capture = orig
    return (tmpl.get("selectors") or {}).get("player") or {}


# --------------------------------------------------------------------------- #
# CHARACTERIZATION — merged player selectors preserved (green before + after)
# --------------------------------------------------------------------------- #
def test_videojs_container_and_play_button():
    p = _player(_VJS)
    assert p.get("container") == ".video-js"
    assert p.get("play_button") == "button.vjs-big-play-button"


def test_theoplayer_container_only():
    p = _player(_THEO)
    assert p.get("container") == ".theoplayer-skin"
    assert "play_button" not in p  # inline produced none; registry must not invent one


def test_jwplayer_container_and_play_button():
    p = _player(_JW)
    assert p.get("container") == ".jwplayer"
    assert p.get("play_button") == ".jw-icon-display, .jw-icon-playback"


def test_aria_video_player_container_and_play_button():
    p = _player(_ARIA)
    assert p.get("container") == '[aria-label="video player"]'
    assert p.get("play_button") == 'button[title="Play Video"]'


# --------------------------------------------------------------------------- #
# OUTCOME — single canonical key + inline block removed (RED before, GREEN after)
# --------------------------------------------------------------------------- #
def test_no_orphan_play_key_in_merged_player():
    # The registry's ``play`` key was an orphan (no reader). After canonicalizing
    # on ``play_button`` it must not survive in the shipped selectors.
    for html in (_VJS, _JW, _ARIA, _THEO):
        assert "play" not in _player(html), "orphan 'play' key still emitted"


def test_inline_player_block_removed_from_html_selectors():
    # _html_selectors no longer emits a player block; player comes from the
    # registry merge alone.
    assert "player" not in b._html_selectors(_VJS)
    assert "player" not in b._html_selectors(_JW)
    assert "player" not in b._html_selectors(_ARIA)
