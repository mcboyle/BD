"""template_extractor_impl.login_extract -- verbatim cluster from template_extractor.py."""

from __future__ import annotations

import re as _re_login
from typing import Any, Dict, List

from ._css import _css_escape, _css_escape_attr
from ._constants import _LOGIN_SUBMIT_TEXT


def _login_form_prefix(tag) -> str:
    """A CSS prefix scoping a selector to the tag's enclosing <form>.

    v3.65.2: ids and class names containing CSS-special characters (':',
    '.', '[', ']', '/', etc.) need backslash-escaping or they parse as
    pseudo-classes / combinators and Playwright errors out. Route them
    through _css_escape — same helper the download-template path uses.
    """
    form = tag.find_parent("form")
    if form is None:
        return ""
    if form.get("id"):
        return f"form#{_css_escape(form['id'])} "
    fcls = [c for c in (form.get("class") or [])
            if not _re_login.search(r"\d", c)]
    return f"form.{_css_escape(fcls[0])} " if fcls else "form "


def _login_selectors(tag, role=None) -> List[str]:
    """Stable CSS selectors for one login element: #id, [name=...],
    tag.class — most specific first, digit-bearing classes dropped
    (they are usually hashed/volatile).

    `role` ("user" | "pass" | "submit") enables type-based
    disambiguation: many sites give the username and password inputs
    the SAME class and no id/name (e.g. a Vue app with one shared
    input class). A bare class selector would then match both, so the
    password gets typed into the username box. When role is given, a
    form-scoped, type-qualified selector is prepended:
      pass   -> form... input[type='password']
      user   -> form... input[type='text'], input[type='email']
    so the two never collide.

    With no identifying attributes at all, a structural form-scoped
    selector is the fallback so a bare element still yields something.

    v3.65.2: ids/classes go through _css_escape, quoted attribute values
    through _css_escape_attr. Without this, an id like "login:form" or
    a name like "user[email]" containing a single quote produces a
    selector Playwright cannot parse.
    """
    out = []
    # Type-qualified, form-scoped selector FIRST for inputs — this is
    # the only selector guaranteed to tell user and pass apart when
    # they share a class.
    if role in ("user", "pass") and tag.name == "input":
        pfx = _login_form_prefix(tag)
        ttype = (tag.get("type") or "text").lower()
        if role == "pass":
            out.append(f"{pfx}input[type='password']".strip())
        else:
            # v3.66.0: normalize unknown user-side input types to 'text'.
            # Browsers treat any unrecognized `type` value as 'text', so
            # a selector with the literal bogus type (e.g. the historical
            # input[type='username'] on Nubiles) won't match any of the
            # inputs that browser created from the same HTML — the
            # selector finds nothing while the input is sitting right
            # there as a text box. Restrict the emitted type to the set
            # of text-shaped types real login forms actually use.
            _TEXT_SHAPED = {"text", "email", "tel", "url", "search"}
            if ttype not in _TEXT_SHAPED:
                ttype = "text"
            out.append(f"{pfx}input[type='{_css_escape_attr(ttype)}']".strip())
    if tag.get("id"):
        out.append(f"#{_css_escape(tag['id'])}")
    if tag.get("name"):
        out.append(f"{tag.name}[name='{_css_escape_attr(tag['name'])}']")
    cls = [c for c in (tag.get("class") or [])
           if not _re_login.search(r"\d", c)]
    # For user/pass inputs, a bare class selector is only safe if the
    # element ALSO has an id or name (so the class is just an extra
    # fallback). When id/name are absent, the class is very often
    # shared between the username and password inputs — emitting it
    # would let one match both. In that case the type-qualified
    # selector built above is the reliable answer; skip the class.
    if cls and (role not in ("user", "pass") or tag.get("id")
                or tag.get("name")):
        out.append(f"{tag.name}." + ".".join(_css_escape(c) for c in cls[:2]))
    if not out:
        # No identifying attributes — build a structural selector.
        pfx = _login_form_prefix(tag)
        ttype = tag.get("type")
        if ttype:
            # v3.66.0: same normalization as the role-qualified branch
            # above — for inputs, an unknown declared type becomes
            # 'text' so the emitted selector matches what the browser
            # actually rendered.
            if tag.name == "input":
                _TEXT_SHAPED = {"text", "email", "tel", "url", "search",
                                "password", "hidden", "submit", "button",
                                "checkbox", "radio", "file"}
                lt = ttype.lower()
                if lt not in _TEXT_SHAPED:
                    ttype = "text"
            out.append(f"{pfx}{tag.name}[type='{_css_escape_attr(ttype)}']".strip())
        else:
            out.append(f"{pfx}{tag.name}".strip())
    seen = []
    for s in out:
        if s not in seen:
            seen.append(s)
    return seen


