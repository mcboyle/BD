"""tpl95-restart-2 (harness-work/UIUX-20260928/download-95/A9-A/tpl95/FINDING-applied-template-dropped-on-restart.md, MED).

Measured on test2 .183 (vip4k, 04:00:51Z restart): sites_config.json kept applied_template = user_a9a_vip4k_..., but
after the restart GET /api/sites/<sid>/export had no applied_template and template_status read "No reviewed template".
_load_sites_config rebuilds each cfg from CFG_FIELDS; "applied_template" (written by POST /templates/apply and the
auto-detect gap fill) is not one of them and was not carried, so every restart dropped it and the next save erased it
from disk. Sibling of tpl95-restart-1 (learned).

Hermetic: the real app module, BD_HOME=tmp (conftest), a save -> in-memory wipe -> _load_sites_config round trip.
"""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

TPL_ID = "user_a9a_vip4k_o1517_1790653015"


def _boot():
    import bulk_downloader.app as bdapp
    from bulk_downloader import db
    with contextlib.suppress(Exception):
        db.db_init()
    bdapp.app.config["TESTING"] = True
    return bdapp


def _seed(bdapp, sid, **extra):
    cfg = {"name": f"site {sid}", "url": "https://tpl95-restart2.test/", **extra}
    bdapp.s_cfg[sid] = cfg
    bdapp.s_meta[sid] = bdapp._build_meta(cfg)


def _restart(bdapp):
    bdapp._save_sites_config()
    bdapp.s_cfg.clear()
    bdapp.s_meta.clear()
    bdapp.runners.clear()
    bdapp._load_sites_config()


@pytest.mark.parametrize("tid", [TPL_ID, "<auto-detect>"])
def test_applied_template_survives_a_restart_and_the_next_save(tid):
    bdapp = _boot()
    _seed(bdapp, "tplrs2a", applied_template=tid)
    _restart(bdapp)
    got = bdapp.s_cfg.get("tplrs2a", {}).get("applied_template")
    assert got == tid, f"TPL95_RESTART_APPLIED_TEMPLATE_DROPPED: {got!r}"
    bdapp._save_sites_config()
    on_disk = json.loads(bdapp.SITES_FILE.read_text(encoding="utf-8"))
    assert on_disk["tplrs2a"].get("applied_template") == tid, (
        "the save after a restart must not erase the applied_template marker")


def test_template_status_reads_the_restored_marker():
    from bulk_downloader.app_sites_teach import _applied_template_status
    bdapp = _boot()
    _seed(bdapp, "tplrs2b", applied_template=TPL_ID)
    _restart(bdapp)
    status = _applied_template_status(bdapp.s_cfg["tplrs2b"])
    assert status is not None and status["id"] == TPL_ID, status


@pytest.mark.parametrize("bad", ["", None, ["x"], {"id": TPL_ID}, 7])
def test_a_malformed_applied_template_is_not_carried(bad):
    bdapp = _boot()
    _seed(bdapp, "tplrs2c", applied_template=bad)
    _restart(bdapp)
    assert bdapp.s_cfg["tplrs2c"].get("applied_template") in (None, ""), \
        bdapp.s_cfg["tplrs2c"].get("applied_template")


def test_control_a_site_without_a_template_stays_clean_and_the_draft_override_still_survives():
    bdapp = _boot()
    _seed(bdapp, "tplrs2d", draft_test_override={"template": {"host": "x.test"}, "persist": True})
    _restart(bdapp)
    assert "applied_template" not in bdapp.s_cfg["tplrs2d"]
    assert bdapp.s_cfg["tplrs2d"]["draft_test_override"]["persist"] is True
