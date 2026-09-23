"""Standard Web Dialog & Notice Acknowledgment Handler (Row 1048).

Provides automated identification and acknowledgment of standard modal dialogs,
consent banners, cookie notices, and informational overlays in web session pipelines.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Standard dialog and modal container selectors
_STANDARD_DIALOG_SELECTORS: tuple[str, ...] = (
    '[role="dialog"]',
    '[aria-modal="true"]',
    ".modal",
    ".dialog",
    ".cookie-banner",
    ".consent-banner",
    ".notice-overlay",
    "#cookie-notice",
    "#consent-notice",
    ".cc-window",
)

# Standard action and dismissal selectors
_STANDARD_DISMISS_SELECTORS: tuple[str, ...] = (
    'button[aria-label*="close" i]',
    'button[aria-label*="dismiss" i]',
    'button[aria-label*="accept" i]',
    '[data-action*="accept" i]',
    '[data-action*="dismiss" i]',
    '[data-action*="agree" i]',
    ".cookie-banner-accept",
    ".consent-accept",
    ".modal-close",
    ".dialog-close",
)

# Upper bound (ms) on a single click: a node that is not actionable must not stall the
# navigation path for Playwright's 30 s default.
_CLICK_TIMEOUT_MS = 2000

# Classification regex patterns
_AGE_RE = re.compile(
    r"\b(age verification|18 years|over 18|legal age|confirm age|(?:18|21) or older|adults? only|adult content)\b"
    r"|\b(?:18|21)\s*\+",
    re.IGNORECASE,
)
_COOKIE_RE = re.compile(r"\b(cookie|cookies|tracking|gdpr|consent)\b", re.IGNORECASE)
_TERMS_RE = re.compile(r"\b(terms of service|terms of use|privacy policy|updated terms)\b", re.IGNORECASE)
_INFO_RE = re.compile(r"\b(notice|notification|announcement|maintenance|update)\b", re.IGNORECASE)


class DialogNoticeType(str, Enum):
    """Categorization of standard web notice and dialog types."""
    COOKIE_CONSENT = "cookie_consent"
    TERMS_PRIVACY = "terms_privacy"
    AGE_VERIFICATION = "age_verification"
    INFORMATIONAL_NOTICE = "informational_notice"
    UNKNOWN = "unknown"


# Only these types are ever acknowledged automatically; everything else (age gates, terms,
# unrecognised modals such as sign-in or delete confirmations) is left for the operator.
_AUTO_ACKNOWLEDGE_TYPES = frozenset({DialogNoticeType.COOKIE_CONSENT, DialogNoticeType.INFORMATIONAL_NOTICE})


def _is_hidden(elem: Any) -> bool:
    """True only when the handle reports itself not visible; handles without is_visible count as visible."""
    fn = getattr(elem, "is_visible", None)
    if not callable(fn):
        return False
    try:
        return fn() is False
    except Exception:
        return True


def classify_dialog_notice(text: str) -> DialogNoticeType:
    """Classifies a notice or dialog based on its textual content."""
    if not text or not isinstance(text, str):
        return DialogNoticeType.UNKNOWN

    cleaned = text.strip()
    # Safety first: age verification takes precedence over all other notice classifications
    if _AGE_RE.search(cleaned):
        return DialogNoticeType.AGE_VERIFICATION
    if _COOKIE_RE.search(cleaned):
        return DialogNoticeType.COOKIE_CONSENT
    if _TERMS_RE.search(cleaned):
        return DialogNoticeType.TERMS_PRIVACY
    if _INFO_RE.search(cleaned):
        return DialogNoticeType.INFORMATIONAL_NOTICE

    return DialogNoticeType.UNKNOWN


def get_standard_dialog_selectors() -> list[str]:
    """Returns list of standard container selectors used to identify dialogs."""
    return list(_STANDARD_DIALOG_SELECTORS)


def get_standard_dismiss_selectors() -> list[str]:
    """Returns list of standard action selectors used to acknowledge or dismiss dialogs."""
    return list(_STANDARD_DISMISS_SELECTORS)


# Why the last _get_element_identity_key() call fell through each probe it tried, e.g.
# ["get_attribute(id): Error: element is detached"]. A detached or crashing handle ends at the
# per-wrapper "obj:" key, which cannot dedupe across wrappers; this says why it got there.
IDENTITY_PROBE_ERRORS: list[str] = []


def _probe(label: str, fn: Any, *args: Any) -> Any:
    """fn(*args), or None with the failure recorded in IDENTITY_PROBE_ERRORS."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 -- a probe must not become the fault
        IDENTITY_PROBE_ERRORS.append(f"{label}: {type(exc).__name__}: {exc}")
        return None


def _get_element_identity_key(elem: Any) -> str:
    """Returns a stable deduplication key for an element handle across distinct handle wrappers."""
    IDENTITY_PROBE_ERRORS.clear()
    if elem is None:
        return ""
    # 1. Attribute / property checks
    for attr in ("data-bd-dedup-id", "id", "data-testid"):
        if hasattr(elem, "get_attribute") and callable(getattr(elem, "get_attribute")):
            val = _probe(f"get_attribute({attr})", elem.get_attribute, attr)
            if isinstance(val, str) and val.strip():
                return f"attr:{attr}:{val.strip()}"
    # 2. Bounding box if available
    if hasattr(elem, "bounding_box") and callable(getattr(elem, "bounding_box")):
        box = _probe("bounding_box", elem.bounding_box)
        if isinstance(box, dict) and all(isinstance(box.get(k), (int, float)) for k in ("x", "y", "width", "height")):
            return f"box:{box.get('x')}:{box.get('y')}:{box.get('width')}:{box.get('height')}"
    # 3. Text content or inner text
    for attr in ("inner_text", "text_content"):
        if hasattr(elem, attr):
            fn = _probe(attr, getattr, elem, attr)
            val = _probe(attr, fn) if callable(fn) else fn
            if isinstance(val, str) and val.strip():
                return f"text:{val.strip()[:100]}"
    return f"obj:{id(elem)}"



