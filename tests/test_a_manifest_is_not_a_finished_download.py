"""A streaming manifest is a few hundred bytes of text, and BD can record it `done`.

MEASURED, against the real fixture, in this container:

    manifest: 204 bytes, content-type='application/vnd.apple.mpegurl'
      first 7 bytes: b'#EXTM3U'
    _looks_like_media('application/vnd.apple.mpegurl', b'#EXTM3U...')  -> True
    _probe_outcome(200, 204, 'application/vnd.apple.mpegurl', head)    -> 'done'

204 bytes of `#EXTINF` lines, reported as a completed download. Since v3.66.819
`bytes_fetched` would record 204 as well, so the row reads as a genuine transfer
of a genuine file. It is neither: a manifest is an INDEX of segments. Nothing in
it is video.

`runner_transport.py:472` accepts it deliberately --

    if h[:7] == b"#EXTM3U":                                   # HLS playlist
        return True

-- and for `_looks_like_media`'s stated purpose that is RIGHT. Its docstring says
"plausibly downloadable MEDIA", and a manifest is media; it is the thing you hand
to ffmpeg. The defect is one layer up, in `_probe_outcome`, which has only two
verdicts to map that onto:

    return "done" if TransportMixin._looks_like_media(ctype, head) else "non_media"

`non_media` would be a lie (it IS media) and `done` is a lie (nothing was
downloaded). The missing verdict is the true one: this is a STREAM, and it needs
the segmented downloader rather than a file save. So `_looks_like_media` keeps
its meaning and `_probe_outcome` gains a third outcome.

`tests/test_v3_66_282_media_verdict.py:36` pins the old behaviour --
`O(206, 8192, "application/octet-stream", b"#EXTM3U") == "done"` -- so that
assertion is a corrected expectation in this cut, not a deletion. It was added by
BP-VH1 to stop a 2xx HTML login-wall being reported `done`, which it does
correctly; the manifest case simply was not considered.

HOW LIVE IS THIS? LATENT, and saying so precisely matters. The deploy host's
seeded HLS rows are `needs_review`, not `done`:

    needs_review  'no dl event; scored ok but no download fired;
                   saw: 1080p(?):Download 1080p (HLS) /hls/scen | 1080p(?):1080p'

six of them, across four days. The browser path bails at `expect_download`
timing out -- measured locally too: clicking `<a href='/hls/scene/2.m3u8'>`
NAVIGATES (page url after click became the manifest) and fires no download event
-- so the HTTP probe is never reached for these rows and `_probe_outcome` never
runs on them. The false `done` is reachable only where BD takes the probe path
for a manifest link. It is a hole, not an active fire, and this cut closes it
before the follow-up cut (routing manifests through hls_downloader) makes the
probe path reachable for exactly these URLs.

AND THE NEEDS_REVIEW MESSAGE DOES NOT SAY WHY. "scored ok but no download fired"
is true and is the wrong half of the story: the link WAS the right link -- BD
scored it correctly as the 1080p HLS download -- and browsers navigate manifests
instead of downloading them. An operator reading that message six times has no
way to know the cause is the link's TYPE rather than a selector problem, and the
existing hint offers them "set Trigger Selector", which would not help. The
cause is knowable at that exact point, from the href, so it is stated.

RED-first: R1 through R5 fail on pristine source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import hls_downloader  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

# The real bytes and content-type the fixture serves, transcribed from the
# measurement rather than invented.
_MANIFEST_HEAD = (b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\n"
                  b"#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:6.0,\n/hls/seg/2_0.ts\n")
_MANIFEST_CT = "application/vnd.apple.mpegurl"
_MANIFEST_LEN = 204

_MP4_HEAD = b"\x00\x00\x00\x18ftypisom"
_DASH_CT = "application/dash+xml"


# ── canary ───────────────────────────────────────────────────────────────────

def test_the_fixture_bytes_are_really_a_manifest():
    """CANARY. Every assertion below turns on these bytes being recognised as a
    manifest. If they are not, the file reports OK for the wrong reason."""
    assert _MANIFEST_HEAD[:7] == b"#EXTM3U", "the fixture head is not a playlist"
    assert hls_downloader.is_hls_content_type(_MANIFEST_CT), (
        f"{_MANIFEST_CT!r} is not recognised as an HLS content type by "
        f"hls_downloader, which is the single source of truth for that question")


# ── R1: the outcome ──────────────────────────────────────────────────────────

def test_a_manifest_is_not_a_completed_download():
    """R1 -- THE DEFECT.

    204 bytes of segment index, reported `done`, with bytes_fetched=204 to match.
    """
    got = SiteRunner._probe_outcome(200, _MANIFEST_LEN, _MANIFEST_CT,
                                    _MANIFEST_HEAD)
    assert got != "done", (
        f"_probe_outcome returned {got!r} for a {_MANIFEST_LEN}-byte HLS "
        f"manifest. That records a segment INDEX as a finished video download, "
        f"and since v3.66.819 bytes_fetched carries the same 204 -- so the row "
        f"reads as a real transfer of a real file. Nothing in a manifest is "
        f"video.")
    assert got == "streaming", (
        f"expected the outcome 'streaming', got {got!r}. 'non_media' would be "
        f"its own falsehood: a manifest IS media -- it is what you hand ffmpeg "
        f"-- so _looks_like_media is right to accept it and the missing verdict "
        f"is the true third one.")


def test_a_dash_manifest_is_treated_the_same_way():
    """DASH is the same shape of problem and the same helper knows it. Leaving it
    out would fix half a class."""
    got = SiteRunner._probe_outcome(200, 900, _DASH_CT, b"<?xml version=\"1.0\"?>")
    assert got == "streaming", f"a DASH manifest returned {got!r}"


def test_looks_like_media_still_accepts_a_manifest():
    """The layer below must NOT change.

    _looks_like_media answers "is this plausibly media", and a manifest is. It
    was added to that table deliberately at v3.66.282. Moving the manifest to
    False would make the probe say `non_media`, which is a different wrong
    answer, and it would also change the meaning of a predicate other code and
    tests read.
    """
    assert SiteRunner._looks_like_media(_MANIFEST_CT, b"") is True
    assert SiteRunner._looks_like_media("application/octet-stream",
                                        _MANIFEST_HEAD) is True
    assert SiteRunner._looks_like_media(_DASH_CT, b"") is True


# ── the outcomes that must not move ──────────────────────────────────────────

def test_a_real_mp4_is_still_done():
    assert SiteRunner._probe_outcome(200, 8192, "video/mp4", _MP4_HEAD) == "done"
    assert SiteRunner._probe_outcome(
        206, 8192, "application/octet-stream", _MP4_HEAD) == "done"


def test_a_non_media_2xx_is_still_non_media():
    """BP-VH1's original subject: a 2xx HTML login-wall with bytes."""
    assert SiteRunner._probe_outcome(
        200, 4096, "text/html", b"<html><body>Sign in") == "non_media"
    assert SiteRunner._probe_outcome(
        200, 4096, "application/json", b'{"x":1}') == "non_media"


