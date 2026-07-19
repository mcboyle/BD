"""test_template_extractor_surface_lock.py -- attribute-surface guard for the
template_extractor -> template_extractor_impl package split (DECOMP-LEAF cut 2).

Freezes the names external consumers reach: the 3 public extractors, the 3 privates
dry_run.py white-box imports (_walk_for_candidates / _score_all / _generalize_selectors),
and the 2 scoring consts referenced off the module. Asserts FROZEN is a SUBSET of the
shim's attribute surface -- guards drops, not leaks (a true package may legitimately
expose more). Runs under the custom run_tests.py harness (zero-arg functions).
"""
from bulk_downloader import template_extractor as te

FROZEN = (
    {"extract_from_html", "refine_with_ai", "extract_login_from_html"}
    | {"_walk_for_candidates", "_score_all", "_generalize_selectors"}
    | {"MIN_SCORE_FOR_ROW", "MIN_SCORE_FOR_CANDIDATE"}
)


def test_surface_lock_frozen_subset():
    missing = FROZEN - set(dir(te))
    assert not missing, f"shim dropped frozen names: {sorted(missing)}"


def test_full_surface_reexported():
    # the shim must re-export the COMPLETE original surface (3 pub + 21 priv + 8 consts)
    pub = ["extract_from_html", "refine_with_ai", "extract_login_from_html"]
    priv = [
        "_walk_for_candidates", "_serialize_classlist", "_collect_ancestor_signals",
        "_score_all", "_generalize_selectors", "_looks_stable_id", "_looks_utility_class",
        "_pick_primary_class", "_pick_keyword", "_css_escape", "_css_escape_attr",
        "_build_template", "_name_from_url", "_patterns_from_url", "_validate_template",
        "_clean_selector_list", "_clean_warning_list", "_login_form_prefix",
        "_login_selectors", "_login_is_honeypot", "_login_find_submit",
    ]
    consts = [
        "_CANDIDATE_TAGS", "_CANDIDATE_ATTRS", "MIN_SCORE_FOR_CANDIDATE",
        "MIN_SCORE_FOR_ROW", "MIN_SCORE_FOR_TEMPLATE", "_CSS_SAFE",
        "REFINE_PROMPT", "_LOGIN_SUBMIT_TEXT",
    ]
    missing = [n for n in pub + priv + consts if not hasattr(te, n)]
    assert not missing, f"shim is incomplete, missing: {missing}"


def test_dry_run_white_box_imports_resolve():
    # dry_run.py does `from .template_extractor import (_walk_for_candidates, _score_all, ...)`
    from bulk_downloader.template_extractor import (  # noqa: F401
        _generalize_selectors,
        _score_all,
        _walk_for_candidates,
    )


def test_extractors_callable_and_return_dicts():
    out = te.extract_from_html('<div class="row"><a href="x.mp4">v</a></div>', "https://e.com")
    assert isinstance(out, dict)
    login = te.extract_login_from_html(
        '<form action="/login"><input name="user">'
        '<input type="password" name="pass"><button type="submit">Login</button></form>'
    )
    assert isinstance(login, dict)
