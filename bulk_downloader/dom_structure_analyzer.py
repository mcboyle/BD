"""bulk_downloader.dom_structure_analyzer -- Row 838: offline DOM tree analyzer.

Offline DOM structure analysis passing redacted captured DOM trees to
10.0.70.228:11434 (local inference assistant on Tesla T4 #3) to produce candidate
CSS selector strings into existing draft template lanes for review.
0 site logins touched (Rule 21).

ACCEPTANCE CONTRACT:
(1) candidate selector matches simulated DOM test tree
(2) fallback to rule-based parser if :11434 is offline
(3) zero external egress beyond LAN (strictly confined to LAN/localhost)

TRANSPORT:
Inference requests route through ``OllamaProvider._http_post`` -- the accounted
transport seam in :mod:`bulk_downloader.ai_provider`. This module opens no new
egress site of its own and satisfies the row 805 SSRF census.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup, Comment

from .ai_provider import OllamaProvider

DEFAULT_INFERENCE_ENDPOINT = "http://10.0.70.228:11434"
DEFAULT_TIMEOUT = 5.0
DEFAULT_MODEL = "qwen2.5:7b"

DRAFT_SUFFIX = ".template-draft.json"
DRAFT_SCHEMA = "bulk_downloader.template.draft.v1"
SAFETY_NOTES = (
    "Generated from offline DOM structure analysis.",
    "Review before enabling.",
    "Do not store signed one-time URLs, cookies, tokens, or challenge artifacts.",
    "This template stores selectors and reusable URL patterns only.",
)

_SENSITIVE_ATTR_PATTERNS = re.compile(
    r"(token|csrf|secret|auth|cookie|session|key|password|jwt|credential)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = re.compile(
    r"(Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*|[A-Za-z0-9_-]{32,}|[\w\.-]+@[\w\.-]+\.\w+)",
    re.IGNORECASE,
)


def is_lan_endpoint(endpoint: str) -> bool:
    """True if endpoint resolves strictly to loopback, private, link-local, or reserved LAN IP.

    Refuses external hosts/IPs to enforce zero external egress beyond LAN.
    """
    try:
        parsed = urllib.parse.urlparse(endpoint)
    except Exception:
        return False
    host = parsed.hostname or ""
    if not host:
        return False

    if host.lower() in ("localhost", "ip6-localhost"):
        return True

    # Check if host is an IP literal
    try:
        ip = ipaddress.ip_address(host)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)
    except ValueError:
        pass

    # Hostname: resolve TCP addresses and ensure all are LAN
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addrs = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        if not addrs:
            return False
        for a in addrs:
            ip = ipaddress.ip_address(a[4][0])
            if not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
                return False
        return True
    except Exception:
        return False


def redact_dom_tree(html: str) -> str:
    """Redact sensitive attributes, secrets, and extraneous nodes from a DOM tree.

    Strips script, style, comments, authentication values, CSRF tokens, session IDs,
    passwords, and credentials while preserving structural tags, IDs, classes, and layout.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-structural elements
    for element in soup(["script", "style", "noscript", "meta", "link"]):
        element.decompose()

    # Remove comments
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()

    # Redact attributes across all elements
    for tag in soup.find_all(True):
        # Always redact input values
        if tag.name == "input" and "value" in tag.attrs:
            tag.attrs["value"] = "[REDACTED]"

        attrs_to_remove = []
        attrs_to_redact = []
        for attr, val in list(tag.attrs.items()):
            if _SENSITIVE_ATTR_PATTERNS.search(attr):
                attrs_to_redact.append(attr)
            elif isinstance(val, str) and _SECRET_VALUE_PATTERNS.search(val):
                attrs_to_redact.append(attr)

        for attr in attrs_to_redact:
            tag.attrs[attr] = "[REDACTED]"

    # Clean text nodes
    for text_node in soup.find_all(string=True):
        if text_node.parent and text_node.parent.name not in ("script", "style"):
            original = str(text_node)
            cleaned = _SECRET_VALUE_PATTERNS.sub("[REDACTED]", original)
            if cleaned != original:
                text_node.replace_with(cleaned)

    return str(soup)


