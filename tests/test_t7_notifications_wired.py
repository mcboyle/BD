"""Current notifications, Telegram, and alerts SPA contract -- EXECUTED.

WHAT THIS FILE USED TO BE, AND WHY IT CHANGED (backlog row 188). Four of its
tests judged RUNTIME properties by substring search over ``frontend/src``:

* the 7 endpoint families were "wired" if ``"/api/tg/status"`` appeared in
  useNotificationsData.ts;
* /notifications was "routed and nav-linked" if ``path="/notifications"``
  appeared in App.tsx;
* the secret inputs were "write-only" if ``useState("")`` appeared in
  Notifications.tsx;
* writes were "never one-click" if a regex over ``onClick={...}`` found no
  ``saveTg.mutate``.

MEASURED, not argued: a six-mutant battery aimed at those four scans on base
51bb41d scored 0 CAUGHT, 6 ESCAPED. Every mutant is a real behavioural break
that the scans consent to -- the token seeded straight out of the GET payload
(two other ``useState("")`` sites survive as decoys), the tg/status queryFn
stubbed with the literal preserved in a trailing comment, the route repathed
with the old path restored in a JSX comment, a one-click write reached through
``[saveTg].map((m) => m.mutate(...))`` so the regex's literal never appears.
A gate with zero discriminating power over its own four claims.

WHAT IT IS NOW. The same four claims are exercised against the real components
through Vitest, on the pattern the T1/T9a/T9b/T10/T11 gates already use, via the
fail-closed bridge in ``tests/frontend_vitest.py``:

* ``Notifications.endpoints.test.tsx``  -- the route is mounted and driven, and
  the paths the transport was actually handed are reconciled to the seven
  families by SET EQUALITY, so a dropped family and an undeclared new one both
  go red;
* ``Notifications.writeonly.test.tsx``  -- the query cache is POISONED with a
  GET payload carrying both secrets before render, and the inputs' VALUES are
  asserted empty; it carries a negative control proving that assertion rejects a
  seeded field, and an over-sensitivity control proving a blank save still omits
  the secret keys rather than wiping the stored ones;
* ``Notifications.confirm.test.tsx``    -- all four gated writes are clicked for
  real and the transport is asked whether anything was sent BEFORE the dialog is
  confirmed, plus a cancel-posts-nothing case;
* ``Notifications.route.test.tsx``      -- /notifications is resolved through the
  REAL App <Routes> table (not a hand-placed component), with a negative control
  at another path, and the command palette's item is SELECTED and the resulting
  pathname asserted.

The lazy-chunk half of claim (b) is proved by the Vite manifest, and the two
backend claims stay in-process here, unchanged.

WHAT IS STILL NOT CONSTRAINED, stated rather than implied by a green run:
the specs mock ``@/lib/api-client``, so nothing here judges CSRF headers, auth,
or the server contract; the sidebar entry in ``lib/navGroups.ts`` is a second
inbound path this gate does not cover (row 188 names the palette);
``validateApprise`` is genuinely one-click today and deliberately left that way
by a gate-hardening cut; and the seven-family list is pinned, so a deliberate
co-edited expansion consents while a drop or a drift does not.

run_tests.py conventions: zero-arg test functions; repo root from __file__;
no pytest builtins.
"""
import json

from tests.frontend_vitest import build_manifest, run_vitest

BD_GATE_SCOPE = "repo-wide"

# MEASURED, NOT GUESSED. Each count is what Vitest reported for that spec on
# test5 at this candidate; run_vitest asserts passed == collected == expected,
# so a spec that silently loses a test fails closed rather than shrinking its
# own denominator.
_SPEC_DENOMINATORS = {
    "src/routes/Notifications.endpoints.test.tsx": 3,
    "src/routes/Notifications.writeonly.test.tsx": 6,
    "src/routes/Notifications.confirm.test.tsx": 5,
    "src/routes/Notifications.route.test.tsx": 3,
}


def test_t7_endpoint_families_are_consumed_at_runtime():
    """The 7 notify/tg/alerts families are CALLED, not merely named."""
    spec = "src/routes/Notifications.endpoints.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t7_secrets_are_write_only_at_runtime():
    """(R) rule: with a GET payload carrying both secrets preloaded into the
    query cache, neither write-only input holds a value."""
    spec = "src/routes/Notifications.writeonly.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t7_writes_are_confirmation_gated_at_runtime():
    """Every save/test write is clicked and observed to send nothing until the
    confirmation dialog is confirmed."""
    spec = "src/routes/Notifications.confirm.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t7_route_is_reachable_at_runtime():
    """/notifications resolves through the real App route table, and selecting
    the palette item lands on that pathname."""
    spec = "src/routes/Notifications.route.test.tsx"
    run_vitest(spec, expected_tests=_SPEC_DENOMINATORS[spec])


def test_t7_route_is_a_lazy_dynamic_entry():
    """The BUILD half of claim (b), which no jsdom render can answer.

    A separate nodeid on purpose. tests/test_t1_dashboard_wired.py folds
    build_manifest() into its run_vitest test; that is the wrong shape to copy,
    because build_manifest runs `tsc -b` plus a full `vite build` (~36s measured)
    and a mutation battery re-runs its band once per mutant. Kept separate, the
    battery bands on the four Vitest nodeids and never pays for a build.
    """
    manifest = build_manifest()
    entry = manifest.get("src/routes/Notifications.tsx")
    assert isinstance(entry, dict), (
        "src/routes/Notifications.tsx is absent from the Vite manifest, so this "
        "gate cannot see the subject it claims to judge")
    assert entry.get("isDynamicEntry") is True, (
        "Notifications must remain a lazy, separately built route; the manifest "
        "entry reports isDynamicEntry=%r" % (entry.get("isDynamicEntry"),))


def test_t7_backend_apprise_get_masks_urls():
    """The legacy GET /api/notify/apprise/settings no longer echoes raw
    apprise URLs (PREP_AUDIT §8 leak). RED on pristine 209."""
    import os
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader.app import (
        app, _load_global_notify_settings, _save_global_notify_settings)
    cfg = _load_global_notify_settings()
    cfg["notify_apprise_urls"] = "tgram://111:SECRETTOKEN/222"
    _save_global_notify_settings(cfg)
    body = app.test_client().get("/api/notify/apprise/settings").get_data(as_text=True)
    assert "SECRETTOKEN" not in body, "raw apprise token leaked in GET"
    j = json.loads(body)
    s = j["settings"]
    assert "notify_apprise_urls" not in s, "raw URLs still echoed"
    assert s.get("notify_apprise_urls_set") is True
    assert s.get("notify_apprise_urls_count") == 1


def test_t7_sensitive_qs_key_covers_code_and_k():
    """SENSITIVE_QS_KEY folds in the code (vix) / k (bang) analytics keys —
    exact-match only (no geocode/key/kind over-match). RED on 209."""
    from bulk_downloader.capture_redact import SENSITIVE_QS_KEY as R
    assert R.search("code") and R.search("k")
    assert not R.search("geocode")
    assert not R.search("zipcode")
    assert not R.search("kind")
