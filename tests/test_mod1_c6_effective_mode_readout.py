"""MOD-1 C-6 (RED-first): the effective takeover mode + downgrade reason must
survive onto the POLLED pending state, not just the one-shot start_solve reply.

The cockpit polls GET /api/captcha/pending, whose items are PendingCaptcha.
start_solve already receives mode/mode_reason from the starter (C-2/C-4/C-5) and
spreads them into its return, but never persists them onto the PendingCaptcha,
so the cockpit poll cannot show the effective mode or explain a downgrade -- a
silent downgrade is a lie by omission (plan 1.2). This persists them.

RED on pristine @805: PendingCaptcha has no mode/mode_reason field, so the
polled dict has no such keys (KeyError on p["mode"]).
"""
from __future__ import annotations

import sys

from bulk_downloader import captcha_relay


class _Base:
    def setup_method(self):
        captcha_relay._reset_for_tests()

    def teardown_method(self):
        captcha_relay._reset_for_tests()
        sys.modules.pop("bulk_downloader.push", None)


class TestEffectiveModeReadout(_Base):
    URL = "https://x.com/challenge"

    def _mark(self):
        captcha_relay.mark_captcha_needed("wowgirls", self.URL, "turnstile")

    def test_pending_carries_mode_and_reason_after_start_solve(self):
        self._mark()
        captcha_relay.register_takeover_starter(
            lambda site_id, url: {
                "session_id": "s1", "mode": "remote",
                "mode_reason": "requested remote_vnc, running remote (vnc backend not provisioned)"})
        captcha_relay.start_solve(self.URL)
        p = captcha_relay.get_pending(self.URL)
        assert p["status"] == "solving"
        assert p["mode"] == "remote"
        assert p["mode_reason"] and "remote_vnc" in p["mode_reason"]

    def test_clean_promotion_has_empty_reason_and_carries_vnc_url(self):
        self._mark()
        captcha_relay.register_takeover_starter(
            lambda site_id, url: {"session_id": "s2", "mode": "remote_vnc",
                                  "mode_reason": "", "vnc_url": "http://127.0.0.1:8444/"})
        captcha_relay.start_solve(self.URL)
        p = captcha_relay.get_pending(self.URL)
        assert p["mode"] == "remote_vnc"
        assert p["mode_reason"] == ""
        assert p["vnc_url"] == "http://127.0.0.1:8444/"

    def test_starter_without_mode_leaves_readout_absent_not_crashing(self):
        # a starter that predates the mode fields must not break the poll.
        self._mark()
        captcha_relay.register_takeover_starter(lambda site_id, url: {"session_id": "s3"})
        captcha_relay.start_solve(self.URL)
        p = captcha_relay.get_pending(self.URL)
        assert p["status"] == "solving"
        assert p["mode"] is None            # field present, defaulted
        assert p["mode_reason"] is None

    def test_a_freshly_marked_pending_item_has_the_fields_defaulted(self):
        # to_dict must always carry the keys so the FE type is stable.
        self._mark()
        p = captcha_relay.get_pending(self.URL)
        assert "mode" in p and "mode_reason" in p
        assert p["mode"] is None and p["mode_reason"] is None
