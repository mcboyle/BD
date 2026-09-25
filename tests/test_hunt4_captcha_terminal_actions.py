"""Terminal captcha actions must not trigger a second queue transition."""

from bulk_downloader import captcha_relay

BD_GATE_SCOPE = "module"


def test_resolved_session_ignores_repeated_and_stale_dismiss(monkeypatch):
    captcha_relay._reset_for_tests()
    monkeypatch.setattr(captcha_relay, "_maybe_push", lambda *_args: None)
    ended = []
    captcha_relay.register_takeover_ender(
        lambda site, url, resolution: ended.append((site, url, resolution))
    )
    url = "https://example.invalid/item"
    try:
        captcha_relay.mark_captcha_needed("site", url, "turnstile")
        assert captcha_relay.mark_resolved(url) is True
        assert captcha_relay.get_pending(url)["status"] == "resolved"
        assert ended == [("site", url, "resolved")]

        assert captcha_relay.mark_resolved(url) is True
        assert captcha_relay.mark_dismissed(url) is False
        assert captcha_relay.get_pending(url)["status"] == "resolved"
        assert ended == [("site", url, "resolved")]
    finally:
        captcha_relay._reset_for_tests()


def test_dismissed_session_cannot_be_resolved_later(monkeypatch):
    captcha_relay._reset_for_tests()
    monkeypatch.setattr(captcha_relay, "_maybe_push", lambda *_args: None)
    ended = []
    captcha_relay.register_takeover_ender(
        lambda site, url, resolution: ended.append((site, url, resolution))
    )
    url = "https://example.invalid/item"
    try:
        captcha_relay.mark_captcha_needed("site", url, "turnstile")
        assert captcha_relay.mark_dismissed(url) is True
        assert ended == [("site", url, "dismissed")]

        assert captcha_relay.mark_resolved(url) is False
        assert captcha_relay.get_pending(url)["status"] == "dismissed"
        assert ended == [("site", url, "dismissed")]
    finally:
        captcha_relay._reset_for_tests()


def test_solving_session_still_resolves_once(monkeypatch):
    # lens (B1): the product path is needed -> start_solve ("solving") -> resolved;
    # only the two terminal states are idempotent guards, "solving" is not.
    captcha_relay._reset_for_tests()
    monkeypatch.setattr(captcha_relay, "_maybe_push", lambda *_args: None)
    ended = []
    captcha_relay.register_takeover_ender(
        lambda site, url, resolution: ended.append((site, url, resolution))
    )
    captcha_relay.register_takeover_starter(lambda site, url: {"session_id": "s-1"})
    url = "https://example.invalid/solving"
    try:
        captcha_relay.mark_captcha_needed("site", url, "turnstile")
        captcha_relay.start_solve(url)
        assert captcha_relay.get_pending(url)["status"] == "solving"
        assert captcha_relay.mark_resolved(url) is True
        assert captcha_relay.get_pending(url)["status"] == "resolved"
        assert ended == [("site", url, "resolved")]
    finally:
        captcha_relay._reset_for_tests()
