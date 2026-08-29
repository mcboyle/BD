"""Learn a site's download affordance from the held-open Capture page.

The module deliberately separates three operator actions:

* learn the DOM affordance (BAR first, then one DROPDOWN interaction),
* inspect already-redacted media-ish network evidence, and
* crawl a rendered listing to produce plans without starting downloads.

The pure/page-adapter functions are exercised against offline rendered HTML.
The small request/result bridge at the bottom lets the Flask process ask the
existing Capture subprocess to run those same functions against its live,
authenticated Playwright page.  Only safe selectors and policy defaults can be
written to a Review draft; live URLs and network values never cross that seam.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from .capture_redact import redact_media_url, redact_query


DEFAULT_ROW_SELECTORS: tuple[str, ...] = (
    "a[class*='DownloadOption']",
    "a[class*='download-option' i]",
    "a[data-quality][href]",
    "a[download][href]",
    "a[href*='/download/']",
)

# Tag-independent class substrings are intentional: Gamma siblings render the
# same control as either a div or a button and suffix the icon class differently.
DEFAULT_TRIGGER_SELECTORS: tuple[str, ...] = (
    "[class*='ScenePlayerHeaderPlus-IconItem']",
    "[class*='download'][role='button' i]",
    "button[aria-label*='download' i]",
    "[role='button'][aria-label*='download' i]",
)

REQUEST_FILE = "AFFORDANCE_REQUEST.json"
RESULT_FILE = "AFFORDANCE_RESULT.{request_id}.json"
CANCEL_FILE = "AFFORDANCE_CANCELLED.{request_id}.json"
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_MAX_BRIDGE_BYTES = 256 * 1024
_MAX_SCENES = 500
_PAGE_NETWORK_LIMIT = 500
_LEARN_SETTLE_MS = 1500
_LEARN_POLL_MS = 100


def _unique_strings(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def probe_page_selectors(page: Any, selectors: Sequence[str]) -> list[dict[str, Any]]:
    """Report a Playwright selector as PROVEN, MISS, or MALFORMED.

    Playwright defers CSS parsing until an operation such as ``count``.  Catching
    around ``locator()`` alone would therefore mislabel malformed selectors as
    valid misses, which is the exact distinction this workflow needs to expose.
    """
    rows: list[dict[str, Any]] = []
    for selector in selectors:
        selector = str(selector or "")
        try:
            count = int(page.locator(selector).count())
        except Exception as exc:
            rows.append({
                "selector": selector,
                "status": "MALFORMED",
                "count": 0,
                "error": f"{type(exc).__name__}: {exc}"[:500],
            })
            continue
        if count:
            rows.append({"selector": selector, "status": "PROVEN", "count": count})
        else:
            rows.append({"selector": selector, "status": "MISS", "count": 0})
    return rows


_URL_HEIGHT = re.compile(r"(?:^|/)(\d{2,4})p(?:/|$|[?#])", re.I)
_LABEL_HEIGHT = re.compile(r"(?<!\d)(\d{2,4})\s*p\b", re.I)
_NAMED_TIERS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"\b(?:8k|uhd\s*8k)\b", re.I), 4320),
    (re.compile(r"\b(?:4k|uhd)\b", re.I), 2160),
    (re.compile(r"\b(?:full\s*hd|fhd)\b", re.I), 1080),
    (re.compile(r"\bweb\s*hd\b", re.I), 540),
    (re.compile(r"\bhd\b", re.I), 720),
    (re.compile(r"\bsd\b", re.I), 480),
)


def parse_height(label: str, href: str) -> int | None:
    """Parse resolution with URL > explicit label digits > named tier priority."""
    match = _URL_HEIGHT.search(str(href or ""))
    if match:
        return int(match.group(1))
    match = _LABEL_HEIGHT.search(str(label or ""))
    if match:
        return int(match.group(1))
    for pattern, height in _NAMED_TIERS:
        if pattern.search(str(label or "")):
            return height
    return None


_SIZE_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)\b", re.I)
_SIZE_MULTIPLIER = {
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}


def _parse_size(label: str) -> tuple[str | None, int | None]:
    match = _SIZE_RE.search(str(label or ""))
    if not match:
        return None, None
    display = f"{match.group(1)} {match.group(2).upper()}"
    size_bytes = int(float(match.group(1)) * _SIZE_MULTIPLIER[match.group(2).upper()])
    return display, size_bytes


def _container(label: str, href: str) -> str | None:
    parsed = urlparse(str(href or ""))
    path = parsed.path
    for pattern in (
        r"/([a-z0-9]{2,5})(?:/)?$",
        r"\.([a-z0-9]{2,5})$",
    ):
        match = re.search(pattern, path, re.I)
        if match and match.group(1).lower() in {
            "mp4", "mkv", "mov", "webm", "m4v", "avi", "m3u8", "mpd",
        }:
            return match.group(1).lower()
    match = re.search(r"\b(mp4|mkv|mov|webm|m4v|avi)\b", str(label or ""), re.I)
    return match.group(1).lower() if match else None


def _extract_options(page: Any, selector: str) -> list[dict[str, Any]]:
    locator = page.locator(selector)
    options: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str]] = set()
    for index in range(int(locator.count())):
        row = locator.nth(index)
        try:
            if not bool(row.is_visible()):
                continue
            raw_href = str(row.get_attribute("href") or "").strip()
            label = " ".join(str(row.inner_text() or "").split())[:500]
        except Exception:
            continue
        height = parse_height(label, raw_href)
        size, size_bytes = _parse_size(label)
        href = redact_media_url(raw_href)[:1000]
        key = (height, href)
        if not href or key in seen:
            continue
        seen.add(key)
        options.append({
            "height": height,
            "container": _container(label, href),
            "format": _container(label, href),
            "size": size,
            "size_bytes": size_bytes,
            "label": label,
            "href": href,
        })
    options.sort(key=lambda row: (row.get("height") or -1, row.get("label") or ""))
    return options


def _preference_ceilings(quality_preference: Any) -> list[float]:
    text = str(quality_preference or "").strip()
    if not text:
        return [math.inf]
    ceilings: list[float] = []
    for raw in text.split(","):
        token = raw.strip().lower()
        if token in {"best", "highest", "max"}:
            ceilings.append(math.inf)
            continue
        match = re.fullmatch(r"(\d{2,4})(?:\s*p)?", token)
        if match:
            ceilings.append(float(int(match.group(1))))
    return ceilings or [math.inf]


def _preference_ceiling(quality_preference: Any) -> float:
    """Return the first valid ceiling in the existing ordered cascade."""
    return _preference_ceilings(quality_preference)[0]


def pick_resolution(
    options: Sequence[Mapping[str, Any]],
    quality_preference: Any,
    min_resolution: Any,
) -> dict[str, Any]:
    """Choose the highest proven option at/below the existing site preference."""
    ceilings = _preference_ceilings(quality_preference)
    try:
        minimum = max(0, int(min_resolution or 0))
    except (TypeError, ValueError):
        minimum = 0
    ranked = sorted(
        (dict(option) for option in options if isinstance(option.get("height"), int)),
        key=lambda option: int(option["height"]),
        reverse=True,
    )
    ceiling = ceilings[0]
    eligible: list[dict[str, Any]] = []
    for ceiling in ceilings:
        eligible = [
            option for option in ranked
            if int(option["height"]) <= ceiling
        ]
        if eligible:
            break
    if not eligible:
        limit = "best" if math.isinf(ceiling) else f"{int(ceiling)}p"
        return {
            "status": "NO_OPTION_AT_OR_BELOW_PREFERENCE",
            "option": None,
            "reason": f"No available option is at or below quality_preference {limit}",
        }
    best = eligible[0]
    if int(best["height"]) < minimum:
        return {
            "status": "BELOW_MIN_RESOLUTION",
            "option": None,
            "best_available": int(best["height"]),
            "reason": (
                f"Best available {best['height']}p is below min_resolution "
                f"{minimum}p; refusing the plan"
            ),
        }
    return {"status": "SELECTED", "option": best, "reason": ""}


def _headers_as_dict(headers: Any) -> dict[str, str]:
    if isinstance(headers, Mapping):
        return {str(k).lower(): str(v) for k, v in headers.items()}
    out: dict[str, str] = {}
    if isinstance(headers, list):
        for item in headers:
            if isinstance(item, Mapping) and item.get("name") is not None:
                out[str(item.get("name")).lower()] = str(item.get("value") or "")
    return out


_MEDIA_PATH = re.compile(
    r"(?:\.(?:mp4|m4v|webm|mkv|m3u8|mpd|ts)(?:$|[?#])|"
    r"/download(?:/|$)|/user/history/(?:download|streaming)/)",
    re.I,
)
_DIRECT_MEDIA_REQUEST = re.compile(
    r"(?:\.(?:mp4|m4v|webm|mkv|m3u8|mpd)(?:$|[?#])|"
    r"/\d{2,4}p/(?:mp4|m4v|webm|mkv|m3u8|mpd)(?:$|[?#]))",
    re.I,
)


def _safe_evidence_url(url: str) -> str:
    return redact_media_url(str(url or ""))


def media_network_evidence(network_log: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return bounded, redacted media-ish request metadata for operator review."""
    evidence: list[dict[str, Any]] = []
    seen: set[tuple[str, int | None]] = set()
    for raw in list(network_log or [])[-2000:]:
        if not isinstance(raw, Mapping):
            continue
        url = str(raw.get("url") or "")
        headers = _headers_as_dict(raw.get("response_headers"))
        content_type = headers.get("content-type", "").lower()
        if not (_MEDIA_PATH.search(url) or any(token in content_type for token in (
            "video/", "audio/", "mpegurl", "dash+xml", "octet-stream",
        ))):
            continue
        safe_url = _safe_evidence_url(url)[:1000]
        status = raw.get("response_status")
        key = (safe_url, status if isinstance(status, int) else None)
        if key in seen:
            continue
        seen.add(key)
        lower = url.lower()
        if "/user/history/download/" in lower:
            kind = "download_history"
        elif ".m3u8" in lower or "mpegurl" in content_type:
            kind = "manifest"
        elif ".mpd" in lower or "dash+xml" in content_type:
            kind = "manifest"
        elif re.search(r"\.(?:mp4|m4v|webm|mkv)(?:$|[?#])", lower):
            kind = "direct_media"
        else:
            kind = "download"
        evidence.append({
            "url": safe_url,
            "kind": kind,
            "status": status,
            "content_type": content_type[:200] or None,
        })
    return evidence[-100:]


