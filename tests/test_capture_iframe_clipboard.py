"""Capture-workflow noVNC iframe — clipboard permission pin.

The SPA `/capture` live-session iframe (CaptureWorkflow.tsx) embeds the
operator's held-open noVNC session. During the manual-login step the operator
pastes credentials INTO the remote session; the browser only allows the
clipboard bridge when the iframe carries an `allow` Permissions-Policy granting
clipboard-read/clipboard-write. The cockpit noVNC iframe (cockpit_console.py)
already grants this; the SPA iframe did NOT (verified empirically: the mounted
iframe's `allow` attribute came back null). This pins the parity.

Scope: the assertion is bound to the live-session iframe specifically
(the element carrying `title="live capture session"` + `src={novncUrl}`), not
to any incidental occurrence elsewhere in the file.

RED on pristine v3.66.265 (proven before implementing): the live-session
iframe has no `allow` attribute, so both assertions fail.
GREEN after the one-line attribute lands.

run_tests.py conventions: zero-arg test functions; repo root from
Path(__file__).resolve().parent.parent; no pytest builtins.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

CAPTURE = REPO / "frontend" / "src" / "routes" / "CaptureWorkflow.tsx"


def _live_session_iframe_tag() -> str:
    """Return the opening <iframe ...> tag of the live-session iframe.

    Locates the iframe by its `title="live capture session"` marker and returns
    the full opening tag (from `<iframe` to the closing `>` or `/>`)."""
    src = CAPTURE.read_text(encoding="utf-8")
    # find every <iframe ...> ... opening tag and keep the one whose body
    # carries the live-session title.
    for m in re.finditer(r"<iframe\b[^>]*?/?>", src, re.DOTALL):
        tag = m.group(0)
        if "live capture session" in tag:
            return tag
    return ""


def test_live_session_iframe_exists():
    """Sanity: the live-session iframe is present in the source (guards against
    a future rename silently turning the clipboard pin into a vacuous pass)."""
    tag = _live_session_iframe_tag()
    assert tag, "could not find the live-session <iframe> (title='live capture session') in CaptureWorkflow.tsx"
    # The iframe must embed the noVNC URL. BUG-2 (v3.66.599) froze the src on the
    # first live URL so a window resize can't remount the iframe and re-open the
    # VNC socket (re-prompting for the password), so the binding is the frozen
    # ref rather than the raw query value -- accept either.
    assert ("src={novncUrl}" in tag or "src={frozenNovncUrl}" in tag), \
        "live-session iframe should embed the noVNC URL (novncUrl / frozenNovncUrl)"


def test_live_session_iframe_grants_clipboard():
    """The live-session iframe MUST carry an `allow` granting clipboard-read +
    clipboard-write, so password paste-through into the noVNC session works
    (parity with the cockpit noVNC iframe). RED on pristine 265."""
    tag = _live_session_iframe_tag()
    assert tag, "live-session iframe not found"
    # must be an allow= attribute (Permissions-Policy), carrying both tokens.
    allow_m = re.search(r'allow\s*=\s*"([^"]*)"', tag)
    assert allow_m, (
        "live-session iframe has no allow= attribute; noVNC clipboard "
        "paste-through will be blocked by the browser. Add "
        'allow="clipboard-read; clipboard-write".'
    )
    allow_val = allow_m.group(1)
    assert "clipboard-read" in allow_val, f"allow= missing clipboard-read: {allow_val!r}"
    assert "clipboard-write" in allow_val, f"allow= missing clipboard-write: {allow_val!r}"
