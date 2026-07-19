"""Tests for the macro replay engine (Phase 186 second half).

Flat pytest-style functions — the project's run_tests.py picks up
`def test_*` and doesn't support unittest.TestCase classes. Mock
Page captures method calls; real Playwright integration is left
for the E2E suite.
"""
from __future__ import annotations

import os
import sys
import tempfile


def _setup_install_dir():
    """Fresh BD install dir + reset module cache. Called at the top
    of every test that touches BD state."""
    tmp = tempfile.mkdtemp(prefix="bd-macro-replay-")
    # v3.66.9: snapshot env + modules so teardown_module can restore them.
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED["env"] = {
            k: os.environ.get(k)
            for k in ("BD_INSTALL_DIR", "BD_DISABLE_KEEPALIVE")
        }
        _ISOLATED_BD_SAVED["modules"] = {
            k: v for k, v in sys.modules.items()
            if k.startswith("bulk_downloader")
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    for m in list(sys.modules):
        if m.startswith("bulk_downloader"):
            del sys.modules[m]
    return tmp


_ISOLATED_BD_SAVED = {}


def teardown_module(module):
    """v3.66.9: restore env + sys.modules after this file's tests."""
    if not _ISOLATED_BD_SAVED:
        return
    for k, v in _ISOLATED_BD_SAVED["env"].items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for m in [k for k in list(sys.modules)
              if k.startswith("bulk_downloader")]:
        del sys.modules[m]
    sys.modules.update(_ISOLATED_BD_SAVED["modules"])
    # FOUND-4: re-bind restored submodules onto their parent package so a
    # later `from . import macro_recorder` resolves to the restored object
    # rather than a stale one left in the package's attribute namespace.
    for name, mod in _ISOLATED_BD_SAVED["modules"].items():
        parent, _, child = name.rpartition(".")
        if parent and parent in sys.modules:
            setattr(sys.modules[parent], child, mod)
    _ISOLATED_BD_SAVED.clear()


# ── Mock Page ─────────────────────────────────────────────────────────

class _MockPage:
    """Minimal Playwright Page stand-in. Records calls; lets tests
    arrange method failures via fail_next()."""

    def __init__(self):
        self.calls = []  # list of (method_name, args, kwargs)
        self._next_failures = {}

    def fail_next(self, method, exc):
        self._next_failures[method] = exc

    def _record(self, method, *args, **kwargs):
        self.calls.append((method, args, kwargs))
        if method in self._next_failures:
            exc = self._next_failures.pop(method)
            raise exc

    def click(self, selector, *, timeout=None, button="left"):
        self._record("click", selector, timeout=timeout, button=button)

    def wait_for_selector(self, selector, *, timeout=None,
                           state="attached"):
        self._record("wait_for_selector", selector,
                     timeout=timeout, state=state)

    def evaluate(self, script):
        self._record("evaluate", script)

    def fill(self, selector, text, *, timeout=None):
        self._record("fill", selector, text, timeout=timeout)

    def type(self, selector, text, *, delay=0, timeout=None):
        self._record("type", selector, text,
                     delay=delay, timeout=timeout)

    def wait_for_timeout(self, ms):
        self._record("wait_for_timeout", ms)

    def locator(self, selector):
        return _MockLocator(self, selector)

    @property
    def keyboard(self):
        return _MockKeyboard(self)


class _MockLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def press(self, key, *, timeout=None):
        self._page._record("locator_press", self._selector, key,
                            timeout=timeout)

    def scroll_into_view_if_needed(self, *, timeout=None):
        self._page._record("scroll_into_view", self._selector,
                            timeout=timeout)


class _MockKeyboard:
    def __init__(self, page):
        self._page = page

    def press(self, key):
        self._page._record("keyboard_press", key)


# ── Click action ──────────────────────────────────────────────────────

def test_click_basic():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "click", "selector": "#submit"}])
    assert result["ok"]
    assert result["completed"] == 1
    assert page.calls[0][0] == "click"
    assert page.calls[0][1] == ("#submit",)


