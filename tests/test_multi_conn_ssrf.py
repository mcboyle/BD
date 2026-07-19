"""RED-first witness for F-RUN03-02.

multi_conn.probe() / download() fetch a site-controlled media URL (and
follow redirects) with no is_global / _classify_ip host allowlist. A media
URL -- or a redirect target -- resolving to loopback/link-local/private is
fetched: download writes the body to output_path (exfiltration-to-file);
probe leaks a status/size oracle.

These tests assert the guard REFUSES a non-public initial host and REFUSES a
redirect hop to a non-public host. On pristine 3.66.555 (no guard) they FAIL:
probe/download return connection-shaped errors (not ssrf_blocked) and the
redirect-guard hook does not exist.

Zero-arg, no live server, no network to a real internal target. The initial
host checks assert the error signature; the redirect-hop check exercises the
hook object directly with a synthetic 302.
"""

import types


def _mk_redirect_response(location):
    """A minimal stand-in for an httpx redirect response the hook inspects."""
    class _Hdrs(dict):
        def get(self, k, default=None):
            return dict.get(self, k.lower(), default)

    req = types.SimpleNamespace(url="http://example.test/start")
    resp = types.SimpleNamespace(
        is_redirect=True,
        status_code=302,
        headers=_Hdrs({"location": location}),
        request=req,
    )
    return resp


def test_probe_refuses_loopback_initial_host():
    from bulk_downloader import multi_conn
    pr = multi_conn.probe("http://127.0.0.1:1/media.bin")
    assert pr.ok is False
    assert "ssrf" in (pr.error or "").lower(), (
        f"expected an ssrf refusal, got error={pr.error!r}"
    )


def test_probe_refuses_link_local_metadata_host():
    from bulk_downloader import multi_conn
    pr = multi_conn.probe("http://169.254.169.254/latest/meta-data/")
    assert pr.ok is False
    assert "ssrf" in (pr.error or "").lower(), (
        f"expected an ssrf refusal, got error={pr.error!r}"
    )


def test_download_refuses_loopback_when_content_length_given():
    # content_length>0 skips the internal probe(), so download() must guard
    # independently. On pristine source this reaches sparse-alloc + workers.
    import tempfile
    import os
    from bulk_downloader import multi_conn
    fd, out = tempfile.mkstemp()
    os.close(fd)
    try:
        dr = multi_conn.download(
            "http://127.0.0.1:1/media.bin", out,
            content_length=1024, chunk_count=2,
        )
        assert dr.ok is False
        assert "ssrf" in (dr.error or "").lower(), (
            f"expected an ssrf refusal, got error={dr.error!r}"
        )
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def test_redirect_hook_blocks_hop_to_metadata():
    # The redirect-hop guard must reject a 302 -> 169.254.169.254 before the
    # client follows it. Pre-fix the hook does not exist (AttributeError=RED).
    from bulk_downloader import multi_conn
    hook = getattr(multi_conn, "_redirect_guard_hook", None)
    assert hook is not None, "redirect-guard hook is absent (SSRF hop unguarded)"
    resp = _mk_redirect_response("http://169.254.169.254/latest/")
    raised = False
    try:
        hook(resp)
    except Exception:
        raised = True
    assert raised, "redirect hook did not reject a hop to link-local metadata"


def test_redirect_hook_allows_public_hop():
    # A public redirect target must NOT be rejected (no over-block).
    from bulk_downloader import multi_conn
    hook = getattr(multi_conn, "_redirect_guard_hook", None)
    assert hook is not None, "redirect-guard hook is absent"
    resp = _mk_redirect_response("http://93.184.216.34/next")  # public literal
    hook(resp)  # must not raise
