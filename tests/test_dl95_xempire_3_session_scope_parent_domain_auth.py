"""dl95-xempire-3: a parent-domain AUTH cookie that reaches the scene host
means the session covers it -- the scope diagnostic must not say otherwise.

MEASURED 2026-09-29 on test2 (HEAD b1a62055, jar cookies/5218e145.json,
mtime 02:14:01Z; names/domains only, no values): 13 cookies = 2 host-only
``www.xempire.com`` path ``/en`` (timezone prefs) + 11 on ``.xempire.com``
path ``/`` including ``autologin_userid`` and ``autologin_hash``. BD's own
``applicable_cookies`` offers 11 of them to the members scene URL, yet
``uncovered_host_diagnostic`` journalled "session does not cover
members.xempire.com ... 0 of them apply" (history 407), because it judged
only the two cookies scoped to the login host. The session IS on the parent
domain, so the line was a false positive that sent the live lane after a
non-defect (follow success_url to mint members cookies).

The brazzers shape stays pinned by
``test_login_session_does_not_cover_the_scene_host.py``: a parent-domain
consent cookie must still not silence a login host whose cookies stay home.
Only an AUTH-named cookie (``login_impl.replay._AUTH_COOKIE_HINTS``, minus
``_NOT_AUTH_COOKIE_HINTS``) that applies to the URL silences the line.
Hermetic: pure unit calls plus the runner seam on ``.test`` hosts.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

_LOGIN_URL = "https://www.gamma.test/en/login"
_LOGIN_HOST = "www.gamma.test"
_SCENE_HOST = "members.gamma.test"
_SCENE_URL = f"https://{_SCENE_HOST}/en/video/studio/Some-Scene/288933"


def _c(name, domain, path="/"):
    return {"name": name, "value": "v", "domain": domain, "path": path,
            "sameSite": "Lax", "secure": True, "httpOnly": True}


def _www_only():
    """The two host-only timezone cookies the login host sets on /en."""
    return [_c("mDateTime", _LOGIN_HOST, "/en"), _c("mOffset", _LOGIN_HOST, "/en")]


def _xempire_shape():
    """The measured 13-cookie jar shape: 2 www host-only + 11 parent-domain,
    two of them auth-named (autologin_*)."""
    parent = ".gamma.test"
    names = ["autologin_userid", "autologin_hash", "SID", "identityStatus",
             "activeMemberValidator", "nats", "nats_sess", "nats_cookie",
             "ageConfirmed", "cookieConsent", "lang"]
    return _www_only() + [_c(n, parent) for n in names]


def _runner():
    from bulk_downloader import db
    from bulk_downloader.runner import SiteRunner
    db.db_init()
    return SiteRunner("xempire3_scope_fixture",
                      {"name": "Xempire3ScopeFixture", "login_url": _LOGIN_URL})


def test_precondition_the_parent_domain_session_reaches_the_scene_host():
    from bulk_downloader.session_scope import applicable_cookies
    names = {c["name"] for c in applicable_cookies(_xempire_shape(), _SCENE_URL)}
    assert len(names) == 11 and {"autologin_userid", "autologin_hash"} <= names


def test_a_parent_domain_auth_cookie_on_the_scene_host_is_not_uncovered():
    """RED on base: the measured xempire jar is reported uncovered."""
    from bulk_downloader.session_scope import uncovered_host_diagnostic
    out = uncovered_host_diagnostic(_xempire_shape(), _SCENE_URL,
                                    login_host=_LOGIN_HOST)
    assert out == "", (
        f"XEMPIRE3-FALSE-UNCOVERED: diagnostic said {out!r} although the "
        f"parent-domain autologin_* cookies apply to {_SCENE_HOST}")


def test_the_runner_seam_journals_no_session_scope_for_the_xempire_jar(capsys):
    r = _runner()
    r.set_cookies(_xempire_shape())
    before = r._event_seq
    assert r._check_cookies_or_relogin(_SCENE_URL) is True
    kinds = [e.get("kind") for e in r.get_events(after_seq=before)]
    err = capsys.readouterr().err
    assert "session_scope" not in kinds, (
        f"XEMPIRE3-FALSE-UNCOVERED: runner journalled session_scope ({err!r})")


def test_positive_control_a_www_only_jar_still_names_the_members_host():
    """The row's own fixture: a jar with www-only cookies and a members
    scene URL. Nothing reaches the scene host, so the line must speak."""
    from bulk_downloader.session_scope import uncovered_host_diagnostic
    out = uncovered_host_diagnostic(_www_only(), _SCENE_URL,
                                    login_host=_LOGIN_HOST)
    assert out.startswith(f"session does not cover {_SCENE_HOST}"), out


def test_positive_control_parent_domain_non_auth_cookies_do_not_silence_it():
    """Consent/pref cookies on the parent domain are not a session."""
    from bulk_downloader.session_scope import uncovered_host_diagnostic
    jar = _www_only() + [_c(n, ".gamma.test") for n in
                         ("cookieConsent", "ageConfirmed", "lang", "csrf_login")]
    out = uncovered_host_diagnostic(jar, _SCENE_URL, login_host=_LOGIN_HOST)
    assert out.startswith(f"session does not cover {_SCENE_HOST}"), out


def test_positive_control_an_auth_cookie_that_does_not_apply_does_not_silence_it():
    """An autologin cookie scoped to a sibling host is not offered to the
    scene host and must not count."""
    from bulk_downloader.session_scope import uncovered_host_diagnostic
    jar = _www_only() + [_c("autologin_hash", "tour.gamma.test")]
    out = uncovered_host_diagnostic(jar, _SCENE_URL, login_host=_LOGIN_HOST)
    assert out.startswith(f"session does not cover {_SCENE_HOST}"), out
