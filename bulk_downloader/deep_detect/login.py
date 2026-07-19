from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import re
from urllib.parse import (
    parse_qsl, urlencode, urljoin, urlparse, urlunparse,
)

from .candidates import (score_download_link)
from .urls import (decode_url)


LOGIN_TERMS = (
    "login", "log in", "log-in", "signin", "sign in", "sign-in",
    "auth", "authenticate", "session", "account", "member",
    "username", "email", "password", "continue", "submit",
)


LOGIN_FIELD_TERMS = (
    "user", "userid", "user_id", "loginid", "login_id",
    "email", "mail", "phone", "mobile",
    "account", "acct", "member", "customer",
    "pass", "passwd", "pwd", "password",
    "otp", "mfa", "2fa", "totp", "code",
)


TOKEN_FIELDS = (
    "csrf", "_csrf", "xsrf", "_token", "authenticity_token",
    "__requestverificationtoken", "nonce", "state",
    "session", "sid", "lt", "execution", "relaystate",
    "samlrequest", "samlresponse",
)


HONEYPOT_NAMES = (
    "honeypot", "hp", "bot", "bots", "botcheck",
    "website", "url", "homepage", "company",
    "fax", "nickname", "middle_name",
    "do_not_fill", "leave_blank", "blank",
    "confirm_email", "confirm_password",
)


HONEYPOT_CSS_HIDDEN = (
    "display:none",
    "visibility:hidden",
    "opacity:0",
    "position:absolute;left:-9999",
    "position:absolute;left:-10000",
    "position:absolute;top:-9999",
    "position:absolute;top:-10000",
    "height:0",
    "width:0",
    "z-index:-1",
    "pointer-events:none",
    "clip-path:inset(100%)",
    # F3 (v3.66.50): off-screen transforms + clip shapes. Stored
    # whitespace-stripped + lowercase to match the inline-style and
    # css-class consultation, which both normalize via
    # .lower().replace(" ", "") before substring-testing. -9999px and
    # -10000px variants mirror the position:absolute;left/top: convention
    # above.
    "transform:translatex(-9999px)",
    "transform:translatex(-10000px)",
    "transform:translatey(-9999px)",
    "transform:translatey(-10000px)",
    "transform:scale(0)",
    "clip-path:circle(0)",
)


BOT_DEFENSE_MARKERS = (
    "recaptcha", "g-recaptcha",
    "hcaptcha",
    "cf-turnstile", "turnstile",
    "cloudflare", "cf-chl",
    "datadome",
    "perimeterx", "_px", "px-captcha",
    "akamai", "_abck", "bm_sz",
    "kasada",
    "imperva", "incapsula",
    "arkose",
    "fingerprintjs", "botd",
)


DRM_MARKERS = (
    "widevine", "playready", "fairplay",
    "com.widevine.alpha", "com.microsoft.playready",
    "com.apple.fps",
    "ContentProtection", "<ContentProtection",
    "pssh", "cenc", "skd://",
    "licenseUrl", "license_url",
    "drmtoday", "drm-today",
    "requestMediaKeySystemAccess",
)


PASSWORDLESS_TERMS = (
    "magic link", "email me a link", "send link",
    "one-time code", "verification code",
    "passcode", "continue with email",
    # F8: additional passwordless phrasings
    "otp", "login code", "sign-in code", "sign in code",
    "email a code", "we'll email you", "we will email you",
    "no password needed", "without a password",
)


MFA_TERMS = (
    "mfa", "2fa", "two-factor", "two step", "two-step",
    "verification code", "authenticator",
    "sms code", "backup code", "security key",
    # F8: additional MFA phrasings
    "totp", "recovery code", "one-time password",
    "authenticator app", "text message code",
    "enter the code", "6-digit code", "six-digit code",
)


MFA_FIELD_NAMES = (
    "otp", "totp", "code", "verification_code",
    "mfa_code", "security_code", "backup_code",
    # F8: additional field-name variants
    "otp_code", "auth_code", "2fa_code", "twofa_code",
    "recovery_code", "one_time_code", "passcode",
)


OAUTH_LOGIN_PATTERNS = (
    "/oauth/authorize", "/authorize",
    "client_id=", "redirect_uri=", "response_type=",
    "scope=", "state=",
    "code_challenge", "code_challenge_method",
    "openid", "oidc",
    # F8: additional OAuth/OIDC markers
    "/oauth2/", "/connect/authorize", "nonce=",
    "id_token", "grant_type", "well-known/openid-configuration",
)


SSO_PROVIDERS = (
    "google", "microsoft", "azure", "entra",
    "okta", "auth0", "cognito", "keycloak",
    "onelogin", "pingidentity", "duo",
)


SAML_MARKERS = (
    "SAMLRequest", "SAMLResponse", "RelayState",
    "/saml/", "/sso/saml",
    "urn:oasis:names:tc:SAML",
    # F8: additional SAML markers
    "/saml2/", "samlp:", "saml:assertion",
    "/adfs/ls", "wctx=", "wa=wsignin",
)


DEVICE_CODE_MARKERS = (
    "device code", "enter this code",
    "microsoft.com/devicelogin",
    "user_code", "device_code", "verification_uri",
    # F8: additional device-code markers
    "verification_uri_complete", "/device/code", "activate",
)


WEBAUTHN_MARKERS = (
    "navigator.credentials", "PublicKeyCredential",
    "webauthn", "passkey", "FIDO", "ctap",
    # F8: additional WebAuthn / passkey markers
    "use your security key", "use a passkey", "sign in with a passkey",
    "credentials.get", "credentials.create", "authenticatorattachment",
    "u2f",
)


def _form_text(form) -> str:
    """Concatenate form text + button text + label text into one
    lowercase blob for term-matching. Avoids picking up wholly
    unrelated body text by staying inside the form scope."""
    parts = [form.get_text(" ", strip=True)]
    try:
        for inp in form.find_all(["input", "button", "label",
                                  "a", "h1", "h2", "h3"]):
            for attr in ("placeholder", "aria-label", "title",
                         "name", "id"):
                v = inp.get(attr)
                if v:
                    parts.append(v)
    except Exception:
        pass
    return " ".join(parts).lower()


