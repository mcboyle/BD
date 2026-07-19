"""Tests for the macro_recorder replay engine (Phase 186)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest


# ─── Test harness: per-test temp install dir ───────────────────────────

def _fresh_install_dir():
    """Per-test install dir keeps macro files from leaking between tests."""
    tmp = tempfile.mkdtemp(prefix="bd-macro-test-")
    # v3.66.9: snapshot env + modules so teardown_module can restore them.
    if not _ISOLATED_BD_SAVED:
        _ISOLATED_BD_SAVED["env"] = {
            k: os.environ.get(k) for k in ("BD_INSTALL_DIR",)
        }
        _ISOLATED_BD_SAVED["modules"] = {
            k: v for k, v in sys.modules.items()
            if k.startswith("bulk_downloader")
        }
    os.environ["BD_INSTALL_DIR"] = tmp
    # Re-import the module so it picks up the new BD_INSTALL_DIR
    for m in list(sys.modules):
        if m.startswith("bulk_downloader.macro_recorder"):
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


# ─── Mock page that records every Playwright call ─────────────────────


class _MockLocator:
    def __init__(self, sel, page):
        self.sel = sel
        self._page = page
        self.first = self

    def click(self, *, timeout=5000, force=False):
        self._page.calls.append(("click", self.sel, {"timeout": timeout,
                                                       "force": force}))

    def wait_for(self, *, state="visible", timeout=5000):
        self._page.calls.append(("wait_for", self.sel,
                                 {"state": state, "timeout": timeout}))

    def fill(self, text, *, timeout=5000):
        self._page.calls.append(("fill", self.sel,
                                 {"text": text, "timeout": timeout}))

    def type(self, text, *, timeout=5000, delay=0):
        self._page.calls.append(("type", self.sel,
                                 {"text": text, "delay": delay}))

    def focus(self, *, timeout=5000):
        self._page.calls.append(("focus", self.sel, {"timeout": timeout}))


class _MockKeyboard:
    def __init__(self, page):
        self._page = page

    def press(self, keys):
        self._page.calls.append(("press", keys, {}))


class _MockPage:
    """Records every call so tests can assert ordering + args."""

    def __init__(self):
        self.calls = []
        self.keyboard = _MockKeyboard(self)

    def locator(self, sel):
        return _MockLocator(sel, self)

    def evaluate(self, js):
        self.calls.append(("evaluate", js, {}))


class _BrokenPage(_MockPage):
    """Mock that raises on locator-based actions."""

    def locator(self, sel):
        class _Broken:
            first = None  # set below
            def click(self_inner, **kw):
                raise RuntimeError(f"element {sel} not found")
            def wait_for(self_inner, **kw):
                raise RuntimeError(f"element {sel} not found")
            def fill(self_inner, *a, **kw):
                raise RuntimeError(f"element {sel} not found")
            def type(self_inner, *a, **kw):
                raise RuntimeError(f"element {sel} not found")
            def focus(self_inner, **kw):
                raise RuntimeError(f"element {sel} not found")
        b = _Broken()
        b.first = b
        return b


# ─── Validation tests ─────────────────────────────────────────────────


class TestValidateMacro(unittest.TestCase):

    def setup_method(self, method=None):
        """Pytest-style setup. The project's run_tests.py harness
        invokes this, but unittest's setUp is also called when running
        via `python -m unittest`, so we wire both to the same impl."""
        _fresh_install_dir()
        from bulk_downloader import macro_recorder
        self.mr = macro_recorder

    setUp = setup_method

    def test_valid_macro_passes(self):
        ok, err = self.mr.validate_macro({"actions": [
            {"kind": "click", "selector": "#x"},
            {"kind": "wait", "for": ".y"},
            {"kind": "sleep", "ms": 100},
        ]})
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_not_a_dict_rejected(self):
        ok, err = self.mr.validate_macro([])
        self.assertFalse(ok)
        self.assertIn("dict", err)

    def test_missing_actions_rejected(self):
        ok, err = self.mr.validate_macro({})
        self.assertFalse(ok)
        self.assertIn("actions", err)

    def test_actions_not_list_rejected(self):
        ok, err = self.mr.validate_macro({"actions": "click"})
        self.assertFalse(ok)
        self.assertIn("list", err)

    def test_unknown_kind_rejected(self):
        ok, err = self.mr.validate_macro({"actions": [
            {"kind": "submit", "selector": "#x"},
        ]})
        self.assertFalse(ok)
        self.assertIn("submit", err)

    def test_click_missing_selector_rejected(self):
        ok, err = self.mr.validate_macro({"actions": [{"kind": "click"}]})
        self.assertFalse(ok)
        self.assertIn("missing selector", err)

    def test_wait_missing_for_rejected(self):
        ok, err = self.mr.validate_macro({"actions": [{"kind": "wait"}]})
        self.assertFalse(ok)
        self.assertIn("'for'", err)

    def test_press_missing_keys_rejected(self):
        ok, err = self.mr.validate_macro({"actions": [{"kind": "press"}]})
        self.assertFalse(ok)
        self.assertIn("keys", err)

    def test_scroll_bad_target_rejected(self):
        ok, err = self.mr.validate_macro({"actions": [
            {"kind": "scroll", "to": "sideways"},
        ]})
        self.assertFalse(ok)
        self.assertIn("scroll", err)

    def test_scroll_int_target_accepted(self):
        ok, err = self.mr.validate_macro({"actions": [
            {"kind": "scroll", "to": 500},
        ]})
        self.assertTrue(ok)


# ─── Replay tests ──────────────────────────────────────────────────────


class TestReplayMacro(unittest.TestCase):

    def setup_method(self, method=None):
        _fresh_install_dir()
        from bulk_downloader import macro_recorder
        self.mr = macro_recorder

    setUp = setup_method

    def test_empty_macro_succeeds(self):
        page = _MockPage()
        result = self.mr.replay_macro(page, {"actions": []})
        self.assertTrue(result["ok"])
        self.assertEqual(result["executed"], 0)
        self.assertIsNone(result["failed_at"])

    def test_each_action_kind_dispatches(self):
        page = _MockPage()
        macro = {"actions": [
            {"kind": "click", "selector": "#a"},
            {"kind": "wait", "for": ".b"},
            {"kind": "scroll", "to": "bottom"},
            {"kind": "type", "selector": "#c", "text": "hi"},
            {"kind": "sleep", "ms": 1},
            {"kind": "press", "keys": "Tab"},
        ]}
        result = self.mr.replay_macro(page, macro)
        self.assertTrue(result["ok"])
        self.assertEqual(result["executed"], 6)
        # Each call landed on the page (sleep doesn't go through page)
        self.assertEqual(len(page.calls), 5)
        kinds = [c[0] for c in page.calls]
        self.assertEqual(
            kinds, ["click", "wait_for", "evaluate", "fill", "press"])

    def test_scroll_top_uses_evaluate_with_zero(self):
        page = _MockPage()
        self.mr.replay_macro(page, {"actions": [
            {"kind": "scroll", "to": "top"},
        ]})
        self.assertEqual(page.calls[0][0], "evaluate")
        self.assertIn("0, 0", page.calls[0][1])

    def test_scroll_int_uses_evaluate_with_int(self):
        page = _MockPage()
        self.mr.replay_macro(page, {"actions": [
            {"kind": "scroll", "to": 750},
        ]})
        self.assertIn("750", page.calls[0][1])

    def test_type_clear_calls_fill_empty_first(self):
        page = _MockPage()
        self.mr.replay_macro(page, {"actions": [
            {"kind": "type", "selector": "#x", "text": "hi", "clear": True},
        ]})
        # Expect two fill calls — empty, then "hi"
        fills = [c for c in page.calls if c[0] == "fill"]
        self.assertEqual(len(fills), 2)
        self.assertEqual(fills[0][2]["text"], "")
        self.assertEqual(fills[1][2]["text"], "hi")

    def test_type_method_uses_type_when_requested(self):
        page = _MockPage()
        self.mr.replay_macro(page, {"actions": [
            {"kind": "type", "selector": "#x", "text": "hi",
             "method": "type", "delay_ms": 50},
        ]})
        types = [c for c in page.calls if c[0] == "type"]
        self.assertEqual(len(types), 1)
        self.assertEqual(types[0][2]["delay"], 50)

    def test_press_with_selector_focuses_first(self):
        page = _MockPage()
        self.mr.replay_macro(page, {"actions": [
            {"kind": "press", "keys": "Enter", "selector": "#search"},
        ]})
        # First call should be focus, second press
        self.assertEqual(page.calls[0][0], "focus")
        self.assertEqual(page.calls[0][1], "#search")
        self.assertEqual(page.calls[1][0], "press")

    def test_press_without_selector_just_presses(self):
        page = _MockPage()
        self.mr.replay_macro(page, {"actions": [
            {"kind": "press", "keys": "Escape"},
        ]})
        self.assertEqual(len(page.calls), 1)
        self.assertEqual(page.calls[0][0], "press")

    def test_failure_stops_replay_when_strict(self):
        page = _BrokenPage()
        result = self.mr.replay_macro(page, {"actions": [
            {"kind": "sleep", "ms": 1},
            {"kind": "click", "selector": "#fails"},
            {"kind": "sleep", "ms": 1},  # would run if strict=False
        ]}, strict=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], 1)
        self.assertEqual(result["executed"], 1)  # only the first sleep
        self.assertIn("element", result["error"])

    def test_failure_continues_when_not_strict(self):
        page = _BrokenPage()
        result = self.mr.replay_macro(page, {"actions": [
            {"kind": "sleep", "ms": 1},
            {"kind": "click", "selector": "#fails"},
            {"kind": "sleep", "ms": 1},
        ]}, strict=False)
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], 1)
        # both sleeps ran; click failed in the middle
        self.assertEqual(result["executed"], 2)

    def test_invalid_kind_caught_at_replay_time(self):
        page = _MockPage()
        result = self.mr.replay_macro(page, {"actions": [
            {"kind": "submit"},
        ]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_at"], 0)

    def test_each_step_records_took_ms(self):
        page = _MockPage()
        result = self.mr.replay_macro(page, {"actions": [
            {"kind": "sleep", "ms": 10},
            {"kind": "click", "selector": "#x"},
        ]})
        self.assertEqual(len(result["steps"]), 2)
        for step in result["steps"]:
            self.assertIn("took_ms", step)
            self.assertIsInstance(step["took_ms"], int)
            self.assertGreaterEqual(step["took_ms"], 0)

    def test_replay_persists_outcome_to_metadata(self):
        """When site_id+name passed, replay_macro updates
        metadata.last_replay_* via mark_replay_result."""
        # First record a macro to disk
        self.mr.record_macro("test_site", "my_macro", [
            {"kind": "click", "selector": "#x"},
        ])
        page = _MockPage()
        m = self.mr.get_macro("test_site", "my_macro")
        self.mr.replay_macro(page, m,
                             site_id="test_site", name="my_macro")
        # Re-read; metadata should reflect the replay
        after = self.mr.get_macro("test_site", "my_macro")
        self.assertTrue(after["metadata"]["last_replay_ok"])
        self.assertIsNotNone(after["metadata"]["last_replay_ts"])

    def test_replay_persists_failure_outcome(self):
        self.mr.record_macro("test_site", "fail_macro", [
            {"kind": "click", "selector": "#never"},
        ])
        page = _BrokenPage()
        m = self.mr.get_macro("test_site", "fail_macro")
        self.mr.replay_macro(page, m,
                             site_id="test_site", name="fail_macro")
        after = self.mr.get_macro("test_site", "fail_macro")
        self.assertFalse(after["metadata"]["last_replay_ok"])
        self.assertIn("element", after["metadata"]["last_replay_message"])

    def test_sleep_caps_at_60_seconds(self):
        """Pathological ms value can't hang the worker forever.

        v3.66.9: this used to actually call time.sleep(60), which is
        wall-clock-dependent and trips pytest --timeout=60. We just
        want to verify the clamp logic, so mock sleep and inspect what
        the action handler asked it to wait for.
        """
        from unittest import mock
        page = _MockPage()
        with mock.patch("bulk_downloader.macro_recorder.time.sleep") as m_sleep:
            self.mr.replay_macro(page, {"actions": [
                {"kind": "sleep", "ms": 60001},  # 60.001s requested
            ]})
        # The clamp is max(0, min(ms, 60_000)) -> 60.0s exactly.
        m_sleep.assert_called_once()
        slept_seconds = m_sleep.call_args.args[0]
        self.assertEqual(slept_seconds, 60.0)


# ─── Runner-side wiring smoke ──────────────────────────────────────────


class TestRunnerWiring(unittest.TestCase):
    """The runner has a pre_download_macro hook that calls
    macro_recorder.replay_macro. Confirm the source has the wire."""

    def test_runner_imports_macro_recorder(self):
        from pathlib import Path
        runner_src = (Path(__file__).parent.parent
                      / "bulk_downloader" / "runner.py").read_text(encoding="utf-8")
        self.assertIn("from . import macro_recorder", runner_src,
                      "runner.py should import macro_recorder for "
                      "pre_download_macro replay")
        self.assertIn("pre_download_macro", runner_src)
        self.assertIn("replay_macro", runner_src)

    def test_runner_uses_failopen_pattern(self):
        """Macro failures must not break downloads."""
        from pathlib import Path
        runner_src = (Path(__file__).parent.parent
                      / "bulk_downloader" / "runner.py").read_text(encoding="utf-8")
        # Find the pre_download_macro block and confirm it's inside
        # a try/except with non-fatal logging
        idx = runner_src.find("pre_download_macro")
        block = runner_src[idx:idx + 2500]
        self.assertIn("try:", block)
        self.assertIn("except Exception", block)
        self.assertIn("sys.stderr.write", block)


if __name__ == "__main__":
    unittest.main()
