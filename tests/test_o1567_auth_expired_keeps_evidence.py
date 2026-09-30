"""fx-auth-expired-evidence (O1567, test3 adulttime 2026-09-29 23:21Z).

MEASURED: with an operator-imported member session (a plain GET of the same
scene URL with the same cookies returned 200 with a logout link), both
workers marked every members.adulttime.com scene
``Session expired -- re-logging in (try 1/2)`` within seconds, and the
re-login parked on Cloudflare Turnstile.  The log carried only that line:
no page URL, no signal, no screenshot.  Whether the worker was redirected to
/login, matched AUTH_BODY_RE (a login-modal password input), or read the
logged-out shape could not be looked at (results/test3/adulttime-cookies.md).

``_check_redirect`` must say WHICH signal made the page "auth", on WHICH URL,
and keep the page (HTML + PNG) in login_evidence -- every time it returns
"auth", and never when it does not.
"""

# The gate parses a module-level ASSIGNMENT, not a docstring line.
BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader.runner import SiteRunner


class _Loc:
    def __init__(self, text):
        self._t = text

    def inner_text(self, timeout=None):
        return self._t


class _Page:
    def __init__(self, url, html, text="", logged_out=False):
        self.url = url
        self._html = html
        self._text = text
        self._logged_out = logged_out
        self.shots = []

    def locator(self, sel):
        return _Loc(self._text)

    def content(self):
        return self._html

    def evaluate(self, js, *a):
        return self._logged_out

    def screenshot(self, path=None, **kw):
        self.shots.append(path)
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")


SCENE = "https://members.adulttime.example/en/video/ch/Some-Scene/289575"
MODAL = ('<html><body><a href="/en/logout">Log out</a><div id="login-modal">'
         '<input type="password" name="password"></div></body></html>')


def _runner(tmp_path):
    r = object.__new__(SiteRunner)
    r.site_id = "f5d491e5"
    r.config = {"name": "adulttime", "login_url": "https://freetour.adulttime.example/en/login",
                "login_evidence_dir": str(tmp_path)}
    return r