def test_click_missing_selector():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, [{"kind": "click"}])
    assert not result["ok"]
    assert result["failed_idx"] == 0
    assert result["failed_kind"] == "click"
    assert "missing selector" in result["error"]


def test_click_button_modifier():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "click", "selector": "a", "button": "right"}])
    assert page.calls[0][2]["button"] == "right"


def test_click_invalid_button_falls_back_to_left():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "click", "selector": "a",
          "button": "MIDDLE-CLICKY"}])
    assert page.calls[0][2]["button"] == "left"


def test_click_custom_timeout():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "click", "selector": "#x", "timeout_ms": 30000}])
    assert page.calls[0][2]["timeout"] == 30000


def test_click_uses_default_timeout_when_unspecified():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "click", "selector": "#x"}])
    # Default for click is 10000 ms per DEFAULT_TIMEOUTS
    assert page.calls[0][2]["timeout"] == 10000


# ── Wait action ───────────────────────────────────────────────────────

def test_wait_basic():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "wait", "for": "#video-player"}])
    assert result["ok"]
    assert page.calls[0][0] == "wait_for_selector"
    assert page.calls[0][1] == ("#video-player",)


def test_wait_accepts_selector_field_too():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "wait", "selector": "#video"}])
    assert page.calls[0][1] == ("#video",)


def test_wait_missing_target():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, [{"kind": "wait"}])
    assert not result["ok"]
    assert "missing" in result["error"]


def test_wait_custom_state():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "wait", "for": "#x", "state": "visible"}])
    assert page.calls[0][2]["state"] == "visible"


def test_wait_default_state_is_attached():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "wait", "for": "#x"}])
    assert page.calls[0][2]["state"] == "attached"


# ── Scroll action ─────────────────────────────────────────────────────

def test_scroll_bottom():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "scroll", "to": "bottom"}])
    assert result["ok"]
    assert page.calls[0][0] == "evaluate"
    assert "scrollHeight" in page.calls[0][1][0]


def test_scroll_top():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page, [{"kind": "scroll", "to": "top"}])
    assert "scrollTo(0, 0)" in page.calls[0][1][0]


def test_scroll_to_y():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page, [{"kind": "scroll", "y": 1200}])
    assert "1200" in page.calls[0][1][0]


def test_scroll_y_invalid():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "scroll", "y": "not-a-number"}])
    assert not result["ok"]
    assert "int" in result["error"]


def test_scroll_to_selector():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "scroll", "selector": "#footer"}])
    assert page.calls[0][0] == "scroll_into_view"


def test_scroll_missing_target():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, [{"kind": "scroll"}])
    assert not result["ok"]


# ── Type action ───────────────────────────────────────────────────────

def test_type_uses_fill_by_default():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "type", "selector": "#user", "text": "alice"}])
    assert page.calls[0][0] == "fill"
    assert page.calls[0][1] == ("#user", "alice")


def test_type_uses_type_when_delay_set():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "type", "selector": "#pw",
          "text": "secret", "delay_ms": 50}])
    assert page.calls[0][0] == "type"
    assert page.calls[0][2]["delay"] == 50


def test_type_missing_selector():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "type", "text": "x"}])
    assert not result["ok"]


def test_type_missing_text():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "type", "selector": "#x"}])
    assert not result["ok"]


def test_type_coerces_non_string_text():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "type", "selector": "#x", "text": 12345}])
    assert page.calls[0][1] == ("#x", "12345")


# ── Sleep action ──────────────────────────────────────────────────────

def test_sleep_basic():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "sleep", "ms": 100}])
    assert result["ok"]
    assert page.calls[0][0] == "wait_for_timeout"
    assert page.calls[0][1] == (100,)


def test_sleep_zero_is_noop():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, [{"kind": "sleep", "ms": 0}])
    assert result["ok"]
    assert page.calls == []


def test_sleep_negative_is_noop():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "sleep", "ms": -500}])
    assert result["ok"]
    assert page.calls == []


def test_sleep_caps_at_30s():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "sleep", "ms": 60000}])
    assert not result["ok"]
    assert "30s cap" in result["error"]


