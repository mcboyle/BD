"""dl95-xvideos-2b (QUESTION-XV2-WGCZ-SPLIT-bd-worker-B1-B.md, MED): the built-in "WGCZ tubes (XVideos / XNXX)"
template was never applied to the xvideos site on test2.

Measured on test2 .95 (sites_config.json, non-secret fields): site ecdb57cd login_url www.xvideos.com,
applied_template None, no dl_selector/trigger_selector, learned.download empty -- while
site_templates.suggest_for_url("https://www.xvideos.com/") == ["wgcz_tubes"]. 24 of 62 sites are in the same
state. The only automatic apply is _auto_pick_templates, which runs inside _create_site; a site that already
exists in sites_config.json (created before the template or by an older build) never gets one, and
_load_sites_config had no gap fill for the download template (it has one for login_trigger).

Fix under test: _load_sites_config gap-fills the site's download template from an UNAMBIGUOUS built-in match
when the site has no download selectors and no applied template. Ambiguous hosts are left to the operator
(measured: spankbang.com matches bang_originals before spankbang because r"bang\\.com" is unanchored).

Hermetic: the real app module, BD_HOME=tmp (conftest), save -> in-memory wipe -> _load_sites_config.
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


def _boot():
    import bulk_downloader.app as bdapp
    from bulk_downloader import db
    with contextlib.suppress(Exception):
        db.db_init()
    bdapp.app.config["TESTING"] = True
    return bdapp


def _seed(bdapp, sid, login_url, **extra):
    cfg = {"name": f"site {sid}", "login_url": login_url, **extra}
    bdapp.s_cfg[sid] = cfg
    bdapp.s_meta[sid] = bdapp._build_meta(cfg)


def _restart(bdapp):
    bdapp._save_sites_config()
    bdapp.s_cfg.clear()
    bdapp.s_meta.clear()
    bdapp.runners.clear()
    bdapp._load_sites_config()


@pytest.mark.parametrize("sid,url", [("xv2b_xv", "https://www.xvideos.com/"),
                                     ("xv2b_xn", "https://www.xnxx.com/")])
def test_an_existing_wgcz_site_gets_the_builtin_template_on_load(sid, url):
    bdapp = _boot()
    _seed(bdapp, sid, url)
    _restart(bdapp)
    cfg = bdapp.s_cfg[sid]
    assert cfg.get("applied_template") == "wgcz_tubes", (
        f"XV2B_WGCZ_NOT_APPLIED: applied_template={cfg.get('applied_template')!r} "
        f"learned={cfg.get('learned')!r}")
    rows = ((cfg.get("learned") or {}).get("download") or {}).get("row_selectors") or []
    assert "a[href*='.mp4']" in rows, rows
    assert bdapp.runners[sid].config.get("applied_template") == "wgcz_tubes"
    bdapp._save_sites_config()
    on_disk = json.loads(bdapp.SITES_FILE.read_text(encoding="utf-8"))
    assert on_disk[sid].get("applied_template") == "wgcz_tubes"


def test_an_explicit_operator_value_is_not_overwritten_by_the_template_defaults():
    bdapp = _boot()
    _seed(bdapp, "xv2b_pp", "https://www.xvideos.com/", use_persistent_profile=False,
          min_resolution=720)
    _restart(bdapp)
    cfg = bdapp.s_cfg["xv2b_pp"]
    assert cfg.get("applied_template") == "wgcz_tubes"
    assert cfg.get("use_persistent_profile") is False
    assert cfg.get("min_resolution") == 720


@pytest.mark.parametrize("extra", [
    {"applied_template": "user_b1b_xnxx_o1517_1790650872"},
    {"dl_selector": "a.dl"},
    {"trigger_selector": "button.dl"},
    {"learned": {"download": {"row_selectors": ["a.mine"]}}},
])
def test_control_a_site_that_already_has_download_config_is_untouched(extra):
    bdapp = _boot()
    _seed(bdapp, "xv2b_own", "https://www.xnxx.com/", **extra)
    _restart(bdapp)
    cfg = bdapp.s_cfg["xv2b_own"]
    assert cfg.get("applied_template") == extra.get("applied_template"), cfg.get("applied_template")
    rows = ((cfg.get("learned") or {}).get("download") or {}).get("row_selectors") or []
    assert "a[href*='.mp4']" not in rows, rows


@pytest.mark.parametrize("url", ["https://tpl-xv2b-nomatch.test/", "https://spankbang.com/"])
def test_control_no_match_or_an_ambiguous_match_applies_nothing(url):
    bdapp = _boot()
    _seed(bdapp, "xv2b_none", url)
    _restart(bdapp)
    cfg = bdapp.s_cfg["xv2b_none"]
    assert "applied_template" not in cfg, cfg.get("applied_template")
    assert not ((cfg.get("learned") or {}).get("download") or {}).get("row_selectors")