def _is_visible_input(el, css_text: str = "") -> bool:
    """Best-effort visibility check. Combines element-level style
    attributes and the document's CSS text (for selectors that target
    this element by class or id). Conservative: when in doubt, treat
    as visible.

    css_text consultation: looks for rule blocks targeting any of the
    element's classes (or its id) with a hiding declaration inside.
    We don't implement a full CSS engine — this only catches the
    common case of `.honeypot-class { display:none }` or
    `#field-id { visibility:hidden }`. Specificity, media queries,
    and pseudo-selectors are ignored. False positives (treating a
    visible element as hidden) would be costly here — we'd skip a
    real input — so the matching is intentionally narrow.
    """
    style = (el.get("style") or "").lower().replace(" ", "")
    for pat in HONEYPOT_CSS_HIDDEN:
        if pat in style:
            return False
    if el.get("hidden") is not None:
        return False
    if el.get("type") == "hidden":
        return False
    if el.get("aria-hidden") == "true":
        return False
    if el.get("tabindex") == "-1":
        return False

    # css_text consultation. Build the list of selectors that could
    # target this element (id-based and class-based, single-selector
    # only for simplicity), then look for a rule block whose selector
    # list contains any of them AND whose body has a hiding rule.
    if css_text:
        selectors_for_el: List[str] = []
        el_id = el.get("id")
        if el_id:
            selectors_for_el.append("#" + el_id)
        classes = el.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        for cls in classes:
            if isinstance(cls, str) and cls:
                selectors_for_el.append("." + cls)
        if selectors_for_el:
            # Iterate rule blocks `selector-list { body }`.
            # The regex is intentionally simple — nested at-rules
            # like @media (e.g. `@media (max-width: …) { .x{display:none} }`)
            # would be matched too, but we treat the inner rule the
            # same; missing media-query context just means a small
            # false-positive rate. Better than missing a class-styled
            # honeypot entirely.
            for sel_block, body in _iter_css_rule_blocks(css_text):
                # Strip whitespace around each comma-separated selector
                # for an exact-token match, so `.foo` doesn't match
                # `.foobar`.
                rule_selectors = [s.strip()
                                  for s in sel_block.split(",")]
                if not any(s in rule_selectors
                           for s in selectors_for_el):
                    continue
                body_l = body.lower().replace(" ", "")
                for pat in HONEYPOT_CSS_HIDDEN:
                    if pat in body_l:
                        return False
    return True


def _iter_css_rule_blocks(css_text):
    """Yield ``(selector_list, body)`` for each ``… { body }`` CSS block.

    A linear-time, backtracking-free replacement for the old
    ``([^{}]+)\\{([^{}]*)\\}`` regex, which drove ``finditer`` into O(n²) on a
    long brace-free CSS region (minified/junk stylesheet, unstripped comment) —
    the engine re-attempted the greedy leading run at every offset (bounding the
    run alone left it O(cap·n), still seconds on a large blob). Splitting on
    ``}`` is O(n) and brace-safe: each pre-``}`` chunk is ``<stuff>{body`` whose
    body (after the last ``{``) is brace-free, and the selector is the maximal
    non-brace run immediately before that ``{`` — so a nested
    ``@media (...) { .x{display:none} }`` still yields the inner ``.x`` rule,
    matching the old behavior. Not a real CSS parser; best-effort, as before.
    See test_deep_detect_redos.
    """
    if not css_text or "{" not in css_text:
        return
    for chunk in css_text.split("}"):
        ob = chunk.rfind("{")
        if ob == -1:
            continue
        body = chunk[ob + 1:]          # brace-free: no '}' in chunk, after last '{'
        sel = chunk[:ob]
        cut = max(sel.rfind("{"), sel.rfind("}"))  # keep only the run before '{'
        if cut != -1:
            sel = sel[cut + 1:]
        if sel:                        # the old '+' required >=1 selector char
            yield sel, body


def _input_is_honeypot(el, css_text: str = "") -> bool:
    """Extended honeypot check — combines the existing
    template_extractor._login_is_honeypot heuristics with the
    HONEYPOT_NAMES vocabulary. Hidden + suspicious-name fields are
    honeypots; visible inputs with suspicious names are NOT (a real
    user-named field like 'company' on a B2B signup is legitimate)."""
    if not _is_visible_input(el, css_text):
        ident = " ".join([el.get("name") or "", el.get("id") or "",
                          el.get("placeholder") or ""]).lower()
        if any(h in ident for h in HONEYPOT_NAMES):
            return True
        # Hidden text/email input that's NOT named like a token field
        # is a honeypot. (Hidden CSRF/state inputs are legitimate.)
        t = (el.get("type") or "text").lower()
        if t in ("text", "email", "tel"):
            if not any(tok in ident for tok in TOKEN_FIELDS):
                return True
    return False


def _classify_login_type(form, page_text: str,
                        scripts_text: str) -> List[str]:
    """Return a list of login-type tags that apply to this form.
    Multiple are possible (a page with email passwordless + a 'use
    your security key' option carries both 'passwordless' and
    'webauthn')."""
    types: List[str] = []
    form_text = _form_text(form)
    blob = " ".join([form_text, page_text, scripts_text]).lower()

    has_password = any(
        (i.get("type") or "").lower() == "password"
        for i in form.find_all("input")
    )

    if has_password:
        types.append("form_password")
    else:
        # No password field → not a classic form. Could be passwordless,
        # MFA-only step, SSO, SAML, WebAuthn, etc.
        if any(t in blob for t in PASSWORDLESS_TERMS):
            types.append("passwordless")
        if any(name in (i.get("name") or "").lower()
               for i in form.find_all("input")
               for name in MFA_FIELD_NAMES):
            types.append("mfa")

    if any(t in blob for t in MFA_TERMS):
        if "mfa" not in types:
            types.append("mfa")

    if any(p in blob for p in OAUTH_LOGIN_PATTERNS):
        types.append("sso_oauth")

    if any(m.lower() in blob for m in SAML_MARKERS):
        types.append("saml")

    if any(m.lower() in blob for m in DEVICE_CODE_MARKERS):
        types.append("device_code")

    if any(m.lower() in blob for m in WEBAUTHN_MARKERS):
        types.append("webauthn")

    # If `has_password` is True, we already appended "form_password"
    # above, so `types` is always non-empty in that case. The fallback
    # only matters for has_password=False with no other markers — e.g.
    # an obscure auth form we can't classify.
    return types or ["unknown"]


