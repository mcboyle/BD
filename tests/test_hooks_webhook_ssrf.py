"""RED-first guard for v3.66.553: hooks._validate_webhook_url SSRF host-guard (F-CORE_BD04-01).

_validate_webhook_url validated only the URL SCHEME (http/https) and that a hostname was
present -- NO IP-range classification -- so a webhook URL resolving to cloud metadata
(169.254.169.254), CGNAT (100.64/10), or other dangerous ranges passed and was dispatched
by urllib.urlopen across all six hook sinks (send_webhook, stash_trigger_scan, plex_refresh,
jellyfin_refresh, home_assistant_notify).

Scope (operator-chosen): the plex/jellyfin/home-assistant/stash hooks LEGITIMATELY target
internal LAN services, so RFC1918 private + loopback are ALLOWED. The fix rejects only the
never-legitimate SSRF ranges -- link-local / cloud-metadata (169.254/16, fe80::/10), RFC 6598
CGNAT (100.64/10), reserved, multicast, unspecified (and the IPv6 IMDS endpoint) -- and
re-validates redirect targets (urlopen follows redirects by default). NOTE: this diverges
from the finding's literal repro (which expected 127.0.0.1 -> reject); loopback is ALLOWED
by the chosen scope.

RED on the pre-553 tree: scheme-only validator -> a metadata/CGNAT/multicast/unspecified URL
returns ok=True, and _HookRedirectHandler does not exist. GREEN once _validate_webhook_url
resolves + classifies the host (rejecting those ranges while allowing LAN/loopback/public)
and the redirect handler re-validates hops.

Convention: zero-arg fns; literal-IP assertions (resolve locally, no network).
"""
import bulk_downloader.hooks as hooks


def _ok(url):
    res = hooks._validate_webhook_url(url)   # -> (ok, url_or_msg)
    return bool(res[0])


# ---- must be REJECTED (never-legitimate SSRF ranges) ----

def test_rejects_cloud_metadata_ipv4():
    assert _ok("http://169.254.169.254/latest/meta-data/") is False


def test_rejects_link_local():
    assert _ok("http://169.254.10.5/") is False


def test_rejects_cgnat():
    assert _ok("http://100.64.0.1/") is False


def test_rejects_multicast():
    assert _ok("http://224.0.0.1/") is False


def test_rejects_unspecified():
    assert _ok("http://0.0.0.0/") is False


# ---- must be ALLOWED (chosen scope: LAN integrations + loopback + public) ----

def test_allows_rfc1918_lan():
    # Plex/Jellyfin/Home Assistant/Stash live here -- must NOT break.
    assert _ok("http://192.168.1.50:32400/") is True
    assert _ok("http://10.0.70.20:9999/") is True
    assert _ok("http://172.16.5.5/") is True


def test_allows_loopback():
    # single-box integrations (per chosen scope)
    assert _ok("http://127.0.0.1:8096/") is True


def test_allows_public():
    assert _ok("http://93.184.216.34/webhook") is True


# ---- scheme guard preserved (v3.42.0 behavior) ----

def test_still_rejects_non_http_scheme():
    assert _ok("file:///etc/passwd") is False
    assert _ok("gopher://127.0.0.1/") is False


# ---- redirect re-validation (urlopen follows redirects by default) ----

def test_redirect_to_metadata_blocked():
    import urllib.request
    import urllib.error
    h = hooks._HookRedirectHandler()
    req = urllib.request.Request("http://93.184.216.34/")
    raised = False
    try:
        h.redirect_request(req, None, 302, "Found", {},
                           "http://169.254.169.254/latest/meta-data/")
    except urllib.error.HTTPError:
        raised = True
    assert raised, "a redirect to cloud metadata must be blocked"


def test_redirect_to_lan_allowed():
    import urllib.request
    h = hooks._HookRedirectHandler()
    req = urllib.request.Request("http://93.184.216.34/")
    out = h.redirect_request(req, None, 302, "Found", {}, "http://192.168.1.50/next")
    assert out is not None, "a redirect to a LAN host should be followed"
