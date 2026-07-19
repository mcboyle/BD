"""site_templates.accessors -- the 3 public accessors, verbatim from templates.py.

Each merges a user_templates overlay (lazy `from .. import user_templates as _ut`,
and reads the package-level TEMPLATES list."""

from . import TEMPLATES


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
