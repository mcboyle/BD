"""H621 r4 / RULING-h621-display-and-972-973-E1 R1: deploy-only is not display debt either.

O1186 classed four env vars as DEPLOY-ONLY -- deployment topology and a secret. Taking them out
of runtime_tunable moved them out of the OPEN bucket and straight into the DISPLAY-OPEN one, so
tests/test_v3_66_312_parity_abc.py and tests/test_v3_66_319_env_tranche_4_3d.py, which compare
display_open to a literal 0, went red at `4 == 0`. Measured on the h621+972-973 combined tree
(PLAN-2040/H621-PLUS-972-973-MEASURE.md) the four survive every GUI control rows972-973 adds,
because a vault key and Redis host/port are deliberately never surfaced.

The PM's ruling is that deploy-only is a class with NO GUI obligation at all: gui_exposure='none'
and runtime_tunable=False by policy, so such a key belongs in NEITHER debt bucket -- the same
disposition _NEVER_EXPOSE and _HARNESS_ONLY already have in _display_open. That is O1186
implemented, not an assertion weakened: 312 and 319 keep their literal 0 and go green because the
inventory stops miscounting.

The risk in a class-wide exclusion is that it swallows real debt, so the negative control below
pins a non-deploy-only undisplayed setting still being counted.
"""
import importlib.util
import os
from pathlib import Path

BD_GATE_SCOPE = "module"

ROOT = Path(os.environ.get('H621_TREE') or Path(__file__).resolve().parent.parent)
spec = importlib.util.spec_from_file_location('h621_r4_subject', ROOT / 'tools/config_surface_inventory.py')
csi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csi)

O1186 = {'BD_VAULT_KEY', 'BD_REDIS_HOST', 'BD_REDIS_PORT', 'BD_CLUSTER_RATE_MODE'}


def _item(key, **over):
    it = dict(key=key, kind='env_var', gui_exposure='none', risk='low',
              category='test', source_file='', runtime_tunable=False)
    it.update(over)
    return it


def test_the_probe_can_say_yes_deploy_only_is_the_class_under_test():
    """Positive control beside the zeros below: the four ARE in _DEPLOY_ONLY, so a probe that
    reports 'not in a debt bucket' is reporting the exclusion, not an empty inventory."""
    assert O1186 <= csi._DEPLOY_ONLY
    assert 'BD_AUTH_TOKEN' not in csi._DEPLOY_ONLY


def test_deploy_only_key_is_in_neither_open_bucket():
    items = [_item(k) for k in sorted(O1186)]
    assert csi._open_settings(items) == []
    assert csi._display_open(items) == []


def test_real_tree_counts_no_deploy_only_display_debt():
    d = csi.build(str(ROOT))
    assert set(csi._display_open(d['items'])) & O1186 == set()
    assert d['counts']['display_open'] == 0


def test_negative_control_non_deploy_undisplayed_setting_still_counts():
    """The exclusion must not swallow real debt: a key that is NOT deploy-only, NOT harness-only
    and NOT a decided exclusion, with no GUI exposure at all, is still display debt."""
    real = 'BD_A_SETTING_WITH_NO_CONTROL_YET'
    assert real not in csi._DEPLOY_ONLY and real not in csi._HARNESS_ONLY
    assert real not in csi._NEVER_EXPOSE
    assert csi._display_open([_item(real)]) == [real]
    assert csi._display_open([_item(real), _item('BD_VAULT_KEY')]) == [real]


def test_display_only_exposure_is_still_not_counted_as_pending():
    assert csi._display_open([_item('BD_A_SETTING_WITH_NO_CONTROL_YET',
                                    gui_exposure='display-only')]) == []
