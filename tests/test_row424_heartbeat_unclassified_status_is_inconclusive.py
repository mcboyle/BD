"""Row 424 -- the heartbeat must not call every unclassified status alive.

All three session-keeper probes enumerated their failure signatures (login
redirect, 401/403, login form in the body) and returned True for EVERYTHING
else, so a 500, 502 or 404 from the site was reported as "session alive":
``_heartbeat_navigate`` answered "navigate ok (HTTP 500)" during an outage,
``_heartbeat_in_page_fetch`` did the same for the in-page fetch, and
``_heartbeat_httpx_fallback`` additionally passed any 3xx whose Location
merely lacked the substrings login/signin.

``_run_one_check`` then set state ``connected``, recorded ``heartbeat_ok``,
zeroed ``consecutive_failures`` and advanced ``predicted_expiry_ts`` -- an
auth-validity claim over a response that measured nothing about auth.  The
operator surface showed connected with a fresh expiry prediction while the
site was down, and a capture launched on that assurance met the real state
only at payload time.

THE FIX IS A THIRD STATE, NOT FAIL-CLOSED.  Classifying 5xx as heartbeat-fail
would fire ``_auto_relogin`` storms against a site that is merely down.  So
alive requires positive evidence of an authenticated response, an unclassified
status is INCONCLUSIVE, and inconclusive neither records ``heartbeat_ok``,
resets ``consecutive_failures``, advances ``predicted_expiry_ts``, nor
triggers relogin.

THE MIRROR DEFECT IS TESTED TOO: the negative controls prove a real login
redirect and a 401 still read DEAD and still relogin, and a genuine
authenticated 200 still reads ALIVE on every one of the three probes.

DELIBERATE BOUNDARY, stated so it is not read as an oversight: a probe that
RAISED (navigate timeout, a dead browser, a transport error) keeps its
existing DEAD classification.  The row is about an unclassified STATUS, and
reclassifying transport exceptions would stop the keeper self-healing from a
wedged browser -- a behaviour change this cut does not carry.
"""
from __future__ import annotations

import pytest

from bulk_downloader import session_keeper as sk


BD_GATE_SCOPE = "module"


_BASE = "https://row424.test"
_CFG = {"login_url": _BASE + "/login", "success_url": _BASE + "/home",
        "keep_alive_check_url": _BASE + "/account", "password": "p"}


# ── fakes ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status):
        self.status = status


class _FakePage:
    """Counts what it served, so every precondition is assertable."""

    def __init__(self, *, status=200, final_url=_BASE + "/account",
                 login_form=False, fetch_result=None):
        self._status = status
        self._final_url = final_url
        self._login_form = login_form
        self._fetch_result = fetch_result
        self.goto_calls = 0
        self.form_evals = 0
        self.fetch_evals = 0

    @property
    def url(self):
        return self._final_url

    def goto(self, url, **_kwargs):
        self.goto_calls += 1
        return _Resp(self._status)

    def evaluate(self, js):
        if "fetch(" in js:
            self.fetch_evals += 1
            assert self._fetch_result is not None, (
                "this page was not scripted for the in-page fetch probe")
            return dict(self._fetch_result)
        self.form_evals += 1
        return self._login_form


class _FakeHttpxResponse:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class _FakeHttpxClient:
    """Records the single GET the probe is allowed to make."""

    last = None

    def __init__(self, response):
        self._response = response
        self.gets = []
        type(self).last = self

    def __call__(self, *_args, **kwargs):
        self.kwargs = kwargs
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url):
        self.gets.append(url)
        return self._response


