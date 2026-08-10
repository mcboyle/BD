"""template_extractor_impl.candidates -- verbatim cluster from template_extractor.py."""

from __future__ import annotations

import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from .. import candidate_filter as cf
from ._css import _css_escape, _css_escape_attr
from .login_extract import _login_is_honeypot
from ._constants import (
    _CANDIDATE_ATTRS,
    _CANDIDATE_TAGS,
    MIN_SCORE_FOR_CANDIDATE,
    MIN_SCORE_FOR_ROW,
    MIN_SCORE_FOR_TEMPLATE,
)


def extract_from_html(html: str,
                       page_url: str = "",
                       site_hint_name: str = "") -> Dict[str, Any]:
    """Main entry point. Parse the pasted HTML, score every plausible
    candidate, and produce a draft template.

    Returns: {
      ok: bool,
      template: { ... draft template ... },
      candidates: [ ... ranked ... ],
      warnings: [ ... ],
      stats: { n_candidates, n_scored, n_kept },
    }

    Never raises — bad input returns {ok: False, error: ...}.
    """
    if not isinstance(html, str) or not html.strip():
        return {"ok": False, "error": "no HTML provided",
                "template": None, "candidates": [], "warnings": []}
    # Import BS4 lazily — if it fails we degrade rather than break
    # the whole module load chain.
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False,
                "error": "BeautifulSoup (bs4) not installed; "
                          "cannot parse HTML server-side",
                "template": None, "candidates": [], "warnings": []}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return {"ok": False,
                "error": f"HTML parse failed: {type(e).__name__}: {e}",
                "template": None, "candidates": [], "warnings": []}
    # 1. Walk the tree and collect candidate elements
    raw_candidates = _walk_for_candidates(soup)
    # 2. Score each through the heuristic_scoring module
    scored = _score_all(raw_candidates)
    # 3. Generalize each high-scorer into selector variants
    enriched = []
    for c in scored:
        c["selector_variants"] = _generalize_selectors(c)
        enriched.append(c)
    # 4. Pick the best row + trigger selectors for the draft template
    template = _build_template(enriched, page_url=page_url,
                                  site_hint_name=site_hint_name)
    warnings = _validate_template(template, enriched, html)
    # Surface the obvious non-download candidates the safeguard filtered out
    # (homepage / nav / generic / share / external), so the UI can show *why*
    # rather than silently dropping them.
    _host = (urlparse(page_url).hostname or "") if isinstance(page_url, str) and page_url else ""
    rejected = []
    for c in enriched:
        top_sel = (c.get("selector_variants") or [{}])[0].get("selector")
        v = cf.classify_candidate(c, page_host=_host or None, selector=top_sel)
        if not v.accepted:
            rejected.append({"selector": top_sel, "url": cf.best_url(c),
                             "text": (c.get("text") or "")[:60],
                             "reason": v.reason})
    return {
        "ok": True,
        "template": template,
        "candidates": enriched[:12],   # cap for UI render
        "rejected_candidates": rejected[:12],
        "warnings": warnings,
        "stats": {
            "n_candidates": len(raw_candidates),
            "n_scored": len(scored),
            "n_kept": len(enriched),
            "n_rejected": len(rejected),
        },
    }


