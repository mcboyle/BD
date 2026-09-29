from __future__ import annotations

from copy import deepcopy
from urllib.parse import urljoin

from .template_registry import find_template_for_url


def _dedupe_keep_order(items):
    out = []
    seen = set()
    for x in items or []:
        if not x:
            continue
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def get_template_for_page(page):
    try:
        url = getattr(page, "url", "") or ""
    except Exception:
        url = ""

    if not url:
        return None

    return find_template_for_url(url)


def selector_group(template, group):
    if not template:
        return {}
    selectors = template.get("selectors") or {}
    val = selectors.get(group) or {}
    return val if isinstance(val, dict) else {}


def preferred_resolutions(template):
    vals = template.get("resolutions") if template else []
    out = []
    for v in vals or []:
        try:
            out.append(int(v))
        except Exception:
            pass
    return sorted(set(out), reverse=True)


def template_summary(template):
    if not template:
        return {
            "enabled": False,
            "host": None,
            "selectors": [],
            "resolutions": [],
            "patterns": [],
        }

    return {
        "enabled": True,
        "host": template.get("host"),
        "selectors": sorted((template.get("selectors") or {}).keys()),
        "resolutions": preferred_resolutions(template),
        "patterns": template.get("network_patterns") or [],
    }


def build_api_url(template, key, **values):
    """Build reviewed first-party API URL from template placeholders.

    This only expands reviewed template paths. It does not add cookies,
    tokens, signatures, challenge params, or bypass logic.
    """
    if not template:
        return None

    api = template.get("api") or {}
    base = api.get("base") or ""
    path = api.get(key) or ""

    if not base or not path:
        return None

    url = urljoin(base.rstrip("/") + "/", path.lstrip("/"))

    for k, v in values.items():
        url = url.replace("{" + k + "}", str(v))

    return url


def template_to_learned_download(template):
    """Convert reviewed template fields into learned-download selector shape.

    detect.find_best_download already knows learned={row_selectors, ...}.
    This adapter lets reviewed templates reuse that path instead of adding a
    separate downloader or direct API caller.
    """
    if not template:
        return {"row_selectors": [], "trigger_selectors": []}

    dl = selector_group(template, "download")
    quality = selector_group(template, "quality")

    # C3: expand any @lib:<name> named-selector references to their concrete
    # selectors before materializing. Pass-through for every value that is not a
    # live reference, so this is byte-identical for templates that use none.
    # Best effort: on any error, fall back to the unexpanded groups.
    try:
        from . import selector_library as _sl
        dl = _sl.expand_in_selectors(dl)
        quality = _sl.expand_in_selectors(quality)
    except Exception:
        pass

    row_selectors = []
    trigger_selectors = []

    # v3.66.144: the reviewed-template download shape can carry a modal
    # `trigger` (e.g. "Download Full Movie", which opens a dialog) plus
    # modal-scoped `row_selectors` for the real resolution/download links.
    # The trigger is a click target tried first; the modal rows are scrape
    # targets. `button` stays supported as a single legacy row selector.
    if dl.get("trigger"):
        trigger_selectors.append(dl["trigger"])

    dl_rows = dl.get("row_selectors")
    if isinstance(dl_rows, (list, tuple)):
        row_selectors.extend(str(r) for r in dl_rows if r)
    elif isinstance(dl_rows, str) and dl_rows:
        row_selectors.append(dl_rows)

    # Direct/candidate download selector (legacy single) goes into row_selectors.
    if dl.get("button"):
        row_selectors.append(dl["button"])

    # Quality menu opener can be tried before scraping.
    if quality.get("open_menu"):
        trigger_selectors.append(quality["open_menu"])

    # Expand {resolution} quality-option template into concrete selectors.
    # This helps pages where selecting 2160/1080 first exposes better URLs.
    opt = quality.get("resolution_option")
    if opt and "{resolution}" in opt:
        for r in preferred_resolutions(template):
            trigger_selectors.append(opt.replace("{resolution}", str(r)))

    return {
        "row_selectors": _dedupe_keep_order(row_selectors),
        "trigger_selectors": _dedupe_keep_order(trigger_selectors),
    }


