"""F-SWEEP-N6 RED-first: the bulk_url_transform endpoint compiles a
request-supplied regex and runs subn() over every queued URL with no match
timeout, so a nested-quantifier pattern ((a+)+ / (.*)*) hangs the worker
(catastrophic backtracking). The fix adds a pre-compile complexity guard,
_regex_redos_risk(pattern) -> reason str ('' == safe), rejected with 400 before
any compile/subn.

Pristine 567 has no such helper, so the import FAILS RED before the fix.

This suite tests the guard in isolation (never runs subn on a pathological
pattern, so it cannot hang the band). It asserts both directions: the ReDoS
signature is flagged AND common benign URL-transform regexes pass (over-blocking
a user-facing regex tool is the functionality risk we are avoiding).

Harness note: zero-arg tests, stdlib-only, safe under run_tests.py.
"""
from bulk_downloader.app_sites_id_core import _regex_redos_risk


def test_nested_unbounded_quantifier_flagged():
    for pat in ["(a+)+$", "(a*)*", "(.+)+", "(.*)*", "([a-z]+)*", "(x*)*y", "(a+){2,}"]:
        assert _regex_redos_risk(pat) != "", f"expected ReDoS flag for {pat!r}"


def test_benign_url_transform_patterns_pass():
    # Realistic patterns an operator writes for bulk URL fixes. None may be flagged.
    for pat in [
        r"https?://(www\.)?example\.com/(.+)",
        r"\?.*$",
        r"/$",
        r"//cdn1\.",
        r"(\d{4})-(\d{2})-(\d{2})",
        r"(\d+){3}",            # bounded outer repetition -- not exponential
        r"(video|audio)_(\d+)",
        r"(a|b)+",              # single-char alternation -- linear
    ]:
        assert _regex_redos_risk(pat) == "", f"false-positive over-block on {pat!r}"


def test_overlong_pattern_flagged():
    assert _regex_redos_risk("a" * 1001) != ""


def test_empty_and_short_patterns_pass():
    assert _regex_redos_risk("") == ""
    assert _regex_redos_risk("abc") == ""