def test_sleep_non_int():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "sleep", "ms": "soon"}])
    assert not result["ok"]


# ── Press action ──────────────────────────────────────────────────────

def test_press_keyboard_global():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "press", "key": "Enter"}])
    assert page.calls[0][0] == "keyboard_press"
    assert page.calls[0][1] == ("Enter",)


def test_press_on_selector():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    mp.replay_on_page(page,
        [{"kind": "press", "key": "Tab",
          "selector": "#search-input"}])
    assert page.calls[0][0] == "locator_press"
    assert page.calls[0][1] == ("#search-input", "Tab")


def test_press_missing_key():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, [{"kind": "press"}])
    assert not result["ok"]


# ── Replay loop semantics ─────────────────────────────────────────────

def test_empty_actions_is_ok():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, [])
    assert result["ok"]
    assert result["action_count"] == 0
    assert result["completed"] == 0


def test_non_list_actions_returns_error():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page, "not-a-list")
    assert not result["ok"]
    assert "list" in result["error"]


def test_multi_action_sequence():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    actions = [
        {"kind": "click", "selector": "#age-gate-yes"},
        {"kind": "wait", "for": "#video-player"},
        {"kind": "scroll", "to": "bottom"},
        {"kind": "sleep", "ms": 100},
    ]
    result = mp.replay_on_page(page, actions)
    assert result["ok"]
    assert result["completed"] == 4
    assert len(page.calls) == 4


def test_failure_stops_replay_and_records_index():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    page.fail_next("wait_for_selector",
                    RuntimeError("element never appeared"))
    actions = [
        {"kind": "click", "selector": "#a"},
        {"kind": "wait", "for": "#b"},
        {"kind": "click", "selector": "#c"},  # never executes
    ]
    result = mp.replay_on_page(page, actions)
    assert not result["ok"]
    assert result["completed"] == 1
    assert result["failed_idx"] == 1
    assert result["failed_kind"] == "wait"
    # Third action MUST NOT have executed
    assert len(page.calls) == 2


def test_unknown_kind_fails_fast():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "click", "selector": "#a"},
         {"kind": "teleport"}])
    assert not result["ok"]
    assert result["failed_idx"] == 1
    assert "unknown action kind" in result["error"]


def test_non_dict_action_fails_with_index():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "click", "selector": "#a"},
         "this is a string, not a dict"])
    assert not result["ok"]
    assert result["failed_idx"] == 1


def test_progress_callback_invoked_per_action():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    observed = []
    actions = [
        {"kind": "click", "selector": "#a"},
        {"kind": "sleep", "ms": 1},
    ]
    mp.replay_on_page(page, actions,
        on_progress=lambda i, a: observed.append((i, a.get("kind"))))
    assert observed == [(0, "click"), (1, "sleep")]


def test_progress_callback_exception_doesnt_break_replay():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    def bad(idx, action):
        raise ValueError("progress bug")
    result = mp.replay_on_page(page,
        [{"kind": "click", "selector": "#a"}],
        on_progress=bad)
    assert result["ok"]


def test_action_count_tracked_on_failure():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    page.fail_next("click", RuntimeError("nope"))
    result = mp.replay_on_page(page,
        [{"kind": "click", "selector": "#a"},
         {"kind": "click", "selector": "#b"}])
    assert result["action_count"] == 2
    assert result["completed"] == 0


def test_duration_seconds_present():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    page = _MockPage()
    result = mp.replay_on_page(page,
        [{"kind": "sleep", "ms": 1}])
    assert "duration_seconds" in result
    assert isinstance(result["duration_seconds"], float)


# ── Standalone replay (storage integration) ───────────────────────────

def test_standalone_missing_macro_returns_clean_shape():
    _setup_install_dir()
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import macro_replay as mp
    result = mp.replay_standalone("nonexistent_site",
                                   "nonexistent_macro")
    assert not result["ok"]
    assert result["error"] == "macro not found"
    assert result["site_id"] == "nonexistent_site"
    assert result["macro_name"] == "nonexistent_macro"
    assert result["completed"] == 0


# ── Module-level helpers ──────────────────────────────────────────────

