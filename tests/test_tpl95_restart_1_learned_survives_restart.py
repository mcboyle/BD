"""tpl95-restart-1 (harness-work/UIUX-20260928/B4-B/tpl95/FINDING-learned-dropped-on-restart.md, HIGH).

Measured on test2 (build 3.66.1711): after the 02:31Z restart, 1 of 62 sites carried a learned block in sites_config.json
-- the one re-applied minutes before. Templates applied through the app at 00:2xZ (youjizz, xhamster, whoreshub, ...)
were gone, and the next youjizz run used none of its template rows. _load_sites_config rebuilds each cfg from
CFG_FIELDS; "learned" is not one of them and, unlike draft_test_override, was not carried, so every restart dropped it
and the next save erased it from disk. The url_attribute heal in the loader read cfg["learned"] and so never ran.

Hermetic: the real app module, BD_HOME=tmp (conftest), a save -> in-memory wipe -> _load_sites_config round trip.
"""
from __future__ import annotations

import contextlib
import copy
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

# the youjizz template's learned block, as POST /api/sites/<sid>/templates/apply merged it on test2
LEARNED = {"download": {"row_selectors": ["video#yj-fluid source[type='application/x-mpegURL']",
                                         "video source[src*='.m3u8']"],
                        "url_attribute": "src"},
           "login": {"submit_btn": ["button.btn-red"]}}


def _boot():
    import bulk_downloader.app as bdapp
    from bulk_downloader import db
    with contextlib.suppress(Exception):   # as tests/test_wave_b2_test_extract.py: a DB-less box still loads configs
        db.db_init()
    bdapp.app.config["TESTING"] = True
    return bdapp


def _seed(bdapp, sid, **extra):
    cfg = {"name": f"site {sid}", "url": "https://tpl95-restart.test/", **extra}
    bdapp.s_cfg[sid] = cfg
    bdapp.s_meta[sid] = bdapp._build_meta(cfg)


def _restart(bdapp):
    bdapp._save_sites_config()
    bdapp.s_cfg.clear()
    bdapp.s_meta.clear()
    bdapp.runners.clear()
    bdapp._load_sites_config()


def test_learned_selectors_survive_a_restart_and_the_next_save():
    bdapp = _boot()
    _seed(bdapp, "tplrs1", learned=copy.deepcopy(LEARNED))
    _restart(bdapp)
    got = bdapp.s_cfg.get("tplrs1", {}).get("learned")
    assert got == LEARNED, f"TPL95_RESTART_LEARNED_DROPPED: {got!r}"
    bdapp._save_sites_config()
    on_disk = json.loads(bdapp.SITES_FILE.read_text(encoding="utf-8"))
    assert on_disk["tplrs1"].get("learned") == LEARNED, "the save after a restart must not erase the learned block"


def test_the_url_attribute_heal_runs_on_the_restored_block():
    """v3.43.16's loader heal reads cfg['learned']; with learned dropped it never ran. Now it does."""
    bdapp = _boot()
    misaligned = {"download": {"row_selectors": ["a.new", "a.old"], "url_attribute": ["href"]}}
    _seed(bdapp, "tplrs2", learned=misaligned)
    _restart(bdapp)
    dl = bdapp.s_cfg["tplrs2"]["learned"]["download"]
    assert dl["url_attribute"] == ["", "href"], dl


@pytest.mark.parametrize("bad", ["", None, ["download"], "learned"])
def test_a_malformed_learned_value_is_not_carried(bad):
    bdapp = _boot()
    _seed(bdapp, "tplrs3", learned=bad)
    _restart(bdapp)
    assert "learned" not in bdapp.s_cfg["tplrs3"] or bdapp.s_cfg["tplrs3"]["learned"] in ("", None), \
        bdapp.s_cfg["tplrs3"].get("learned")


def test_control_a_site_without_learned_stays_clean_and_the_draft_override_still_survives():
    bdapp = _boot()
    _seed(bdapp, "tplrs4", draft_test_override={"template": {"host": "x.test"}, "persist": True})
    _restart(bdapp)
    assert "learned" not in bdapp.s_cfg["tplrs4"]
    assert bdapp.s_cfg["tplrs4"]["draft_test_override"]["persist"] is True
