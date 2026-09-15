"""Row 722 G25a/G25b: an invalid config selector is rejected at PUT time and,
if it ever reaches a worker, is a needs_review outcome carrying the FULL
selector -- never a "Retry N/2 in 10m/1h" transient.

Live (dfxtra f6322017 / evilangel bd31142f): PUT dl_selector
`a.VideoJSPlayer-DownloadOption-Link[href*=2160p]` (unquoted value, not a CSS
identifier) was accepted; the worker died with
`Locator.count: SyntaxError: Failed to execute 'querySelectorAll' ...`, retried
10m then 1h, and the state message truncated the selector at ~40 chars.
"""
import json
import os
import threading

import pytest

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.bd_module_wipe

BAD = 'a.VideoJSPlayer-DownloadOption-Link[href*=2160p]'
GOOD = 'a.VideoJSPlayer-DownloadOption-Link[href*="2160p"]'
LIVE_ERROR = (
    "Locator.count: SyntaxError: Failed to execute 'querySelectorAll' on "
    f"'Document': '{BAD}' is not a valid selector.\n"
    "Call log:\n  - waiting for locator(...)")


# ── unit: the validator ────────────────────────────────────────────────────────
def test_validator_rejects_unquoted_non_identifier_attribute_value():
    from bulk_downloader import site_editor as se
    errs = se.validate_selector_updates({"dl_selector": BAD})
    assert "dl_selector" in errs, errs
    assert "unquoted attribute value '2160p'" in errs["dl_selector"], errs


@pytest.mark.parametrize("sel", [
    GOOD, "a[href*='2160p']", "a[href*=mp4]", "input[type=submit]",
    "div.download button", 'button:has-text("Download")', ':text-is("4K")',
    "a.dl:visible", "xpath=//a[@href='x']", "//a[contains(@href,'2160p')]",
    "css=a.dl >> text=Download", "#gate .close, button.accept", "",
])
def test_validator_accepts_valid_css_and_playwright_only_syntax(sel):
    from bulk_downloader import site_editor as se
    assert se.validate_selector_updates({"dl_selector": sel}) == {}


def test_validator_checks_dismiss_lines_and_comma_items_individually():
    from bulk_downloader import site_editor as se
    assert se.validate_selector_updates(
        {"dismiss_selectors": "# comment\nbutton.ok\n"}) == {}
    assert "dismiss_selectors" in se.validate_selector_updates(
        {"dismiss_selectors": "button.ok\na[href*=2160p]\n"})
    assert "trigger_selector" in se.validate_selector_updates(
        {"trigger_selector": "button.ok, a[href$=.mp4]"})


# ── PUT boundary ───────────────────────────────────────────────────────────────
def _boot_with_site():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    a.SITES_FILE.write_text(json.dumps({"demo": {
        "name": "Demo", "max_concurrent": 4, "wait": 5,
        "dl_selector": "a.original"}}), encoding="utf-8")
    a._load_sites_config()
    a.runners["demo"].update_config = lambda *_a, **_k: None
    return a, a.app.test_client()


def _disk(a):
    return json.loads(a.SITES_FILE.read_text(encoding="utf-8"))["demo"]


def test_put_invalid_dl_selector_is_400_with_field_and_reason_and_not_persisted():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"dl_selector": BAD})
    assert r.status_code == 400, (r.status_code, r.get_json())
    err = (r.get_json() or {}).get("error", "")
    assert err.startswith("invalid selector 'dl_selector': "), err
    assert "2160p" in err, err
    assert _disk(a)["dl_selector"] == "a.original"


def test_put_valid_quoted_selector_is_accepted_unchanged():
    a, c = _boot_with_site()
    r = c.put("/api/sites/demo", json={"dl_selector": GOOD,
                                        "trigger_selector": "div.download button"})
    assert r.status_code == 200, (r.status_code, r.get_json())
    assert _disk(a)["dl_selector"] == GOOD
    assert _disk(a)["trigger_selector"] == "div.download button"


# ── worker seam ────────────────────────────────────────────────────────────────
def _runner(config):
    from bulk_downloader.runner import SiteRunner
    r = type("WorkerSurface", (), {})()
    r.site_id = "row722"
    r.config = dict(config)
    r.jobs = {"https://members.example.test/video/1": {"retries": 0, "status": "running"}}
    r._lock = threading.Lock()
    r._RETRY_DELAYS_BY_KIND = SiteRunner._RETRY_DELAYS_BY_KIND
    r._SELECTOR_SYNTAX_MARKERS = SiteRunner._SELECTOR_SYNTAX_MARKERS
    for name in ("_classify_error", "_config_selector_syntax_error",
                 "_handle_failure", "_handle_failure_current",
                 "_publish_worker_exception"):
        setattr(r, name, getattr(SiteRunner, name).__get__(r))
    updates = []
    r._update_job = lambda *a, **k: updates.append((a, k))
    return r, updates


def _publish(monkeypatch, config, exc):
    from bulk_downloader import hooks, runner, runner_telemetry
    logged = []
    monkeypatch.setattr(runner_telemetry, "db_log", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(runner, "db_log", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(hooks, "fire_event", lambda *a, **k: None)
    r, updates = _runner(config)
    r._publish_worker_exception("https://members.example.test/video/1", exc)
    assert len(updates) == 1, updates
    return updates[0]


def test_selector_syntax_error_from_config_is_needs_review_with_full_selector(monkeypatch):
    args, kwargs = _publish(monkeypatch, {"name": "dfxtra", "max_retries": 2,
                                          "dl_selector": BAD}, Exception(LIVE_ERROR))
    url, status, message = args
    assert status == "needs_review", (status, message)
    assert message.startswith(f"invalid selector in site config: dl_selector='{BAD}'"), message
    assert "SyntaxError" in message
    assert "Retry" not in message


def test_selector_syntax_error_from_dismiss_line_names_that_field(monkeypatch):
    err = ("Locator.count: SyntaxError: Failed to execute 'querySelectorAll' on "
           "'Document': 'button[data-x=1]' is not a valid selector.")
    args, _ = _publish(monkeypatch, {"name": "s", "dl_selector": GOOD,
                                     "dismiss_selectors": "# c\nbutton.ok\nbutton[data-x=1]"},
                       Exception(err))
    assert args[1] == "needs_review"
    assert "dismiss_selectors='button[data-x=1]'" in args[2], args[2]


def test_genuine_transient_error_still_retries(monkeypatch):
    args, kwargs = _publish(monkeypatch, {"name": "s", "max_retries": 2,
                                          "dl_selector": BAD},
                            Exception("Locator.click: Target page, context or browser has been closed"))
    assert args[1] == "pending"
    assert args[2].startswith("Retry 1/2 in "), args[2]
    assert kwargs.get("retries") == 1


def test_selector_syntax_error_not_from_config_keeps_generic_path(monkeypatch):
    # A learned/template selector that fails to parse is not the operator's
    # config: the generic path stays (out of scope for G25b).
    err = ("Locator.count: SyntaxError: Failed to execute 'querySelectorAll' on "
           "'Document': 'a[href*=learned]' is not a valid selector.")
    args, _ = _publish(monkeypatch, {"name": "s", "max_retries": 2,
                                     "dl_selector": GOOD}, Exception(err))
    assert args[1] == "pending"
    assert args[2].startswith("Retry 1/2 in "), args[2]