@dataclass
class AcknowledgmentResult:
    """Outcome of dialog notice detection and acknowledgment processing."""
    success: bool
    acknowledged_count: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


class WebDialogNoticeHandler:
    """Automated detector and acknowledgment engine for standard web dialogs and notices."""

    def __init__(
        self,
        dialog_selectors: list[str] | None = None,
        dismiss_selectors: list[str] | None = None,
        max_dialogs: int = 5,
    ) -> None:
        self.dialog_selectors = dialog_selectors or list(_STANDARD_DIALOG_SELECTORS)
        self.dismiss_selectors = dismiss_selectors or list(_STANDARD_DISMISS_SELECTORS)
        self.max_dialogs = max_dialogs

    def process_dialogs(self, page_or_dom: Any) -> AcknowledgmentResult:
        """Finds and acknowledges any matching standard dialogs on the page or DOM tree.

        Supports Playwright Page, ElementHandle, or mock DOM objects exposing
        query_selector and query_selector_all.
        """
        if page_or_dom is None:
            return AcknowledgmentResult(success=True, acknowledged_count=0)
        if not callable(getattr(page_or_dom, "query_selector_all", None)):
            return AcknowledgmentResult(success=False, error="unsupported page object: no query_selector_all")

        acknowledged = 0
        details: list[dict[str, Any]] = []

        try:
            seen_elements: set[Any] = set()
            seen_keys: set[str] = set()
            for sel in self.dialog_selectors:
                if acknowledged >= self.max_dialogs:
                    break

                try:
                    elements = page_or_dom.query_selector_all(sel) or []
                except Exception as ex:
                    logger.debug("Failed query_selector_all(%s): %s", sel, ex)
                    continue

                for elem in elements:
                    if acknowledged >= self.max_dialogs:
                        break
                    elem_key = _get_element_identity_key(elem)
                    if elem in seen_elements or (elem_key and elem_key in seen_keys):
                        continue
                    seen_elements.add(elem)
                    if elem_key:
                        seen_keys.add(elem_key)

                    if _is_hidden(elem):
                        details.append({"selector": sel, "action_taken": False, "reason": "hidden"})
                        continue

                    try:
                        text = ""
                        if hasattr(elem, "inner_text"):
                            text = elem.inner_text() or ""
                        elif hasattr(elem, "text_content"):
                            text = elem.text_content() or ""

                        notice_type = classify_dialog_notice(text)
                        action_taken = False
                        btn_clicked = None
                        elem_error = None

                        # Gating: Age verification dialogs must NEVER be automatically dismissed/clicked
                        if notice_type == DialogNoticeType.AGE_VERIFICATION:
                            logger.info(
                                "Skipping automated click for age verification notice (explicit operator confirmation required)"
                            )
                            detail_entry: dict[str, Any] = {
                                "selector": sel,
                                "dialog_type": notice_type.value,
                                "action_taken": False,
                                "reason": "age_verification_requires_explicit_confirmation",
                            }
                            details.append(detail_entry)
                            continue

                        if notice_type not in _AUTO_ACKNOWLEDGE_TYPES:
                            details.append({
                                "selector": sel,
                                "dialog_type": notice_type.value,
                                "action_taken": False,
                                "reason": "not_auto_acknowledged",
                            })
                            continue

                        # Search for dismiss / accept button
                        for d_sel in self.dismiss_selectors:
                            try:
                                btn = elem.query_selector(d_sel)
                                if btn and not _is_hidden(btn):
                                    btn.click(timeout=_CLICK_TIMEOUT_MS)
                                    action_taken = True
                                    btn_clicked = d_sel
                                    break
                            except Exception as click_err:
                                elem_error = str(click_err)
                                logger.debug("Dialog interaction error on %s: %s", d_sel, click_err)
                                continue  # Fallback to remaining dismiss selectors

                        detail_entry = {
                            "selector": sel,
                            "dialog_type": notice_type.value,
                            "action_taken": action_taken,
                        }
                        if btn_clicked:
                            detail_entry["button_selector"] = btn_clicked
                        if elem_error:
                            detail_entry["error"] = elem_error

                        details.append(detail_entry)
                        if action_taken:
                            acknowledged += 1

                    except Exception as err:
                        logger.debug("Failed inspecting element under %s: %s", sel, err)
                        details.append({"selector": sel, "error": str(err), "action_taken": False})

            return AcknowledgmentResult(success=True, acknowledged_count=acknowledged, details=details)

        except Exception as ex:
            logger.warning("WebDialogNoticeHandler process error: %s", ex)
            return AcknowledgmentResult(success=False, acknowledged_count=acknowledged, details=details, error=str(ex))


def acknowledge_web_dialogs(page_or_dom: Any) -> dict[str, Any]:
    """Convenience helper to acknowledge standard web dialogs and notices."""
    handler = WebDialogNoticeHandler()
    res = handler.process_dialogs(page_or_dom)
    return {
        "success": res.success,
        "acknowledged_count": res.acknowledged_count,
        "details": res.details,
        "error": res.error,
    }