def test_status_dict_shape():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    d = mp.status_dict()
    assert "playwright_available" in d
    assert "max_replay_seconds" in d
    assert "supported_kinds" in d
    for k in ("click", "wait", "scroll", "type", "sleep", "press"):
        assert k in d["supported_kinds"]


def test_is_playwright_available_returns_bool():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    v = mp.is_playwright_available()
    assert isinstance(v, bool)


# ── API endpoint integration ──────────────────────────────────────────

def test_replay_status_endpoint():
    _setup_install_dir()
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import app as a
    client = a.app.test_client()
    r = client.get("/api/macros/replay_status")
    assert r.status_code == 200
    d = r.get_json()
    assert "playwright_available" in d
    assert "supported_kinds" in d


def test_replay_missing_macro_returns_404():
    _setup_install_dir()
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")
    csrf = client.get("/api/csrf").get_json()["csrf_token"]
    r = client.post(
        "/api/macros/replay/nosuch/nope",
        json={"start_url": "about:blank"},
        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 404
    d = r.get_json()
    assert not d["ok"]
    assert d["error"] == "macro not found"


def test_replay_csrf_required():
    """When the BD test client has no authenticated session, CSRF is
    a no-op (it only fires for cookie-authenticated requests). This
    test stands in as a smoke check that the endpoint route is
    registered; the real CSRF check happens at the cookie layer
    which is exercised by test_security.py."""
    _setup_install_dir()
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import app as a
    client = a.app.test_client()
    r = client.post(
        "/api/macros/replay/site/macro",
        json={"start_url": "about:blank"})
    # Route is registered (not 404 from Flask, only the "macro not
    # found" 404 from inside the handler) — accept both since the
    # macro storage is empty
    assert r.status_code in (200, 400, 401, 403, 404)
    # Body should be JSON — confirms the handler ran (not a Flask
    # default 404 page)
    d = r.get_json()
    assert d is not None
    assert "ok" in d or "error" in d


# ── Module surface assertions ─────────────────────────────────────────

def test_public_functions_exist():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    for name in ("replay_on_page", "replay_standalone",
                 "is_playwright_available", "status_dict",
                 "ReplayError"):
        assert hasattr(mp, name), f"macro_replay missing {name}"


def test_executors_cover_all_documented_kinds():
    _setup_install_dir()
    from bulk_downloader import macro_replay as mp
    from bulk_downloader.macro_recorder import _VALID_KINDS
    executor_kinds = set(mp._EXECUTORS.keys())
    assert executor_kinds == _VALID_KINDS, (
        f"executor coverage mismatch: have {executor_kinds}, "
        f"storage accepts {_VALID_KINDS}")


# ── Runner integration source check ───────────────────────────────────

def test_runner_imports_macro_replay_in_process_one():
    """Source-level check: the runner's _process_one wires
    pre_download_macro via macro_recorder. The hook is placed AFTER
    the JD/qB short-circuits + the page.goto, so the window has to
    be generous — ~12K chars from the function header.

    v3.46.0: the wiring was simplified to call
    `macro_recorder.replay_macro` directly rather than a separate
    `macro_replay` module. Test updated to match.

    v3.66.692: extract the REAL _process_one body (header to the next
    method def) instead of a fixed ~12K-char window. The window was a
    brittle heuristic -- a legitimate dispatch insertion (e.g. the
    plugin-extractor branch) pushed `replay_macro` past 12000 chars even
    though the wiring is intact. The three markers are the actual
    assertion; the extraction just shouldn't depend on byte offsets."""
    from pathlib import Path
    import bulk_downloader
    runner_src = (Path(bulk_downloader.__file__).parent
                  / "runner.py").read_text(encoding="utf-8")
    pos = runner_src.find("def _process_one(self,browser,url")
    assert pos > 0
    nxt = runner_src.find("\n    def ", pos + 1)   # next method def
    body = runner_src[pos:nxt if nxt > 0 else len(runner_src)]
    assert "pre_download_macro" in body
    assert "macro_recorder" in body
    assert "replay_macro" in body
