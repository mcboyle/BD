"""Row 971: template onboarding must NOT pick the login page as content.

_site_primary_url(cfg, prefer_login=False) should return "" when the config
carries only login_url. A site configured with only a login URL has no content
URL, and the captured template should describe content, not the login form.

RED on unfixed main: _site_primary_url({"login_url": "https://members.example.com/login"})
returns "https://members.example.com/login" instead of "".

Fixture-only: no browser, no network, no live site, no login started (Rule 21).
"""
import pathlib
import sys

BD_GATE_SCOPE = "repo-wide"

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bulk_downloader.app as bd_app  # noqa: E402


LOGIN = "https://members.example.com/login"
START = "https://members.example.com/content"


def test_login_only_config_returns_empty_in_content_mode():
    """A config with only login_url must yield '' in content mode."""
    cfg = {"login_url": LOGIN}
    result = bd_app._site_primary_url(cfg, prefer_login=False)
    assert result == "", (
        f"_site_primary_url must not return login_url in content mode, "
        f"got {result!r}"
    )


def test_login_only_config_still_returns_login_in_login_mode():
    """prefer_login=True must still return the login URL."""
    cfg = {"login_url": LOGIN}
    result = bd_app._site_primary_url(cfg, prefer_login=True)
    assert result == LOGIN


def test_content_plus_login_returns_content_not_login():
    """When start_url exists alongside login_url, content mode returns start_url."""
    cfg = {"login_url": LOGIN, "start_url": START}
    result = bd_app._site_primary_url(cfg, prefer_login=False)
    assert result == START


def test_content_plus_login_login_mode_returns_login():
    """prefer_login=True returns login_url even when start_url is present."""
    cfg = {"login_url": LOGIN, "start_url": START}
    result = bd_app._site_primary_url(cfg, prefer_login=True)
    assert result == LOGIN


def test_unusable_content_and_login_returns_empty_in_content_mode():
    """Non-http content keys + login_url -> '' in content mode (not login)."""
    cfg = {
        "login_url": LOGIN,
        "crawler_listing_url": "javascript:alert(1)",
        "listing_url": "not-a-url",
    }
    result = bd_app._site_primary_url(cfg, prefer_login=False)
    assert result == "", (
        f"Expected '' for unusable content + login-only, got {result!r}"
    )


def test_negative_control_revert_restores_login_fallback():
    """Negative control: with the fix reverted (login_url in fallback tuple),
    the login-only config WOULD return the login URL. We verify that the
    function's fallback tuple no longer contains login_url by checking the
    function source.

    This is a structural negative control, not a behavioral one, because
    we cannot un-apply the fix in the same process.
    """
    import inspect
    src = inspect.getsource(bd_app._site_primary_url)
    # The old code had: login_first[1:] + ("login_url",)
    # The fix changes it to: login_first[1:]
    # So '"login_url"' should NOT appear in the fallback concatenation
    assert '+ ("login_url",)' not in src, (
        "The login_url fallback should have been removed from the "
        "content-mode branch of _site_primary_url"
    )
