"""BD-SEC-2 ROW 2 (withhold): what a client may be shown of a site config.

Replaces text scrubbing with withholding decided by the FIELD, not by a regex
over the value:

* Notifier / credential URL fields (webhook_urls, notify_apprise_urls, ...)
  are never serialized. Every egress carries ``{configured, scheme, masked}``
  built from the structured field: the scheme is the stored value's own
  leading ``name:`` (letters/digits only, so it cannot carry a secret).
* URL fields (``url``, ``*_url``, ``*_urls``) are served only when the value
  is a plain routing URL: http(s), a bare host, a path with no ``:`` / ``%``
  / ``@`` / ``=`` / ``;``, allow-listed query names with plain values, a plain fragment.
  Anything else (userinfo, a percent escape at any level, a second scheme in
  the path or fragment, a token-shaped query) is served as ``<scheme>://***``.
* Free-text fields (notes, labels, errors) replace every whitespace-delimited
  URL-like token with PLACEHOLDER, in dict keys as well as values. Nothing is
  decoded; one pass, linear time.
* Every other value is walked to any depth (dicts, keys included, and lists),
  and the rules above apply by the key at each level. A string under any other
  key keeps each token except a URL-shaped one (``://``, ``user:pw@host``,
  ``www.``, ``path?name=value``) that is not a plain routing URL; that token
  is served as ``<scheme>://***``. So a proxy with userinfo, an endpoint or a
  search pattern with a token query, a nested subscription / login URL and a
  notifier URL inside a pipeline command are all withheld.

Exports carry a withheld MARKER (``{"withheld": true, ...}``) for every
top-level field whose served form differs from the stored value.
restore_withheld() is the import / save side: a marker keeps the value
already stored for the same site and field; with no stored value the field
is left blank (``""``, ``[]`` or ``{}`` by the marker's ``kind``; re-enter
after import) or takes the marker's placeholder text (free-text fields). A
served form sent back unchanged (a PUT of what a GET showed, at any depth)
keeps the stored value too.
"""
from __future__ import annotations

import re

PLACEHOLDER = "[url withheld]"
MASK = "***"
# Shown for a secret-named field that holds a value (the existing convention:
# GET /api/global_config already masks ai_api_key this way and its POST treats
# the sentinel as "no change").
CONFIGURED = "<configured>"

CREDENTIAL_URL_FIELDS = frozenset({
    "webhook_urls", "webhook_url", "notify_apprise_urls", "notify_apprise_url",
})
_CREDENTIAL_URL_SUFFIXES = ("webhook_url", "webhook_urls", "apprise_url", "apprise_urls")

FREE_TEXT_FIELDS = frozenset({
    "notes", "note", "description", "label", "labels", "comment", "comments",
    "last_error", "error", "login_status", "message", "status_message", "reason",
})

# Query names a plain routing URL may carry. Anything else (token, sig, k, t,
# pw, ...) makes the URL withheld: a name allow-list, never a secret deny-list.
PLAIN_QUERY_NAMES = frozenset({
    "page", "p", "sort", "order", "orderby", "q", "query", "search", "tag",
    "tags", "category", "cat", "type", "view", "lang", "limit", "offset",
    "per_page", "year", "month", "filter", "id", "start", "size",
})
# Notifier hosts: the host or any subdomain of it (www.discord.com) is never
# a plain routing URL. A path segment naming a hook (/hooks/, /webhook/) is
# not plain on any host either; a token-in-path webhook on a host that is in
# neither list is a design limit (no host list can be complete).
_NOTIFIER_HOSTS = frozenset({
    "api.telegram.org", "hooks.slack.com", "discord.com", "discordapp.com",
    "outlook.office.com", "webhook.office.com", "maker.ifttt.com",
    "api.pushover.net", "ntfy.sh", "hooks.zapier.com", "api.pushbullet.com",
    "events.pagerduty.com", "chat.googleapis.com",
})
_HOOK_SEGMENTS = frozenset({"hook", "hooks", "webhook", "webhooks"})

_PLAIN_URL = re.compile(
    r"(https?)://([A-Za-z0-9.-]{1,253})(?::[0-9]{1,5})?"
    r"(?:/[A-Za-z0-9._~!$'()*+,/{}-]*)?"
    r"(?:\?([A-Za-z0-9._~&=+,{}-]*))?"
    r"(?:#[A-Za-z0-9._~!$'()*+,/-]*)?")