def _detect_bot_defenses_in_blob(blob: str) -> List[str]:
    """Names of bot-defense systems mentioned in the page or its
    scripts. Each name in BOT_DEFENSE_MARKERS that appears is
    returned (deduped)."""
    found = []
    low = blob.lower()
    for m in BOT_DEFENSE_MARKERS:
        if m.lower() in low and m not in found:
            found.append(m)
    return found


_BOT_DEFENSE_SYSTEMS = (
    ("Cloudflare", ("cloudflare", "cf-chl", "cf-turnstile", "turnstile")),
    ("DataDome", ("datadome",)),
    ("PerimeterX/HUMAN", ("perimeterx", "_px", "px-captcha")),
    ("Akamai Bot Manager", ("akamai", "_abck", "bm_sz")),
    ("Kasada", ("kasada",)),
    ("Imperva/Incapsula", ("imperva", "incapsula")),
    ("Arkose Labs", ("arkose",)),
    ("reCAPTCHA", ("recaptcha", "g-recaptcha")),
    ("hCaptcha", ("hcaptcha",)),
    ("fingerprinting library", ("fingerprintjs", "botd")),
)


def classify_bot_defenses(blob: str) -> List[str]:
    """Return the NAMED bot-defense / anti-automation systems present in
    the page, grouped from the raw markers. Detect-and-report only."""
    low = blob.lower()
    names = []
    for name, markers in _BOT_DEFENSE_SYSTEMS:
        if any(m in low for m in markers) and name not in names:
            names.append(name)
    return names


_FINGERPRINT_SIGNALS = (
    ("canvas", ("todataurl", "getimagedata")),
    ("webgl", ("webgl_debug_renderer_info", "unmasked_renderer",
               "getextension('webgl")),
    ("webdriver_probe", ("navigator.webdriver", "_phantom", "__nightmare",
                         "domautomation")),
    ("audio", ("audiocontext", "createoscillator", "createanalyser")),
    ("fonts", ("offsetwidth", "measuretext")),  # weak; only with others
)


def detect_fingerprinting_signals(blob: str) -> List[str]:
    """Names of fingerprinting techniques referenced in the page's inline
    scripts. Static signal — confirms the page CONTAINS fingerprinting
    code; whether it executes is confirmed by the live observer. The
    'fonts' signal is weak alone (offsetWidth/measureText have benign
    uses), so it is reported only when a stronger signal is also present."""
    low = blob.lower()
    hits = []
    for name, needles in _FINGERPRINT_SIGNALS:
        if any(n in low for n in needles):
            hits.append(name)
    if "fonts" in hits and len(hits) == 1:
        hits = []
    return hits


