"""outbound_guard -- one judgment for a request-supplied URL before BD fetches it (BD-SEC-3 r3).

WHY.  The api-ssrf static audit (2026-10-13) found 26 routes whose URL reaches an
outbound fetch with no address check: the worker's browser navigation, plain
``urllib`` clients, raw sockets, and URLs handed to an external fetcher.  The
guarded seams that already exist (``ssrf_transport`` for httpx, ``urllib_ssrf``
for urllib) each need a caller to choose them; this module is the one place a
call site that has only a URL string asks "may BD open this?".

WHAT IS JUDGED.  Scheme (http/https unless the caller names others), a host,
a parseable port, then EVERY address the host resolves to -- a name with one
refused answer is refused, and a name that does not resolve is refused (fail
closed).  Two policies, the same two ``ssrf_transport`` has:

``LAN_OK``      -- an operator's own targets may live on the LAN: loopback,
                   RFC1918, ULA and CGNAT are admitted; link-local (169.254/16
                   with the cloud-metadata endpoint, fe80::/10), unspecified,
                   multicast and IPv4 reserved are refused, on the address and
                   on every IPv4 an IPv6 address embeds
                   (``ssrf_transport.never_admissible``).
``PUBLIC_ONLY`` -- public unicast only (``provider_resolve_impl._common._classify_ip``).

WHAT A CHECK DOES NOT DO.  ``check_url`` judges; it does not pin.  A fetcher
that resolves again afterwards (a browser, ffmpeg, a third-party server) keeps
a DNS-rebinding window and follows its own redirects.  Where BD itself opens
the socket use ``urlopen`` (resolve once, connect to the vetted literal, every
redirect hop judged and pinned again) or ``connect`` (raw TCP to a vetted
literal).  ``install_page_guard`` judges every request a Playwright page makes;
Playwright does not hand redirect hops to a route, so those are judged by the
browser-level backstop (``install_fetch_backstop``, measured r8).
"""

from __future__ import annotations

import base64
import ipaddress
import socket
import time
import types
import urllib.parse
import urllib.request
from typing import Callable, Optional, Tuple

from bulk_downloader.ssrf_transport import metadata_host, never_admissible
from bulk_downloader.urllib_ssrf import PinnedUrlOpener

LAN_OK = "lan-ok"
PUBLIC_ONLY = "public-only"
POLICIES = (LAN_OK, PUBLIC_ONLY)

_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}
# Schemes a browser page uses that open no connection BD could judge.
_PAGE_INERT_SCHEMES = frozenset(
    {"about", "data", "blob", "javascript", "chrome", "devtools", "chrome-extension"}
)
_PAGE_JUDGED_SCHEMES = ("http", "https", "ws", "wss")
_PAGE_CACHE_MAX = 512


class OutboundRefused(ValueError):
    """BD will not open ``url``.  ``reason`` is a short token + detail, safe to show."""

    def __init__(self, message: str, *, url: str, reason: str) -> None:
        super().__init__(message)
        self.url = url
        self.reason = reason


def _resolve(host: str, port: int):
    """The one resolution this module does (tests replace it with a fake resolver)."""
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def address_refusal(addr, host: str, policy: str = LAN_OK) -> Optional[str]:
    """Why ``addr`` (an ``ipaddress`` object ``host`` resolved to) is refused under ``policy``, else None."""
    if policy == LAN_OK:
        reason = never_admissible(addr)
        return None if reason is None else f"{reason} address ({host} -> {addr})"
    if policy == PUBLIC_ONLY:
        from bulk_downloader.provider_resolve_impl._common import _classify_ip

        ok, message = _classify_ip(addr, host)
        return (
            None if ok else (str(message) or f"non-public address ({host} -> {addr})")
        )
    raise ValueError(f"unknown outbound policy {policy!r}; expected one of {POLICIES}")


