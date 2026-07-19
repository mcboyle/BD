"""Phase 9.15 -- naming-template suggestion (bounded helper).

Given sample filenames + known metadata fields + existing rules, propose a naming
template for OPERATOR REVIEW. No rename/move and no auto-apply -- this module only
proposes (there is no filesystem mutation anywhere here). Invalid template fields
are rejected.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

ALLOWED_FIELDS = ("title", "season", "episode", "resolution", "codec", "year",
                  "source", "group", "ext")

_SXXEXX = re.compile(r"\bS(\d{1,2})E(\d{1,3})\b", re.I)
_NXNN = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
_RES = re.compile(r"\b(2160p|1080p|720p|480p|4k)\b", re.I)
_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_CODEC = re.compile(r"\b(x264|x265|h\.?264|h\.?265|hevc|av1)\b", re.I)


def _detect_fields(name: str) -> Dict[str, Any]:
    found: Dict[str, Any] = {}
    if _SXXEXX.search(name) or _NXNN.search(name):
        found["season"] = True
        found["episode"] = True
    if _RES.search(name):
        found["resolution"] = True
    if _YEAR.search(name):
        found["year"] = True
    if _CODEC.search(name):
        found["codec"] = True
    found["title"] = True   # always have a title stem
    return found


def validate_template(template: str, allowed_fields=ALLOWED_FIELDS) -> Tuple[bool, List[str]]:
    """Return (ok, unknown_fields). A template references fields as {field}."""
    fields = re.findall(r"\{([a-zA-Z_]+)\}", template or "")
    unknown = [f for f in fields if f not in allowed_fields]
    return (not unknown), unknown


def _example(template: str) -> str:
    return (template
            .replace("{title}", "Show")
            .replace("{season}", "01").replace("{episode}", "02")
            .replace("{resolution}", "1080p").replace("{codec}", "x265")
            .replace("{year}", "2024").replace("{ext}", "mkv"))


def suggest(sample_filenames: List[str], known_fields: List[str],
            existing_rules: Optional[Dict[str, Any]] = None,
            *, _call=None) -> Dict[str, Any]:
    """Propose a naming template from samples. Returns proposed_template,
    recognized_fields, unknown_fields, examples, confidence, requires_review."""
    detected: Dict[str, bool] = {}
    for n in sample_filenames or []:
        for k, v in _detect_fields(n).items():
            detected[k] = detected.get(k, False) or v

    recognized = [f for f in known_fields if detected.get(f)]
    unknown = [f for f in known_fields if not detected.get(f)]

    parts = ["{title}"]
    if detected.get("season") and detected.get("episode"):
        parts.append("S{season}E{episode}")
    if detected.get("year") and not (detected.get("season")):
        parts.append("{year}")
    if detected.get("resolution"):
        parts.append("{resolution}")
    if detected.get("codec"):
        parts.append("{codec}")
    template = ".".join(parts)

    ok, unknown_template_fields = validate_template(template)
    examples = [{"before": (sample_filenames or ["<sample>"])[0],
                 "after": _example(template)}]
    confidence = round(min(1.0, 0.4 + 0.15 * len(recognized)), 2)

    return {
        "proposed_template": template,
        "recognized_fields": recognized,
        "unknown_fields": unknown,
        "examples": examples,
        "confidence": confidence,
        "requires_review": True,
        "template_valid": ok,
        "advisory": True,
    }
