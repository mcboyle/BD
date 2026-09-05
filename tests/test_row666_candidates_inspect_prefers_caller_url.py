"""Row 666: candidate inspection resolves links against the caller's page."""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from bulk_downloader import app_sites_id_core as site_core
from bulk_downloader import app_sites_teach as site_teach
from bulk_downloader import candidate_filter as candidate_filter
from bulk_downloader import dry_run


BD_GATE_SCOPE = "repo-wide"

_SITE_ID = "row666"
_PRIMARY_URL = "https://login.example.test/login"
_CALLER_URL = "https://facebook.invalid/x/y/"
_MEASUREMENT_URL = "https://site/x/y/"
_ROOT_RELATIVE = "/scene/123.mp4"
_PATH_RELATIVE = "clip.mp4"
_ABSOLUTE = "https://cdn.example.test/fixed.mp4"
_ROW734_LISTING = "https://example.com/category/fixture"
_ROW734_SCENE = "https://example.com/video/fixture.mp4"
_RELATIVE_HTML = (
    f'<a href="{_ROOT_RELATIVE}">Root</a>'
    f'<a href="{_PATH_RELATIVE}">Path</a>'
    f'<a href="{_ABSOLUTE}">Absolute</a>'
)


def _configure_site(monkeypatch):
    config = {"name": _SITE_ID, "login_url": _PRIMARY_URL}
    monkeypatch.setattr(site_core, "_app_s_cfg", lambda: {_SITE_ID: config})
    primary_url = site_core._site_primary_url(config)
    assert primary_url == _PRIMARY_URL
    assert primary_url
    assert primary_url != _CALLER_URL
    assert urlsplit(primary_url).netloc != urlsplit(_CALLER_URL).netloc
    assert candidate_filter._registrable(urlsplit(primary_url).netloc) != (
        candidate_filter._registrable(urlsplit(_CALLER_URL).netloc)
    )
    return config


def _candidate(payload, href):
    matches = [row for row in payload["candidates"] if row["href"] == href]
    assert len(matches) == 1, (href, payload["candidates"])
    return matches[0]


def _assert_template_dry_run_uses_caller(payload):
    classification = payload["candidate_classification"]
    assert classification is not None
    assert classification["page_url"] == _CALLER_URL, (
        "template dry-run must classify against caller page URL"
    )
    assert classification["n_candidates"] == 1
    assert len(classification["candidates"]) == 1
    assert _candidate(classification, _PATH_RELATIVE)["url"] == (
        "https://facebook.invalid/x/y/clip.mp4"
    )


def test_inspector_resolves_root_relative_form():
    payload = dry_run.inspect_candidates(_RELATIVE_HTML, page_url=_MEASUREMENT_URL)

    assert payload["ok"] is True
    assert len(payload["candidates"]) == payload["n_candidates"] == 3
    assert _candidate(payload, _ROOT_RELATIVE)["url"] == (
        "https://site/scene/123.mp4"
    )


def test_inspector_resolves_path_relative_form():
    payload = dry_run.inspect_candidates(_RELATIVE_HTML, page_url=_MEASUREMENT_URL)

    assert payload["ok"] is True
    assert len(payload["candidates"]) == payload["n_candidates"] == 3
    assert _candidate(payload, _PATH_RELATIVE)["url"] == (
        "https://site/x/y/clip.mp4"
    )


def test_inspector_preserves_absolute_url():
    payload = dry_run.inspect_candidates(_RELATIVE_HTML, page_url=_MEASUREMENT_URL)

    assert payload["ok"] is True
    assert len(payload["candidates"]) == payload["n_candidates"] == 3
    assert _candidate(payload, _ABSOLUTE)["url"] == _ABSOLUTE