def _login_is_honeypot(tag) -> bool:
    """True if a field is hidden from real users (a bot trap). Such a
    field must never be chosen as the username input."""
    style = (tag.get("style") or "").lower().replace(" ", "")
    if any(x in style for x in ("display:none", "left:-10000",
                                "left:-9999", "width:1px", "height:1px",
                                "opacity:0", "visibility:hidden")):
        return True
    if tag.get("tabindex") == "-1":
        return True
    if tag.get("aria-hidden") == "true":
        return True
    parent = tag.parent
    for _ in range(3):
        if parent is None or getattr(parent, "get", None) is None:
            break
        ps = (parent.get("style") or "").lower().replace(" ", "")
        if (any(x in ps for x in ("left:-10000", "left:-9999",
                                  "display:none"))
                or parent.get("aria-hidden") == "true"):
            return True
        parent = parent.parent
    ident = " ".join([tag.get("name") or "", tag.get("id") or ""]).lower()
    if "website" in ident or "honeypot" in ident:
        return True
    return False


def _login_find_submit(scope):
    """Find the login form's submit control. Returns (element, note).
    Tries, in order: a real submit button/input; an element with an
    onclick that calls submit(); an element whose class contains
    'submit'; an element whose visible text reads like a login button.
    When several real submit controls exist, a <button type=submit> is
    preferred over an <input type=submit> (the bare input is usually a
    secondary/nav-bar quick-login control).
    """
    submits = scope.find_all(
        ["button", "input"],
        attrs={"type": _re_login.compile("^submit$", _re_login.I)})
    if submits:
        for el in submits:                       # prefer a <button>
            if el.name == "button":
                return el, None
        return submits[0], None                  # else the first <input>
    for el in scope.find_all(attrs={"onclick":
                                    _re_login.compile("submit",
                                                      _re_login.I)}):
        return el, "submit is an onclick handler, not a real button"
    for el in scope.find_all(class_=_re_login.compile("submit",
                                                      _re_login.I)):
        if el.name in ("div", "a", "span", "button"):
            return el, "submit is a styled element (class~=submit)"
    for el in scope.find_all(["div", "a", "span", "button"]):
        txt = (el.get_text() or "").strip()
        if (txt and len(txt) < 24 and _LOGIN_SUBMIT_TEXT.search(txt)
                and not el.find(["div", "a"])):
            return el, f"submit found by visible text {txt!r}"
    b = scope.find("button")
    return (b, "no submit type; using the first <button>") if b else (None, None)


