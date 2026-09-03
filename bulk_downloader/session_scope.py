"""session_scope -- does the jar we are about to use cover the page we are
about to load?

WHY THIS EXISTS. On 2026-09-03 the campaign lane recorded
``TEMPLATE-LOGIN-WALL-SESSION-DOES-NOT-CARRY`` (HIGH) on brazzers: the
credential login reported ``OK -- 19 cookies``, the runner reported the job as
running normally, and the scene page rendered a login wall. The site's
configured ``login_host`` is ``site-ma.brazzers.com``; the scene lives on
``www.brazzers.com``. Those are SIBLING subdomains, so a session cookie the
login host issued host-only (no ``Domain`` attribute) is never sent to the
scene host -- Chromium is behaving exactly as RFC 6265 5.1.3 requires.

Nothing in BD measured that. ``login_impl.submit`` harvests the whole login
context (``pw_to_json(ctx.cookies())``) and reports success by cookie COUNT;
``runner_auth._check_cookies_or_relogin`` then asks only whether the jar is
expired -- ``if ei["expired"] <= 0 or ei["session"] != 0: return True`` -- a
question with no host in it. A jar of 19 live session cookies for
``site-ma.brazzers.com`` passes that predicate while covering nothing the
worker is about to load.

WHAT WAS MEASURED, AND WHAT WAS NOT. A round-trip probe against a local
fixture host through BD's real chain (``pw_to_json`` -> JSON -> disk shape ->
``normalize_stored_cookie`` -> ``add_cookies``) showed the carry itself is
CLEAN: a parent-domain cookie survives into a fresh context and is offered to
the sibling host; a host-only cookie survives and is correctly withheld.
Chromium reports an unspecified ``SameSite`` as ``Lax``, so
``normalize_stored_cookie``'s ``"None"`` default never fires on a harvested
cookie and nothing is dropped for want of ``Secure``. So this module does NOT
widen a scope: the browser is right and the jar is right. What was missing is
the DIAGNOSTIC -- BD could not say "this jar covers nothing on that host", and
so an operator read a login wall with no cause attached to it.

Scope of the claim. ``applicable_cookies`` answers one question -- which
cookies in a BD jar would be offered to a URL -- using RFC 6265 5.1.3
(domain-match), 5.1.4 (path-match) and the Secure attribute. It deliberately
does NOT decide whether a session is valid: a site may authenticate from
``localStorage``, a bearer token, or a persistent browser profile whose
cookies were never written to the flat jar. Zero applicable cookies is
therefore reported, never enforced.
"""
from __future__ import annotations

from urllib.parse import urlsplit

__all__ = [
    "host_of",
    "domain_matches",
    "path_matches",
    "applicable_cookies",
    "uncovered_host_diagnostic",
    "UNCOVERED_PREFIX",
]

# The named diagnostic. Kept as a module constant so the runner seam and the
# gate that asserts on it cannot drift apart into two spellings.
UNCOVERED_PREFIX = "session does not cover"


