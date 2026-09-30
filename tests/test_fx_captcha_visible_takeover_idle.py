"""fx-captcha-visible-takeover-idle (bd1 2026-09-29, OP-bd1): the dorcelclub hCaptcha on
www.dorcelclub.com/blocked was parked for the operator in the app's captcha take-over
(POST /api/captcha/start_solve -> mode "visible", a real browser on the server display,
worked over VNC). 23:52Z opened; 23:56:08Z "manual_login: session thread exited" +
"Manual solve dismissed" with nobody touching it: the A-5b idle sweep dismisses a
`solving` session after captcha_takeover_idle_timeout_s (300 s) without an input
accepted by submit_takeover_input -- which a visible session's input never passes.
"""
from __future__ import annotations

import time

BD_GATE_SCOPE = "module"
DIAG = "FX_VISIBLE_TAKEOVER_REAPED_IDLE"
URL = "https://www.dorcelclub.com/en/scene/305502/captive-of-irresistible-pleasure"


class _RelayReset:
    def setup_method(self):
        from bulk_downloader import captcha_relay, takeover
        captcha_relay._reset_for_tests()
        with takeover._channels_lock:
            for sid in list(takeover._channels):
                takeover._channels[sid].closed.set()
            takeover._channels.clear()

    def teardown_method(self):
        self.setup_method()


class TestVisibleTakeoverIdle(_RelayReset):
    def _solve(self, mode):
        from bulk_downloader import captcha_relay
        ended = []
        captcha_relay.mark_captcha_needed("8cab7bee", URL, "hcaptcha")
        captcha_relay.register_takeover_starter(
            lambda s, u: {"session_id": "captcha-8cab7bee-1", "ok": True, "mode": mode})
        captcha_relay.register_takeover_ender(lambda s, u, r: ended.append((s, u, r)))
        captcha_relay.start_solve(URL)
        return ended

    def test_a_visible_takeover_is_not_reaped_by_the_input_idle_clock(self):
        from bulk_downloader import captcha_relay
        ended = self._solve("visible")
        now = time.time() + captcha_relay._takeover_idle_timeout_s() + 60   # 23:52 -> 23:58
        rep = captcha_relay.sweep_report(now=now)
        status = captcha_relay.get_pending(URL)["status"]
        assert status == "solving" and ended == [], (
            f"{DIAG}: a visible (server-display) take-over was dismissed after "
            f"{captcha_relay._takeover_idle_timeout_s()} s without app-relayed input "
            f"(status={status!r}, ended={ended!r}, report={rep.get('expired_or_idle')})")

    def test_a_visible_takeover_still_expires_on_the_pending_timeout(self):
        from bulk_downloader import captcha_relay
        ended = self._solve("visible")
        now = time.time() + captcha_relay._pending_timeout_s() + 1
        assert captcha_relay.sweep_report(now=now)["expired_or_idle"] == 1
        assert captcha_relay.get_pending(URL)["status"] == "dismissed"
        assert ended == [("8cab7bee", URL, "dismissed")]

    def test_a_remote_takeover_is_still_reaped_when_idle(self):
        from bulk_downloader import captcha_relay
        ended = self._solve("remote")
        now = time.time() + captcha_relay._takeover_idle_timeout_s() + 1
        assert captcha_relay.sweep_report(now=now)["expired_or_idle"] == 1
        assert ended == [("8cab7bee", URL, "dismissed")]

    def test_the_live_visible_browser_is_not_an_orphan(self):
        from bulk_downloader import captcha_relay
        ended = self._solve("visible")
        captcha_relay.register_session_census(lambda: [("captcha-8cab7bee-1", URL)])
        now = time.time() + captcha_relay._takeover_idle_timeout_s() + 60
        captcha_relay.sweep_report(now=now)
        assert ended == [], f"{DIAG}: the live visible solve browser was reaped: {ended!r}"
