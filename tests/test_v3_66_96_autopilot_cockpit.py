"""v3.66.96 — operator_layer autopilot cockpit.

The autopilot subcommand takes a folder/list of captures, discovers them, runs
the real analysis chain itself (temporal series + optional perturbation), scans
posture, and writes one cockpit. It must: detect captures, compute distinct
renditions from VIDEO media only (not thumbnails), reflect the temporal floor,
and NEVER write the corpus or retire debt. Recognition-only.
"""
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import operator_layer as ol


def _mk_capture(path, identity, rendition):
    """Minimal capture json the loader accepts. URLs are query-stripped exactly
    as the real capture pipeline redacts them — no raw signing values, because a
    real .wacz never carries them past load_capture."""
    cap = {
        "host": "example.com",
        "cookies": [{"name": "sess", "value": "x"}],
        "network_log": [
            {"url": f"https://example.com/members/content/item/{identity}-clip"},
            {"url": f"https://cdn.example.com/{rendition}"},
            {"url": "https://cdn.example.com/poster_3840x2160.jpg"},  # thumbnail trap
        ],
    }
    path.write_text(json.dumps(cap), encoding="utf-8")


class TestAutopilotDiscoversAndRuns:
    def _run(self, tmp_path, axis=None):
        caps = tmp_path / "caps"
        caps.mkdir()
        _mk_capture(caps / "q4k.json", "abcd1234", "3840x2160_60.mp4")
        _mk_capture(caps / "q1080.json", "abcd1234", "1920x1080_60.mp4")
        _mk_capture(caps / "q720.json", "abcd1234", "1280x720_60.mp4")
        out = tmp_path / "cockpit"

        class A:
            captures = [str(caps)]
            out_dir = str(out)
        A.axis = axis
        rc = ol.cmd_autopilot(A)
        return rc, out

    def test_discovers_all_three(self, tmp_path):
        rc, out = self._run(tmp_path)
        assert rc == 0
        ck = json.loads((out / "autopilot_cockpit.json").read_text())
        assert ck["captures_analyzed"] == 3

    def test_distinct_renditions_excludes_thumbnails(self, tmp_path):
        # three video renditions; the 3840x2160.jpg poster must NOT count
        rc, out = self._run(tmp_path)
        ck = json.loads((out / "autopilot_cockpit.json").read_text())
        assert len(ck["distinct_top_renditions"]) == 3
        assert not any(r.endswith(".jpg") for r in ck["distinct_top_renditions"])

    def test_floor_confirmed_on_three_distinct(self, tmp_path):
        rc, out = self._run(tmp_path)
        ck = json.loads((out / "autopilot_cockpit.json").read_text())
        floor = ck["temporal_floor"]
        assert floor is not None
        assert floor["outcome"] == "confirmed"
        assert floor["n_sessions"] == 3

    def test_writes_human_cockpit(self, tmp_path):
        rc, out = self._run(tmp_path)
        md = (out / "capture_cockpit.md").read_text()
        assert "Capture cockpit" in md
        assert "Temporal floor" in md

    def test_perturbation_runs_with_axis(self, tmp_path):
        rc, out = self._run(tmp_path, axis="player_config")
        ck = json.loads((out / "autopilot_cockpit.json").read_text())
        assert ck["perturbation_axis"] == "player_config"

    def test_never_writes_corpus_or_retires_debt(self, tmp_path):
        # the cockpit reflects debt but the run must not mutate it
        from bulk_downloader import validation_corpus as vc
        before = len(vc.load_corpus())
        rc, out = self._run(tmp_path, axis="player_config")
        after = len(vc.load_corpus())
        assert before == after, "autopilot must not write the corpus"
        ck = json.loads((out / "autopilot_cockpit.json").read_text())
        assert "Recognition-only" in ck["_status"]

    def test_empty_dir_returns_nonzero(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()

        class A:
            captures = [str(empty)]
            out_dir = str(tmp_path / "o")
            axis = None
        assert ol.cmd_autopilot(A) == 2


class TestPosture:
    def test_no_signing_value_in_cockpit(self, tmp_path):
        from bulk_downloader.capture_ingest import posture_scan
        caps = tmp_path / "caps"
        caps.mkdir()
        _mk_capture(caps / "a.json", "id1", "1920x1080.mp4")
        _mk_capture(caps / "b.json", "id1", "1280x720.mp4")

        class A:
            captures = [str(caps)]
            out_dir = str(tmp_path / "ck")
            axis = "player_config"
        ol.cmd_autopilot(A)
        for f in (tmp_path / "ck").glob("*"):
            assert not posture_scan(f.read_text(encoding="utf-8")), f
