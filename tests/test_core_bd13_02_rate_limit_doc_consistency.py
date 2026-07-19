"""F-COREBD13-02 (RED-first): no documented-but-unimplemented per-token rate
limit, and no dangling shares._rate_ok reference.

Pristine: shares.py's module docstring advertises 'rate-limited per-token
(default 60 req/min)' while no _rate_ok symbol or rate check exists, and
api_tokens.py points callers to 'mirror shares._rate_ok' -- a reference to a
function that does not exist (DP-13). These tests pin doc<->impl consistency:
if the docstring claims per-token rate limiting, an implementation must exist;
and api_tokens.py may not reference a shares symbol that does not exist.

Custom runner: zero-arg functions, no pytest builtins.
"""
import os


def _pkg_dir():
    import bulk_downloader
    return os.path.dirname(bulk_downloader.__file__)


def _shares_has_rate_impl():
    import bulk_downloader.shares as shares
    if hasattr(shares, "_rate_ok"):
        return True
    src = open(os.path.join(_pkg_dir(), "shares.py"), encoding="utf-8").read()
    return "def _rate_ok" in src


def test_docstring_rate_limit_claim_matches_implementation():
    """If the module docstring ADVERTISES that tokens are rate-limited, an
    implementation must exist. A disclaimer ('there is no rate limit') is not
    an advertisement. RED on pristine: 'rate-limited ... (default 60 req/min)'
    is advertised while no impl exists."""
    import bulk_downloader.shares as shares
    doc = (shares.__doc__ or "").lower()
    advertises = ("rate-limited" in doc or "req/min" in doc
                  or "requests per minute" in doc or "requests/min" in doc)
    if advertises:
        assert _shares_has_rate_impl(), (
            "shares docstring advertises rate limiting but no implementation "
            "(_rate_ok / rate check) exists")


def test_no_dangling_shares_rate_ok_reference():
    """api_tokens.py must not reference a shares symbol that does not exist.
    RED on pristine: the 'shares._rate_ok' reference is present but the symbol
    is absent."""
    import bulk_downloader.shares as shares
    src = open(os.path.join(_pkg_dir(), "api_tokens.py"),
               encoding="utf-8").read()
    if "shares._rate_ok" in src:
        assert hasattr(shares, "_rate_ok"), (
            "api_tokens.py references shares._rate_ok which does not exist")