def _evidence(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


@pytest.mark.parametrize("page,signal", [
    (_Page("https://freetour.adulttime.example/en/login", "<html></html>"), "url"),
    (_Page(SCENE, MODAL), "body"),
    (_Page(SCENE, "<html><body>Log in</body></html>", logged_out=True), "logged-out"),
    (_Page(SCENE, "<html><body>ACCESS DENIED You must be a member to watch this video</body></html>",
           text="ACCESS DENIED  You must be a member to watch this video"), "members-only"),
    (_Page(SCENE, "<html><body>403 Forbidden Request is denied</body></html>",
           text="403 Forbidden\nRequest is denied"), "bare-403"),
], ids=["redirect-to-login", "password-input-in-body", "logged-out-shape", "members-only-wall",
        "bare-403-login-wall"])
def test_every_auth_verdict_names_its_signal_and_keeps_the_page(tmp_path, capsys, page, signal):
    r = _runner(tmp_path)
    got = SiteRunner._check_redirect(r, page, SCENE, no_candidate=(signal == "logged-out"))
    assert got == "auth"
    err = capsys.readouterr().err
    assert "O1567-AUTH-EVIDENCE" in err and f"signal={signal}" in err, (
        f"O1567-AUTH-EVIDENCE: the 'auth' verdict on {page.url} logged no signal "
        f"(expected signal={signal}); stderr was: {err!r}")
    assert page.url in err, "the auth verdict must name the URL the worker actually read"
    names = _evidence(tmp_path)
    assert any(n.endswith(".html") for n in names) and any(n.endswith(".png") for n in names), (
        f"O1567-AUTH-EVIDENCE: no page kept in login_evidence for the auth verdict: {names}")
    assert page.shots, "the page the worker read must be screenshotted"


def test_control_a_normal_member_page_keeps_nothing(tmp_path, capsys):
    r = _runner(tmp_path)
    page = _Page(SCENE, '<html><body><a href="/en/logout">Log out</a> Download 1080p</body></html>',
                 text="Download 1080p")
    assert SiteRunner._check_redirect(r, page, SCENE) is None
    assert "O1567-AUTH-EVIDENCE" not in capsys.readouterr().err
    assert _evidence(tmp_path) == [] and page.shots == []


def test_control_a_rate_limit_keeps_no_auth_evidence(tmp_path, capsys):
    r = _runner(tmp_path)
    page = _Page(SCENE, "<html><body>Too many requests</body></html>",
                 text="Too many requests -- try again later")
    assert SiteRunner._check_redirect(r, page, SCENE) == "rl"
    assert "O1567-AUTH-EVIDENCE" not in capsys.readouterr().err
    assert _evidence(tmp_path) == []


def test_evidence_failure_never_changes_the_verdict(tmp_path, capsys):
    r = _runner(tmp_path)
    r.config["login_evidence_dir"] = str(tmp_path / "file-not-dir")
    (tmp_path / "file-not-dir").write_text("x")
    page = _Page(SCENE, MODAL)
    assert SiteRunner._check_redirect(r, page, SCENE) == "auth"
    assert "signal=body" in capsys.readouterr().err


# Lens REFUTE (bd-worker-OP-bd3-B): this path runs for every job on every worker and nothing prunes
# login_evidence; the target page is a LOGGED-IN member page, which inlines its session data.
MEMBER_WITH_SESSION = ('<html><body><a href="/en/logout">Log out</a><div id="login-modal">'
                       '<input type="password" name="password" value="typed-pw"></div>'
                       '<input type="text" name="q" value=\'acct@example.test\'>'
                       '<script>window.__USER__={"token":"LENS-SESSION-TOKEN"}</script></body></html>')


def test_repeated_verdicts_keep_one_page_per_signal_but_log_every_time(tmp_path, capsys):
    r = _runner(tmp_path)
    for _ in range(20):
        assert SiteRunner._check_redirect(r, _Page(SCENE, MODAL), SCENE) == "auth"
    err = capsys.readouterr().err
    assert err.count("O1567-AUTH-EVIDENCE signal=body") == 20, "every verdict must still be logged"
    html = [n for n in _evidence(tmp_path) if n.endswith(".html")]
    assert len(html) == 1, f"O1567-AUTH-EVIDENCE-UNBOUNDED: 20 verdicts kept {len(html)} pages: {html}"


def test_another_signal_and_an_elapsed_window_are_kept(tmp_path):
    from bulk_downloader import runner_auth
    r = _runner(tmp_path)
    SiteRunner._check_redirect(r, _Page(SCENE, MODAL), SCENE)
    SiteRunner._check_redirect(r, _Page("https://freetour.adulttime.example/en/login", "<html></html>"), SCENE)
    assert sorted(r._auth_evidence_at) == ["body", "url"]
    r._auth_evidence_at["body"] -= runner_auth._AUTH_EVIDENCE_WINDOW_S + 1
    page = _Page(SCENE, MODAL)
    SiteRunner._check_redirect(r, page, SCENE)
    assert page.shots, "a verdict after the window must keep the page again"


def test_kept_page_carries_no_member_session_data(tmp_path):
    r = _runner(tmp_path)
    assert SiteRunner._check_redirect(r, _Page(SCENE, MEMBER_WITH_SESSION), SCENE) == "auth"
    [kept] = [p for p in tmp_path.iterdir() if p.suffix == ".html"]
    html = kept.read_text()
    for secret in ("LENS-SESSION-TOKEN", "typed-pw", "acct@example.test"):
        assert secret not in html, f"O1567-AUTH-EVIDENCE-LEAK: {secret!r} written to {kept.name}"
    # the markup that explains the verdict stays readable
    assert 'type="password"' in html and "Log out" in html and "<script>" in html
