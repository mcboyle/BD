"""R4 rendered proxy-userinfo contract: fixture-only, env and persisted sources."""
# R4 proxy-masking class contract. Wired into the gate-suites "artifacts-pins" shard in
# .github/workflows/ci.yml beside test_settings_center_slice5.py (H622 anti-orphan).
BD_GATE_SCOPE = "repo-wide"

import html
import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

import pytest
from flask import Flask

# ROWS_TREE selects the checkout under test for the scratch replay (base vs candidate).
# CI has no such variable and nothing may supply one, so the in-repo copy defaults to its
# own checkout rather than failing collection with a KeyError.
ROOT = Path(os.environ.get('ROWS_TREE') or Path(__file__).resolve().parent.parent).resolve()
sys.path.insert(0, str(ROOT))
from bulk_downloader import app_settings_center as sc, global_config as GC

# id, raw, expected rendered input value, nonempty secrets/userinfo markers
CASES = [
    ('scheme', 'http://user_one:secret_one@proxy.invalid:8080', 'http://***:***@proxy.invalid:8080', ('user_one', 'secret_one')),
    ('network_relative', '//user_two:secret_two@proxy.invalid', '//***:***@proxy.invalid', ('user_two', 'secret_two')),
    ('bare_with_port', 'user_three:secret_three@proxy.invalid:8080', '***:***@proxy.invalid:8080', ('user_three', 'secret_three')),
    ('bare_without_port', 'user_four:secret_four@proxy.invalid', '***:***@proxy.invalid', ('user_four', 'secret_four')),
    ('uppercase_scheme', 'HTTP://user_five:secret_five@proxy.invalid', 'HTTP://***:***@proxy.invalid', ('user_five', 'secret_five')),
    ('ipv6_scheme', 'https://user_six:secret_six@[::1]:8443', 'https://***:***@[::1]:8443', ('user_six', 'secret_six')),
    ('ipv6_relative', '//user_seven:secret_seven@[::1]:8080', '//***:***@[::1]:8080', ('user_seven', 'secret_seven')),
    ('ipv6_bare', 'user_eight:secret_eight@[::1]', '***:***@[::1]', ('user_eight', 'secret_eight')),
    ('encoded_colon_at', 'https://encoded_user%3Atag:secret%3A%40nine@proxy.invalid', 'https://***:***@proxy.invalid', ('encoded_user%3Atag', 'encoded_user:tag', 'secret%3A%40nine', 'secret:@nine')),
    ('encoded_relative', '//user_ten:secret%40%3Aten@proxy.invalid', '//***:***@proxy.invalid', ('user_ten', 'secret%40%3Aten', 'secret@:ten')),
    ('empty_password', 'http://empty_user:@proxy.invalid', 'http://***:***@proxy.invalid', ('empty_user',)),
    ('empty_password_bare', 'empty_bare:@proxy.invalid', '***:***@proxy.invalid', ('empty_bare',)),
    ('at_in_password', 'http://at_user:secret@twelve@proxy.invalid', 'http://***:***@proxy.invalid', ('at_user', 'secret@twelve')),
    ('no_userinfo_scheme', 'http://proxy.invalid:8080', 'http://proxy.invalid:8080', ()),
    ('no_userinfo_relative', '//proxy.invalid:8080', '//proxy.invalid:8080', ()),
    ('no_userinfo_bare', 'proxy.invalid:8080', 'proxy.invalid:8080', ()),
    ('no_userinfo_ipv6', 'https://[::1]:8443', 'https://[::1]:8443', ()),
    ('at_outside_authority', 'https://proxy.invalid/path?mail=public@label#part', 'https://proxy.invalid/path?mail=public@label#part', ()),
    ('at_in_query_no_slash', 'https://proxy.invalid:8443?mail=public@label', 'https://proxy.invalid:8443?mail=public@label', ()),
    ('at_in_fragment_no_slash', 'http://proxy.invalid:8080#public@label', 'http://proxy.invalid:8080#public@label', ()),
    ('userinfo_query_boundary', 'http://query_user:query_secret@proxy.invalid:8080?mail=public@label', 'http://***:***@proxy.invalid:8080?mail=public@label', ('query_user', 'query_secret')),
]

class ProxyInput(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.inputs = []
        self.label_ids = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'input' and a.get('name') == 'BD_HTTP_PROXY':
            self.inputs.append(a)
        if tag == 'label' and a.get('for'):
            self.label_ids.append(a['for'])


@pytest.fixture
def isolated_client(tmp_path, monkeypatch):
    with monkeypatch.context() as m:
        m.chdir(tmp_path)
        m.setattr(GC, '_CONFIG_FILE', tmp_path / 'app_config.json')
        m.setattr(GC, '_cached', None)
        m.setattr(GC, '_cached_mtime', 0.0)
        m.delenv('BD_HTTP_PROXY', raising=False)
        assert GC.set_config({'BD_HTTP_PROXY': 'http://sentinel.invalid:80', 'turnstile_one_click_enabled': False})
        app = Flask(__name__)
        app.config['TESTING'] = True
        sc.register_routes(app)
        with app.test_client() as client:
            yield client, m


# The stored product reader accepts only lower-case http(s) URLs. Environment
# fallback is what renders legacy forms; all forms are tested through that path.
# Unsupported stored values never reach this surface, so do not invent fixture REDs.
STORED_IDS = {'scheme', 'ipv6_scheme', 'encoded_colon_at', 'empty_password',
              'at_in_password', 'no_userinfo_scheme', 'no_userinfo_ipv6',
              'at_outside_authority', 'at_in_query_no_slash', 'at_in_fragment_no_slash',
              'userinfo_query_boundary'}
PARAMS = [(source, *case) for case in CASES for source in ('environment', 'persisted')
          if source == 'environment' or case[0] in STORED_IDS]

@pytest.mark.parametrize('source,case_id,raw,expected,forbidden', PARAMS,
                         ids=[f'{p[1]}-{p[0]}' for p in PARAMS])
def test_rendered_proxy_masks_userinfo_for_every_form(isolated_client, source, case_id, raw, expected, forbidden):
    client, m = isolated_client
    if source == 'environment':
        m.setenv('BD_HTTP_PROXY', raw)
        assert GC.get('BD_HTTP_PROXY') == 'http://sentinel.invalid:80'
    else:
        assert GC.set_config({'BD_HTTP_PROXY': raw})
        assert 'BD_HTTP_PROXY' not in os.environ
    # Prove the real read source supplied the complete credentialed input.
    assert sc._effective_proxy() == raw
    reply = client.get('/cockpit/settings')
    assert reply.status_code == 200
    rendered = reply.get_data(as_text=True)
    parsed = ProxyInput(rendered)
    assert len(parsed.inputs) == 1, 'missing/duplicate real proxy input cannot prove masking'
    field = parsed.inputs[0]
    assert field.get('id') in parsed.label_ids, 'rendered input must retain its associated label'
    assert 'value' in field
    # The full response, not only a helper/descriptor, must exclude raw and encoded secrets.
    for token in forbidden:
        assert token, 'empty password uses username masking/exact-value checks, never empty-substring logic'
        assert token not in rendered, f'{case_id}/{source}: raw userinfo leaked in HTML'
        assert token not in html.unescape(rendered), f'{case_id}/{source}: entity-escaped userinfo leaked'
        assert token not in unquote(html.unescape(rendered)), f'{case_id}/{source}: percent-encoded userinfo leaked'
    assert field['value'] == expected, f'{case_id}/{source}: mask userinfo only; preserve scheme, host, IPv6, port and suffix'