_PLAIN_URL_MAX = 2048
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{0,19}(?=:)")
_TOKEN = re.compile(r"\S+")
_URLISH_CHARS = frozenset("/\\%@?#&=")
_SCHEME_COLON = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S")
_ITEM_SPLIT = re.compile(r"[\s,]+")
_STRUCT_HINTS = ("://", "@", "?")
# A percent-encoded ``:``, ``/``, ``@``, ``?`` or ``%`` (any case): an encoded
# URL inside a structural string (``u=https%3A%2F%2Fhost/...``).
_PCT_URL = re.compile(r"%(?:3[aAfF]|2[fF5]|40)")
# Scheme-less userinfo (deny-by-default: the host is never validated, a host
# shape a pattern did not foresee must not fail open). A run of two or more
# ``/`` or ``\`` starts a network-path authority, which ends at the next
# ``/ ? # \``; a bare token's authority starts at the token. A bare token
# needs a ``:port`` after the ``@`` (``a@b.com`` is an e-mail address).
_NET_PATH = re.compile(r"[/\\]{2,}")
_AUTHORITY_END = re.compile(r"[/?#\\]")
_BARE_PORT = re.compile(r"\S*:[0-9]+")
_XPATH_FN = re.compile(r"[A-Za-z_][\w.-]*\(")
_XPATH_ATTR = re.compile(r"[A-Za-z_][\w.-]*[),]")


def is_credential_url_field(key) -> bool:
    return isinstance(key, str) and (
        key in CREDENTIAL_URL_FIELDS or key.endswith(_CREDENTIAL_URL_SUFFIXES))


def is_url_field(key) -> bool:
    return isinstance(key, str) and not is_credential_url_field(key) and (
        key == "url" or key.endswith("_url") or key.endswith("_urls"))


def is_secret_field(key) -> bool:
    """The shared config-domain secret-name predicate (site_editor SoT)."""
    from .site_editor import is_secret_config_key  # lazy: no static import edge
    return is_secret_config_key(key)


def secret_value(value):
    """A secret-named field as a client may see it: presence only."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and _already_masked(value):
        return value
    return CONFIGURED if value else value


# The whole value is one of the masks the code base writes, or a vault
# reference (@cred:<id>, no secret in it). A value merely CONTAINING such a
# word is a value, not a mask.
_KNOWN_MASK = re.compile(
    r"\*+|<configured>|<redacted>|<scrubbed>|\[redacted\]|\**redacted\**"
    r"|@cred:[A-Za-z0-9_.:-]{1,128}", re.IGNORECASE)


def _already_masked(value: str) -> bool:
    """An upstream mask or a vault reference (fullmatch, see _KNOWN_MASK)."""
    v = value.strip()
    return v == "" or _KNOWN_MASK.fullmatch(v) is not None


def withholds(key) -> bool:
    """True iff site_view / export_view decide this field's served form."""
    return is_credential_url_field(key) or is_url_field(key) or key in FREE_TEXT_FIELDS


def _scheme(value: str) -> str:
    m = _SCHEME.match(value.strip())
    return m.group(0).lower() if m else ""


def _credential_items(value) -> list:
    """The URLs a credential field holds (a list, or one string split on
    whitespace and commas)."""
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    if isinstance(value, str):
        return [x for x in _ITEM_SPLIT.split(value) if x]
    return [str(value)] if value else []


def credential_url_view(value) -> dict:
    """{configured, scheme, masked} for a notifier/credential URL field. A
    value that already is this view (a frame viewed upstream) is returned
    as it is."""
    if _is_credential_view(value):
        return value
    items = _credential_items(value)
    scheme = _scheme(items[0]) if items else ""
    masked = (f"{scheme}://{MASK}" if scheme else MASK) if items else ""
    return {"configured": bool(items), "scheme": scheme, "masked": masked,
            "count": len(items), "withheld": True}


_VIEW_SCHEME = re.compile(r"(?:[a-z][a-z0-9+.-]{0,19})?")
_VIEW_MASKED = re.compile(r"(?:(?:[a-z][a-z0-9+.-]{0,19}://)?\*\*\*)?")