def suggest_selectors_rule_based(soup: BeautifulSoup, target_desc: str = "video") -> List[str]:
    """Derive candidate CSS selectors matching elements in the DOM tree using deterministic rules."""
    candidates: List[str] = []
    target_lower = target_desc.lower()

    # 1. Look for specific tags matching the target description
    tags_to_check: List[str] = []
    if "video" in target_lower or "player" in target_lower or "media" in target_lower:
        tags_to_check.extend(["video", "iframe", "audio"])
    if "download" in target_lower:
        tags_to_check.extend(["a", "button"])
    if "article" in target_lower or "content" in target_lower:
        tags_to_check.extend(["article", "main", "section"])

    if not tags_to_check:
        tags_to_check = ["video", "a", "article", "div"]

    for tag_name in tags_to_check:
        for elem in soup.find_all(tag_name):
            classes = elem.get("class", [])
            class_str = "." + ".".join(classes) if classes else ""
            elem_id = elem.get("id")

            # ID selector
            if elem_id:
                candidates.append(f"{tag_name}#{elem_id}")

            # Specific class selector
            if class_str:
                candidates.append(f"{tag_name}{class_str}")
                candidates.append(class_str)

            # Hierarchical selector with parent
            parent = elem.parent
            if parent and parent.name not in ("[document]", "html", "body"):
                parent_id = parent.get("id")
                parent_classes = parent.get("class", [])
                parent_class_str = "." + ".".join(parent_classes) if parent_classes else ""

                if parent_id and class_str:
                    candidates.append(f"#{parent_id} {tag_name}{class_str}")
                elif parent_class_str and class_str:
                    candidates.append(f"{parent_class_str} {tag_name}{class_str}")
                elif parent_class_str:
                    candidates.append(f"{parent_class_str} {tag_name}")

            # Tag selector as fallback
            candidates.append(tag_name)

    # 2. Look for class/id names containing target words
    for attr in ("class", "id"):
        for elem in soup.find_all(attrs={attr: True}):
            val = elem.get(attr)
            val_str = " ".join(val) if isinstance(val, list) else str(val)
            if any(k in val_str.lower() for k in ("video", "player", "media", "download")):
                tag = elem.name
                if attr == "id":
                    candidates.append(f"{tag}#{val_str}")
                    candidates.append(f"#{val_str}")
                elif isinstance(val, list) and val:
                    for cls in val:
                        candidates.append(f"{tag}.{cls}")
                        candidates.append(f".{cls}")

    # Validate against DOM and filter out invalid/empty matches
    valid_selectors: List[str] = []
    seen = set()
    for sel in candidates:
        sel = sel.strip()
        if not sel or sel in seen:
            continue
        try:
            matches = soup.select(sel)
            if len(matches) > 0:
                seen.add(sel)
                valid_selectors.append(sel)
        except Exception:
            continue

    return valid_selectors


def suggest_selectors_inference(
    redacted_html: str,
    target_desc: str,
    endpoint: str = DEFAULT_INFERENCE_ENDPOINT,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
) -> Optional[List[str]]:
    """Query the local inference assistant at :11434 for candidate CSS selectors.

    Returns list of candidate selector strings, or None on failure/offline.
    """
    prompt = (
        f"You are an expert web scraping and DOM structure assistant.\n"
        f"Analyze the following redacted HTML DOM tree structure and suggest 1 to 5 candidate CSS selector strings targeting: {target_desc}.\n"
        f"Return ONLY a JSON object with a 'selectors' list of CSS selector strings, like:\n"
        f'{{"selectors": ["div.player > video", "video.main"]}}\n\n'
        f"HTML:\n{redacted_html[:4000]}\n"
    )

    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "num_predict": 256,
        },
    }

    try:
        provider = OllamaProvider(endpoint=endpoint)
        url = endpoint.rstrip("/") + "/api/generate"
        ok, status, resp, _ms = provider._http_post(url, body, {}, timeout=timeout)
        if not ok or not isinstance(resp, dict):
            return None

        text = str(resp.get("response") or "")
        # Extract JSON substring if wrapped in markdown code blocks
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group(0))
            sels = parsed.get("selectors")
            if isinstance(sels, list):
                return [str(s) for s in sels if isinstance(s, str) and s.strip()]
    except Exception:
        return None

    return None


