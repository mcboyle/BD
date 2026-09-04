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
import pytest

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


# ---- every sink must CONSULT the guard, not merely have one available -------
#
# Everything above judges the PREDICATE, plus the redirect handler. Nothing
# above judges whether the five outbound sinks THIS FILE'S OWN DOCSTRING names
# -- send_webhook, stash_trigger_scan, plex_refresh, jellyfin_refresh,
# home_assistant_notify -- actually call it. A sink that lost its guard call
# would leave every assertion above green and dispatch the request anyway,
# which is the whole finding the guard exists to prevent.
#
# These prove it at the only seam that matters: the module's single urlopen
# entry point. If a refused target ever reaches _hook_urlopen, the request was
# going out.

_METADATA_URL = "http://169.254.169.254/latest/meta-data/"
_LAN_URL = "http://192.168.1.50/hook"


def _spy_on_dispatch(monkeypatch):
    """Record every request the module tries to send, and never send one.

    Returns the list of dispatched URLs. Raising keeps a spy return value from
    being mistaken for a response; each sink wraps its request in
    ``except Exception`` and turns it into a failed (ok, message), which the
    LAN control below relies on.
    """
    dispatched = []

    def _spy(req, timeout=15):
        dispatched.append(getattr(req, "full_url", str(req)))
        raise RuntimeError("BD-TEST: a refused target reached the network seam")

    monkeypatch.setattr(hooks, "_hook_urlopen", _spy)
    return dispatched


_SINKS = [
    ("send_webhook", lambda url: hooks.send_webhook(url, {"event": "test"})),
    ("stash_trigger_scan", lambda url: hooks.stash_trigger_scan(url, "api-key")),
    ("plex_refresh", lambda url: hooks.plex_refresh(url, "plex-token")),
    ("jellyfin_refresh", lambda url: hooks.jellyfin_refresh(url, "api-key")),
    ("home_assistant_notify",
     lambda url: hooks.home_assistant_notify(url, "ha-token", "mobile_app", "hi")),
]
_SINK_IDS = [name for name, _ in _SINKS]


@pytest.mark.parametrize("name,call", _SINKS, ids=_SINK_IDS)
def test_sink_refuses_cloud_metadata_before_dispatching(name, call, monkeypatch):
    """The sink must refuse a metadata target WITHOUT reaching the network."""
    dispatched = _spy_on_dispatch(monkeypatch)
    result = call(_METADATA_URL)
    assert dispatched == [], (
        f"{name} dispatched a request to a refused target: {dispatched!r}")
    ok, message = result[0], result[1]
    assert ok is False, f"{name} reported success for a refused target"
    assert "rejected" in str(message).lower(), (
        f"{name} failed for some other reason than the host guard: {message!r}")


@pytest.mark.parametrize("name,call", _SINKS, ids=_SINK_IDS)
def test_sink_still_dispatches_to_a_lan_target(name, call, monkeypatch):
    """Negative control, and the precondition for the test above.

    LAN is DELIBERATELY allowed (see this file's docstring), so each sink must
    still dispatch there. This is also what proves the spy can record at all --
    without it, ``dispatched == []`` above would pass for a sink that never
    reaches the seam under any input.
    """
    dispatched = _spy_on_dispatch(monkeypatch)
    call(_LAN_URL)
    assert len(dispatched) == 1, (
        f"{name} did not dispatch exactly one request to an allowed LAN "
        f"target: {dispatched!r}")
    assert "192.168.1.50" in dispatched[0], dispatched[0]
