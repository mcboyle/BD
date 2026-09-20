"""Overlapping z-index / pointer-events overlay inspector (Row 946).

Before a candidate control is clicked, prove it is the element that would
actually receive the click. Two failure shapes this catches:

* a transparent or near-transparent overlay (cookie shield, modal backdrop,
  z-indexed ad frame) sits above the control, so ``elementFromPoint`` at the
  control's centre returns the overlay instead;
* the control itself is styled ``pointer-events: none`` and can never
  receive the click, whatever is above it.

Two halves, mirroring :mod:`bulk_downloader.dom_overlay`:

* **Pure half (sandbox-testable):** :func:`classify_pointer_probe` turns the
  in-page probe result into a :class:`PointerVerdict`.
* **Live half:** :func:`inspect_pointer_target` runs :data:`INSPECT_JS` via
  ``locator.evaluate`` and classifies. Fail-open as ``unknown`` on any error:
  the inspector never raises into the interaction loop.

Read-only: the probe mutates nothing and dispatches no events.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict

REASON_OBSCURED = "obscured_by_overlay"
REASON_POINTER_EVENTS_NONE = "pointer_events_none"
REASON_OFF_VIEWPORT = "off_viewport"
REASON_UNKNOWN = "unknown"

# Hit-tests the target's centre point; reports whether the topmost element
# there is the target (or a descendant of it) and, if not, who it is.
INSPECT_JS = r"""
el => {
  // A pre-click gate judges the ELEMENT, not the scroll position: bring the
  // target into view first (Playwright's own actionability does the same),
  // then hit-test its centre.
  try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
  const r = el.getBoundingClientRect();
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  const s = getComputedStyle(el);
  const inView = r.width > 0 && r.height > 0 &&
    cx >= 0 && cy >= 0 && cx <= innerWidth && cy <= innerHeight;
  // Descend through open shadow roots: document.elementFromPoint stops at a
  // shadow host, so the innermost hit is the one to compare against.
  let hit = inView ? document.elementFromPoint(cx, cy) : null;
  let depth = 0;
  while (hit && hit.shadowRoot && depth < 32) {
    const inner = hit.shadowRoot.elementFromPoint(cx, cy);
    if (!inner || inner === hit) break;
    hit = inner; depth++;
  }
  // containment on the composed tree: walk up through shadow hosts
  const composedContains = (root, node) => {
    for (let n = node; n; ) {
      if (n === root) return true;
      n = n.parentNode || (n.host ? n.host : null);
      if (n && n.nodeType === 11 && n.host) n = n.host;  // DocumentFragment -> host
    }
    return false;
  };
  const hs = hit ? getComputedStyle(hit) : null;
  return {
    in_viewport: inView,
    scrolled_into_view: true,
    pointer_events: s.pointerEvents,
    hit_is_target: !!hit && (hit === el || composedContains(el, hit)),
    hit_tag: hit ? hit.tagName.toLowerCase() : '',
    hit_id: hit ? (hit.id || '') : '',
    hit_class: hit ? (hit.className && hit.className.baseVal !== undefined
                      ? hit.className.baseVal : String(hit.className || '')) : '',
    hit_z_index: hs ? hs.zIndex : '',
    hit_opacity: hs ? hs.opacity : ''
  };
}
""".strip()


@dataclass(frozen=True)
class PointerVerdict:
    interactive: bool
    reason: str = ""
    blocker: str = ""
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _describe_hit(probe: Dict[str, Any]) -> str:
    tag = str(probe.get("hit_tag") or "")
    hid = str(probe.get("hit_id") or "")
    cls = str(probe.get("hit_class") or "").split()
    out = tag
    if hid:
        out += "#" + hid
    if cls:
        out += "." + ".".join(cls)
    return out


def classify_pointer_probe(probe: Dict[str, Any]) -> PointerVerdict:
    if str(probe.get("pointer_events") or "").lower() == "none":
        return PointerVerdict(False, REASON_POINTER_EVENTS_NONE,
                              detail="target computed pointer-events:none")
    if not probe.get("in_viewport"):
        # After scrollIntoView an off-viewport centre means zero-size or
        # unscrollable (position:fixed off-screen): reported, not "obscured".
        return PointerVerdict(False, REASON_OFF_VIEWPORT,
                              detail="target centre outside viewport after scrollIntoView, or zero-size")
    if probe.get("hit_is_target"):
        return PointerVerdict(True)
    blocker = _describe_hit(probe)
    detail = "z=%s opacity=%s" % (probe.get("hit_z_index") or "auto",
                                  probe.get("hit_opacity") or "1")
    return PointerVerdict(False, REASON_OBSCURED, blocker=blocker, detail=detail)


def inspect_pointer_target(locator) -> PointerVerdict:
    try:
        probe = locator.evaluate(INSPECT_JS)
    except Exception as exc:
        return PointerVerdict(False, REASON_UNKNOWN, detail="evaluate failed: %s" % exc)
    if not isinstance(probe, dict):
        return PointerVerdict(False, REASON_UNKNOWN,
                              detail="probe returned %s" % type(probe).__name__)
    return classify_pointer_probe(probe)
