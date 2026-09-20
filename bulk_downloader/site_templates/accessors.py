"""site_templates.accessors -- the 3 public accessors, verbatim from templates.py.

Each merges a user_templates overlay (lazy `from .. import user_templates as _ut`,
and reads the package-level TEMPLATES list."""

import logging

from . import TEMPLATES

# The template maintenance log (register row 918: "warning emitted to
# template maintenance log"): selector degradation is routed here as a
# WARNING record, as well as to warnings.warn for interactive callers.
maintenance_log = logging.getLogger("bulk_downloader.site_templates.maintenance")


class SelectorResolutionError(Exception):
    """Every strategy in a resolve_selector_cascade() call failed."""


def resolve_selector_cascade(page, strategies, *, warn=None):
    """Resolve a DOM element via a prioritized fallback cascade.

    `strategies` is an ordered list of {"type": <kind>, "value": str} dicts;
    strategies[0] is the primary. Kinds, in the register's hierarchy:
    "css" -> "xpath" -> "text" -> "aria" (alias "role": an ARIA role) ->
    "ancestor" (the value is a CSS selector of a STABLE descendant --
    a label, an icon -- and the element is its ancestor `levels` up,
    default 1: a container whose own class drifted but whose child did not).
    Returns the first locator whose element count is nonzero. Falling back
    past the primary strategy is the maintenance signal a template's
    selector has drifted, so `warn` (default: a WARNING record on
    `maintenance_log` plus warnings.warn) is called with a diagnostic
    message whenever that happens. Raises SelectorResolutionError if every
    strategy fails, or if `strategies` is empty."""
    if not strategies:
        raise SelectorResolutionError("no strategies given")
    warn = warn if warn is not None else _default_warn
    for index, strategy in enumerate(strategies):
        locator = _locate(page, strategy.get("type"), strategy.get("value"),
                          strategy.get("levels", 1))
        if locator is None:
            continue
        try:
            found = locator.count() > 0
        except Exception:
            found = False
        if not found:
            continue
        if index > 0:
            primary = strategies[0]
            warn(
                f"selector cascade fell back to strategy #{index} "
                f"({strategy.get('type')}={strategy.get('value')!r}); the "
                f"primary ({primary.get('type')}={primary.get('value')!r}) "
                f"did not resolve -- the template needs a refresh"
            )
        return locator
    raise SelectorResolutionError(f"no strategy resolved an element: {strategies}")


def _default_warn(message):
    import warnings
    maintenance_log.warning(message)
    warnings.warn(message, stacklevel=3)


def _locate(page, kind, value, levels=1):
    try:
        if kind == "css":
            return page.locator(value)
        if kind == "xpath":
            return page.locator(f"xpath={value}")
        if kind == "text":
            return page.get_by_text(value)
        if kind in ("aria", "role"):
            return page.get_by_role(value)
        if kind == "ancestor":
            levels = int(levels)
            if levels < 1:
                return None
            return page.locator(value).locator("xpath=" + "/".join([".."] * levels))
    except Exception:
        return None
    return None


def get(template_id):
    """Look up a template by id. Returns None if not found.

    v3.43.9: also searches user templates from user_templates.py so the
    template apply endpoint works for both built-ins and saved teaches."""
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    # Fall through to user-saved templates
    try:
        from .. import user_templates as _ut
        return _ut.get_user_template(template_id)
    except Exception:
        return None


def list_templates():
    """Return all templates with their metadata (no internal fields).

    v3.43.9: includes both built-in and user-saved templates, with
    a `source` field so the UI can show a 👤 badge on user ones and
    hide edit/delete on built-ins."""
    out = [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "patterns": t.get("patterns", []),
            "row_count": len((t.get("learned", {}).get("download", {}).get("row_selectors") or [])),
            "trigger_count": len((t.get("learned", {}).get("download", {}).get("trigger_selectors") or [])),
            "source": "builtin",
        }
        for t in TEMPLATES
    ]
    try:
        from .. import user_templates as _ut
        for t in _ut.list_user_templates():
            out.append({
                "id": t["id"],
                "name": t.get("name", "(unnamed)"),
                "description": t.get("description", ""),
                "patterns": t.get("patterns", []),
                "row_count": len((t.get("learned", {}).get("download", {}).get("row_selectors") or [])),
                "trigger_count": len((t.get("learned", {}).get("download", {}).get("trigger_selectors") or [])),
                "source": "user",
                "created_ts": t.get("created_ts"),
                "updated_ts": t.get("updated_ts"),
            })
    except Exception:
        pass
    return out


def suggest_for_url(url):
    """Given a URL or hostname, return matching template IDs ordered by
    relevance. Empty list when nothing matches (the UI shows the full
    library in that case).

    v3.43.8: patterns are treated as regex (which was the original
    intent — the `r""` prefix on existing patterns implies it) with a
    fallback to substring match if regex compile fails.

    v3.43.9: USER TEMPLATES WIN. If both a built-in and a user template
    match the URL, the user one is suggested first. Rationale: if you've
    gone through teach + save-as-template, your version is presumably
    better-tuned for your sites than my generic built-in guess."""
    import re as _re
    if not url: return []
    matches = []
    lower = url.lower()
    # User templates first
    try:
        from .. import user_templates as _ut
        matches.extend(_ut.suggest_for_url(url))
    except Exception:
        pass
    for t in TEMPLATES:
        for pat in t.get("patterns") or []:
            try:
                if _re.search(pat.lower(), lower):
                    matches.append(t["id"])
                    break
            except _re.error:
                # Bad regex — fall back to substring
                if pat.lower() in lower:
                    matches.append(t["id"])
                    break
    # Dedup while preserving first-seen order. User templates were
    # extended first above, so when an id collides the user entry wins
    # the slot — preserving the documented "USER TEMPLATES WIN"
    # precedence instead of silently appending a duplicate built-in.
    return list(dict.fromkeys(matches))
