"""Row 784 -- T1 follow-up to 708/708b.

`member_state_check` (bulk_downloader/login_impl/replay.py) returns early
with `evidence_path=None` when neither a `success_url` nor a learned
`member_indicator` is declared, BEFORE `write_login_evidence` is ever
called. Row 708's acceptance already requires the page a no-nav login
actually read to be kept as evidence "either way" -- the no-check case is
not an exemption, and losing the page there means the settled-no-nav
verdict for the commonest config shape (no success_url, no learned
indicator yet) has nothing anyone can re-read.

ACCEPTANCE (row 784, verbatim): "no-check no-nav retains actual HTML/final
URL, verdict not success; RED file exists/bytes match read page, not just
non-None. Capture failure stays falsy."
"""
from pathlib import Path

BD_GATE_SCOPE = "module"


class _Page:
    """Minimal page double: only `.url` and `.content()` are read here."""

    def __init__(self, url, html, content_ok=True):
        self._url = url
        self._html = html
        self._content_ok = content_ok
        self.content_calls = 0

    @property
    def url(self):
        return self._url

    def content(self):
        self.content_calls += 1
        if not self._content_ok:
            raise RuntimeError("fixture page content unavailable")
        return self._html


def test_no_check_no_nav_evidence_matches_the_page_actually_read(tmp_path):
    """THE ROW. Neither success_url nor member_indicator declared -- the
    only branch that used to skip write_login_evidence entirely."""
    from bulk_downloader.login_impl.replay import member_state_check

    final_url = "https://login.example.invalid/login"
    html = "<html><body>fixture no-check page</body></html>"
    page = _Page(final_url, html)
    config = {"login_evidence_dir": str(tmp_path / "evidence")}

    matched, why, evidence_path = member_state_check(page, config, tag="login-ajax")

    assert matched is False, (matched, why)
    assert evidence_path, (
        "no success_url and no member indicator declared, but the page "
        f"actually read must still be kept as evidence: got {evidence_path!r}"
    )
    assert page.content_calls == 1, (
        "the page must be read exactly once to produce the evidence file")
    path = Path(evidence_path)
    assert path.is_file(), f"declared evidence path does not exist: {path}"
    body = path.read_text(encoding="utf-8")
    assert final_url in body, body[:200]
    assert html in body, body[:200]


def test_no_check_no_nav_submit_path_checks_once_and_carries_evidence(
        monkeypatch, tmp_path):
    """The call seam: drive the real AJAX no-nav submit path, rather than
    testing member_state_check directly, so removing submit.py's call cannot
    silently leave a settled verdict without its rendered evidence."""
    from bulk_downloader.login_impl import submit
    from test_row708_no_nav_login_is_not_success import _drive

    calls = []
    real_member_state_check = submit.member_state_check

    def counted_member_state_check(*args, **kwargs):
        calls.append((args, kwargs))
        return real_member_state_check(*args, **kwargs)

    monkeypatch.setattr(submit, "member_state_check", counted_member_state_check)
    result, driven = _drive(monkeypatch, tmp_path, branch="ajax", success_url=None)

    outcome = result[0]
    evidence_path = getattr(outcome, "evidence_path", None)
    assert len(calls) == 1, f"member_state_check calls: {calls!r}"
    assert getattr(outcome, "ok", None) is False, result
    assert getattr(outcome, "status", None) == "settled-no-nav", result
    assert evidence_path, f"settled verdict omitted evidence: {outcome!r}"
    body = Path(evidence_path).read_text(encoding="utf-8")
    assert "https://login.example.invalid/login" in body, body[:200]
    assert "fixture members page" in body, body[:200]
    assert driven["content"] == 1, driven


def test_capture_failure_on_the_no_check_path_stays_falsy(tmp_path):
    """NEGATIVE CONTROL, intended-reason branch: even on the no-check path,
    a capture failure must never manufacture evidence or improve the
    verdict -- `write_login_evidence`'s own contract (returns None on a
    write failure) must still hold here."""
    from bulk_downloader.login_impl.replay import member_state_check

    final_url = "https://login.example.invalid/login"
    page = _Page(final_url, "<html>unused</html>")
    # A file in place of the evidence directory makes directory.mkdir()
    # raise, so write_login_evidence's own except-branch returns None --
    # proving the fixture actually forces the failure path, not merely a
    # config that happens to be empty.
    blocker = tmp_path / "evidence"
    blocker.write_text("not a directory", encoding="utf-8")
    config = {"login_evidence_dir": str(blocker)}

    matched, why, evidence_path = member_state_check(page, config, tag="login-ajax")

    assert matched is False, (matched, why)
    assert evidence_path is None, (
        f"a capture failure must stay falsy, got {evidence_path!r}")
    assert page.content_calls == 1, (
        "the page read is still attempted even though the write fails")


def test_declared_success_url_path_already_captures_evidence(tmp_path):
    """POSITIVE CONTROL: the declared-success_url branch already calls
    write_login_evidence on the base tree. Proves the probe (reading the
    file the function names) can say YES before the no-check test above is
    trusted to say NO-then-YES."""
    from bulk_downloader.login_impl.replay import member_state_check

    final_url = "https://login.example.invalid/members/home"
    html = "<html><body>fixture members page</body></html>"
    page = _Page(final_url, html)
    config = {"success_url": "/members",
              "login_evidence_dir": str(tmp_path / "evidence")}

    matched, why, evidence_path = member_state_check(page, config, tag="login-ajax")

    assert matched is True, (matched, why)
    assert evidence_path, "positive-control path must produce evidence too"
    body = Path(evidence_path).read_text(encoding="utf-8")
    assert html in body, body[:200]
