"""Row 773: the egress identity a history / login record carries.

test2 downloaded through one public IP from the CLI and through another
from the browser / CF path; an IP-signed CDN answered 474 and no record
said which egress the failing request used. This module is the single
owner of that identity:

* ``normalize_egress_ip(value)``: an IP literal in canonical form, else
  exactly ``UNKNOWN_EGRESS``. Never raises. NULL is not a value here --
  "UNKNOWN" is the honest stamp for an unmeasured egress, so a record can
  be attributed (or explicitly not) without a NULL/empty ambiguity.
* an observation registry keyed by the CARRIER the traffic left through
  (``None`` for the clear path, otherwise the proxy url the client was
  built with). The registry is fed by the producers that already measure
  an exit IP (the IPv4 leak probe, the tunnel health record); nothing here
  opens a socket. Split egress is exactly the failure this pins, so an
  observation is never shared across carriers.
* a site -> carrier binding, written by the two places that CHOOSE a
  carrier (``download_egress.effective_download_proxy`` for the download
  clients, ``vpn_runtime.playwright_proxy_for_site`` for the browser), so a
  record writer that only knows the site (``db.db_log``,
  ``session_keeper.reserve_login_attempt``) resolves the identity itself
  via ``egress_ip_for_site`` and no call site changes.
"""
from __future__ import annotations

import ipaddress
import threading
from typing import Optional

UNKNOWN_EGRESS = "UNKNOWN"

_lock = threading.Lock()
_observed: dict[Optional[str], str] = {}
_carrier_of_site: dict[str, Optional[str]] = {}


def normalize_egress_ip(value) -> str:
    """Canonical IP literal, or ``UNKNOWN_EGRESS`` for anything else."""
    if value is None:
        return UNKNOWN_EGRESS
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return UNKNOWN_EGRESS


def observe_egress_ip(carrier: Optional[str], ip) -> str:
    """Record the exit IP measured THROUGH ``carrier``; returns the stamp."""
    stamp = normalize_egress_ip(ip)
    if stamp != UNKNOWN_EGRESS:
        with _lock:
            _observed[carrier] = stamp
    return stamp


def egress_ip_for(carrier: Optional[str]) -> str:
    """Last observed exit IP for ``carrier``, or ``UNKNOWN_EGRESS``."""
    with _lock:
        return _observed.get(carrier, UNKNOWN_EGRESS)


def bind_site_carrier(site_id: str, carrier: Optional[str]) -> Optional[str]:
    """Record that ``site_id``'s clients now leave through ``carrier``.

    ``None`` is the clear path and is bound too, so a site whose tunnel went
    away is not still attributed to the tunnel's last exit. Returns the
    carrier so a chooser can bind on its return line.
    """
    with _lock:
        _carrier_of_site[str(site_id)] = carrier
    return carrier


def egress_ip_for_site(site_id) -> str:
    """Exit IP of the carrier ``site_id`` is bound to, or ``UNKNOWN_EGRESS``."""
    with _lock:
        if str(site_id) not in _carrier_of_site:
            return UNKNOWN_EGRESS
        return _observed.get(_carrier_of_site[str(site_id)], UNKNOWN_EGRESS)
