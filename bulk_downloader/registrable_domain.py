"""The registrable domain (eTLD+1) of a hostname, done once and correctly.

WHY THIS MODULE EXISTS. An AST census at v3.66.1013 found THIRTEEN functions in
this repository computing a registrable domain as `".".join(h.split(".")[-2:])`
-- under the names `_registrable`, `_etld1`, `get_registrable_domain`,
`_reg_domain`, `_extract_domain` and `_is_same_etld1`. All thirteen carry the
same defect: the rule is right for `members.vip4k.com` and wrong for every
multi-part public suffix.

IT IS NOT COSMETIC. Two of them are same-site predicates gating whether a URL
discovered in a listing or a search page gets FOLLOWED. Measured against the
shipped code before this module existed:

    same-site?  True   https://victim.co.uk/list  vs  https://attacker.co.uk/x
    same-site?  True   https://a.github.io/list   vs  https://b.github.io/x

Every GitHub Pages site was "the same site" as every other, and so was any pair
of `.co.uk` registrants. A same-site check that says yes to unrelated
registrants is a scope escape in a fetch decision.

WHY A CURATED SET RATHER THAN A LIBRARY. `tldextract`, `publicsuffix2` and
`publicsuffixlist` are all absent from this environment. Adding one would put a
new runtime dependency on the deploy box, into requirements.txt, and through the
dep-freshness gate, to be right about a few dozen suffixes that do not change
often. The set below is explicit and testable.

THE LIMIT, STATED RATHER THAN IMPLIED. This is not the full Public Suffix List
and cannot be. An unknown multi-part suffix degrades to the old last-two-labels
answer -- no worse than what every call site did before, and still not correct.
`is_known_suffix()` exists so a caller that needs to know can ask, and the
degradation is asserted by a test rather than left as a comment.
"""
from __future__ import annotations

from urllib.parse import urlparse


# Multi-part public suffixes: everything under these is a separate registrant,
# so the registrable domain takes one MORE label than the suffix.
#
# Curated, not exhaustive. Chosen as the suffixes most likely to appear in a
# capture corpus or a scraped listing: the ccTLD second-levels, the big
# user-content hosts (where the security consequence is sharpest -- every
# github.io page is a different owner), and the common commercial ones.
_MULTI_PART_SUFFIXES = frozenset({
    # United Kingdom
    "co.uk", "org.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk", "sch.uk",
    "ac.uk", "gov.uk", "nhs.uk", "police.uk", "mod.uk",
    # Australia / New Zealand
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "asn.au", "id.au",
    "co.nz", "net.nz", "org.nz", "govt.nz", "ac.nz", "school.nz",
    # Japan / Korea / China / Taiwan / Hong Kong / Singapore / India
    "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp", "ad.jp", "ed.jp", "gr.jp",
    "co.kr", "or.kr", "ne.kr", "go.kr", "re.kr", "pe.kr",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "com.tw", "net.tw", "org.tw", "idv.tw", "gov.tw", "edu.tw",
    "com.hk", "net.hk", "org.hk", "edu.hk", "gov.hk", "idv.hk",
    "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg", "per.sg",
    "co.in", "net.in", "org.in", "firm.in", "gen.in", "ind.in", "gov.in",
    # Europe / Americas / Africa
    "co.za", "org.za", "net.za", "web.za", "gov.za", "ac.za",
    "com.br", "net.br", "org.br", "gov.br", "edu.br", "art.br", "blog.br",
    "com.mx", "org.mx", "net.mx", "edu.mx", "gob.mx",
    "com.ar", "net.ar", "org.ar", "gob.ar", "edu.ar",
    "co.il", "org.il", "net.il", "ac.il", "gov.il",
    "com.tr", "net.tr", "org.tr", "gov.tr", "edu.tr",
    "com.ru", "net.ru", "org.ru", "pp.ru",
    "com.pl", "net.pl", "org.pl", "gov.pl", "edu.pl",
    "com.ua", "net.ua", "org.ua", "in.ua", "kiev.ua",
    "co.id", "web.id", "or.id", "ac.id", "go.id",
    "com.my", "net.my", "org.my", "edu.my", "gov.my",
    "com.ph", "net.ph", "org.ph", "gov.ph",
    "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn",
    "co.th", "in.th", "ac.th", "go.th", "or.th",
    "com.es", "org.es", "nom.es", "gob.es", "edu.es",
    "com.pt", "org.pt", "edu.pt", "gov.pt",
    "com.gr", "net.gr", "org.gr", "edu.gr", "gov.gr",
    # User-content hosts. The security case is sharpest here: two projects on
    # github.io are unrelated strangers, and the old rule called them one site.
    "github.io", "gitlab.io", "githubusercontent.com",
    "pages.dev", "workers.dev", "netlify.app", "vercel.app",
    "herokuapp.com", "azurewebsites.net", "cloudfront.net",
    "s3.amazonaws.com", "blogspot.com", "wordpress.com", "tumblr.com",
    "myshopify.com", "web.app", "firebaseapp.com", "appspot.com",
    "r2.dev", "b-cdn.net", "sourceforge.io", "readthedocs.io",
})


