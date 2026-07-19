"""bulk_downloader.disco_triage -- A-DISCO cut 3: the triage + auto-queue
orchestrator that ties enumeration to the queue.

Composes cut 2 (``host_enumerator``) + cut 1 (``candidate_filter`` confidence
tier) + the queue: enumerate an already-approved host (bounded, host-confined) ->
score every candidate URL -> auto-queue the high-confidence ones (best-first,
capped) -> stage the uncertain ones for operator review -> drop the junk. It is
DEFAULT-OFF and DARK: nothing imports it yet. Cut 4 wires the toggle, the
scheduler task, the operator route, and durable persistence.

TRIAGE of an enumerated PAGE URL (not a template download control):
  * ``classify`` supplies the junk rejections (nav / account / search / social /
    homepage / generic / off-host / internal) -> ``reject``;
  * a strong-signal media/download URL -> ``high`` (cut 1 tier);
  * a URL matching the operator's per-host content pattern -> ``high`` (the same
    ``url_pattern`` mechanism ``discovery.py`` uses to say "these are content");
  * anything else clean-but-unsignalled -> ``review``. FAIL-TO-REVIEW: a clean
    page with no content-pattern match is NEVER auto-queued.

SAFETY inherited, not re-invented:
  * the MASTER OFF-SWITCH dominates (default = ``automation_controller``'s), and
    an unreadable off-switch fails safe to INERT -- nothing enumerated or queued;
  * host confinement is the enumerator's (off-host URLs never reach the queue);
  * the AR4 per-run enqueue cap bounds how many URLs one run can queue, on top of
    the enumeration budget that already bounds how many candidates one run yields.
The seams (fetch, enqueue, seen, content-match, persist, off-switch) are injected
so this stays a pure, offline-testable orchestrator.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from . import candidate_filter as cf
from . import host_enumerator as he

# classify() rejections that mean "structurally not a capture target" -- drop.
# "no download signal" / "no url, no trigger signal" are deliberately NOT here:
# those are a clean page that merely lacks a media URL, i.e. the review case.
_STRUCTURAL_REJECTIONS = frozenset({
    "nav/header/footer",
    "search/settings/login/logout",
    "search/filter",
    "share/favorite/comment/vote",
    "external/unrelated link",
    "generic href-only selector",
    "homepage link",
    "navigation URL",
    "internal/non-public host",
})

_CONTENT_MATCH_SCORE = 0.8   # floor score for a content-pattern-matched page


def _default_off_switch() -> bool:
    """The master automation off-switch. A-DISCO inherits the single kill path
    rather than inventing its own. Fail-safe is the controller's (unreadable ->
    engaged)."""
    from . import automation_controller as ac
    return ac.off_switch_engaged()


def triage_url(url: str, *, page_host: Optional[str] = None,
               content_match_fn: Optional[Callable[[str], bool]] = None
               ) -> Tuple[str, float]:
    """Triage one enumerated URL -> ``(tier, score)``. See the module docstring
    for the rules. Fail-to-review: anything that is neither clear junk nor a
    high-confidence download/content-page lands in ``review``, never auto-queue."""
    v = cf.classify(url=url, page_host=page_host)
    score = cf.confidence_score(v)
    # 1. structural junk -> reject.
    if set(v.rejections) & _STRUCTURAL_REJECTIONS:
        return cf.TIER_REJECT, score
    # 2. a strong-signal media/download URL -> high (cut 1 tier).
    if cf.confidence_tier(v) == cf.TIER_HIGH:
        return cf.TIER_HIGH, score
    # 3. operator content pattern promotes a clean page to high.
    if content_match_fn is not None:
        try:
            if content_match_fn(url):
                return cf.TIER_HIGH, max(score, _CONTENT_MATCH_SCORE)
        except Exception:
            pass
    # 4. clean but unsignalled (or a trigger) -> review (fail-to-review).
    return cf.TIER_REVIEW, score


def run_discovery_triage(root_url: str, *,
                         fetch_fn: Callable[[str], Optional[str]],
                         enqueue_fn: Callable[[str], Any],
                         seen_fn: Optional[Callable[[str], bool]] = None,
                         content_match_fn: Optional[Callable[[str], bool]] = None,
                         budget: Optional["he.EnumBudget"] = None,
                         max_enqueue: int = 0,
                         off_switch_fn: Optional[Callable[[], bool]] = None,
                         persist_fn: Optional[Callable[[Dict[str, Any]], Any]] = None,
                         page_host: Optional[str] = None,
                         ) -> Dict[str, Any]:
    """Enumerate ``root_url`` (approved host), triage its candidates, and auto-queue
    the high-confidence tier via ``enqueue_fn`` -- capped by ``max_enqueue`` (0 =
    uncapped; the enumeration budget still bounds the total), highest score first.
    The ``review`` tier is returned (staged), never queued. Returns a run record.

    The MASTER OFF-SWITCH is checked FIRST: engaged (or unreadable) -> INERT, and
    nothing is enumerated or queued.
    """
    off = off_switch_fn or _default_off_switch
    try:
        engaged = bool(off())
    except Exception:
        engaged = True                       # fail-safe: unreadable -> inert
    if engaged:
        return {"inert": True, "reason": "master_off_switch",
                "root": root_url, "host": "", "enumerated": 0,
                "high": [], "review": [], "reject": 0, "enqueued": 0,
                "capped": False}

    enum = he.enumerate_host(root_url, fetch_fn=fetch_fn, seen_fn=seen_fn,
                             budget=budget)
    ph = page_host or enum.get("host") or ""

    high: List[Tuple[str, float]] = []
    review: List[str] = []
    reject_n = 0
    for url in enum.get("candidates", []):
        tier, score = triage_url(url, page_host=ph,
                                 content_match_fn=content_match_fn)
        if tier == cf.TIER_HIGH:
            high.append((url, score))
        elif tier == cf.TIER_REVIEW:
            review.append(url)
        else:
            reject_n += 1

    # AR4 per-run enqueue cap: best-first, capped. A breach is VISIBLE (capped).
    high.sort(key=lambda t: t[1], reverse=True)
    to_queue = high if not max_enqueue else high[:max_enqueue]
    enqueued = 0
    for url, _score in to_queue:
        try:
            n = enqueue_fn(url)
            if n is None or n:               # None or truthy count -> queued
                enqueued += 1
        except Exception:
            pass                             # isolate a bad enqueue; run continues

    rec: Dict[str, Any] = {
        "inert": False, "root": root_url, "host": ph,
        "enumerated": len(enum.get("candidates", [])),
        "high": [u for u, _ in high], "review": review, "reject": reject_n,
        "enqueued": enqueued,
        "capped": bool(max_enqueue) and len(high) > max_enqueue,
        "enum_halted": enum.get("halted", False),
        "enum_halt_reason": enum.get("halt_reason", ""),
    }
    if persist_fn is not None:
        try:
            persist_fn(rec)
        except Exception:
            pass
    return rec
