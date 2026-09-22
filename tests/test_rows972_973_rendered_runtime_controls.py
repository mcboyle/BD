"""D1 RED contract. Run from the checkout, with ROWS_TREE set to its absolute path.
Chosen interface: labelled HTML forms on /cockpit/settings POST fields to
/api/settings/runtime. Both settings are runtime-tunable; metadata is insufficient.
"""
# Rows 972/973 acceptance. Wired into the gate-suites "artifacts-pins" shard in
# .github/workflows/ci.yml beside test_settings_center_slice5.py, so this contract
# cannot become one of the orphan acceptance tests H622 counted.
BD_GATE_SCOPE = "repo-wide"

import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest
from flask import Flask

# ROWS_TREE selects the checkout under test for the scratch replay (base vs candidate).
# In CI there is no such variable and nothing may supply one, so the in-repo copy
# defaults to its own checkout rather than failing collection with a KeyError.
ROOT = Path(os.environ.get('ROWS_TREE') or Path(__file__).resolve().parent.parent).resolve()
sys.path.insert(0, str(ROOT))
from bulk_downloader import app_settings_center as sc, global_config as GC, http_client

KEYS = ('BD_HTTP_PROXY', 'turnstile_one_click_enabled')
WRITE = '/api/settings/runtime'

class Surface(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.controls = []
        self.labels = {}
        self.forms = []
        self.form = None
        self.label = None
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'form':
            self.form = a
            self.forms.append(a)
        if tag == 'label':
            self.label = a.get('for')
            if self.label:
                self.labels.setdefault(self.label, '')
        if tag in ('input', 'select', 'button'):
            c = dict(a, tag=tag, form=self.form, options=[])
            self.controls.append(c)
            if tag == 'select':
                self.select = c
        if tag == 'option' and self.select is not None:
            self.select['options'].append(a)

    def handle_endtag(self, tag):
        if tag == 'label': self.label = None
        if tag == 'form': self.form = None
        if tag == 'select': self.select = None

    def handle_data(self, data):
        if self.label: self.labels[self.label] += data

    def control(self, key):
        matches = [c for c in self.controls if c.get('name') == key and c.get('type') != 'hidden']
        assert len(matches) == 1, f'{key}: expected one rendered control, found {len(matches)}'
        c = matches[0]
        assert 'disabled' not in c and 'readonly' not in c, f'{key}: runtime full requires an operable control'
        label = self.labels.get(c.get('id'), '') or c.get('aria-label', '')
        assert label.strip(), f'{key}: missing associated visible or accessible label'
        assert c['form'] and c['form'].get('action') == WRITE, f'{key}: no actual form write target'
        assert c['form'].get('method', 'get').lower() == 'post'
        submit = [x for x in self.controls if x['form'] is c['form'] and
                  ((x['tag'] == 'button' and x.get('type', 'submit') == 'submit') or x.get('type') == 'submit')]
        assert submit, f'{key}: no rendered submit control'
        return c

    def boolean(self, key):
        c = self.control(key)
        if c['tag'] == 'input':
            assert c.get('type') == 'checkbox'
            return 'checked' in c
        assert c['tag'] == 'select'
        selected = [o for o in c['options'] if 'selected' in o]
        assert len(selected) == 1, 'boolean select must explicitly show current state'
        assert selected[0].get('value') in ('true', 'false')
        return selected[0]['value'] == 'true'

@pytest.fixture
def client(tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        m.setattr(GC, '_CONFIG_FILE', tmp_path / 'app_config.json')
        m.setattr(GC, '_cached', None)
        m.setattr(GC, '_cached_mtime', 0.0)
        # Establish a real temporary store; writes must preserve unrelated data.
        assert GC.set_config({'turnstile_one_click_enabled': False, 'quiet_hours': 'untouched'})
        m.setenv('BD_HTTP_PROXY', 'http://baseline.invalid:8080')
        app = Flask(__name__)
        app.config['TESTING'] = True
        sc.register_routes(app)
        with app.test_client() as c:
            c.environment = m
            yield c


def page(client):
    r = client.get('/cockpit/settings')
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_surface_probe_positive_and_negative_controls():
    good = '<form action="/api/settings/runtime" method="post"><label for="p">HTTP proxy</label><input id="p" name="BD_HTTP_PROXY" value="http://host:80"><button>Save</button></form>'
    assert Surface(good).control('BD_HTTP_PROXY')['value'] == 'http://host:80'
    with pytest.raises(AssertionError, match='expected one rendered control'):
        Surface('<p>BD_HTTP_PROXY</p>').control('BD_HTTP_PROXY')

@pytest.mark.parametrize('key', KEYS)
def test_manifest_full_is_coupled_to_rendered_labelled_control(client, key):
    manifest = json.loads((ROOT / 'reports/config_gui_manifest.json').read_text())
    assert manifest['exposed'].get(key) == 'full', f'{key}: missing promised full exposure'
    Surface(page(client)).control(key)

@pytest.mark.parametrize('enabled', [False, True])
def test_toggle_effective_state_is_visible(client, enabled):
    assert GC.set_config({'turnstile_one_click_enabled': enabled})
    assert GC.get('turnstile_one_click_enabled') is enabled
    assert Surface(page(client)).boolean('turnstile_one_click_enabled') is enabled

@pytest.mark.parametrize('raw,masked', [
    ('http://alice:private_one@host-a.invalid:8080', 'http://***:***@host-a.invalid:8080'),
    ('bob:p%40ss%23word@host-b.invalid:8443', '***:***@host-b.invalid:8443'),
])
def test_proxy_effective_value_renders_with_userinfo_masked(client, raw, masked):
    client.environment.setenv('BD_HTTP_PROXY', raw)
    text = page(client)
    assert 'alice' not in text and 'private_one' not in text and 'bob:' not in text and 'p%40ss%23word' not in text
    c = Surface(text).control('BD_HTTP_PROXY')
    assert c.get('value') == masked, 'proxy control must show the masked effective endpoint'

@pytest.mark.parametrize('value', ['http://proxy-a.invalid:8080', 'https://proxy-b.invalid:8443', ''])
def test_proxy_form_write_changes_actual_runtime_reader(client, value):
    r = client.post(WRITE, data={'BD_HTTP_PROXY': value})
    assert r.status_code in (200, 303), f'valid proxy write refused/missing: {r.status_code}'
    assert http_client._proxy_url() == (value or None), 'descriptor changed without changing product reader'
    assert Surface(page(client)).control('BD_HTTP_PROXY').get('value', '') == value
    assert GC.get('quiet_hours') == 'untouched'

@pytest.mark.parametrize('enabled', [True, False])
def test_toggle_form_write_changes_real_store_and_render(client, enabled):
    assert GC.set_config({'turnstile_one_click_enabled': not enabled})
    r = client.post(WRITE, data={'turnstile_one_click_enabled': str(enabled).lower()})
    assert r.status_code in (200, 303), f'valid boolean write refused/missing: {r.status_code}'
    assert GC.get('turnstile_one_click_enabled') is enabled
    assert json.loads(GC._CONFIG_FILE.read_text())['turnstile_one_click_enabled'] is enabled
    assert GC.get('quiet_hours') == 'untouched'
    assert Surface(page(client)).boolean('turnstile_one_click_enabled') is enabled

@pytest.mark.parametrize('bad', [
    'http://host.invalid:80\rInjected: yes', 'http://host.invalid:80\nInjected: yes',
    '\r\nhttp://host.invalid:80', 'javascript:alert(1)', 'file:///etc/passwd',
    'socks5://host.invalid:1080', 'host.invalid:8080', 'http://',
])
def test_proxy_invalid_write_refused_without_state_change(client, bad):
    old_env = os.environ['BD_HTTP_PROXY']
    old_file = GC._CONFIG_FILE.read_bytes()
    r = client.post(WRITE, data={'BD_HTTP_PROXY': bad})
    assert r.status_code in (400, 422), f'invalid value must be validated, not missing route/accepted: {r.status_code}'
    assert os.environ['BD_HTTP_PROXY'] == old_env
    assert GC._CONFIG_FILE.read_bytes() == old_file


def test_unknown_field_and_bad_boolean_refused_atomically(client):
    old_file = GC._CONFIG_FILE.read_bytes()
    for data in ({'turnstile_one_click_enabled': 'not-a-boolean'}, {'unregistered_setting': 'true'}):
        r = client.post(WRITE, data=data)
        assert r.status_code in (400, 422), f'validation path absent or fail-open: {r.status_code}'
        assert GC._CONFIG_FILE.read_bytes() == old_file


def test_mask_placeholder_cannot_overwrite_proxy_credentials(client):
    raw = 'http://alice:private_one@host-a.invalid:8080'
    client.environment.setenv('BD_HTTP_PROXY', raw)
    r = client.post(WRITE, data={'BD_HTTP_PROXY': 'http://***:***@host-a.invalid:8080'})
    # F2: the window used to accept 200 OR 400, so it pinned neither half of the promise.
    # Saving the field unchanged is a PRESERVING save and must succeed.
    assert r.status_code == 200, f'unchanged masked field refused: {r.status_code} {r.get_data(as_text=True)}'
    assert os.environ['BD_HTTP_PROXY'] == raw, 'saving a masked view overwrote live userinfo'
    assert 'private_one' not in r.get_data(as_text=True)


@pytest.mark.parametrize('source', ('environment', 'persisted'))
def test_masked_save_preserves_credentials_from_either_source(client, source):
    """F2: render and validate read ONE source.

    The control renders from _effective_proxy(), which falls back to the persisted store
    when BD_HTTP_PROXY is absent -- the post-restart state row 972's fallback exists to
    serve. When validate read os.environ alone, that state rendered masked credentials
    and then refused the unchanged field, contradicting the help text rendered beside it.
    """
    raw = 'http://alice:private_two@host-b.invalid:8080'
    masked = 'http://***:***@host-b.invalid:8080'
    if source == 'environment':
        client.environment.setenv('BD_HTTP_PROXY', raw)
    else:
        client.environment.delenv('BD_HTTP_PROXY', raising=False)
        assert GC.set_config({'BD_HTTP_PROXY': raw})
    assert http_client._proxy_url() == raw, f'{source}: fixture did not establish the source'
    assert Surface(page(client)).control('BD_HTTP_PROXY')['value'] == masked

    r = client.post(WRITE, data={'BD_HTTP_PROXY': masked})
    assert r.status_code == 200, f'{source}: unchanged masked field refused: {r.get_data(as_text=True)}'
    assert http_client._proxy_url() == raw, f'{source}: live credentials not preserved'
    assert 'private_two' not in r.get_data(as_text=True)


@pytest.mark.parametrize('stored', ('http://stale.invalid:3128', ''))
@pytest.mark.parametrize('env', ('absent', 'empty'))
def test_env_precedence_over_store_including_the_empty_string(client, env, stored):
    """F1: an EXPORTED-EMPTY proxy is an explicit 'no proxy', not a missing one.

    The fallback is taken only when BD_HTTP_PROXY is ABSENT. Present-but-empty means the
    operator turned egress proxying off, and a stale stored value must not resurrect it.
    The one-token mutant `if raw is None:` -> `if not raw:` flips exactly this case and
    was passing every test in rows 972/973 before this pin existed.
    """
    assert GC.set_config({'BD_HTTP_PROXY': stored})
    if env == 'absent':
        client.environment.delenv('BD_HTTP_PROXY', raising=False)
        expected = stored or None
    else:
        client.environment.setenv('BD_HTTP_PROXY', '')
        expected = None
    assert http_client._proxy_url() == expected


def test_proxy_userinfo_write_not_echoed_by_any_read_surface(client):
    raw = 'http://writer_name:writer_password@proxy-write.invalid:8080'
    r = client.post(WRITE, data={'BD_HTTP_PROXY': raw})
    assert r.status_code in (200, 303), f'credentialed proxy write absent: {r.status_code}'
    assert http_client._proxy_url() == raw
    replies = [r] + [client.get(path) for path in ('/cockpit/settings', '/api/settings/schema', '/api/settings/env/effective')]
    for reply in replies:
        assert reply.status_code in (200, 303)
        text = reply.get_data(as_text=True)
        assert 'writer_name' not in text and 'writer_password' not in text
    assert Surface(page(client)).control('BD_HTTP_PROXY')['value'] == 'http://***:***@proxy-write.invalid:8080'
