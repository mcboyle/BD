"""F-SWEEP-N3 RED-first: _success_url_matches must require a path-segment
boundary, so a same-origin prefix sibling ('/dashboard-evil') is NOT classified
as landing on the configured success path ('/dashboard').

Pristine 567 uses bare str.startswith at three sites (path-only, bare-host,
full-URL), so the two *_prefix_sibling_* asserts FAIL RED before the fix. The
remaining asserts already pass on pristine and are regression guards proving the
segment-boundary fix does not break legitimate exact / subpath / root matches or
the error-query short-circuit.

Harness note: zero-arg tests, stdlib-only, safe under run_tests.py (no caplog /
tmp_path / monkeypatch).
"""
from bulk_downloader.login_impl.replay import _success_url_matches


def test_path_only_prefix_sibling_rejected():
    # RED on pristine: '/dashboard-evil'.startswith('/dashboard') is True.
    assert _success_url_matches("/dashboard", "https://site.com/dashboard-evil") is False


def test_full_url_prefix_sibling_rejected():
    # RED on pristine: scheme+host match, '/account-x'.startswith('/account') True.
    assert _success_url_matches("https://site.com/account",
                                "https://site.com/account-x") is False


def test_path_only_exact_match():
    assert _success_url_matches("/dashboard", "https://site.com/dashboard") is True


def test_path_only_subpath_match():
    assert _success_url_matches("/dashboard", "https://site.com/dashboard/home") is True


def test_root_success_matches_any_path():
    # A configured success_url of '/' should match any path on the host.
    assert _success_url_matches("/", "https://site.com/anything/here") is True


def test_full_url_subpath_match():
    assert _success_url_matches("https://site.com/account",
                                "https://site.com/account/settings") is True


def test_full_url_cross_host_rejected():
    assert _success_url_matches("https://site.com/account",
                                "https://evil.com/account") is False


def test_error_query_short_circuit_preserved():
    # Regression: an error-indicator query is still a non-match even on a path hit.
    assert _success_url_matches("/dashboard", "https://site.com/dashboard?error=nope") is False
