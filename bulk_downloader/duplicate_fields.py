"""Duplicate-field honeypot detection — F11 (v3.66.50).

A form that pairs a real visible input with a hidden same-name decoy
is a classic anti-bot trap: a human fills only the visible field, but a
bot that auto-fills every same-named field writes the decoy too and
trips the honeypot.

The logic was originally embedded in :mod:`dom_honeypot` alongside the
link/button decoy checks. F11 lifts it into this standalone module so
callers that only need the form-side check (e.g. a login-flow analyzer)
can import it without pulling in the link-decoy surface. The visibility
primitives stay in :mod:`dom_honeypot` (shared with the link check);
this module imports them.

Backward compatibility: ``from bulk_downloader.dom_honeypot import
find_duplicate_field_decoys`` still works — dom_honeypot re-exports the
name via a module-level ``__getattr__`` (lazy, to avoid an import
cycle).
"""

from __future__ import annotations

# Visibility primitives live in dom_honeypot (shared with the link
# decoy check). Importing them here does NOT create a cycle: dom_honeypot
# re-exports our public function lazily via __getattr__, so it never
# eagerly imports this module at load time.
from .dom_honeypot import _bs_css_class_hidden, _bs_inline_style_hidden


def find_duplicate_field_decoys(form_inputs, css_text: str = ""):
    """Detect paired hidden+visible inputs sharing the same name —
    a known anti-bot honeypot pattern. The form expects users to
    fill the visible one; bots that auto-fill every same-named
    field trip the trap.

    Parameters
    ----------
    form_inputs : iterable of BeautifulSoup ``<input>`` elements
    css_text : str
        Document CSS text for class-based visibility consultation
        (passed through to the visibility helper).

    Returns
    -------
    list of dicts
        Each entry: ``{"name": str, "reason": str}``. The decoy
        (hidden) member of the pair is what's reported — the
        visible member is the legitimate input. If multiple inputs
        share a name and all are visible (genuine multi-step form
        chunking), they are NOT reported. If all are hidden, that
        likely isn't a duplicate-decoy pattern (more often a
        framework artifact like dual CSRF mirrors) — also not
        reported.

    Note: the visibility check here uses the form-field rules
    (standalone tabindex=-1) rather than the link-style rules, since
    tabindex semantics differ between the two cases.
    """
    by_name: dict = {}
    for inp in form_inputs:
        name = inp.get("name") or ""
        if not name:
            continue
        by_name.setdefault(name, []).append(inp)

    decoys = []
    for name, items in by_name.items():
        if len(items) < 2:
            continue
        visible = []
        hidden = []
        for inp in items:
            # For inputs, use the standalone-tabindex semantics
            # from deep_detect._is_visible_input — that's the
            # right rule for form fields.
            if _input_is_hidden(inp, css_text):
                hidden.append(inp)
            else:
                visible.append(inp)
        # Report only mixed-visibility pairs — that's the
        # honeypot shape (visible decoy companion).
        if visible and hidden:
            decoys.append({
                "name": name,
                "reason": "duplicate_field_hidden_decoy",
            })
    return decoys


def _input_is_hidden(el, css_text: str = "") -> bool:
    """Visibility check for ``<input>`` elements — applies the
    form-field rules (including standalone tabindex=-1). Returns
    True if the input is hidden.

    Used only by :func:`find_duplicate_field_decoys`. Distinct from
    the link/button visibility rules in :mod:`dom_honeypot` because
    tabindex semantics differ between the two cases.
    """
    if _bs_inline_style_hidden(el):
        return True
    if el.get("hidden") is not None:
        return True
    if el.get("type") == "hidden":
        return True
    if el.get("aria-hidden") == "true":
        return True
    if el.get("tabindex") == "-1":
        # Form inputs: standalone trigger. Hidden input with
        # tabindex=-1 is overwhelmingly a honeypot field.
        return True
    if _bs_css_class_hidden(el, css_text):
        return True
    return False
