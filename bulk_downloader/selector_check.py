"""Phase 9.12 -- selector self-consistency pass.

Second-pass validation of selector suggestions before surfacing. A deterministic
resolver checks each candidate against the captured DOM excerpt and is the AUTHORITY:
zero-match -> reject, not evaluable here -> review, too-many -> review,
brittle (nth-child / hashy class) -> warn, honeypot/trap conflict -> review. The model may explain brittleness or suggest a
safer variant, but it can NEVER mark a candidate approved -- `merge_model` enforces
that the deterministic verdict wins.
"""

import re
from typing import Any, Dict, List, Optional

_TOO_MANY = 5
_BRITTLE_RE = re.compile(r":nth-child|:nth-of-type|\[\d+\]|[._-][a-f0-9]{8,}", re.I)
_TRAP_WORDS = ("honeypot", "trap", "decoy", "hidden-bot", "ad-slot", "advert")


# Brittle index parts are dropped before resolving, so the verdict is about the
# base target ("#dl:nth-child(2)" is judged as "#dl" and then warned as brittle).
_INDEX_PART_RE = re.compile(r":nth-child\([^)]*\)|:nth-of-type\([^)]*\)|\[\d+\]")
# Playwright's text pseudo-class, as teach synthesizes it: button:has-text('Log in')
_HAS_TEXT_RE = re.compile(r""":has-text\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*\)""")


def _norm_text(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _split_has_text(selector: str):
    """Split trailing Playwright ``:has-text('...')`` pseudo-classes from a
    CSS selector into ``(css, wanted)``, or ``None`` when the selector uses
    Playwright text syntax that cannot be evaluated as CSS + element text."""
    base = selector.strip()
    if base.startswith("text=") or ">>" in base:
        return None
    wanted = [_norm_text(re.sub(r"\\(.)", r"\1", a or b))
              for a, b in _HAS_TEXT_RE.findall(base)]
    css = _HAS_TEXT_RE.sub("", base).strip()
    # :has-text is resolved only where it ends the selector: there it filters
    # the matched elements themselves.
    if wanted and (not base.startswith(css)
                   or _HAS_TEXT_RE.sub("", base[len(css):]).strip()):
        return None
    if ":has-text" in css.lower():
        return None
    if not css:
        if not wanted:
            return None
        css = "*"
    return css, wanted


def _has_wanted_text(text: str, wanted: List[str]) -> bool:
    if wanted and not all(w in _norm_text(text) for w in wanted):
        return False
    return True


def _select(selector: str, dom_excerpt: str) -> Optional[List[Dict[str, Any]]]:
    """Elements of the excerpt the selector matches, or None when the selector
    cannot be evaluated here (not CSS, or no CSS engine installed)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    base = _INDEX_PART_RE.sub("", selector.strip()).strip()
    split = _split_has_text(base)
    if split is None:
        return None
    css, wanted = split
    try:
        found = BeautifulSoup(dom_excerpt or "", "html.parser").select(css)
    except Exception:  # soupsieve SelectorSyntaxError / unsupported pseudo-class
        return None
    out = []
    for el in found:
        if not _has_wanted_text(el.get_text(), wanted):
            continue
        attrs = {k: (" ".join(v) if isinstance(v, list) else (v or ""))
                 for k, v in el.attrs.items()}
        out.append({"tag": el.name, "id": attrs.get("id", ""),
                    "class": attrs.get("class", ""), "attrs": attrs})
    return out


def resolve(selector: str, dom_excerpt: str) -> Dict[str, Any]:
    """Deterministic resolver: how many elements in the excerpt match the selector.
    ``count`` is None when the selector could not be evaluated."""
    hits = _select(selector or "", dom_excerpt)
    if hits is None:
        return {"count": None, "matches": []}
    return {"count": len(hits), "matches": hits}


def check_candidate(selector: str, dom_excerpt: str) -> Dict[str, Any]:
    """Deterministic verdict for a candidate selector."""
    r = resolve(selector, dom_excerpt)
    count = r["count"]
    if count is None:
        # Not evaluated is neither a zero-match nor a match: a person decides.
        return {"status": "review", "reason": "selector not evaluated", "count": None}
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
