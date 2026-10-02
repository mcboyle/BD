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


# xnxx is its own positive case below: since fx-templates-vm-seed (04d38dd31) seeded
# user_b1b_xnxx_o1517 as a built-in, xnxx.com matches TWO templates, and by operator
# ruling O1638 the seeded site-specific one wins that tie over the wgcz_tubes family.
@pytest.mark.parametrize("sid,url", [("xv2b_xv", "https://www.xvideos.com/")])
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


XNXX_SEEDED = "user_b1b_xnxx_o1517_1790650872"


def test_xnxx_gets_the_seeded_site_template_over_the_wgcz_family_on_load():
    """O1638: xnxx.com matches wgcz_tubes (family) and the seeded xnxx built-in; the seeded one wins."""
    bdapp = _boot()
    import bulk_downloader.site_templates as st
    assert set(st.suggest_for_url("https://www.xnxx.com/")) >= {"wgcz_tubes", XNXX_SEEDED}
    _seed(bdapp, "xv2b_xn", "https://www.xnxx.com/")
    _restart(bdapp)
    cfg = bdapp.s_cfg["xv2b_xn"]
    assert cfg.get("applied_template") == XNXX_SEEDED, (
        f"XV2B_XNXX_SEEDED_NOT_APPLIED: applied_template={cfg.get('applied_template')!r}")
    rows = ((cfg.get("learned") or {}).get("download") or {}).get("row_selectors") or []
    assert "a[href*='xnxx-cdn.com'][href*='.mp4']" in rows, rows
    assert "a[href*='.mp4']" not in rows, rows  # wgcz_tubes' rows were not merged too
    assert bdapp.runners["xv2b_xn"].config.get("applied_template") == XNXX_SEEDED
    bdapp._save_sites_config()
    on_disk = json.loads(bdapp.SITES_FILE.read_text(encoding="utf-8"))
    assert on_disk["xv2b_xn"].get("applied_template") == XNXX_SEEDED


def _seeded_ids():
    import bulk_downloader.site_templates as st
    return sorted(st.SEEDED_BUILTIN_IDS)


def test_tie_seeded_vs_family_picks_the_seeded_template():
    from bulk_downloader.site_templates import unambiguous_template
    assert unambiguous_template(["wgcz_tubes", XNXX_SEEDED]) == XNXX_SEEDED
    assert unambiguous_template([XNXX_SEEDED, "wgcz_tubes"]) == XNXX_SEEDED


def test_tie_family_vs_family_picks_nothing():
    import bulk_downloader.site_templates as st
    tids = st.suggest_for_url("https://spankbang.com/")
    assert len(tids) >= 2 and not set(tids) & st.SEEDED_BUILTIN_IDS, tids
    assert st.unambiguous_template(tids) is None
    assert st.unambiguous_template(["wgcz_tubes", "spankbang"]) is None


def test_tie_two_seeded_templates_picks_nothing():
    from bulk_downloader.site_templates import unambiguous_template
    a, b = _seeded_ids()[:2]
    assert unambiguous_template([a, b]) is None
    assert unambiguous_template([a, b, "wgcz_tubes"]) is None


def test_tie_seeded_vs_a_site_specific_builtin_picks_nothing():
    """O1638 covers seeded vs FAMILY only: naughtyamerica.com matches the hand-written
    single-site naughtyamerica built-in and a seeded one -- still a tie, still nothing."""
    import bulk_downloader.site_templates as st
    tids = st.suggest_for_url("https://www.naughtyamerica.com/")
    assert "naughtyamerica" in tids and len(set(tids) & st.SEEDED_BUILTIN_IDS) == 1, tids
    assert st.unambiguous_template(tids) is None


def test_tie_seeded_vs_a_user_only_template_picks_nothing():
    from bulk_downloader.site_templates import unambiguous_template
    assert unambiguous_template([XNXX_SEEDED, "user_not_a_builtin_o1638"]) is None


def test_single_and_empty_matches_are_unchanged():
    from bulk_downloader.site_templates import unambiguous_template
    assert unambiguous_template(["wgcz_tubes"]) == "wgcz_tubes"
    assert unambiguous_template([XNXX_SEEDED]) == XNXX_SEEDED
    assert unambiguous_template([]) is None


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
