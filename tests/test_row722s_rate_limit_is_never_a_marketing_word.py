"""Row 722s live (dorcelclub, 2026-09-15 13:55Z): the scene page rendered
normally, then the worker vanished and the job sat 'waiting' for 30 min.
rl_8cab7bee.json said the site was in the 24-hour cooldown: RL_RE matched the
lone word "forbidden" inside the sidebar's marketing copy ("Explore forbidden
sexual desires!") and _check_redirect returned "rl". Nothing on the job named
the cause.

Two contracts: (1) the block words only count as the HTTP status phrase
("403 Forbidden", "Access forbidden"), never as an English word; (2) a
cooldown writes a `rate_limit` event on the job that quotes the page text it
matched.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from bulk_downloader.constants import RL_RE
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

MARKETING = ("SPECIAL OFFERS!\nPureTaboo 75% OFF\nExplore forbidden sexual desires!\n"
             "Wicked 75% OFF\nThe most wicked girls in porn!")


@pytest.mark.parametrize("text", [
    MARKETING,
    "forbidden fruit, episode 4030",
    "the 403rd scene in the series",
    "HTTP 403",  # a bare status number is not a page that says it is blocked
])
def test_a_lone_block_word_in_page_copy_is_not_a_rate_limit(text):
    m = RL_RE.search(text)
    assert m is None, (
        f"RL_RE matched {m.group(0)!r} inside ordinary page copy -- this is the "
        "dorcelclub false cooldown: a healthy site gets 24h of silence")


@pytest.mark.parametrize("text", [
    "403 Forbidden", "Forbidden (403)", "Error 403", "Access forbidden",
    "Access Denied", "Too many requests", "rate limit exceeded",
    "You are being throttled", "please wait 30 minutes",
])
def test_negative_control_the_real_block_phrases_still_match(text):
    assert RL_RE.search(text) is not None, text


class _Loc:
    def __init__(self, text): self._t = text
    def inner_text(self, timeout=None): return self._t


class _Page:
    def __init__(self, text, url="https://members.example.test/scene/1"):
        self.url = url; self._t = text
    def locator(self, sel): return _Loc(self._t)
    def content(self): return "<html><body>" + self._t + "</body></html>"


def _bare_runner():
    r = object.__new__(SiteRunner)
    return r


def test_check_redirect_ignores_marketing_copy_but_names_a_real_block():
    r = _bare_runner()
    assert SiteRunner._check_redirect(r, _Page(MARKETING), "u") is None
    assert SiteRunner._check_redirect(r, _Page("Sorry.\n403 Forbidden\nnginx"), "u") == "rl"
    assert "403 Forbidden" in r._rl_match, r._rl_match


def _stub_for_cooldown():
    class _Stub:
        def __init__(self):
            self.site_id = "sid"; self._rl_until = 0.0; self._lock = threading.RLock()
            self.jobs = {}; self._stop = threading.Event(); self._pause = threading.Event()
            self._state = ""; self._rl_autostart = False; self.events = []
            self._rl_match = "Sorry. 403 Forbidden nginx"
        def _rotate_account_if_available(self, reason): return False
        def _save_rl(self): pass
        def _wait_rl_autostart(self): pass
        def _fire_site_hook(self, name, payload): pass
        def log_event(self, kind, message, url=None, extra=None):
            self.events.append((kind, message, url))
        @contextmanager
        def _job_status_writer(self):
            yield lambda: None
    return _Stub()


def test_a_cooldown_writes_a_rate_limit_event_that_quotes_the_page_text():
    stub = _stub_for_cooldown()
    SiteRunner.trigger_rate_limit(stub, "https://x/y", reason="Rate limit at https://x/y")
    kinds = [k for k, _m, _u in stub.events]
    assert kinds.count("rate_limit") == 1, (
        f"no rate_limit event on the job: the cooldown is invisible -- events={stub.events!r}")
    _k, msg, url = [e for e in stub.events if e[0] == "rate_limit"][0]
    assert url == "https://x/y"
    assert "403 Forbidden" in msg, msg


def test_negative_control_no_match_text_means_the_plain_reason():
    stub = _stub_for_cooldown(); stub._rl_match = ""
    SiteRunner.trigger_rate_limit(stub, "https://x/y", reason="429 burst")
    assert [m for k, m, _u in stub.events if k == "rate_limit"] == ["429 burst"]
