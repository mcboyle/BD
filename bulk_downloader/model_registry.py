"""Phase 9.1 -- Model capability registry.

Cut 7.1 gave the operator a dropdown of model *names*. 9.1 turns each name into an
operational entry with provider/local/reachable/capability/lane metadata, and adds
capability gating (text-only models may not be used for image/vision tasks) plus
lane defaults.

Design principles:
  * read-only and side-effect-free; it classifies and recommends, it never writes
    config and never blocks a Settings save;
  * fails open -- a probe failure yields an empty registry with safe defaults and
    NEVER raises, so the UI keeps its built-in suggestions and free-text entry;
  * classification is a name heuristic (advisory). A model we can't positively
    identify as vision is treated as text-only for *picker* purposes; that is a
    conservative picker default, not a runtime authority.
"""

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# Task lanes (the operational routing surface for LLM work).
TASK_LANES: Tuple[str, ...] = (
    "cheap classify/parse",
    "selector repair",
    "login reasoning",
    "template summary",
    "failure triage",
    "redaction second-pass",
    "report summarization",
    "filename metadata",
    "vision/screenshot review",
    "UI/accessibility review",
    "OPV evidence summarization",
)

# Lanes that require a vision-capable model.
VISION_LANES = frozenset({"vision/screenshot review", "UI/accessibility review"})

# Providers whose endpoint is local/LAN (vs cloud).
_LOCAL_PROVIDERS = frozenset({"ollama"})

# Positive vision markers in a model name.
_VISION_SUBSTR = ("vision", "llava", "moondream", "bakllava", "cogvlm",
                   "minicpm-v", "llama3.2-vision", "pixtral", "internvl")
# qwen-vl family / generic "...vl" right before a tag/size boundary.
_VISION_RE = re.compile(r"(qwen[\d.]*-?vl|[\d.]vl([:\-]|$)|vl[:\-]\w)", re.I)

# Families with a usable structured-output reputation (advisory hint only).
_JSON_MEDIUM = ("qwen2.5", "qwen2", "llama3", "llama-3", "mistral", "mixtral",
                "gemma2", "phi3", "phi-3", "command-r")


def classify_capabilities(name: Optional[str]) -> Tuple[bool, bool]:
    """Return (text_capable, vision_capable) for a model name. Vision models are
    also text-capable. Unknown/empty names default to text-only."""
    if not name:
        return True, False
    low = str(name).lower()
    vision = any(k in low for k in _VISION_SUBSTR) or bool(_VISION_RE.search(low))
    return True, bool(vision)


def is_local_provider(provider: Optional[str]) -> bool:
    return str(provider or "").strip().lower() in _LOCAL_PROVIDERS


def display_for(name: str) -> str:
    """Human-ish display label. For `hf.co/org/repo:quant` keep the repo+quant."""
    if not name:
        return ""
    if name.lower().startswith("hf.co/"):
        tail = name.split("/", 2)[-1]
        return tail or name
    return name


def json_reliability_hint(name: Optional[str]) -> str:
    """Advisory structured-output reliability hint: unknown|low|medium|high."""
    if not name:
        return "unknown"
    low = str(name).lower()
    if re.search(r"(:0\.5b|:0_5b|:1b|:1\.5b|tiny|small)", low):
        return "low"
    if any(fam in low for fam in _JSON_MEDIUM):
        return "medium"
    return "unknown"


def recommended_lane_for(text_capable: bool, vision_capable: bool) -> str:
    if vision_capable:
        return "vision/screenshot review"
    return "cheap classify/parse"


@dataclass
class ModelEntry:
    provider: str
    name: str
    display_name: str
    local: bool
    reachable: bool
    last_checked: float
    last_error: str
    text_capable: bool
    vision_capable: bool
    json_reliability: str
    recommended_lane: str
    approx_size: str = ""        # "" when unknown
    approx_context: int = 0      # 0 when unknown


def make_entry(name: str, provider: str, *, reachable: bool = True,
               last_error: str = "", local: Optional[bool] = None) -> ModelEntry:
    """Build a single registry entry. Pure -- no I/O."""
    text_cap, vision_cap = classify_capabilities(name)
    return ModelEntry(
        provider=str(provider or ""),
        name=str(name or ""),
        display_name=display_for(name or ""),
        local=is_local_provider(provider) if local is None else bool(local),
        reachable=bool(reachable),
        last_checked=time.time(),
        last_error=str(last_error or ""),
        text_capable=text_cap,
        vision_capable=vision_cap,
        json_reliability=json_reliability_hint(name),
        recommended_lane=recommended_lane_for(text_cap, vision_cap),
    )


def can_use(name: Optional[str], capability: str) -> Tuple[bool, str]:
    """May `name` be used for a task requiring `capability` ("text" | "vision")?
    Text tasks are always allowed. Vision tasks require a vision-capable model;
    a text-only (or unrecognized) model is rejected for vision -- the conservative
    picker default."""
    cap = (capability or "text").strip().lower()
    if cap != "vision":
        return True, ""
    _t, vision = classify_capabilities(name)
    if vision:
        return True, ""
    label = name or "(none)"
    return False, f"{label} is text-only; it cannot be used for image/vision tasks"


def _default_lister(provider: Optional[str]) -> Callable[[], Dict[str, Any]]:
    def _list():
        from . import aiassist
        return aiassist.list_available_models(provider=provider)
    return _list


def lane_defaults(entries: List[ModelEntry]) -> Dict[str, Optional[str]]:
    """Pick a sensible default model per lane from reachable entries.
    Vision lanes need a vision-capable model; everything else takes the first
    reachable text-capable model. Returns name|None per lane."""
    reachable = [e for e in entries if e.reachable]
    first_vision = next((e.name for e in reachable if e.vision_capable), None)
    first_text = next((e.name for e in reachable if e.text_capable), None)
    out: Dict[str, Optional[str]] = {}
    for lane in TASK_LANES:
        out[lane] = first_vision if lane in VISION_LANES else first_text
    return out


def build_registry(provider: Optional[str] = None, *,
                   _lister: Optional[Callable[[], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Build the registry for a provider. Uses `aiassist.list_available_models`
    by default (injectable for tests). FAILS OPEN: any error -> ok=False with an
    empty entry list and safe (None) lane defaults; never raises."""
    prov = str(provider or "ollama").strip().lower()
    lister = _lister or _default_lister(provider)
    try:
        res = lister() or {}
    except Exception as e:  # fail open
        return {"ok": False, "provider": prov, "entries": [],
                "lanes": list(TASK_LANES),
                "defaults": {lane: None for lane in TASK_LANES},
                "error": f"{type(e).__name__}: {e}"}

    models = [m for m in (res.get("models") or []) if m]
    ok = bool(res.get("ok")) and bool(models)
    entries = [make_entry(m, prov, reachable=True) for m in models]
    return {
        "ok": ok,
        "provider": prov,
        "entries": [asdict(e) for e in entries],
        "lanes": list(TASK_LANES),
        "defaults": lane_defaults(entries),
        "error": "" if ok else (res.get("error") or "no models available"),
    }
