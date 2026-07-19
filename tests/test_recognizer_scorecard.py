"""recognizer_scorecard (A2) — characterization tests.

Pins the scorecard's ACCOUNTING — per-family precision/recall/F1, the confusion
matrix, overall accuracy, aux (delivery/drm/ad) scoring, corpus loading (file +
directory), the thin-corpus note, and the POSTURE guarantee that entry `inputs`
(html / script srcs / urls / storage values) never reach the scorecard output.

The unit under test is the SCORECARD, not the recognizer: ground-truth labels in
the fixtures are set to what `player_recognition.detect` actually returns for the
synthetic markup (probed, not guessed), plus one deliberate mislabel so the
FP/FN/confusion math is exercised. Synthetic fixtures only — never a real capture.
"""
import json
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import recognizer_scorecard as RS


# ── synthetic corpus (markup verified against detect()'s real output) ────────

def _corpus():
    return [
        {"id": "vjs", "expected_family": "videojs",
         "expected_delivery": "hls", "expected_drm": False, "expected_ad": False,
         "inputs": {"html": '<div class="video-js vjs-control-bar" data-vjs-player></div>',
                    "script_srcs": ["https://cdn.example/video.js"]}},
        {"id": "theo", "expected_family": "theoplayer",
         "inputs": {"html": '<div class="theoplayer-skin theo-primary-color"></div>',
                    "script_srcs": ["https://cdn.example/THEOplayer.js"],
                    "storage_keys": ["THEOplayer.cache"]}},
        {"id": "jw", "expected_family": "jwplayer",
         "inputs": {"html": '<div id="jwplayer-1" class="jw-reset jw-icon"></div>',
                    "script_srcs": ["https://cdn.example/jwplayer.js"]}},
        {"id": "vimeo", "expected_family": "vimeo",
         "inputs": {"html": '<iframe src="https://player.vimeo.com/video/123"></iframe>',
                    "iframe_hosts": ["player.vimeo.com"]}},
        {"id": "native", "expected_family": "native_custom",
         "inputs": {"html": '<video src="movie.mp4" controls></video>'}},
        {"id": "drm", "expected_family": "videojs", "expected_drm": True,
         "inputs": {"html": '<div class="video-js"></div>',
                    "script_srcs": ["https://cdn.example/video.js",
                                    "https://widevine.example/license"]}},
        # deliberate mislabel: videojs markup labelled jwplayer -> drives FP/FN.
        {"id": "mislabel", "expected_family": "jwplayer",
         "inputs": {"html": '<div class="video-js vjs-control-bar" data-vjs-player></div>',
                    "script_srcs": ["https://cdn.example/video.js"]}},
    ]


def test_score_entry_correct_and_aux():
    rec = RS.score_entry(_corpus()[0])
    assert rec["predicted"] == "videojs"
    assert rec["correct"] is True
    assert rec["delivery_correct"] is True
    assert rec["drm_correct"] is True and rec["ad_correct"] is True


def test_overall_accuracy_and_unknown_count():
    sc = RS.score_corpus(_corpus())
    assert sc["n"] == 7
    assert sc["correct"] == 6              # only the deliberate mislabel is wrong
    assert sc["accuracy"] == round(6 / 7, 3)
    assert sc["unknown_predicted"] == 0


def test_per_family_precision_recall_from_mislabel():
    pf = RS.score_corpus(_corpus())["per_family"]
    # videojs: TP={vjs,drm}=2, FP={mislabel}=1, FN=0  -> P=2/3, R=1.0
    assert pf["videojs"]["tp"] == 2 and pf["videojs"]["fp"] == 1 and pf["videojs"]["fn"] == 0
    assert pf["videojs"]["precision"] == round(2 / 3, 3)
    assert pf["videojs"]["recall"] == 1.0
    # jwplayer: TP={jw}=1, FP=0, FN={mislabel}=1 -> P=1.0, R=0.5
    assert pf["jwplayer"]["precision"] == 1.0
    assert pf["jwplayer"]["recall"] == 0.5
    assert pf["jwplayer"]["support"] == 2


def test_confusion_records_the_mislabel():
    conf = RS.score_corpus(_corpus())["confusion"]
    # expected jwplayer was predicted videojs once
    assert conf["jwplayer"]["videojs"] == 1