def _is_credential_view(value) -> bool:
    """A credential_url_view() result: a marker whose scheme and masked form
    carry no secret (a dict merely shaped like one is viewed afresh)."""
    if not is_withheld_marker(value):
        return False
    scheme, masked = value.get("scheme", ""), value.get("masked", "")
    return (isinstance(scheme, str) and isinstance(masked, str)
            and _VIEW_SCHEME.fullmatch(scheme) is not None
            and _VIEW_MASKED.fullmatch(masked) is not None)


def is_plain_url(value) -> bool:
    """True iff ``value`` is a plain routing URL (see module docstring)."""
    if not isinstance(value, str) or len(value) > _PLAIN_URL_MAX:
        return False
    m = _PLAIN_URL.fullmatch(value)
    if not m or _notifier_host(m.group(2)):
        return False
    path = value[m.end(2):].partition("?")[0].partition("#")[0]
    if any(seg.lower() in _HOOK_SEGMENTS for seg in path.split("/")):
        return False
    query = m.group(3)
    if query:
        for part in query.split("&"):
            name, _, _ = part.partition("=")
            if name.lower() not in PLAIN_QUERY_NAMES:
                return False
    return True


def _notifier_host(host: str) -> bool:
    h = host.lower().rstrip(".")
    while h:
        if h in _NOTIFIER_HOSTS:
            return True
        h = h.partition(".")[2]
    return False


def masked_url(value) -> str:
    """The served form of a withheld URL value: ``<scheme>://***`` for http(s)."""
    scheme = _scheme(value) if isinstance(value, str) else ""
    return f"{scheme}://{MASK}" if scheme in ("http", "https") else MASK


