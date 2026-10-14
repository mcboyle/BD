"""A site's browser cookies, scoped to a URL the way a browser scopes them.

O1789 cookie-egress class (detect-extract HIGH-F1/F11, session-auth-capture
HIGH-F1). Playwright hands cookies over as dicts carrying name, value, domain,
path and secure. Flattening them to {name: value} drops the domain, and an HTTP
client given a flat dict sends EVERY cookie in the jar -- the site's session,
an IdP's SSO cookie -- to whatever host it is pointed or redirected at
(measured: detect-extract/ev/p_cookie_class.log M1/M2b/M3/M5). Every client
that carries a site's cookies builds them here instead.
"""
from __future__ import annotations

from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy

__all__ = ["scoped_jar", "cookie_header_for"]


def scoped_jar(browser_cookies, *, strict_host_only: bool = False) -> CookieJar:
    """Browser cookies as a CookieJar keeping each one's domain, path and
    secure flag: a cookie goes to its own domain only and a secure cookie never
    goes over http. A cookie without a domain cannot be scoped and is dropped
    (fail closed); so is anything that is not a cookie dict, a flat
    {name: value} included.

    strict_host_only: a cookie set without a leading dot goes to its exact host
    only, as in a browser. httpx copies a client's jar into a default-policy jar
    on every request, so for httpx clients this is lost and a host-only cookie
    also reaches subdomains of its host -- never another site.
    """
    jar = CookieJar(DefaultCookiePolicy(
        strict_ns_domain=DefaultCookiePolicy.DomainStrictNonDomain
        if strict_host_only else DefaultCookiePolicy.DomainLiberal))
    for c in browser_cookies or ():
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        domain = (c.get("domain") or "").strip()
        if not name or "value" not in c or not domain:
            continue
        dot = domain.startswith(".")
        jar.set_cookie(Cookie(
            0, name, str(c["value"]), None, False,
            domain, dot, dot,
            c.get("path") or "/", True,
            bool(c.get("secure")), None, False, None, None, {}))
    return jar


def cookie_header_for(browser_cookies, url: str) -> str:
    """The Cookie header a browser would send to `url` ("" for none): domain
    (host-only kept strict), path and secure all applied."""
    import urllib.request
    try:
        req = urllib.request.Request(url)
    except ValueError:
        return ""
    scoped_jar(browser_cookies, strict_host_only=True).add_cookie_header(req)
    return req.get_header("Cookie") or ""