def _walk_for_candidates(soup) -> List[Dict[str, Any]]:
    """Walk every element that could be a download trigger and
    pull the fields the scorer needs."""
    out = []
    seen_keys = set()
    # Search by tag first
    elements = []
    for tag in _CANDIDATE_TAGS:
        elements.extend(soup.find_all(tag))
    # Plus anything with our data-* attrs (covers tags we don't list)
    for attr in _CANDIDATE_ATTRS:
        for el in soup.find_all(attrs={attr: True}):
            if el not in elements:
                elements.append(el)
    for el in elements:
        try:
            text = (el.get_text() or "").strip()[:200]
            href = el.get("href") or ""
            data_href = el.get("data-href") or ""
            data_url = el.get("data-url") or ""
            data_src = el.get("data-src") or ""
            data_download = el.get("data-download") or ""
            tag = el.name.lower() if el.name else "?"
            # Skip nothing-elements
            if not (text or href or data_href or data_url or data_src):
                continue
            # Build ancestor text for context scoring
            ancestor_text = _collect_ancestor_signals(el, depth=4)
            # Dedup
            key = (tag, text[:50], (href or data_href)[:60])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append({
                "tag": tag,
                "text": text,
                "href": href,
                "data_href": data_href,
                "data_url": data_url,
                "data_src": data_src,
                "data_download": data_download,
                "id": el.get("id") or "",
                "className": _serialize_classlist(el.get("class")),
                "data_res": el.get("data-res")
                              or el.get("data-resolution")
                              or el.get("data-quality") or "",
                "ancestor_text": ancestor_text,
                "_attrs": {k: v for k, v in el.attrs.items()
                              if k.startswith("data-")},
                "_el": el,  # keep for selector generalization
            })
        except Exception:
            continue
    return out


def _serialize_classlist(cl) -> str:
    """BS4 returns class as a list; learn.py expected a string."""
    if isinstance(cl, str):
        return cl
    if isinstance(cl, list):
        return " ".join(c for c in cl if isinstance(c, str))
    return ""


def _collect_ancestor_signals(el, depth: int = 4) -> str:
    """Walk up to `depth` parents, concatenating their class+id
    strings so the ancestor-context heuristic has material."""
    parts = []
    cur = el.parent
    for _ in range(depth):
        if cur is None:
            break
        cl = _serialize_classlist(cur.get("class")) if hasattr(cur, "get") else ""
        elid = cur.get("id", "") if hasattr(cur, "get") else ""
        if cl: parts.append(cl)
        if elid: parts.append(elid)
        cur = getattr(cur, "parent", None)
    return " ".join(parts).strip()


