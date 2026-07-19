"""bulk_downloader.login_impl -- decomposed login package (manual-login + do_login flow).

login.py is a thin ADD-only re-export shim over this package (R1 shim-over-rm; no rm on
deploy, login.py stays a FILE so the lens-#9 path tests still find it). Full surface
(6 public + 1 class + 16 private + 8 consts) re-exported here and on the shim so module-
attribute access and white-box from-imports keep resolving unchanged."""

from ._common import (
    _LOGIN_CSS_SAFE,
    _all_visible,
    _try_fill,
    _try_click,
    _human_move_to,
    _css_escape_for_id,
    _ms_since,
)
from .manual import (
    _MANUAL_LOGIN_BANNER_JS,
    ManualLoginSession,
    open_manual_login_browser,
    finalize_manual_login,
    cancel_manual_login,
)
from .replay import (
    _AUTH_COOKIE_HINTS,
    _NOT_AUTH_COOKIE_HINTS,
    replay_saved_login_flow,
    verify_login_replay,
    _success_url_matches,
    _looks_authenticated,
    _build_verify_result,
    _compute_cookie_expiry_days,
    _attempt_headless_fill_submit,
)
from .submit import (
    USER_FIELD_FALLBACKS,
    PASS_FIELD_FALLBACKS,
    SUBMIT_FALLBACKS,
    _SUBMIT_TEXTS,
    _staged_password_retry,
    _wait_captcha_tokens,
    _build_submit_fallbacks,
    _submit_login,
    _try_check_remember_me,
    do_login,
)

__all__ = [
    "ManualLoginSession",
    "open_manual_login_browser",
    "finalize_manual_login",
    "cancel_manual_login",
    "replay_saved_login_flow",
    "do_login",
    "verify_login_replay",
]