def check_host(
    host: str,
    port: int,
    policy: str = LAN_OK,
    *,
    url: str = "",
    unresolved_ok: bool = False,
) -> Tuple[str, ...]:
    """Every literal ``host`` resolves to, all admitted by ``policy``; else ``OutboundRefused``.
    ``unresolved_ok``: a name that does not resolve HERE returns ``()``, not a refusal (see ``check_url``)."""
    shown = url or host
    host = (host or "").strip().strip("[]")
    if not host:
        raise OutboundRefused(
            f"outbound refused: no host in {shown!r}", url=shown, reason="no-host"
        )
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        if metadata_host(host):  # before any lookup: refused where it does not resolve too
            raise OutboundRefused(
                f"outbound refused: cloud-metadata host {host!r}",
                url=shown,
                reason="cloud-metadata",
            ) from None
        try:
            infos = _resolve(host, port)
        except (OSError, UnicodeError) as exc:
            if unresolved_ok:
                return ()
            raise OutboundRefused(
                f"outbound refused: {host!r} does not resolve ({exc})",
                url=shown,
                reason="resolution-failed",
            ) from exc
        addresses = []
        for family, _type, _proto, _canon, sockaddr in infos:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            try:
                addresses.append(
                    ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])
                )
            except ValueError as exc:
                raise OutboundRefused(
                    f"outbound refused: non-IP DNS answer for {host!r}",
                    url=shown,
                    reason="resolution-failed",
                ) from exc
        if not addresses:
            if unresolved_ok:
                return ()
            raise OutboundRefused(
                f"outbound refused: {host!r} has no address",
                url=shown,
                reason="no-address",
            )
    for addr in addresses:
        why = address_refusal(addr, host, policy)
        if why is not None:
            raise OutboundRefused(f"outbound refused: {why}", url=shown, reason=why)
    return tuple(str(a) for a in addresses)


def check_url(
    url,
    policy: str = LAN_OK,
    *,
    schemes: Tuple[str, ...] = ("http", "https"),
    unresolved_ok: bool = False,
) -> Tuple[str, ...]:
    """Judge ``url``: scheme, host, port, every resolved address.  Returns the vetted literals.

    ``unresolved_ok`` is for a URL an EXTERNAL fetcher resolves again on this host (a browser,
    ffmpeg): behind a per-site proxy or VPN tunnel the lookup happens at the far end, so a name BD
    cannot resolve is left to that fetcher (``()`` is returned) while literals and every name BD
    can resolve are judged as always.  The default refuses an unresolvable name (fail closed):
    right wherever BD opens the socket itself, or hands the URL to a fetcher on another network."""
    text = url if isinstance(url, str) else ""
    try:
        parts = urllib.parse.urlsplit(text.strip())
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise OutboundRefused(
            f"outbound refused: invalid URL ({exc})", url=text, reason="invalid-url"
        ) from exc
    if scheme not in schemes:
        raise OutboundRefused(
            f"outbound refused: scheme {scheme or '(none)'!r} is not one of {'/'.join(schemes)}",
            url=text,
            reason="scheme",
        )
    if not host:
        raise OutboundRefused(
            f"outbound refused: no host in {text!r}", url=text, reason="no-host"
        )
    return check_host(
        host,
        port or _DEFAULT_PORTS.get(scheme, 80),
        policy,
        url=text,
        unresolved_ok=unresolved_ok,
    )


def url_refusal(
    url,
    policy: str = LAN_OK,
    *,
    schemes: Tuple[str, ...] = ("http", "https"),
    unresolved_ok: bool = False,
) -> Optional[str]:
    """``check_url`` as a predicate: the refusal text, or None when the URL is admitted."""
    try:
        check_url(url, policy, schemes=schemes, unresolved_ok=unresolved_ok)
    except OutboundRefused as exc:
        return str(exc)
    return None


def _allowed(policy: str) -> Callable:
    def address_allowed(addr, host):
        why = address_refusal(addr, host, policy)
        return why is None, (f"outbound refused: {why}" if why else "")

    return address_allowed


class _GatedPinnedUrlOpener(PinnedUrlOpener):
    """``PinnedUrlOpener`` that hands each hop's logical URL to the caller's ``hop_gate`` before pinning it."""

    def __init__(self, address_allowed, hop_gate: Callable[[str], None]):
        super().__init__(address_allowed)
        self._hop_gate = hop_gate

    def pin_request(self, request):
        self._hop_gate(getattr(request, "_pinned_logical_url", request.full_url))
        return super().pin_request(request)


