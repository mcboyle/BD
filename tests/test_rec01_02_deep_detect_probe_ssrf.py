"""F-REC01-02 (+ F-REC03-01) RED-first: the deep_detect runtime probe client
must SSRF-guard every request.

deep_detect_live builds one httpx.Client via _build_default_http_client and
hands it to three page-derived-URL egress sinks (_probe_head,
_fetch_manifest_capped, _poll_async_workflow). Pristine 580 builds a PLAIN
client with no host gate, so a page can steer a HEAD/GET/POST at private,
loopback, link-local (169.254/16), CGNAT (100.64/10) or reserved space.

The fix installs an httpx 'request' event hook (the same canonical pattern as
tier_probe._ssrf_guard_hook) that fires on the initial request AND every
redirect target, classifies the host via provider_resolve_impl._common.
_is_safe_public_host, and raises SSRFBlocked on a non-public host. These tests
pin that the client carries such a hook and that it fails closed without
over-blocking public hosts.

Custom runner: zero-arg functions, no pytest builtins.
"""


def _client_or_none():
    from bulk_downloader.deep_detect import orchestrate
    return orchestrate._build_default_http_client()


def test_default_http_client_blocks_link_local_metadata_host():
    """RED on pristine: the client has no SSRF request hook, so a link-local
    metadata host is not blocked."""
    import httpx
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
    client = _client_or_none()
    if client is None:
        return  # httpx unavailable; live path not exercisable
    hooks = client.event_hooks.get("request") or []
    assert hooks, "deep_detect probe client has no request event hook (SSRF unguarded)"
    req = httpx.Request("HEAD", "http://169.254.169.254/latest/meta-data/")
    raised = False
    try:
        for h in hooks:
            h(req)
    except SSRFBlocked:
        raised = True
    assert raised, "SSRF hook did not block a link-local (169.254) metadata host"


def test_default_http_client_blocks_private_and_loopback():
    """Fail-closed across the private/loopback/CGNAT classes."""
    import httpx
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
    client = _client_or_none()
    if client is None:
        return
    hooks = client.event_hooks.get("request") or []
    assert hooks, "no request event hook"
    for host in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "100.64.0.1"):
        req = httpx.Request("GET", "http://%s/" % host)
        blocked = False
        try:
            for h in hooks:
                h(req)
        except SSRFBlocked:
            blocked = True
        assert blocked, "SSRF hook failed to block %s" % host


def test_default_http_client_allows_public_ip():
    """Boundary guard: a public IP literal must pass (no over-blocking)."""
    import httpx
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
    client = _client_or_none()
    if client is None:
        return
    hooks = client.event_hooks.get("request") or []
    if not hooks:
        return  # covered by the RED test above
    req = httpx.Request("HEAD", "http://93.184.216.34/")  # example.com, public
    try:
        for h in hooks:
            h(req)
    except SSRFBlocked as e:
        assert False, "SSRF hook wrongly blocked a public IP literal: %s" % e