def _merge_evidence(*groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for row in (item for group in groups for item in group):
        if not isinstance(row, Mapping):
            continue
        key = (str(row.get("url") or ""), row.get("status"))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        merged.append(dict(row))
    return merged[-100:]


def attach_page_network_buffer(page: Any, capture: Any) -> list[dict[str, Any]] | None:
    """Record bounded, redacted response metadata for exactly one Capture page.

    The shared CDP log combines every tab.  Learning needs traffic emitted while
    the active scene document loaded, so Capture wires this page-keyed buffer
    before navigation and the bridge later reads only the selected Page's rows.
    """
    try:
        by_page = getattr(capture, "_affordance_page_network", None)
        if not isinstance(by_page, dict):
            by_page = {}
            setattr(capture, "_affordance_page_network", by_page)
        existing = by_page.get(page)
        if isinstance(existing, list):
            return existing
        rows: list[dict[str, Any]] = []
        by_page[page] = rows
    except Exception:
        return None

    generation = [0]
    request_generations: dict[int, int] = {}
    pending_main_navigations: set[int] = set()

    def _is_main_navigation(request: Any) -> bool:
        try:
            return (
                bool(request.is_navigation_request())
                and request.frame == page.main_frame
            )
        except Exception:
            return False

    def _record_request(request: Any) -> None:
        request_generations[id(request)] = generation[0]
        if _is_main_navigation(request):
            pending_main_navigations.add(id(request))

    def _reset_document(frame: Any) -> None:
        try:
            if frame != page.main_frame:
                return
        except Exception:
            return
        # Playwright also emits ``framenavigated`` for pushState/replaceState
        # and hash changes.  Those keep the same Document and its evidence.
        if not pending_main_navigations:
            return
        # Page objects survive both full and same-URL navigations.  Start a new
        # evidence epoch at the document boundary; request generations below
        # also reject late responses belonging to the document just replaced.
        generation[0] += 1
        rows.clear()
        pending_main_navigations.clear()

    def _record_finished(request: Any) -> None:
        pending_main_navigations.discard(id(request))

    def _append(
        url: Any,
        status: Any,
        content_type: Any,
        request_generation: int,
    ) -> None:
        if request_generation != generation[0]:
            return
        safe_url = redact_media_url(str(url or ""))[:1000]
        if not safe_url:
            return
        try:
            safe_status = int(status) if status is not None else None
        except (TypeError, ValueError):
            safe_status = None
        rows.append({
            "url": safe_url,
            "response_status": safe_status,
            "response_headers": {
                "content-type": str(content_type or "")[:200],
            },
        })
        if len(rows) > _PAGE_NETWORK_LIMIT:
            del rows[:-_PAGE_NETWORK_LIMIT]

    def _record_response(response: Any) -> None:
        try:
            request = response.request
            request_generation = request_generations.pop(
                id(request), generation[0]
            )
            headers = getattr(response, "headers", {}) or {}
            content_type = (
                headers.get("content-type", "")
                if isinstance(headers, Mapping) else ""
            )
            _append(
                response.url,
                response.status,
                content_type,
                request_generation,
            )
        except Exception:
            pass

    def _record_failure(request: Any) -> None:
        try:
            pending_main_navigations.discard(id(request))
            request_generation = request_generations.pop(
                id(request), generation[0]
            )
            _append(request.url, None, "", request_generation)
        except Exception:
            pass

    try:
        page.on("request", _record_request)
        page.on("framenavigated", _reset_document)
        page.on("response", _record_response)
        page.on("requestfinished", _record_finished)
        page.on("requestfailed", _record_failure)
    except Exception:
        try:
            by_page.pop(page, None)
        except Exception:
            pass
        return None
    return rows


def attach_page_activity_marker(page: Any) -> bool:
    """Track trusted operator input so live actions follow the visible tab.

    Headed Chromium exposes the selected tab through focus/visibility, but its
    headless test implementation can report every Page as focused and visible.
    A per-document trusted-input clock supplies a deterministic fallback and
    also handles sites that open playback in a popup.  The non-configurable
    accessor prevents page script from assigning a fabricated timestamp.
    """
    wired_at_ms = time.time_ns() / 1_000_000
    script = f"""
(() => {{
  const key = "__bdAffordanceOperatorActivity363";
  const installed = "__bdAffordanceOperatorActivityInstalled363";
  if (window[installed]) return true;
  let last = Math.max({wired_at_ms!r}, Date.now());
  try {{
    Object.defineProperty(window, key, {{
      configurable: false,
      enumerable: false,
      get: () => last,
    }});
  }} catch (_) {{
    return false;
  }}
  const mark = (event) => {{
    if (event && event.isTrusted) last = Math.max(Date.now(), last + 0.001);
  }};
  for (const event of ["pointerdown", "keydown", "touchstart", "wheel"]) {{
    document.addEventListener(event, mark, true);
  }}
  Object.defineProperty(window, installed, {{
    configurable: false,
    enumerable: false,
    value: true,
  }});
  return true;
}})()
"""
    try:
        page.add_init_script(script=script)
        return bool(page.evaluate(script))
    except Exception:
        return False


def _active_page_network(capture: Any, page: Any) -> list[dict[str, Any]]:
    try:
        by_page = getattr(capture, "_affordance_page_network", None)
    except Exception as exc:
        raise RuntimeError(
            "Held-page network capture is unavailable; retry the Capture session."
        ) from exc
    if not isinstance(by_page, dict) or page not in by_page:
        raise RuntimeError(
            "Held-page network capture is unavailable; retry the Capture session."
        )
    rows = by_page[page]
    if not isinstance(rows, list):
        raise RuntimeError(
            "Held-page network capture is malformed; retry the Capture session."
        )
    try:
        selected: list[dict[str, Any]] = []
        for raw in list(rows)[-_PAGE_NETWORK_LIMIT:]:
            if not isinstance(raw, Mapping):
                raise RuntimeError(
                    "Held-page network capture is malformed; retry the Capture session."
                )
            selected.append(dict(raw))
        return selected
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Held-page network capture could not be read; retry the Capture session."
        ) from exc