def _keeper(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    keeper = sk.SessionKeeper("row424", 0, dict(_CFG), lambda *_a: (False, "no"))
    persisted = []
    monkeypatch.setattr(keeper, "_persist_cookies",
                        lambda: persisted.append(1))
    keeper._persisted = persisted
    return keeper


def _arm_httpx(monkeypatch, keeper, response):
    """Give the httpx probe everything it needs EXCEPT the verdict.

    The probe imports ``vpn_runtime`` and ``download_egress`` at call time and
    fails CLOSED when a required tunnel is unavailable.  Leaving that
    unstubbed would make every case refuse for a reason that is not the
    subject -- an unrelated early refusal manufacturing a vacuous DEAD
    (CLAUDE.md A7).
    """
    import httpx
    from bulk_downloader import download_egress as _de

    monkeypatch.setattr(keeper, "_load_cookies", lambda: {"sid": "row424"})
    monkeypatch.setattr(_de, "effective_download_proxy",
                        lambda *_a, **_k: None)
    client = _FakeHttpxClient(response)
    monkeypatch.setattr(httpx, "Client", client)
    return client


# ── the defect, witnessed WITHOUT naming any symbol the fix introduces ────
#
# Every other test in this file asserts against sk.ALIVE/DEAD/INCONCLUSIVE, so
# on the defective parent it fails with AttributeError -- which proves the
# symbols are absent, not that the heartbeat lies. These four fail on the
# parent for the DEFECT itself, using only truthiness and keeper state, and
# they are the file's RED provenance.

@pytest.mark.parametrize("status", [500, 502, 404])
def test_witness_navigate_must_not_claim_ok_over_an_outage(monkeypatch,
                                                           tmp_path, status):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(status=status)
    keeper._page = page

    ok, detail = keeper._heartbeat_navigate()

    assert page.goto_calls == 1, page.goto_calls
    assert not ok, (
        f"HTTP {status} from the site reported the session ALIVE "
        f"({detail!r}). Nothing in that response measured auth.")


@pytest.mark.parametrize("status", [500, 503, 404])
def test_witness_in_page_fetch_must_not_claim_ok_over_an_outage(
        monkeypatch, tmp_path, status):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(fetch_result={"status": status, "type": "basic",
                                   "url": _BASE + "/account", "body": ""})
    keeper._page = page

    ok, detail = keeper._heartbeat_in_page_fetch()

    assert page.fetch_evals == 1, page.fetch_evals
    assert not ok, (
        f"an in-page fetch returning {status} reported the session ALIVE "
        f"({detail!r}).")


@pytest.mark.parametrize("response", [
    _FakeHttpxResponse(500),
    _FakeHttpxResponse(404),
    _FakeHttpxResponse(302, headers={"location": "/maintenance"}),
])
def test_witness_httpx_must_not_claim_ok_over_an_outage_or_a_stray_redirect(
        monkeypatch, tmp_path, response):
    keeper = _keeper(monkeypatch, tmp_path)
    client = _arm_httpx(monkeypatch, keeper, response)

    ok, detail = keeper._heartbeat_httpx_fallback()

    assert client.gets == [_CFG["keep_alive_check_url"]], client.gets
    assert not ok, (
        f"status {response.status_code} reported the session ALIVE "
        f"({detail!r}). session_keeper.py:898 passes any 3xx whose Location "
        f"merely lacks the substrings login/signin.")


def test_witness_run_one_check_must_not_report_connected_over_a_500(
        monkeypatch, tmp_path):
    """The operator surface. _run_one_check set state connected, recorded
    heartbeat_ok, zeroed consecutive_failures and advanced the expiry
    prediction -- four auth claims over a response that measured no auth."""
    keeper = _keeper(monkeypatch, tmp_path)
    keeper.state["consecutive_failures"] = 2
    keeper.state["predicted_expiry_ts"] = 111.0
    events = []
    monkeypatch.setattr(keeper, "_record_event",
                        lambda et, d="": events.append((et, d)))
    monkeypatch.setattr(keeper, "_auto_relogin", lambda: (False, "no"))
    monkeypatch.setattr(sk, "predict_next_expiry", lambda *_a, **_k: 4_242_424.0)
    page = _FakePage(status=500)
    keeper._page = page
    keeper._last_navigate_at = 0.0
    monkeypatch.setattr(keeper, "_browser_alive", lambda: True)
    monkeypatch.setattr(keeper, "_browser_age", lambda: 0.0)

    keeper._run_one_check()

    assert page.goto_calls == 1, (
        "the navigate probe must have run, or this measures nothing")
    assert [e[0] for e in events] != ["heartbeat_ok"], (
        f"a 500 recorded heartbeat_ok: {events!r}")
    assert keeper.state["state"] != "connected", (
        f"a 500 left the keeper reporting connected: {keeper.state!r}")
    assert keeper.state["predicted_expiry_ts"] == 111.0, (
        f"a 500 advanced the expiry prediction: {keeper.state!r}")
    assert keeper.state["consecutive_failures"] == 2, (
        f"a 500 zeroed consecutive_failures: {keeper.state!r}")


# ── the 3 x 3 matrix: each state reachable on each probe ──────────────────

def test_navigate_reports_alive_only_for_an_authenticated_2xx(monkeypatch,
                                                              tmp_path):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(status=200)
    keeper._page = page

    verdict, detail = keeper._heartbeat_navigate()

    # PRECONDITIONS: the stub really served the intended shape.
    assert page.goto_calls == 1, page.goto_calls
    assert page.form_evals == 1, page.form_evals
    assert verdict is sk.ALIVE, (verdict, detail)
    assert bool(verdict) is True
    # Cookies are only evidence when the response was authenticated.
    assert keeper._persisted == [1], keeper._persisted


@pytest.mark.parametrize("status", [500, 502, 404, 418])
def test_navigate_reports_inconclusive_for_an_unclassified_status(
        monkeypatch, tmp_path, status):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(status=status)
    keeper._page = page

    verdict, detail = keeper._heartbeat_navigate()

    assert page.goto_calls == 1, page.goto_calls
    assert verdict is sk.INCONCLUSIVE, (verdict, detail)
    assert bool(verdict) is False
    assert str(status) in detail, detail
    # An unmeasured response is not a cookie refresh either.
    assert keeper._persisted == [], keeper._persisted


@pytest.mark.parametrize("page_kwargs, expected_word", [
    ({"status": 200, "final_url": _BASE + "/login"}, "redirected"),
    ({"status": 401}, "401"),
    ({"status": 403}, "403"),
    ({"status": 200, "login_form": True}, "login form"),
])
def test_navigate_still_reports_dead_for_the_real_logout_signatures(
        monkeypatch, tmp_path, page_kwargs, expected_word):
    """NEGATIVE CONTROL: the fix must not launder a real logout into UNKNOWN."""
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(**page_kwargs)
    keeper._page = page

    verdict, detail = keeper._heartbeat_navigate()

    assert page.goto_calls == 1, page.goto_calls
    assert verdict is sk.DEAD, (verdict, detail)
    assert expected_word in detail, detail
    assert keeper._persisted == [], keeper._persisted


def test_in_page_fetch_reports_alive_only_for_an_authenticated_2xx(
        monkeypatch, tmp_path):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(fetch_result={"status": 200, "type": "basic",
                                   "url": _BASE + "/account",
                                   "body": "<html>welcome back</html>"})
    keeper._page = page

    verdict, detail = keeper._heartbeat_in_page_fetch()

    assert page.fetch_evals == 1, page.fetch_evals
    assert verdict is sk.ALIVE, (verdict, detail)
    assert keeper._persisted == [1], keeper._persisted


@pytest.mark.parametrize("status", [500, 503, 404])
def test_in_page_fetch_reports_inconclusive_for_an_unclassified_status(
        monkeypatch, tmp_path, status):
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(fetch_result={"status": status, "type": "basic",
                                   "url": _BASE + "/account", "body": ""})
    keeper._page = page

    verdict, detail = keeper._heartbeat_in_page_fetch()

    assert page.fetch_evals == 1, page.fetch_evals
    assert verdict is sk.INCONCLUSIVE, (verdict, detail)
    assert str(status) in detail, detail
    assert keeper._persisted == [], keeper._persisted


@pytest.mark.parametrize("result, expected_word", [
    ({"status": 401, "type": "basic", "body": ""}, "401"),
    ({"status": 403, "type": "basic", "body": ""}, "403"),
    ({"status": 0, "type": "opaqueredirect", "body": ""}, "opaque"),
    ({"status": 302, "type": "basic", "body": ""}, "redirect"),
    ({"status": 200, "type": "basic",
      "body": '<form><input type="password"> login</form>'}, "login form"),
])
def test_in_page_fetch_still_reports_dead_for_the_real_signatures(
        monkeypatch, tmp_path, result, expected_word):
    """NEGATIVE CONTROL for the fetch probe."""
    keeper = _keeper(monkeypatch, tmp_path)
    page = _FakePage(fetch_result=dict(result, url=_BASE + "/account"))
    keeper._page = page

    verdict, detail = keeper._heartbeat_in_page_fetch()

    assert page.fetch_evals == 1, page.fetch_evals
    assert verdict is sk.DEAD, (verdict, detail)
    assert expected_word in detail.lower(), detail
    assert keeper._persisted == [], keeper._persisted


def test_httpx_fallback_reports_alive_for_an_authenticated_200(monkeypatch,
                                                               tmp_path):
    keeper = _keeper(monkeypatch, tmp_path)
    client = _arm_httpx(monkeypatch, keeper,
                        _FakeHttpxResponse(200, text="<html>welcome</html>"))

    verdict, detail = keeper._heartbeat_httpx_fallback()

    assert client.gets == [_CFG["keep_alive_check_url"]], client.gets
    assert verdict is sk.ALIVE, (verdict, detail)


@pytest.mark.parametrize("response, expected_word", [
    (_FakeHttpxResponse(500), "500"),
    (_FakeHttpxResponse(404), "404"),
    # THE ONE THE ROW NAMES: a 3xx whose Location merely lacks login/signin.
    (_FakeHttpxResponse(302, headers={"location": "/maintenance"}), "302"),
])
def test_httpx_fallback_reports_inconclusive_for_an_unclassified_status(
        monkeypatch, tmp_path, response, expected_word):
    keeper = _keeper(monkeypatch, tmp_path)
    client = _arm_httpx(monkeypatch, keeper, response)

    verdict, detail = keeper._heartbeat_httpx_fallback()

    assert client.gets == [_CFG["keep_alive_check_url"]], client.gets
    assert verdict is sk.INCONCLUSIVE, (verdict, detail)
    assert expected_word in detail, detail


@pytest.mark.parametrize("response, expected_word", [
    (_FakeHttpxResponse(302, headers={"location": "/users/login"}), "login"),
    (_FakeHttpxResponse(401), "401"),
    (_FakeHttpxResponse(403), "403"),
    (_FakeHttpxResponse(
        200, text='<form><input type="password"> please login</form>'),
     "login form"),
])
def test_httpx_fallback_still_reports_dead_for_the_real_signatures(
        monkeypatch, tmp_path, response, expected_word):
    """NEGATIVE CONTROL for the fallback probe."""
    keeper = _keeper(monkeypatch, tmp_path)
    client = _arm_httpx(monkeypatch, keeper, response)

    verdict, detail = keeper._heartbeat_httpx_fallback()

    assert client.gets == [_CFG["keep_alive_check_url"]], client.gets
    assert verdict is sk.DEAD, (verdict, detail)
    assert expected_word in detail.lower(), detail


# ── the seam that made the silence operator-visible ───────────────────────

def _instrument_run_one_check(monkeypatch, keeper, verdict, detail):
    events = []
    relogins = []
    monkeypatch.setattr(keeper, "_record_event",
                        lambda et, d="": events.append((et, d)))
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (verdict, detail))
    monkeypatch.setattr(keeper, "_auto_relogin",
                        lambda: (relogins.append(1), (False, "no"))[1])
    monkeypatch.setattr(sk, "predict_next_expiry",
                        lambda *_a, **_k: 4_242_424.0)
    return events, relogins


