"""Row 309: capture completeness is independent of ``manifest.json``.

The capture plan has 52 logical views and two themes.  This gate pins that
population independently, drives every planned view through its failure seam,
and proves the navigator refuses incomplete, duplicate, failed, or unavailable
measurements instead of treating the rows it happened to receive as complete.
"""
from __future__ import annotations

import asyncio
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_PK = _REPO / "project-knowledge"
_CAPTURE = _PK / "capture_all.py"
_NAVIGATOR = _PK / "build_navigator.py"

# Deliberately independent of capture_all.py, its manifest, and the shared
# production contract.  A deletion from any of those populations cannot shrink
# this denominator with it.
_EXPECTED_LOGICAL_VIEWS = frozenset({
    ("nav", "/"),
    ("nav", "/queue"),
    ("nav", "/history"),
    ("nav", "/activity"),
    ("nav", "/needs-review"),
    ("nav", "/library"),
    ("nav", "/sites"),
    ("nav", "/capture"),
    ("nav", "/templates"),
    ("nav", "/dom-analyzer"),
    ("nav", "/ai-teach"),
    ("nav", "/pools-macros"),
    ("nav", "/batch-ops"),
    ("nav", "/imports"),
    ("nav", "/import-views"),
    ("nav", "/dedup"),
    ("nav", "/rebalance"),
    ("nav", "/maintenance"),
    ("nav", "/backup"),
    ("nav", "/integrations"),
    ("nav", "/notifications"),
    ("nav", "/vpn"),
    ("nav", "/cluster"),
    ("nav", "/secrets"),
    ("nav", "/settings"),
    ("nav", "/settings/advanced"),
    ("nav", "/dashboard"),
    ("nav", "/more-actions"),
    ("nav", "/logs/diff"),
    ("drillin", "/sites/__probe__"),
    ("drillin", "/sites/__probe__/actions"),
    ("drillin", "/sites/__probe__/inspect"),
    ("drillin", "/sites/__probe__/payload-actions"),
    ("drillin", "/sites/__probe__/settings"),
    ("subtab", "/sites › All"),
    ("subtab", "/sites › Active"),
    ("subtab", "/sites › Paused"),
    ("subtab", "/sites › Issues"),
    ("subtab", "/activity › 24h"),
    ("subtab", "/activity › 7d"),
    ("subtab", "/activity › 30d"),
    ("subtab", "/activity › All"),
    ("subtab", "/history › History"),
    ("subtab", "/history › Events"),
    ("subtab", "/history › Logs"),
    ("subtab", "/history › Saved"),
    ("popup", "Add-site wizard"),
    ("popup", "Command palette"),
    ("popup", "Mobile nav drawer"),
    ("cockpit", "/cockpit"),
    ("popup", "Cockpit gear popover"),
    ("popup", "Cockpit nav dropdown"),
})
_THEMES = ("light", "dark")


def _release_version():
    source = (_REPO / "bulk_downloader" / "__init__.py").read_text(
        encoding="utf-8")
    versions = []
    for statement in ast.parse(source).body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = (statement.targets if isinstance(statement, ast.Assign)
                   else [statement.target])
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in targets
        ):
            continue
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            versions.append(value.value)
    assert len(versions) == 1
    return versions[0]