def url_value(value):
    """A URL field as a client may see it (strings, lists, nested dicts)."""
    if isinstance(value, str):
        return value if (not value or is_plain_url(value)) else masked_url(value)
    if isinstance(value, (list, tuple)):
        return [url_value(v) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        return _view_dict(value)
    return MASK


def _token(m) -> str:
    t = m.group(0)
    if len(t) <= 1:
        return t
    if _URLISH_CHARS.isdisjoint(t) and (not _SCHEME_COLON.match(t)) and ("www." not in t.lower()):
        return t
    return PLACEHOLDER


# BH1-64: a credential after ``Authorization:`` / ``Authorization=`` (the rest
# of that header value, fail closed: Basic, Digest and AWS forms hold several
# tokens), ``Bearer`` (whitespace and/or ``:`` / ``=``: the separator is never
# the credential) or ``token=`` (also ``access_token = X``) is replaced by MASK,
# which the per-token pass cannot do: it never sees the trigger token. Every
# token is still judged WHOLE as that pass judges it, so the mask never serves
# a part of a token the pass withholds: a trigger glued inside a withheld token
# leaves that token PLACEHOLDER, a withheld credential token is all MASK. Any
# case; one pass, linear time.
# BH1-71: the trigger may be a JSON key (``"access_token": "X"``, ``"bearer":
# "X"``), ``token:`` and ``X-Api-Key:`` are triggers too, the separator may be
# a fullwidth / ratio / small colon or fullwidth / small equals, a dash or em
# dash between whitespace, or ``+`` after Bearer, and the whitespace around it
# may be NBSP, a newline, or a zero-width / BOM / soft-hyphen code point (not
# ``\s`` in Python ``re``). A dash is a separator after a header name, never
# after the prose word ``token`` (``the token - it expired`` stays prose).
# Each branch keeps exactly one group (lastindex).
_WS = r"[\s\u00ad\u115f\u1160\u180e\u200b-\u200f\u2060-\u2064\u2800\u3164\ufeff\uffa0]"
_SEP = r"[:=\uff1a\u2236\ufe55\ua789\uff1d\ufe66]"
_DASH = r"[\-\u2010-\u2015]"
_AUTH_HEAD = rf"authorization[\"']?{_WS}*(?:{_SEP}|{_WS}{_DASH}+){_WS}*"
_AUTH = rf"{_AUTH_HEAD}(\S[^\r\n]*)"


def _one_token(cred: str) -> str:
    return (
        rf"bearer(?:[\"']?{_WS}*{_SEP}(?:{_WS}|{_SEP}|>)*|{_WS}+{_DASH}+{_WS}*|{_WS}+|\+)({cred})"
        rf"|token[\"']?{_WS}*{_SEP}(?:{_WS}|{_SEP}|>)*({cred})"
        rf"|(?:x-)?api[-_]?key[\"']?{_WS}*(?:{_SEP}(?:{_WS}|{_SEP}|>)*|{_WS}{_DASH}+{_WS}*)({cred})")


# The lookahead names the branches' first two letters so ``re`` skips every
# other position at C speed (text without a trigger scans faster than at BASE).
_CREDENTIAL_TEXT = re.compile(rf"(?=[abtx][uepo-])(?:{_AUTH}|{_one_token(r'\S+')})", re.IGNORECASE)
# BH1-71 r2: the captured credential may itself hold a trigger (``token:
# Bearer X``, ``"access_token": "Bearer X"``, ``token: Authorization: Basic
# X``, ``token: plainBearer X``): the mask runs on through every trigger that
# starts inside it to that trigger's own credential, so a trigger never
# shields the value after it. The nested scan looks for a trigger word only
# inside the credential masked so far, matches there and captures one
# character (the credential's start: a token credential then runs to the end
# of its token, an Authorization credential to the end of its line). BH1-71
# r3: inside an Authorization value a nested Authorization trigger counts too
# when its credential starts past the end of the line (``Authorization: x
# authorization:`` + newline + X); one on the same line is already masked, so
# the rest of a line is read once and the scan stays one forward pass.
_TRIGGER_WORD = re.compile(r"authorization|bearer|token|(?:x-)?api", re.IGNORECASE)
_NESTED = re.compile(rf"{_AUTH_HEAD}(\S)|{_one_token(r'\S')}", re.IGNORECASE)
_LINE_REST = re.compile(r"[^\r\n]*")


def _token_start(value: str, i: int, lo: int) -> int:
    while i > lo and not value[i - 1].isspace():
        i -= 1
    return i


def _withheld(value: str, i: int) -> bool:
    return _token(_TOKEN.match(value, i)) == PLACEHOLDER


def _credential_end(value: str, m) -> int:
    """End of m's credential, carried through every nested trigger."""
    end, scan, in_auth = m.end(), m.start(m.lastindex), m.lastindex == 1
    while w := _TRIGGER_WORD.search(value, scan, end):
        n = _NESTED.match(value, w.start())
        if n is None:
            scan = w.start() + 1
            continue
        scan = n.start(n.lastindex)
        if n.lastindex == 1:
            if not in_auth or scan >= end:
                end, in_auth = max(end, _LINE_REST.match(value, scan).end()), True
        elif scan >= end:
            end, in_auth = _TOKEN.match(value, scan).end(), False
    return end


def _free_text(value: str, lo: int = 0) -> str:
    """value[lo:] withheld; value[:lo] is served context (a kept segment) that
    is scanned for a trigger but never written: a credential a kept segment's
    trailing trigger introduces (``token=`` + ``X``) is still MASK (BH1-71)."""
    out, pos = [], lo
    m = _CREDENTIAL_TEXT.search(value)
    while m is not None:
        end = _credential_end(value, m)
        if end <= lo:
            m = _CREDENTIAL_TEXT.search(value, end)
            continue
        if m.start() < lo:
            cred = max(lo, m.start(m.lastindex))
            while cred < end and value[cred].isspace():
                cred += 1
            out.append(_TOKEN.sub(_token, value[lo:cred]) + MASK)
            pos, m = end, _CREDENTIAL_TEXT.search(value, end)
            continue
        start, cred = _token_start(value, m.start(), pos), m.start(m.lastindex)
        held = _token_start(value, cred, start)
        out.append(_TOKEN.sub(_token, value[pos:start]))
        if held == start and _withheld(value, start):
            rest = value[_TOKEN.match(value, start).end():end]
            body = rest.lstrip()
            out.append(PLACEHOLDER + (rest[:len(rest) - len(body)] + MASK if body else rest))
        elif held == start:
            out.append(value[start:cred] + MASK)
        else:
            out.append(_TOKEN.sub(_token, value[start:held]))
            out.append(MASK if _withheld(value, held) else value[held:cred] + MASK)
        pos, m = end, _CREDENTIAL_TEXT.search(value, end)
    out.append(_TOKEN.sub(_token, value[pos:]))
    return "".join(out)


def _text_key(key):
    return _free_text(key) if isinstance(key, str) and key else key


def withhold_text(value):
    """Free text with every credential after Authorization / Bearer / token=
    replaced by MASK and every URL-like token by PLACEHOLDER (linear), in dict
    keys as well as values."""
    if isinstance(value, str):
        return _free_text(value) if value else value
    if isinstance(value, (list, tuple)):
        return [withhold_text(v) for v in value]
    if isinstance(value, dict):
        return _unique_keys((_text_key(k), withhold_text(v)) for k, v in value.items())
    return value


def _unique_keys(pairs) -> dict:
    """A dict from (key, value) pairs; two keys withheld to the same text both
    stay (the later one gets a ``#n`` suffix) instead of one overwriting."""
    out = {}
    for k, v in pairs:
        if k in out:
            n = 2
            while f"{k} #{n}" in out:
                n += 1
            k = f"{k} #{n}"
        out[k] = v
    return out


def _urlish_token(t: str) -> bool:
    """A URL-shaped token: a ``scheme://``, ``user:pw@host`` userinfo, a
    ``www.`` host, a ``path?name=value`` query or a percent-encoded URL
    character (_PCT_URL). Linear scans only."""
    if "://" in t or t[:4].lower() == "www." or _PCT_URL.search(t):
        return True
    at = t.find("@")
    if at > 0 and ":" in t[:at] and "/" not in t[:at]:
        return True
    if "@" in t:
        if any(_userinfo_at(_authority(t, m.end())) > 0 for m in _NET_PATH.finditer(t)):
            return True
        bare = _authority(t, 0)
        at = _userinfo_at(bare)
        if at > 0 and _BARE_PORT.fullmatch(bare, at + 1):
            return True
    q = t.find("?")
    return q > 0 and "/" in t[:q] and "=" in t[q:]


def _authority(t: str, start: int) -> str:
    end = _AUTHORITY_END.search(t, start)
    return t[start:end.start()] if end else t[start:]


def _xpath_step(authority: str, at: int) -> bool:
    """The ``@`` at ``at`` is an XPath/CSS attribute step, not userinfo: the
    text before it holds a ``[`` (not RFC 3986 userinfo; urllib refuses it), or
    the whole text before it is a function name and ``(`` (no ``:``, so never
    ``user:pw(``) and an attribute name closes with ``)`` or ``,`` right after
    it (``//a(@x)``). Anything else after the ``@`` is a host: redact."""
    head = authority[:at]
    return "[" in head or bool(
        _XPATH_FN.fullmatch(head) and _XPATH_ATTR.match(authority, at + 1))


def _userinfo_at(authority: str) -> int:
    """Index of the first ``@`` in ``authority`` that ends a userinfo, else -1."""
    at = authority.find("@")
    bracket = authority.find("[")
    while at >= 0:
        if 0 <= bracket < at:
            return -1  # this and every later ``@`` has a ``[`` before it (linear)
        if at > 0 and not _xpath_step(authority, at):
            return at
        at = authority.find("@", at + 1)
    return -1


def is_proxy_field(key) -> bool:
    """A proxy-named key (proxy, http_proxy, socks_proxy, proxy_pool, socks5,
    ...): its value never holds an e-mail address, so any ``@`` is userinfo."""
    k = key.lower() if isinstance(key, str) else ""
    return "proxy" in k or k.startswith("socks")


def _proxy_token(m) -> str:
    t = m.group(0)
    return masked_url(t) if t.find("@") > 0 else _struct_token(m)


def proxy_text(value: str) -> str:
    """A string under a proxy-named key: struct_text, and every token with
    an ``@`` after its first character withheld (``@cred:<id>`` is kept)."""
    return _TOKEN.sub(_proxy_token, value) if value else value


def _struct_token(m) -> str:
    t = m.group(0)
    if not _urlish_token(t) or is_plain_url(t):
        return t
    return masked_url(t)


def struct_text(value: str) -> str:
    """A string under a structural (not free-text, not URL-named) key: every
    URL-shaped token that is not a plain routing URL becomes
    ``<scheme>://***``; every other token is kept as stored."""
    if not value or (not any(c in value for c in _STRUCT_HINTS) and "www." not in value.lower()
                     and _PCT_URL.search(value) is None):
        return value
    return _TOKEN.sub(_struct_token, value)


# Setting names the secret-name floor matches that hold no secret: the
# secrets_audit sink choice ("", "file", "log") round-trips as stored.
NOT_SECRET_FIELDS = frozenset({"secrets_audit_sink"})


def value_view(key, value):
    """``value`` as a client may see it when stored under ``key``, at any depth."""
    if is_credential_url_field(key):
        return credential_url_view(value)
    if is_url_field(key):
        return url_value(value)
    if key in FREE_TEXT_FIELDS:
        return withhold_text(value)
    if is_secret_field(key) and key not in NOT_SECRET_FIELDS:
        return secret_value(value)
    if isinstance(value, str):
        return proxy_text(value) if is_proxy_field(key) else struct_text(value)
    if isinstance(value, dict):
        return _view_dict(value)
    if isinstance(value, (list, tuple)):
        return [value_view(key, v) for v in value]
    return value


def _view_dict(cfg: dict) -> dict:
    return _unique_keys(
        (struct_text(k) if isinstance(k, str) else k, value_view(k, v))
        for k, v in cfg.items())


def site_view(cfg) -> dict:
    """A site / app config (or any dict a route serves from stored config) as
    /api/status, the SSE status frame and every other GET show it."""
    if not isinstance(cfg, dict):
        return cfg
    return _view_dict(cfg)


def status_entry(st) -> dict:
    """One /api/status (or SSE status) site entry: config, jobs, free text."""
    return site_view(st)


def frame_view(data):
    """A live /api/stream frame payload (any shape) as every GET shows it.
    Idempotent: a frame built from an already-viewed snapshot is unchanged."""
    return value_view("", data)


# Free-text fields the app itself writes (an error string can quote a
# request URL, path and all: "Max retries exceeded with url: /services/..").
# Every JSON response serves them through withhold_text (app.after_request);
# user-authored free text (notes, labels, ...) is withheld on the config
# routes, where a save restores it (restore_withheld).
DIAGNOSTIC_TEXT_FIELDS = frozenset({
    "last_error", "error", "login_status", "message", "status_message", "reason",
})
_DIAGNOSTIC_KEYS = tuple(f'"{k}"'.encode() for k in sorted(DIAGNOSTIC_TEXT_FIELDS))


def may_hold_diagnostic_text(raw: bytes) -> bool:
    """Cheap pre-check on a JSON body: names a DIAGNOSTIC_TEXT_FIELDS key."""
    return any(k in raw for k in _DIAGNOSTIC_KEYS)


# row700 x SEC-2: fixed refusal texts the app authors (constants only, no
# stored or request value in them) that a client must read verbatim -- the
# captcha gate names the request-only ack field and the provider's terms
# URLs.  Keyed by message id: only the function that builds the text records
# it, for the current request (app_message); the serve side keeps exactly
# those recorded segments and withholds every other part of the value.
# sec2-withhold-messages r1: the validation refusals a client must read
# (the scheme rule, the rule-id "/" guidance, the export root) are app messages too.
APP_MESSAGE_IDS = frozenset({"captcha-egress-disclosure", "alert-rule-invalid",
                             "template-sandbox-scheme", "scheduled-export-invalid"})
_APP_MESSAGES = "bd_sec2_app_messages"


def app_message(msg_id: str, text: str) -> str:
    """Record ``text`` as the app's fixed message ``msg_id`` for the current
    request and return it unchanged. Outside a request, for an id not in
    APP_MESSAGE_IDS or for a non-string, nothing is recorded."""
    if msg_id not in APP_MESSAGE_IDS or not isinstance(text, str) or not text:
        return text
    from flask import g, has_request_context
    if has_request_context():
        g.setdefault(_APP_MESSAGES, set()).add(text)
    return text


def request_app_messages() -> tuple:
    """The fixed messages recorded for the current request (longest first)."""
    from flask import g, has_request_context
    if not has_request_context():
        return ()
    return tuple(sorted(g.get(_APP_MESSAGES) or (), key=lambda t: (-len(t), t)))


def _withhold_except(value, keep: tuple):
    """withhold_text(value), except that every occurrence of a ``keep``
    segment inside a string is served verbatim; the parts around it are
    still withheld on their own."""
    if not keep or not isinstance(value, str):
        return withhold_text(value)
    out, i, ctx = [], 0, ""
    while True:
        hits = [(j, -len(s), s) for s in keep for j in (value.find(s, i),) if j >= 0]
        if not hits:
            break
        j, _, seg = min(hits)
        out += [_free_text(ctx + value[i:j], len(ctx)), seg]
        i, ctx = j + len(seg), seg
    out.append(_free_text(ctx + value[i:], len(ctx)))
    return "".join(out)


def diagnostic_view(value, keep: tuple = ()):
    """A JSON value with every DIAGNOSTIC_TEXT_FIELDS value, at any depth,
    served through withhold_text; everything else as it is. ``keep`` holds
    the app's recorded fixed messages (request_app_messages), served as is."""
    if isinstance(value, dict):
        return {k: _withhold_except(v, keep) if k in DIAGNOSTIC_TEXT_FIELDS
                else diagnostic_view(v, keep)
                for k, v in value.items()}
    if isinstance(value, list):
        return [diagnostic_view(v, keep) for v in value]
    return value


def _kind(value) -> str:
    return "list" if isinstance(value, (list, tuple)) else "dict" if isinstance(value, dict) else "str"


def _marker(scheme: str, masked, configured: bool = True, kind: str = "str") -> dict:
    return {"withheld": True, "configured": configured, "scheme": scheme,
            "masked": masked, "kind": kind}


def export_view(cfg) -> dict:
    """A site config for a default (secret-less) export: every top-level value
    whose served form differs becomes a withheld marker that
    restore_withheld() understands."""
    if not isinstance(cfg, dict):
        return cfg
    out = {}
    for k, v in cfg.items():
        if is_credential_url_field(k):
            view = credential_url_view(v)
            out[k] = view if view["configured"] else ""
            continue
        if is_secret_field(k) and not withholds(k):
            # A secret-named key outside site_editor.SECRET_FIELDS (e.g.
            # api_key) is not blanked by the export routes: withhold it here.
            out[k] = v if secret_value(v) == v else _marker("", MASK, kind=_kind(v))
            continue
        shown = value_view(k, v)
        if shown == v:
            out[k] = v
        elif k in FREE_TEXT_FIELDS:
            out[k] = _marker("", shown, kind=_kind(v))
        else:
            out[k] = _marker(_scheme(v) if isinstance(v, str) else "",
                             shown if isinstance(shown, str) else MASK, kind=_kind(v))
    key_view = {k: struct_text(k) if isinstance(k, str) else k for k in out}
    if any(kv != k for k, kv in key_view.items()):
        out = _unique_keys((key_view[k], v) for k, v in out.items())
    return out


_MARKER_KEYS = frozenset({"withheld", "configured", "scheme", "masked", "count", "kind"})
_BLANK = {"list": list, "dict": dict}


def is_withheld_marker(value) -> bool:
    return (isinstance(value, dict) and value.get("withheld") is True
            and set(value) <= _MARKER_KEYS)


def _masked_credential(sent, stored) -> bool:
    """``sent`` is the masked string (or the count-less view) a credential
    field showed for ``stored``."""
    view = credential_url_view(stored)
    return bool(view["configured"]) and isinstance(sent, str) and sent.strip() == view["masked"]


def _restore_credential(sent, stored):
    """A credential field as sent back: the served mask alone keeps ``stored``;
    a mask item beside edited items stands for every stored item (kept, in
    place of the mask). A mask is never stored as a URL: with nothing stored
    it is dropped."""
    if _masked_credential(sent, stored):
        return stored
    if not isinstance(sent, (str, list, tuple)):
        return sent
    items = _credential_items(sent)
    if not any(_VIEW_MASKED.fullmatch(x) for x in items):
        return sent
    kept, out = _credential_items(stored), []
    for x in items:
        if _VIEW_MASKED.fullmatch(x):
            out.extend(kept)
            kept = []
        else:
            out.append(x)
    if out == _credential_items(stored):
        return stored
    if isinstance(sent, str):
        return ("\n" if "\n" in sent else ",").join(out)
    return out


def _key_map(stored: dict, key_view) -> dict:
    """served key -> stored key, for the keys a client was shown withheld
    (ambiguous served keys map to None)."""
    out = {}
    for k in stored:
        shown = key_view(k) if isinstance(k, str) else k
        if shown != k:
            out[shown] = None if shown in out else k
    return out


def _stored_key(sent_key, stored: dict, kmap: dict):
    """The stored key a client was shown as ``sent_key``, or None."""
    if sent_key in stored:
        return sent_key
    return kmap.get(sent_key)


def _restore_echo(key, sent, stored, text: bool = False):
    """``sent`` with every part a client was shown for ``stored`` (and sent
    back unchanged) replaced by the stored value, at any depth. ``text`` is
    true inside a free-text field (keys and values are text there)."""
    if sent == stored:
        return sent
    text = text or key in FREE_TEXT_FIELDS
    if not text:
        if is_credential_url_field(key):
            return _restore_credential(sent, stored)
        if is_secret_field(key):
            return stored if value_view(key, stored) == sent else sent
    if (withhold_text(stored) if text else value_view(key, stored)) == sent:
        return stored
    if isinstance(sent, dict) and isinstance(stored, dict):
        kmap = _key_map(stored, _text_key if text else struct_text)
        out = {}
        for sk, sv in sent.items():
            k = _stored_key(sk, stored, kmap)
            if k is None:
                out[sk] = sv
            else:
                out[k] = _restore_echo(k, sv, stored[k], text)
        return out
    if isinstance(sent, list) and isinstance(stored, (list, tuple)):
        # Each sent item is the stored item whose served form it matches best
        # (whole item, else most equal fields); a tie goes to the same index
        # only when no item was added or removed. Masks look alike, so an
        # item never takes a withheld value by position alone.
        shown = [withhold_text(b) if text else value_view(key, b) for b in stored]
        same_len = len(sent) == len(stored)
        out = []
        for i, a in enumerate(sent):
            j = _best_item(a, shown, i if same_len else None)
            out.append(a if j is None else _restore_echo(key, a, stored[j], text))
        return out
    return sent


def _best_item(sent, shown: list, pos):
    """Index of the served item ``sent`` echoes (see _restore_echo), or None."""
    def score(v):
        if v == sent:
            return float("inf")
        if isinstance(sent, dict) and isinstance(v, dict):
            return sum(1 for k, x in sent.items() if k in v and v[k] == x)
        return 0
    scores = [score(v) for v in shown]
    best = max(scores, default=0)
    if not best:
        return None
    ties = [i for i, x in enumerate(scores) if x == best]
    if len(ties) == 1:
        return ties[0]
    return pos if pos in ties else None


def restore_withheld(cfg: dict, stored: dict | None) -> list:
    """Resolve withheld markers in an incoming config, in place.

    A marker keeps ``stored[key]`` (the same site's current value). Without
    one, a free-text field takes the marker's placeholder text and any other
    field is left blank (by the marker's kind); those field names are
    returned (re-enter them). Any served form a client was shown
    (``https://***``, ``tgram://***``, text with PLACEHOLDER, a nested list
    or dict with withheld parts) and sends back unchanged keeps the stored
    value."""
    reenter = []
    if not isinstance(cfg, dict):
        return reenter
    kmap = _key_map(stored, struct_text) if isinstance(stored, dict) else {}
    for k, v in list(cfg.items()):
        if not is_withheld_marker(v):
            if isinstance(stored, dict):
                sk = _stored_key(k, stored, kmap)
                if sk is not None:
                    new = _restore_echo(sk, v, stored[sk])
                    if sk != k:
                        del cfg[k]
                    cfg[sk] = new
            continue
        if isinstance(stored, dict) and k in stored:
            cfg[k] = stored[k]
        elif k in FREE_TEXT_FIELDS and isinstance(v.get("masked"), (str, list, dict)):
            cfg[k] = v["masked"]
        else:
            cfg[k] = _BLANK.get(v.get("kind"), str)()
            if v.get("configured"):
                reenter.append(k)
    return reenter
