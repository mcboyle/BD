"""Row 751: both template-apply seams refuse unacknowledged captcha egress."""
BD_GATE_SCOPE = "repo-wide"

from unittest.mock import Mock


def _template():
    # Zero-entropy synthetic credential fixture.
    return {"name": "synthetic", "config_defaults": {"captcha_api_key": "SYNTHETIC-NOT-A-KEY", "captcha_provider": "anticaptcha"}}


def _template_smuggling_the_ack():
    """The same template, but carrying the acknowledgement INSIDE its own
    config_defaults.

    This is the hostile shape row 751 names in capitals: a template can
    never carry the ack field into persistence.  Both apply seams strip
    it with `defaults.pop(CAPTCHA_EGRESS_ACK_FIELD, None)` BEFORE the
    gate reads `updates`, so the smuggled value can never satisfy the
    gate meant to stop it.  Delete either pop and the gate returns "",
    the key persists, and the ack is written into the site config --
    which is what the two assertions below fail on.
    """
    tpl = _template()
    tpl["config_defaults"]["captcha_egress_disclosure_ack"] = True
    return tpl


def test_bulk_template_apply_refuses_before_persistence(monkeypatch):
    from bulk_downloader import app
    runner = Mock()
    monkeypatch.setattr(app, "runners", {"s": runner})
    monkeypatch.setattr(app, "s_cfg", {"s": {}})
    monkeypatch.setattr(app, "_save_sites_config", Mock())
    from bulk_downloader import templates
    monkeypatch.setattr(templates, "get", lambda _id: _template())
    ok, message = app._apply_template_by_id("s", "user_synthetic")
    assert ok is False and "captcha_egress_disclosure_ack" in message
    assert "captcha_api_key" not in app.s_cfg["s"]
    assert runner.update_config.call_count == 0


def test_route_template_apply_refuses_before_persistence(monkeypatch):
    from bulk_downloader import app, app_sites_teach, app_state, templates
    runner = Mock()
    monkeypatch.setattr(app_state, "runners", {"s": runner})
    monkeypatch.setattr(app_state, "s_cfg", {"s": {}})
    monkeypatch.setattr(app, "_save_sites_config", Mock())
    monkeypatch.setattr(templates, "get", lambda _id: _template())
    with app.app.test_request_context("/api/sites/s/templates/apply", method="POST", json={"template_id": "user_synthetic"}):
        rv = app_sites_teach.api_template_apply("s")
    # The route returns a (Response, status) tuple only on the REFUSAL
    # path; on success it returns a bare Response.  Unpacking blind makes
    # the base failure a TypeError -- the test failing to call the seam,
    # not the seam failing -- so normalise first and let the three
    # assertions below be what fails on base.
    response, status = rv if isinstance(rv, tuple) else (rv, rv.status_code)
    assert status == 400 and "captcha_egress_disclosure_ack" in response.get_json()["error"]
    assert "captcha_api_key" not in app_state.s_cfg["s"]
    assert runner.update_config.call_count == 0


# ── the ack strip itself, one test per apply site ────────────────────
#
# Both sites are gated by `captcha_egress_disclosure_error(defaults, cfg)`,
# and the gate honours `updates[CAPTCHA_EGRESS_ACK_FIELD] is True`.  The
# only thing standing between a hostile template and a silently enabled
# paid solver is the pop that runs first.  Neutralising either pop leaves
# every other test in this cut green, so each site owes its own test.


def test_bulk_apply_strips_an_ack_smuggled_inside_config_defaults(monkeypatch):
    """Deleting `defaults.pop(...)` in app.py makes this fail."""
    from bulk_downloader import app
    runner = Mock()
    monkeypatch.setattr(app, "runners", {"s": runner})
    monkeypatch.setattr(app, "s_cfg", {"s": {}})
    monkeypatch.setattr(app, "_save_sites_config", Mock())
    from bulk_downloader import templates
    tpl = _template_smuggling_the_ack()
    assert tpl["config_defaults"]["captcha_egress_disclosure_ack"] is True, (
        "precondition: the fixture really carries the smuggled ack")
    monkeypatch.setattr(templates, "get", lambda _id: tpl)
    ok, message = app._apply_template_by_id("s", "user_synthetic")
    assert ok is False, (
        "a template must not acknowledge its own paid-egress transition")
    assert "captcha_egress_disclosure_ack" in message
    assert "captcha_api_key" not in app.s_cfg["s"], (
        "the paid solver key must never reach the site config")
    assert "captcha_egress_disclosure_ack" not in app.s_cfg["s"], (
        "the acknowledgement must never be persisted into the site config")
    assert runner.update_config.call_count == 0


def test_route_apply_strips_an_ack_smuggled_inside_config_defaults(monkeypatch):
    """Deleting `defaults.pop(...)` in app_sites_teach.py makes this fail."""
    from bulk_downloader import app, app_sites_teach, app_state, templates
    runner = Mock()
    monkeypatch.setattr(app_state, "runners", {"s": runner})
    monkeypatch.setattr(app_state, "s_cfg", {"s": {}})
    monkeypatch.setattr(app, "_save_sites_config", Mock())
    tpl = _template_smuggling_the_ack()
    assert tpl["config_defaults"]["captcha_egress_disclosure_ack"] is True, (
        "precondition: the fixture really carries the smuggled ack")
    monkeypatch.setattr(templates, "get", lambda _id: tpl)
    with app.app.test_request_context("/api/sites/s/templates/apply", method="POST", json={"template_id": "user_synthetic"}):
        rv = app_sites_teach.api_template_apply("s")
    response, status = rv if isinstance(rv, tuple) else (rv, rv.status_code)
    assert status == 400, (
        "a template must not acknowledge its own paid-egress transition")
    assert "captcha_egress_disclosure_ack" in response.get_json()["error"]
    assert "captcha_api_key" not in app_state.s_cfg["s"], (
        "the paid solver key must never reach the site config")
    assert "captcha_egress_disclosure_ack" not in app_state.s_cfg["s"], (
        "the acknowledgement must never be persisted into the site config")
    assert runner.update_config.call_count == 0
