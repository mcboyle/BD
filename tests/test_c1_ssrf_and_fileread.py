"""C1 security cut -- RED tests for the two confirmed ledger findings.

F-APP03-01 (medium): SSRF in POST /api/template/sandbox -- the fetch validates
ONLY the http(s) scheme, with no is-global host guard, so an internal URL
(127.0.0.1 / 169.254.169.254 / RFC1918) is fetched in-request. The fix routes
the fetch through the canonical ``_is_safe_public_host`` BEFORE the network call
(both the http and browser modes), refusing private/loopback/link-local targets.

F-APP03-02 (low): arbitrary-file-read in POST /api/sites/<sid>/cookies/import --
the ``path`` source does ``Path(path_in).read_text()`` with no allowlist check,
so an authenticated caller can read any absolute path. The fix validates the path
against the never-empty reveal-safe roots (``_validate_reveal_path``), refusing a
path outside the configured allowlist / default download roots.

Both mirror witnesses/app03_witnesses.py. RED on pristine 3.66.617:
  - sandbox returns a generic "fetch failed" (it attempted the fetch), not a
    clean private-host refusal;
  - cookie import reads the file and fails later at JSON parse ("not valid
    cookie JSON"), rather than refusing the path up front.
"""
import sys
import os
import tempfile


# ---- F-APP03-01: SSRF in api_template_sandbox --------------------------------

def _sandbox_error(client, url, mode="http"):
    r = client.post("/api/template/sandbox",
                    json={"url": url, "template": {}, "mode": mode})
    body = r.get_json() or {}
    return r.status_code, (body.get("error") or ""), body.get("ok")


def test_sandbox_allows_loopback_capability(fresh_app):
    """Loopback (127.0.0.1 / localhost) is the INTENTIONAL selector-testing
    capability and must NOT be rejected by the SSRF host guard.

    The guard exempts loopback so operators can point the sandbox at a page
    served on their own box. The fetch may still fail to connect (closed port),
    but it must NOT come back with an 'url host not allowed' refusal -- that
    would mean the guard blanket-blocked the intentional capability.
    """
    status, err, ok = _sandbox_error(fresh_app, "http://127.0.0.1:9/x")
    assert "host not allowed" not in err.lower(), (
        f"loopback was blanket-blocked; the intentional 127.0.0.1 "
        f"selector-testing capability must be preserved: {err!r}")


def test_sandbox_refuses_link_local_metadata(fresh_app):
    """169.254.169.254 (cloud metadata) must be refused as link-local."""
    status, err, ok = _sandbox_error(fresh_app,
                                     "http://169.254.169.254/latest/meta-data/")
    assert ok is False
    low = err.lower()
    assert "fetch failed" not in low, (
        f"metadata IP was fetched, not guarded: {err!r}")
    assert any(tok in low for tok in
               ("private", "link", "local", "not allowed", "blocked",
                "ssrf", "not public", "internal", "public")), (
        f"expected a link-local refusal, got {err!r}")


def test_sandbox_refuses_rfc1918(fresh_app):
    """A private 10.x address must be refused."""
    status, err, ok = _sandbox_error(fresh_app, "http://10.255.255.255/x")
    assert ok is False
    low = err.lower()
    assert "fetch failed" not in low, (
        f"private IP was fetched, not guarded: {err!r}")
    assert any(tok in low for tok in
               ("private", "not allowed", "blocked", "ssrf",
                "not public", "internal", "public")), (
        f"expected a private-host refusal, got {err!r}")


def test_sandbox_still_rejects_bad_scheme(fresh_app):
    """The existing scheme guard must remain (regression)."""
    status, err, ok = _sandbox_error(fresh_app, "file:///etc/passwd")
    assert ok is False
    assert "http" in err.lower()


# ---- F-APP03-02: arbitrary-file-read in cookie import ------------------------

def _seed_site(sid="sec_site"):
    app_mod = sys.modules["bulk_downloader.app"]
    app_mod.s_cfg[sid] = {"name": sid, "base_url": "https://example.com"}
    return sid


def test_cookie_import_refuses_path_outside_safe_roots(fresh_app):
    """A cookie 'path' pointing outside the reveal-safe roots must be refused
    up front, before the file is read.

    /etc/hostname exists in the sandbox and sits outside BD_HOME / the default
    download roots. On pristine the handler reads it and fails downstream at
    JSON parse ('not valid cookie JSON'); after the fix it refuses the path with
    an 'outside allowed roots' message and never reads the file.
    """
    sid = _seed_site()
    victim = "/etc/hostname"
    if not os.path.isfile(victim):
        # Fall back to any guaranteed-present absolute file outside BD_HOME.
        victim = os.path.abspath(sys.executable)
    r = fresh_app.post(f"/api/sites/{sid}/cookies/import", json={"path": victim})
    body = r.get_json() or {}
    err = (body.get("error") or "").lower()
    assert body.get("ok") is False
    assert "not valid cookie" not in err, (
        f"file was read (pre-fix behaviour); expected an up-front path refusal, "
        f"got {err!r}")
    assert any(tok in err for tok in
               ("outside", "allowed", "roots", "allowlist", "not permitted",
                "denied")), (
        f"expected an outside-safe-roots refusal, got {err!r}")


def test_cookie_import_rejects_traversal_path(fresh_app):
    """A traversing path must be refused (no '..' segments)."""
    sid = _seed_site("sec_site2")
    r = fresh_app.post(f"/api/sites/{sid}/cookies/import",
                       json={"path": "/tmp/../etc/hostname"})
    body = r.get_json() or {}
    err = (body.get("error") or "").lower()
    assert body.get("ok") is False
    assert "not valid cookie" not in err, (
        f"traversal path was read; expected a refusal, got {err!r}")
