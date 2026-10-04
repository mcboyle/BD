"""O1826 BRIEF-48: dead and duplicate code (M002, M013, M107).

M205 (api-client CSRF retry) is driven through its vitest spec from
tests/test_o1826_c48_dead_and_duplicate_code_scope.py.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import os
import textwrap

import pytest
from flask import Flask

BD_GATE_SCOPE = "module"


# ── M002: aria_audit's placeholder branch was unreachable ───────────────────


def _aria_audit_branch_conditions():
    from bulk_downloader import accessibility

    tree = ast.parse(textwrap.dedent(inspect.getsource(accessibility.aria_audit)))
    return [
        ast.unparse(node.test) for node in ast.walk(tree) if isinstance(node, ast.If)
    ]


def test_m002_no_branch_implied_by_the_unlabeled_branch():
    conditions = _aria_audit_branch_conditions()
    # Precondition: the probe sees aria_audit's real branches.
    assert "not has_label_for and (not has_aria)" in conditions, conditions
    assert "has_placeholder_only" not in conditions, (
        "M002-DEAD-BRANCH: aria_audit still carries the unreachable "
        "placeholder-only branch"
    )


def test_m002_placeholder_only_input_is_reported_unlabeled_once():
    from bulk_downloader.accessibility import aria_audit

    result = aria_audit(
        '<html><body><input type="text" placeholder="Name"></body></html>'
    )
    kinds = [issue["kind"] for issue in result["issues"]]
    assert kinds.count("input_unlabeled") == 1, result
    assert "placeholder_only_label" not in kinds, result


# ── M013: resolve no longer masks an AttributeError as an "older runner" ────


class _BrokenRunner:
    """A runner that HAS the methods; they fail inside with AttributeError."""

    def start_manual_login(self):
        raise AttributeError("fixture-manual-login-internal-bug")

    def login_async(self):
        raise AttributeError("fixture-login-async-internal-bug")


def _resolve(monkeypatch, kind):
    app_dashboard = importlib.import_module("bulk_downloader.app_dashboard")
    monkeypatch.setattr(
        app_dashboard, "_app_runners", lambda: {"broken": _BrokenRunner()}
    )
    app = Flask(__name__)
    app.register_blueprint(app_dashboard.dashboard_bp)
    return app.test_client().post(
        "/api/dashboard/v2/resolve", json={"site_id": "broken", "kind": kind}
    )


@pytest.mark.parametrize(
    "kind, marker",
    [
        ("captcha_pending", "fixture-manual-login-internal-bug"),
        ("login_expired", "fixture-login-async-internal-bug"),
    ],
)
def test_m013_internal_attribute_error_surfaces(monkeypatch, kind, marker):
    response = _resolve(monkeypatch, kind)
    body = response.get_json()
    assert response.status_code == 500, (
        f"M013-ATTRIBUTEERROR-MASKED: {kind} answered {response.status_code} {body}"
    )
    assert body == {"ok": False, "error": f"AttributeError: {marker}"}


# ── M107: one search-field detector ─────────────────────────────────────────

CHROME = os.environ.get("BD_PW_CHROME", "")

# Each fixture holds one text input, #f. The two old detectors disagreed on
# the first two: name=q is a search box only to _SEARCH_FIELD_JS, and the
# substring "search" inside "researcher" fooled only _SEARCH_INPUT_JS.
FIXTURES = {
    "name_q_search_box": '<form action="/go"><input id="f" type="text" name="q"></form>',
    "researcher_login_field": '<form action="/login"><input id="f" type="text" name="researcher"></form>',
    "type_search": '<input id="f" type="search" name="term">',
    "plain_username": '<form action="/login"><input id="f" type="text" name="username"></form>',
}


def _launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    if CHROME and os.path.exists(CHROME):
        try:
            return p.chromium.launch(
                headless=True, timeout=20000, args=args, executable_path=CHROME
            )
        except Exception:  # noqa: BLE001,S110 -- fall back to the bundled chromium
            pass
    try:
        return p.chromium.launch(headless=True, timeout=20000, args=args)
    except Exception as e:  # noqa: BLE001 -- any launch failure is a FAILURE (T5)
        pytest.fail(
            f"chromium not launchable here -- a launch failure is a FAILURE, not a skip (T5): {e}"
        )


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = _launch(p)
        try:
            yield b
        finally:
            b.close()


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_m107_login_field_probe_agrees_with_search_detector(browser, name):
    from bulk_downloader.login_impl._common import (
        _is_search_field,
        _visible_login_field,
    )

    page = browser.new_page()
    try:
        page.set_content(
            "<!doctype html><html><body>" + FIXTURES[name] + "</body></html>"
        )
        is_search, why = _is_search_field(page.locator("#f"))
        login_field = _visible_login_field(page, ["#f"])
        assert login_field is (not is_search), (
            f"M107-DETECTORS-DISAGREE: {name}: _is_search_field={is_search!r} "
            f"({why!r}) but _visible_login_field={login_field!r}"
        )
    finally:
        page.close()


def test_m107_fixture_set_holds_both_answers(browser):
    """Positive control: the fixture set exercises a search box AND a field."""
    from bulk_downloader.login_impl._common import _is_search_field

    page = browser.new_page()
    try:
        answers = set()
        for html in FIXTURES.values():
            page.set_content("<!doctype html><html><body>" + html + "</body></html>")
            answers.add(_is_search_field(page.locator("#f"))[0])
        assert answers == {True, False}
    finally:
        page.close()
