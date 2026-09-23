"""Tests for Row 1048: Standard Web Dialog & Notice Acknowledgment Handler.

Validates automated detection and acknowledgment of standard modal dialogs,
consent banners, cookie notices, and informational overlays in web session workflows,
with strict age-verification safety gating and multi-handle deduplication.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import unittest
from typing import Any
from unittest.mock import MagicMock


class TestRow1048WebDialogNoticeAcknowledgment(unittest.TestCase):
    """Test suite for Row 1048 standard web dialog and notice acknowledgment."""

    def test_positive_control(self) -> None:
        """Positive control: verify existing detect module capabilities exist."""
        import bulk_downloader.detect as detect
        self.assertTrue(callable(getattr(detect, "parse_duration_seconds", None)))

    def test_row1048_capability_present(self) -> None:
        """RED test: verify row 1048 capability is exposed on bulk_downloader.detect."""
        import bulk_downloader.detect as detect
        self.assertTrue(
            hasattr(detect, "acknowledge_web_dialog_notices"),
            "Row 1048 capability missing: bulk_downloader.detect has no acknowledge_web_dialog_notices",
        )

    def test_dialog_type_classification(self) -> None:
        """Verify categorization of notice and dialog types based on text/selectors."""
        from bulk_downloader.web_dialog import DialogNoticeType, classify_dialog_notice

        self.assertEqual(
            classify_dialog_notice("We use cookies to improve your experience. Accept all cookies."),
            DialogNoticeType.COOKIE_CONSENT,
        )
        self.assertEqual(
            classify_dialog_notice("Please review our updated Terms of Service and Privacy Policy."),
            DialogNoticeType.TERMS_PRIVACY,
        )
        self.assertEqual(
            classify_dialog_notice("You must be 18 years or older to enter this site. Confirm age."),
            DialogNoticeType.AGE_VERIFICATION,
        )
        self.assertEqual(
            classify_dialog_notice("Important notification: System maintenance scheduled."),
            DialogNoticeType.INFORMATIONAL_NOTICE,
        )
        self.assertEqual(
            classify_dialog_notice("Random unrelated paragraph text content."),
            DialogNoticeType.UNKNOWN,
        )
        self.assertEqual(
            classify_dialog_notice(""),
            DialogNoticeType.UNKNOWN,
        )

    def test_compound_cookie_and_age_gate_classified_as_age_verification(self) -> None:
        """Verify priority classification: age verification takes precedence over cookie consent (F3)."""
        from bulk_downloader.web_dialog import DialogNoticeType, classify_dialog_notice

        mixed_text = "Cookie notice. We use cookies. You must also confirm age to enter."
        self.assertEqual(
            classify_dialog_notice(mixed_text),
            DialogNoticeType.AGE_VERIFICATION,
        )

    def test_standard_selectors_present_and_pinned(self) -> None:
        """Verify standard selector rules cover common dialog containers and dismiss buttons (M3 anchor)."""
        from bulk_downloader.web_dialog import get_standard_dismiss_selectors, get_standard_dialog_selectors

        containers = get_standard_dialog_selectors()
        self.assertIn('[role="dialog"]', containers)
        self.assertIn('[aria-modal="true"]', containers)
        self.assertIn(".modal", containers)

        dismiss_selectors = get_standard_dismiss_selectors()
        self.assertTrue(any("accept" in s.lower() for s in dismiss_selectors))
        self.assertTrue(any("close" in s.lower() for s in dismiss_selectors))
        self.assertTrue(any("dismiss" in s.lower() for s in dismiss_selectors))
        # E2 (bounce): no catch-all that would match the first button of any modal
        self.assertNotIn("button", dismiss_selectors)
        self.assertNotIn('button[type="button"]', dismiss_selectors)

    def _one_dialog_page(self, text: str, pick: Any) -> tuple[Any, Any, Any]:
        mock_page = MagicMock()
        mock_dialog = MagicMock()
        mock_btn = MagicMock()
        mock_dialog.inner_text.return_value = text
        mock_dialog.query_selector.side_effect = lambda sel: mock_btn if pick(sel) else None
        mock_page.query_selector_all.side_effect = lambda sel: [mock_dialog] if sel == '[role="dialog"]' else []
        return mock_page, mock_dialog, mock_btn

    def test_bare_button_is_never_clicked_when_no_specific_selector_matches(self) -> None:
        """E2 (bounce): a dialog whose only button is a generic one ("Sign in", "Delete") is left alone."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        for text in ("Sign in to continue", "Delete this item permanently?",
                     "System notification: scheduled maintenance tonight."):
            with self.subTest(text=text):
                mock_page, _, mock_btn = self._one_dialog_page(
                    text, lambda sel: sel in ("button", 'button[type="button"]'))
                res = WebDialogNoticeHandler().process_dialogs(mock_page)
                self.assertTrue(res.success)
                self.assertEqual(res.acknowledged_count, 0)
                mock_btn.click.assert_not_called()

    def test_age_gates_in_other_phrasings_are_classified_and_not_clicked(self) -> None:
        """E1 (bounce): "21 or older" / "18+" / "adults only" / "adult content" are age gates."""
        from bulk_downloader.web_dialog import DialogNoticeType, WebDialogNoticeHandler, classify_dialog_notice

        for text in ("You must be 21 or older to enter. Adults only.",
                     "This site contains adult content. Are you 18+?",
                     "Adults only beyond this point.",
                     "21+ only. Enter?"):
            with self.subTest(text=text):
                self.assertEqual(classify_dialog_notice(text), DialogNoticeType.AGE_VERIFICATION)
                mock_page, _, mock_btn = self._one_dialog_page(text, lambda sel: True)
                res = WebDialogNoticeHandler().process_dialogs(mock_page)
                self.assertEqual(res.acknowledged_count, 0)
                mock_btn.click.assert_not_called()
        # negative control: numbers that are not age gates stay unclassified as age
        self.assertNotEqual(classify_dialog_notice("Save 18% today, 21 items left."),
                            DialogNoticeType.AGE_VERIFICATION)

    def test_only_cookie_and_informational_notices_are_clicked(self) -> None:
        """E1 (bounce): the click is gated on an allow-list of types; UNKNOWN and TERMS are never clicked."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        close = lambda sel: 'aria-label*="close"' in sel
        for text, expected in (("Random unrelated paragraph text content.", 0),
                               ("Please review our updated Terms of Service.", 0),
                               ("We use cookies. Accept cookies.", 1),
                               ("Important notice: maintenance tonight.", 1)):
            with self.subTest(text=text):
                mock_page, _, mock_btn = self._one_dialog_page(text, close)
                res = WebDialogNoticeHandler().process_dialogs(mock_page)
                self.assertEqual(res.acknowledged_count, expected)
                self.assertEqual(mock_btn.click.call_count, expected)

    def test_hidden_dialog_and_hidden_button_are_skipped_and_click_is_bounded(self) -> None:
        """E3 (bounce): hidden nodes are never clicked (no 30 s visibility wait); clicks carry a short timeout."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page, mock_dialog, mock_btn = self._one_dialog_page("We use cookies.", lambda sel: True)
        mock_dialog.is_visible.return_value = False
        res = WebDialogNoticeHandler().process_dialogs(mock_page)
        self.assertEqual(res.acknowledged_count, 0)
        mock_btn.click.assert_not_called()

        mock_page, _, mock_btn = self._one_dialog_page("We use cookies.", lambda sel: True)
        mock_btn.is_visible.return_value = False
        res = WebDialogNoticeHandler().process_dialogs(mock_page)
        self.assertEqual(res.acknowledged_count, 0)
        mock_btn.click.assert_not_called()

        # control: visible dialog + visible button -> clicked once, with a bounded timeout
        mock_page, _, mock_btn = self._one_dialog_page("We use cookies.", lambda sel: True)
        res = WebDialogNoticeHandler().process_dialogs(mock_page)
        self.assertEqual(res.acknowledged_count, 1)
        timeout = mock_btn.click.call_args.kwargs.get("timeout")
        self.assertIsInstance(timeout, (int, float))
        self.assertTrue(0 < timeout <= 5000, timeout)

    def test_page_without_query_api_reports_failure(self) -> None:
        """E4 (bounce): an object that cannot be queried is not reported as success."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        res = WebDialogNoticeHandler().process_dialogs(object())
        self.assertFalse(res.success)
        self.assertIn("query_selector_all", res.error)

    def test_age_verification_dialog_is_never_auto_clicked(self) -> None:
        """Verify age verification dialogs are NEVER automatically clicked or dismissed (F2)."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page = MagicMock()
        mock_dialog = MagicMock()
        mock_btn = MagicMock()

        mock_dialog.inner_text.return_value = "Age verification required. You must be over 18 to enter."
        mock_dialog.query_selector.return_value = mock_btn
        mock_page.query_selector_all.side_effect = lambda sel: [mock_dialog] if '[role="dialog"]' in sel else []

        handler = WebDialogNoticeHandler()
        res = handler.process_dialogs(mock_page)

        self.assertTrue(res.success)
        self.assertEqual(res.acknowledged_count, 0)
        mock_btn.click.assert_not_called()
        self.assertEqual(len(res.details), 1)
        self.assertEqual(res.details[0]["dialog_type"], "age_verification")
        self.assertFalse(res.details[0]["action_taken"])
        self.assertEqual(res.details[0]["reason"], "age_verification_requires_explicit_confirmation")

    def test_element_deduplication_across_distinct_handles(self) -> None:
        """Verify distinct handle objects representing the same underlying DOM element are deduplicated (F4)."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page = MagicMock()
        # Two distinct handle objects for the same underlying element
        mock_dialog1 = MagicMock()
        mock_dialog2 = MagicMock()
        mock_btn = MagicMock()

        mock_dialog1.inner_text.return_value = "We use cookies to enhance your visit. Accept cookies."
        mock_dialog1.query_selector.return_value = mock_btn

        mock_dialog2.inner_text.return_value = "We use cookies to enhance your visit. Accept cookies."
        mock_dialog2.query_selector.return_value = mock_btn

        # Selector 1 returns handle 1, Selector 2 returns handle 2
        mock_page.query_selector_all.side_effect = lambda sel: (
            [mock_dialog1] if sel == '[role="dialog"]' else ([mock_dialog2] if sel == '[aria-modal="true"]' else [])
        )

        handler = WebDialogNoticeHandler()
        res = handler.process_dialogs(mock_page)

        self.assertEqual(res.acknowledged_count, 1)
        self.assertEqual(len(res.details), 1)
        mock_btn.click.assert_called_once()

    def test_dismiss_selector_fallback_on_exception(self) -> None:
        """Verify fallback to subsequent dismiss selectors when an earlier selector raises an exception (F5)."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page = MagicMock()
        mock_dialog = MagicMock()
        stale_btn = MagicMock()
        stale_btn.click.side_effect = RuntimeError("Node is detached from document")
        fallback_btn = MagicMock()

        mock_dialog.inner_text.return_value = "Accept our cookie terms to continue."

        def selector_side_effect(sel: str) -> Any:
            if 'aria-label*="close"' in sel:
                return stale_btn
            if 'aria-label*="dismiss"' in sel:
                return fallback_btn
            return None

        mock_dialog.query_selector.side_effect = selector_side_effect
        mock_page.query_selector_all.side_effect = lambda sel: [mock_dialog] if '[role="dialog"]' in sel else []

        handler = WebDialogNoticeHandler()
        res = handler.process_dialogs(mock_page)

        self.assertTrue(res.success)
        self.assertEqual(res.acknowledged_count, 1)
        self.assertTrue(res.details[0]["action_taken"])
        self.assertIn('aria-label*="dismiss"', res.details[0]["button_selector"])
        stale_btn.click.assert_called_once()
        fallback_btn.click.assert_called_once()

    def test_mock_dialog_acknowledgment(self) -> None:
        """Verify acknowledgment engine successfully detects and interacts with dialog elements."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler, AcknowledgmentResult

        mock_page = MagicMock()
        mock_dialog = MagicMock()
        mock_button = MagicMock()

        mock_dialog.inner_text.return_value = "Accept all cookies to proceed."
        mock_dialog.query_selector.return_value = mock_button
        mock_page.query_selector_all.side_effect = lambda sel: [mock_dialog] if '[role="dialog"]' in sel else []

        handler = WebDialogNoticeHandler()
        result: AcknowledgmentResult = handler.process_dialogs(mock_page)

        self.assertTrue(result.success)
        self.assertEqual(result.acknowledged_count, 1)
        self.assertEqual(len(result.details), 1)
        self.assertEqual(result.details[0]["dialog_type"], "cookie_consent")
        mock_button.click.assert_called_once()

    def test_no_dialogs_present(self) -> None:
        """Verify graceful no-op when no dialogs are present in DOM."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page = MagicMock()
        mock_page.query_selector_all.return_value = []

        handler = WebDialogNoticeHandler()
        result = handler.process_dialogs(mock_page)

        self.assertTrue(result.success)
        self.assertEqual(result.acknowledged_count, 0)
        self.assertEqual(result.details, [])

    def test_max_dialogs_enforced(self) -> None:
        """Verify max_dialogs constraint halts further processing."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page = MagicMock()
        dialogs = []
        buttons = []
        for i in range(5):
            d = MagicMock()
            d.inner_text.return_value = f"Cookie notice {i}"
            b = MagicMock()
            d.query_selector.return_value = b
            dialogs.append(d)
            buttons.append(b)

        mock_page.query_selector_all.side_effect = lambda sel: dialogs if '[role="dialog"]' in sel else []

        handler = WebDialogNoticeHandler(max_dialogs=2)
        result = handler.process_dialogs(mock_page)

        self.assertEqual(result.acknowledged_count, 2)
        self.assertEqual(len(result.details), 2)
        self.assertEqual(buttons[0].click.call_count, 1)
        self.assertEqual(buttons[1].click.call_count, 1)
        self.assertEqual(buttons[2].click.call_count, 0)

    def test_text_content_fallback(self) -> None:
        """Verify text_content fallback when inner_text is absent."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        mock_page = MagicMock()
        mock_dialog = MagicMock(spec=["text_content", "query_selector"])
        mock_button = MagicMock()

        mock_dialog.text_content.return_value = "Please accept our cookie policy."
        mock_dialog.query_selector.return_value = mock_button
        mock_page.query_selector_all.side_effect = lambda sel: [mock_dialog] if '[role="dialog"]' in sel else []

        handler = WebDialogNoticeHandler()
        result = handler.process_dialogs(mock_page)

        self.assertEqual(result.acknowledged_count, 1)
        self.assertEqual(result.details[0]["dialog_type"], "cookie_consent")

    def test_none_page_handling(self) -> None:
        """Verify handling when page_or_dom is None."""
        from bulk_downloader.web_dialog import WebDialogNoticeHandler

        handler = WebDialogNoticeHandler()
        result = handler.process_dialogs(None)
        self.assertTrue(result.success)
        self.assertEqual(result.acknowledged_count, 0)

    def test_detect_integration(self) -> None:
        """Verify integration entry point on bulk_downloader.detect."""
        import bulk_downloader.detect as detect

        mock_page = MagicMock()
        mock_page.query_selector_all.return_value = []

        res = detect.acknowledge_web_dialog_notices(mock_page)
        self.assertTrue(res.get("success", False))
        self.assertEqual(res.get("acknowledged_count", -1), 0)

    def test_runner_browser_mixin_caller_integration(self) -> None:
        """Verify concrete caller in bulk_downloader.runner_browser.BrowserMixin (F1)."""
        from bulk_downloader.runner_browser import BrowserMixin

        class DummyRunner(BrowserMixin):
            def __init__(self, config: dict[str, Any]) -> None:
                self.config = config

        mock_page = MagicMock()
        mock_page.query_selector_all.return_value = []

        # Default OFF: returns None and does not perform dialog acknowledgment
        runner_off = DummyRunner(config={})
        res_off = runner_off.maybe_acknowledge_dialogs(mock_page)
        self.assertIsNone(res_off)

        # Enabled: invokes acknowledgment pipeline
        runner_on = DummyRunner(config={"acknowledge_dialogs": True})
        res_on = runner_on.maybe_acknowledge_dialogs(mock_page)
        self.assertIsNotNone(res_on)
        self.assertTrue(res_on.get("success", False))
        self.assertEqual(res_on.get("acknowledged_count", -1), 0)

    def test_a_detached_handle_falls_through_the_identity_probes_and_says_why(self) -> None:
        """ORDERS-0076 (DP-13 x3 in _get_element_identity_key): each probe swallowed its
        failure, so a handle that ended on the per-wrapper obj: key left no trace of why."""
        from bulk_downloader import web_dialog

        class _Detached:
            def get_attribute(self, name: str) -> Any:
                raise RuntimeError("element is detached from the DOM")

            def bounding_box(self) -> Any:
                raise RuntimeError("element is detached from the DOM")

            def inner_text(self) -> str:
                return "  I am 18 or older  "

        key = web_dialog._get_element_identity_key(_Detached())
        self.assertEqual(key, "text:I am 18 or older")
        reasons = list(getattr(web_dialog, "IDENTITY_PROBE_ERRORS", []))
        self.assertEqual(
            [r.split(":")[0] for r in reasons],
            ["get_attribute(data-bd-dedup-id)", "get_attribute(id)", "get_attribute(data-testid)", "bounding_box"],
            "the identity probes failed without a recorded reason: %r" % (reasons,))
        self.assertIn("RuntimeError: element is detached", reasons[0])

        good = MagicMock()
        good.get_attribute.return_value = "dlg-1"
        self.assertEqual(web_dialog._get_element_identity_key(good), "attr:data-bd-dedup-id:dlg-1")
        self.assertEqual(list(getattr(web_dialog, "IDENTITY_PROBE_ERRORS", [])), [],
                         "a clean probe must not report the previous call's failures")


if __name__ == "__main__":
    unittest.main()
