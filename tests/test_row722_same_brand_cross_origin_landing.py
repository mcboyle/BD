"""Row 722 (G17) -- a same-brand cross-origin landing after submit is
recorded and judged, not refused.

Live (test2, LIVE-RUNNER-1/3): brazzers' login page is
site-ma.brazzers.com/login and the submit lands on www.brazzers.com/...;
blacked's www.blacked.com login goes through members.blacked.com/oidc. The
row-774 branch refused both as "cross-origin navigation refused: login page
... ended on ..." and handed off to manual takeover. The operator's ruling
(2026-09-15): cross-origin page issues are not blockers -- screenshot, record,
verify, and continue.

Fix: one predicate, ``submit._same_brand_origin(login_url, landed_url)``
(shared registrable domain). A same-brand landing writes evidence tagged
``login-cross-origin-landing`` and then runs the SAME post-submit judgment
as a same-origin landing (anonymous surface / member state / success_url).
A FOREIGN registrable domain (accounts.google.com) keeps row 774's refusal
verbatim -- a jar read on Google is still not a login.

Fixture pages only; no live site, no login started.
"""
from __future__ import annotations

import pytest

from tests.test_row708_no_nav_login_is_not_success import _drive

BD_GATE_SCOPE = "module"

LOGIN = "https://site-ma.brand.test/login"
SAME_BRAND = "https://www.brand.test/members"
GOOGLE = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x"
ANON_TEXT = "HOME  TOUR  LOG IN  JOIN NOW"
MEMBER_TEXT = "HOME  MY VIDEOS  Log out\nWelcome back"


def _surface(monkeypatch, *, password_visible=False, text=""):
    from bulk_downloader.login_impl import replay
    monkeypatch.setattr(replay, "_read_login_surface",
                        lambda page: {"password_visible": password_visible, "text": text})


def _navigated(monkeypatch, tmp_path, capsys, *, final_url, **kw):
    kw.setdefault("success_url", None)
    # The harness's login URL is fixed; point the row's brand at a different
    # host of the SAME registrable domain via final_url only.
    result, calls = _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True,
                           final_url=final_url, **kw)
    return result, calls, capsys.readouterr().err


def _evidence(tmp_path):
    d = tmp_path / "evidence"
    return sorted(p.name for p in d.glob("*")) if d.is_dir() else []


# ── THE ROW ──

def test_same_brand_cross_origin_landing_with_member_surface_is_ok(monkeypatch, tmp_path, capsys):
    """login.example.invalid/login -> www.example.invalid/members with a member
    surface: recorded, judged, OK. Base refuses it as cross-origin."""
    _surface(monkeypatch, text=MEMBER_TEXT)
    result, _, err = _navigated(monkeypatch, tmp_path, capsys,
                                final_url="https://www.example.invalid/members")
    assert result[0] is True, (
        "a same-brand cross-origin landing (login.example.invalid -> "
        f"www.example.invalid) was refused instead of judged: {result[:2]!r}")
    assert result[1].startswith("OK"), result[1]
    kept = _evidence(tmp_path)
    assert any(n.startswith("login-cross-origin-landing") and n.endswith(".html") for n in kept), (
        f"no login-cross-origin-landing evidence was kept: {kept!r}")
    assert ("login: cross-origin landing recorded (same brand: "
            "login.example.invalid -> www.example.invalid); evidence ") in err, err


def test_the_predicate_is_the_registrable_domain(monkeypatch):
    from bulk_downloader.login_impl import submit
    same = submit._same_brand_origin
    assert same(LOGIN, SAME_BRAND) is True
    assert same("https://www.blacked.com/login", "https://members.blacked.com/oidc/x") is True
    assert same("https://login.example.co.uk/x", "https://www.example.co.uk/y") is True
    assert same(LOGIN, GOOGLE) is False
    assert same("https://login.example.co.uk/x", "https://other.co.uk/") is False
    assert same("https://login.brand.test/x", "https://brand.test.evil.example/") is False
    assert same(LOGIN, "") is False and same("", SAME_BRAND) is False


# ── Negative controls ──