def test_an_inconclusive_heartbeat_makes_no_auth_claim(monkeypatch, tmp_path):
    """THE DEFECT AT THE SURFACE. A 500 used to leave the keeper reporting
    connected with a freshly advanced expiry prediction."""
    keeper = _keeper(monkeypatch, tmp_path)
    keeper.state["consecutive_failures"] = 2
    keeper.state["predicted_expiry_ts"] = 111.0
    keeper.state["last_heartbeat_ts"] = 0.0
    events, relogins = _instrument_run_one_check(
        monkeypatch, keeper, sk.INCONCLUSIVE, "navigate inconclusive (HTTP 500)")

    keeper._run_one_check()

    # PRECONDITION: the check really ran (it can early-return on config).
    assert events, "no event was recorded, so _run_one_check did not proceed"

    assert keeper.state["state"] == "inconclusive", keeper.state
    assert [e[0] for e in events] == ["heartbeat_inconclusive"], events
    assert "500" in events[0][1], events
    # None of the four auth claims the row enumerates.
    assert keeper.state["last_heartbeat_ts"] == 0.0, keeper.state
    assert keeper.state["predicted_expiry_ts"] == 111.0, keeper.state
    # Not reset -- and not incremented either: a non-measurement must not
    # walk the keeper into BACKOFF_AFTER_N_FAILURES/disconnected.
    assert keeper.state["consecutive_failures"] == 2, keeper.state
    assert relogins == [], "an inconclusive probe must not fire relogin"