def _corroboration(options: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    if not options and not evidence:
        return {"status": "NONE", "detail": "Neither DOM rows nor media-ish requests were found."}
    if options and not evidence:
        return {"status": "DOM_ONLY", "detail": "DOM options were found; the network trace did not corroborate them."}
    if evidence and not options:
        return {"status": "NETWORK_ONLY", "detail": "Media-ish requests were found, but no DOM download option was present."}
    dom_paths = {urlparse(str(row.get("href") or "")).path for row in options}
    net_paths = {urlparse(str(row.get("url") or "")).path for row in evidence}
    if dom_paths & net_paths:
        return {"status": "AGREE", "detail": "DOM and network evidence include the same media path."}
    return {
        "status": "DISAGREE",
        "detail": "DOM options and network evidence were both present but exposed different paths; both are retained for review.",
    }


def _find_proven(probes: Sequence[Mapping[str, Any]]) -> str | None:
    for probe in probes:
        if probe.get("status") == "PROVEN":
            return str(probe.get("selector"))
    return None


def _proven_option_rows(
    page: Any, probes: Sequence[Mapping[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Return the proven selector yielding the complete visible option set."""
    best_selector: str | None = None
    best_options: list[dict[str, Any]] = []
    best_score = (-1, -1, -10**9, -1)
    for probe in probes:
        if probe.get("status") != "PROVEN":
            continue
        selector = str(probe.get("selector") or "")
        options = _extract_options(page, selector)
        if not options:
            continue
        numeric = sum(isinstance(option.get("height"), int) for option in options)
        media_shaped = sum(
            bool(option.get("height") or option.get("container") or option.get("size"))
            for option in options
        )
        unknown = len(options) - media_shaped
        score = (numeric, media_shaped, -unknown, len(options))
        if score > best_score:
            best_selector, best_options = selector, options
            best_score = score
    return best_selector, best_options


def _semantic_trigger(locator: Any) -> Any:
    """Choose the download-like match when a stable substring matches siblings."""
    try:
        count = int(locator.count())
    except Exception:
        return locator.first
    for index in range(count):
        candidate = locator.nth(index)
        try:
            haystack = (
                str(candidate.inner_text() or "") + " "
                + str(candidate.evaluate("el => el.outerHTML") or "")
            ).lower()
            if "download" in haystack:
                return candidate
        except Exception:
            continue
    return locator.first


def _specific_trigger(
    page: Any,
    selector: str,
    attempts: list[dict[str, Any]],
) -> tuple[str | None, Any | None]:
    """Return a replayable proven selector for the semantic download control.

    A stable Gamma family substring can intentionally match several header
    icons.  Clicking the semantically correct nth match is enough for this
    learning run, but saving the broad selector would make the runtime click
    whichever sibling happens to render first.  When there are siblings, prove
    a tag-independent ``:has`` selector around the stable download-icon
    substring and record that exact selector as the learned affordance.
    """
    locator = page.locator(selector)
    try:
        count = int(locator.count())
        # A Gamma-family substring that is unique today can match the wrong
        # sibling tomorrow. Always prove the stable download descendant before
        # saving it; the resulting selector replays across div/button siblings.
        needs_semantic_refinement = (
            "ScenePlayerHeaderPlus-IconItem" in selector
            and ":has(" not in selector
        )
        if count == 1 and not needs_semantic_refinement:
            return selector, locator.first
    except Exception:
        return selector, _semantic_trigger(locator)

    for descendant in (
        "[class*='Icon-Download']",
        "[aria-label*='download' i]",
        "[title*='download' i]",
    ):
        refined = f"{selector}:has({descendant})"
        probe = probe_page_selectors(page, [refined])[0]
        attempts.append({
            "role": "trigger",
            "phase": "semantic_refinement",
            **probe,
        })
        if probe.get("status") == "PROVEN" and probe.get("count") == 1:
            return refined, _semantic_trigger(page.locator(refined))
    attempts.append({
        "role": "trigger",
        "phase": "semantic_refinement",
        "selector": selector,
        "status": "FAILED",
        "count": int(locator.count()),
        "error": "Ambiguous download trigger: no refinement proved exactly one control",
    })
    return None, None


def _trigger_is_non_navigational(locator: Any) -> bool:
    """Refuse trigger candidates that can themselves navigate/download."""
    try:
        shape = locator.evaluate(
            "el => ({tag: el.tagName.toLowerCase(), href: el.getAttribute('href') || ''})"
        )
    except Exception:
        return False
    if not isinstance(shape, Mapping):
        return False
    href = str(shape.get("href") or "").strip()
    return str(shape.get("tag") or "").lower() != "a" or href in {"", "#"}


_DOM_DOWNLOAD_GUARD_JS = r"""
() => {
  const key = "__bdAffordanceDownloadGuard";
  if (window[key]) return true;
  const proto = HTMLAnchorElement.prototype;
  const original = proto.click;
  const isDownload = (anchor) => {
    if (!anchor) return false;
    const href = anchor.getAttribute("href") || "";
    return anchor.hasAttribute("download") ||
      /(?:\.(?:mp4|m4v|webm|mkv|m3u8|mpd)(?:$|[?#])|\/\d{2,4}p\/(?:mp4|m4v|webm|mkv|m3u8|mpd)(?:$|[?#]))/i.test(href);
  };
  const state = { original, count: 0, listener: null };
  state.listener = (event) => {
    const anchor = event.target && event.target.closest
      ? event.target.closest("a") : null;
    if (!isDownload(anchor)) return;
    state.count += 1;
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  proto.click = function(...args) {
    if (isDownload(this)) {
      state.count += 1;
      return undefined;
    }
    return original.apply(this, args);
  };
  document.addEventListener("click", state.listener, true);
  window[key] = state;
  return true;
}
"""


def _arm_dom_download_guard(page: Any) -> bool:
    """Prevent programmatic/native anchor downloads before the one click."""
    try:
        return bool(page.evaluate(_DOM_DOWNLOAD_GUARD_JS))
    except Exception:
        return False


def _release_dom_download_guard(page: Any) -> tuple[int, bool]:
    try:
        value = page.evaluate(r"""
() => {
  const key = "__bdAffordanceDownloadGuard";
  const state = window[key];
  if (!state) return {count: 0, released: false};
  const proto = HTMLAnchorElement.prototype;
  proto.click = state.original;
  document.removeEventListener("click", state.listener, true);
  delete window[key];
  return {
    count: state.count || 0,
    released: proto.click === state.original && !window[key]
  };
}
""")
        if not isinstance(value, Mapping):
            return 0, False
        return int(value.get("count") or 0), value.get("released") is True
    except Exception:
        return 0, False


def learn_from_page(
    page: Any,
    *,
    network_log: Sequence[Mapping[str, Any]] | None = None,
    quality_preference: Any = "best",
    min_resolution: Any = 0,
    row_selectors: Sequence[str] | None = None,
    trigger_selectors: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Learn the proven download selector and enumerate every option.

    The no-interaction BAR shape is always attempted first.  Only when every
    row selector misses (or is malformed) is one proven trigger clicked.
    """
    rows = _unique_strings([*(row_selectors or ()), *DEFAULT_ROW_SELECTORS])
    triggers = _unique_strings([*(trigger_selectors or ()), *DEFAULT_TRIGGER_SELECTORS])
    row_probes = probe_page_selectors(page, rows)
    attempts: list[dict[str, Any]] = [{"role": "row", "phase": "before_click", **row} for row in row_probes]
    proven_row, options = _proven_option_rows(page, row_probes)
    proven_trigger: str | None = None
    shape = "BAR" if proven_row else "UNKNOWN"
    interaction_count = 0
    blocked_download_attempts = 0
    download_events_started = 0
    blocked_request_urls: list[str] = []
    unsafe_request_urls: list[str] = []
    guard_unavailable = False
    guard_setup_failed = False
    guard_cleanup_failed = False

    if not proven_row:
        trigger_probes = probe_page_selectors(page, triggers)
        attempts.extend({"role": "trigger", "phase": "before_click", **row} for row in trigger_probes)
        target = None
        for trigger_probe in trigger_probes:
            if trigger_probe.get("status") != "PROVEN":
                continue
            candidate_selector, candidate_target = _specific_trigger(
                page, str(trigger_probe.get("selector") or ""), attempts)
            if candidate_target is None:
                continue
            if not _trigger_is_non_navigational(candidate_target):
                attempts.append({
                    "role": "trigger",
                    "phase": "click",
                    "selector": candidate_selector,
                    "status": "FAILED",
                    "count": 1,
                    "error": "Refused a navigational/download anchor as a dropdown trigger",
                })
                continue
            proven_trigger, target = candidate_selector, candidate_target
            break

        if target is not None:
            download_events: list[Any] = []

            def _cancel_download(download: Any) -> None:
                download_events.append(download)
                try:
                    download.cancel()
                except Exception:
                    pass

            def _block_interaction_request(route: Any, request: Any) -> None:
                url = str(getattr(request, "url", "") or "")
                parsed = urlparse(url)
                if parsed.scheme in {"http", "https"}:
                    safe_url = _safe_evidence_url(url)[:1000]
                    blocked_request_urls.append(safe_url)
                    if not re.search(
                        r"/user/history/(?:download|streaming)/", url, re.I,
                    ):
                        # An opaque fetch cannot be proven non-media without
                        # letting bytes escape. Fail closed after aborting it.
                        unsafe_request_urls.append(safe_url)
                    route.abort()
                else:
                    route.fallback()

            dom_guard_armed = _arm_dom_download_guard(page)
            download_listener_installed = False
            interaction_route_installed = False
            after: list[dict[str, Any]] = []
            try:
                if not dom_guard_armed:
                    guard_unavailable = True
                    raise RuntimeError(
                        "DOM download guard could not be armed; refusing the click"
                    )
                try:
                    page.on("download", _cancel_download)
                    download_listener_installed = True
                except Exception as exc:
                    guard_setup_failed = True
                    raise RuntimeError(
                        "download-listener guard setup failed"
                    ) from exc
                try:
                    page.route("**/*", _block_interaction_request)
                    interaction_route_installed = True
                except Exception as exc:
                    guard_setup_failed = True
                    raise RuntimeError("request guard setup failed") from exc
                target.click(timeout=5000)
                interaction_count = 1
                deadline = time.monotonic() + (_LEARN_SETTLE_MS / 1000)
                # Keep every guard armed for the full bounded settle window.
                # Rows may render asynchronously, and a late timer must not be
                # able to start a download after an early successful probe.
                while time.monotonic() < deadline:
                    page.wait_for_timeout(_LEARN_POLL_MS)
                    after = probe_page_selectors(page, rows)
            except Exception as exc:
                attempts.append({
                    "role": "trigger",
                    "phase": "click",
                    "selector": proven_trigger,
                    "status": "FAILED",
                    "count": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })
            finally:
                if dom_guard_armed:
                    released_count, released = _release_dom_download_guard(page)
                    blocked_download_attempts += released_count
                    guard_cleanup_failed = not released
                if interaction_route_installed:
                    try:
                        page.unroute("**/*", _block_interaction_request)
                    except Exception:
                        guard_cleanup_failed = True
                if download_listener_installed:
                    try:
                        page.remove_listener("download", _cancel_download)
                    except Exception:
                        guard_cleanup_failed = True
                download_events_started += len(download_events)
            if not after:
                after = probe_page_selectors(page, rows)
            attempts.extend({"role": "row", "phase": "after_click", **row} for row in after)
            proven_row, options = _proven_option_rows(page, after)
            if proven_row:
                shape = "DROPDOWN"

    selection = pick_resolution(options, quality_preference, min_resolution) if options else {
        "status": "UNKNOWN",
        "option": None,
        "reason": "No download options were found on the rendered page.",
    }
    interaction_network = [
        {
            "url": url,
            "response_status": None,
            "response_headers": {},
        }
        for url in blocked_request_urls
    ]
    evidence = media_network_evidence([
        *list(network_log or ()),
        *interaction_network,
    ])
    side_effects = (
        blocked_download_attempts + download_events_started
        + len(unsafe_request_urls)
    )
    if guard_unavailable:
        selection = {
            "status": "REFUSED_GUARD_UNAVAILABLE",
            "option": None,
            "reason": (
                "The DOM download guard could not be armed, so the affordance "
                "was not clicked."
            ),
        }
    elif guard_setup_failed:
        selection = {
            "status": "REFUSED_GUARD_SETUP",
            "option": None,
            "reason": (
                "The request/download guard could not be fully installed, so "
                "the affordance was not clicked."
            ),
        }
    elif guard_cleanup_failed:
        selection = {
            "status": "REFUSED_GUARD_CLEANUP",
            "option": None,
            "reason": (
                "The temporary request/download guards could not be proven "
                "removed/restored after learning; refusing the result."
            ),
        }
    elif side_effects:
        selection = {
            "status": "REFUSED_DOWNLOAD_SIDE_EFFECT",
            "option": None,
            "reason": (
                "The affordance attempted a media request/download while learning; "
                "it was blocked and the result is refused."
            ),
        }
    guard_failure = guard_unavailable or guard_setup_failed or guard_cleanup_failed
    failed = guard_failure or bool(side_effects)
    status = "FAILURE" if failed else ("FOUND" if options else "UNKNOWN")
    state = "failed" if failed else ("found" if options else "found_nothing")
    return {
        "status": status,
        "state": state,
        "shape": shape if options else "UNKNOWN",
        "trigger_selector": proven_trigger if options and shape == "DROPDOWN" else None,
        "row_selector": proven_row if options else None,
        "url_attribute": "href",
        "interaction_count": interaction_count,
        "blocked_download_attempts": blocked_download_attempts,
        "download_events_started": download_events_started,
        "blocked_media_requests": unsafe_request_urls,
        "blocked_network_requests": blocked_request_urls,
        "selector_attempts": attempts,
        "options": options,
        "selection": selection,
        "network_evidence": evidence,
        "corroboration": _corroboration(options, evidence),
        "page_host": (urlparse(str(getattr(page, "url", "") or "")).hostname or "").lower(),
        **({"error": selection["reason"]} if failed else {}),
    }


def _canonical_scene_url(url: str) -> str:
    parsed = urlparse(redact_query(str(url or "")))
    identity_keys = {"id", "scene", "scene_id", "video", "video_id", "movie_id", "slug"}
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if key.lower() in identity_keys]
    return urlunparse(parsed._replace(fragment="", query=urlencode(query)))


def _canonical_page_url(url: str) -> str:
    """Canonicalize traversal identity without dropping page/cursor state."""
    parsed = urlparse(redact_query(str(url or "")))
    return urlunparse(parsed._replace(fragment=""))


_SCENE_JS = r"""
() => {
  const cardSelector = '[class*="Scene"], [class*="scene"], [class*="VideoCard"], [class*="video-card"], [class*="MovieCard"]';
  const excluded = /\/(?:download)(?:\/|$)|\.(?:mp4|m3u8|mpd)(?:$|[?#])/i;
  const sceneLike = /\/(?:scene|video|movie|watch)(?:\/|$)/i;
  const taxonomy = /\/(?:performer|model|actor|category|tag|search)(?:\/|$)/i;
  const usable = (anchor) => !excluded.test(anchor.getAttribute('href') || '');
  const found = [];
  for (const card of document.querySelectorAll(cardSelector)) {
    const anchors = Array.from(card.querySelectorAll('a[href]')).filter(usable);
    const primary = anchors.find(a => sceneLike.test(a.getAttribute('href') || ''))
      || anchors.find(a => !taxonomy.test(a.getAttribute('href') || ''));
    if (primary) found.push(primary.href);
  }
  for (const anchor of document.querySelectorAll('a[href]')) {
    if (!anchor.closest(cardSelector) && usable(anchor)
        && sceneLike.test(anchor.getAttribute('href') || '')) {
      found.push(anchor.href);
    }
  }
  return Array.from(new Set(found));
}
"""


def _scene_links(
    page: Any, allowed_host: str = "", allowed_scheme: str = "",
) -> list[str]:
    try:
        raw = page.evaluate(_SCENE_JS)
    except Exception:
        return []
    links: list[str] = []
    for value in raw or []:
        parsed = urlparse(str(value or ""))
        if parsed.scheme not in {"http", "https", "file"}:
            continue
        if allowed_scheme and parsed.scheme != allowed_scheme:
            continue
        if allowed_host and parsed.hostname != allowed_host:
            continue
        links.append(_canonical_scene_url(str(value)))
    return links


def _next_page_url(
    page: Any, initial_host: str, initial_scheme: str, visited: set[str],
) -> str | None:
    selectors = (
        "a[rel='next'][href]",
        "nav[class*='Pagination'] a[href][aria-label*='next' i]",
        "nav[class*='Pagination'] a[href]:last-child",
        "a[class*='Pagination'][href][aria-label*='next' i]",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if not locator.count():
                continue
            href = str(locator.first.get_attribute("href") or "")
        except Exception:
            continue
        absolute = urljoin(str(page.url), href)
        parsed = urlparse(absolute)
        if initial_scheme and parsed.scheme != initial_scheme:
            continue
        if initial_host and parsed.hostname and parsed.hostname != initial_host:
            continue
        canonical = _canonical_page_url(absolute)
        if canonical and canonical not in visited:
            return absolute
    return None


def _scroll_until_stable(
    page: Any,
    scenes: dict[str, str],
    *,
    allowed_host: str = "",
    allowed_scheme: str = "",
    max_rounds: int = 15,
) -> tuple[int, bool]:
    stable = 0
    rounds = 0
    while rounds < max_rounds and stable < 3 and len(scenes) < _MAX_SCENES:
        before = len(scenes)
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1000)
        except Exception:
            break
        rounds += 1
        for url in _scene_links(page, allowed_host, allowed_scheme):
            scenes.setdefault(url, url)
        stable = stable + 1 if len(scenes) == before else 0
    return rounds, stable >= 3


def _listing_completion_proven(page: Any, scene_count: int) -> bool:
    """Require an observable end for a scrollable listing before success."""
    try:
        evidence = page.evaluate(r"""
() => {
  const rawTotal = document.body && document.body.getAttribute('data-total-scenes');
  const total = rawTotal && /^\d+$/.test(rawTotal) ? Number(rawTotal) : null;
  const terminal = !!document.querySelector(
    '[data-end-of-list="true"], [class*="EndOfList"], [class*="end-of-list"]'
  );
  const scrollable = document.documentElement.scrollHeight > window.innerHeight + 1;
  return { total, terminal, scrollable };
}
""")
    except Exception:
        return False
    if not isinstance(evidence, Mapping):
        return False
    if evidence.get("terminal") is True:
        return True
    # A short pagination page has an observable end even when a site-wide total
    # exceeds the number of cards on that one page.
    if evidence.get("scrollable") is False:
        return True
    total = evidence.get("total")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return scene_count >= int(total)
    return False


def _listing_explicitly_empty(page: Any) -> bool:
    """Recognize a rendered empty catalog without laundering a broken grid."""
    try:
        return bool(page.evaluate(r"""
() => {
  const total = document.body && document.body.getAttribute('data-total-scenes');
  return !!total && /^\d+$/.test(total) && Number(total) === 0;
}
"""))
    except Exception:
        return False


def crawl_listing_page(
    page: Any,
    *,
    options: Sequence[Mapping[str, Any]],
    quality_preference: Any,
    min_resolution: Any,
    probe_scene_pages: bool = False,
    row_selectors: Sequence[str] | None = None,
    trigger_selectors: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Enumerate pagination and infinite-scroll scenes and return plans only."""
    initial_url = str(page.url)
    initial_parsed = urlparse(initial_url)
    initial_host = initial_parsed.hostname or ""
    initial_scheme = initial_parsed.scheme
    # Offline rendered fixtures may preserve absolute HTTPS scene hrefs while
    # the document itself is loaded from file://.  A fixture-only declared host
    # keeps those tests realistic without weakening production origin checks.
    fixture_host = ""
    if initial_scheme == "file":
        try:
            fixture_host = str(
                page.locator("body").get_attribute("data-site-host") or ""
            ).strip().lower()
        except Exception:
            fixture_host = ""
    scene_host = fixture_host or initial_host
    scene_scheme = "https" if fixture_host else initial_scheme
    visited_pages: set[str] = set()
    scenes: dict[str, str] = {}
    pagination_pages = 0
    scroll_rounds = 0
    pagination_error: str | None = None
    pagination_limit_hit = False
    scroll_limit_hit = False
    page_completion_unproven = False
    downloads_blocked = 0
    downloads_started = 0
    media_requests_blocked = 0
    listing_guard_error: str | None = None
    listing_download_events: list[str] = []
    listing_blocked_requests: list[str] = []
    listing_dom_guard_armed = False
    listing_route_installed = False
    listing_download_listener_installed = False

    def _cancel_listing_download(download: Any) -> None:
        listing_download_events.append(str(
            getattr(download, "suggested_filename", "") or "download"
        ))
        try:
            download.cancel()
        except Exception:
            pass

    def _block_listing_media_request(route: Any, request: Any) -> None:
        url = str(getattr(request, "url", "") or "")
        resource_type = str(
            getattr(request, "resource_type", "") or ""
        ).lower()
        parsed = urlparse(url)
        # Listing JSON/XHR/fetch traffic is how many infinite-scroll grids
        # reveal more cards, so it must remain available.  Block only requests
        # proven media-shaped (plus the browser's explicit media resource type).
        if (
            parsed.scheme in {"http", "https"}
            and (resource_type == "media" or _MEDIA_PATH.search(url))
        ):
            listing_blocked_requests.append(_safe_evidence_url(url)[:1000])
            route.abort()
        else:
            route.fallback()

    try:
        try:
            page.on("download", _cancel_listing_download)
            listing_download_listener_installed = True
        except Exception as exc:
            listing_guard_error = (
                "Listing download-listener guard setup failed: "
                f"{type(exc).__name__}: {exc}"
            )[:500]
        if not listing_guard_error:
            try:
                page.route("**/*", _block_listing_media_request)
                listing_route_installed = True
            except Exception as exc:
                listing_guard_error = (
                    "Listing request guard setup failed: "
                    f"{type(exc).__name__}: {exc}"
                )[:500]
        while pagination_pages < 25 and not listing_guard_error:
            current = _canonical_page_url(str(page.url))
            if current in visited_pages:
                break
            visited_pages.add(current)
            pagination_pages += 1
            listing_dom_guard_armed = _arm_dom_download_guard(page)
            if not listing_dom_guard_armed:
                listing_guard_error = (
                    "Could not arm the listing download guard; refusing to "
                    "scroll or navigate the listing."
                )
                break
            for url in _scene_links(page, scene_host, scene_scheme):
                scenes.setdefault(url, url)
                if len(scenes) >= _MAX_SCENES:
                    break
            page_scroll_rounds, page_scroll_complete = _scroll_until_stable(
                page,
                scenes,
                allowed_host=scene_host,
                allowed_scheme=scene_scheme,
            )
            scroll_rounds += page_scroll_rounds
            scroll_limit_hit = scroll_limit_hit or not page_scroll_complete
            page_completion_unproven = (
                page_completion_unproven
                or not _listing_completion_proven(page, len(scenes))
            )
            next_url = _next_page_url(
                page, initial_host, initial_scheme, visited_pages)
            released_count, released = _release_dom_download_guard(page)
            downloads_blocked += released_count
            if not released:
                listing_guard_error = (
                    "Could not prove the listing download guard was restored; "
                    "refusing the crawl result."
                )
                break
            listing_dom_guard_armed = False
            if not next_url or len(scenes) >= _MAX_SCENES:
                break
            try:
                page.goto(next_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                pagination_error = f"Pagination failed: {type(exc).__name__}: {exc}"[:500]
                break
        else:
            if not listing_guard_error:
                # A safety bound is not evidence that enumeration completed.  If the
                # loop exhausts, iteration 25 already found and navigated to an
                # unvisited next page.  Retain the plans but fail closed instead of
                # reporting that partial crawl as FOUND.
                pagination_limit_hit = True
    finally:
        if listing_dom_guard_armed:
            released_count, released = _release_dom_download_guard(page)
            downloads_blocked += released_count
            if not released and not listing_guard_error:
                listing_guard_error = (
                    "Could not prove the listing download guard was restored; "
                    "refusing the crawl result."
                )
        if listing_route_installed:
            try:
                page.unroute("**/*", _block_listing_media_request)
            except Exception as exc:
                listing_guard_error = (
                    "Listing request guard cleanup failed: "
                    f"{type(exc).__name__}: {exc}"
                )[:500]
        if listing_download_listener_installed:
            try:
                page.remove_listener("download", _cancel_listing_download)
            except Exception as exc:
                listing_guard_error = (
                    "Listing download-listener guard cleanup failed: "
                    f"{type(exc).__name__}: {exc}"
                )[:500]
    downloads_started += len(listing_download_events)
    downloads_blocked += len(listing_download_events)
    media_requests_blocked += len(listing_blocked_requests)

    empty_integrity_error = pagination_error or listing_guard_error or (
        "Listing reached the 25-page safety limit while another page remained; "
        "refusing to claim complete enumeration."
        if pagination_limit_hit else ""
    ) or (
        "Listing kept exposing scenes through the infinite-scroll safety limit; "
        "refusing to claim complete enumeration."
        if scroll_limit_hit else ""
    ) or (
        "The scrollable listing had no proven total/end completion; refusing "
        "to accept an empty marker as authoritative."
        if page_completion_unproven else ""
    ) or (
        "A browser download started while planning; refusing the crawl result."
        if downloads_started else ""
    )
    if not scenes and empty_integrity_error:
        return {
            "status": "FAILURE",
            "state": "failed",
            "scene_count": 0,
            "plans": [],
            "pagination_pages": pagination_pages,
            "scroll_rounds": scroll_rounds,
            "downloads_started": downloads_started,
            "downloads_blocked": downloads_blocked,
            "media_requests_blocked": media_requests_blocked,
            "error": empty_integrity_error,
        }

    if not scenes and pagination_pages == 1 and _listing_explicitly_empty(page):
        return {
            "status": "EMPTY",
            "state": "found_nothing",
            "scene_count": 0,
            "plans": [],
            "pagination_pages": pagination_pages,
            "scroll_rounds": scroll_rounds,
            "downloads_started": downloads_started,
            "downloads_blocked": downloads_blocked,
            "media_requests_blocked": media_requests_blocked,
            "reason": "Rendered listing explicitly declares zero scenes.",
        }

    if not scenes:
        return {
            "status": "FAILURE",
            "state": "failed",
            "scene_count": 0,
            "plans": [],
            "pagination_pages": pagination_pages,
            "scroll_rounds": scroll_rounds,
            "downloads_started": downloads_started,
            "downloads_blocked": downloads_blocked,
            "media_requests_blocked": media_requests_blocked,
            "error": "Zero scenes found on a rendered listing; refusing empty success.",
        }

    shared_selection = pick_resolution(options, quality_preference, min_resolution)
    plans: list[dict[str, Any]] = []
    for scene_url in list(scenes)[:_MAX_SCENES]:
        selection = shared_selection
        source = "current_page_options"
        if probe_scene_pages:
            probe = None
            blocked: list[str] = []
            blocked_requests: list[str] = []
            try:
                probe = page.context.new_page()

                def _cancel_download(download: Any) -> None:
                    blocked.append(str(
                        getattr(download, "suggested_filename", "") or "download"
                    ))
                    try:
                        download.cancel()
                    except Exception:
                        pass

                def _block_media_request(route: Any, request: Any) -> None:
                    url = str(getattr(request, "url", "") or "")
                    resource_type = str(
                        getattr(request, "resource_type", "") or ""
                    ).lower()
                    if (
                        resource_type in {
                            "media", "xhr", "fetch", "eventsource", "websocket",
                        }
                        or _MEDIA_PATH.search(url)
                    ):
                        blocked_requests.append(_safe_evidence_url(url))
                        route.abort()
                    else:
                        route.fallback()

                # Install both guards before navigation.  Scene planning may
                # render markup and scripts, but it never permits a player
                # media subresource or browser download to escape first.
                probe.on("download", _cancel_download)
                probe.route("**/*", _block_media_request)
                probe.goto(scene_url, wait_until="domcontentloaded", timeout=30000)
                learned = learn_from_page(
                    probe,
                    quality_preference=quality_preference,
                    min_resolution=min_resolution,
                    row_selectors=row_selectors,
                    trigger_selectors=trigger_selectors,
                )
                # The outer page guard blocks opaque player/autoplay requests
                # throughout the probe.  The learner installs its own stricter
                # all-HTTP guard around the one dropdown click and reports those
                # separately, so a late request initiated by navigation cannot
                # be mislabeled as a download-affordance side effect.
                media_requests_blocked += len(blocked_requests)
                selection = learned.get("selection") or selection
                source = "scene_page"
                guarded_attempts = int(
                    learned.get("blocked_download_attempts") or 0
                ) + len(learned.get("blocked_media_requests") or [])
                downloads_started += max(
                    len(blocked),
                    int(learned.get("download_events_started") or 0),
                )
                if blocked or guarded_attempts:
                    downloads_blocked += (
                        len(blocked) + guarded_attempts
                    )
                    selection = {
                        "status": "REFUSED_DOWNLOAD_SIDE_EFFECT",
                        "option": None,
                        "reason": "A media request/download was attempted while probing the affordance; it was blocked and this scene is refused.",
                    }
            except Exception as exc:
                selection = {
                    "status": "UNKNOWN", "option": None,
                    "reason": f"Scene planning failed: {type(exc).__name__}: {exc}"[:300],
                }
                source = "scene_page"
            finally:
                if probe is not None:
                    try:
                        probe.close()
                    except Exception:
                        pass
        chosen = selection.get("option") if isinstance(selection, Mapping) else None
        plans.append({
            "url": scene_url,
            "status": "PLANNED" if chosen else "REFUSED",
            "chosen_height": chosen.get("height") if isinstance(chosen, Mapping) else None,
            "selection_status": selection.get("status") if isinstance(selection, Mapping) else "UNKNOWN",
            "reason": selection.get("reason", "") if isinstance(selection, Mapping) else "",
            "source": source,
        })
    truncated = len(scenes) >= _MAX_SCENES
    completion_unproven = (
        page_completion_unproven
        or not _listing_completion_proven(page, len(scenes))
    )
    error = pagination_error or listing_guard_error or (
        f"Listing reached the {_MAX_SCENES}-scene safety limit; refusing to claim complete enumeration."
        if truncated else ""
    ) or (
        "Listing reached the 25-page safety limit while another page remained; "
        "refusing to claim complete enumeration."
        if pagination_limit_hit else ""
    ) or (
        "Listing kept exposing scenes through the infinite-scroll safety limit; "
        "refusing to claim complete enumeration."
        if scroll_limit_hit else ""
    ) or (
        "At least one scrollable listing page had no proven total/end completion; "
        "refusing to claim complete infinite-scroll enumeration."
        if completion_unproven else ""
    ) or (
        "A browser download started while planning; refusing the crawl result."
        if downloads_started else ""
    )
    return {
        "status": "FAILURE" if error else "FOUND",
        "state": "failed" if error else "found",
        "scene_count": len(plans),
        "plans": plans,
        "pagination_pages": pagination_pages,
        "scroll_rounds": scroll_rounds,
        "downloads_started": downloads_started,
        "downloads_blocked": downloads_blocked,
        "media_requests_blocked": media_requests_blocked,
        **({"error": error} if error else {}),
    }


_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:cookie|cookies|cookie_file|credential|credentials|password|passwd|"
    r"token|auth_token|authorization|session|session_id|secret|api_key|username|"
    r"member_url)(?:$|_)",
    re.I,
)
_FORBIDDEN_VALUE = re.compile(
    r"(?:bearer\s+[a-z0-9._~-]+|(?:cookie|password|token|secret)\s*[:=])",
    re.I,
)
_CONCRETE_SELECTOR_PATH = re.compile(
    r"(?:https?://|[?&](?:token|auth|session|sig|key)=|"
    r"/\d{5,}(?:/|['\"]|$)|"
    r"\[\s*(?:(?:data|x)[-_])?(?:api[-_]?)?"
    r"(?:key|sig(?:nature)?|csrf|xsrf|nonce|jwt|bearer)\b)",
    re.I,
)
_SELECTOR_LITERAL = re.compile(r"['\"]([^'\"]+)['\"]")
_GENERIC_SELECTOR_PATHS = {
    "/download/", "/media/", "/movie/", "/video/", "/scene/", "/watch/",
}


def selector_safety_findings(selector: str, path: str = "selector") -> list[str]:
    """Reject selectors that embed a concrete member URL, id, or token."""
    text = str(selector or "")
    if _CONCRETE_SELECTOR_PATH.search(text):
        return [f"{path}: concrete member URL/id/token is not allowed in a reusable selector"]
    if re.search(
        r"\[[^\]]*(?:auth|token|cookie|session|credential|password|secret)[^\]]*\]",
        text,
        re.I,
    ):
        return [f"{path}: credential-shaped selector attribute is not allowed"]
    for literal in _SELECTOR_LITERAL.findall(text):
        if "/" in literal and literal.lower() not in _GENERIC_SELECTOR_PATHS:
            return [f"{path}: concrete member URL/path is not allowed in a reusable selector"]
    return []


def template_safety_findings(value: Any, path: str = "$", *, _depth: int = 0) -> list[str]:
    """Fail closed on credential-shaped keys or values in a learned template."""
    if _depth > 30:
        return [f"{path}: nesting exceeds safety limit"]
    findings: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            normalized_key = re.sub(
                r"[^a-z0-9]+", "_",
                re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key_text),
                flags=re.I,
            ).strip("_").lower()
            if _FORBIDDEN_KEY.search(normalized_key):
                findings.append(f"{child_path}: credential/cookie/secret-shaped field")
            findings.extend(template_safety_findings(child, child_path, _depth=_depth + 1))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            findings.extend(template_safety_findings(child, f"{path}[{index}]", _depth=_depth + 1))
    elif isinstance(value, str):
        if value.startswith(("http://", "https://")):
            findings.append(f"{path}: absolute/member URL is not allowed in a selector template")
        if _FORBIDDEN_VALUE.search(value) or "zero-entropy-fixture-token" in value.lower():
            findings.append(f"{path}: credential/cookie/secret-shaped value")
        if any(marker in path for marker in (
            ".row_selectors", ".trigger_selectors", ".selectors.download",
        )):
            findings.extend(selector_safety_findings(value, path))
    elif isinstance(value, (bytes, bytearray)):
        findings.append(f"{path}: binary value is not allowed")
    return findings


def _template_host(host: str) -> str:
    parsed = urlparse(host if "://" in str(host) else f"https://{host}")
    clean = (parsed.hostname or "").strip(".").lower()
    if not clean or not re.fullmatch(r"[a-z0-9.-]+", clean):
        raise ValueError("valid template host required")
    return clean


def _template_quality_preference(value: Any) -> str:
    """Normalize the existing setting without admitting arbitrary text."""
    text = str(value or "best").strip()
    parts = [part.strip().lower() for part in text.split(",") if part.strip()]
    normalized: list[str] = []
    for part in parts:
        if part in {"best", "highest", "max"}:
            token = "best"
        else:
            match = re.fullmatch(r"(\d{2,4})(?:p)?", part)
            if not match or not 1 <= int(match.group(1)) <= 8640:
                raise ValueError(
                    "quality_preference must contain only comma-separated "
                    "resolution heights or best"
                )
            token = str(int(match.group(1)))
        if token not in normalized:
            normalized.append(token)
    if not normalized:
        raise ValueError("quality_preference must not be empty")
    return ",".join(normalized)


def _exact_mapping_keys(
    value: Any,
    *,
    path: str,
    allowed: set[str],
    required: set[str] | None = None,
) -> list[str]:
    if not isinstance(value, Mapping):
        return [f"{path} must be an object"]
    actual = {str(key) for key in value}
    errors = [
        f"{path}.{key}: field is not allowed in a learned template"
        for key in sorted(actual - allowed)
    ]
    for key in sorted((required or set()) - actual):
        errors.append(f"{path}.{key}: required learned-template field is missing")
    return errors


def learned_template_shape_errors(template: Mapping[str, Any]) -> list[str]:
    """Enforce the complete selector-only artifact allowlist."""
    top = {
        "schema_version", "status", "review_required", "host", "patterns",
        "learned", "config_defaults", "selectors", "resolutions",
        "network_patterns", "learning_evidence",
    }
    errors = _exact_mapping_keys(
        template, path="$", allowed=top, required=top)
    learned = template.get("learned")
    errors.extend(_exact_mapping_keys(
        learned, path="$.learned", allowed={"download"}, required={"download"}))
    learned_download = learned.get("download") if isinstance(learned, Mapping) else None
    download_keys = {"trigger_selectors", "row_selectors", "url_attribute"}
    errors.extend(_exact_mapping_keys(
        learned_download,
        path="$.learned.download",
        allowed=download_keys,
        required=download_keys,
    ))
    config_keys = {"quality_preference", "min_resolution"}
    errors.extend(_exact_mapping_keys(
        template.get("config_defaults"),
        path="$.config_defaults",
        allowed=config_keys,
        required=config_keys,
    ))
    selectors = template.get("selectors")
    errors.extend(_exact_mapping_keys(
        selectors, path="$.selectors", allowed={"download"}, required={"download"}))
    selector_download = selectors.get("download") if isinstance(selectors, Mapping) else None
    errors.extend(_exact_mapping_keys(
        selector_download,
        path="$.selectors.download",
        allowed={"trigger", "row_selectors", "url_attribute"},
        required={"row_selectors", "url_attribute"},
    ))
    evidence_keys = {
        "shape", "option_count", "corroboration", "dom_options_proven",
        "proof_digest",
    }
    errors.extend(_exact_mapping_keys(
        template.get("learning_evidence"),
        path="$.learning_evidence",
        allowed=evidence_keys,
        required=evidence_keys,
    ))
    return errors


def learned_template_digest(template: Mapping[str, Any]) -> str:
    """Digest the complete artifact except explicit promotion-state fields."""
    payload = {
        str(key): value
        for key, value in template.items()
        if key not in {"status", "review_required"}
    }
    evidence = dict(payload.get("learning_evidence") or {})
    evidence.pop("proof_digest", None)
    payload["learning_evidence"] = evidence
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def learned_template_gate_errors(template: Mapping[str, Any]) -> list[str]:
    """Recheck row-363 safety and proof integrity at preflight/promotion."""
    errors = learned_template_shape_errors(template)
    errors.extend(template_safety_findings(template))
    evidence = template.get("learning_evidence")
    claimed = str(evidence.get("proof_digest") or "") if isinstance(evidence, Mapping) else ""
    if not re.fullmatch(r"[0-9a-f]{64}", claimed):
        errors.append("learning_evidence.proof_digest is missing or malformed")
    elif claimed != learned_template_digest(template):
        errors.append("learned selector proof digest does not match the staged artifact")
    return errors


def build_learned_template(
    learned: Mapping[str, Any],
    *,
    host: str,
    quality_preference: Any,
    min_resolution: Any,
) -> dict[str, Any]:
    """Build an allowlisted selector-only draft from a learner result."""
    clean_host = _template_host(host)
    row_selector = str(learned.get("row_selector") or "").strip()
    trigger_selector = str(learned.get("trigger_selector") or "").strip()
    if learned.get("status") != "FOUND" or not row_selector:
        raise ValueError("cannot save an UNKNOWN affordance")
    selector_findings = selector_safety_findings(row_selector, "learned.download.row_selectors[0]")
    if trigger_selector:
        selector_findings.extend(selector_safety_findings(
            trigger_selector, "learned.download.trigger_selectors[0]"))
    if selector_findings:
        raise ValueError("credential/member URL safety check failed: " + "; ".join(selector_findings))
    try:
        minimum = int(min_resolution or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("min_resolution must be an integer") from exc
    if minimum < 0 or minimum > 8640:
        raise ValueError("min_resolution must be between 0 and 8640")
    preference = _template_quality_preference(quality_preference)
    shape = str(learned.get("shape") or "UNKNOWN").upper()
    if shape not in {"BAR", "DROPDOWN"}:
        raise ValueError("learned affordance must prove BAR or DROPDOWN shape")
    heights = sorted({
        int(option["height"]) for option in (learned.get("options") or [])
        if isinstance(option, Mapping) and isinstance(option.get("height"), int)
    }, reverse=True)
    if not heights:
        raise ValueError("learned affordance has no numeric resolutions")
    policy = pick_resolution(
        learned.get("options") or (), preference, minimum)
    if policy.get("status") != "SELECTED":
        raise ValueError(str(policy.get("reason") or "learned options violate quality policy"))
    template = {
        "schema_version": "row363.learned-template.v1",
        "status": "draft_review_required",
        "review_required": True,
        "host": clean_host,
        "patterns": [re.escape(clean_host)],
        "learned": {
            "download": {
                "trigger_selectors": [trigger_selector] if trigger_selector else [],
                "row_selectors": [row_selector],
                "url_attribute": "href",
            },
        },
        "config_defaults": {
            "quality_preference": preference,
            "min_resolution": minimum,
        },
        # Existing Template Manager runtime/review shape.  These duplicate the
        # lossless learned block so the existing explicit Promote gate can lint
        # and install this draft without a parallel promotion surface.
        "selectors": {
            "download": {
                **({"trigger": trigger_selector} if trigger_selector else {}),
                "row_selectors": [row_selector],
                "url_attribute": "href",
            },
        },
        "resolutions": heights,
        # No network proof is fabricated. The existing Promote gate has a
        # narrow row363 exception for a selector-only, DOM-proven candidate.
        "network_patterns": [],
        "learning_evidence": {
            "shape": shape,
            "option_count": len(learned.get("options") or []),
            "corroboration": (
                str((learned.get("corroboration") or {}).get("status") or "NONE")
                if str((learned.get("corroboration") or {}).get("status") or "NONE")
                in {"AGREE", "DISAGREE", "DOM_ONLY", "NETWORK_ONLY", "NONE"}
                else "NONE"
            ),
            "dom_options_proven": True,
        },
    }
    template["learning_evidence"]["proof_digest"] = learned_template_digest(template)
    findings = template_safety_findings(template)
    if findings:
        raise ValueError("credential/cookie/secret safety check failed: " + "; ".join(findings))
    return template


def write_learned_template(template: Mapping[str, Any], *, drafts_dir: str | Path) -> dict[str, Any]:
    findings = (
        learned_template_gate_errors(template)
        if template.get("schema_version") == "row363.learned-template.v1"
        else template_safety_findings(template)
    )
    if findings:
        raise ValueError("learned template safety/integrity check failed: " + "; ".join(findings))
    host = _template_host(str(template.get("host") or ""))
    directory = Path(drafts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    proof_digest = learned_template_digest(template)
    filename = (
        re.sub(r"[^a-z0-9.-]+", "-", host)
        + f".{proof_digest[:12]}.template-draft.json"
    )
    path = directory / filename
    encoded = (
        json.dumps(dict(template), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    def _same_existing_draft() -> bool:
        try:
            return not path.is_symlink() and path.is_file() and path.read_bytes() == encoded
        except OSError:
            return False

    if path.exists() or path.is_symlink():
        if not _same_existing_draft():
            raise ValueError(
                "learned-template draft collision; refusing to overwrite an "
                "existing operator draft"
            )
        return {"ok": True, "file": filename, "status": "draft_review_required"}

    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # hard-link publication is atomic and fails if another writer won;
            # unlike os.replace it can never clobber an unreviewed draft.
            os.link(tmp, path)
        except FileExistsError:
            if not _same_existing_draft():
                raise ValueError(
                    "learned-template draft collision; refusing to overwrite "
                    "an existing operator draft"
                )
    finally:
        tmp.unlink(missing_ok=True)
    return {"ok": True, "file": filename, "status": "draft_review_required"}


def stage_learned_template(
    learned: Mapping[str, Any],
    *,
    host: str,
    quality_preference: Any,
    min_resolution: Any,
    drafts_dir: str | Path,
) -> dict[str, Any]:
    template = build_learned_template(
        learned,
        host=host,
        quality_preference=quality_preference,
        min_resolution=min_resolution,
    )
    written = write_learned_template(template, drafts_dir=drafts_dir)
    return {**written, "template": template}


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dict(value), separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _nonce_path(out_dir: str | Path, pattern: str, request_id: str) -> Path:
    """Resolve a bridge artifact without allowing a caller-controlled path."""
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("valid live-learning request_id required")
    return Path(out_dir) / pattern.format(request_id=request_id)


def live_result_path(out_dir: str | Path, request_id: str) -> Path:
    """Return the result path owned exclusively by one live-action nonce."""
    return _nonce_path(out_dir, RESULT_FILE, request_id)


def _cancel_path(out_dir: str | Path, request_id: str) -> Path:
    return _nonce_path(out_dir, CANCEL_FILE, request_id)


def _is_cancelled(out_dir: str | Path, request_id: str) -> bool:
    return _cancel_path(out_dir, request_id).is_file()


def request_live_action(out_dir: str | Path, mode: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Arm one nonce-bound request for the Capture subprocess."""
    if mode not in {"learn", "network", "crawl"}:
        raise ValueError(f"unknown live-learning mode: {mode!r}")
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    request_id = uuid.uuid4().hex
    body = {"request_id": request_id, "mode": mode, "payload": dict(payload or {})}
    encoded = json.dumps(body)
    if len(encoded.encode("utf-8")) > _MAX_BRIDGE_BYTES:
        raise ValueError("live-learning request is too large")
    live_result_path(directory, request_id).unlink(missing_ok=True)
    _cancel_path(directory, request_id).unlink(missing_ok=True)
    _atomic_json(directory / REQUEST_FILE, body)
    return {"request_id": request_id, "state": "running", "mode": mode}


def cancel_live_action(out_dir: str | Path, request_id: str) -> bool:
    """Tombstone ``request_id`` and remove only bridge files it owns.

    The marker is written before deleting anything so a Capture worker that
    already consumed the shared request cannot publish a late result.
    """
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    marker = _cancel_path(directory, request_id)
    _atomic_json(marker, {"request_id": request_id, "cancelled": True})
    removed = False
    request_path = directory / REQUEST_FILE
    try:
        if request_path.is_file() and request_path.stat().st_size <= _MAX_BRIDGE_BYTES:
            value = json.loads(request_path.read_text(encoding="utf-8"))
            if isinstance(value, Mapping) and value.get("request_id") == request_id:
                request_path.unlink(missing_ok=True)
                removed = True
    except (OSError, ValueError, TypeError):
        pass
    result_path = live_result_path(directory, request_id)
    if result_path.is_file():
        result_path.unlink(missing_ok=True)
        removed = True
    return removed or marker.is_file()


def consume_live_result(out_dir: str | Path, request_id: str) -> dict[str, Any] | None:
    path = live_result_path(out_dir, request_id)
    if _is_cancelled(out_dir, request_id):
        path.unlink(missing_ok=True)
        return None
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > _MAX_BRIDGE_BYTES:
            path.unlink(missing_ok=True)
            return {
                "request_id": request_id,
                "state": "failed",
                "error": "live learning result exceeded the bounded bridge",
            }
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        path.unlink(missing_ok=True)
        return {
            "request_id": request_id,
            "state": "failed",
            "error": "live learning result was unreadable or malformed",
        }
    if not isinstance(value, dict) or value.get("request_id") != request_id:
        path.unlink(missing_ok=True)
        return {
            "request_id": request_id,
            "state": "failed",
            "error": "live learning result did not match the active request",
        }
    path.unlink(missing_ok=True)
    return value


def _active_page(pages: Sequence[Any]) -> Any | None:
    candidates: list[tuple[Any, bool, float, int]] = []
    for index, page in enumerate(list(pages)):
        try:
            if page.is_closed():
                continue
        except Exception:
            continue
        focused_visible = False
        activity = 0.0
        try:
            state = page.evaluate(r"""
() => ({
  focused: document.hasFocus(),
  visible: document.visibilityState === "visible",
  activity: Number(window.__bdAffordanceOperatorActivity363 || 0),
})
""")
            if isinstance(state, Mapping):
                focused_visible = (
                    state.get("focused") is True and state.get("visible") is True
                )
                raw_activity = state.get("activity")
                if isinstance(raw_activity, (int, float)) and not isinstance(
                    raw_activity, bool
                ):
                    activity = float(raw_activity)
        except Exception:
            pass
        candidates.append((page, focused_visible, activity, index))
    if not candidates:
        return None
    focused = [row for row in candidates if row[1]]
    # In headed Chromium exactly one selected tab is focused/visible.  If the
    # browser reports several (as headless Chromium does), trusted activity is
    # the tiebreaker.  Original ordering is only the last-resort compatibility
    # fallback for pages wired before the marker existed.
    pool = focused if focused else candidates
    with_activity = [row for row in pool if row[2] > 0]
    if with_activity:
        return max(with_activity, key=lambda row: (row[2], row[3]))[0]
    if len(focused) == 1:
        return focused[0][0]
    return candidates[-1][0]


def maybe_service_live_request(pages: Sequence[Any], capture: Any, out_dir: str | Path) -> bool:
    """Capture-side tick service for one atomically claimed request."""
    directory = Path(out_dir)
    request_path = directory / REQUEST_FILE
    claim_path = directory / f".{REQUEST_FILE}.{uuid.uuid4().hex}.claim"
    try:
        os.replace(request_path, claim_path)
    except FileNotFoundError:
        return False
    try:
        if claim_path.stat().st_size > _MAX_BRIDGE_BYTES:
            raise ValueError("request exceeds bridge size limit")
        request = json.loads(claim_path.read_text(encoding="utf-8"))
    except Exception:
        claim_path.unlink(missing_ok=True)
        return False
    claim_path.unlink(missing_ok=True)
    request_id = str(request.get("request_id") or "")
    mode = str(request.get("mode") or "")
    payload = request.get("payload") if isinstance(request.get("payload"), Mapping) else {}
    if not _REQUEST_ID.fullmatch(request_id):
        return True
    if _is_cancelled(directory, request_id):
        return True
    try:
        page = _active_page(pages)
        if page is None:
            raise RuntimeError("held-open Capture page is unavailable")
        if mode == "learn":
            page_baseline = _active_page_network(capture, page)
            page_network: list[dict[str, Any]] = []

            def _record_page_response(response: Any) -> None:
                try:
                    headers = getattr(response, "headers", {}) or {}
                    content_type = (
                        headers.get("content-type", "")
                        if isinstance(headers, Mapping) else ""
                    )
                    page_network.append({
                        "url": redact_media_url(str(response.url))[:1000],
                        "response_status": int(response.status),
                        "response_headers": {
                            "content-type": str(content_type)[:200],
                        },
                    })
                except Exception:
                    pass

            def _record_page_failure(failed_request: Any) -> None:
                try:
                    page_network.append({
                        "url": redact_media_url(str(failed_request.url))[:1000],
                        "response_status": None,
                        "response_headers": {},
                    })
                except Exception:
                    pass

            page.on("response", _record_page_response)
            page.on("requestfailed", _record_page_failure)
            try:
                result = learn_from_page(
                    page,
                    network_log=page_baseline,
                    quality_preference=payload.get("quality_preference", "best"),
                    min_resolution=payload.get("min_resolution", 0),
                    row_selectors=payload.get("row_selectors") or (),
                    trigger_selectors=payload.get("trigger_selectors") or (),
                )
                page.wait_for_timeout(150)
            finally:
                try:
                    page.remove_listener("response", _record_page_response)
                    page.remove_listener("requestfailed", _record_page_failure)
                except Exception:
                    pass
            evidence = _merge_evidence(
                result.get("network_evidence") or (),
                media_network_evidence(page_network),
            )
            result["network_evidence"] = evidence
            result["corroboration"] = _corroboration(
                result.get("options") or (), evidence)
        elif mode == "network":
            evidence = media_network_evidence(_active_page_network(capture, page))
            result = {
                "status": "FOUND" if evidence else "UNKNOWN",
                "state": "found" if evidence else "found_nothing",
                "count": len(evidence),
                "network_evidence": evidence,
            }
        elif mode == "crawl":
            result = crawl_listing_page(
                page,
                options=payload.get("options") or (),
                quality_preference=payload.get("quality_preference", "best"),
                min_resolution=payload.get("min_resolution", 0),
                probe_scene_pages=True,
                row_selectors=payload.get("row_selectors") or (),
                trigger_selectors=payload.get("trigger_selectors") or (),
            )
        else:
            raise ValueError(f"unknown request mode: {mode!r}")
        envelope = {
            "request_id": request_id,
            "mode": mode,
            "state": result.get("state"),
            "result": result,
            **({"error": result.get("error")} if result.get("state") == "failed" else {}),
        }
    except Exception as exc:
        envelope = {
            "request_id": request_id,
            "mode": mode,
            "state": "failed",
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
    if len(json.dumps(envelope).encode("utf-8")) > _MAX_BRIDGE_BYTES:
        envelope = {
            "request_id": request_id,
            "mode": mode,
            "state": "failed",
            "error": "Live learning result exceeded the bounded bridge; narrow the listing and retry.",
        }
    # Check both before and after the atomic write.  Those checks cover either
    # ordering of a cross-process cancellation racing this completion.
    if _is_cancelled(directory, request_id):
        return True
    result_path = live_result_path(directory, request_id)
    _atomic_json(result_path, envelope)
    if _is_cancelled(directory, request_id):
        result_path.unlink(missing_ok=True)
    return True