def score_login_form(form, *, page_text: str = "",
                     scripts_text: str = "",
                     css_text: str = "",
                     base_url: str = "",
                     site_memory: Optional[dict] = None) -> dict:
    """Score a single <form> element. Returns:

        {
            "confidence":         0..100,
            "login_types":        [...],
            "has_password":       bool,
            "has_username_or_email": bool,
            "method":             "GET" | "POST" | ...,
            "action":             str,
            "safe_fields":        {field_name: input_role, ...},
            "honeypot_fields":    [field_name, ...],
            "submit_selector":    str | None,
            "bot_defenses":       [...],
            "do_not_auto_submit": bool,
            "reasons":            [...],
            "warnings":           [...],
        }
    """
    out = {
        "confidence": 0,
        "raw_score": 0,
        "login_types": [],
        "has_password": False,
        "has_username_or_email": False,
        "method": (form.get("method") or "GET").upper(),
        "action": form.get("action") or "",
        "safe_fields": {},
        "honeypot_fields": [],
        "submit_selector": None,
        "bot_defenses": [],
        "do_not_auto_submit": False,
        "reasons": [],
        "warnings": [],
    }
    score = 0
    inputs = form.find_all("input")

    visible_password = None
    visible_user = None
    # Collect candidate user inputs in a first pass so we can prefer
    # the best one rather than locking in the first visible text/email/
    # tel field we see. Pre-fix this picked up things like a header
    # search box that appeared before the real username input.
    user_candidates: List[tuple] = []  # (priority_tuple, input)
    for inp in inputs:
        t = (inp.get("type") or "text").lower()
        name = (inp.get("name") or "").lower()
        if _input_is_honeypot(inp, css_text):
            label = inp.get("name") or inp.get("id") or "?"
            out["honeypot_fields"].append(label)
            continue
        if t == "password" and _is_visible_input(inp, css_text):
            visible_password = inp
            out["has_password"] = True
            out["safe_fields"][inp.get("name") or "password"] = "password"
            continue
        # Username/email candidate gathering. Don't pick yet.
        if t in ("text", "email", "tel"):
            if _is_visible_input(inp, css_text):
                # Priority key — lower (further left in tuple) wins
                # when sorted ascending. Three signals layered:
                #   1. name matches login vocabulary (strongest signal)
                #   2. type=email (next strongest — explicit semantic)
                #   3. document order (fallback — earlier wins)
                name_matches = any(term in name
                                   for term in LOGIN_FIELD_TERMS)
                # autocomplete attribute is also a strong signal:
                # autocomplete="username" or "email" beats name-matching
                # on modern forms (WAI / W3C autofill spec).
                ac = (inp.get("autocomplete") or "").lower()
                ac_is_login = ac in ("username", "email")
                priority = (
                    0 if (name_matches or ac_is_login) else 1,
                    0 if t == "email" else 1,
                    len(user_candidates),  # document order
                )
                user_candidates.append((priority, inp, t))
            continue
        # token fields
        if t == "hidden" and any(tok in name for tok in TOKEN_FIELDS):
            out["safe_fields"][inp.get("name") or "_token"] = "token"

    # Pick the best user input from the candidates.
    if user_candidates:
        user_candidates.sort(key=lambda c: c[0])
        _, best_input, best_t = user_candidates[0]
        visible_user = best_input
        out["has_username_or_email"] = True
        out["safe_fields"][
            best_input.get("name") or "username"] = (
            "email" if best_t == "email" else "username")

    # Submit button
    for btn in form.find_all(["button", "input"]):
        bt = (btn.get("type") or "").lower()
        if bt == "submit":
            if btn.get("id"):
                out["submit_selector"] = f"#{btn.get('id')}"
            elif btn.get("class"):
                out["submit_selector"] = (
                    btn.name + "." + ".".join(btn.get("class")[:2]))
            else:
                out["submit_selector"] = (
                    f"{btn.name}[type='submit']")
            break

    form_text = _form_text(form)

    # ── Scoring buckets ────────────────────────────────────────────
    if out["has_password"]:
        score += 50
        out["reasons"].append("visible password input (+50)")
    if out["has_username_or_email"]:
        score += 25
        out["reasons"].append("visible username/email input (+25)")
    if out["method"] == "POST":
        score += 10
        out["reasons"].append("form method=POST (+10)")
    action_low = out["action"].lower()
    if any(t in action_low for t in
           ("login", "signin", "sign-in", "auth", "session")):
        score += 20
        out["reasons"].append("form action contains login term (+20)")
    if any("csrf" in n.lower() or "token" in n.lower()
           or "_token" in n.lower()
           for n in out["safe_fields"]):
        score += 10
        out["reasons"].append("CSRF/token field captured (+10)")
    if any(t in form_text for t in LOGIN_TERMS):
        score += 10
        out["reasons"].append("login vocabulary in form text (+10)")
    if out["honeypot_fields"]:
        score -= 5
        out["reasons"].append(
            f"honeypot fields excluded: {out['honeypot_fields']} (-5)")

    # SSO / SAML / WebAuthn / passwordless / MFA / device-code
    out["login_types"] = _classify_login_type(
        form, page_text, scripts_text)

    # Penalty: if there's no visible password AND no passwordless
    # markers AND no SSO/SAML/webauthn — this probably isn't a login
    # form at all (it's a search box or newsletter signup).
    if (not out["has_password"]
            and not any(t in out["login_types"] for t in
                        ("passwordless", "mfa", "sso_oauth",
                         "saml", "webauthn", "device_code"))):
        score -= 30
        out["reasons"].append(
            "no password + no alternative auth detected (-30)")

    # Bot defenses + interactive challenges no longer hard-block. They
    # require the operator's per-site approval before auto-submit; the
    # choice is remembered (see _apply_auto_submit_approval). Collect
    # the reasons, then apply one approval decision for this form.
    _gate_reasons = []

    # Bot defenses — detect, do not bypass.
    defenses = _detect_bot_defenses_in_blob(
        " ".join([form_text, page_text, scripts_text]))
    if defenses:
        out["bot_defenses"] = defenses
        _gate_reasons.append(f"bot defense detected: {defenses}")
        # Don't penalize the form itself — it's still a real login,
        # just one we can't drive headlessly without approval.

    # F8: challenge classes that require a human-held second factor or
    # an out-of-band step cannot be driven headlessly. (form_password
    # and sso_oauth are deliberately NOT here: a plain form is
    # automatable, and an OAuth redirect is handled by the manual-login
    # fallback.)
    _NON_AUTOMATABLE = ("mfa", "webauthn", "saml",
                        "device_code", "passwordless")
    challenge = [t for t in out["login_types"] if t in _NON_AUTOMATABLE]
    if challenge:
        _gate_reasons.append(
            f"interactive auth challenge ({', '.join(challenge)})")

    if _gate_reasons:
        _apply_auto_submit_approval(
            out, site_memory=site_memory,
            key=_login_form_key(out.get("action", ""), base_url),
            why="; ".join(_gate_reasons))

    out["raw_score"] = score
    out["confidence"] = max(0, min(100, score))
    return out


