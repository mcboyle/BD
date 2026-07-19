"""Held-open capture robustness (A/B/C) — pure-logic unit tests.

The live behaviour (reopening a real page, re-navigating a real browser through
an akamai/cloudflare interstitial) is stash-only — no network / noVNC in the
sandbox. What IS testable here is the decision logic those live steps key off:

  A — page-death recovery: when the context has no live page, the tick decides
      "reopen" (open a fresh page + re-nav to start_url) instead of polling a
      dead session until finish.
  B — "Go to my capture URL" re-nav: a GOTO sentinel (scoped like FINISH/CANCEL)
      makes the tick decide "renav" so the operator can return to the deep URL
      after a login redirect dumps them on home.
  C — challenge settle: detect a cloudflare/akamai interstitial by title so the
      capture can wait for it to clear instead of treating it as the content.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import tools.capture_session as cs


class _FakePage:
    def __init__(self, closed=False):
        self._closed = closed

    def is_closed(self):
        return self._closed


class _FakeCtx:
    def __init__(self, pages):
        self.pages = pages


# --- A: recovery decision ---------------------------------------------------

def test_recovery_decision_reopen_when_no_live_pages():
    assert cs._recovery_decision(0, goto_seen=False) == "reopen"


def test_recovery_decision_reopen_wins_even_if_goto():
    # a dead browser + a pending GOTO: reopening also lands on start_url
    assert cs._recovery_decision(0, goto_seen=True) == "reopen"


def test_recovery_decision_renav_when_goto_and_live():
    assert cs._recovery_decision(2, goto_seen=True) == "renav"


def test_recovery_decision_none_when_live_and_no_goto():
    assert cs._recovery_decision(1, goto_seen=False) == "none"


def test_live_pages_filters_closed():
    ctx = _FakeCtx([_FakePage(closed=True), _FakePage(closed=False),
                    _FakePage(closed=True)])
    assert len(cs._live_pages(ctx)) == 1


def test_live_pages_tolerates_garbage_context():
    class _Bad:
        @property
        def pages(self):
            raise RuntimeError("boom")
    assert cs._live_pages(_Bad()) == []


# --- B: GOTO sentinel scoping + consume ------------------------------------

def test_goto_sentinel_path_default():
    d = Path(tempfile.mkdtemp())
    assert cs._goto_sentinel_path(d, None) == d / "GOTO"


def test_goto_sentinel_path_scoped_to_finish_file():
    # per-capture --finish-file <wacz>.FINISH -> sibling <wacz>.GOTO
    ff = "/x/captures/host_ts.FINISH"
    assert cs._goto_sentinel_path(None, ff) == Path("/x/captures/host_ts.GOTO")


def test_consume_goto_true_then_removes():
    d = Path(tempfile.mkdtemp())
    (d / "GOTO").touch()
    assert cs._consume_goto(d, None) is True
    assert not (d / "GOTO").exists()      # consumed, so it fires once
    assert cs._consume_goto(d, None) is False


# --- C: challenge interstitial detection -----------------------------------

def test_challenge_in_title_cloudflare():
    assert cs._challenge_in_title("Just a moment...") is True
    assert cs._challenge_in_title("Checking your browser before accessing") is True


def test_challenge_in_title_akamai():
    assert cs._challenge_in_title("Access Denied") is True
    assert cs._challenge_in_title("Reference #18.abcd") is True


def test_challenge_in_title_normal_is_false():
    assert cs._challenge_in_title("Jada — Slutty Maid | UltraFilms") is False
    assert cs._challenge_in_title("") is False
    assert cs._challenge_in_title(None) is False