def host_of(url: str) -> str:
    """Lower-cased hostname of `url`, without port or trailing root dot.

    Returns "" when the URL carries no host, which callers must read as
    "unmeasurable" rather than "not covered"."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    host = (parts.hostname or "").strip().lower()
    return host.rstrip(".")


def domain_matches(cookie_domain: str, host: str) -> bool:
    """RFC 6265 5.1.3 domain-match, in Playwright's stored convention.

    Playwright distinguishes the two cookie kinds by a LEADING DOT, verified
    against Chromium on a local fixture host: a cookie set without a ``Domain``
    attribute comes back as ``login.example.test`` (host-only) and one set with
    ``Domain=.example.test`` comes back as ``.example.test`` (a domain cookie
    covering every subdomain). Treating the two alike is the whole defect, so
    the dot is load-bearing here and must not be normalised away.

    A domain cookie also covers its own bare domain (``.example.test`` is sent
    to ``example.test``); a host-only cookie covers exactly one host and does
    NOT cover its own subdomains."""
    d = (cookie_domain or "").strip().lower().rstrip(".")
    h = (host or "").strip().lower().rstrip(".")
    if not d or not h:
        return False
    if d.startswith("."):
        bare = d[1:]
        if not bare:
            return False
        return h == bare or h.endswith("." + bare)
    return h == d


def path_matches(cookie_path: str, request_path: str) -> bool:
    """RFC 6265 5.1.4 path-match.

    A cookie scoped to ``/en/members`` is not offered to ``/en/video/1``, and
    the prefix test alone would wrongly offer it to ``/en/membersonly``: the
    character after the prefix has to be a separator."""
    c = cookie_path or "/"
    r = request_path or "/"
    if not c.startswith("/"):
        c = "/" + c
    if not r.startswith("/"):
        r = "/" + r
    if c == r:
        return True
    if not r.startswith(c):
        return False
    if c.endswith("/"):
        return True
    return r[len(c):].startswith("/")


def applicable_cookies(cookies, url: str):
    """The subset of `cookies` a browser would offer to `url`.

    `cookies` is a BD in-memory jar (``cookies.normalize_stored_cookie``'s
    shape, which is also Playwright's ``add_cookies`` shape). Returns a list;
    an unparsable URL or a URL with no host returns [] and callers must not
    read that as evidence of anything -- see `uncovered_host_diagnostic`,
    which refuses to speak without a host."""
    host = host_of(url)
    if not host:
        return []
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return []
    scheme = (parts.scheme or "").lower()
    req_path = parts.path or "/"
    out = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        if not domain_matches(str(c.get("domain", "")), host):
            continue
        if not path_matches(str(c.get("path", "/") or "/"), req_path):
            continue
        # A Secure cookie is withheld from a plaintext request. Schemes other
        # than http are treated as secure-capable so a file:// or blob:// page
        # is not silently graded "uncovered" by this rule alone.
        if c.get("secure") and scheme == "http":
            continue
        out.append(c)
    return out


def _cookie_domain(c) -> str:
    return str(c.get("domain", "") or "").strip().lower().rstrip(".")


def uncovered_host_diagnostic(cookies, url: str, *, login_host: str = "") -> str:
    """The named diagnostic, or "" when there is nothing honest to say.

    Speaks ONLY when all three preconditions hold, so that it names the
    brazzers shape and cannot be confused with a site that never logged in:

      * the URL has a host (otherwise the question is unmeasurable);
      * the jar is NON-EMPTY (an empty jar is "no session captured", an
        already-visible state with its own handling, not a scoping fault);
      * ZERO of the LOGIN HOST's cookies (those scoped exactly to it; the
        whole jar when no login host is known) would be offered to that URL.

    It is a statement about the flat jar and says so, because the runner may
    also carry a persistent browser profile whose cookies were never written
    to the jar. That is exactly why the caller logs this and does not refuse.
    """
    host = host_of(url)
    if not host:
        return ""
    jar = [c for c in (cookies or []) if isinstance(c, dict)]
    if not jar:
        return ""
    lh = (login_host or "").strip().lower().rstrip(".")
    # The LOGIN HOST's cookies are the subject, not the jar total: one
    # unrelated applicable cookie (a parent-domain consent cookie, say) must
    # not silence the fact that every cookie the login minted stays home.
    # Measured by the shape lens on 2026-09-03: 19 login-host cookies + 1
    # `.example.test` cookie read as "covered".
    # "Belongs to the login host" means SCOPED to it (host-only for that
    # host, or a domain cookie on exactly that host) -- not merely offered to
    # it, or a parent-domain consent cookie would join the set and hide it.
    login_jar = [c for c in jar
                 if lh and _cookie_domain(c) in (lh, "." + lh)]
    if login_jar:
        if applicable_cookies(login_jar, url):
            return ""
    elif applicable_cookies(jar, url):
        return ""
    subject = login_jar or jar
    scoped = sorted({str(c.get("domain", "")) for c in subject
                     if c.get("domain")})
    where = ", ".join(scoped[:4]) or "(no domain recorded)"
    if len(scoped) > 4:
        where += f", +{len(scoped) - 4} more"
    tail = ""
    if lh and lh != host:
        tail = f"; login host is {lh}"
    if login_jar:
        return (f"{UNCOVERED_PREFIX} {host}: {len(login_jar)} of the "
                f"{len(jar)} cookie(s) in the jar belong to the login host "
                f"and 0 of them apply to this URL (login-host cookies are "
                f"scoped to {where}{tail}). The page can render as a login "
                f"wall even though login reported success.")
    return (f"{UNCOVERED_PREFIX} {host}: {len(jar)} cookie(s) in the jar, "
            f"0 apply to this URL (jar is scoped to {where}{tail}). "
            f"The page can render as a login wall even though login reported "
            f"success.")