def score_login_page(html: str, *, base_url: str = "",
                     site_memory: Optional[dict] = None) -> dict:
    """Score EVERY <form> on the page and return the highest-confidence
    one plus the full ranked list. Convenience wrapper over
    score_login_form. Also surfaces page-level signals (bot defenses,
    SSO/SAML markers that aren't tied to a single form)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"best": None, "candidates": [], "warnings": []}
    if not html or not html.strip():
        return {"best": None, "candidates": [], "warnings": []}

    soup = BeautifulSoup(html, "html.parser")
    # Visible text is not enough — bot-defense markers live in class
    # names (cf-turnstile) and SSO endpoints live in href values
    # (/oauth/authorize). Build a combined blob: visible text PLUS
    # every element's attribute values. Cap each attribute at a
    # sensible length so a pathological page can't make this O(huge).
    text_parts = [soup.get_text(" ", strip=True)]
    for el in soup.find_all(True):
        for k, v in (el.attrs or {}).items():
            if isinstance(v, list):
                v = " ".join(v)
            if isinstance(v, str) and v:
                text_parts.append(f"{k}={v[:400]}")
    page_text = " ".join(text_parts)
    scripts_text = " ".join(
        (s.string or s.text or "")[:5000]
        for s in soup.find_all("script")
    )
    css_text = " ".join(
        (s.string or s.text or "")
        for s in soup.find_all("style")
    )

    candidates = []
    for form in soup.find_all("form"):
        c = score_login_form(form, page_text=page_text,
                             scripts_text=scripts_text,
                             css_text=css_text,
                             base_url=base_url,
                             site_memory=site_memory)
        candidates.append(c)

    # Page-level synthesis: SSO/SAML/WebAuthn/passwordless options
    # that exist OUTSIDE any <form> (e.g. "Sign in with Google" button
    # that's a plain <a> link rather than a form submit). v3.66.10
    # change: surface these even when a password form ALSO exists.
    # Pre-fix the block was gated on `not any(has_password)` and
    # silently dropped the SSO option whenever a username/password
    # form was present — but modern login pages routinely offer BOTH,
    # and the caller deserves to see both options.
    #
    # Deduplication: only synthesize a page-level candidate for a
    # given auth type if no existing form-candidate ALREADY carries
    # that type in its login_types list.
    existing_types: set = set()
    for c in candidates:
        for t in c.get("login_types") or []:
            existing_types.add(t)
    page_blob = " ".join([page_text, scripts_text]).lower()
    page_types: List[str] = []
    if (any(p in page_blob for p in OAUTH_LOGIN_PATTERNS)
            and "sso_oauth" not in existing_types):
        page_types.append("sso_oauth")
    if (any(m.lower() in page_blob for m in SAML_MARKERS)
            and "saml" not in existing_types):
        page_types.append("saml")
    if (any(m.lower() in page_blob for m in WEBAUTHN_MARKERS)
            and "webauthn" not in existing_types):
        page_types.append("webauthn")
    if (any(t.lower() in page_blob for t in PASSWORDLESS_TERMS)
            and "passwordless" not in existing_types):
        page_types.append("passwordless")
    if page_types:
        page_cand = {
            "confidence": 40,
            "login_types": page_types,
            "has_password": False,
            "has_username_or_email": False,
            "method": "POST",
            "action": "",
            "safe_fields": {},
            "honeypot_fields": [],
            "submit_selector": None,
            "bot_defenses": _detect_bot_defenses_in_blob(
                page_blob),
            "do_not_auto_submit": False,
            "reasons": [
                f"page-level auth markers: {page_types} (no form)"],
            "warnings": [
                "auth option detected outside any <form>; classic "
                "credential submission may not apply"],
        }
        # These page-level options (SSO/SAML/WebAuthn/passwordless
        # outside a form) are inherently non-automatable — gate them on
        # per-site operator approval, same as form challenges. Keyed by
        # page host since there's no form action.
        _apply_auto_submit_approval(
            page_cand, site_memory=site_memory,
            key=_login_form_key("", base_url),
            why=f"page-level auth markers: {page_types}")
        candidates.append(page_cand)

    # Sort by confidence (always present, capped 0-100) with raw_score
    # as the tiebreaker (uncapped — distinguishes between two forms
    # both clamped to confidence=100 but with different actual scores).
    # Pre-fix this used .get("raw_score", confidence) which mixed the
    # two scales — a synthesized page-level candidate at confidence=40
    # was compared against form candidates' uncapped raw_score values.
    candidates.sort(
        key=lambda c: (c.get("confidence", 0),
                       c.get("raw_score", 0)),
        reverse=True)
    best = candidates[0] if candidates else None
    warnings = []
    if best and best.get("do_not_auto_submit"):
        warnings.append("bot defense present on best login candidate")
    # WebAuthn / passkey can't be automated headlessly — requires the
    # browser credential interaction. Pre-fix the check was equality
    # with the single-element list ['webauthn'], so a passkey-OR-
    # password page (`['form_password', 'webauthn']`) didn't warn even
    # though the WebAuthn path is still un-automatable. Better:
    # warn whenever WebAuthn is one of the options AND no password
    # fallback exists.
    if (best
            and "webauthn" in (best.get("login_types") or [])
            and not best.get("has_password")):
        warnings.append(
            "best login is WebAuthn/passkey only — requires browser "
            "credential interaction, cannot be automated")
    return {"best": best, "candidates": candidates,
            "warnings": warnings}


def find_honeypots(html_or_form, *, css_text: str = "") -> dict:
    """Catalog every honeypot signal in a form OR an entire HTML
    document. Returns:

        {
            "honeypot_inputs":    [{name, id, reason}, ...],
            "duplicate_fields":   [{name, count, hidden_count}, ...],
            "fake_submit_buttons":[{selector, reason}, ...],
            "bot_defenses":       [...],
            "time_traps":         [...],
            "behavior_traps":     [...],
        }

    Accepts either a bs4 Tag (a <form>) or raw HTML."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return {"honeypot_inputs": [], "duplicate_fields": [],
                "fake_submit_buttons": [], "bot_defenses": [],
                "time_traps": [], "behavior_traps": []}

    if hasattr(html_or_form, "find_all"):
        root = html_or_form
        full_text = root.get_text(" ", strip=True)
    else:
        if not html_or_form or not str(html_or_form).strip():
            return {"honeypot_inputs": [], "duplicate_fields": [],
                    "fake_submit_buttons": [], "bot_defenses": [],
                    "time_traps": [], "behavior_traps": []}
        soup = BeautifulSoup(html_or_form, "html.parser")
        root = soup
        full_text = soup.get_text(" ", strip=True)

    out = {
        "honeypot_inputs": [],
        "duplicate_fields": [],
        "fake_submit_buttons": [],
        "bot_defenses": [],
        "time_traps": [],
        "behavior_traps": [],
    }

    inputs = root.find_all("input") if hasattr(root, "find_all") else []

    # Per-input honeypot check
    for inp in inputs:
        if _input_is_honeypot(inp, css_text):
            ident = (inp.get("name") or inp.get("id") or "?")
            t = (inp.get("type") or "text").lower()
            reasons = []
            if any(h in (inp.get("name", "") + inp.get("id", "")
                         + inp.get("placeholder", "")).lower()
                   for h in HONEYPOT_NAMES):
                reasons.append("name matches honeypot vocabulary")
            if not _is_visible_input(inp, css_text):
                reasons.append("hidden via style/attribute")
            if inp.get("tabindex") == "-1":
                reasons.append("tabindex=-1")
            if inp.get("aria-hidden") == "true":
                reasons.append('aria-hidden="true"')
            out["honeypot_inputs"].append({
                "name": ident,
                "type": t,
                "reasons": reasons or ["heuristic match"],
            })

    # Duplicate-field detection: same name on multiple inputs where
    # at least one is hidden. The hidden one is the honeypot.
    by_name: Dict[str, list] = {}
    for inp in inputs:
        n = inp.get("name")
        if not n:
            continue
        by_name.setdefault(n, []).append(inp)
    for n, items in by_name.items():
        if len(items) < 2:
            continue
        hidden_count = sum(
            1 for i in items if not _is_visible_input(i, css_text))
        if hidden_count:
            out["duplicate_fields"].append({
                "name": n,
                "count": len(items),
                "hidden_count": hidden_count,
            })

    # Fake submit buttons: buttons styled to look like a submit but
    # marked disabled, opacity:0, aria-disabled, etc. (Real submits
    # that are temporarily disabled use the disabled attribute too,
    # so we only flag when other anti-bot signals coexist.)
    for btn in (root.find_all(["button", "input"])
                if hasattr(root, "find_all") else []):
        bt = (btn.get("type") or "").lower()
        if bt != "submit":
            continue
        suspicious_reasons = []
        if btn.get("aria-disabled") == "true":
            suspicious_reasons.append('aria-disabled="true"')
        style = (btn.get("style") or "").lower().replace(" ", "")
        if "pointer-events:none" in style:
            suspicious_reasons.append("pointer-events:none")
        if "opacity:0" in style:
            suspicious_reasons.append("opacity:0")
        if suspicious_reasons:
            if btn.get("id"):
                sel = f"#{btn['id']}"
            elif btn.get("class"):
                # Pre-fix: ".".join(...)[:60] sliced the joined string
                # at exactly 60 chars, which could truncate mid-class-
                # name and produce an invalid CSS selector. Instead,
                # cap by the number of class names kept, then join.
                classes = [c for c in (btn.get("class") or [])
                           if isinstance(c, str) and c][:3]
                sel = btn.name + "." + ".".join(classes) if classes \
                    else btn.name
            else:
                sel = btn.name
            out["fake_submit_buttons"].append({
                "selector": sel,
                "reasons": suspicious_reasons,
            })

    # Bot defenses across the entire (form or page) blob.
    scripts_text = ""
    if hasattr(root, "find_all"):
        scripts_text = " ".join(
            (s.string or s.text or "")[:5000]
            for s in root.find_all("script")
        )
    out["bot_defenses"] = _detect_bot_defenses_in_blob(
        " ".join([full_text, scripts_text]))

    # Time/behavior trap markers in scripts.
    low_scripts = scripts_text.lower()
    for term in ("form_loaded_at", "submitted_too_fast",
                 "minimum_time", "min_submit_time"):
        if term.lower() in low_scripts and term not in out["time_traps"]:
            out["time_traps"].append(term)
    for term in ("mousemove", "behavior_score", "human_check",
                 "interaction_score"):
        if term.lower() in low_scripts \
                and term not in out["behavior_traps"]:
            out["behavior_traps"].append(term)

    return out


