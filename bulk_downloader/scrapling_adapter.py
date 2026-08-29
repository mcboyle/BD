"""v3.43.73: Scrapling adapter — adaptive selectors + Turnstile bypass.

# What this module is

A fail-open wrapper around the Scrapling library that gives BD two
new superpowers:

  1. ADAPTIVE SELECTORS: when a learned CSS/XPath selector fails at
     runtime (the site tweaked its markup), Scrapling can re-find the
     same element using a content-based fingerprint we stored at teach
     time. This is automatic selector recovery — no re-teaching needed.

  2. TURNSTILE BYPASS: Cloudflare's Turnstile challenge (replacement
     for reCAPTCHA) is increasingly common on premium adult sites.
     Scrapling's StealthyFetcher handles it natively via a one-shot
     request that returns the post-challenge cookies. BD can hand
     those cookies to its existing Playwright session to skip the
     challenge altogether.

# Architecture

Scrapling types stay inside this module. The rest of BD interacts
through plain dict-shaped fingerprints and HTML strings. No
Scrapling import leaks out of `scrapling_adapter.py`.

  - `is_available()` — Scrapling's Adaptor capability is callable
  - `build_fingerprint(html, selector)` — capture a content-based
    fingerprint of the element matched by `selector`
  - `recover_selector(html, fingerprint)` — re-locate an element
     given the saved fingerprint
  - `is_turnstile_page(html)` — detect Turnstile challenge
  - `bypass_turnstile(url, ...)` — run StealthyFetcher, return cookies
  - `stats()` — runtime counters for dashboard widget

# Fingerprint shape

A fingerprint is a JSON-serializable dict:

    {
        "tag": "a",
        "text_hash": "abc123...",     # SHA-256 of normalized inner text
        "attrs": {"class": [...], "id": "foo"},
        "ancestor_tags": ["body", "div", "main"],
        "child_count": 0,
        "version": 1,
    }

We use SHA-256 over normalized text (whitespace-collapsed, lowercased)
plus tag + ancestor chain. This is robust to most site tweaks: class
renames, sibling reordering, ID changes don't kill it as long as the
visible content stays similar.

# Fail-open everywhere

Missing Scrapling → all functions return None / False. Scrapling
itself raised → log debug, return None. The caller treats None as
"recovery unavailable" and falls through to its existing failure path
(needs_review, etc).
"""
from __future__ import annotations

import hashlib
import importlib
import logging
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ─── Availability ──────────────────────────────────────────────────


def _probe_capability(symbol: str, *, available_reason: str,
                      unavailable_reason: str,
                      required_callable: str = "") -> dict:
    """Measure one Scrapling symbol without turning probe failure into OK.

    A missing package, transitive dependency, or expected symbol is a measured
    unavailable capability.  An unrelated exception means the measurement
    itself failed, so its status is UNKNOWN even though callers still receive a
    fail-closed ``available=False`` boolean.
    """
    try:
        module = importlib.import_module("scrapling")
        capability = getattr(module, symbol)
        if not callable(capability):
            raise AttributeError(f"{symbol} is not callable")
        if required_callable and not callable(
                getattr(capability, required_callable, None)):
            raise AttributeError(
                f"{symbol}.{required_callable} is not callable")
    except ModuleNotFoundError as exc:
        missing = str(getattr(exc, "name", "") or "").strip()
        reason = (
            "scrapling_not_installed"
            if missing == "scrapling"
            else f"missing_dependency:{missing or 'unknown'}"
        )
        return {"available": False, "status": "unavailable", "reason": reason}
    except (ImportError, AttributeError):
        return {
            "available": False,
            "status": "unavailable",
            "reason": unavailable_reason,
        }
    except Exception as exc:  # noqa: BLE001 - UNKNOWN is distinct from absent
        return {
            "available": False,
            "status": "unknown",
            "reason": f"capability_probe_failed:{type(exc).__name__}",
        }
    return {"available": True, "status": "available", "reason": available_reason}


def capability_status() -> dict:
    """Return independently measured adaptive-selector and bypass states."""
    return {
        "adaptive_selectors": _probe_capability(
            "Adaptor",
            available_reason="adaptor_available",
            unavailable_reason="adaptor_unavailable",
        ),
        "turnstile_bypass": _probe_capability(
            "StealthyFetcher",
            available_reason="stealthy_fetcher_available",
            unavailable_reason="stealthy_fetcher_unavailable",
            required_callable="fetch",
        ),
    }


