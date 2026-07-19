"""PHC-1 (B1) follow-up: the cockpit must actually *send* a CSRF token on its writes.

The @531 cut extended the global CSRF/origin guard to cover `/cockpit/api/`
writes (good). But the cockpit shell's `api()` fetch helper attaches
`X-CSRF-Token: csrf()`, where `csrf()` reads a `meta[name="csrf-token"]` tag --
and the cockpit `_PAGE` head never embeds one. So `api()` sends an EMPTY token,
and every `/cockpit/api/` write the cockpit issues now 403s in a real browser
session (a browser that has loaded the SPA carries the `bd_session` cookie that
`/api/csrf` mints unconditionally with path=/).

RED-first on pristine v3.66.531:
  * `test_cockpit_can_supply_csrf_token_for_writes` -- the cockpit shell can
    produce a NON-empty CSRF token for its writes. This is true iff EITHER the
    `_PAGE` head embeds a `csrf-token` meta tag, OR the `api()` helper self-mints
    from `/api/csrf` (as `apiRoot()` already does). On 531 NEITHER holds -> RED.
After the fix (api() self-mints, mirroring apiRoot()) -> GREEN.

The other two are end-to-end / coverage assurances (green before and after) that
pin the contract so it can't silently regress again.
"""
import os
import re

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def _repo_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def _cockpit_src():
    p = os.path.join(_repo_root(), "tools", "cockpit_console.py")
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def _extract_js_fn(src, name):
    """Return the body of the embedded JS `async function <name>(...){ ... }`
    by brace-matching from its opening brace. Returns '' if not found."""
    m = re.search(r"async function %s\s*\([^)]*\)\s*\{" % re.escape(name), src)
    if not m:
        return ""
    i = m.end() - 1  # at the '{'
    depth = 0
    for j in range(i, len(src)):
        ch = src[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    return ""


# --- THE RED GUARD --------------------------------------------------------
def test_cockpit_can_supply_csrf_token_for_writes():
    src = _cockpit_src()
    # Does the cockpit page embed a server-resolvable csrf-token meta tag?
    has_meta = re.search(
        r'<meta[^>]*name=["\']csrf-token["\']', src) is not None
    # Does the write helper api() self-mint a token (fetch /api/csrf)?
    api_body = _extract_js_fn(src, "api")
    assert api_body, "could not locate the cockpit `api()` JS helper"
    api_self_mints = "/api/csrf" in api_body
    assert has_meta or api_self_mints, (
        "the cockpit shell cannot supply a non-empty CSRF token for its writes: "
        "the _PAGE head embeds no csrf-token meta tag AND api() does not self-mint "
        "from /api/csrf, so every /cockpit/api/ write sends an empty X-CSRF-Token "
        "and 403s once the @531 gate covers /cockpit/api/.")


# --- end-to-end assurance: the token api() *should* send is accepted -------
def test_cockpit_write_with_minted_token_is_not_csrf_refused():
    from bulk_downloader.app import app
    c = app.test_client()
    # SPA load mints bd_session (path=/) and returns the bound CSRF token.
    r = c.get("/api/csrf")
    assert r.status_code == 200
    tok = (r.get_json() or {}).get("csrf_token") or ""
    assert tok, "/api/csrf should return a csrf_token"
    # A same-origin cockpit write carrying that token must NOT be CSRF-refused.
    w = c.post("/cockpit/api/ui_prefs",
               headers={"Origin": "http://localhost", "Host": "localhost",
                        "X-CSRF-Token": tok},
               json={"x": 1})
    assert w.status_code != 403, (
        f"cockpit write with a valid minted token must not be CSRF-refused; "
        f"got {w.status_code}: {w.get_data(as_text=True)[:200]}")


# --- coverage assurance: cockpit writes all route through api() -----------
def test_cockpit_api_writes_route_through_csrf_helper():
    src = _cockpit_src()
    # Every cockpit-issued `/api/...` write goes through api( (which the fix
    # makes self-mint). A raw fetch('/cockpit/api/...', {method:POST}) would
    # bypass the helper and re-introduce the empty-token bug.
    raw = re.findall(
        r"fetch\(\s*['\"]/cockpit/api/[^'\"]+['\"][^)]*method\s*:\s*['\"](?:%s)"
        % "|".join(WRITE_METHODS), src)
    assert raw == [], (
        "found raw fetch() cockpit writes that bypass the api() CSRF helper: "
        + "; ".join(raw))
    # And api() is the helper used for writes -> it must exist.
    assert _extract_js_fn(src, "api"), "cockpit api() helper missing"
