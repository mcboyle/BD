"""v3.66.512 — 3c: surface the enabled host-level template that applies at
download time when it differs from the primary (login-host) resolution.

The site page resolved the reviewed-template status by `_site_primary_url`,
which prefers `login_url`. When the login host has no template but the
content/job host (`start_url`) has an ENABLED host-level template, the card
said "No reviewed template" while that template actually drove the run. This
adds an additive `download_template` field reporting the job-host template so
the SPA can explain the apparent contradiction. No new route; same endpoint,
new response field.
"""
from __future__ import annotations

import bulk_downloader.app as bd_app

REPTYLE = "https://app.reptyle.com/"          # ships an ENABLED reviewed template
LOGIN_NOHOST = "https://auth.no-such-host.example/login"  # no template for this host


def _seed(sid, **cfg):
    cfg.setdefault("name", sid)
    bd_app.s_cfg[sid] = cfg
    return cfg


def test_job_host_template_surfaced_when_login_host_has_none(fresh_app):
    # login host (auth.no-such-host.example) has NO template -> primary status
    # is "No reviewed template"; the content/job host (app.reptyle.com) HAS an
    # enabled template that drives downloads.
    _seed("j1", login_url=LOGIN_NOHOST, start_url=REPTYLE)
    r = fresh_app.get("/api/sites/j1/template_status")
    assert r.status_code == 200
    body = r.get_json()
    # primary resolution still reports no reviewed template (login host)
    assert body["template"]["enabled"] is False
    assert body["label"] == "No reviewed template"
    # but the job-host template is surfaced additively
    dl = body.get("download_template")
    assert dl is not None, "download_template should report the enabled job-host template"
    assert dl["enabled"] is True
    assert dl["host"] == "app.reptyle.com"


def test_no_download_template_for_single_host_site(fresh_app):
    # single content host that itself has the enabled template -> the primary
    # status already reflects it; no redundant job-host signal.
    _seed("j2", start_url=REPTYLE)
    body = fresh_app.get("/api/sites/j2/template_status").get_json()
    assert body["template"]["enabled"] is True
    assert body["template"]["host"] == "app.reptyle.com"
    assert body.get("download_template") in (None, {}), \
        "no download_template when the primary host already has the template"


def test_no_download_template_when_login_and_content_share_host(fresh_app):
    # login_url + start_url on the SAME host -> same template -> no job signal.
    _seed("j3", login_url="https://app.reptyle.com/login", start_url=REPTYLE)
    body = fresh_app.get("/api/sites/j3/template_status").get_json()
    assert body["template"]["enabled"] is True
    assert body.get("download_template") in (None, {})
