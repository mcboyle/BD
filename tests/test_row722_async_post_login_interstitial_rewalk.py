"""Row 722 live (site-ma.bangbros.com/store, 2026-09-15 13:0xZ, operator
screenshots): the post-login offer block (CheckboxOfferV2Block.js) renders
AFTER load; the single gate walk at load saw no "CONTINUE TO MEMBERS AREA"
and the run handed off with "Expected URL contains ... got .../store".

Now: when the first post-login walk clears nothing on a non-success URL, the
run settles and walks once more. Fakes only (row 708 harness); no live site.
"""
from __future__ import annotations

from test_row708_no_nav_login_is_not_success import _drive, _jar

BD_GATE_SCOPE = "module"


def _walks(monkeypatch, outcomes):
    """Replace the interstitial walk: each call pops the next action list.

    The row 708 harness pins ``interstitial.dismiss_gates`` to an empty walk
    AFTER this fixture runs, so the module is shadowed by a proxy whose
    ``dismiss_gates`` cannot be re-pinned; everything else delegates."""
    import sys
    import bulk_downloader
    from bulk_downloader import interstitial as real
    calls = []

    def fake(page, wall, **kw):
        # Only the POST-LOGIN walks (on the landing page) are the subject;
        # the pre-form / fill-time walks on the login page see nothing.
        from urllib.parse import urlparse
        if urlparse(page.url).path.startswith("/login"):
            return []
        calls.append(page.url)
        actions = outcomes.pop(0) if outcomes else []
        if any(a.get("outcome") == "cleared" for a in actions):
            # a cleared interstitial is a navigation: the fake follows it
            type(page).url = property(lambda self: "https://login.example.invalid/members/home")
        return actions

    class _Proxy:
        def __getattr__(self, name):
            return getattr(real, name)

        @property
        def dismiss_gates(self):
            return fake

        @dismiss_gates.setter
        def dismiss_gates(self, _value):
            pass  # the harness's blanket pin is ignored on purpose
    proxy = _Proxy()
    monkeypatch.setitem(sys.modules, "bulk_downloader.interstitial", proxy)
    monkeypatch.setattr(bulk_downloader, "interstitial", proxy)
    return calls


def test_the_row_a_late_rendered_interstitial_gets_a_second_walk(monkeypatch, tmp_path, capsys):
    """THE ROW: pass 1 sees nothing (block not rendered yet), pass 2 clears it."""
    calls = _walks(monkeypatch, [[], [{"outcome": "cleared", "label": "CONTINUE TO MEMBERS AREA"}]])
    before = _jar(["pref_a"])
    _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True,
           before=before, after=_jar(["pref_a", "sessionid"]),
           success_url="/members", final_url="https://login.example.invalid/store")
    assert len(calls) == 2, (
        "the post-login walk ran %d time(s); a late-rendered interstitial "
        "(bangbros /store) is never seen" % len(calls))
    err = capsys.readouterr().err
    assert "settling and walking once more" in err, err[-400:]
    assert "dismissed post-login interstitial (1 cleared action(s))" in err, err[-400:]


def test_negative_control_a_success_landing_walks_once(monkeypatch, tmp_path, capsys):
    calls = _walks(monkeypatch, [[], []])
    before = _jar(["pref_a"])
    _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True,
           before=before, after=_jar(["pref_a", "sessionid"]),
           success_url="/members", final_url="https://login.example.invalid/members/home")
    assert len(calls) == 1, calls
    assert "walking once more" not in capsys.readouterr().err


def test_negative_control_a_cleared_first_pass_walks_once(monkeypatch, tmp_path, capsys):
    calls = _walks(monkeypatch, [[{"outcome": "cleared", "label": "No thanks"}], []])
    before = _jar(["pref_a"])
    _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True,
           before=before, after=_jar(["pref_a", "sessionid"]),
           success_url="/members", final_url="https://login.example.invalid/store")
    assert len(calls) == 1, calls
    assert "walking once more" not in capsys.readouterr().err
