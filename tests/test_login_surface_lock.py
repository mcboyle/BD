"""test_login_surface_lock.py -- attribute-surface guard for the login -> login_impl
package split (DECOMP-LEAF cut 3).

Freezes the external-private set (the §2 forensic freeze: 5 privates + 2 consts that
external code reaches off bulk_downloader.login) plus the 6 public fns + the
ManualLoginSession class, and smoke-imports every submodule (catches a lazy-import miss
or a cross-cluster wiring break). Runs under the custom run_tests.py harness.
"""
from bulk_downloader import login as L

PUBLIC = {
    "open_manual_login_browser", "finalize_manual_login", "cancel_manual_login",
    "replay_saved_login_flow", "do_login", "verify_login_replay", "ManualLoginSession",
}
# external-private freeze set (forensic §2): reached by tests / runner / dynamic access
FROZEN_PRIV = {
    "_build_verify_result", "_submit_login", "_looks_authenticated",
    "_staged_password_retry", "_compute_cookie_expiry_days",
}
FROZEN_CONST = {"_SUBMIT_TEXTS", "_AUTH_COOKIE_HINTS"}

FULL_PRIV = {
    "_all_visible", "_try_fill", "_try_click", "_staged_password_retry", "_human_move_to",
    "_wait_captcha_tokens", "_build_submit_fallbacks", "_submit_login",
    "_try_check_remember_me", "_css_escape_for_id", "_success_url_matches",
    "_looks_authenticated", "_ms_since", "_build_verify_result",
    "_compute_cookie_expiry_days", "_attempt_headless_fill_submit",
}
FULL_CONST = {
    "USER_FIELD_FALLBACKS", "PASS_FIELD_FALLBACKS", "_SUBMIT_TEXTS", "SUBMIT_FALLBACKS",
    "_MANUAL_LOGIN_BANNER_JS", "_AUTH_COOKIE_HINTS", "_NOT_AUTH_COOKIE_HINTS",
    "_LOGIN_CSS_SAFE",
}


def test_frozen_external_surface_present():
    frozen = PUBLIC | FROZEN_PRIV | FROZEN_CONST
    missing = frozen - set(dir(L))
    assert not missing, f"login shim dropped frozen names: {sorted(missing)}"


def test_full_surface_reexported():
    full = PUBLIC | FULL_PRIV | FULL_CONST
    missing = full - set(dir(L))
    assert not missing, f"login shim incomplete, missing: {sorted(missing)}"


def test_manual_login_session_is_class():
    assert isinstance(L.ManualLoginSession, type)


def test_submit_fallbacks_computed():
    # SUBMIT_FALLBACKS = _build_submit_fallbacks() must evaluate at import (source order)
    assert isinstance(L.SUBMIT_FALLBACKS, list) and len(L.SUBMIT_FALLBACKS) > 0


def test_white_box_imports_resolve():
    # runner.py / runner_auth.py module-level + wizard white-box private
    from bulk_downloader.login import do_login, verify_login_replay  # noqa: F401
    from bulk_downloader.login import _build_verify_result  # noqa: F401


def test_each_submodule_imports():
    import importlib
    for mod in ("_common", "manual", "replay", "submit"):
        importlib.import_module(f"bulk_downloader.login_impl.{mod}")