def test_candidates_inspect_prefers_the_callers_page_url(fresh_app, monkeypatch):
    _configure_site(monkeypatch)

    response = fresh_app.post(
        f"/api/sites/{_SITE_ID}/candidates/inspect",
        json={"html": _RELATIVE_HTML, "url": _CALLER_URL},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert len(payload["candidates"]) == payload["n_candidates"] == 3
    assert payload["page_url"] == _CALLER_URL
    assert payload["page_host"] == urlsplit(_CALLER_URL).netloc
    assert _candidate(payload, _PATH_RELATIVE)["url"] == (
        "https://facebook.invalid/x/y/clip.mp4"
    )


def test_inspector_classifies_relative_media_against_the_caller_host():
    payload = dry_run.inspect_candidates(_RELATIVE_HTML, page_url=_CALLER_URL)

    assert payload["page_host"] == "facebook.invalid"
    row = _candidate(payload, _PATH_RELATIVE)
    assert row["host"] == "facebook.invalid"
    assert row["accepted"] is True
    assert row["reason"] == "download: media_extension"


def test_candidates_inspect_without_url_falls_back_to_site_primary(
    fresh_app, monkeypatch
):
    _configure_site(monkeypatch)

    response = fresh_app.post(
        f"/api/sites/{_SITE_ID}/candidates/inspect",
        json={"html": _RELATIVE_HTML},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert len(payload["candidates"]) == payload["n_candidates"] == 3
    assert payload["page_url"] == _PRIMARY_URL
    assert payload["page_host"] == urlsplit(_PRIMARY_URL).netloc
    assert _candidate(payload, _PATH_RELATIVE)["url"] == (
        "https://login.example.test/clip.mp4"
    )


def test_template_dry_run_prefers_the_callers_page_url(fresh_app, monkeypatch):
    config = _configure_site(monkeypatch)
    monkeypatch.setattr(site_teach, "_app_s_cfg", lambda: {_SITE_ID: config})

    response = fresh_app.post(
        f"/api/sites/{_SITE_ID}/template/dry_run",
        json={"html": f'<a href="{_PATH_RELATIVE}">Path</a>', "url": _CALLER_URL},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["url"] == _CALLER_URL
    assert payload["host"] == urlsplit(_CALLER_URL).netloc
    _assert_template_dry_run_uses_caller(payload)


def test_template_dry_run_without_url_falls_back_to_site_primary(
    fresh_app, monkeypatch
):
    config = _configure_site(monkeypatch)
    monkeypatch.setattr(site_teach, "_app_s_cfg", lambda: {_SITE_ID: config})
    primary_url = site_teach._site_primary_url
    fallback_calls = []

    def measured_primary_url(candidate_config):
        fallback_calls.append(candidate_config)
        return primary_url(candidate_config)

    monkeypatch.setattr(site_teach, "_site_primary_url", measured_primary_url)
    html = f'<a href="{_PATH_RELATIVE}">Path</a>'
    request_body = {"html": html}
    assert request_body == {"html": '<a href="clip.mp4">Path</a>'}
    assert "url" not in request_body

    response = fresh_app.post(
        f"/api/sites/{_SITE_ID}/template/dry_run",
        json=request_body,
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["url"] == _PRIMARY_URL, (
        "template dry-run without caller URL must use site primary"
    )
    assert fallback_calls == [config]
    assert payload["host"] == urlsplit(_PRIMARY_URL).netloc
    classification = payload["candidate_classification"]
    assert classification is not None
    assert classification["page_url"] == _PRIMARY_URL
    assert classification["n_candidates"] == 1
    assert len(classification["candidates"]) == 1
    assert _candidate(classification, _PATH_RELATIVE)["url"] == (
        "https://login.example.test/clip.mp4"
    )


def test_template_dry_run_caller_assertion_rejects_primary_url():
    payload = dry_run.template_dry_run(
        _PRIMARY_URL, html=f'<a href="{_PATH_RELATIVE}">Path</a>'
    )
    classification = payload["candidate_classification"]
    assert classification is not None
    assert classification["n_candidates"] == 1
    assert _candidate(classification, _PATH_RELATIVE)["url"] == (
        "https://login.example.test/clip.mp4"
    )

    with pytest.raises(
        AssertionError, match="template dry-run must classify against caller page URL"
    ):
        _assert_template_dry_run_uses_caller(payload)


def test_row666_transform_control_imports_route_without_judging_precedence():
    assert callable(site_core.api_candidates_inspect)


def test_row734_backend_emits_safe_candidate_available_by_name():
    accepted = dry_run.inspect_candidates(
        f'<a href="{_ROW734_SCENE}">fixture</a>', page_url=_ROW734_LISTING
    )
    empty = dry_run.inspect_candidates("<p>fixture</p>", page_url=_ROW734_LISTING)

    assert accepted["n_candidates"] == 1
    assert accepted["n_accepted"] == 1
    assert accepted["winner"] is not None
    assert list(accepted).count("safe_candidate_available") == 1
    assert accepted["safe_candidate_available"] is True
    assert empty["n_candidates"] == 0
    assert empty["n_accepted"] == 0
    assert empty["winner"] is None
    assert list(empty).count("safe_candidate_available") == 1
    assert empty["safe_candidate_available"] is False