def is_known_suffix(suffix: str) -> bool:
    """Whether `suffix` is in this module's curated set.

    Exists so a caller can distinguish "correct" from "degraded" rather than
    being told a number and left to trust it.
    """
    return (suffix or "").strip().lower().lstrip(".") in _MULTI_PART_SUFFIXES


def is_public_suffix(host: str) -> bool:
    """Whether `host` is itself a public suffix with no registrant.

    `co.uk` is; `bbc.co.uk` is not. A caller that treats a bare public suffix as
    a registrable domain is how two strangers become one site.
    """
    h = _normalize(host)
    if not h:
        return False
    return h in _MULTI_PART_SUFFIXES or "." not in h


def _normalize(host) -> str:
    if not host or not isinstance(host, str):
        return ""
    h = host.strip().lower().rstrip(".")
    if "://" in h:                      # tolerate being handed a URL
        h = urlparse(h).hostname or ""
    if "@" in h:                        # or a userinfo-bearing authority
        h = h.rsplit("@", 1)[-1]
    if h.startswith("[") or ":" in h:   # IPv6 / host:port -- strip the port
        if not h.startswith("[") and h.count(":") == 1:
            h = h.split(":", 1)[0]
    return h.strip(".")


def registrable_domain(host) -> str:
    """The registrable domain (eTLD+1) of `host`, or "" when there is none.

    Never raises: every caller reaches this from `urlparse().hostname`, which
    yields None and worse on malformed input, and a predicate that raises is a
    predicate that fails open inside whatever `except:` encloses it.
    """
    h = _normalize(host)
    if not h or "." not in h:
        return h
    labels = h.split(".")
    # Longest known multi-part suffix wins: `s3.amazonaws.com` before
    # `amazonaws.com` would matter if both were listed, and checking longest
    # first means adding one later cannot change an existing answer.
    for n in range(min(len(labels) - 1, 4), 1, -1):
        candidate = ".".join(labels[-n:])
        if candidate in _MULTI_PART_SUFFIXES:
            return ".".join(labels[-(n + 1):])
    if h in _MULTI_PART_SUFFIXES:
        return h
    return ".".join(labels[-2:])


def same_site(url1, url2) -> bool:
    """Whether two URLs share a registrable domain.

    FAILS CLOSED. Anything unparseable, empty, or without a registrable domain
    is not a match -- this gates whether a discovered URL gets followed, and the
    safe answer to "I cannot tell" is no.
    """
    try:
        h1 = urlparse(url1 or "").hostname or ""
        h2 = urlparse(url2 or "").hostname or ""
    except Exception:
        return False
    d1, d2 = registrable_domain(h1), registrable_domain(h2)
    if not d1 or not d2 or "." not in d1:
        return False
    if is_public_suffix(d1) or is_public_suffix(d2):
        return False
    return d1 == d2