CAPTCHA_MARKERS = (
    "g-recaptcha", "recaptcha",
    "hcaptcha", "h-captcha",
    "cf-turnstile", "turnstile",
    "arkoselabs", "funcaptcha",
)


def scan_blockers(html: str, *, base_url: str = "",
                  site_memory: Optional[dict] = None) -> dict:
    """Single API that surfaces every reason the caller should STOP
    rather than continue scraping/auto-submitting:

        {
            "blocked":            bool,      # any blocker present
            "bot_defenses":       [...],     # which systems
            "captchas":           [...],     # captcha types specifically
            "drm_systems":        [...],     # widevine/playready/fairplay/etc.
            "drm_or_encryption":  bool,
            "honeypot_count":     int,
            "duplicate_fields":   int,       # name-collisions, ≥1 hidden
            "warnings":           [...],     # human-readable summary
            "do_not_auto_submit": bool,
            "do_not_download":    bool,
        }

    Treat this as advisory: the orchestrator uses it to gate which
    candidates ship in the final ranked output."""
    out = {
        "blocked": False,
        "bot_defenses": [],
        "bot_defense_systems": [],
        "fingerprinting": [],
        "captchas": [],
        "drm_systems": [],
        "drm_or_encryption": False,
        "honeypot_count": 0,
        "duplicate_fields": 0,
        "warnings": [],
        "do_not_auto_submit": False,
        "do_not_download": False,
    }
    if not html or not isinstance(html, str):
        return out

    blob = html.lower()

    # Bot defenses — uses the existing helper, then we partition into
    # the captcha subset for sharper UI messages.
    out["bot_defenses"] = _detect_bot_defenses_in_blob(html)
    # F9/F10 detect-side: named vendor systems + fingerprinting signals,
    # surfaced for the operator. Report-only; never used to evade.
    out["bot_defense_systems"] = classify_bot_defenses(blob)
    out["fingerprinting"] = detect_fingerprinting_signals(blob)
    out["captchas"] = [m for m in CAPTCHA_MARKERS if m.lower() in blob]
    # Dedup the captcha list — names overlap (e.g. "turnstile" and
    # "cf-turnstile" both fire on the same widget).
    seen, dedup = set(), []
    for c in out["captchas"]:
        base = c.lstrip("cf-").lstrip("g-").lstrip("h-")
        if base in seen:
            continue
        seen.add(base)
        dedup.append(c)
    out["captchas"] = dedup

    # DRM — distinct names of the systems present.
    for m in DRM_MARKERS:
        ml = m.lower()
        if ml in blob:
            out["drm_or_encryption"] = True
            # Normalize the system name. Tag generic markers
            # (ContentProtection, pssh, cenc) under "encrypted".
            if "widevine" in ml:
                name = "widevine"
            elif "playready" in ml:
                name = "playready"
            elif ("fairplay" in ml or "skd://" in ml
                  or "com.apple.fps" in ml):
                name = "fairplay"
            elif "drmtoday" in ml or "drm-today" in ml:
                name = "drmtoday"
            else:
                name = "encrypted"
            if name not in out["drm_systems"]:
                out["drm_systems"].append(name)

    # Honeypot summary — defer to find_honeypots so the rules stay
    # in one place.
    hp = find_honeypots(html)
    out["honeypot_count"] = len(hp.get("honeypot_inputs") or [])
    out["duplicate_fields"] = len(hp.get("duplicate_fields") or [])

    # Build warning strings + the gating flags. CAPTCHA and bot-defense
    # no longer hard-block: they require per-site operator approval
    # before auto-submit, and the choice is remembered. (DRM/encryption
    # remains a separate do_not_DOWNLOAD gate, untouched — that's a
    # content-protection line, not an auto-submit decision.)
    _blocker_reasons = []
    if out["captchas"]:
        _blocker_reasons.append(
            f"CAPTCHA present ({', '.join(out['captchas'])}) "
            "— manual human interaction needed")
    elif out["bot_defenses"]:
        _named = out.get("bot_defense_systems") or out["bot_defenses"]
        _blocker_reasons.append(
            f"bot defense present ({', '.join(_named)}) "
            "— automated download may be detected; manual download "
            "recommended")
    # Fingerprinting is reported even when no captcha/bot-defense vendor
    # name matched — the page actively fingerprints the browser.
    if out.get("fingerprinting"):
        _blocker_reasons.append(
            "browser fingerprinting detected "
            f"({', '.join(out['fingerprinting'])}) — this site profiles "
            "the browser; automation may be flagged")
    if _blocker_reasons:
        _apply_auto_submit_approval(
            out, site_memory=site_memory,
            key=_login_form_key("", base_url),
            why="; ".join(_blocker_reasons))
    if out["drm_or_encryption"]:
        out["do_not_download"] = True
        systems = ", ".join(out["drm_systems"]) or "encrypted"
        out["warnings"].append(
            f"DRM/encryption detected ({systems}); content is "
            "protected — do not attempt to bypass")
    if out["honeypot_count"]:
        out["warnings"].append(
            f"{out['honeypot_count']} honeypot field(s) present; "
            "exclude from any form submission")

    out["blocked"] = bool(
        out["do_not_auto_submit"] or out["do_not_download"])
    return out