def test_a_failure_is_still_a_failure():
    assert SiteRunner._probe_outcome(403, 0, "", b"") == "fail"
    assert SiteRunner._probe_outcome(200, 0, "video/mp4", b"") == "fail"
    # A non-2xx MANIFEST is a failure, not a stream: the streaming verdict must
    # not override the status check, or a 404 page served as mpegurl would read
    # as a stream waiting to be downloaded.
    assert SiteRunner._probe_outcome(
        404, 120, _MANIFEST_CT, _MANIFEST_HEAD) == "fail"


# ── R2: the verdict the operator reads ───────────────────────────────────────

def test_the_probe_routes_a_stream_to_needs_review_naming_the_cause():
    """R2 -- the outcome has to reach a verdict, and the verdict has to explain.

    A third outcome nothing handles would fall into the probe caller's `else`
    branch and be reported as "probe failed: status=200" -- which is false, and
    a worse report than the one it replaced because it accuses the server.
    """
    import ast
    src = (ROOT / "bulk_downloader" / "runner_transport.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.FunctionDef)
                and any("_probe_outcome" in ast.unparse(c)
                        for c in ast.walk(node) if isinstance(c, ast.Call))
                and node.name != "_probe_outcome"):
            fn = node
            break
    assert fn is not None, "no caller of _probe_outcome found"

    # SCOPED TO THE STREAMING BRANCH, and requiring BOTH facts.
    #
    # The first draft asserted `"manifest" in body or "segmented" in body` over
    # the whole function. A mutation renaming the note to "probe: unexpected"
    # survived it, because the other half of the same f-string still said
    # "segmented downloader" -- an `or` makes either half sufficient, which is
    # the wrong-quantifier defect (the same shape as an `any()` that one branch
    # satisfies alone). Find the branch, read ITS strings, require both.
    branch = None
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        rendered = ast.unparse(node)
        if "outcome" in rendered and "streaming" in rendered:
            branch = node
            break
    assert branch is not None, (
        f"the probe caller ({fn.name}) does not test for the 'streaming' "
        f"outcome, so it falls through to the else branch and reports 'probe "
        f"failed: status=200' -- blaming the server for a manifest BD simply "
        f"cannot save as a file.")

    # The If whose test is that comparison, so the note strings are the branch's.
    owner = next((n for n in ast.walk(fn)
                  if isinstance(n, ast.If) and n.test is branch), None)
    assert owner is not None, "the streaming comparison is not an if-test"
    notes = " ".join(c.value for c in ast.walk(owner)
                     if isinstance(c, ast.Constant)
                     and isinstance(c.value, str)).lower()
    for word in ("manifest", "segmented"):
        assert word in notes, (
            f"the streaming branch's verdict does not contain {word!r}. The "
            f"operator reads this note and needs both facts: that the response "
            f"was a stream index (not a transfer failure) and that the "
            f"segmented downloader is what handles it. Branch text was: "
            f"{notes[:200]!r}")


