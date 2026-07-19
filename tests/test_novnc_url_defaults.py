"""noVNC embed-URL defaults — cockpit_core.novnc_url().

BD_NOVNC_URL is the single source of truth (config/env only; never browser-
supplied) and is embedded verbatim as the iframe `src` by BOTH the SPA /capture
page and the cockpit noVNC page. BD appends nothing, so if the operator omits
`resize=scale` the remote canvas renders at native resolution and CLIPS inside
the 78vh iframe (the recurring scaling footgun). This pins the hardening:
novnc_url() fills two embed defaults WHEN ABSENT --

  * resize=scale     -> canvas scales to fit the iframe (kills the clip)
  * autoconnect=true -> connects without a manual click

-- while NEVER overriding a value the operator set explicitly (resize=remote /
autoconnect=false survive), never touching the existing query bytes, inserting
before any #fragment, and never raising (empty -> "" so configured:false holds).

RED on pristine v3.66.265 (proven before implementing): novnc_url() returns the
env value verbatim, so the no-param / explicit-resize / fragment / existing-query
cases all lack the injected defaults and fail.
GREEN after _embed_url_with_defaults lands.

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins; restore os.environ
in try/finally (monkeypatch is unreliable in this harness).
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import cockpit_core as cc  # noqa: E402


def _with_env(value, fn):
    """Run fn() with BD_NOVNC_URL set to value, restoring the prior env after."""
    prev = os.environ.get("BD_NOVNC_URL")
    try:
        if value is None:
            os.environ.pop("BD_NOVNC_URL", None)
        else:
            os.environ["BD_NOVNC_URL"] = value
        return fn()
    finally:
        if prev is None:
            os.environ.pop("BD_NOVNC_URL", None)
        else:
            os.environ["BD_NOVNC_URL"] = prev


def _params(url):
    """Return the set of query param NAMES (lowercased) of a URL."""
    core = url.split("#", 1)[0]
    q = core.split("?", 1)[1] if "?" in core else ""
    return {seg.split("=", 1)[0].strip().lower() for seg in q.split("&") if seg}


def test_empty_stays_empty():
    """No BD_NOVNC_URL -> "" so the endpoint still reports configured:false."""
    assert _with_env(None, cc.novnc_url) == ""
    assert _with_env("", cc.novnc_url) == ""


def test_no_params_gets_both_defaults():
    out = _with_env("http://10.0.70.20:6080/vnc.html", cc.novnc_url)
    assert "resize=scale" in out, f"resize=scale not injected: {out!r}"
    assert "autoconnect=true" in out, f"autoconnect=true not injected: {out!r}"
    # exactly one '?' and the params hang off the real path
    assert out.startswith("http://10.0.70.20:6080/vnc.html?"), out


def test_explicit_resize_is_preserved():
    """An operator-set resize=remote must NOT be overridden to scale."""
    out = _with_env("http://h:6080/vnc.html?resize=remote", cc.novnc_url)
    assert "resize=remote" in out, f"explicit resize lost: {out!r}"
    assert "resize=scale" not in out, f"explicit resize overridden: {out!r}"
    # autoconnect still filled (it was absent)
    assert "autoconnect=true" in out, out


def test_explicit_autoconnect_preserved_and_resize_added():
    out = _with_env("http://h:6080/vnc.html?autoconnect=false", cc.novnc_url)
    assert "autoconnect=false" in out, f"explicit autoconnect lost: {out!r}"
    assert "autoconnect=true" not in out, f"explicit autoconnect overridden: {out!r}"
    assert "resize=scale" in out, f"resize not added alongside: {out!r}"


def test_all_params_present_unchanged():
    """When every gap-filled param is already set, the URL is returned
    unchanged (no duplicates). BUG-2 added reconnect defaults, so a no-op now
    requires resize + autoconnect + reconnect + reconnect_delay all present."""
    url = ("http://h:6080/vnc.html?resize=off&autoconnect=false"
           "&reconnect=false&reconnect_delay=500")
    out = _with_env(url, cc.novnc_url)
    assert out == url, f"URL with all params should be untouched: {out!r}"


def test_reconnect_defaults_added_when_absent():
    """BUG-2: a resize can bounce the VNC socket; without auto-reconnect noVNC
    drops to its manual connect screen (reads as a password re-prompt). The
    embed URL must fill reconnect=true + reconnect_delay when absent."""
    out = _with_env("http://h:6080/vnc.html", cc.novnc_url)
    assert "reconnect=true" in out, f"reconnect not injected: {out!r}"
    assert "reconnect_delay=2000" in out, f"reconnect_delay not injected: {out!r}"


def test_explicit_reconnect_preserved():
    out = _with_env("http://h:6080/vnc.html?reconnect=false", cc.novnc_url)
    assert "reconnect=false" in out and out.count("reconnect=") == 1, \
        f"explicit reconnect overridden/duplicated: {out!r}"


def test_existing_query_appends_with_ampersand():
    out = _with_env("http://h:6080/vnc.html?reconnect=true", cc.novnc_url)
    assert "reconnect=true" in out, f"existing param lost: {out!r}"
    assert "resize=scale" in out and "autoconnect=true" in out, out
    # no '??' and only one '?'
    assert out.count("?") == 1, out


def test_fragment_params_go_before_hash():
    out = _with_env("http://h:6080/vnc.html#sec", cc.novnc_url)
    # the injected query must precede the fragment
    assert "?" in out and "#sec" in out, out
    assert out.index("?") < out.index("#"), f"query must precede fragment: {out!r}"
    assert "resize=scale" in out and "autoconnect=true" in out, out


def test_does_not_raise_on_oddball():
    """Never raises (it runs inside a request handler); odd input returns a string."""
    out = _with_env("not a url", cc.novnc_url)
    assert isinstance(out, str)