def scan_links_for_traps(html: str, *,
                         base_url: str = "") -> List[dict]:
    """Run score_download_link over every anchor/button on the page
    and return the candidates whose score is BELOW zero — i.e. the
    ones the orchestrator should reject. Each entry includes the
    full reason set so the UI can explain rejections."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    rejected = []
    for el in soup.find_all(["a", "button"]):
        r = score_download_link(el, base_url=base_url)
        if r["rejected"]:
            rejected.append(r)
    return rejected


POST_REVEAL_BUTTON_TERMS = (
    "download", "download now", "start download",
    "generate link", "generate", "create link", "get link",
    "continue", "proceed", "unlock", "reveal",
    "prepare download", "free download", "export",
    "request file", "get file", "open download",
)


def detect_post_reveal_forms(html: str, *,
                             base_url: str = "",
                             site_memory: Optional[dict] = None) -> List[dict]:
    """Find <form method="POST"> elements whose submit button has
    download-ish text. Each becomes a workflow candidate:

        [
            {
                "source_type": "two_step_post_reveal",
                "action": str,             # POST endpoint
                "method": "POST",
                "submit_selector": str,
                "submit_text": str,        # what the button says
                "safe_fields": {name: value, ...},   # hidden form values to keep
                "user_fields": [name, ...],          # visible inputs to fill
                "honeypot_fields": [name, ...],      # to drop
                "needs_approval": bool,              # F12: honeypot/challenge markers present
                "approval_status": str,              # F12: not_required|pending|approved|declined
                "bot_defenses": [name, ...],         # F12: bot-defense systems named
                "confidence": int,         # 0-100
                "reasons": [...],
            }
        ]

    The runtime then issues a POST to `action` with `safe_fields` +
    `user_fields` and inspects the response for a redirect or a JSON
    body containing a download URL.

    F12 approval flow: when honeypot/challenge markers are present,
    `needs_approval` is True and `approval_status` is "pending" unless
    the operator has already approved or declined this action on this
    site (read from site_memory). On "pending" the UI prompts the
    operator to approve/decline; record the choice with
    `learn.record_post_reveal_decision` so it is not re-asked. The
    runtime should only auto-submit when approval_status is "approved"
    (or "not_required")."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []
    if not html or not html.strip():
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: List[dict] = []

    # Gather inline CSS once — passed through to _input_is_honeypot so
    # honeypots hidden via `<style>.bot{display:none}</style>` plus
    # class="bot" can be caught. Pre-fix the helper was called with
    # css_text="" and missed every class-styled hidden field.
    css_text = " ".join(
        (s.string or s.text or "") for s in soup.find_all("style"))[:80_000]

    for form in soup.find_all("form"):
        method = (form.get("method") or "GET").upper()
        if method != "POST":
            continue
        action = (form.get("action") or "").strip()
        # Find a submit-shaped element and check its text against the
        # download vocabulary. <button> defaults to type=submit when
        # the attribute is missing, but explicit type=button / reset
        # / image are NOT submits and must be excluded.
        submit_text = ""
        submit_el = None
        for btn in form.find_all(["button", "input"]):
            t = (btn.get("type") or "").lower()
            is_submit = (
                t == "submit"
                or (btn.name == "button" and t in ("", "submit")))
            if is_submit:
                # Buttons: visible text. Inputs: value attribute.
                if btn.name == "button":
                    txt = btn.get_text(" ", strip=True).lower()
                else:
                    txt = (btn.get("value") or "").lower()
                if any(term in txt for term in POST_REVEAL_BUTTON_TERMS):
                    submit_text = txt
                    submit_el = btn
                    break
        if not submit_el:
            continue
        # Don't double-count an obvious login form (password field).
        if any((i.get("type") or "").lower() == "password"
               for i in form.find_all("input")):
            continue

        # Confidence boosters / penalties.
        score = 40
        reasons = [f"button text matches: {submit_text!r}"]
        action_low = action.lower()
        if any(w in action_low for w in
               ("download", "generate", "prepare", "export",
                "reveal", "unlock", "asset", "file")):
            score += 25
            reasons.append("action URL contains download-workflow term")
        # A page that ALREADY exposes a direct file URL doesn't need
        # the workflow — de-emphasize this candidate.
        if re.search(r'https?://[^\s"\'<>]+'
                     r'\.(?:mp4|m3u8|mpd|zip|pdf|exe|dmg|mp3|m4a)',
                     html, re.I):
            score -= 20
            reasons.append("page also exposes a direct file URL "
                           "(workflow likely redundant)")

        # Gather safe/user/honeypot fields. Mirrors the login scorer's
        # field-classifier but with simpler rules — anything hidden
        # with a non-honeypot name is "safe to replay verbatim";
        # visible inputs are "user_fields the runtime should fill or
        # leave blank"; honeypots are dropped.
        safe_fields: Dict[str, str] = {}
        user_fields: List[str] = []
        honeypot_fields: List[str] = []
        for inp in form.find_all("input"):
            name = (inp.get("name") or "").strip()
            if not name:
                continue
            t = (inp.get("type") or "text").lower()
            if _input_is_honeypot(inp, css_text):
                honeypot_fields.append(name)
                continue
            if t in ("hidden",):
                safe_fields[name] = inp.get("value", "")
            elif t in ("submit", "button", "image", "reset"):
                continue
            else:
                user_fields.append(name)

        # Submit selector.
        if submit_el.get("id"):
            submit_sel = f"#{submit_el['id']}"
        elif submit_el.get("class"):
            submit_sel = (submit_el.name + "."
                          + ".".join(submit_el.get("class")[:2]))
        else:
            submit_sel = f"{submit_el.name}[type='submit']"

        # F12 caveat: when honeypot fields or bot-defense markers are
        # present, this workflow needs the operator's explicit approval
        # before the runtime auto-submits it. We don't hard-block — we
        # surface an approval prompt and remember the operator's choice
        # per site so it isn't re-asked. A previously-saved decision in
        # site_memory short-circuits the prompt.
        form_blob = " ".join(
            [_form_text(form), css_text]).lower()
        challenge_markers = _detect_bot_defenses_in_blob(form_blob)
        needs_approval = bool(honeypot_fields) or bool(challenge_markers)
        action_url = decode_url(action, base_url=base_url) if action else ""
        approval_status = "not_required"
        if needs_approval:
            approval_status = _post_reveal_saved_decision(
                site_memory, action_url) or "pending"
            if honeypot_fields:
                reasons.append(
                    f"honeypot fields present {honeypot_fields}; "
                    "needs operator approval before auto-submit")
            if challenge_markers:
                reasons.append(
                    f"bot-defense markers {challenge_markers}; "
                    "needs operator approval before auto-submit")

        out.append({
            "source_type": "two_step_post_reveal",
            "action": action_url,
            "method": "POST",
            "submit_selector": submit_sel,
            "submit_text": submit_text,
            "safe_fields": safe_fields,
            "user_fields": user_fields,
            "honeypot_fields": honeypot_fields,
            "needs_approval": needs_approval,
            "approval_status": approval_status,  # not_required|pending|approved|declined
            "bot_defenses": challenge_markers,
            "confidence": max(0, min(100, score)),
            "reasons": reasons,
        })

    return out


