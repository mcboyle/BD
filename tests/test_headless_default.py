"""v3.43.11: workers must default to headless=True.

When headless was implicitly False:
  - Workers opened visible Chrome windows
  - On Windows with persistent profiles, the worker's window could
    share state with the takeover browser including OS-level
    download dir, bypassing the app's download routing.
  - Symptom: file appeared in Chrome's configured download folder
    but the queue tracking was incomplete (no integrity check,
    no proper rename, no atomic write).

Workers MUST be headless. The teach/manual-login flows explicitly
pass headless=False to override when they want a visible window.
"""


def test_default_headless_is_true():
    """The DEFAULTS dict must have headless=True. Previous versions
    silently used False because the field wasn't in DEFAULTS at all
    and every callsite did config.get('headless', False)."""
    from bulk_downloader import app as A
    assert "headless" in A.DEFAULTS, \
        "headless missing from DEFAULTS — workers will pick up the wrong default"
    assert A.DEFAULTS["headless"] is True, \
        f"DEFAULTS['headless'] is {A.DEFAULTS['headless']!r} but workers " \
        f"must default to headless=True"


def test_new_site_inherits_headless_true():
    """A freshly created site should have headless=True without the user
    having to set it explicitly."""
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    r = c.get('/api/pair'); token = r.get_json()['token']
    r = c.post('/api/pair/redeem', json={'token': token})
    csrf = r.get_json()['csrf_token']
    H = {'X-CSRF-Token': csrf}
    r = c.post('/api/sites', json={'name': 'headless_default_test'}, headers=H)
    sid = r.get_json()['id']
    cfg = A.s_cfg[sid]
    assert cfg.get("headless") is True, \
        f"New site got headless={cfg.get('headless')!r}, expected True"
    # Cleanup
    A.s_cfg.pop(sid, None)
    A.s_meta.pop(sid, None)
    A.runners.pop(sid, None)


def test_runner_uses_headless_true_at_callsites():
    """Both _launch_browser callers in runner.py must default to True
    when the config doesn't specify headless. Defense in depth — even
    if DEFAULTS is bypassed somehow, the bare get() defaults are right."""
    import re
    from pathlib import Path
    from bulk_downloader import runner as _r
    src = Path(_r.__file__).read_text(encoding="utf-8")
    # Find all `self.config.get("headless", ...)` callsites
    matches = re.findall(r'self\.config\.get\(\s*"headless"\s*,\s*([A-Za-z]+)\s*\)', src)
    assert matches, "No headless config gets found — pattern may have changed"
    for default in matches:
        assert default == "True", \
            f"Found config.get('headless', {default}) — must be True"