def urlopen(request, *, policy: str = LAN_OK, timeout: float = 15,
            hop_gate: Optional[Callable[[str], None]] = None):
    """``urllib.request.urlopen`` for a request-supplied URL: resolved once, every answer judged, the socket
    opened to the vetted literal (Host and SNI keep the name), and every redirect hop judged and pinned the
    same way.  A refusal is ``urllib_ssrf.PinnedUrlRefused`` (a ``URLError``), raised before any socket opens.
    ``hop_gate(url)``, when given, sees every hop's URL first and refuses one by raising."""
    if isinstance(request, str):
        request = urllib.request.Request(request)
    opener = (PinnedUrlOpener(_allowed(policy)) if hop_gate is None
              else _GatedPinnedUrlOpener(_allowed(policy), hop_gate))
    return opener.open(request, timeout=timeout)


def connect(host: str, port: int, *, policy: str = LAN_OK, timeout: float = 10):
    """A TCP socket to the first vetted literal of ``host`` (the name is never resolved a second time)."""
    literals = check_host(host, port, policy)
    return socket.create_connection((literals[0], port), timeout=timeout)


def page_request_refusal(url, policy: str = LAN_OK) -> Optional[str]:
    """Why a browser page may not request ``url``, else None.  http(s)/ws(s) are judged as ``check_url`` does;
    about:/data:/blob: and the browser's own schemes open no connection; every other scheme (file:, ftp:,
    view-source:, ...) is refused.  A name that does not resolve HERE is left to the browser
    (``unresolved_ok``): literals and every name BD can resolve are judged."""
    text = str(url or "")
    try:
        scheme = (urllib.parse.urlsplit(text).scheme or "").lower()
    except ValueError:
        return "outbound refused: invalid URL"
    if scheme in _PAGE_INERT_SCHEMES:
        return None
    return url_refusal(text, policy, schemes=_PAGE_JUDGED_SCHEMES, unresolved_ok=True)


