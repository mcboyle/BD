"""bulk_downloader.template_extractor_impl -- decomposed template_extractor package.

template_extractor.py is a thin ADD-only re-export shim over this package (R1
shim-over-rm; no rm on deploy, H-01). The full surface (3 public + 21 private fns
+ 8 consts) is re-exported here and on the shim so module-attribute access
(`from . import template_extractor as _te; _te._walk_for_candidates`) and white-box
from-imports (dry_run) keep resolving unchanged."""

from ._constants import (
    _CANDIDATE_TAGS,
    _CANDIDATE_ATTRS,
    MIN_SCORE_FOR_CANDIDATE,
    MIN_SCORE_FOR_ROW,
    MIN_SCORE_FOR_TEMPLATE,
    REFINE_PROMPT,
    _LOGIN_SUBMIT_TEXT,
)
from ._css import _CSS_SAFE, _css_escape, _css_escape_attr
from .candidates import (
    extract_from_html,
    _walk_for_candidates,
    _serialize_classlist,
    _collect_ancestor_signals,
    _score_all,
    _generalize_selectors,
    _looks_stable_id,
    _looks_utility_class,
    _pick_primary_class,
    _pick_keyword,
    _build_template,
    _name_from_url,
    _patterns_from_url,
    _validate_template,
)
from .refine import refine_with_ai, _clean_selector_list, _clean_warning_list
from .login_extract import (
    _login_form_prefix,
    _login_selectors,
    _login_is_honeypot,
    _login_find_submit,
    extract_login_from_html,
)

__all__ = [
    "extract_from_html",
    "refine_with_ai",
    "extract_login_from_html",
    "MIN_SCORE_FOR_CANDIDATE",
    "MIN_SCORE_FOR_ROW",
    "MIN_SCORE_FOR_TEMPLATE",
]
