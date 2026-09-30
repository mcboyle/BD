"""dl95-pegasproductions-2b: on a login site the scene is the MEMBER rendition.

Measured on test2 (download-95/B6-B/p1/pegasproductions, auth ok): the scene
page's download pick was a file the LOGGED-OUT page links too -- the public
trailer -- and the job closed done under the scene's title. B3-B's cut refuses
a trailers/teasers DIRECTORY; a public-tier file outside one (``/videos/<slug>
-sd.mp4``, a page-media ``<video>`` source) still passed.

The test is a measurement, not a word list: the scene URL is fetched from the
page itself with ``credentials: 'omit'`` (the logged-out view, through the
browser's own network path and egress), and a pick the logged-out view also
links is public-tier only when a cookie-less HEAD of it is SERVED as a file
(2xx, not an HTML page) -- a member link a tour page merely shows (refused,
redirected to a join page, or unanswerable) stays the member file. Every "no
answer" is no verdict and changes nothing: an unreadable logged-out view, a
HEAD that throws (a cross-origin redirect without CORS), and a pick past the
probe limit are never public. Read-only: nothing on the site is clicked or
submitted.
"""
from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

# The URL-bearing attributes a candidate may carry its file in (detect's
# _WIDE_SCAN_URL_ATTRS), in the order a click would honour them.
CANDIDATE_URL_ATTRS = ("href", "data-href", "data-url", "data-download",
                       "data-signed-url-key", "data-src", "src")

LOGGED_OUT_URLS_JS = r"""async (u) => {
  let r;
  try {
    r = await fetch(u, {credentials: 'omit', redirect: 'follow', cache: 'no-store'});
  } catch (e) { return {error: 'fetch ' + String((e && e.name) || e)}; }
  if (!r.ok) return {error: 'HTTP ' + r.status};
  const ct = r.headers.get('content-type') || '';
  if (!/html/i.test(ct)) return {error: 'content-type ' + ct.slice(0, 40)};
  const html = (await r.text()).slice(0, 4000000);
  const out = [];
  const attrs = ['href', 'src', 'data-src', 'data-href', 'data-url', 'data-download',
                 'data-signed-url-key'];
  const doc = new DOMParser().parseFromString(html, 'text/html');
  for (const el of doc.querySelectorAll(attrs.map(a => '[' + a + ']').join(','))) {
    for (const a of attrs) { const v = el.getAttribute(a); if (v) out.push(v); }
    if (out.length > 20000) break;
  }
  const text = html.replace(/\\\//g, '/');
  const re = /["'(\s=]((?:https?:)?\/\/?[^\s"'<>()\\]+?\.(?:mp4|m4v|mov|webm|mkv|m3u8|mpd|avi|wmv|flv)(?:\?[^\s"'<>()\\]*)?)/gi;
  for (const m of text.matchAll(re)) { out.push(m[1]); if (out.length > 40000) break; }
  return {url: r.url || u, urls: out};
}"""

# A cookie-less HEAD of each linked pick. Only a positive answer -- 2xx and not
# an HTML page -- says the file is served to a logged-out visitor (public).
# Refused (401/403), an HTML page (login/join wall) and a HEAD that throws (a
# cross-origin redirect to a billing page without CORS) are all "not served",
# so the pick keeps its BASE fate: a probe failure never manufactures a hold.
PUBLIC_PROBE_JS = r"""async (urls) => {
  const served = [];
  for (const u of urls) {
    let r = null;
    try {
      r = await fetch(u, {method: 'HEAD', credentials: 'omit', redirect: 'follow',
                          cache: 'no-store'});
    } catch (e) {
      r = null;  // no answer: not served, so never public
    }
    if (!r) continue;
    const ct = r.headers.get('content-type') || '';
    if (r.ok && !/html/i.test(ct)) served.push(u);
  }
  return served;
}"""

# At most this many linked picks are probed per scene; a pick past it is not
# judged public (no verdict), so the limit can only fail open.
PROBE_LIMIT = 24


def is_login_site(config) -> bool:
    """A site that logs in (a login_url), as runner_auth._page_shows_logged_out asks.

    O1567 fx-auto-approve-loginfree: a site that declares auth_required=False
    has no members area even with an http login_url (bd3 spankbang), so there
    is no public tier to hold -- the logged-out page's file is the scene. The
    explicit bool wins, as in scene_crawler._site_is_public."""
    config = config or {}
    return (config.get("auth_required") is not False
            and str(config.get("login_url") or "").startswith("http"))


def normalize(value, base="") -> str:
    """Absolute http(s) URL, fragment dropped, scheme/host lower-cased; "" otherwise."""
    try:
        joined = urldefrag(urljoin(base or "", str(value or "").strip()))[0]
        p = urlsplit(joined)
    except (TypeError, ValueError):
        return ""
    if p.scheme.lower() not in ("http", "https") or not p.netloc:
        return ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def element_url(locator, base="") -> str:
    """The file URL a candidate element names, normalized; "" when it names none."""
    if locator is None:
        return ""
    page = normalize(base)
    for attr in CANDIDATE_URL_ATTRS:
        try:
            value = locator.get_attribute(attr)
        except Exception:  # noqa: BLE001 -- a detached element names nothing
            return ""
        url = normalize(value, base) if value else ""
        # A JS control's incidental ``href="#"`` (or a link back to the page
        # itself) names no file; the file is in a later attribute (data-url).
        if url and url != page:
            return url
    return ""


class LoggedOutView:
    """The scene page as a logged-out visitor sees it, read once on demand."""

    def __init__(self, page, scene_url):
        self.page, self.scene_url = page, scene_url
        self.linked = None
        self.why = ""
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            res = self.page.evaluate(LOGGED_OUT_URLS_JS, self.scene_url)
        except Exception as e:  # noqa: BLE001 -- no verdict, never a refusal
            self.why = f"evaluate {type(e).__name__}"
            return
        if not isinstance(res, dict) or not isinstance(res.get("urls"), list):
            self.why = str((res or {}).get("error") if isinstance(res, dict) else "no result")
            return
        base = str(res.get("url") or self.scene_url)
        self.linked = {u for u in (normalize(v, base) for v in res["urls"]) if u}

    def readable(self) -> bool:
        self._load()
        return self.linked is not None

    def public(self, urls) -> set:
        """The subset of ``urls`` (normalized) that are public-tier files."""
        if not self.readable():
            return set()
        linked = sorted({u for u in urls if u and u in self.linked})[:PROBE_LIMIT]
        if not linked:
            return set()
        try:
            served = self.page.evaluate(PUBLIC_PROBE_JS, linked)
        except Exception as e:  # noqa: BLE001 -- unprobed: no verdict, never public
            self.why = f"probe {type(e).__name__}"
            return set()
        if not isinstance(served, list):
            return set()
        return set(linked) & {normalize(u) for u in served}


def leaf(url) -> str:
    """The file name an operator recognises in a message."""
    return (urlsplit(url).path.rsplit("/", 1)[-1] or url)[:80]


HELD_MESSAGE = ("Only a public-tier file found: {leaf} is also linked on the "
                "logged-out page, and no member rendition was offered -- "
                "Approve to force")
