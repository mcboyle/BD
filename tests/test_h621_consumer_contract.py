"""H621 deep-lane consumer contract: every reader of the classifier, pinned by OUTCOME.

Written RED-first by bd-cx-worker-1 against the refuted tree (7 FAIL at base, 4 FAIL / 3 PASS on
that candidate) and adopted into the cut by bd-worker-B5-B under ORDERS-2145. Its subject is the
repository this file lives in, so it runs in CI unattended; H621_TREE still overrides that, which
is how the deep lane ran it against retained scratch checkouts side by side.

Why by consumer rather than by the classifier alone: the refuted cut changed _is_runtime_tunable
correctly and every one of its own tests passed, while _display_open, _counts, _write_baseline and
the full build kept carrying the two harness keys as DISPLAY debt -- the same manufactured debt in
a different column. A test that drives only the helper cannot see that.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path
import pytest

# CI shard scope (O906.1): this file drives tools/config_surface_inventory.py in-process on the
# repository tree, so it is a module-scope gate, not a per-site one. The marker's own name is
# BD_GATE_SCOPE -- one of the two harness-only variables this cut declassifies, read by the shard
# gate and by no product file, which is exactly the property the contract below pins.
BD_GATE_SCOPE = "module"

# The tree under test: this repository by default (so CI runs it), H621_TREE when the deep lane
# points it at a retained scratch checkout.
ROOT = Path(os.environ.get('H621_TREE') or Path(__file__).resolve().parent.parent)
spec = importlib.util.spec_from_file_location('h621_subject', ROOT / 'tools/config_surface_inventory.py')
csi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csi)
HARNESS = {'BD_GATE_SCOPE', 'BD_WHEELHOUSE_DIR'}
DEPLOY = {'BD_VAULT_KEY', 'BD_REDIS_HOST', 'BD_REDIS_PORT', 'BD_CLUSTER_RATE_MODE'}

def items():
    return [dict(key=k, kind='env_var', gui_exposure='none', risk='low', category='test', source_file='')
            for k in sorted(HARNESS | DEPLOY | {'BD_HTTP_PROXY'})]

def classified():
    return csi._apply_manifest(items(), {})

def test_apply_manifest_consumer_preserves_policy_and_unknown_control():
    d = {x['key']: x for x in classified()}
    assert all(d[k]['runtime_tunable'] is False for k in HARNESS | DEPLOY)
    assert d['BD_HTTP_PROXY']['runtime_tunable'] is True
    assert csi._is_runtime_tunable({'kind':'env_var','key':'NEW_PRODUCT_SETTING'}) is True
    for k in HARNESS | DEPLOY:
        assert csi._is_runtime_tunable({'kind':'site_key','key':k}) is True

def test_danger_consumer_preserves_deploy_and_path_meanings():
    for k in DEPLOY:
        danger, note = csi._danger_for({'key':k,'kind':'env_var'})
        assert danger and 'deploy time' in note and 'Path / storage root' not in note
    assert 'Path / storage root' in csi._danger_for({'key':'BD_ROOT','kind':'env_var'})[1]

def test_display_open_consumer_excludes_harness_and_deploy_debt():
    # H621 r4 (R1): deploy-only joined harness-only as a class with no GUI obligation, so this
    # consumer now reports NEITHER. The negative control that it still reports real debt lives in
    # tests/test_h621_r4_deploy_only_is_not_display_debt.py.
    assert set(csi._display_open(classified())) == set()
    assert set(csi._display_open(classified())) & (HARNESS | DEPLOY) == set()
    # A same-named real runtime setting is not silently removed from runtime debt.
    assert csi._open_settings([{'key':'BD_GATE_SCOPE','kind':'site_key','runtime_tunable':True,'gui_exposure':'none'}]) == ['BD_GATE_SCOPE']

def test_counts_consumer_has_no_manufactured_debt_of_either_kind():
    # Was 'four deploy items not six' before R1 ruled deploy-only out of the bucket too; the
    # six-to-four step is still pinned by the harness-only tests, this is now the whole remainder.
    d = csi._counts(classified())
    assert d['display_open'] == 0
    assert d['open_runtime_tunable'] == 1

def test_baseline_writer_consumer_does_not_serialize_harness_debt(tmp_path):
    path = tmp_path / csi._BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {'items':classified(), 'counts':csi._counts(classified())}
    assert csi._write_baseline(str(tmp_path), data) == 0
    written = json.loads(path.read_text())
    assert written['display_open'] == []            # R1: deploy-only is not serialized as debt
    assert written['open'] == ['BD_HTTP_PROXY']

def test_editor_source_consumer_preserves_exact_host_exclusions():
    source = ast.parse((ROOT/'tests/test_v3_66_504_envfile_editor.py').read_text())
    host = next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='_HOST_MANAGED' for t in n.targets))
    assert DEPLOY <= host
    assert 'BD_PORT' not in host

# The kinds the GUI manifest governs. Site keys carry their own per-site exposure and are never
# listed in reports/config_gui_manifest.json, so including them would make the derivation below
# report two settings (kafka_event_streaming_enabled, use_audio_normalization) that ARE rendered.
# MEASURED, not assumed: both are absent from `exposed` and gui_exposure='full' on this tree.
_MANIFEST_KINDS = ('env_var', 'global_config')


def _manifest_runtime_keys_without_a_rendered_control(d):
    """The OPEN set derived from the manifest FILE rather than from the classified items.

    R2 (RULING-h621-display-and-972-973-E1): the old form hardcoded
    {'BD_HTTP_PROXY','turnstile_one_click_enabled'}, which is the remainder on h621's OWN tree.
    rows972-973 renders controls for exactly those two, so the pair went stale the moment the two
    cuts were measured on one tree -- the test was right about h621 and wrong about the merge.
    Reading reports/config_gui_manifest.json directly keeps this a real check and not a restatement
    of _open_settings: it crosses _apply_manifest, so a consumer that drops or rewrites a manifest
    entry on its way into the items still shows up as a disagreement. A key the manifest does not
    mention at all has no rendered control either, which is why `.get(...) != 'full'` and not
    `in exposed` -- BD_HTTP_PROXY is exactly that case on h621's tree.
    """
    exposed = json.loads((ROOT / 'reports/config_gui_manifest.json').read_text())['exposed']
    return {it['key'] for it in d['items']
            if it.get('runtime_tunable') and it['kind'] in _MANIFEST_KINDS
            and exposed.get(it['key']) != 'full'}


def test_full_inventory_consumer_names_both_remaining_debt_sets():
    d = csi.build(str(ROOT))
    assert set(csi._open_settings(d['items'])) == _manifest_runtime_keys_without_a_rendered_control(d)
    # H621 r4 (R1): deploy-only is a class with no GUI obligation, so it is display debt no more.
    assert set(csi._display_open(d['items'])) & DEPLOY == set()
    assert d['counts']['display_open'] == 0


def test_the_manifest_derivation_can_name_an_uncontrolled_key():
    """Control for the assertion above: the derivation is not vacuously empty. Feed it a runtime
    key the manifest does not expose as full and it must come back -- otherwise 'the two sets
    agree' would be satisfied by any tree at all, including one with every control missing.
    (On h621's own tree the real remainder is 2: BD_HTTP_PROXY and turnstile_one_click_enabled.)"""
    exposed = json.loads((ROOT / 'reports/config_gui_manifest.json').read_text())['exposed']
    d = {'items': [dict(key='BD_NOT_RENDERED_ANYWHERE', kind='env_var', runtime_tunable=True),
                   dict(key='BD_AUTH_TOKEN', kind='env_var', runtime_tunable=True)]}
    assert exposed.get('BD_AUTH_TOKEN') == 'full'          # the tree really does render this one
    assert _manifest_runtime_keys_without_a_rendered_control(d) == {'BD_NOT_RENDERED_ANYWHERE'}
    # and it is non-empty on h621's own tree: the real remainder is the O1186 pair, 2 of them.
    real = _manifest_runtime_keys_without_a_rendered_control(csi.build(str(ROOT)))
    assert len(real) in (0, 2), sorted(real)
    if real:
        assert real == {'BD_HTTP_PROXY', 'turnstile_one_click_enabled'}, sorted(real)