# ---- WebSockets -------------------------------------------------------------------------------------------------
# Playwright hands no ws/wss handshake to ``route``.  Its ``route_web_socket`` (used in r4/r5) needs a page binding
# that the default cloakbrowser backend does not expose: with any ws route registered there, the page's own
# ``new WebSocket(...)`` threw for ADMITTED sockets too (lens R36-B F1).  r6 uses one mechanism on both backends
# that needs no binding: an init script wraps the page's WebSocket constructor and asks THIS judge, synchronously,
# through an ordinary same-origin request that only the page route answers (it never reaches the network).
WS_VERDICT_PATH = "/__bd_ws_verdict__"
_WS_VERDICT_HEADERS = {
    "content-type": "text/plain",
    "cache-control": "no-store",
    "access-control-allow-origin": "*",
}
# The page script (cloakbrowser, and any browser whose backend the caller did not name).  It is a patch in
# the PAGE's own JS realm: a convenience that refuses early, not a boundary (limit PAGE-REALM below).
# When no judge answers it decides address LITERALS by itself from the same tables the Python judge reads
# (``ssrf_transport.literal_refusal_table``); a name is then admitted unless it is a metadata name.
_WS_GUARD_SCRIPT = """
(() => {
  const Native = window.WebSocket;
  if (!Native || Native.__bdOutboundGuard) return;
  const TABLE = __TABLE__;
  const EVERY_LITERAL = __EVERY_LITERAL__;
  const listed = (version, value) =>
    TABLE.ranges.some(([v, low, high]) => v === version && BigInt(low) <= value && value <= BigInt(high)) ||
    TABLE.metadata.some(([v, one]) => v === version && BigInt(one) === value);
  const ipv4 = (text) => {
    const m = /^(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})$/.exec(text);
    return m ? m.slice(1).reduce((n, part) => n * 256n + BigInt(part), 0n) : null;
  };
  const ipv6 = (text) => {
    const halves = text.split("::");
    if (halves.length > 2) return null;
    const head = halves[0] ? halves[0].split(":") : [];
    const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
    const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
    if (fill < 0) return null;
    const groups = head.concat(Array(fill).fill("0"), tail);
    if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
    return groups.reduce((n, g) => (n << 16n) | BigInt("0x" + g), 0n);
  };
  const carried = (value) => {  // every IPv4 an IPv6 literal carries: the forms _common unpacks
    const low = value & 0xffffffffn;
    const out = [];
    if (value >> 32n === 0xffffn) out.push(low);                                    // ::ffff:a.b.c.d
    if (value >> 112n === 0x2002n) out.push((value >> 80n) & 0xffffffffn);          // 6to4
    if (value >> 96n === 0x20010000n) out.push((value >> 64n) & 0xffffffffn, low ^ 0xffffffffn);  // Teredo
    if (value >> 32n === 0x64ff9b0000000000000000n) out.push(low);                  // 64:ff9b::/96
    if (value >> 32n === 0n && value > 1n) out.push(low);                           // ::a.b.c.d
    if (value >> 32n === 0xffff0000n) out.push(low);                                // ::ffff:0:a.b.c.d
    if (value >> 80n === 0x64ff9b0001n) {                                           // 64:ff9b:1::/48, 4 layouts
      const readings = [
        (((value >> 64n) & 0xffffn) << 16n) | ((value >> 40n) & 0xffffn),
        (((value >> 64n) & 0xffn) << 24n) | ((value >> 32n) & 0xffffffn),
        (value >> 24n) & 0xffffffffn,
        low,
      ].filter((reading) => reading !== 0n);
      out.push(...(readings.length ? readings : [low]));
    }
    return out;
  };
  const literalRefusal = (hostname) => {
    const host = String(hostname || "").toLowerCase().replace(/\\.$/, "");
    if (TABLE.names.includes(host)) return true;
    let version = 4;
    let value = ipv4(host);
    if (value === null && host.startsWith("[")) { version = 6; value = ipv6(host.slice(1, -1)); }
    if (value === null) return false;  // a name: only the judge can know where it leads
    if (EVERY_LITERAL) return true;
    return listed(version, value) || (version === 6 && carried(value).some((v4) => listed(4, v4)));
  };
  const web = (w) => /^https?:$/.test(w.location.protocol);
  const ask = (target) => {
    // A realm without an http origin (about:blank popup, srcdoc) asks through the realm that made it.  It
    // must: a synchronous request from a popup still being created never returns (r6 lens F3).
    let asker = window;
    try {
      for (let hop = 0; hop < 8 && !web(asker); hop++) {
        const next = asker.parent !== asker ? asker.parent : asker.opener;
        if (!next) break;
        asker = next;
      }
      if (!web(asker)) asker = null;
    } catch (e) { asker = null; }
    if (asker === null && window.parent === window) return "";
    const base = asker === null ? "http://bd-outbound-guard.invalid" : asker.location.origin;
    const xhr = new (asker || window).XMLHttpRequest();
    xhr.open("GET", base + "__PATH__?u=" + encodeURIComponent(target.href), false);
    xhr.send();
    return xhr.status === 200 ? String(xhr.responseText) : "";
  };
  const refused = (target) => {
    let verdict = "";
    try { verdict = ask(target); } catch (e) { verdict = ""; }
    if (verdict === "ok") return false;
    if (verdict === "no") return true;
    return literalRefusal(target.hostname);
  };
  function WebSocket(url, protocols) {
    if (!new.target) throw new TypeError("Failed to construct 'WebSocket': Please use the 'new' operator");
    let target = null;
    try { target = new URL(String(url), location.href); } catch (e) { target = null; }
    if (target && refused(target)) {
      throw new DOMException("Failed to construct 'WebSocket': outbound refused", "SecurityError");
    }
    const args = protocols === undefined ? [url] : [url, protocols];
    return Reflect.construct(Native, args, new.target === WebSocket ? Native : new.target);
  }
  WebSocket.prototype = Native.prototype;
  for (const k of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
    Object.defineProperty(WebSocket, k, { value: Native[k], enumerable: true });
  }
  Object.defineProperty(WebSocket, "__bdOutboundGuard", { value: true });
  Object.defineProperty(WebSocket, "__bdLiteralRefusal", { value: literalRefusal });
  window.WebSocket = WebSocket;
})();
"""
# The backend name BD's cloak wrapper reports for plain Playwright Chromium (``cloak.launch_browser``).
PLAYWRIGHT_BACKEND = "playwright"


def ws_guard_script(policy: str = LAN_OK) -> str:
    """The init script that makes a page ask the judge before it opens a WebSocket.  With no judge
    answering it refuses the address literals ``ssrf_transport.never_admissible`` refuses (same tables);
    under ``PUBLIC_ONLY`` it then refuses EVERY address literal (it cannot tell public from private)."""
    import json

    from bulk_downloader.ssrf_transport import literal_refusal_table

    return (
        _WS_GUARD_SCRIPT.replace("__TABLE__", json.dumps(literal_refusal_table(), sort_keys=True))
        .replace("__EVERY_LITERAL__", "true" if policy == PUBLIC_ONLY else "false")
        .replace("__PATH__", WS_VERDICT_PATH)
    )


