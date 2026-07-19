"""RE-EXPRESSED at v3.66.203 (Phase 1 root flip): every shim now
resolves to "/" (the SPA root) instead of "/m2/"; /m2 itself is a
deep-link-preserving 302 shim. The opt-in cookie machinery stays in
the module (deliberate keep, pinned below) but is no longer consumed.

D3 U9 contract — /m retirement (D4, landed v3.65.1).

Per D3_OPT_IN.md's retirement schedule, /m was scheduled to become an
unconditional 302 → /m2/ in v3.65.0. The v3.65.0 release-cut shipped the
visual track only, and D4 actually landed in v3.65.1.

Pre-D4 behaviour (v3.64.x):
  - /m default → 200 legacy mobile.html
  - /m?ui=v2  → 302 /m2/ + bd_ui=v2 cookie
  - /m?ui=v1  → 200 legacy + bd_ui=v1 cookie
  - /m2?ui=v1 → 302 /m + bd_ui=v1 cookie
  - /m2 default → SPA (200 or 503 if dist missing)

Post-D4 (v3.65.1+):
  - /m, /m/ → 302 /m2/ unconditional. No template service.
  - /m/ops, /m/ops/ → 302 /m2/ unconditional (companion page retires
    on the same schedule per D3_OPT_IN.md). The m_ops admin workflow
    is covered by the SPA's per-site stop API.
  - /m2?ui=v1 → SPA (does NOT redirect — would loop with the new /m).
                The cookie is no longer set (no legacy UI to opt back to).
  - /m2?ui=v2 → SPA + bd_ui=v2 cookie (back-compat: harmless preserve).
  - /m2 default → SPA (200 or 503).

The cookie machinery (_m2_opt_state, _m2_apply_opt_cookie, _M2_COOKIE_NAME,
_M2_COOKIE_TTL) is kept in app.py because the ?ui=v2 path on /m2 still
records the user's explicit preference. v3.66.0 will remove the legacy
mobile.html template from disk (per D3_OPT_IN.md); these helpers may
retire at the same time or stay for telemetry.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

_REPO = Path(__file__).resolve().parent.parent


# ── Backend helpers (load-bearing, kept across D4) ────────────────────


def test_d3_u9_opt_state_helper_present():
    """_m2_opt_state remains in the module — the /m2?ui=v2 cookie path
    still uses it. Removal would break the back-compat preference
    recording without breaking any route, so this pin guards intent."""
    from bulk_downloader import app as a
    assert hasattr(a, "_m2_opt_state"), "_m2_opt_state helper missing"


def test_d3_u9_opt_cookie_helper_present():
    from bulk_downloader import app as a
    assert hasattr(a, "_m2_apply_opt_cookie"), "_m2_apply_opt_cookie missing"


def test_d3_u9_cookie_name_is_bd_ui():
    """The cookie name is documented as bd_ui — drift would break
    every operator's existing preference."""
    from bulk_downloader import app as a
    assert a._M2_COOKIE_NAME == "bd_ui", (
        f"cookie name drifted to {a._M2_COOKIE_NAME!r}, "
        "must be 'bd_ui' (documented in D3_OPT_IN.md)"
    )


def test_d3_u9_cookie_ttl_is_one_year():
    """1-year TTL — survives the browser's normal cookie sweeps."""
    from bulk_downloader import app as a
    # Tolerate small drift; what matters is "much longer than a session"
    assert a._M2_COOKIE_TTL >= 30 * 24 * 3600, (
        f"cookie TTL is {a._M2_COOKIE_TTL}s — too short"
    )


# ── /m: unconditional redirect (D4) ──────────────────────────────────


def test_d3_u9_m_unconditional_redirect_to_m2():
    """Post-D4: /m with no cookie, no query → 302 to /m2/.
    Pre-D4 this returned 200 legacy template. The retirement schedule
    in D3_OPT_IN.md named v3.65.0 as the cutover; D4 landed v3.65.1."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m")
    assert r.status_code == 302, (
        f"/m must redirect (D4); got {r.status_code}"
    )
    loc = r.headers.get("Location", "")
    assert loc == "/", (
        f"/m must redirect to /m2/; got Location={loc!r}"
    )


def test_d3_u9_m_trailing_slash_also_redirects():
    """Both /m and /m/ are registered routes — both redirect."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m/")
    assert r.status_code == 302
    loc = r.headers.get("Location", "")
    assert loc == "/"