def _score_all(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run each candidate through the v3.43.44 scorer. Keeps a
    broader set than just download buttons — menu/trigger elements
    typically score lower (no resolution mention, no filesize) but
    we still need them for `trigger_selectors`. Two-tier filter
    happens in `_build_template`.

    Matches the input shape of `heuristic_scoring.score_candidate`:
    individual data_* fields (not pre-concatenated), and
    `ancestor_classes` (not `ancestor_text`).
    """
    from .. import heuristic_scoring as hs
    out = []
    for c in candidates:
        try:
            result = hs.score_candidate(
                {
                    "tag": c.get("tag", ""),
                    "text": c.get("text", ""),
                    "href": c.get("href", ""),
                    "data_href": c.get("data_href", ""),
                    "data_url": c.get("data_url", ""),
                    "data_src": c.get("data_src", ""),
                    "data_download": c.get("data_download", ""),
                    "ancestor_classes": c.get("ancestor_text", ""),
                    "surrounding_text": c.get("text", ""),
                    "rect": None,
                    "viewport_height": 0,
                },
                fingerprint=None,
            )
            c["score"] = result.get("score", 0)
            c["score_reasons"] = result.get("reasons", [])
            c["resolution_tier"] = result.get("resolution_tier", 0)
            c["estimated_size_bytes"] = result.get(
                "estimated_size_bytes", 0)
        except Exception as e:
            c["score"] = 0
            c["score_reasons"] = [(0, f"scoring failed: {e}")]
            c["resolution_tier"] = 0
            c["estimated_size_bytes"] = 0
        # MIN_SCORE_FOR_CANDIDATE is the floor for inclusion at all
        # — even menu triggers must clear something. Row selection
        # uses the higher MIN_SCORE_FOR_ROW.
        if c.get("score", 0) >= MIN_SCORE_FOR_CANDIDATE:
            out.append(c)
    out.sort(key=lambda c: (
        c.get("score", 0),
        c.get("resolution_tier", 0),
        c.get("estimated_size_bytes", 0),
    ), reverse=True)
    return out


def _generalize_selectors(candidate: Dict[str, Any]) -> List[Dict[str, str]]:
    """Given a scored candidate, propose 3-5 CSS selectors at
    different specificity levels. Returns list of:
      [{selector, kind, specificity, note}, ...]
    `kind` is one of: id | attribute | class_multi | class_primary |
                       extension | text | tag.

    Order is most-specific first, so the UI's default pick is the
    safest selector.
    """
    el = candidate.get("_el")
    tag = candidate.get("tag", "a")
    variants = []
    if el is None:
        return variants

    # 1. ID-based (most specific). Skip generated IDs that look like
    #    framework-generated UUIDs (very long or all-digits).
    el_id = candidate.get("id", "").strip()
    if el_id and _looks_stable_id(el_id):
        variants.append({
            "selector": f"#{_css_escape(el_id)}",
            "kind": "id",
            "specificity": 100,
            "note": "exact ID — most brittle; breaks if the site changes the ID",
        })

    # 2. Custom data attribute
    for attr, val in (candidate.get("_attrs") or {}).items():
        if not val:
            continue
        # data-href / data-url / data-src are useful identifiers
        if attr in ("data-href", "data-url", "data-src"):
            variants.append({
                "selector": f"{tag}[{attr}]",
                "kind": "attribute",
                "specificity": 75,
                "note": f"matches any {tag} with {attr} attribute set",
            })
            break  # one is enough
    # data-res attribute specifically
    data_res = candidate.get("data_res", "")
    if data_res:
        variants.append({
            "selector": f"{tag}[data-res='{_css_escape_attr(data_res)}']",
            "kind": "attribute",
            "specificity": 80,
            "note": f"specific to {data_res}p quality",
        })

    # 3. Multi-class selector
    classes = [c for c in candidate.get("className", "").split()
                  if c and not _looks_utility_class(c)]
    if len(classes) >= 2:
        variants.append({
            "selector": f"{tag}." + ".".join(_css_escape(c) for c in classes[:3]),
            "kind": "class_multi",
            "specificity": 65,
            "note": "all classes — brittle if any class changes",
        })

    # 4. Primary class only
    primary = _pick_primary_class(classes)
    if primary:
        variants.append({
            "selector": f"{tag}.{_css_escape(primary)}",
            "kind": "class_primary",
            "specificity": 50,
            "note": "primary class — tolerates secondary class changes",
        })

    # 5. Extension-based
    # v3.65.2: the matching URL might live in href OR data-href OR
    # data-url. Previously this branch scanned all three concatenated
    # but then ALWAYS emitted [href$=...], which matched zero elements
    # on sites whose download links carry the URL in data-href (a
    # common pattern that this same module recognizes elsewhere).
    # Now: find which attribute supplied the match, then point the
    # selector at THAT attribute.
    _ext_re = re.compile(r"\.(mp4|mkv|webm|mov|m3u8|mpd)(?:\?|#|$)", re.I)
    url_src_attr = None
    ext = None
    for cand_attr, cand_val in (
        ("href",      candidate.get("href", "")),
        ("data-href", candidate.get("data_href", "")),
        ("data-url",  candidate.get("data_url", "")),
    ):
        m = _ext_re.search(cand_val or "")
        if m:
            url_src_attr = cand_attr
            ext = m.group(1).lower()
            break
    if url_src_attr and ext:
        variants.append({
            "selector": f"{tag}[{url_src_attr}$='.{ext}']",
            "kind": "extension",
            "specificity": 40,
            "note": f"any {tag} pointing at a {ext} file via {url_src_attr}",
        })

    # 6. Text-anchored (Playwright-flavored, for use with locator())
    text = candidate.get("text", "").strip()
    if text and len(text) <= 30:
        # Pick the most distinctive word: prefer ones that match the
        # download/resolution keyword list
        key_word = _pick_keyword(text)
        if key_word:
            variants.append({
                "selector": f"{tag}:has-text('{_css_escape_attr(key_word)}')",
                "kind": "text",
                "specificity": 35,
                "note": "text-based; survives most layout changes",
            })

    # 7. Fallback: tag-only (last resort, only useful if combined
    #    with a parent selector by the user)
    if not variants:
        variants.append({
            "selector": tag,
            "kind": "tag",
            "specificity": 5,
            "note": "extremely generic — needs scoping by parent",
        })
    return variants


def _looks_stable_id(elid: str) -> bool:
    """ID is 'stable' if it's short, alphanumeric, doesn't look like a
    UUID/hash/framework-generated identifier, and isn't all-digits.

    Heuristics for "framework-generated":
      - All hex characters and 16+ chars long (likely a hash)
      - Contains a colon (React/Vue auto-ID convention)
      - Contains 'react-', 'mui-', 'aria-', 'radix-', 'headless-'
        prefixes used by common UI libraries
      - 4+ hyphen-separated segments (e.g. 'foo-bar-baz-qux-quux')
      - Contains a 4+ digit cluster (sequence number in generated ID)
      - Longer than 30 chars (rare for hand-authored IDs)
    """
    if not elid or len(elid) > 30:
        return False
    if elid.isdigit():
        return False
    if re.match(r"^[0-9a-f]{16,}$", elid, re.I):
        return False  # hash-y
    if ":" in elid:
        return False
    # Framework prefixes
    lower = elid.lower()
    for prefix in ("react-", "mui-", "aria-", "radix-", "headless-",
                     "ember", "ng-"):
        if lower.startswith(prefix):
            return False
    # Too many segments
    if elid.count("-") >= 3:
        return False
    # Long digit clusters (sequence numbers in generated IDs)
    if re.search(r"\d{4,}", elid):
        return False
    return True


def _looks_utility_class(c: str) -> bool:
    """Tailwind / Bootstrap utility classes are layout-only and
    aren't reliable identifiers. Skip them when picking primary."""
    if not c:
        return True
    # Tailwind: 'flex', 'pt-4', 'text-lg', 'hover:bg-red-500',
    # 'sm:w-1/2', 'col-span-3'. Many of these contain hyphens with
    # specific patterns.
    if re.match(r"^(pt|pb|pl|pr|p|mt|mb|ml|mr|m|w|h|gap|space|"
                 r"text|bg|border|rounded|shadow|hover|focus|sm|md|"
                 r"lg|xl|flex|grid|col|row|justify|items|self)[-:]",
                 c):
        return True
    # Bootstrap: 'btn-primary', 'col-md-6'. The 'btn' itself is
    # often informative — keep it. Strip variants only.
    if re.match(r"^col-(xs|sm|md|lg|xl)-\d+$", c):
        return True
    return False


def _pick_primary_class(classes: List[str]) -> str:
    """Pick the most distinctive class name. Prefer ones containing
    'download', 'btn', 'play', 'video', 'dl', 'mirror'."""
    if not classes:
        return ""
    keywords = ("download", "mirror", "source", "quality", "btn",
                  "play", "video", "dl", "get", "hd", "4k", "8k")
    # First pass: any class containing a hot keyword
    for c in classes:
        cl = c.lower()
        if any(k in cl for k in keywords):
            return c
    # Fall back to the longest class (most specific by length)
    return max(classes, key=len)


def _pick_keyword(text: str) -> str:
    """From a short button text, pick the most distinctive word
    for use in a :has-text() selector. Single word preferred."""
    words = re.findall(r"\b\w{3,}\b", text)
    if not words:
        return ""
    # Prefer download-flavored words
    download_words = ("download", "save", "mp4", "video", "full",
                        "stream", "play", "view", "watch", "get")
    for w in words:
        if w.lower() in download_words:
            return w
    # Otherwise first non-stopword
    stopwords = ("the", "and", "for", "with", "this", "that")
    for w in words:
        if w.lower() not in stopwords:
            return w
    return words[0]


def _row_is_inline_hidden(candidate: Dict[str, Any]) -> bool:
    """True when the candidate's DOM element is hidden from real users by
    the same evidence the login side has always screened with
    (login_extract._login_is_honeypot): inline style (display:none /
    offscreen left / 1px box / opacity:0 / visibility:hidden), tabindex=-1,
    aria-hidden=true, the same inline styles on up to 3 ancestors, or a
    name/id carrying a bot-trap token. Item D (register 15.76): the
    download path never called that screen, so a hidden decoy anchor could
    outrank the real download link and ship as row_selectors[0].

    This must run HERE, where the candidate still carries its DOM element
    (``_el``, kept by _walk_for_candidates) -- template_normalize sees only
    selector strings and cannot ask any of these questions.

    LIMIT, stated so nobody reads this screen as complete: it sees INLINE
    evidence only. A decoy hidden by a class or a stylesheet rule is
    invisible to it and survives. A candidate without ``_el`` (synthetic
    callers of _build_template) cannot be screened and also survives --
    the screen drops on positive evidence, never on ignorance.
    """
    el = candidate.get("_el")
    if el is None:
        return False
    try:
        return bool(_login_is_honeypot(el))
    except Exception:
        return False


def _build_template(enriched: List[Dict[str, Any]],
                      page_url: str = "",
                      site_hint_name: str = "") -> Dict[str, Any]:
    """Take the ranked candidates and assemble a draft template."""
    if not enriched:
        return {
            "name": site_hint_name or _name_from_url(page_url) or "Untitled site",
            "url_patterns": _patterns_from_url(page_url),
            "row_selectors": [],
            "trigger_selectors": [],
            "url_attribute": "href",
            "min_resolution": 1080,
            "review_required": True,
        }
    # Filter for ROW selectors: stronger evidence required than for
    # triggers. A button with no download signal doesn't deserve to
    # be `row_selectors[0]`.
    scored_rows = [c for c in enriched
                   if c.get("score", 0) >= MIN_SCORE_FOR_ROW]
    # Item D: screen the ROW pool through the repo's own honeypot evidence
    # BEFORE anything downstream reads it, so a hidden decoy cannot become
    # a row selector, drive url_attribute / min_resolution off row_pool[0],
    # or read as clean evidence to a reviewer. Rows specifically: at
    # runtime the learned-row path (detect.py, v3.66.247) skips rows
    # Playwright reports non-visible, but that only covers display:none /
    # visibility:hidden -- the offscreen, opacity:0, 1px, tabindex and
    # aria-hidden classes all read as VISIBLE there and would be clicked
    # or harvested. Trigger selection below still walks the unscreened
    # candidate list; that is a separate, narrower exposure (triggers are
    # clicked, and clicks auto-wait for actionability).
    hidden_rows_dropped: List[str] = []
    _visible_rows: List[Dict[str, Any]] = []
    for c in scored_rows:
        if _row_is_inline_hidden(c):
            _top = (c.get("selector_variants") or [{}])[0].get("selector")
            hidden_rows_dropped.append(_top or c.get("tag", "?"))
        else:
            _visible_rows.append(c)
    scored_rows = _visible_rows
    # v3.66.x safeguard: a download ROW must carry a real site-provided
    # media/download URL signal (extension / manifest / download path / API)
    # — never a generic, homepage, or nav link (the bad-`download.bin` bug).
    # Fall back to the best-scored rows only when none qualify, and flag the
    # draft `review_required` so a human confirms before it is trusted.
    _host = (urlparse(page_url).hostname or "") if isinstance(page_url, str) and page_url else ""
    _URL_SIGNALS = {"media_extension", "manifest_url", "download_path",
                    "api_pattern"}

    def _has_row_url_signal(c: Dict[str, Any]) -> bool:
        top_sel = (c.get("selector_variants") or [{}])[0].get("selector")
        v = cf.classify_candidate(c, page_host=_host or None, selector=top_sel)
        c["filter_verdict"] = v.to_dict()
        return bool(set(v.positive_signals) & _URL_SIGNALS)

    strong_rows = [c for c in scored_rows if _has_row_url_signal(c)]
    review_required = False
    if strong_rows:
        row_pool = strong_rows
    elif scored_rows:
        row_pool = scored_rows           # nothing had a clean URL signal
        review_required = True
    else:
        row_pool = []
    if not row_pool:
        return {
            "name": site_hint_name or _name_from_url(page_url) or "Untitled site",
            "url_patterns": _patterns_from_url(page_url),
            "row_selectors": [],
            "trigger_selectors": [],
            "url_attribute": "href",
            "min_resolution": 1080,
            "review_required": True,
            # The all-hidden case lands here: every scored row was decoy
            # evidence. Carried so _validate_template can say WHY the row
            # list is empty instead of only "no candidates found".
            "_hidden_rows_dropped": hidden_rows_dropped,
        }
    # Pick row selectors: top 3 row-candidates' best variant
    row_selectors = []
    for c in row_pool[:3]:
        variants = c.get("selector_variants") or []
        if variants:
            row_selectors.append(variants[0]["selector"])
    # Pick trigger selectors: candidates that look like menu openers
    # (button/anchor with whole-word "quality"/"menu"/etc.). Walk
    # the FULL candidate list (not just the scored survivors) since
    # menu openers often don't have download score themselves.
    trigger_selectors = []
    seen_trig = set(row_selectors)
    # Whole-word regex so 'quality-8k' on a download button doesn't
    # mistakenly classify it as a trigger.
    _TRIG_RE = re.compile(
        r"\b(quality|menu|options|select|more|download\s*option)s?\b",
        re.I)
    for c in enriched:
        text = c.get("text", "")
        cls = c.get("className", "")
        # Skip the download elements we already picked as rows.
        # v3.65.2: was `any(s in <selector> for s in seen_trig)` —
        # SUBSTRING containment. When a row selector fell back to
        # something short like "a" or "button" (the tag-only fallback
        # at the bottom of _generalize_selectors), every other anchor
        # or button candidate matched the substring check and got
        # skipped, leaving trigger_selectors empty on pages that had
        # obvious quality-menu buttons. Use set membership instead.
        top_sel = (c.get("selector_variants") or [{}])[0].get("selector", "")
        if top_sel in seen_trig:
            continue
        # Class check is keyword-based but excludes 'quality-<resolution>'
        # patterns (those are quality variants, not quality MENUs)
        cls_is_menu = bool(re.search(
            r"\b(quality[-_]?menu|quality[-_]?picker|menu|dropdown|selector)\b",
            cls, re.I))
        text_is_menu = bool(_TRIG_RE.search(text))
        if not (cls_is_menu or text_is_menu):
            continue
        variants = c.get("selector_variants") or []
        if variants:
            sel = variants[0]["selector"]
            if sel not in seen_trig:
                trigger_selectors.append(sel)
                seen_trig.add(sel)
        if len(trigger_selectors) >= 2:
            break
    # Determine url_attribute: if top candidates have data-href, use
    # that; else default to href
    url_attribute = "href"
    top = row_pool[0]
    if not top.get("href") and top.get("data_href"):
        url_attribute = "data-href"
    elif not top.get("href") and top.get("data_src"):
        url_attribute = "data-src"
    elif not top.get("href") and top.get("data_url"):
        url_attribute = "data-url"
    # Min resolution: if any row candidate flagged 4K or higher
    # (resolution_tier >= 60 == 4K), set 2160 so workers prefer the
    # high-res variant; if 8K+ (tier 80), go 4320.
    min_res = 1080
    for c in row_pool[:5]:
        tier = c.get("resolution_tier", 0)
        if tier >= 80:
            min_res = 4320
            break
        elif tier >= 60:
            min_res = 2160
            # don't break — a later candidate could be 8K
    return {
        "name": site_hint_name or _name_from_url(page_url) or "New site",
        "url_patterns": _patterns_from_url(page_url),
        "row_selectors": row_selectors,
        "trigger_selectors": trigger_selectors,
        "url_attribute": url_attribute,
        "min_resolution": min_res,
        "review_required": review_required,
        "_top_candidate_score": top.get("score", 0),
        "_hidden_rows_dropped": hidden_rows_dropped,
    }


def _name_from_url(url: str) -> str:
    """Heuristic site name from a URL — last 2 parts of hostname,
    title-cased. https://www.example.co.uk/foo → Example."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        h = urlparse(url).hostname or ""
        if not h:
            return ""
        parts = h.lower().split(".")
        # Drop www. prefix
        if parts and parts[0] == "www":
            parts = parts[1:]
        # Take the registrable label (second-to-last for .com/.net,
        # third-to-last for .co.uk style — but heuristic only, not
        # tld-database-driven). Good enough for a name suggestion.
        if len(parts) >= 2:
            return parts[0].title()
        return parts[0].title() if parts else ""
    except Exception:
        return ""


def _patterns_from_url(url: str) -> str:
    """Build a url_patterns regex from the page URL — escapes the
    hostname and makes the path generic. Result is one line per
    pattern, comma-OK."""
    if not isinstance(url, str) or not url:
        return ""
    try:
        h = urlparse(url).hostname or ""
        if not h:
            return ""
        # Drop www. for the regex (match both with and without)
        if h.startswith("www."):
            h = h[4:]
        # Escape dots; pattern matches both www and bare host
        return re.escape(h)
    except Exception:
        return ""


def _validate_template(template: Dict[str, Any],
                          candidates: List[Dict[str, Any]],
                          html: str) -> List[str]:
    """Produce human-readable warnings about the proposed template."""
    warnings = []
    dropped = template.get("_hidden_rows_dropped") or []
    if dropped:
        shown = ", ".join(str(s) for s in dropped[:3])
        warnings.append(
            f"Dropped {len(dropped)} hidden download candidate(s) as "
            f"probable honeypots ({shown}) — hidden from real users by "
            "inline evidence (style/tabindex/aria-hidden/ancestor style). "
            "Note the screen's limit: it reads INLINE evidence only; a "
            "decoy hidden by a class or stylesheet rule is invisible to "
            "it and is NOT screened.")
    if not template.get("row_selectors"):
        warnings.append(
            "No download candidates found — paste HTML containing actual "
            "download buttons or links.")
    elif len(template["row_selectors"]) == 1:
        warnings.append(
            "Only one selector candidate — template will be brittle. "
            "Consider pasting more of the page to capture variants.")
    top = candidates[0] if candidates else None
    if top and top.get("score", 0) < 60:
        warnings.append(
            f"Top candidate score is only {top['score']} — the rule-based "
            "scorer is unsure. Use the AI refinement option for a "
            "second opinion, or check that your pasted HTML contains "
            "the download section.")
    if html and len(html) > 500000:
        warnings.append(
            "Pasted HTML is large (>500KB). Consider pasting just the "
            "relevant section for cleaner results.")
    if not template.get("url_patterns"):
        warnings.append(
            "No page URL provided — url_patterns left empty. Edit the "
            "site after save to add a routing pattern.")
    if not template.get("trigger_selectors"):
        # Not always a problem — some sites have direct download links
        # without a trigger. Just informational.
        warnings.append(
            "No trigger selectors detected (menu/quality buttons). "
            "If the site requires a click to reveal downloads, edit the "
            "template to add one.")
    return warnings