def ws_verdict_target(url) -> Optional[str]:
    """The ws/wss URL a page asks about when ``url`` is a verdict request, else None."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return None
    if parts.path != WS_VERDICT_PATH:
        return None
    asked = urllib.parse.parse_qs(parts.query).get("u", [""])[0]
    return asked


def ws_refusal(url, policy: str = LAN_OK) -> Optional[str]:
    """Why a page may not open the WebSocket ``url``: only ws/wss, judged as any page request."""
    text = str(url or "")
    try:
        scheme = (urllib.parse.urlsplit(text).scheme or "").lower()
    except ValueError:
        return "outbound refused: invalid URL"
    if scheme not in ("ws", "wss"):
        return f"outbound refused: {scheme or '(none)'!r} is not a websocket scheme"
    return page_request_refusal(text, policy)


def page_guard(
    policy: str = LAN_OK, on_refuse: Optional[Callable[[str, str], None]] = None
) -> Callable:
    """A judge for Playwright route handlers: ``judge(route, request)`` aborts a refused request and returns
    True; returns False (route untouched) for an admitted one.  Verdicts are remembered per origin for the
    life of the judge, so a page's hundredth request to one host costs no lookup.  A judge that cannot
    decide (an unexpected error) refuses.

    The same judge answers the page's WebSocket verdict requests (``WS_VERDICT_PATH``): it fulfils them
    with ``ok`` / ``no`` and returns True -- they are never sent anywhere."""
    verdicts: dict = {}

    def decide(url: str, refusal: Callable) -> Optional[str]:
        try:
            parts = urllib.parse.urlsplit(url)
            key = ((parts.scheme or "").lower(), parts.netloc)
        except ValueError:
            key = None
        if key is not None and key in verdicts:
            return verdicts[key]
        try:
            why = refusal(url, policy)
        except Exception as exc:  # fail closed: an undecided request is not sent
            why = f"outbound refused: guard error {type(exc).__name__}"
        if key is not None:
            if len(verdicts) >= _PAGE_CACHE_MAX:
                verdicts.clear()
            verdicts[key] = why
        return why

    def told(url: str, why: str) -> None:
        if on_refuse is not None:
            try:
                on_refuse(url, why)
            except Exception:
                pass

    def judge(route, request) -> bool:
        url = str(getattr(request, "url", "") or "")
        asked = ws_verdict_target(url)
        if asked is not None:
            why = decide(asked, ws_refusal)
            route.fulfill(
                status=200, body="ok" if why is None else "no", headers=dict(_WS_VERDICT_HEADERS)
            )
            if why is not None:
                told(asked, why)
            return True
        why = decide(url, page_request_refusal)
        if why is None:
            return False
        route.abort()
        told(url, why)
        return True

    return judge


def pass_on(route) -> None:
    """Hand an admitted request to the next route handler (``route.fallback()``), not straight to the
    network: Playwright runs the LAST registered handler first and ``continue_()`` ends the chain, so a
    guard that continued would silently switch off every route registered before it (a mock, a session
    route, the context-level guard).  With no other handler ``fallback`` sends the request."""
    fallback = getattr(route, "fallback", None)
    if callable(fallback):
        fallback()
    else:
        route.continue_()


ATTACHED_BACKEND = "attached"  # login_impl/submit: Playwright attached to the operator's own challenge browser
_FETCH_BACKSTOP_ATTR = "_bd_outbound_fetch_backstop"


class _FetchRoute:
    """The route verbs ``page_guard``'s judge uses, answered on a request paused by CDP ``Fetch``."""

    def __init__(self, session, request_id):
        self._session, self._request_id, self.answered = session, request_id, False

    def _answer(self, method: str, **params) -> None:
        self.answered = True
        try:
            self._session.send(method, {"requestId": self._request_id, **params})
        except Exception:  # the request, its page or the browser is already gone: nothing was sent
            pass

    def abort(self, *args) -> None:
        self._answer("Fetch.failRequest", errorReason="BlockedByClient")

    def fulfill(self, status=200, body="", headers=None) -> None:
        raw = body.encode() if isinstance(body, str) else bytes(body)
        self._answer(
            "Fetch.fulfillRequest",
            responseCode=status,
            responseHeaders=[{"name": str(k), "value": str(v)} for k, v in (headers or {}).items()],
            body=base64.b64encode(raw).decode("ascii"),
        )