def _post_reveal_saved_decision(site_memory, action_url):
    """Return the operator's saved approve/decline decision for this
    POST-reveal action on this site, or None if not yet decided.

    Reads site_memory["post_reveal_decisions"][key] where key is the
    normalized action URL (see _post_reveal_key). Returns
    "approved" | "declined" | None. Tolerant of missing / malformed
    site_memory (read-path no-op, matching the rest of deep_detect)."""
    if not isinstance(site_memory, dict):
        return None
    decisions = site_memory.get("post_reveal_decisions")
    if not isinstance(decisions, dict):
        return None
    rec = decisions.get(_post_reveal_key(action_url))
    if not isinstance(rec, dict):
        return None
    d = rec.get("decision")
    if d == "approve":
        return "approved"
    if d == "decline":
        return "declined"
    return None


def _post_reveal_key(action_url):
    """Normalize a POST-reveal action URL into a stable per-site key:
    scheme+host+path, query/fragment stripped (signed query params are
    request-specific and would fragment the key). Empty string for a
    falsy URL."""
    if not action_url:
        return ""
    try:
        from urllib.parse import urlsplit
        p = urlsplit(action_url)
        host = (p.netloc or "").lower()
        return f"{host}{p.path}" if host else (p.path or action_url)
    except Exception:
        return action_url


def _auto_submit_saved_decision(site_memory, key):
    """Return the operator's saved approve/decline decision for an
    auto-submit-gated surface (login form / page blocker), or None.

    Reads site_memory["auto_submit_decisions"][key]. Returns
    "approved" | "declined" | None. Tolerant of missing / malformed
    site_memory — read-path no-op, matching the rest of deep_detect.
    The post-reveal surface keeps its own dedicated store
    (post_reveal_decisions); this one covers everything else that used
    to hard-set do_not_auto_submit."""
    if not isinstance(site_memory, dict) or not key:
        return None
    decisions = site_memory.get("auto_submit_decisions")
    if not isinstance(decisions, dict):
        return None
    rec = decisions.get(key)
    if not isinstance(rec, dict):
        return None
    d = rec.get("decision")
    if d == "approve":
        return "approved"
    if d == "decline":
        return "declined"
    return None


def _apply_auto_submit_approval(out, *, site_memory, key, why):
    """Convert a hard 'do_not_auto_submit=True' gate into a per-site
    approve/decline-and-remember decision.

    `out` is the result dict being built (a login-form score or a
    blocker scan). `key` is the stable per-site key for this surface
    (e.g. the form action's host+path, or the page host for a
    page-level blocker). `why` is a short human reason
    (e.g. "bot defense: cloudflare").

    Behavior:
      • sets out["needs_approval"] = True
      • out["approval_status"] = "approved" | "declined" | "pending"
        from any saved decision in site_memory
      • out["do_not_auto_submit"] stays True UNLESS the operator has
        already approved this surface on this site — so the default is
        always the safe one (no auto-submit until explicitly approved),
        and a saved approval opens the gate without re-prompting.

    The UI shows an approve/decline notification while status is
    "pending"; the choice is persisted via
    learn.record_auto_submit_decision so it isn't re-asked."""
    status = _auto_submit_saved_decision(site_memory, key) or "pending"
    out["needs_approval"] = True
    out["approval_key"] = key
    out["approval_status"] = status
    out["do_not_auto_submit"] = (status != "approved")
    note = f"{why}; "
    if status == "approved":
        note += "operator-approved for this site — auto-submit allowed"
    elif status == "declined":
        note += "operator-declined for this site — will not auto-submit"
    else:
        note += "awaiting operator approval before auto-submit"
    out.setdefault("warnings", []).append(note)
    return out


def _login_form_key(action_url, base_url=""):
    """Per-site key for a login form's auto-submit approval. Resolves
    the form action against base_url first so a root-relative action
    (e.g. '/login') still keys per host rather than colliding across
    every site that posts to the same path. Falls back to the page
    host when there's no action (submit-to-self / page-level)."""
    resolved = ""
    if action_url:
        try:
            from urllib.parse import urljoin
            resolved = urljoin(base_url or "", action_url)
        except Exception:
            resolved = action_url
    key = _post_reveal_key(resolved)
    # If the action had no host and base_url couldn't supply one, the
    # key may still be path-only; prefer the page host in that case.
    if key and "/" in key and not key.split("/", 1)[0]:
        key = ""
    if key:
        return key
    return _post_reveal_key(base_url)
