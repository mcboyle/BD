"""selector_lint — flag unsafe/generic selectors in learned configs and
reviewed/draft templates before they reach the download path.

A *row* selector like ``a[href]`` / ``[href]`` / ``a`` / ``*`` / ``button`` /
``.btn`` matches a large fraction of a page, so the download flow can pick a
nav link or the homepage and emit a bad ``download.bin``. This linter inspects
selectors (and whole templates / learned blocks) and returns structured issues.

Levels:
  * ``error``  — unsafe to use as a row selector (generic/root/nav); a reviewed
    template MUST NOT ship one (enforced by test).
  * ``warn``   — risky but sometimes legitimate (e.g. a bare ``button`` used as
    a *trigger*, where clicking the wrong button is recoverable).

This module is pure (no I/O, no browser) and is safe to call from onboarding,
template promotion, the dry-run inspector, or a status endpoint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List

# Selectors that are too generic to ever be a safe *row* (final download)
# selector: bare tag, attribute-only, universal, or a root/nav anchor.
_GENERIC_ROW_RE = re.compile(
    r"^\s*(?:"
    r"a|\*|button|\[href\]|a\[href\]|"            # bare tag / universal / href-only
    r"\.btn|\.button|\.link|"                      # generic class
    r"a\[href=['\"]?/['\"]?\]|"                    # explicit root link
    r"body\s+a|html\s+a|"                          # any anchor on the page
    r"a:link|a:visited"
    r")\s*$", re.I)

# Nav / account / search words appearing in a selector → the selector targets
# chrome, not a download control. Split into HARD chrome (almost always site
# chrome → blocking error) and SOFT/ambiguous words (browse/category/home/
# search/account/settings — these ARE the listing containers on content sites,
# so they downgrade to a non-blocking WARN; S1 false-positive fix).
_HARD_CHROME_RE = re.compile(
    r"(?:nav|navbar|navigation|menu-?bar|topbar|site-?header|site-?footer|"
    r"masthead|breadcrumb|footer\b|header\b|log[\s-]?in|signin|log[\s-]?out|"
    r"signout|logout|register|sign[\s-]?up|signup)", re.I)
_SOFT_NAV_RE = re.compile(
    r"(?:settings?|preferences?|account|search|browse|categor|home(?:page)?)", re.I)

# A scope prefix that makes an otherwise-generic anchor safe (it's confined to a
# dialog/modal/specific container, as reviewed templates do).
_SCOPED_RE = re.compile(
    r"\[role=['\"]?dialog['\"]?\]|\.modal|\.ant-modal|\.dialog|"
    r"\[aria-modal|download|resolution|\.player|\.video", re.I)

# Playback/media-control context. When a selector clearly targets a video
# player control (quality / resolution / playback / subtitles / etc.), words
# like "settings" or "menu" in it are playback chrome, not account/nav chrome,
# so the nav-words rule below must not flag it. (The generic-root checks above
# still apply — this only suppresses the nav-words false positive.)
_MEDIA_CTX_RE = re.compile(
    r"(?:\bquality\b|resolution|playback|bitrate|subtitle|caption|"
    r"theo(?:player)?|vjs|video[\s-]?player|player[\s-]?control)", re.I)


@dataclass
class Issue:
    level: str       # "error" | "warn"
    code: str        # machine code, e.g. "generic_row_selector"
    selector: str
    role: str        # "row" | "trigger" | "button" | "quality" | "learned"
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level, "code": self.code, "selector": self.selector,
            "role": self.role, "message": self.message,
        }


def _split_selector_list(selector: str) -> List[str]:
    # CSS selector lists are comma-separated; lint each clause.
    return [p.strip() for p in (selector or "").split(",") if p.strip()]


def lint_selector(selector: str, *, role: str = "row") -> List[Issue]:
    """Lint a single selector string (which may be a comma-separated list).

    ``role`` controls severity: a generic clause is an ``error`` for ``row``
    (final download target) but a ``warn`` for ``trigger`` (a click that opens
    a menu — picking the wrong one is recoverable).
    """
    issues: List[Issue] = []
    for clause in _split_selector_list(selector):
        scoped = bool(_SCOPED_RE.search(clause))
        if _GENERIC_ROW_RE.match(clause) and not scoped:
            lvl = "error" if role in ("row", "button", "learned") else "warn"
            issues.append(Issue(
                lvl, "generic_row_selector", clause, role,
                f"generic selector {clause!r} matches unrelated links; "
                f"scope it (e.g. inside a dialog/container) or target the "
                f"real download control"))
            continue
        if _HARD_CHROME_RE.search(clause) and not scoped and not _MEDIA_CTX_RE.search(clause):
            issues.append(Issue(
                "error" if role in ("row", "button") else "warn",
                "nav_selector", clause, role,
                f"selector {clause!r} targets nav/account/search chrome"))
        elif _SOFT_NAV_RE.search(clause) and not scoped and not _MEDIA_CTX_RE.search(clause):
            # ambiguous: on content/listing sites these ARE the row containers,
            # so flag (visible) but never block promotion.
            issues.append(Issue(
                "warn", "nav_selector", clause, role,
                f"selector {clause!r} may target nav/listing chrome — review"))
    return issues


def lint_learned(learned_dl: Dict[str, Any]) -> List[Issue]:
    """Lint a learned-download block ({row_selectors, trigger_selectors})."""
    issues: List[Issue] = []
    for sel in (learned_dl or {}).get("row_selectors") or []:
        issues.extend(lint_selector(str(sel), role="row"))
    for sel in (learned_dl or {}).get("trigger_selectors") or []:
        issues.extend(lint_selector(str(sel), role="trigger"))
    return issues


def lint_template(template: Dict[str, Any]) -> List[Issue]:
    """Lint the selector groups of a reviewed/draft template.

    Row-type fields (``download.button``, ``download.row_selectors``) are linted
    as ``row`` (errors). ``download.trigger`` and ``quality.*`` are linted as
    ``trigger`` (warns).
    """
    issues: List[Issue] = []
    selectors = (template or {}).get("selectors") or {}
    dl = selectors.get("download") or {}

    if isinstance(dl.get("button"), str):
        issues.extend(lint_selector(dl["button"], role="row"))
    rows = dl.get("row_selectors")
    if isinstance(rows, (list, tuple)):
        for sel in rows:
            issues.extend(lint_selector(str(sel), role="row"))
    elif isinstance(rows, str):
        issues.extend(lint_selector(rows, role="row"))
    if isinstance(dl.get("trigger"), str):
        issues.extend(lint_selector(dl["trigger"], role="trigger"))

    quality = selectors.get("quality") or {}
    for key in ("open_menu", "resolution_option"):
        if isinstance(quality.get(key), str):
            # {resolution} placeholders are fine; lint the literal shape.
            issues.extend(lint_selector(
                quality[key].replace("{resolution}", "1080"), role="trigger"))
    return issues


def has_blocking_issues(issues: List[Issue]) -> bool:
    """True if any issue is an ``error`` (a reviewed template must have none)."""
    return any(i.level == "error" for i in issues)