def install_fetch_backstop(browser, policy: str = LAN_OK) -> bool:
    """Judge every HTTP request of ``browser`` (all its contexts and pages) once more, below Playwright, on a
    browser-level CDP session: ``Fetch`` pauses each request after Playwright's own interception has let it
    go, and the same judge as the page route answers it (refused -> failed, a WebSocket verdict request ->
    answered, admitted -> continued).  Needed because Playwright never hands an about:blank popup's request
    to any route: it pairs the request's two protocol events per page and they arrive on different pages, so
    the request is held while the opener lives and sent unjudged when the opener closes (r7 REFUTE F1).  A
    per-page session attached at the context's "page" event is too late for a popup opened by a popup's own
    load script (measured r8), so this is one session per browser.  One backstop per browser, memoized on
    it; a later install with a stricter policy makes it stricter.  Returns False when the browser has no
    browser-level CDP session (not a Chromium, or not a real browser)."""
    open_session = getattr(browser, "new_browser_cdp_session", None)
    if not callable(open_session):
        return False
    state = getattr(browser, _FETCH_BACKSTOP_ATTR, None)
    if isinstance(state, dict):
        if policy == PUBLIC_ONLY and state["policy"] != PUBLIC_ONLY:  # PUBLIC_ONLY refuses all LAN_OK refuses
            state["policy"], state["judge"] = policy, page_guard(policy)
        return True
    session = open_session()
    state = {"policy": policy, "judge": page_guard(policy)}

    def paused(event) -> None:
        route = _FetchRoute(session, event.get("requestId"))
        try:
            url = str((event.get("request") or {}).get("url") or "")
            answered = state["judge"](route, types.SimpleNamespace(url=url))
        except Exception:  # fail closed: an undecided request is not sent
            answered = False
            route.abort()
        if not answered and not route.answered:
            route._answer("Fetch.continueRequest")

    session.on("Fetch.requestPaused", paused)
    session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
    setattr(browser, _FETCH_BACKSTOP_ATTR, state)
    return True