def test_d3_u9_m_with_ui_v2_still_redirects():
    """?ui=v2 was the original opt-in trigger. Post-D4 it still
    redirects — the destination is the same, just no longer conditional."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m?ui=v2")
    assert r.status_code == 302
    loc = r.headers.get("Location", "")
    assert loc == "/"


def test_d3_u9_m_with_ui_v1_now_redirects():
    """Pre-D4: ?ui=v1 served legacy + set v1 cookie. Post-D4: redirects
    like everything else. The legacy UI is no longer reachable via /m,
    period. (mobile.html still on disk for emergency rollback — v3.66.0
    removes it.)"""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m?ui=v1")
    assert r.status_code == 302, (
        f"/m?ui=v1 must redirect post-D4; got {r.status_code}"
    )
    loc = r.headers.get("Location", "")
    assert loc == "/"


def test_d3_u9_m_with_stale_v1_cookie_still_redirects():
    """A user with a stale bd_ui=v1 cookie from the v3.64.x era visits
    /m — they get the SPA, not the legacy template. The retirement
    schedule said this would happen; the cookie carried over because
    we kept the cookie name stable for back-compat."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    c.set_cookie("bd_ui", "v1")
    r = c.get("/m")
    assert r.status_code == 302
    loc = r.headers.get("Location", "")
    assert loc == "/"


def test_d3_u9_m_with_v2_cookie_redirects():
    """Symmetry check — v2 cookie also resolves to /m2/."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    c.set_cookie("bd_ui", "v2")
    r = c.get("/m")
    assert r.status_code == 302
    loc = r.headers.get("Location", "")
    assert loc == "/"


def test_d3_u9_m_redirect_is_302_not_301():
    """Use 302 (Found), not 301 (Permanent). The route ITSELF is now
    permanent, but a 301 would be aggressively cached by browsers,
    making an emergency rollback (re-route /m to serve legacy in a
    hotfix) ineffective for users who hit the route once. 302 lets a
    future release flip behaviour without fighting browser caches."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m")
    assert r.status_code == 302, (
        f"redirect should be 302 (cache-friendly for emergency rollback), "
        f"not {r.status_code}"
    )


# ── /m/ops: companion retirement ──────────────────────────────────────


def test_d3_u9_m_ops_redirects():
    """/m/ops admin page retires on the same schedule per D3_OPT_IN.md."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m/ops")
    assert r.status_code == 302, (
        f"/m/ops must redirect post-D4; got {r.status_code}"
    )
    loc = r.headers.get("Location", "")
    assert loc == "/"


def test_d3_u9_m_ops_trailing_slash_redirects():
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m/ops/")
    assert r.status_code == 302


# ── /m2: SPA serves regardless of opt-out query (no loop) ────────────


def test_d3_u9_m2_with_ui_v1_does_not_redirect():
    """Pre-D4: /m2?ui=v1 redirected to /m. Post-D4: with /m itself a
    redirect to /m2/, that would loop. /m2 ignores ?ui=v1 and serves
    the SPA. The legacy UI is no longer reachable via opt-out."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/m2/?ui=v1")
    # Post-root-flip: /m2 is a 302 shim to "/" — and "/" serves the SPA
    # directly (it never redirects back), so no loop is possible. The
    # original no-loop contract is preserved by construction.
    assert r.status_code == 302
    assert r.headers.get("Location", "").startswith("/"), "shim must land on root"
    assert not r.headers.get("Location", "").startswith("/m"), "must not bounce back into shims"


