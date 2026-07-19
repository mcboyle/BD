"""v3.66.659 -- POS-4 (notify path-secret mask) + windows.bat ffmpeg check.

POS-4: notify_apprise._safe_url_display already masked userinfo credentials (the
`creds` before the first '/') but left PATH-embedded secrets visible (path[:60]).
Several apprise services carry the secret in the PATH, not userinfo -- Discord
(discord://webhook_id/webhook_token), Slack (slack://TokenA/TokenB/TokenC/channel),
generic webhooks. This masks long path segments (>= 16 chars = a token) while keeping
short segments (channel names / numeric IDs) readable, so an operator can still tell
destinations apart without leaking the secret into logs.

windows.bat: install_windows.bat installs Python deps + Playwright but never checks
for ffmpeg, so a fresh Windows box fails the v3.66.657 ffmpeg_hls startup selftest and
real HLS/TS downloads. A non-fatal ffmpeg presence check-and-warn step is added (ASCII,
lint-clean) so the gap is surfaced at install time.
"""
from pathlib import Path
from bulk_downloader import notify_apprise as n


# ---- POS-4: path-secret masking ----

def test_masks_discord_path_token():
    url = "discord://123456789/AbCdEfGh0123456789LongWebhookTokenXYZ"
    masked = n._safe_url_display(url)
    assert "AbCdEfGh0123456789LongWebhookTokenXYZ" not in masked
    assert "discord" in masked  # scheme still visible


def test_masks_slack_path_tokens():
    url = "slack://TokenAaaaaaaaaaaaaaaa/TokenBbbbbbbbbbbbbbbb/general"
    masked = n._safe_url_display(url)
    assert "TokenAaaaaaaaaaaaaaaa" not in masked
    assert "TokenBbbbbbbbbbbbbbbb" not in masked
    assert "general" in masked  # short channel segment stays readable


def test_still_masks_userinfo_creds():
    # regression: the original userinfo mask must still hold
    url = "tgram://1234567890:secrettoken/9876543210"
    masked = n._safe_url_display(url)
    assert "secrettoken" not in masked
    assert "tgram" in masked


def test_keeps_short_path_segments_readable():
    # numeric chat IDs / short channel names remain visible (destination-telling)
    assert "9876543210" in n._safe_url_display("tgram://111:secret/9876543210")
    assert "MyChannel" in n._safe_url_display("tgram://111:secret/MyChannel")


def test_empty_and_pathless_unchanged():
    assert n._safe_url_display("") == ""
    assert n._safe_url_display("not a url") == "not a url"
    assert "****" in n._safe_url_display("ntfy://server.example.com")


# ---- windows.bat ffmpeg check ----

def _win_bat() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "install_windows.bat").read_text(encoding="utf-8", errors="replace")


def test_windows_installer_checks_ffmpeg():
    body = _win_bat().lower()
    assert "ffmpeg" in body, "install_windows.bat must check for ffmpeg (ROB-3 alignment)"


def test_windows_installer_ffmpeg_note_is_ascii():
    # the whole file must stay 7-bit ASCII (bat_lint gate); assert the addition kept it so
    raw = _win_bat().encode("utf-8", "replace")
    raw.decode("ascii")  # raises if any non-ASCII crept in
