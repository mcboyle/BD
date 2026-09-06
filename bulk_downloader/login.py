"""bulk_downloader.login -- thin re-export shim over login_impl/.

Decomposed @v3.66.450 (DECOMP-LEAF cut 3). ADD-only (R1 shim-over-rm): this module stays a
FILE so the lens-#9 path/structure tests (which read bulk_downloader/login.py and
_login.__file__) still resolve; nothing is deleted on deploy. Re-exports the COMPLETE
surface explicitly (no `import *`, Phase-1 lesson) for byte-for-byte consumer compatibility."""

from .login_impl import (  # noqa: F401
    ManualLoginSession,
    PASS_FIELD_FALLBACKS,
    SUBMIT_FALLBACKS,
    USER_FIELD_FALLBACKS,
    _AUTH_COOKIE_HINTS,
    _LOGIN_CSS_SAFE,
    _MANUAL_LOGIN_BANNER_JS,
    _NOT_AUTH_COOKIE_HINTS,
    _SUBMIT_TEXTS,
    _all_visible,
    _attempt_headless_fill_submit,
    _build_submit_fallbacks,
    _build_verify_result,
    _compute_cookie_expiry_days,
    _css_escape_for_id,
    _human_move_to,
    _looks_authenticated,
    _ms_since,
    _staged_password_retry,
    _submit_login,
    _success_url_matches,
    LOGIN_SETTLED_NO_NAV,
    LoginOutcome,
    member_state_check,
    _try_check_remember_me,
    _try_click,
    _try_fill,
    _wait_captcha_tokens,
    cancel_manual_login,
    do_login,
    finalize_manual_login,
    open_manual_login_browser,
    replay_saved_login_flow,
    verify_login_replay,
)

__all__ = [
    "ManualLoginSession",
    "open_manual_login_browser",
    "finalize_manual_login",
    "cancel_manual_login",
    "replay_saved_login_flow",
    "do_login",
    "verify_login_replay",
    "LOGIN_SETTLED_NO_NAV",
    "LoginOutcome",
    "member_state_check",
]
