"""sl-fix-r2 (bughunt sites-lifecycle FINDINGS rows 2 and 6), both in
app_sites_id_core.

Row 2: the clone strip set was a hand list that drifted from
site_editor.SECRET_FIELDS (the set export/diff/audit use), so qb_password,
tpdb_api_key, ai_api_key and auth_token were copied into the clone.  The
tests plant EVERY SECRET_FIELDS value on the parent, and a synthetic field
added to SECRET_FIELDS proves the strip set is derived, not re-listed.

Row 6: re.compile(pattern) was guarded but rgx.subn(replacement) was not, so
a bad group reference in the replacement raised re.error (HTTP 500).  subn
only runs per queued URL, so the site is seeded with one URL first.
"""
from __future__ import annotations

import pytest

BD_GATE_SCOPE = "repo-wide"


def _new_site(fresh_app, name):
    r = fresh_app.post("/api/sites", json={"name": name})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r.get_json()["id"]


def _clone(fresh_app, sid):
    rc = fresh_app.post("/api/sites/%s/clone" % sid, json={})
    assert rc.status_code == 200, rc.get_data(as_text=True)[:300]
    new_sid = rc.get_json()["id"]
    assert new_sid != sid, "precondition: the clone is a distinct site"
    return new_sid


# -- row 2: clone strips every SECRET_FIELDS value -------------------------

def test_clone_strips_every_secret_field(fresh_app):
    from bulk_downloader import app as app_module
    from bulk_downloader.site_editor import SECRET_FIELDS
    sid = _new_site(fresh_app, "secret-parent")
    for f in SECRET_FIELDS:
        app_module.s_cfg[sid][f] = "secret-%s" % f
    new_sid = _clone(fresh_app, sid)
    clone = app_module.s_cfg[new_sid]
    kept = sorted(f for f in SECRET_FIELDS if clone.get(f) not in ("", None))
    for f in kept:
        pytest.fail("sl-fix-r2 row 2: clone kept secret %r (kept: %s)"
                    % (f, ", ".join(kept)))
    for f in SECRET_FIELDS:
        assert app_module.s_cfg[sid][f] == "secret-%s" % f, (
            "cloning must not disturb the parent's %r" % f)


def test_clone_strip_set_is_derived_from_secret_fields(fresh_app, monkeypatch):
    from bulk_downloader import app as app_module
    from bulk_downloader import site_editor
    monkeypatch.setattr(site_editor, "SECRET_FIELDS",
                        site_editor.SECRET_FIELDS | {"zz_probe_secret"})
    sid = _new_site(fresh_app, "derived-parent")
    app_module.s_cfg[sid]["zz_probe_secret"] = "secret-zz"
    new_sid = _clone(fresh_app, sid)
    val = app_module.s_cfg[new_sid].get("zz_probe_secret")
    if val not in ("", None):
        pytest.fail("sl-fix-r2 row 2: strip set is not derived from "
                    "site_editor.SECRET_FIELDS (zz_probe_secret=%r)" % val)


def test_clone_keeps_non_secret_fields(fresh_app):
    """Control: an ordinary field still carries over."""
    from bulk_downloader import app as app_module
    sid = _new_site(fresh_app, "plain-parent")
    app_module.s_cfg[sid]["max_retries"] = 7
    new_sid = _clone(fresh_app, sid)
    assert app_module.s_cfg[new_sid]["max_retries"] == 7


# -- row 6: bad replacement template -> 400, not re.error ------------------

def _seed_url(sid, url):
    from bulk_downloader import app as app_module
    app_module.runners[sid].urls.append(url)


def _transform(fresh_app, sid, replacement):
    body = {"pattern": r"x(\d)", "replacement": replacement, "dry_run": True}
    try:
        return fresh_app.post("/api/sites/%s/bulk_url_transform" % sid,
                              json=body)
    except Exception as e:  # fresh_app propagates route exceptions
        pytest.fail("sl-fix-r2 row 6: replacement %r raised %s instead of 400"
                    % (replacement, type(e).__name__))


@pytest.mark.parametrize("replacement", ["y$2", "y\\2", "y\\q", "y\\g<zz>"])
def test_bad_replacement_template_is_400(fresh_app, replacement):
    sid = _new_site(fresh_app, "transform-bad")
    _seed_url(sid, "https://example.test/x1")
    r = _transform(fresh_app, sid, replacement)
    assert r.status_code == 400, (
        "sl-fix-r2 row 6: replacement %r -> %s %s"
        % (replacement, r.status_code, r.get_data(as_text=True)[:200]))
    body = r.get_json()
    assert body["ok"] is False
    assert "invalid replacement" in body["error"]


def test_good_replacement_template_still_transforms(fresh_app):
    """Control: a valid group reference is unchanged (200, rewritten)."""
    sid = _new_site(fresh_app, "transform-good")
    _seed_url(sid, "https://example.test/x1")
    r = _transform(fresh_app, sid, "y$1")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    body = r.get_json()
    assert body["changed"] == 1
    assert body["sample"] == [{"from": "https://example.test/x1",
                               "to": "https://example.test/y1"}]
