"""bulk_downloader.template_extractor -- thin re-export shim over template_extractor_impl/.

Decomposed @v3.66.449 (DECOMP-LEAF cut 2). ADD-only (R1 shim-over-rm): this module
stays a FILE, nothing is deleted on deploy, no overlay-ghost. Re-exports the COMPLETE
surface explicitly (no `import *`, which would drop underscored names -- Phase-1
lesson) so every external consumer keeps working byte-for-byte."""

from .template_extractor_impl import (  # noqa: F401
    MIN_SCORE_FOR_CANDIDATE,
    MIN_SCORE_FOR_ROW,
    MIN_SCORE_FOR_TEMPLATE,
    REFINE_PROMPT,
    _CANDIDATE_ATTRS,
    _CANDIDATE_TAGS,
    _CSS_SAFE,
    _LOGIN_SUBMIT_TEXT,
    _build_template,
    _clean_selector_list,
    _clean_warning_list,
    _collect_ancestor_signals,
    _css_escape,
    _css_escape_attr,
    _generalize_selectors,
    _login_find_submit,
    _login_form_prefix,
    _login_is_honeypot,
    _login_selectors,
    _looks_stable_id,
    _looks_utility_class,
    _name_from_url,
    _patterns_from_url,
    _pick_keyword,
    _pick_primary_class,
    _score_all,
    _serialize_classlist,
    _validate_template,
    _walk_for_candidates,
    extract_from_html,
    extract_login_from_html,
    refine_with_ai,
)

__all__ = [
    "extract_from_html",
    "refine_with_ai",
    "extract_login_from_html",
]
