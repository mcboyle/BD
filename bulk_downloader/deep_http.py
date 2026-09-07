"""Guarded stdlib/requests HTTP for token-bearing deep integrations.

``allow_private_hosts`` relaxes host classification for deliberate LAN
integrations only.  It never relaxes the same-origin-only redirect policy.

SUBSTITUTION SEAM.  :func:`guarded_open` classifies the target and only then
delegates the send to the module-level opener ``_OPENER``.  That opener is the
single substitutable point: a test that needs a fake transport replaces
``deep_http._OPENER``, and classification still runs ahead of it, because
``_check`` executes before the opener is reached on every hop.  There is no
branch in this module that skips ``_check``, no hostname is special-cased and
no environment variable relaxes anything, so a host that fails to resolve is
refused whether or not a test is running.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import requests

from bulk_downloader.provider_resolve_impl._common import SSRFBlocked, _is_safe_public_host

_MAX_REDIRECTS = 5


def refusal_message(reason, setting_name: str) -> str:
    """The refusal an operator actually reads.  It NAMES the per-site setting
    that re-enables a LAN target, because an operator whose own media server
    stops answering has to be told how to allow it here, not in a changelog."""
    return (f"{reason}; set {setting_name}=true on this site to allow a "
            "private address")


def _check(url: str, allow_private_hosts: bool) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SSRFBlocked(f"unsupported URL scheme: {parsed.scheme or 'missing'}")
    if not allow_private_hosts:
        safe, reason = _is_safe_public_host(parsed.hostname or "")
        if not safe:
            raise SSRFBlocked(str(reason))


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme.lower(), (parsed.hostname or "").lower(),
            parsed.port or (443 if parsed.scheme.lower() == "https" else 80))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are decided by :func:`guarded_open`, never by urllib, whose
    own handler copies every header (token included) to the new host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# The transport every guarded send goes through, AFTER classification.
_OPENER = urllib.request.build_opener(_NoRedirect)


def guarded_open(url, *, data=None, method="GET", headers=None, timeout,
                 allow_private_hosts=False):
    """Open a URL after classification; follow at most five same-origin hops."""
    current = url
    request_data = data
    request_method = method
    request_headers = dict(headers or {})
    for _hop in range(_MAX_REDIRECTS + 1):
        _check(current, allow_private_hosts)
        request = urllib.request.Request(current, data=request_data,
                                         method=request_method,
                                         headers=request_headers)
        try:
            return _OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in {301, 302, 303, 307, 308}:
                raise
            target = urllib.parse.urljoin(current, exc.headers.get("Location", ""))
            if not exc.headers.get("Location") or _origin(target) != _origin(current):
                raise SSRFBlocked(f"cross-origin redirect refused: {current} -> {target}")
            current = target
            if exc.code == 303 or (exc.code in {301, 302} and request_method == "POST"):
                request_data, request_method = None, "GET"
    raise SSRFBlocked("redirect hop limit exhausted")


class _GuardedSession(requests.Session):
    def __init__(self, *, allow_private_hosts=False):
        super().__init__()
        self._allow_private_hosts = allow_private_hosts

    def request(self, method, url, **kwargs):
        kwargs["allow_redirects"] = False
        current = url
        for _hop in range(_MAX_REDIRECTS + 1):
            _check(current, self._allow_private_hosts)
            response = super().request(method, current, **kwargs)
            location = response.headers.get("Location")
            if not (response.is_redirect and location):
                return response
            target = urllib.parse.urljoin(current, location)
            if _origin(target) != _origin(current):
                raise SSRFBlocked(
                    f"cross-origin redirect refused: {current} -> {target}")
            if response.status_code == 303 or (
                    response.status_code in {301, 302}
                    and method.upper() == "POST"):
                method = "GET"
                for body_kwarg in ("data", "json", "files"):
                    kwargs.pop(body_kwarg, None)
            current = target
        raise SSRFBlocked("redirect hop limit exhausted")


def guarded_session(*, allow_private_hosts=False):
    """Return a session which classifies every request and never redirects."""
    return _GuardedSession(allow_private_hosts=allow_private_hosts)