# ── R3: the browser branch must say why the click did nothing ────────────────

def test_the_no_download_event_hint_names_a_manifest():
    """R3 -- six identical needs_review rows over four days, none saying why.

    Measured on the deploy host:

        'no dl event; scored ok but no download fired;
         saw: 1080p(?):Download 1080p (HLS) /hls/scen | ...'

    and measured locally: clicking `<a href='/hls/scene/2.m3u8'>` navigates to
    the manifest and fires no download event. The link was CORRECT -- BD scored
    it as the 1080p HLS download -- so "scored ok but no download fired" points
    the reader at their selectors, and the existing alternative hint literally
    offers "set Trigger Selector", which cannot help. The cause is the link's
    TYPE, and it is knowable right there from the href.
    """
    import ast
    src = (ROOT / "bulk_downloader" / "runner_transport.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    hits = [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and "no dl event" in n.value]
    assert hits, "the 'no dl event' message is gone -- re-derive this gate"
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and any(isinstance(c, ast.Constant)
                       and isinstance(c.value, str)
                       and "no dl event" in c.value
                       for c in ast.walk(n))), None)
    assert fn is not None, "no function contains the 'no dl event' message"

    # THE HELPER MUST BE CALLED, not merely mentioned.
    #
    # The first draft asserted `"is_streaming_url" in body or "manifest" in
    # body`. Replacing the call with `streaming = False` survived it, because the
    # hint STRING still contained the word "manifest" -- so the message promised
    # a check that no longer happened, which is worse than not checking. A Call
    # node cannot be faked by prose.
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and ((isinstance(n.func, ast.Attribute)
                   and n.func.attr == "is_streaming_url")
                  or (isinstance(n.func, ast.Name)
                      and n.func.id == "is_streaming_url"))]
    assert calls, (
        f"{fn.name} reports 'no download fired' without ever CALLING "
        f"is_streaming_url. hls_downloader owns that question and has no caller "
        f"outside runner_extractors.py -- the capability exists and the code "
        f"path that needs it never asks. A hint that merely says 'manifest' "
        f"while the check is gone is a message promising an answer nobody "
        f"computed.")
    body = ast.unparse(fn).lower()
    assert "navigate" in body, (
        f"{fn.name} does not tell the operator WHY no event fired -- that a "
        f"browser navigates a manifest rather than downloading it. Measured "
        f"locally: after the click the page URL became the manifest. Without "
        f"that sentence the existing hint sends them to Trigger Selector, which "
        f"cannot help.")


# ── R4: the helper that answers this must be reused, not re-implemented ──────

def test_the_streaming_predicate_is_not_a_second_copy_of_the_extension_list():
    """A local `if url.endswith('.m3u8')` would pass every assertion above and
    start the three-copies-drift that CLAUDE.md section 5 records for the system
    package lists -- where the copy nobody updated is the one the box runs.

    hls_downloader owns _HLS_EXTS / _DASH_EXTS and exposes is_streaming_url,
    is_hls_content_type and is_dash_content_type. Those are the denominator.
    """
    import ast
    path = ROOT / "bulk_downloader" / "runner_transport.py"
    src = path.read_text(encoding="utf-8")
    assert "hls_downloader" in src, (
        "runner_transport.py answers the manifest question without consulting "
        "hls_downloader, which owns the extension and content-type tables.")

    # AST STRING CONSTANTS, not a text search over the file. The first draft of
    # this assertion searched the raw source for ".m3u8'" and failed on the
    # PROSE of the comment explaining why the extension list must not be
    # duplicated -- a gate firing on text that was not its subject, which is
    # CLAUDE.md section 0's inverse: over-sensitivity is a soundness bug because
    # a gate that cries wolf gets switched off. `#` comments are not in the AST
    # at all; docstrings are, so they are excluded explicitly.
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            for ext in (".m3u8", ".mpd", ".m3u"):
                if ext in node.value:
                    offenders.append(f"line {node.lineno}: {node.value[:60]!r}")
    assert not offenders, (
        "runner_transport.py carries streaming extensions as its own string "
        "constants -- a second copy of hls_downloader's table:\n  "
        + "\n  ".join(offenders) +
        "\nTwo copies of a denominator drift, and the one nobody updates is the "
        "one that runs.")


def test_hls_downloader_helpers_resolve():
    """Name resolution, not just parsing (CLAUDE.md section 6)."""
    assert hls_downloader.is_streaming_url("http://x/y/2.m3u8") is True
    assert hls_downloader.is_streaming_url("http://x/y/2.mpd") is True
    assert hls_downloader.is_streaming_url("http://x/y/2.mp4") is False
    assert hls_downloader.is_hls_content_type(_MANIFEST_CT) is True
    assert hls_downloader.is_dash_content_type(_DASH_CT) is True
