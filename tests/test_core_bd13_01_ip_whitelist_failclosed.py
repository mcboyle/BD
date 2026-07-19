"""F-COREBD13-01 (RED-first): share-token IP whitelist must fail CLOSED.

A token minted with a non-empty ip_whitelist must be DENIED when the caller's
client_ip is empty/unknown. Pristine 578 skips the whitelist block whenever
client_ip is falsy (`if wl and client_ip:`), so a whitelisted token is accepted
with no caller IP -- the fail-OPEN bug. These tests pin the fail-closed
contract and guard the boundary so the fix does not over-tighten.

Custom runner: zero-arg functions, no pytest builtins; globals restored in
try/finally.
"""
import os
import tempfile


def _isolate_db():
    """Point the DB at a fresh temp file (the _isolated_bd idiom). Returns a
    restore callable."""
    import bulk_downloader.db as _dbmod
    tmpd = tempfile.mkdtemp()
    orig = _dbmod.DB_PATH
    _dbmod.DB_PATH = os.path.join(tmpd, "bd_shares_test.db")

    def _restore():
        _dbmod.DB_PATH = orig
    return _restore


def test_whitelisted_token_denied_when_client_ip_empty():
    """RED on pristine: whitelisted token + empty client_ip is accepted."""
    restore = _isolate_db()
    try:
        from bulk_downloader import shares
        res = shares.create_token(scopes=["all"], label="t",
                                  ip_whitelist="203.0.113.7")
        assert res.get("ok"), res
        out = shares.verify_token(res["token"], required_scope="all",
                                  client_ip="")
        assert out.get("ok") is False, (
            "whitelisted token accepted with empty client_ip (fail-open)")
        assert out.get("reason") == "ip not allowed", out
    finally:
        restore()


def test_whitelisted_token_allowed_for_listed_ip():
    """Boundary guard: a listed IP must still pass (no over-tightening)."""
    restore = _isolate_db()
    try:
        from bulk_downloader import shares
        res = shares.create_token(scopes=["all"], label="t",
                                  ip_whitelist="203.0.113.7, 198.51.100.4")
        assert res.get("ok"), res
        out = shares.verify_token(res["token"], required_scope="all",
                                  client_ip="198.51.100.4")
        assert out.get("ok") is True, out
    finally:
        restore()


def test_no_whitelist_allows_empty_ip():
    """Boundary guard: no whitelist set -> caller IP is irrelevant."""
    restore = _isolate_db()
    try:
        from bulk_downloader import shares
        res = shares.create_token(scopes=["all"], label="t")
        assert res.get("ok"), res
        out = shares.verify_token(res["token"], required_scope="all",
                                  client_ip="")
        assert out.get("ok") is True, out
    finally:
        restore()