def save_candidate_to_draft_lane(
    host: str,
    selectors: Dict[str, Any] | List[str],
    drafts_dir: str | Path,
    role: str = "video",
    name: str = "player",
) -> Dict[str, Any]:
    """Save suggested selectors into a review-only template draft lane."""
    dd = Path(drafts_dir)
    dd.mkdir(parents=True, exist_ok=True)
    safe_host = re.sub(r"[^A-Za-z0-9_.-]", "_", host)
    fp = dd / f"{safe_host}{DRAFT_SUFFIX}"

    draft: Dict[str, Any] = {}
    if fp.is_file():
        try:
            draft = json.loads(fp.read_text("utf-8"))
        except Exception:
            draft = {}

    if not isinstance(draft, dict) or not draft:
        draft = {
            "schema": DRAFT_SCHEMA,
            "status": "draft_review_required",
            "safety_notes": list(SAFETY_NOTES),
            "host": str(host),
            "confidence": "review",
            "origin": "dom_structure_analyzer",
            "selectors": {},
        }

    draft["status"] = "draft_review_required"
    draft["review_required"] = True
    sels = draft.setdefault("selectors", {})

    if isinstance(selectors, list):
        role_map = sels.setdefault(role, {})
        for idx, sel in enumerate(selectors):
            key = name if idx == 0 else f"{name}_{idx + 1}"
            role_map[key] = sel
    elif isinstance(selectors, dict):
        for k, v in selectors.items():
            sels.setdefault(role, {})[k] = str(v)

    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(fp)

    return {
        "ok": True,
        "file": fp.name,
        "path": str(fp),
        "status": draft["status"],
        "review_required": draft["review_required"],
        "selectors": draft["selectors"],
    }


def analyze_dom_structure(
    html: str,
    target_desc: str = "video",
    endpoint: str = DEFAULT_INFERENCE_ENDPOINT,
    timeout: float = DEFAULT_TIMEOUT,
    save_draft_host: Optional[str] = None,
    drafts_dir: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Perform offline DOM structure analysis to suggest candidate CSS selectors.

    Guarantees:
    - Enforces zero external egress beyond LAN (refuses non-LAN endpoints).
    - Redacts captured DOM tree before inference.
    - Queries local inference assistant at :11434 with fallback to rule-based parser.
    - Ensures candidate selectors match elements in the DOM tree.
    - Optionally records candidate selectors into draft template lane for review.
    """
    # Acceptance (3): Zero external egress beyond LAN
    if not is_lan_endpoint(endpoint):
        raise ValueError(
            f"External egress forbidden: endpoint {endpoint!r} does not resolve strictly to a LAN/loopback address"
        )

    # Redact captured DOM tree
    redacted = redact_dom_tree(html)
    soup = BeautifulSoup(html, "html.parser")

    selectors: List[str] = []
    source = "inference"

    # Attempt inference via local Ollama assistant
    inference_selectors = suggest_selectors_inference(
        redacted_html=redacted,
        target_desc=target_desc,
        endpoint=endpoint,
        timeout=timeout,
    )

    if inference_selectors:
        # Validate that inference selectors match the DOM
        for sel in inference_selectors:
            try:
                if len(soup.select(sel)) > 0:
                    selectors.append(sel)
            except Exception:
                continue

    # Acceptance (2): Fallback to rule-based parser if :11434 is offline or returns 0 matches
    if not selectors:
        source = "rule_based_fallback"
        selectors = suggest_selectors_rule_based(soup, target_desc=target_desc)

    # Calculate matches for each selector
    matches = {}
    for sel in selectors:
        try:
            matches[sel] = len(soup.select(sel))
        except Exception:
            matches[sel] = 0

    draft_info = None
    if save_draft_host and drafts_dir:
        draft_info = save_candidate_to_draft_lane(
            host=save_draft_host,
            selectors=selectors,
            drafts_dir=drafts_dir,
            role="media",
            name="candidate",
        )

    return {
        "ok": True,
        "source": source,
        "selectors": selectors,
        "matches": matches,
        "redacted_dom": redacted,
        "draft_lane": draft_info,
    }