def _load_capture(monkeypatch):
    monkeypatch.syspath_prepend(str(_PK))
    name = "row_309_capture_all"
    spec = importlib.util.spec_from_file_location(name, _CAPTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    saved_modules = {name: sys.modules[name]} if name in sys.modules else {}
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
    return module


def _valid_rows(contract):
    rows = []
    capture_release_version = _release_version()
    for view in contract.EXPECTED_VIEWS:
        for theme in contract.THEMES:
            rows.append({
                "cat": view.cat,
                "route": view.route,
                "label": view.label,
                "theme": theme,
                "status": "ok",
                "file": f"{theme}/{view.filename}",
                "head": "fixture",
                "h": 1,
                "err": 0,
                "ox": False,
                "capture_release_version": capture_release_version,
            })
    return rows


def test_expected_view_population_is_exact_nonzero_and_has_dropdown(monkeypatch):
    capture = _load_capture(monkeypatch)
    actual = frozenset((view.cat, view.route) for view in capture.EXPECTED_VIEWS)

    assert len(_EXPECTED_LOGICAL_VIEWS) == 52
    assert len(actual) == 52
    assert actual == _EXPECTED_LOGICAL_VIEWS
    assert ("popup", "Cockpit nav dropdown") in actual

    expected_rows = {
        (cat, route, theme)
        for cat, route in _EXPECTED_LOGICAL_VIEWS
        for theme in _THEMES
    }
    assert len(expected_rows) == 104
    assert capture.expected_manifest_keys() == expected_rows


class _FailingPage:
    def __init__(self, owner):
        self.owner = owner

    def on(self, *_args):
        return None

    async def goto(self, url, **_kwargs):
        self.owner.goto_urls.append(url)
        raise RuntimeError("row-309 synthetic page failure")

    async def close(self):
        self.owner.closed_pages += 1


class _FailingContext:
    def __init__(self, owner):
        self.owner = owner

    async def add_init_script(self, *_args):
        return None

    async def new_page(self):
        self.owner.created_pages += 1
        return _FailingPage(self.owner)

    async def close(self):
        self.owner.closed_contexts += 1


class _FailingBrowser:
    def __init__(self):
        self.created_contexts = 0
        self.closed_contexts = 0
        self.created_pages = 0
        self.closed_pages = 0
        self.goto_urls = []

    async def new_context(self, **_kwargs):
        self.created_contexts += 1
        return _FailingContext(self)


def test_every_planned_view_emits_one_failure_row(monkeypatch):
    capture = _load_capture(monkeypatch)
    browser = _FailingBrowser()
    capture.manifest.clear()

    asyncio.run(capture.capture_theme(browser, "light"))

    fired = len(browser.goto_urls)
    assert fired == len(_EXPECTED_LOGICAL_VIEWS) == 52
    assert browser.created_pages == browser.closed_pages == fired
    assert browser.created_contexts == browser.closed_contexts == 3
    assert len(capture.manifest) == fired
    assert {
        (row["cat"], row["route"], row["theme"])
        for row in capture.manifest
    } == {(cat, route, "light") for cat, route in _EXPECTED_LOGICAL_VIEWS}
    assert all(row["status"] == "error" for row in capture.manifest)
    assert all(row["file"] is None for row in capture.manifest)
    assert all(row["err"] == 1 for row in capture.manifest)
    assert all("row-309 synthetic page failure" in row["error"]
               for row in capture.manifest)


class _CockpitLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        if self.selector == ".nav .navsec.baropen .navitems":
            return self.page.menu_visible
        return True

    async def click(self, **_kwargs):
        self.page.clicks.append(self.selector)


class _CockpitPage:
    def __init__(self, *, menu_visible):
        self.menu_visible = menu_visible
        self.goto_urls = []
        self.layouts = []
        self.reloads = 0
        self.waits = []
        self.locators = []
        self.clicks = []
        self.screenshots = []
        self.closes = 0

    async def goto(self, url, **_kwargs):
        self.goto_urls.append(url)

    async def evaluate(self, _script, value):
        self.layouts.append(value)

    async def reload(self, **_kwargs):
        self.reloads += 1

    async def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)

    def locator(self, selector):
        self.locators.append(selector)
        return _CockpitLocator(self, selector)

    async def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)

    async def close(self):
        self.closes += 1


class _OnePageContext:
    def __init__(self, page):
        self.page = page
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        return self.page


def test_cockpit_dropdown_proves_trigger_and_open_menu(monkeypatch):
    capture = _load_capture(monkeypatch)
    trigger = '.nav .navsec .navhead:has-text("Captures")'
    menu = ".nav .navsec.baropen .navitems"

    capture.manifest.clear()
    opened_page = _CockpitPage(menu_visible=True)
    opened_context = _OnePageContext(opened_page)
    asyncio.run(capture._cockpit_popup(
        opened_context, "dark", "Cockpit nav dropdown", (trigger,),
        layout="top", visible_selector=menu,
    ))
    assert opened_context.new_page_calls == 1
    assert opened_page.goto_urls == [capture.BASE + "/cockpit"]
    assert opened_page.layouts == ["top"]
    assert opened_page.reloads == 1
    assert opened_page.locators == [trigger, menu]
    assert opened_page.clicks == [trigger]
    assert opened_page.screenshots == [{
        "path": f"{capture.OUT}/dark/cockpit_nav_dropdown.png"
    }]
    assert opened_page.closes == 1
    assert len(capture.manifest) == 1
    assert capture.manifest[0]["status"] == "ok"
    assert capture.manifest[0]["file"] == "dark/cockpit_nav_dropdown.png"
    assert capture.manifest[0]["opened"] is True

    capture.manifest.clear()
    closed_page = _CockpitPage(menu_visible=False)
    closed_context = _OnePageContext(closed_page)
    asyncio.run(capture._cockpit_popup(
        closed_context, "dark", "Cockpit nav dropdown", (trigger,),
        layout="top", visible_selector=menu,
    ))
    assert closed_context.new_page_calls == 1
    assert closed_page.clicks == [trigger]
    assert closed_page.screenshots == []
    assert closed_page.closes == 1
    assert len(capture.manifest) == 1
    assert capture.manifest[0]["status"] == "error"
    assert capture.manifest[0]["file"] is None
    assert capture.manifest[0]["err"] == 1
    assert capture.manifest[0]["error"] == (
        "Cockpit nav dropdown stayed closed after trigger")


