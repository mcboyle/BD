"""v3.66.321 — guarded struct_embed tie-breaker.

AI-6 promotion: the structural-embedding verdict (``player_struct_embed.classify``)
is, by default, a purely advisory ``struct_embed`` field on ``detect()`` and CANNOT
change ``player_family`` (the 320 invariant). This adds an OPT-IN tie-breaker:
``detect(..., struct_tiebreak=True)``. When and ONLY when

  • the flag is on, AND
  • the rule recognizer is left with a GENUINE 2-way tie — the top two eligible
    candidates are in the SAME policy tier with scores within ``_TIEBREAK_EPS``, AND
  • NEITHER of those two is ``storage_confirmed`` (a storage tell is the strong
    signal and is never overridden), AND
  • ``struct_embed`` is present with ``confidence == "high"`` and its family is one
    of the two tied candidates,

the tie is broken toward the struct_embed pick. It can ONLY re-rank the two tied
candidates — it never invents a family the rules didn't surface, never reaches past
the top-2, and never overrides a storage tell. With the flag OFF the behaviour is
byte-identical to 320 (proven here + by the unchanged corpus/recognizer pin suites).

Fixture: ``<div class="plyr"></div><video class="video-js"></video>`` scores
videojs 0.4 / plyr 0.4 (a clean same-tier same-score tie, neither storage-confirmed);
the rule recognizer picks videojs on the stable sort. struct_embed is stubbed so the
tie-break logic is isolated from the baked-centroid model's quirks.

Sandbox: custom runner (no pytest fixtures); zero-arg tests; module globals restored
in try/finally.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
for _p in (str(_REPO), str(_REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import player_recognition as pr           # noqa: E402
import player_struct_embed as pse         # noqa: E402

# The genuine 2-way tie (videojs 0.4 / plyr 0.4), neither storage-confirmed.
_TIE_HTML = '<div class="plyr"></div><video class="video-js"></video>'


def _stub_classify(family, confidence, *, score=0.6, margin=0.2, runner_up="hlsjs"):
    def _c(*_a, **_k):
        return {"family": family, "score": score, "margin": margin,
                "confidence": confidence, "runner_up": runner_up}
    return _c


def _with_stub(stub, fn):
    """Swap player_struct_embed.classify for the duration of fn(); always restore."""
    orig = pse.classify
    pse.classify = stub
    try:
        return fn()
    finally:
        pse.classify = orig


# --- baseline: the fixture really is a tie, videojs wins on the rules ---------
def test_fixture_is_a_genuine_tie_videojs_wins():
    r = pr.detect(_TIE_HTML)
    cands = {c["family"]: c["score"] for c in r["candidates"]}
    assert cands.get("videojs") == cands.get("plyr") == 0.4, cands
    assert r["player_family"] == "videojs"
    assert "videojs" not in r["storage_confirmed"]
    assert "plyr" not in r["storage_confirmed"]


# --- flag default OFF: advisory only, no family change -----------------------
def test_tiebreak_default_off_keeps_rule_winner():
    r = _with_stub(_stub_classify("plyr", "high"),
                   lambda: pr.detect(_TIE_HTML))
    assert r["player_family"] == "videojs"   # struct says plyr but flag is OFF
    assert r["struct_embed"]["family"] == "plyr"   # advisory field still emitted


def test_tiebreak_explicit_false_keeps_rule_winner():
    r = _with_stub(_stub_classify("plyr", "high"),
                   lambda: pr.detect(_TIE_HTML, struct_tiebreak=False))
    assert r["player_family"] == "videojs"


# --- flag ON: the tie is broken toward the high-confidence struct pick --------
def test_tiebreak_on_flips_to_struct_pick():
    r = _with_stub(_stub_classify("plyr", "high"),
                   lambda: pr.detect(_TIE_HTML, struct_tiebreak=True))
    assert r["player_family"] == "plyr"
    assert any("tie" in n.lower() and "struct" in n.lower() for n in r["notes"]), r["notes"]


def test_tiebreak_on_agreement_is_a_no_op():
    # struct agrees with the rule winner -> no flip, no spurious tie note.
    r = _with_stub(_stub_classify("videojs", "high"),
                   lambda: pr.detect(_TIE_HTML, struct_tiebreak=True))
    assert r["player_family"] == "videojs"
    assert not any("tie-break" in n.lower() for n in r["notes"])


# --- guards: confidence, tied-set membership, storage, clear-winner ----------
def test_tiebreak_on_ignores_non_high_confidence():
    r = _with_stub(_stub_classify("plyr", "medium"),
                   lambda: pr.detect(_TIE_HTML, struct_tiebreak=True))
    assert r["player_family"] == "videojs"   # medium is not strong enough


def test_tiebreak_on_ignores_family_outside_the_tied_pair():
    # struct confidently names a family that is NOT one of the two tied candidates.
    r = _with_stub(_stub_classify("flowplayer", "high"),
                   lambda: pr.detect(_TIE_HTML, struct_tiebreak=True))
    assert r["player_family"] == "videojs"   # never invents a family off the tied set


def test_tiebreak_on_does_not_override_a_storage_tell():
    # videojs is storage-confirmed here -> it is the strong winner; struct->plyr
    # must NOT override it even though plyr is co-tied and the flag is on.
    r = _with_stub(
        _stub_classify("plyr", "high"),
        lambda: pr.detect(_TIE_HTML, storage_keys=["vjs-volume"],
                          struct_tiebreak=True))
    assert "videojs" in r["storage_confirmed"]
    assert r["player_family"] == "videojs"


def test_tiebreak_on_no_op_when_there_is_a_clear_winner():
    # flowplayer 0.5 / plyr 0.4 -> gap 0.1 > eps -> NOT a tie -> struct ignored.
    html = '<div class="flowplayer"></div><div class="plyr"></div>'
    r = _with_stub(_stub_classify("plyr", "high"),
                   lambda: pr.detect(html, struct_tiebreak=True))
    assert r["player_family"] == "flowplayer"


def test_tiebreak_on_tolerates_absent_struct_model():
    # classify returns None (no player structure / model absent) -> no crash, no flip.
    r = _with_stub(lambda *_a, **_k: None,
                   lambda: pr.detect(_TIE_HTML, struct_tiebreak=True))
    assert r["player_family"] == "videojs"
    assert r["struct_embed"] is None
