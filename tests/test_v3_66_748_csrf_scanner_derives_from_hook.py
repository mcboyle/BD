"""v3.66.748 — the CSRF column must be DERIVED from the hook, not mirrored.

AUDIT ROUND 2, findings R11/R12/R13. The highest-severity item in that audit,
and the fix it asked for is a test, not a patch.

THE DRIFT. `_check_csrf` (bulk_downloader/app.py) gates:
    if not path.startswith(("/api/", "/cockpit/api/")): return None
The scanner every artifact calls (tools/build_endpoint_catalog.py
`_csrf_fires_for`) re-typed that rule and never learned the second prefix:
    if not path.startswith("/api/"): return False

So 28 cockpit write endpoints are PROTECTED by the app and reported
`csrf: false` by ROUTE_INDEX.json, ENDPOINT_CATALOG.md, and — the one that
ships — the OpenAPI spec served at /api/openapi.json, which therefore OMITS
the required X-CSRF-Token header and the 403 response for them. A generated
client breaks on its first cockpit write; a security reviewer reads the spec
and concludes cockpit is unprotected. It is not.

THE PART THAT MATTERS MOST (R13). Because the artifact ALREADY says
`csrf: false`, a real regression — someone drops `/cockpit/api/` from the hook
— changes NO artifact, diffs NOTHING, and trips NO gate. The mirror being
wrong in the safe direction has burned down the alarm for a move in the
dangerous one. A gate that is wrong-but-reassuring cannot detect the thing it
exists to detect.

THE FIX, in the program's own idiom: DON'T MIRROR — DERIVE. The app exports its
route-level CSRF policy (`CSRF_GUARDED_PREFIXES`, `CSRF_EXEMPT_PATHS`,
`CSRF_TRIPPING_METHODS`) as the single source of truth; the hook itself reads
them, and the scanner imports them. A predicate that cannot be re-typed cannot
drift.

And because "derived" is a claim, this file PROVES it two ways:
  1. Structurally: the scanner's constants ARE the app's objects (identity).
  2. Behaviourally: for every mutating route on the live app, the scanner's
     verdict matches what the REAL hook actually does to a cookie-session
     request with no token — 403 or not. Drive the hook; don't trust the label.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _bec():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    sys.path.insert(0, os.path.join(_ROOT, "tools"))
    import build_endpoint_catalog as BEC
    BEC._bind_csrf_policy()   # policy binds lazily (see the module's comment)
    return BEC


def test_the_scanner_does_not_keep_its_own_copy_of_the_policy():
    """STRUCTURAL. The scanner's CSRF constants must BE the app's — not equal
    to them, but the same objects. Equality would still permit a fork that
    drifts on the next edit; identity makes drift impossible to express."""
    import bulk_downloader.app as A
    BEC = _bec()

    assert BEC._CSRF_GUARDED_PREFIXES is A.CSRF_GUARDED_PREFIXES, (
        "the scanner keeps its own copy of the guarded-prefix rule — that copy "
        "is what drifted (cockpit writes reported csrf:false while the app 403s "
        "them). Import the app's policy; do not re-type it."
    )
    assert BEC._CSRF_EXEMPT_PATHS is A.CSRF_EXEMPT_PATHS
    assert BEC._CSRF_TRIPPING_METHODS is A.CSRF_TRIPPING_METHODS


def test_cockpit_writes_are_reported_as_csrf_guarded():
    """The concrete R11 regression, pinned. The app gates /cockpit/api/ writes;
    the scanner must say so."""
    BEC = _bec()
    assert BEC._csrf_fires_for("POST", "/cockpit/api/collections") is True
    assert BEC._csrf_fires_for("POST", "/cockpit/api/escalations") is True
    # and the declared exemption is still exempt
    assert BEC._csrf_fires_for("POST", "/api/pair/redeem") is False
    # reads never trip it
    assert BEC._csrf_fires_for("GET", "/cockpit/api/collections") is False
    # non-api paths are not guarded
    assert BEC._csrf_fires_for("POST", "/cockpit/ui") is False


def test_scanner_verdict_matches_the_REAL_hook_for_every_mutating_route():
    """BEHAVIOURAL — the alarm the audit actually asked for.

    For every mutating route on the live app, drive the REAL request path with
    a cookie session and no CSRF token, and compare what happens (403 or not)
    to what the scanner predicts. This is the test that fails the day someone
    edits _check_csrf and the scanner does not follow — which is precisely the
    regression that is invisible today.

    Note the asymmetry we tolerate: the scanner is a ROUTE-LEVEL predicate, so
    it cannot know about per-request escapes (Bearer auth, absent session).
    We hold those constant — cookie session, no bearer, no Origin — so the only
    variable left is the route policy, which is exactly what the scanner claims
    to model.
    """
    BEC = _bec()
    from bulk_downloader.app import app

    c = app.test_client()
    c.get("/")  # establish a bd_session cookie the way a browser would

    mismatches = []
    checked = 0
    for rule in app.url_map.iter_rules():
        path = str(rule)
        if "<" in path:
            continue  # no generic valid value for a parameterized route
        for method in sorted(rule.methods - {"GET", "HEAD", "OPTIONS"}):
            if method not in ("POST", "PUT", "PATCH", "DELETE"):
                continue
            predicted = BEC._csrf_fires_for(method, path)
            r = c.open(path, method=method, json={})
            # 403 with the CSRF marker == the hook fired. Anything else (400,
            # 404, 200, 500...) means it did not fire on THIS request.
            body = (r.get_data(as_text=True) or "").lower()
            actually_fired = (r.status_code == 403 and "csrf" in body)
            checked += 1
            if predicted != actually_fired:
                mismatches.append(
                    f"{method} {path}: scanner says csrf={predicted}, "
                    f"the hook returned {r.status_code} "
                    f"(fired={actually_fired})")

    assert checked > 50, (
        f"only {checked} mutating routes were driven — the cross-validation "
        "denominator collapsed; a check that inspects almost nothing reports "
        "clean truthfully and uselessly"
    )
    assert not mismatches, (
        f"{len(mismatches)} of {checked} mutating routes: the scanner's CSRF "
        "verdict DISAGREES with what the real hook does. Every artifact "
        "(ROUTE_INDEX, ENDPOINT_CATALOG, and the SHIPPED OpenAPI spec) carries "
        "the scanner's answer.\n  " + "\n  ".join(mismatches[:12])
    )


def test_the_cross_validation_can_actually_fail():
    """A gate that can only pass is not a gate (the @738 lesson). Perturb the
    derived policy and confirm the predicate's verdict moves with it — i.e.
    this file is wired to the thing it claims to check."""
    BEC = _bec()

    import bulk_downloader.app as A

    before = BEC._csrf_fires_for("POST", "/cockpit/api/collections")
    original = A.CSRF_GUARDED_PREFIXES
    try:
        # Replant the DRIFTED rule at the source of truth. Because the scanner
        # delegates to the app's predicate, perturbing the app must move the
        # scanner's verdict -- if it does not, the "derivation" is decorative.
        A.CSRF_GUARDED_PREFIXES = ("/api/",)
        after = BEC._csrf_fires_for("POST", "/cockpit/api/collections")
    finally:
        A.CSRF_GUARDED_PREFIXES = original

    assert before is True and after is False, (
        "the scanner's verdict did not move when the APP's policy was "
        "perturbed — it is not deriving from the hook, it is still mirroring it"
    )