def test_negative_control_a_foreign_domain_keeps_the_row_774_refusal(monkeypatch, tmp_path, capsys):
    # Row 774's own harness: the refusal fires BEFORE the jar is read, which
    # the row-708 harness treats as a broken fixture.
    from tests.test_row774_login_submit_refuses_cross_origin_navigation import _drive as _drive_774
    _surface(monkeypatch, text=MEMBER_TEXT)
    result, _ = _drive_774(monkeypatch, tmp_path, final_url=GOOGLE)
    err = capsys.readouterr().err
    assert result[0] is not True, (
        f"a landing on accounts.google.com was accepted: {result[:2]!r}")
    assert result[0] is False, repr(result[0])
    assert ("cross-origin navigation refused: login page "
            "https://www.bang.com ended on 'https://accounts.google.com'") in result[1], result[1]
    assert result[2] == []
    assert "cross-origin landing recorded" not in err, err
    assert not any("login-cross-origin-landing" in n for n in _evidence(tmp_path)), _evidence(tmp_path)


def test_negative_control_b_same_brand_landing_still_anonymous_is_not_success(monkeypatch, tmp_path, capsys):
    _surface(monkeypatch, password_visible=True, text=ANON_TEXT)
    result, _, err = _navigated(monkeypatch, tmp_path, capsys,
                                final_url="https://www.example.invalid/login?error=1")
    assert result[0] is False, repr(result[:2])
    assert "post-submit page still anonymous (login form visible)" in result[1], result[1]
    assert "NOT success" in result[1], result[1]
    # The landing was still recorded before the judgment refused it.
    assert "cross-origin landing recorded (same brand" in err, err
    assert any(n.startswith("login-cross-origin-landing") for n in _evidence(tmp_path)), _evidence(tmp_path)


def test_in_sweep_same_brand_hop_is_a_submit_but_google_is_still_refused():
    """The click method must not be skipped for a same-brand hop; the
    foreign hop keeps row 774's in-sweep refusal text."""
    from tests.test_row774_login_submit_refuses_cross_origin_navigation import _Page, SAFE, TEXT_MATCHED
    from bulk_downloader.login_impl import submit
    page = _Page(url="https://login.bang.com/login", on_click={SAFE: "https://www.bang.com/members"})
    ok, label = submit._submit_login(page, [SAFE], [])
    assert (ok, label) == (True, "click submit selector"), (ok, label)
    page = _Page(url="https://login.bang.com/login", on_click={TEXT_MATCHED: GOOGLE})
    ok, label = submit._submit_login(page, [TEXT_MATCHED], [])
    assert ok is False and label == (
        "cross-origin navigation refused (https://accounts.google.com) at click submit selector"), (ok, label)


def test_in_sweep_a_declared_success_origin_is_a_submit_for_a_different_brand():
    """stepsiblingscaught live (16:0xZ): the login host stepsiblingscaught.com
    submits to members.nubiles-porn.com -- a DIFFERENT brand, but the
    operator declared it as success_url. Declared = admitted in the sweep;
    undeclared foreign hosts keep the refusal."""
    from tests.test_row774_login_submit_refuses_cross_origin_navigation import _Page, SAFE
    from bulk_downloader.login_impl import submit
    page = _Page(url="https://stepsiblingscaught.test/login",
                 on_click={SAFE: "https://members.nubiles-porn.test/"})
    ok, label = submit._submit_login(page, [SAFE], [],
                                     declared_origins={"https://members.nubiles-porn.test"})
    assert (ok, label) == (True, "click submit selector"), (
        "a declared success_url origin was refused inside the sweep: %r" % ((ok, label),))
    page = _Page(url="https://stepsiblingscaught.test/login",
                 on_click={SAFE: "https://members.nubiles-porn.test/"})
    ok, label = submit._submit_login(page, [SAFE], [])
    assert ok is False and "cross-origin navigation refused" in label, (ok, label)


def test_do_login_passes_the_declared_success_origin_into_the_sweep():
    import inspect
    from bulk_downloader.login_impl import submit
    src = inspect.getsource(submit.do_login)
    assert "_SWEEP_DECLARED_ORIGINS={o for o in (_origin(success or \"\"),) if o}" in src