def is_available() -> bool:
    """True if Scrapling's adaptive-selector capability imports cleanly."""
    return bool(_probe_capability(
        "Adaptor",
        available_reason="adaptor_available",
        unavailable_reason="adaptor_unavailable",
    )["available"])


def is_stealthy_fetcher_available() -> bool:
    """True when StealthyFetcher imports and exposes callable ``fetch``."""
    return bool(_probe_capability(
        "StealthyFetcher",
        available_reason="stealthy_fetcher_available",
        unavailable_reason="stealthy_fetcher_unavailable",
        required_callable="fetch",
    )["available"])


def turnstile_probe_verdict(state: dict) -> tuple[int, str]:
    """Map a measured bypass state to provisioning's three exit states."""
    measured = state if isinstance(state, dict) else {}
    status = str(measured.get("status") or "unknown")
    reason = str(measured.get("reason") or "measurement_not_supplied")
    if status == "available" and measured.get("available") is True:
        return 0, f"available:{reason}"
    if status == "unavailable" and measured.get("available") is False:
        return 1, f"unavailable:{reason}"
    return 2, f"unknown:{reason}"


def _main(argv: Optional[list[str]] = None) -> int:
    """Machine-facing runtime probe used by cloud provisioning."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["--probe-turnstile"]:
        print(
            "usage: python -m bulk_downloader.scrapling_adapter "
            "--probe-turnstile",
            file=sys.stderr,
        )
        return 2
    code, detail = turnstile_probe_verdict(
        capability_status()["turnstile_bypass"])
    print(detail)
    return code


# ─── Runtime stats ─────────────────────────────────────────────────


@dataclass
class ScraplingStats:
    """Counters maintained by the adapter for the dashboard widget."""
    fingerprints_built: int = 0
    recoveries_attempted: int = 0
    recoveries_succeeded: int = 0
    turnstile_detected: int = 0
    turnstile_bypassed: int = 0
    turnstile_failed: int = 0


_stats = ScraplingStats()
_stats_lock = threading.Lock()


def stats() -> dict:
    """Return a snapshot of the counters."""
    with _stats_lock:
        return {
            "fingerprints_built": _stats.fingerprints_built,
            "recoveries_attempted": _stats.recoveries_attempted,
            "recoveries_succeeded": _stats.recoveries_succeeded,
            "recovery_success_rate": (
                _stats.recoveries_succeeded / _stats.recoveries_attempted
                if _stats.recoveries_attempted > 0 else 0.0
            ),
            "turnstile_detected": _stats.turnstile_detected,
            "turnstile_bypassed": _stats.turnstile_bypassed,
            "turnstile_failed": _stats.turnstile_failed,
            "turnstile_success_rate": (
                _stats.turnstile_bypassed / _stats.turnstile_detected
                if _stats.turnstile_detected > 0 else 0.0
            ),
        }


def reset_stats() -> None:
    """Zero the counters. Used by tests; not exposed in production."""
    global _stats
    with _stats_lock:
        _stats = ScraplingStats()


def _bump(field: str, by: int = 1) -> None:
    """Thread-safe counter increment."""
    with _stats_lock:
        setattr(_stats, field, getattr(_stats, field) + by)


# ─── Text normalization for content hashing ────────────────────────


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """Collapse whitespace + lowercase for hashing. Content hashes
    survive minor formatting changes (extra spaces, line breaks)."""
    if not isinstance(text, str):
        return ""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _text_hash(text: str) -> str:
    """SHA-256 of normalized text, hex-encoded. Returns empty string
    for empty input — caller can decide whether to use the fingerprint."""
    norm = _normalize_text(text)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


# ─── Fingerprint capture + recovery ────────────────────────────────


def build_fingerprint(html: str, selector: str) -> Optional[dict]:
    """Build a content-based fingerprint of the element matched by
    `selector` in `html`. Returns None if Scrapling unavailable OR
    selector doesn't match anything.

    Fingerprint is JSON-serializable and small (under 1KB typical) so
    it can live alongside the learned selectors in the site config.
    """
    if not is_available():
        return None
    if not html or not selector:
        return None
    try:
        from scrapling import Adaptor
        # Scrapling's Adaptor wraps HTML and provides CSS/XPath
        # selection. auto_save=True makes the resulting element
        # carry the metadata we'll later use for recovery.
        page = Adaptor(html, auto_match=True, debug=False)
        el = page.css_first(selector)
        if el is None:
            return None
        # Build the fingerprint manually so it doesn't depend on
        # internal Scrapling representation (which varies across
        # versions). We capture enough features to re-locate on
        # similar pages even when the selector breaks.
        text_content = ""
        try:
            text_content = el.text or ""
        except Exception:
            pass
        # Attrs as a flat dict (Scrapling returns various shapes)
        attrs: dict = {}
        try:
            raw_attrs = getattr(el, "attrib", None) or \
                        getattr(el, "attrs", None) or {}
            if hasattr(raw_attrs, "items"):
                for k, v in raw_attrs.items():
                    if isinstance(v, str):
                        if k == "class":
                            attrs[k] = sorted(v.split())
                        else:
                            attrs[k] = v
        except Exception:
            pass
        # Ancestor tag chain (3-4 levels up is robust)
        ancestor_tags: list = []
        try:
            parent = getattr(el, "parent", None)
            depth = 0
            while parent is not None and depth < 4:
                tag = getattr(parent, "tag", None)
                if tag:
                    ancestor_tags.append(tag.lower())
                parent = getattr(parent, "parent", None)
                depth += 1
        except Exception:
            pass
        # Child count (rough structural fingerprint)
        child_count = 0
        try:
            children = getattr(el, "children", None) or []
            child_count = len(list(children))
        except Exception:
            pass
        fp = {
            "version": 1,
            "tag": getattr(el, "tag", "").lower() or "",
            "text_hash": _text_hash(text_content),
            "text_preview": _normalize_text(text_content)[:120],
            "attrs": attrs,
            "ancestor_tags": ancestor_tags,
            "child_count": child_count,
            "original_selector": selector,
            "built_at": time.time(),
        }
        _bump("fingerprints_built")
        return fp
    except Exception as e:
        log.debug("scrapling: build_fingerprint raised %s", e)
        return None


def _candidate_score(el, fingerprint: dict) -> float:
    """Score how well an element matches the fingerprint. 1.0 = perfect
    match; 0.0 = nothing matches. Used by recover_selector to pick the
    best candidate."""
    score = 0.0
    weights = {
        "tag": 0.15,
        "text_hash": 0.40,
        "text_preview": 0.20,  # partial credit when hash doesn't match
        "class_set": 0.15,
        "id": 0.05,
        "ancestor_tags": 0.05,
    }
    # Tag match
    try:
        if (getattr(el, "tag", "") or "").lower() == fingerprint.get("tag", ""):
            score += weights["tag"]
    except Exception:
        pass
    # Text hash match (exact) — this is the strongest signal
    try:
        el_text_hash = _text_hash(el.text or "")
        if el_text_hash and el_text_hash == fingerprint.get("text_hash"):
            score += weights["text_hash"]
        else:
            # Partial credit: text contains fingerprint preview
            preview = fingerprint.get("text_preview", "")
            if preview and preview in _normalize_text(el.text or ""):
                score += weights["text_preview"]
    except Exception:
        pass
    # Class set overlap
    try:
        fp_classes = set(fingerprint.get("attrs", {}).get("class", []))
        if fp_classes:
            raw = getattr(el, "attrib", None) or getattr(el, "attrs", None) or {}
            el_classes_raw = raw.get("class", "") if hasattr(raw, "get") else ""
            el_classes = set(el_classes_raw.split()) if isinstance(el_classes_raw, str) else set()
            overlap = len(fp_classes & el_classes)
            if overlap > 0:
                score += weights["class_set"] * (overlap / len(fp_classes))
    except Exception:
        pass
    # ID match
    try:
        fp_id = fingerprint.get("attrs", {}).get("id", "")
        if fp_id:
            raw = getattr(el, "attrib", None) or getattr(el, "attrs", None) or {}
            el_id = raw.get("id", "") if hasattr(raw, "get") else ""
            if el_id == fp_id:
                score += weights["id"]
    except Exception:
        pass
    return score


@dataclass
class RecoveryResult:
    """Outcome of recover_selector."""
    ok: bool
    selector: str = ""        # NEW selector that locates the element
    score: float = 0.0        # match confidence (0.0-1.0)
    candidates_considered: int = 0
    error: str = ""


def recover_selector(
    html: str, fingerprint: dict,
    *, min_score: float = 0.6,
) -> RecoveryResult:
    """Re-locate an element using a stored fingerprint. Returns a
    RecoveryResult with a NEW selector that finds the element, plus
    a confidence score.

    Strategy: enumerate elements with the same tag, score each against
    the fingerprint, return the highest-scoring candidate above
    `min_score`. The new selector is built from a unique attribute on
    the winning element (id preferred, then a class+text combo).

    Returns ok=False if Scrapling unavailable, fingerprint malformed,
    or no candidate meets `min_score`.
    """
    if not is_available():
        return RecoveryResult(ok=False, error="scrapling_not_installed")
    if not html or not fingerprint or not isinstance(fingerprint, dict):
        return RecoveryResult(ok=False, error="invalid_inputs")
    tag = fingerprint.get("tag", "")
    if not tag:
        return RecoveryResult(ok=False, error="fingerprint_missing_tag")
    _bump("recoveries_attempted")
    try:
        from scrapling import Adaptor
        page = Adaptor(html, auto_match=False, debug=False)
        # Try the original selector first (cheapest path)
        orig_sel = fingerprint.get("original_selector", "")
        if orig_sel:
            try:
                el = page.css_first(orig_sel)
                if el is not None:
                    score = _candidate_score(el, fingerprint)
                    if score >= min_score:
                        _bump("recoveries_succeeded")
                        return RecoveryResult(
                            ok=True, selector=orig_sel, score=score,
                            candidates_considered=1,
                        )
            except Exception:
                pass
        # Fallback: enumerate by tag, score each
        candidates: list = []
        try:
            tag_matches = page.css(tag) or []
        except Exception:
            tag_matches = []
        for el in tag_matches:
            try:
                s = _candidate_score(el, fingerprint)
                if s >= min_score:
                    candidates.append((s, el))
            except Exception:
                continue
        if not candidates:
            return RecoveryResult(
                ok=False, error="no_candidates_above_threshold",
                candidates_considered=len(tag_matches),
            )
        # Sort descending by score
        candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_el = candidates[0]
        # Build a NEW selector from the best element's attributes
        new_selector = _build_new_selector(best_el, tag)
        if not new_selector:
            return RecoveryResult(
                ok=False, error="failed_to_synthesize_new_selector",
                candidates_considered=len(tag_matches),
            )
        _bump("recoveries_succeeded")
        return RecoveryResult(
            ok=True, selector=new_selector, score=best_score,
            candidates_considered=len(tag_matches),
        )
    except Exception as e:
        log.debug("scrapling: recover_selector raised %s", e)
        return RecoveryResult(
            ok=False, error=f"{type(e).__name__}:{str(e)[:120]}",
        )


def _build_new_selector(el, tag: str) -> str:
    """Synthesize a new CSS selector for an element. Prefers id, then
    a unique class, then tag + nth-of-type fallback."""
    try:
        # Use explicit `is None` checks because an empty dict {} is
        # falsy in Python, and `getattr(el, "attrib", None) or
        # getattr(el, "attrs", None)` would skip past a legitimate
        # empty `attrib` to a different attribute. Real Scrapling
        # elements expose `.attrib` (lxml convention); Scrapling
        # versions before 0.2 used `.attrs`.
        raw = getattr(el, "attrib", None)
        if raw is None:
            raw = getattr(el, "attrs", None)
        if raw is None:
            raw = {}
        if hasattr(raw, "get"):
            el_id = raw.get("id", "")
            if el_id:
                return f"#{el_id}"
            el_class = raw.get("class", "")
            if el_class:
                # Pick the most-specific-looking class
                classes = [c for c in el_class.split() if c and not c.startswith("hover:")]
                if classes:
                    return f"{tag}.{classes[0]}"
    except Exception:
        pass
    return tag  # last-resort fallback: tag alone


# ─── Turnstile detection + bypass ──────────────────────────────────


_TURNSTILE_SIGNALS = [
    "challenges.cloudflare.com",
    "cf-turnstile",
    "cf-clearance-required",
    "data-sitekey=",
    "turnstile-wrapper",
]


def is_turnstile_page(html: str) -> bool:
    """Heuristic: does this HTML look like a Cloudflare Turnstile
    challenge page? Looks for any of several known signal strings.

    False positives are mostly harmless (we'd attempt a bypass that
    fails through gracefully). False negatives are worse (we'd miss
    the bypass opportunity) but unlikely given Cloudflare's tooling.

    Pure check — does NOT increment the turnstile_detected counter.
    Counter bumps happen at the dispatch point (when we actually
    decide to attempt a bypass) so they're meaningful.
    """
    if not html or not isinstance(html, str):
        return False
    lowered = html.lower()
    for sig in _TURNSTILE_SIGNALS:
        if sig.lower() in lowered:
            return True
    return False


def note_turnstile_detected() -> None:
    """Bump the turnstile_detected counter. Called by the dispatcher
    when it commits to attempting a bypass."""
    _bump("turnstile_detected")


@dataclass
class BypassResult:
    """Outcome of bypass_turnstile."""
    ok: bool
    cookies: list = field(default_factory=list)  # list of dicts {name, value, domain, ...}
    html: str = ""           # final page HTML after challenge
    elapsed_s: float = 0.0
    error: str = ""


def bypass_turnstile(
    url: str,
    *, user_agent: str = "",
    timeout_s: float = 60.0,
    headless: bool = True,
) -> BypassResult:
    """Run StealthyFetcher against `url` to clear a Turnstile challenge.

    Returns BypassResult with cookies + final HTML on success. The
    caller (typically the SiteRunner) can inject the cookies into the
    existing Playwright context to skip the challenge for follow-up
    requests.

    `timeout_s` is the hard limit on the whole operation including
    the challenge wait. Cloudflare's challenges typically complete in
    5-15 seconds; default 60 leaves headroom.

    Fail-open: missing Scrapling or StealthyFetcher → returns
    BypassResult(ok=False) with a clear error string.
    """
    if not is_stealthy_fetcher_available():
        return BypassResult(ok=False, error="stealthy_fetcher_unavailable")
    if not url:
        return BypassResult(ok=False, error="empty_url")
    start = time.monotonic()
    try:
        from scrapling import StealthyFetcher
        # StealthyFetcher.fetch signature varies; we use the
        # documented kwargs in a defensive try/except.
        kwargs = {
            "url": url,
            "headless": headless,
            "network_idle": True,
            "timeout": int(timeout_s * 1000),  # most versions expect ms
        }
        if user_agent:
            kwargs["useragent"] = user_agent
        try:
            page = StealthyFetcher.fetch(**kwargs)
        except TypeError:
            # Older Scrapling: positional URL + minimal kwargs
            page = StealthyFetcher.fetch(url)
    except Exception as e:
        _bump("turnstile_failed")
        return BypassResult(
            ok=False,
            error=f"{type(e).__name__}:{str(e)[:160]}",
            elapsed_s=time.monotonic() - start,
        )
    # Extract cookies + HTML from the result
    cookies: list = []
    final_html = ""
    try:
        # Scrapling's response object exposes a `.cookies` property
        # (sometimes called `.session_cookies`). Try both.
        raw_cookies = getattr(page, "cookies", None) or \
                      getattr(page, "session_cookies", None) or []
        for c in raw_cookies:
            if hasattr(c, "name") and hasattr(c, "value"):
                cookies.append({
                    "name": c.name, "value": c.value,
                    "domain": getattr(c, "domain", ""),
                    "path": getattr(c, "path", "/"),
                    "secure": getattr(c, "secure", False),
                    "httpOnly": getattr(c, "http_only", False),
                })
            elif isinstance(c, dict):
                cookies.append(c)
        # HTML body
        final_html = getattr(page, "html_content", "") or \
                     getattr(page, "text", "") or ""
    except Exception as e:
        log.debug("scrapling: bypass result parse raised %s", e)
    elapsed = time.monotonic() - start
    # If we got HTML but no cookies AND the HTML still looks like a
    # challenge page, the bypass didn't actually work.
    if final_html and is_turnstile_page(final_html) and not cookies:
        _bump("turnstile_failed")
        return BypassResult(
            ok=False, error="bypass_completed_but_still_challenged",
            html=final_html, elapsed_s=elapsed,
        )
    _bump("turnstile_bypassed")
    return BypassResult(
        ok=True, cookies=cookies, html=final_html,
        elapsed_s=elapsed,
    )


__all__ = [
    "capability_status",
    "turnstile_probe_verdict",
    "is_available",
    "is_stealthy_fetcher_available",
    "ScraplingStats",
    "stats",
    "reset_stats",
    "build_fingerprint",
    "recover_selector",
    "RecoveryResult",
    "is_turnstile_page",
    "note_turnstile_detected",
    "bypass_turnstile",
    "BypassResult",
]


if __name__ == "__main__":
    raise SystemExit(_main())