def install_page_guard(
    page,
    policy: str = LAN_OK,
    on_refuse: Optional[Callable[[str, str], None]] = None,
    backend: Optional[str] = None,
) -> None:
    """Judge every request ``page`` (a Playwright page or browser context) makes: navigations, frames,
    subresources, fetch/XHR.  An admitted request goes on to the handlers registered before this one
    (``pass_on``).  HTTP is judged at the route, below the page.

    Two things a page-level route does not see are covered here as well:
    POPUPS -- a page's route does not apply to a page it opens (``window.open``, ``target=_blank``), so
    the same guard is installed on the page's browser context too; and WEBSOCKETS, by one of two
    mechanisms (PM 23:45Z, option B; measured r7: neither backend can pause a ws handshake below the page):
    ``backend == PLAYWRIGHT_BACKEND`` -- Playwright's ``route_web_socket``: a refused socket is never
    connected to its server; any other backend (cloakbrowser: its Chromium lacks the binding
    ``route_web_socket`` needs and every WebSocket constructor would throw; or a caller that did not
    name its backend) -- the init script ``ws_guard_script``, whose verdict requests the route answers.
    BELOW THE ROUTE -- the page's browser gets the browser-level ``install_fetch_backstop`` (same judge):
    an about:blank popup's request (never handed to a route), a redirect hop, a cloakbrowser sandboxed
    srcdoc frame's request (its own process, which the route reaches late) are judged there.  It judges
    every context of that browser, guarded or not.

    NAMED LIMITS (operator-visible; they stand until the judging launch-proxy row lands):
    PAGE-REALM         BOTH WebSocket mechanisms live in the page's own JS realm (Playwright's is an
                       injected mock as well).  They stop a page that is not trying to get past them; a
                       HOSTILE page can bypass them (a saved or re-derived constructor, replaced
                       intrinsics, a realm the script has not reached) and then opens a WebSocket to ANY
                       address, refused ones included.  HTTP requests are not affected by this limit.
    WORKER-WEBSOCKET   a WebSocket opened inside a dedicated Worker or a SharedWorker (and a SharedWorker's
                       fetch) is outside the route and both mechanisms: it is NOT judged.  Service workers
                       are blocked on the login / verify / macro / takeover browsers only.
    WS-VERDICT-CHANNEL (page script only) when the page cannot reach the judge (a site CSP without
                       connect-src 'self', a page route registered later that continues everything, a
                       popup with no reachable http opener) the script judges address LITERALS itself, from
                       the same tables as the Python judge; a NAME that resolves to a refused address is
                       then NOT caught on a WebSocket (metadata names are).  Under ``PUBLIC_ONLY`` it
                       refuses every address literal in that state.
    REFUSED-SOCKET-SHAPE under ``route_web_socket`` a refused socket does not throw: the page holds a
                       socket that never opens.
    PAGE-REALM-OOPIF   (cloakbrowser) a sandboxed srcdoc frame is its own out-of-process target; the page
                       script reaches it late or not at all, so its WebSocket is NOT judged (measured r8: 3 of
                       4 runs reached; Chromium 153: 0).  Its HTTP is judged by the backstop.  PM 04:50Z ruling
                       B (operator O3099): fixed by the judging launch-proxy row, not by turning off
                       IsolateSandboxedIframes.
    POPUP-HOLD         an about:blank popup's request is held while its opener lives (the popup's page sees
                       it pending) and judged by the backstop when the opener closes; an admitted one is
                       sent then, not before.
    ATTACHED-BROWSER   ``backend == ATTACHED_BACKEND`` (the operator's own challenge browser) gets no
                       backstop: browser-wide it would also hold the targets outside the attached context
                       whenever BD is not pumping Playwright.  Its pages keep the route only, so POPUP-HOLD's
                       release there is still UNJUDGED (r7 F1 stands on this one backend)."""
    judge = page_guard(policy, on_refuse)

    def guard(route, request):
        if not judge(route, request):
            pass_on(route)

    def ws_refused(url) -> bool:
        try:
            return ws_refusal(str(url), policy) is not None
        except Exception:
            return True  # fail closed: an undecided socket is not opened

    def ws_unconnected(ws) -> None:
        # A routed socket reaches its server only through ``connect_to_server()``.  Not calling it IS the
        # refusal.  (``ws.close()`` from inside this handler deadlocks the sync API while the page is
        # inside evaluate(): measured, r4.)
        if on_refuse is not None:
            try:
                on_refuse(str(getattr(ws, "url", "") or ""), "outbound refused: websocket to a refused address")
            except Exception:
                pass

    script = ws_guard_script(policy)
    targets = [page]
    context = getattr(page, "context", None)
    if context is not None and context is not page and callable(getattr(context, "route", None)):
        targets.append(context)
    for target in targets:
        target.route("**/*", guard)
        route_web_socket = getattr(target, "route_web_socket", None)
        add_init_script = getattr(target, "add_init_script", None)
        if backend == PLAYWRIGHT_BACKEND and callable(route_web_socket):
            route_web_socket(ws_refused, ws_unconnected)
        elif callable(add_init_script):
            add_init_script(script)
    if backend != ATTACHED_BACKEND:
        holder = context if context is not None and context is not page else page
        install_fetch_backstop(getattr(holder, "browser", None), policy)


def idle_in_playwright(target, seconds: float) -> None:
    """Idle for ``seconds`` INSIDE a Playwright call on ``target`` (a browser context).  BD-SEC-3 r10
    (r9 lens A7 F1, the r8 F1 class): sync Playwright runs the guard's route and the backstop's CDP Fetch
    handlers only while the owner thread is in a Playwright call, so a session that waits on its command queue
    holds every request its pages make until a command arrives.  A human-operated session's idle tick waits
    here instead.  Waiting on the CONTEXT's close event keeps working after the operator closes the first tab;
    when it cannot be waited on (closed, or not a Playwright object) the rest is slept."""
    t0 = time.monotonic()
    try:
        target.wait_for_event("close", timeout=seconds * 1000)
    except Exception:
        pass  # the timeout is the normal exit
    rest = seconds - (time.monotonic() - t0)
    if rest > 0:
        time.sleep(rest)
