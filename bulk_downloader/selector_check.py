"""Phase 9.12 -- selector self-consistency pass.

Second-pass validation of selector suggestions before surfacing. A deterministic
resolver checks each candidate against the captured DOM excerpt and is the AUTHORITY:
zero-match -> reject, too-many -> review, brittle (nth-child / hashy class) -> warn,
honeypot/trap conflict -> review. The model may explain brittleness or suggest a
safer variant, but it can NEVER mark a candidate approved -- `merge_model` enforces
that the deterministic verdict wins.
"""

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

_TOO_MANY = 5
_BRITTLE_RE = re.compile(r":nth-child|:nth-of-type|\[\d+\]|[._-][a-f0-9]{8,}", re.I)
_TRAP_WORDS = ("honeypot", "trap", "decoy", "hidden-bot", "ad-slot", "advert")


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements: List[Dict[str, Any]] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        self.elements.append({"tag": tag, "id": a.get("id", ""),
                              "class": a.get("class", ""), "attrs": a})


def _parse(dom_excerpt: str) -> List[Dict[str, Any]]:
    c = _Collector()
    try:
        c.feed(dom_excerpt or "")
    except Exception:
        pass
    return c.elements


def _matches(sel: str, el: Dict[str, Any]) -> bool:
    sel = sel.strip()
    # strip pseudo / index suffixes for matching the base target
    base = re.split(r":nth-child|:nth-of-type|\[", sel)[0].strip()
    if not base:
        base = sel
    if base.startswith("#"):
        return el["id"] == base[1:]
    if base.startswith("."):
        return base[1:] in el["class"].split()
    m = re.match(r"^([a-zA-Z][\w-]*)(?:\.([\w-]+))?$", base)
    if m:
        tag, cls = m.group(1), m.group(2)
        if el["tag"] != tag:
            return False
        if cls and cls not in el["class"].split():
            return False
        return True
    return False


def resolve(selector: str, dom_excerpt: str) -> Dict[str, Any]:
    """Deterministic resolver: how many elements in the excerpt match the selector."""
    els = _parse(dom_excerpt)
    hits = [e for e in els if _matches(selector, e)]
    return {"count": len(hits), "matches": hits}


def check_candidate(selector: str, dom_excerpt: str) -> Dict[str, Any]:
    """Deterministic verdict for a candidate selector."""
    r = resolve(selector, dom_excerpt)
    count = r["count"]
    if count == 0:
        return {"status": "reject", "reason": "zero-match", "count": count}
    # honeypot/trap conflict on the matched element(s)
    for el in r["matches"]:
        blob = (el["id"] + " " + el["class"]).lower()
        if any(w in blob for w in _TRAP_WORDS):
            return {"status": "review", "reason": "honeypot/trap conflict", "count": count}
    if count > _TOO_MANY:
        return {"status": "review", "reason": "too-many-matches", "count": count}
    if _BRITTLE_RE.search(selector):
        return {"status": "warn", "reason": "brittle (nth-child/hash)", "count": count}
    return {"status": "approve", "reason": "single stable match", "count": count}


def merge_model(deterministic_status: str, model_says_approved: bool) -> str:
    """The deterministic resolver is the authority. A model opinion can never
    upgrade a non-approved verdict to 'approve'."""
    if deterministic_status == "approve":
        return "approve"
    # model cannot promote reject/review/warn to approve
    return deterministic_status
