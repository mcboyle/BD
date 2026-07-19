"""Track-K: fail-closed proxy selection for in-process payload downloads.

The browser path (``runner`` launch / ``vpn_runtime.playwright_proxy_for_site``)
already routes chromium through the per-site VPN tunnel and fails closed when a
``vpn_required`` site's tunnel is unavailable. The in-process download clients
(``curl_cffi`` / ``httpx`` / ``multi_conn``) historically derived their proxy
*only* from the explicit per-site ``proxy`` config and ignored the tunnel -- so a
``vpn_required`` site whose tunnel was down could egress the payload bytes on the
clear interface.

``effective_download_proxy`` mirrors the browser's selection for the download
clients by reusing the SAME resolver (``vpn_runtime.get_socks_url_for_site``),
which already encodes the operator's posture. The resolver is injected so this
stays a pure, side-effect-free decision -- the production resolver brings the
tunnel up on demand and is therefore not safe to call from unit tests.
"""
from typing import Callable, Optional

__all__ = ["effective_download_proxy"]


def effective_download_proxy(
    explicit_proxy: Optional[str],
    site_id: str,
    socks_for_site: Optional[Callable[[str], Optional[str]]],
) -> Optional[str]:
    """Return the proxy URL an in-process download client should use.

    Precedence / posture (the VPN behavior is inherited from ``socks_for_site``):

      * A non-empty ``explicit_proxy`` always wins -- preserves the pre-Track-K
        per-site proxy behavior, and lets an operator override the tunnel.
      * ``socks_for_site is None`` (VPN runtime unavailable / degraded import):
        return the explicit proxy or ``None`` -- behave exactly as before.
      * Otherwise return ``socks_for_site(site_id)``, which the VPN runtime
        defines as:
          - a tunnel SOCKS url   when the site's tunnel is up,
          - ``None``             when no tunnel is configured / the site is not
                                 ``vpn_required`` (degrade open -- the operator's
                                 opt-in posture), and
          - *raises* ``VPNRequiredError`` when the site IS ``vpn_required`` but
            the tunnel is down/killed. This function lets that exception
            propagate so the caller fails closed and never builds an unproxied
            client. The payload bytes never touch the clear interface.
    """
    explicit = (explicit_proxy or "").strip()
    if explicit:
        return explicit
    if socks_for_site is None:
        return None
    return socks_for_site(site_id)