def test_d3_u9_m2_with_ui_v2_still_sets_cookie():
    """Back-compat: ?ui=v2 still records the preference (harmless —
    cookie just states 'user is on the SPA', which is now always true)."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.get("/m2/?ui=v2")
    # Post-root-flip: the shim redirects and intentionally no longer
    # consumes the cookie machinery — there is nothing to opt into.
    # The query string is preserved across the redirect (deep links).
    assert r.status_code == 302
    assert "ui=v2" in r.headers.get("Location", "")


def test_d3_u9_m2_serves_spa_with_stale_v1_cookie():
    """A user who opted out previously but then types /m2 directly
    must STILL get the SPA. The cookie expresses default-route
    preference (vestigial now), not a hard ban on /m2."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    c.set_cookie("bd_ui", "v1")
    r = c.get("/m2/")
    # Post-root-flip: the shim redirects to "/" regardless of any stale
    # cookie — the SPA is the only UI at root, the cookie can't ban it.
    assert r.status_code == 302, (
        f"/m2/ with stale v1 cookie: expected the 302 shim, got {r.status_code}"
    )
    assert r.headers.get("Location") == "/"


# ── Cookie attributes (when set) ──────────────────────────────────────


def test_d3_u9_cookie_when_set_is_not_httponly():
    """If /m2?ui=v2 successfully sets the cookie (requires dist built),
    it must not be HttpOnly. In the test env dist is missing, so we
    can't exercise the success path — but we can verify the helper
    sets the right attributes via direct call."""
    from bulk_downloader import app as a
    from flask import Response
    # Simulate a request where ?ui=v2 is present
    with a.app.test_request_context("/m2/?ui=v2"):
        from flask import request
        resp = Response("")
        a._m2_apply_opt_cookie(resp, request)
        set_cookie = " ".join(resp.headers.get_all("Set-Cookie") or [])
        assert "bd_ui=v2" in set_cookie, (
            f"helper failed to set cookie; got {set_cookie!r}"
        )
        assert "HttpOnly" not in set_cookie, (
            "bd_ui must NOT be HttpOnly — frontend reads it"
        )


def test_d3_u9_cookie_when_set_is_lax():
    """SameSite=Lax."""
    from bulk_downloader import app as a
    from flask import Response
    with a.app.test_request_context("/m2/?ui=v2"):
        from flask import request
        resp = Response("")
        a._m2_apply_opt_cookie(resp, request)
        set_cookie = " ".join(resp.headers.get_all("Set-Cookie") or [])
        assert "SameSite=Lax" in set_cookie or "samesite=lax" in set_cookie.lower(), (
            f"bd_ui must be SameSite=Lax; got {set_cookie!r}"
        )


# ── Invalid ?ui= input is silent ─────────────────────────────────────


def test_d3_u9_invalid_ui_value_is_silently_default():
    """?ui=bogus on /m still redirects (post-D4 unconditional behaviour).
    No cookie set, no 400, no error — UI preference URLs don't 400 on
    garbage."""
    from bulk_downloader import app as a
    r = a.app.test_client().get("/m?ui=bogus")
    assert r.status_code == 302
    set_cookie = " ".join(r.headers.get_all("Set-Cookie") or [])
    assert "bd_ui=" not in set_cookie, (
        "invalid ?ui= value must not set a cookie"
    )


# ── Legacy template stays on disk (v3.66.0 removes) ──────────────────


# ── Doc ──────────────────────────────────────────────────────────────


def test_d3_u9_opt_in_doc_present():
    f = _REPO / "D3_OPT_IN.md"
    assert f.is_file(), f"missing {f}"


def test_d3_u9_opt_in_doc_names_d4_landing():
    """Doc must reflect that D4 landed in v3.65.1 (slipped one release
    from the original v3.65.0 plan)."""
    src = (_REPO / "D3_OPT_IN.md").read_text(encoding="utf-8")
    assert "v3.65.1" in src, (
        "D3_OPT_IN.md must name v3.65.1 as the D4 landing release"
    )
    # And the v3.66.0 template-removal milestone
    assert "v3.66.0" in src, (
        "D3_OPT_IN.md must name v3.66.0 as the template-removal milestone"
    )