def test_a_genuine_200_still_reports_connected(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: the healthy path still claims health."""
    keeper = _keeper(monkeypatch, tmp_path)
    keeper.state["consecutive_failures"] = 2
    events, relogins = _instrument_run_one_check(
        monkeypatch, keeper, sk.ALIVE, "navigate ok (HTTP 200)")

    keeper._run_one_check()

    assert keeper.state["state"] == "connected", keeper.state
    assert [e[0] for e in events] == ["heartbeat_ok"], events
    assert keeper.state["consecutive_failures"] == 0, keeper.state
    assert keeper.state["predicted_expiry_ts"] == 4_242_424.0, keeper.state
    assert keeper.state["last_heartbeat_ts"] > 0, keeper.state
    assert relogins == [], relogins


def test_a_dead_heartbeat_still_reloggs_in(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: a real logout must still be repaired."""
    keeper = _keeper(monkeypatch, tmp_path)
    events, relogins = _instrument_run_one_check(
        monkeypatch, keeper, sk.DEAD, "server returned 401")

    keeper._run_one_check()

    assert [e[0] for e in events] == ["heartbeat_fail", "auto_relogin_fail"], events
    assert relogins == [1], relogins
    assert keeper.state["consecutive_failures"] == 1, keeper.state
    assert keeper.state["state"] == "disconnected", keeper.state


def test_repeated_inconclusive_checks_never_reach_backoff(monkeypatch,
                                                          tmp_path):
    """The delayed form of the mirror defect. If inconclusive incremented
    consecutive_failures, ten outage minutes would report `disconnected` --
    a claim about auth made from evidence about the site being down."""
    keeper = _keeper(monkeypatch, tmp_path)
    events, relogins = _instrument_run_one_check(
        monkeypatch, keeper, sk.INCONCLUSIVE, "navigate inconclusive (HTTP 503)")

    for _ in range(sk.BACKOFF_AFTER_N_FAILURES + 2):
        keeper._run_one_check()

    assert len(events) == sk.BACKOFF_AFTER_N_FAILURES + 2, events
    assert {e[0] for e in events} == {"heartbeat_inconclusive"}, events
    assert keeper.state["consecutive_failures"] == 0, keeper.state
    assert keeper.state["state"] == "inconclusive", keeper.state
    assert relogins == [], relogins


# ── the verdict type itself ───────────────────────────────────────────────

def test_the_verdict_preserves_the_two_state_truthiness_contract():
    """Every existing caller unpacks ``ok, detail`` and tests truthiness.
    ALIVE must be truthy; DEAD and INCONCLUSIVE must both be falsy, because
    neither is evidence that the session is usable."""
    assert bool(sk.ALIVE) is True
    assert bool(sk.DEAD) is False
    assert bool(sk.INCONCLUSIVE) is False
    assert sk.ALIVE is not sk.DEAD is not sk.INCONCLUSIVE
    assert sk.INCONCLUSIVE is not sk.ALIVE
    # Distinguishable by identity, and legible in a failure message.
    assert "inconclusive" in repr(sk.INCONCLUSIVE).lower()
    assert "alive" in repr(sk.ALIVE).lower()


def test_a_probe_that_still_answers_with_a_bool_keeps_its_old_meaning(
        monkeypatch, tmp_path):
    """The compatibility shim, asserted rather than assumed. A plain bool is
    never promoted to INCONCLUSIVE -- inventing an unknown from a boolean
    would hide a real dead session."""
    assert sk._as_verdict(True) is sk.ALIVE
    assert sk._as_verdict(False) is sk.DEAD
    assert sk._as_verdict(sk.INCONCLUSIVE) is sk.INCONCLUSIVE

    keeper = _keeper(monkeypatch, tmp_path)
    events, relogins = _instrument_run_one_check(
        monkeypatch, keeper, True, "legacy probe said ok")
    keeper._run_one_check()
    assert keeper.state["state"] == "connected", keeper.state
    assert [e[0] for e in events] == ["heartbeat_ok"], events