def extract_login_from_html(html: str) -> Dict[str, Any]:
    """Parse a pasted login page's HTML into a learned.login block.

    Returns: {
      ok: bool,
      login: { user_field: [...], pass_field: [...], submit_btn: [...] },
      form_action: str,
      warnings: [...],
    }
    Never raises — bad input returns {ok: False, error: ...}.
    """
    if not isinstance(html, str) or not html.strip():
        return {"ok": False, "error": "no HTML provided",
                "login": None, "warnings": []}
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"ok": False,
                "error": "BeautifulSoup (bs4) not installed",
                "login": None, "warnings": []}
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        return {"ok": False,
                "error": f"HTML parse failed: {type(e).__name__}: {e}",
                "login": None, "warnings": []}
    warnings: List[str] = []
    # The password input is the anchor of a login form. A page may
    # carry several (a signup/join modal AND the real login form);
    # prefer the one whose enclosing <form> action looks like a login
    # endpoint, explicitly skip ones inside a signup/join/register
    # form, and fall back to the first if nothing matches.
    pw_inputs = soup.find_all(
        "input", attrs={"type": _re_login.compile("^password$",
                                                  _re_login.I)})
    if not pw_inputs:
        return {"ok": False,
                "error": "no password input found in the HTML",
                "login": None, "warnings": []}

    def _form_action(inp):
        f = inp.find_parent("form")
        return (f.get("action") if f else "") or ""

    _LOGIN_ACTION = _re_login.compile(
        r"log[\-_]?in|auth|signin|sign[\-_]?in", _re_login.I)
    _JOIN_ACTION = _re_login.compile(
        r"join|signup|sign[\-_]?up|register|checkout|payment",
        _re_login.I)
    pw = None
    for cand in pw_inputs:                       # 1. a login-action form
        if _LOGIN_ACTION.search(_form_action(cand)):
            pw = cand
            break
    if pw is None:
        for cand in pw_inputs:                   # 2. not a join form
            if not _JOIN_ACTION.search(_form_action(cand)):
                pw = cand
                break
    if pw is None:                               # 3. give up, take first
        pw = pw_inputs[0]
        if len(pw_inputs) > 1:
            warnings.append("multiple password fields found; could "
                            "not tell which form is the login — using "
                            "the first")
    form = pw.find_parent("form")
    # v3.65.2: SPAs commonly build "forms" without an enclosing <form>
    # element (React/Vue control state in JS). Falling all the way back
    # to `soup` (the whole document) lets the username scan below pick
    # up the first plausible input on the page — which is often a
    # navbar search box or newsletter signup, not the login. When no
    # form exists, climb up from the password field until we find an
    # ancestor that also contains a username-shaped input; that
    # ancestor is the de-facto login container.
    _NON_USERNAME_TYPES = ("password", "hidden", "submit", "button",
                           "checkbox", "radio", "file", "image",
                           "reset", "range", "color", "date")
    def _looks_like_username_input(inp):
        if inp is pw or getattr(inp, "name", None) != "input":
            return False
        t = (inp.get("type") or "text").lower()
        if t in _NON_USERNAME_TYPES:
            return False
        ident = " ".join([inp.get("name") or "", inp.get("id") or "",
                           inp.get("placeholder") or ""]).lower()
        if any(x in ident for x in ("search", "captcha", "coupon",
                                    "promo")):
            return False
        if _login_is_honeypot(inp):
            return False
        return True

    scope = None
    if form:
        scope = form
    else:
        warnings.append("password field is not inside a <form>; "
                        "scoping to the nearest ancestor that also "
                        "contains a username-shaped input")
        ancestor = pw.parent
        # Walk up to a sensible depth — most SPA login widgets sit 2-6
        # levels above the password input. Stop at the body or after
        # 12 hops, whichever comes first.
        hops = 0
        while ancestor is not None and hops < 12:
            if getattr(ancestor, "name", None) in (None, "[document]", "html"):
                break
            try:
                inputs = ancestor.find_all("input")
            except Exception:
                inputs = []
            if any(_looks_like_username_input(i) for i in inputs):
                scope = ancestor
                break
            if getattr(ancestor, "name", None) == "body":
                break
            ancestor = ancestor.parent
            hops += 1
        if scope is None:
            # Nothing better found — fall back to the document, with a
            # warning so the user knows the selectors may be loose.
            scope = soup
            warnings.append("could not locate a login container above "
                            "the password field; scanning the whole "
                            "document for the username input")
    # Username: the first NON-honeypot username-like input in the
    # form. Real login forms use type text/email/tel, but some sites
    # use a non-standard type (e.g. type="username", which browsers
    # treat as text) — accept anything that is not a clearly-different
    # control type.
    user = None
    for inp in scope.find_all("input"):
        t = (inp.get("type") or "text").lower()
        if t in _NON_USERNAME_TYPES:
            continue
        ident = " ".join([inp.get("name") or "", inp.get("id") or "",
                           inp.get("placeholder") or ""]).lower()
        if any(x in ident for x in ("search", "captcha", "coupon",
                                    "promo")):
            continue
        if _login_is_honeypot(inp):
            warnings.append("skipped a honeypot field: "
                            + (inp.get("name") or inp.get("id") or "?"))
            continue
        user = inp
        break
    if user is None:
        warnings.append("no username/email input found near the "
                        "password field; user_field left empty")
    btn, note = _login_find_submit(scope)
    if note:
        warnings.append(note)
    login = {
        "user_field": _login_selectors(user, role="user") if user else [],
        "pass_field": _login_selectors(pw, role="pass"),
        "submit_btn": _login_selectors(btn, role="submit") if btn else [],
    }
    # A selector present in BOTH user_field and pass_field cannot tell
    # the two inputs apart — drop it from both. The role-aware
    # type-qualified selector built above is the one that distinguishes
    # them and is always kept (it never collides).
    shared = set(login["user_field"]) & set(login["pass_field"])
    if shared:
        login["user_field"] = [s for s in login["user_field"]
                               if s not in shared]
        login["pass_field"] = [s for s in login["pass_field"]
                               if s not in shared]
    return {"ok": True, "login": login,
            "form_action": (form.get("action") if form else ""),
            "warnings": warnings}