def test_aux_accuracy_scored_only_where_labelled():
    aux = RS.score_corpus(_corpus())["aux"]
    assert aux["drm"]["scored"] == 2 and aux["drm"]["accuracy"] == 1.0
    assert aux["delivery"]["scored"] == 1


# ── corpus loading: single file, directory, bare list, missing ───────────────

def test_load_corpus_file_and_dir():
    with tempfile.TemporaryDirectory() as td:
        # single file with {"entries":[...]}
        f = Path(td) / "corpus.json"
        f.write_text(json.dumps({"entries": _corpus()[:2]}))
        assert len(RS.load_corpus(str(f))) == 2
        # directory of per-entry files
        d = Path(td) / "dir"
        d.mkdir()
        for e in _corpus()[:3]:
            (d / f"{e['id']}.json").write_text(json.dumps(e))
        assert len(RS.load_corpus(str(d))) == 3
        # bare list file
        bl = Path(td) / "list.json"
        bl.write_text(json.dumps(_corpus()[:2]))
        assert len(RS.load_corpus(str(bl))) == 2


def test_missing_corpus_is_empty_with_note():
    sc = RS.report("/no/such/corpus/path.json")
    assert sc["n"] == 0
    assert "measurement pending" in sc["note"]
    assert sc["registered_families"] >= 36   # brand pack registered


def test_thin_corpus_note():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "c.json"
        f.write_text(json.dumps(_corpus()[:2]))
        sc = RS.report(str(f))
    assert "candidate-grade only" in sc["note"]


# ── POSTURE: entry inputs never reach the scorecard output ───────────────────

def test_scorecard_output_carries_no_input_values():
    sc = RS.score_corpus(_corpus())
    blob = json.dumps(sc)
    # none of the markup / script srcs / iframe hosts / storage keys leak through
    for needle in ("video-js", "THEOplayer.cache", "player.vimeo.com",
                   "widevine.example", "movie.mp4", "jw-reset", "<div", "<iframe"):
        assert needle not in blob, f"input value leaked into scorecard output: {needle}"


def test_render_markdown_is_label_only():
    md = RS.render_markdown(RS.score_corpus(_corpus()))
    assert "Recognizer scorecard" in md
    assert "videojs" in md and "jwplayer" in md   # labels are fine
    for needle in ("video-js", "THEOplayer.cache", "movie.mp4", "<div"):
        assert needle not in md


# ── v3.66.261: no-tell weak-markup skin pinned in the scorecard ──────────────

def _no_tell_markup_skin_entry():
    # The newsensations over-call shape, as SYNTHETIC markup (no real values): a
    # dense video.js SKIN (video-js + vjs- control bar) PLUS a stacked jwplayer
    # class, on a native progressive <video>, with NO lib script / storage / data-
    # attr tell. v3.66.261 demotes this to native_custom (a leftover skin is not
    # the active engine). Probed against detect(), not guessed.
    return {"id": "no_tell_markup_skin", "expected_family": "native_custom",
            "inputs": {"html":
                       '<div class="video-js vjs-default-skin">'
                       '<video src="/media/clip.mp4" type="video/mp4"></video>'
                       '<button class="vjs-big-play-button"></button>'
                       '<div class="vjs-control-bar"><div class="vjs-play-control"></div></div>'
                       '<div class="jwplayer jw-reset"><div class="jw-icon-settings"></div></div>'
                       '</div>'}}


def test_no_tell_markup_skin_scored_native_custom():
    # Pinned so a recognizer change that reintroduces the videojs/jwplayer over-call
    # on a no-tell native skin is a committed-test failure, not a manual corpus walk.
    rec = RS.score_entry(_no_tell_markup_skin_entry())
    assert rec["predicted"] == "native_custom", rec["predicted"]
    assert rec["correct"] is True
    # the demoted brand markup must not leak into the scorecard record
    blob = json.dumps(rec)
    for needle in ("video-js", "vjs-", "jwplayer", "<div", "clip.mp4"):
        assert needle not in blob, f"input value leaked into scorecard record: {needle}"


def test_no_tell_markup_skin_scores_correct_in_corpus():
    # and it counts as a correct prediction in the corpus-level accounting
    sc = RS.score_corpus(_corpus() + [_no_tell_markup_skin_entry()])
    assert sc["n"] == 8
    assert sc["correct"] == 7              # the one deliberate mislabel stays wrong
    assert sc["unknown_predicted"] == 0    # native_custom is a real prediction, not unknown