def test_manifest_validator_accepts_exact_population_and_refuses_bad_evidence(
        monkeypatch, tmp_path):
    capture = _load_capture(monkeypatch)
    contract = capture.capture_manifest_contract
    valid = _valid_rows(contract)
    assert len(valid) == 104
    assert len({contract.manifest_key(row) for row in valid}) == 104
    assert contract.validate_manifest(valid) == valid

    cases = []
    missing = valid[:-1]
    assert len(missing) == 103
    cases.append((missing, "missing 1 expected row"))

    duplicate = [*valid, dict(valid[0])]
    assert len(duplicate) == 105
    cases.append((duplicate, "duplicate 1 row"))

    failed = [dict(row) for row in valid]
    failed[0].update(status="error", file=None, err=1,
                     error="row-309 negative-control failure")
    assert sum(row["status"] == "error" for row in failed) == 1
    cases.append((failed, "failed 1 row"))

    for rows, diagnostic in cases:
        with pytest.raises(contract.ManifestContractError, match=diagnostic):
            contract.validate_manifest(rows)

    unavailable = tmp_path / "absent-manifest.json"
    assert not unavailable.exists()
    with pytest.raises(
        contract.ManifestContractError,
        match="manifest measurement unavailable",
    ):
        contract.load_manifest(unavailable)


def test_navigator_refuses_missing_row_before_reading_images(monkeypatch, tmp_path):
    capture = _load_capture(monkeypatch)
    rows = _valid_rows(capture.capture_manifest_contract)
    missing = rows.pop()
    assert len(rows) == 103
    assert missing["route"] == "Cockpit nav dropdown"
    (tmp_path / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    output = tmp_path / "functional.html"

    env = os.environ.copy()
    env["BD_CAPTURE_DIR"] = str(tmp_path)
    env["BD_NAVIGATOR_OUT"] = str(output)
    proc = subprocess.run(
        [sys.executable, str(_NAVIGATOR)],
        cwd=_REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 2
    assert "CAPTURE MANIFEST UNKNOWN" in proc.stderr
    assert "missing 1 expected row" in proc.stderr
    assert "Cockpit nav dropdown" in proc.stderr
    assert not output.exists()


def test_navigator_reports_unavailable_image_as_unknown(monkeypatch, tmp_path):
    capture = _load_capture(monkeypatch)
    rows = _valid_rows(capture.capture_manifest_contract)
    assert len(rows) == 104
    first_image = tmp_path / rows[0]["file"]
    assert not first_image.exists()
    (tmp_path / "manifest.json").write_text(json.dumps(rows), encoding="utf-8")
    output = tmp_path / "functional.html"

    env = os.environ.copy()
    env["BD_CAPTURE_DIR"] = str(tmp_path)
    env["BD_NAVIGATOR_OUT"] = str(output)
    proc = subprocess.run(
        [sys.executable, str(_NAVIGATOR)],
        cwd=_REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert proc.returncode == 2
    assert "CAPTURE MANIFEST UNKNOWN: image measurement unavailable" in proc.stderr
    assert str(first_image) in proc.stderr
    assert not output.exists()


def test_transform_control_imports_capture_without_asserting_completeness(monkeypatch):
    """Named transform control: import alone must not catch a valid mutant."""
    capture = _load_capture(monkeypatch)
    assert capture.__name__ == "row_309_capture_all"
