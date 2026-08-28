"""Row 356: cookie quality must not call an unmeasured jar perfect.

The fixtures in this module are real browser-export JSON files.  Every scorer
call therefore crosses ``load_cookies_from_file`` and
``normalize_stored_cookie``; no scorer helper is replaced to manufacture an
otherwise unreachable state.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


BD_GATE_SCOPE = "module"


def _write_jar(path: Path, cookies: list[dict]) -> list[dict]:
    path.write_text(json.dumps(cookies), encoding="utf-8")
    assert path.is_file(), "precondition: the browser-export jar exists"

    from bulk_downloader.cookies import load_cookies_from_file

    loaded = load_cookies_from_file(str(path))
    assert len(loaded) == len(cookies), (
        "precondition: the production cookie loader read every fixture cookie"
    )
    return loaded


def _score_from(path: Path, site_id: str, **cfg: object) -> dict:
    from bulk_downloader import cookie_quality

    return cookie_quality.score(
        site_id,
        s_cfg_entry={"cookie_file": str(path), **cfg},
    )


def _assert_numeric_score_has_breakdown(result: dict) -> None:
    score = result.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        assert result.get("breakdown"), (
            f"numeric cookie-quality score {score!r} was published without "
            "measurement evidence"
        )


def _session_jar(path: Path) -> list[dict]:
    return _write_jar(
        path,
        [
            {
                "name": "session_with_sentinel",
                "value": "test-only",
                "domain": "auth.row356.invalid",
                "path": "/",
                "expirationDate": -1,
            },
            {
                "name": "session_without_expiry",
                "value": "test-only",
                "domain": "auth.row356.invalid",
                "path": "/",
            },
        ],
    )


def _fresh_complete_jar(path: Path) -> tuple[list[dict], list[str]]:
    expected = ["session_token", "__cf_bm"]
    expires = time.time() + 14 * 86400
    loaded = _write_jar(
        path,
        [
            {
                "name": name,
                "value": "test-only",
                "domain": ".row356.invalid",
                "path": "/",
                "expirationDate": expires,
            }
            for name in expected
        ],
    )
    return loaded, expected


def test_session_only_jar_reports_freshness_unmeasured(tmp_path):
    jar = tmp_path / "session-only.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: -1 and absent expirationDate both normalize as sessions"
    )
    expected = [cookie["name"] for cookie in loaded]
    assert expected and len(expected) == len(loaded), (
        "precondition: a separate expected-cookie check can run and pass"
    )

    result = _score_from(
        jar,
        "row356_session_only",
        expected_cookies=expected,
    )

    assert result.get("checks") == {
        "applicable": ["freshness", "expected_cookies", "recent_success_rate"],
        "ran": ["expected_cookies"],
    }, result
    assert not (
        result.get("score") == 100
        and result.get("suggested_action") == "ok"
    ), result
    assert result.get("score") is None, result
    assert result.get("suggested_action") == "unknown", result
    assert result.get("measurement_status") == "partial", result
    assert result["breakdown"].get("freshness_unmeasured") == (
        "no expiring cookies in the jar"
    )
    _assert_numeric_score_has_breakdown(result)


def test_unconfigured_expected_cookies_records_unmeasured_check(tmp_path):
    from bulk_downloader import cookie_quality

    site_id = "row356_no_expected_config"
    assert site_id not in cookie_quality._DEFAULT_EXPECTED, (
        "precondition: no built-in expected-cookie list applies"
    )
    jar = tmp_path / "fresh-but-unconfigured.json"
    loaded = _write_jar(
        jar,
        [
            {
                "name": "opaque_session_name",
                "value": "test-only",
                "domain": ".row356.invalid",
                "path": "/",
                "expirationDate": time.time() + 14 * 86400,
            }
        ],
    )
    assert loaded[0]["expires"] > time.time(), (
        "precondition: freshness is independently measurable"
    )

    result = _score_from(jar, site_id)

    assert result.get("checks") == {
        "applicable": ["freshness", "recent_success_rate"],
        "ran": ["freshness"],
    }, result
    assert result["breakdown"].get("expected_cookies_unmeasured") == (
        "no expected_cookies configured"
    )
    _assert_numeric_score_has_breakdown(result)


def test_no_runnable_check_has_explicit_unknown_verdict(tmp_path):
    from bulk_downloader import cookie_quality

    site_id = "row356_nothing_measurable"
    jar = tmp_path / "nothing-measurable.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: freshness has no timestamp to inspect"
    )
    assert site_id not in cookie_quality._DEFAULT_EXPECTED, (
        "precondition: expected-cookie presence has no configured denominator"
    )
    assert not any(
        cookie.get("name") in {"__cf_bm", "cf_clearance"}
        for cookie in loaded
    ), "precondition: no Cloudflare-specific check is applicable"
    assert cookie_quality._recent_success_rate(site_id) is None, (
        "precondition: no local job history can run the success-rate check"
    )

    result = _score_from(jar, site_id)

    assert result.get("measurement_status") == "unmeasured", result
    assert result.get("suggested_action") == "unknown", result
    assert result.get("score") is None, result
    assert result.get("checks") == {
        "applicable": ["freshness", "recent_success_rate"],
        "ran": [],
    }, result
    skipped = {
        key: value
        for key, value in result["breakdown"].items()
        if key.endswith("_unmeasured")
    }
    assert skipped == {
        "freshness_unmeasured": "no expiring cookies in the jar",
        "expected_cookies_unmeasured": "no expected_cookies configured",
        "cloudflare_unmeasured": "no Cloudflare cookies expected or present",
        "success_rate_unmeasured": "no measurable recent job history",
    }, result
    _assert_numeric_score_has_breakdown(result)


def test_fresh_complete_jar_stays_well_scored_and_ok(tmp_path):
    jar = tmp_path / "fresh-complete.json"
    loaded, expected = _fresh_complete_jar(jar)
    assert {cookie["name"] for cookie in loaded} == set(expected), (
        "precondition: every configured expected cookie is present"
    )
    assert all(cookie["expires"] > time.time() for cookie in loaded), (
        "precondition: every cookie has a genuinely fresh expiration"
    )

    result = _score_from(
        jar,
        "row356_fresh_complete",
        expected_cookies=expected,
    )

    assert result["score"] >= 80, result
    assert result["suggested_action"] == "ok", result
    assert "freshness_ok" in result["breakdown"], result
    _assert_numeric_score_has_breakdown(result)


def test_missing_jar_stays_refresh_now(tmp_path):
    jar = tmp_path / "does-not-exist.json"
    assert not jar.exists(), "precondition: no jar exists at the configured path"

    result = _score_from(jar, "row356_missing")

    assert result["jar_size"] == 0, result
    assert result["score"] == 0, result
    assert result["breakdown"] == {"missing_jar": -100}, result
    assert result["suggested_action"] == "refresh_now", result
    _assert_numeric_score_has_breakdown(result)


def test_every_published_numeric_score_has_nonempty_breakdown(tmp_path):
    from bulk_downloader import cookie_quality

    session_path = tmp_path / "invariant-session.json"
    session_loaded = _session_jar(session_path)
    assert all("expires" not in cookie for cookie in session_loaded), (
        "precondition: the invariant includes an unmeasurable session jar"
    )

    fresh_path = tmp_path / "invariant-fresh.json"
    fresh_loaded, expected = _fresh_complete_jar(fresh_path)
    assert {cookie["name"] for cookie in fresh_loaded} == set(expected), (
        "precondition: the invariant includes a measured fresh jar"
    )

    missing_path = tmp_path / "invariant-missing.json"
    assert not missing_path.exists(), (
        "precondition: the invariant includes the existing missing-jar state"
    )

    cases = [
        _score_from(session_path, "row356_invariant_session"),
        _score_from(
            fresh_path,
            "row356_invariant_fresh",
            expected_cookies=expected,
        ),
        _score_from(missing_path, "row356_invariant_missing"),
    ]
    assert len(cases) == 3, "precondition: every intended result was produced"

    for result in cases:
        _assert_numeric_score_has_breakdown(result)


def test_report_all_keeps_unmeasured_rows_explicit(tmp_path):
    from bulk_downloader import cookie_quality

    session_path = tmp_path / "report-session.json"
    loaded = _session_jar(session_path)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: report_all receives a genuinely session-only jar"
    )
    missing_path = tmp_path / "report-missing.json"
    assert not missing_path.exists(), "precondition: the comparison jar is missing"
    cfg = {
        "row356_report_session": {"cookie_file": str(session_path)},
        "row356_report_missing": {"cookie_file": str(missing_path)},
    }
    assert len(cfg) == 2, "precondition: report_all has a mixed-state population"

    rows = cookie_quality.report_all(cfg)

    assert {row["site_id"] for row in rows} == set(cfg), rows
    for row in rows:
        _assert_numeric_score_has_breakdown(row)
    session = next(
        row for row in rows if row["site_id"] == "row356_report_session"
    )
    assert session.get("measurement_status") == "unmeasured", session
    assert session.get("suggested_action") == "unknown", session


def test_auto_relogin_skips_unknown_without_scheduling_it(tmp_path):
    from bulk_downloader import cookie_relogin

    site_id = "row356_relogin_unknown"
    jar = tmp_path / "relogin-session.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: relogin receives a jar whose freshness is unknown"
    )
    cfg = {
        site_id: {
            "cookie_file": str(jar),
            "auth_required": True,
            "auto_relogin_enabled": True,
        }
    }
    assert cfg[site_id]["auto_relogin_enabled"], (
        "precondition: the site reaches quality-based relogin policy"
    )

    result = cookie_relogin.check_and_schedule(cfg)

    assert "error" not in result, result
    assert result["scheduled"] == 0, result
    skipped = [row for row in result["skipped"] if row["site_id"] == site_id]
    assert len(skipped) == 1, result
    assert "unknown" in skipped[0]["reason"].lower(), skipped


def test_queue_priority_marks_unknown_without_defaulting_it_to_perfect(tmp_path):
    from bulk_downloader import queue_priority

    site_id = "row356_priority_unknown"
    jar = tmp_path / "priority-session.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: queue priority receives an unmeasurable session jar"
    )
    cfg = {site_id: {"cookie_file": str(jar)}}

    context = queue_priority._gather_context(cfg)

    assert site_id not in context["cookie_scores"], context
    assert site_id in context["cookie_quality_unknown"], context
    ranked = queue_priority._score_one(
        {"site_id": site_id, "url": "https://row356.invalid/video"},
        s_cfg=cfg,
        context=context,
    )
    assert "cookie_quality_unmeasured" in ranked["breakdown"], ranked
    assert "cookie_quality_penalty" not in ranked["breakdown"], ranked


def test_cockpit_adapter_marks_unmeasured_quality_unavailable(tmp_path):
    from tools import cockpit_templates

    site_id = "row356_cockpit_unknown"
    jar = tmp_path / "cockpit-session.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: cockpit receives an unmeasurable session jar"
    )

    result = cockpit_templates._cookie_quality(
        site_id,
        {"cookie_file": str(jar)},
    )

    assert result.get("measurement_status") == "unmeasured", result
    assert result.get("unavailable") is True, result
    assert result.get("suggested_action") == "unknown", result
    _assert_numeric_score_has_breakdown(result)


def test_cockpit_orders_partial_unknown_before_numeric_quality(
    tmp_path, monkeypatch
):
    from tools import cockpit_templates

    unknown_id = "row356_cockpit_partial"
    unknown_path = tmp_path / "cockpit-partial.json"
    unknown_loaded = _session_jar(unknown_path)
    unknown_expected = [cookie["name"] for cookie in unknown_loaded]
    assert unknown_expected and all(
        "expires" not in cookie for cookie in unknown_loaded
    ), "precondition: expected names are measurable but freshness is not"

    fresh_id = "row356_cockpit_fresh"
    fresh_path = tmp_path / "cockpit-fresh.json"
    fresh_loaded, fresh_expected = _fresh_complete_jar(fresh_path)
    assert all(cookie["expires"] > time.time() for cookie in fresh_loaded), (
        "precondition: the comparison site's quality is numeric and fresh"
    )

    learned_login = {
        "login": {
            "user_field": ["#username"],
            "pass_field": ["#password"],
            "submit_btn": ["button[type=submit]"],
        }
    }
    sites = [
        {
            "id": fresh_id,
            "cookie_file": str(fresh_path),
            "expected_cookies": fresh_expected,
            "learned": learned_login,
        },
        {
            "id": unknown_id,
            "cookie_file": str(unknown_path),
            "expected_cookies": unknown_expected,
            "learned": learned_login,
        },
    ]
    config_path = tmp_path / "sites-row356.json"
    config_path.write_text(json.dumps(sites), encoding="utf-8")
    assert config_path.is_file() and len(sites) == 2, (
        "precondition: login health reads both real site configurations"
    )
    monkeypatch.setenv("BD_SITES_CONFIG_PATH", str(config_path))

    health = cockpit_templates.login_template_health()

    assert health["site_count"] == 2, health
    assert [row["site"] for row in health["sites"]] == [unknown_id, fresh_id], (
        health
    )
    unknown = health["sites"][0]["session"]
    assert unknown["measurement_status"] == "partial", unknown
    assert unknown["suggested_action"] == "unknown", unknown
    assert unknown["available"] is False, unknown


def test_cockpit_page_has_an_explicit_unknown_rendering_branch():
    from flask import Flask
    from tools import cockpit_console

    app = Flask("row356-cockpit-page")
    app.register_blueprint(cockpit_console.bp)
    response = app.test_client().get("/cockpit/")
    assert response.status_code == 200, (
        "precondition: the shipped Cockpit page rendered through its real route"
    )
    body = response.get_data(as_text=True)
    assert "PAGES.logintemplates" in body, (
        "precondition: the login-template UI is present in the served page"
    )

    assert "s.session.suggested_action==='unknown'" in body
    assert "s.session.measurement_status||'unmeasured'" in body


def test_cookie_quality_api_serializes_unknown_score_as_json_null(
    tmp_path, monkeypatch
):
    from flask import Flask
    from bulk_downloader import app_cookie_quality

    site_id = "row356_api_unknown"
    jar = tmp_path / "api-session.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: the API calls the scorer with a session-only real jar"
    )
    cfg = {site_id: {"cookie_file": str(jar)}}
    monkeypatch.setattr(app_cookie_quality, "_app_s_cfg", lambda: cfg)
    app = Flask("row356-cookie-quality-api")
    app_cookie_quality.register_routes(app)

    response = app.test_client().get(f"/api/cookie_quality/{site_id}")

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["site_id"] == site_id, payload
    assert payload["score"] is None, payload
    assert payload["measurement_status"] == "unmeasured", payload
    assert payload["suggested_action"] == "unknown", payload
    _assert_numeric_score_has_breakdown(payload)


def test_template_stability_does_not_claim_to_grade_cookie_freshness(
    tmp_path, monkeypatch
):
    from tools import cockpit_templates

    site_id = "row356_template_stability_scope"
    jar = tmp_path / "stability-session.json"
    loaded = _session_jar(jar)
    assert all("expires" not in cookie for cookie in loaded), (
        "precondition: the configured site has unmeasurable cookie freshness"
    )
    config_path = tmp_path / "stability-sites.json"
    config_path.write_text(
        json.dumps([{"id": site_id, "cookie_file": str(jar)}]),
        encoding="utf-8",
    )
    assert config_path.is_file(), (
        "precondition: template stability receives the real site configuration"
    )
    monkeypatch.setenv("BD_SITES_CONFIG_PATH", str(config_path))
    cookie_quality_calls = []

    def _record_cookie_quality_call(*args, **kwargs):
        cookie_quality_calls.append((args, kwargs))
        return {"score": 100, "suggested_action": "ok"}

    monkeypatch.setattr(
        cockpit_templates, "_cookie_quality", _record_cookie_quality_call
    )

    result = cockpit_templates.template_stability_score()

    assert cookie_quality_calls == [], (
        "template stability must not consult an unmeasurable cookie lifetime"
    )
    assert result["site_count"] == 1, result
    row = result["sites"][0]
    assert set(row["components"]) == {
        "download_clean",
        "login_clean",
        "drift_quiet",
    }, row
    assert "session" not in (
        cockpit_templates.template_stability_score.__doc__ or ""
    ).lower()
    assert "cookie" not in json.dumps(row["inputs"]).lower(), row