def applied_template_download(config, lookup=None):
    """The learned download block of the template /templates/apply recorded for
    this site (``config["applied_template"]``), or {} when none is recorded or
    the id no longer resolves. ``lookup`` defaults to ``templates.get``."""
    tid = ((config or {}).get("applied_template") or "").strip()
    if not tid:
        return {}
    if lookup is None:
        from .templates import get as lookup
    try:
        tpl = lookup(tid) or {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return dict((tpl.get("learned") or {}).get("download") or {})


def merge_template_download_hints(page, learned_dl, override_template=None,
                                  applied=None):
    """Merge enabled reviewed template selectors into learned_dl.

    Template selectors are prepended so reviewed hints get tried first.
    Existing learned selectors remain as fallback.
    Returns: (merged_learned_dl, template_or_none)

    ``applied`` (tpl95-reptyle-1): the download block of the template the
    operator applied to this site (``applied_template_download``). Its rows and
    triggers that are still in learned_dl lead the reviewed hints -- applying a
    template is an explicit choice, and without this a reviewed host (reptyle)
    always outranked it. Rows decay dropped from learned_dl are not revived.
    Ignored on the draft-test override branch.

    ``override_template`` (B2, v3.66.240): when provided (a draft-test override
    set per-site via ``POST /api/template/test_extract``), it is used DIRECTLY
    and the enabled-only matcher (``get_template_for_page`` ->
    ``find_template_for_url``) is bypassed entirely. This is the ONLY path by
    which an unreviewed draft drives extraction; it is a separate branch, never
    a relaxation of the enabled-only gate. For every normal run
    (``override_template is None``) the matcher branch below is byte-identical
    to its pre-B2 behaviour. The override dict is still fed through the same
    ``template_to_learned_download`` adapter, so no new selector-resolution
    path is introduced.
    """
    if override_template is not None:
        template = override_template
    else:
        template = get_template_for_page(page)
    if not template:
        return learned_dl or {}, None

    merged = deepcopy(learned_dl or {})
    hints = template_to_learned_download(template)
    pinned = (applied or {}) if override_template is None else {}

    for role in ("row_selectors", "trigger_selectors"):
        learned = merged.get(role) or []
        chosen = pinned.get(role) or []
        lead = [s for s in learned if s in chosen]
        merged[role] = _dedupe_keep_order(
            lead + hints.get(role, []) + learned)

    merged["_template_host"] = template.get("host")
    merged["_template_file"] = template.get("_template_file")

    return merged, template


def template_to_selector_probe(template, probe_id):
    """Compose a reviewed template into the shape the selector verifier measures.

    ``verify_template_source`` measures a ``learned`` block; a reviewed template
    carries a ``selectors`` block. This is the ONE adapter between the two, and
    row 673 is what happens when it is not: the composition existed only inside
    ``tests/``, so every new caller copied it, and three copies drifted apart
    where one shipped function would have been measured once.

    Fail closed on the reviewed groups. ``login["email"]`` and
    ``player["container"]`` are direct lookups on purpose: a reviewed template
    missing one of those is a defect IN THE TEMPLATE, and substituting an empty
    selector for it would hand the verifier something that resolves nothing
    while the report still counts the group as measured -- a false denominator
    (CLAUDE.md A7) wearing a green verdict.
    """
    login = selector_group(template, "login")
    player = selector_group(template, "player")
    return {
        "id": probe_id,
        "learned": {
            "download": template_to_learned_download(template),
            "login": {
                "user_field": login["email"],
                "pass_field": login["password"],
                "submit_btn": login["submit"],
            },
            "player": {
                "player_selectors": [player["container"], player["play_button"]],
            },
        },
    }
