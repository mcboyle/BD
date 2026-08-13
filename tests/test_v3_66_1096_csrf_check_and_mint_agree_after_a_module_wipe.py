"""CSRF mint and check must land on the same key after a module re-execution.

BACKLOG 92/93. `_csrf_key` is module-level in `bulk_downloader/app.py`, so a
fresh module EXECUTION mints a NEW one. The two halves of the double-submit
pattern then disagree about which module they belong to:

  * MINT is late. `app_csrf.py` and `app_pair.py` resolve `_csrf_token_for`
    through `importlib.import_module("bulk_downloader.app")` at call time, so
    they always reach the module that is CURRENTLY installed in sys.modules.
  * CHECK was early. `_check_csrf` lives in app.py and called
    `_csrf_token_for(sess)` through its OWN module globals, so a stale module
    object -- one a test file still holds a reference to -- kept using the key
    minted when that object was executed.

A test that wipes `bulk_downloader.*` from sys.modules and re-imports therefore
produces a client whose token was derived from the LIVE key and a checker still
holding the STALE one. They never match, and the request is refused 403 with a
message about CSRF, which reads as a security defect rather than as two module
objects being alive at once.

WHAT DOES NOT CHANGE, stated because this touches auth-adjacent code: which
routes are guarded (`CSRF_GUARDED_PREFIXES`), the bootstrap exemptions
(`CSRF_EXEMPT_PATHS`), the Bearer bypass, the cross-origin Origin refusal, and
key rotation on restart. This is a binding-TIME fix, not a policy change -- the
same key is used, it is simply looked up when it is needed rather than captured
when the module happened to be executed.
"""

from __future__ import annotations

import importlib
import sys

# Its subject is one function's binding time, not an invariant over the tree.
BD_GATE_SCOPE = "module"

_PKG = "bulk_downloader"


def _wipe_package_modules() -> dict:
    """Drop every bulk_downloader.* module, returning what was removed.

    This is the shape 8 tracked test files produce (backlog 93): a module-scope
    `bulk_downloader` import plus a fixture that wipes the package so the next
    import re-reads env vars. The wipe is the documented, supported idiom --
    tests/conftest.py ships a `bd_module_wipe` marker for it -- so the fix has
    to make the CSRF pair survive it rather than forbid it.
    """
    saved = {n: m for n, m in sys.modules.items()
             if n == _PKG or n.startswith(_PKG + ".")}
    for n in saved:
        del sys.modules[n]
    return saved


def _restore(saved: dict) -> None:
    for n in [n for n in sys.modules
              if n == _PKG or n.startswith(_PKG + ".")]:
        del sys.modules[n]
    sys.modules.update(saved)


def test_the_wipe_really_mints_a_new_key():
    """The precondition. If re-execution reused the key there would be no
    asymmetry to fix and every assertion below would pass vacuously."""
    saved = _wipe_package_modules()
    try:
        old = importlib.import_module(f"{_PKG}.app")
        old_key = old._csrf_key
        _wipe_package_modules()
        new = importlib.import_module(f"{_PKG}.app")

        assert new is not old, "the wipe did not produce a second module object"
        assert new._csrf_key != old_key, (
            "re-execution reused the CSRF key, so this file cannot exercise "
            "the mint/check split it exists to test")
    finally:
        _restore(saved)


def test_a_token_minted_after_a_wipe_is_accepted_by_the_stale_checker():
    """The defect, end to end through a real request.

    `GET /api/csrf` is served by the blueprint, which resolves
    `_csrf_token_for` LATE and therefore mints against the live module's key.
    The POST is refused by `_check_csrf`, which lived on the stale module. On
    pristine source those are two different keys and the request is 403.
    """
    saved = _wipe_package_modules()
    try:
        stale = importlib.import_module(f"{_PKG}.app")
        client = stale.app.test_client()

        # Re-execute the package underneath the client we are holding. This is
        # exactly what a victim test file's fixture does between tests.
        _wipe_package_modules()
        live = importlib.import_module(f"{_PKG}.app")
        assert live is not stale
        assert live._csrf_key != stale._csrf_key

        minted = client.get("/api/csrf")
        assert minted.status_code == 200, minted.status_code
        token = (minted.get_json() or {}).get("csrf_token", "")
        assert token, f"no csrf_token in {minted.get_json()!r}"

        refused = client.post(
            "/api/zzz_csrf_probe",
            headers={"X-CSRF-Token": token, "Host": "localhost:5555"},
        )

        assert not (refused.status_code == 403
                    and b"CSRF" in refused.data.upper()), (
            "the CSRF check rejected a token the app itself had just minted. "
            "The mint resolved the live module and the check used the stale "
            "one, so the two halves of the double-submit pattern were reading "
            f"different keys. status={refused.status_code} "
            f"body={refused.data[:200]!r}")
    finally:
        _restore(saved)


def test_a_genuinely_wrong_token_is_still_refused():
    """THE OVER-SENSITIVITY CONTROL, and the one that matters here.

    Making the check agree with the mint must not be achieved by making the
    check accept anything. A fix that late-binds the key but stops comparing
    would pass the test above and remove CSRF protection entirely -- CLAUDE.md
    section 6's "a fix that calls every scan inconclusive passes the escape's
    test and destroys the tool", applied to auth.
    """
    saved = _wipe_package_modules()
    try:
        app_mod = importlib.import_module(f"{_PKG}.app")
        client = app_mod.app.test_client()

        minted = client.get("/api/csrf")
        assert minted.status_code == 200

        refused = client.post(
            "/api/zzz_csrf_probe",
            headers={"X-CSRF-Token": "0" * 32, "Host": "localhost:5555"},
        )

        assert refused.status_code == 403, (
            "a forged CSRF token was NOT refused; the check has stopped "
            f"checking. status={refused.status_code}")
    finally:
        _restore(saved)


def test_a_missing_token_is_still_refused():
    """The other half of the control: absent must not become acceptable."""
    saved = _wipe_package_modules()
    try:
        app_mod = importlib.import_module(f"{_PKG}.app")
        client = app_mod.app.test_client()

        assert client.get("/api/csrf").status_code == 200

        refused = client.post("/api/zzz_csrf_probe",
                              headers={"Host": "localhost:5555"})

        assert refused.status_code == 403, (
            "a request with NO CSRF token was accepted; the guard is not "
            f"running at all. status={refused.status_code}")
    finally:
        _restore(saved)
