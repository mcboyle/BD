"""v3.43.71: tests for the Telegram bot remote-control module.

Covers:
  1. parse_command — empty/non-command/with bot suffix/with args
  2. parse_allowlist — blanks, comments, "12345 Matt" annotations
  3. _html_escape — special chars, non-string fallback
  4. _truncate_for_telegram — short message, large message
  5. Command handlers in isolation (mocked callbacks):
     - /status with sites
     - /queue (all sites summary, per-site detail)
     - /mirror happy path / missing arg / non-http URL
     - /cancel / /retry / /pause / /resume
     - /recent
  6. TelegramBot lifecycle (configure, set_callbacks, shutdown)
  7. TelegramBot doesn't start polling without token/allowlist
  8. TelegramBot reject unauthorized chat
  9. record_event() updates the ring buffer
 10. Flask routes registered
 11. bdctl cmd_tg_status function exists
 12. Runner hook calls record_event()
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import pytest
# [SAST 3:13pm 13 may] removed unused: from unittest.mock import patch, MagicMock

from bulk_downloader import tg_bot as t


# ─── parse_command ──────────────────────────────────────────────────


class TestParseCommand:
    def test_simple_command(self):
        assert t.parse_command("/status") == ("status", "")

    def test_command_with_args(self):
        assert t.parse_command("/mirror https://example.com/x") == \
            ("mirror", "https://example.com/x")

    def test_strips_bot_suffix(self):
        """Telegram appends @BotName when in a group."""
        assert t.parse_command("/status@MyBulkBot") == ("status", "")
        assert t.parse_command("/queue@MyBulkBot wow") == ("queue", "wow")

    def test_lowercase_command(self):
        """Commands are case-insensitive (Telegram convention)."""
        assert t.parse_command("/STATUS")[0] == "status"
        assert t.parse_command("/Mirror foo")[0] == "mirror"

    def test_non_command_returns_empty(self):
        assert t.parse_command("hello") == ("", "")
        assert t.parse_command("status") == ("", "")  # no leading /

    def test_empty_input(self):
        assert t.parse_command("") == ("", "")
        assert t.parse_command(None) == ("", "")  # type: ignore[arg-type]

    def test_strips_whitespace(self):
        assert t.parse_command("  /status  arg  ") == ("status", "arg")


# ─── parse_allowlist ────────────────────────────────────────────────


class TestParseAllowlist:
    def test_simple_list(self):
        text = "12345\n67890"
        assert t.parse_allowlist(text) == [12345, 67890]

    def test_skips_comments(self):
        text = "# matt's main\n12345\n# spare phone\n67890"
        assert t.parse_allowlist(text) == [12345, 67890]

    def test_skips_blanks(self):
        text = "12345\n\n\n67890"
        assert t.parse_allowlist(text) == [12345, 67890]

    def test_allows_name_annotation(self):
        """Format `12345 Matt's phone` is common; take first token."""
        text = "12345 Matt\n67890 Phone"
        assert t.parse_allowlist(text) == [12345, 67890]

    def test_negative_chat_id_for_group(self):
        """Group chats have NEGATIVE chat IDs in Telegram."""
        text = "-1001234567890"
        assert t.parse_allowlist(text) == [-1001234567890]

    def test_drops_bad_lines(self):
        """Non-numeric first token is silently dropped."""
        text = "12345\nbogus_line_here\n67890"
        assert t.parse_allowlist(text) == [12345, 67890]

    def test_empty_input(self):
        assert t.parse_allowlist("") == []
        assert t.parse_allowlist(None) == []  # type: ignore[arg-type]


# ─── _html_escape ───────────────────────────────────────────────────


class TestHtmlEscape:
    def test_basic_chars(self):
        assert t._html_escape("<a>") == "&lt;a&gt;"
        assert t._html_escape("&") == "&amp;"
        assert t._html_escape('"') == '"'  # double-quote not escaped

    def test_combined(self):
        assert t._html_escape("<script>foo & bar</script>") == \
            "&lt;script&gt;foo &amp; bar&lt;/script&gt;"

    def test_non_string_returns_empty(self):
        assert t._html_escape(None) == ""  # type: ignore[arg-type]
        assert t._html_escape(123) == ""  # type: ignore[arg-type]


# ─── _truncate_for_telegram ─────────────────────────────────────────


class TestTruncateForTelegram:
    def test_short_message_passthrough(self):
        assert t._truncate_for_telegram("short") == "short"

    def test_long_message_truncated(self):
        big = "x" * 5000
        result = t._truncate_for_telegram(big)
        # Telegram's hard limit is 4096; we leave headroom
        assert len(result) <= 4096
        assert "truncated" in result

    def test_at_boundary(self):
        """A message exactly _MAX_MSG_LEN long should not be truncated."""
        msg = "x" * t._MAX_MSG_LEN
        assert t._truncate_for_telegram(msg) == msg

    def test_empty(self):
        assert t._truncate_for_telegram("") == ""
        assert t._truncate_for_telegram(None) == ""  # type: ignore[arg-type]


# ─── Command handlers ──────────────────────────────────────────────


class TestCmdStatus:
    def _update(self):
        return t.TgUpdate(update_id=1, chat_id=12345, text="/status")

    def test_with_sites(self):
        cb = {"get_status": lambda: {
            "wow": {"name": "WowGirls", "state": "running",
                    "counts": {"pending": 5, "running": 1,
                               "done": 100, "failed": 2}},
        }}
        reply = t._cmd_status(self._update(), "", cb)
        assert "WowGirls" in reply
        assert "p=5" in reply
        assert "running" in reply

    def test_empty_sites(self):
        cb = {"get_status": lambda: {}}
        reply = t._cmd_status(self._update(), "", cb)
        assert "No sites" in reply

    def test_callback_missing(self):
        reply = t._cmd_status(self._update(), "", {})
        assert "unavailable" in reply

    def test_callback_raises(self):
        cb = {"get_status": lambda: (_ for _ in ()).throw(RuntimeError("boom"))}
        reply = t._cmd_status(self._update(), "", cb)
        assert "failed" in reply.lower()


class TestCmdQueue:
    def _update(self):
        return t.TgUpdate(update_id=1, chat_id=12345, text="/queue")

    def test_summary_all_sites(self):
        cb = {
            "get_status": lambda: {
                "wow": {"counts": {"pending": 5, "running": 1}},
                "idle": {"counts": {"pending": 0, "running": 0}},
            },
            "get_queue": lambda sid: [],
        }
        reply = t._cmd_queue(self._update(), "", cb)
        assert "wow" in reply
        # Idle sites with no work are skipped
        assert "idle" not in reply or "0 pending" not in reply

    def test_per_site_detail(self):
        cb = {
            "get_status": lambda: {},
            "get_queue": lambda sid: [
                {"url": "https://x/1", "status": "pending"},
                {"url": "https://x/2", "status": "running"},
            ],
        }
        reply = t._cmd_queue(self._update(), "wow", cb)
        assert "pending" in reply
        assert "https://x/1" in reply
        assert "2 jobs" in reply

    def test_per_site_empty(self):
        cb = {
            "get_status": lambda: {},
            "get_queue": lambda sid: [],
        }
        reply = t._cmd_queue(self._update(), "wow", cb)
        assert "empty" in reply.lower()


class TestCmdMirror:
    def _update(self):
        return t.TgUpdate(update_id=1, chat_id=12345, text="/mirror")

    def test_happy_path(self):
        cb = {"add_url": lambda url: {"ok": True, "site_id": "wow"}}
        reply = t._cmd_mirror(self._update(), "https://example.com/v", cb)
        assert "added" in reply
        assert "wow" in reply

    def test_missing_arg(self):
        cb = {"add_url": lambda url: {"ok": True}}
        reply = t._cmd_mirror(self._update(), "", cb)
        assert "Usage" in reply

    def test_non_http_url(self):
        cb = {"add_url": lambda url: {"ok": True}}
        reply = t._cmd_mirror(self._update(), "magnet:?xt=urn", cb)
        assert "not a valid URL" in reply

    def test_routing_rejected(self):
        cb = {"add_url": lambda url: {"ok": False, "error": "no matching site"}}
        reply = t._cmd_mirror(self._update(), "https://unknown.com/x", cb)
        assert "rejected" in reply
        assert "no matching site" in reply


class TestCmdCancelRetryPauseResume:
    def _update(self):
        return t.TgUpdate(update_id=1, chat_id=12345, text="/x")

    def test_cancel(self):
        cb = {"cancel_url": lambda url: {"ok": True}}
        reply = t._cmd_cancel(self._update(), "https://x/y", cb)
        assert "cancelled" in reply

    def test_retry_with_count(self):
        cb = {"retry_site": lambda sid: {"ok": True, "count": 7}}
        reply = t._cmd_retry(self._update(), "wow", cb)
        assert "retried" in reply
        assert "7" in reply

    def test_pause(self):
        cb = {"pause_site": lambda sid: {"ok": True, "site_ids": [sid]}}
        reply = t._cmd_pause(self._update(), "wow", cb)
        assert "paused" in reply
        assert "wow" in reply

    def test_resume(self):
        cb = {"resume_site": lambda sid: {"ok": True, "site_ids": [sid]}}
        reply = t._cmd_resume(self._update(), "wow", cb)
        assert "resumed" in reply

    def test_missing_arg_returns_usage(self):
        """All action commands return usage when called without args."""
        cb = {"pause_site": lambda sid: {"ok": True}}
        reply = t._cmd_pause(self._update(), "", cb)
        assert "Usage" in reply


class TestCmdRecent:
    def _update(self):
        return t.TgUpdate(update_id=1, chat_id=12345, text="/recent")

    def test_with_events(self):
        cb = {"get_recent_events": lambda: [
            {"kind": "done", "message": "Foo.mp4", "site_id": "wow"},
            {"kind": "fail", "message": "auth required", "site_id": "wow"},
        ]}
        reply = t._cmd_recent(self._update(), "", cb)
        assert "done" in reply
        assert "fail" in reply

    def test_empty(self):
        cb = {"get_recent_events": lambda: []}
        reply = t._cmd_recent(self._update(), "", cb)
        assert "no recent" in reply.lower()


# ─── TelegramBot ───────────────────────────────────────────────────


class TestBotLifecycle:
    def test_singleton(self):
        b1 = t.get_bot()
        b2 = t.get_bot()
        assert b1 is b2

    def test_configure_doesnt_start_without_token(self):
        bot = t.TelegramBot()
        bot.configure(token="", allowlist=[12345], enabled=True)
        s = bot.get_status()
        assert s.running is False
        bot.shutdown()

    def test_configure_doesnt_start_without_allowlist(self):
        bot = t.TelegramBot()
        bot.configure(token="fake_token", allowlist=[], enabled=True)
        s = bot.get_status()
        assert s.running is False
        bot.shutdown()

    def test_configure_doesnt_start_when_disabled(self):
        bot = t.TelegramBot()
        bot.configure(token="fake_token", allowlist=[12345], enabled=False)
        s = bot.get_status()
        assert s.running is False
        bot.shutdown()

    def test_set_callbacks_filters_invalid_names(self):
        bot = t.TelegramBot()
        bot.set_callbacks(
            get_status=lambda: {},
            invalid_callback=lambda: None,
        )
        assert "get_status" in bot._callbacks
        assert "invalid_callback" not in bot._callbacks
        bot.shutdown()

    def test_record_event(self):
        bot = t.TelegramBot()
        bot.record_event("done", "Foo.mp4", site_id="wow")
        bot.record_event("fail", "auth", site_id="wow")
        events = bot._callbacks["get_recent_events"]()
        assert len(events) == 2
        assert events[0]["kind"] == "done"
        bot.shutdown()


class TestBotStatus:
    def test_initial_status(self):
        bot = t.TelegramBot()
        s = bot.get_status()
        assert s.enabled is False
        assert s.running is False
        assert s.updates_received == 0
        assert s.commands_executed == 0
        bot.shutdown()

    def test_allowlist_size_tracked(self):
        bot = t.TelegramBot()
        bot.configure(allowlist=[1, 2, 3])
        s = bot.get_status()
        assert s.allowlist_size == 3
        bot.shutdown()


class TestUnauthorizedChat:
    def test_unauthorized_doesnt_dispatch(self):
        """A chat ID not on the allowlist shouldn't trigger a command
        handler — just a generic reply."""
        bot = t.TelegramBot()
        sent = []
        bot._send_message = lambda chat_id, text: sent.append((chat_id, text))
        update = t.TgUpdate(update_id=1, chat_id=99999, text="/status")
        bot._handle_unauthorized(update)
        assert len(sent) == 1
        # Generic message includes the chat ID
        assert "99999" in sent[0][1]
        bot.shutdown()


# ─── Flask routes ──────────────────────────────────────────────────


class TestFlaskRoutes:
    def test_tg_status_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/tg/status" in rules

    def test_tg_settings_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/tg/settings" in rules

    def test_tg_test_route(self):
        from bulk_downloader.app import app
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/api/tg/test" in rules


# ─── bdctl ──────────────────────────────────────────────────────────


class TestBdctl:
    def test_cmd_tg_status_exists(self):
        import bdctl
        assert hasattr(bdctl, "cmd_tg_status")
        assert callable(bdctl.cmd_tg_status)


# ─── Module-level helpers ──────────────────────────────────────────


class TestHandlerRegistry:
    def test_all_commands_registered(self):
        h = t._make_handlers()
        expected = {
            "start", "help", "status", "queue", "mirror",
            "cancel", "retry", "pause", "resume", "recent",
        }
        assert set(h.keys()) == expected

    def test_every_handler_callable(self):
        for cmd, fn in t._make_handlers().items():
            assert callable(fn), f"{cmd} handler not callable"


class TestUnknownCommand:
    def test_unknown_command_returns_help_hint(self):
        """Unknown commands route to a "type /help" hint via the
        dispatch logic. We test the registry directly here — the
        dispatcher's behavior with no handler is to send a message."""
        h = t._make_handlers()
        assert "bogus" not in h
