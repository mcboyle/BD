"""BD scrapes the right HLS link, then clicks it and waits 60s for nothing.

CUT B of #19. Cut A (#72) stopped a manifest being recorded as a finished
download and made the needs_review message name the cause. This makes the
download actually happen.

THE DEFECT, measured on the deploy host six times across four days:

    needs_review  'no dl event; scored ok but no download fired;
                   saw: 1080p(?):Download 1080p (HLS) /hls/scen | 1080p(?):1080p'

and reproduced locally against the real fixture:

    scraped download-link href: '/hls/scene/2.m3u8'
    NO DOWNLOAD EVENT: TimeoutError 6000ms
      page url after click: http://127.0.0.1:8899/hls/scene/2.m3u8

The link is CORRECT -- BD scores it as the 1080p HLS download. A browser
NAVIGATES a manifest rather than downloading it, so `page.expect_download` can
never fire, and `runner_transport.py:780` waits the full 60000ms before giving
up. Every seeded HLS URL costs a minute of the capture to produce a row that
says nothing happened.

THE CAPABILITY ALREADY EXISTS AND WAS UNREACHABLE. `hls_downloader.download`
drives ffmpeg over the manifest and returns bytes_written; it never raises. But
it is called only from `runner_extractors.py` -- the jsonapi, vixen, aylo and
plugin extractors -- none of which applies to a generic scraped site. ffmpeg is
present on the box (`/usr/bin/ffmpeg`, `is_available() -> True`), so this was
never an environment problem: the generic scrape-and-click path simply had no
branch that asked.

WHAT THIS CUT DOES. `_do_download` reads the candidate's href BEFORE clicking. If
it is a streaming manifest, the click is skipped entirely -- no 60s timeout -- and
the transfer goes through hls_downloader at the point the HTTP/Playwright
transfer would have happened. Everything else is reused: the filename template,
`safe_dest`, the skip_if_exists check and the final db_log. `bytes_written`
becomes `bytes_fetched`, so a real stream records a real transfer count rather
than a manifest's 204 bytes.

THE ROUTING DECISION IS A PURE FUNCTION, deliberately. `_do_download` is 500
lines of browser-coupled code and its transfer point sits ~130 lines below its
detection point; a decision buried in there could only be checked by asserting
over source. `_stream_route(href, page_url)` takes two strings and returns two,
so the part that decides is tested on behaviour and the wiring is what the AST
assertions cover.

RED-first: every assertion below fails on pristine source.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import hls_downloader  # noqa: E402
from bulk_downloader.runner import SiteRunner  # noqa: E402

_RT = ROOT / "bulk_downloader" / "runner_transport.py"
_PAGE = "http://127.0.0.1:8899/hlspage/2?bdseed=1"


def _rt_fn(name):
    tree = ast.parse(_RT.read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    return None


# ── the pure decision ────────────────────────────────────────────────────────

def test_a_relative_manifest_href_is_resolved_against_the_page():
    """The measured href is relative: '/hls/scene/2.m3u8'.

    The browser resolves a relative href natively when clicked; ffmpeg gets a
    string and cannot. This is the same trap Phase 19.fix records for the
    direct-URL path ("Request URL is missing scheme").
    """
    url, name = SiteRunner._stream_route("/hls/scene/2.m3u8", _PAGE)
    assert url == "http://127.0.0.1:8899/hls/scene/2.m3u8", (
        f"relative manifest href resolved to {url!r}")
    assert name, "no filename was suggested"


def test_an_absolute_manifest_href_is_left_alone():
    abs_url = "https://cdn.example.invalid/v/master.m3u8?token=abc"
    url, _ = SiteRunner._stream_route(abs_url, _PAGE)
    assert url == abs_url, f"absolute href was rewritten to {url!r}"


def test_the_suggested_name_is_mp4_because_ffmpeg_remuxes():
    """ffmpeg writes an MP4 container from the segments, so the destination must
    not be named .m3u8 -- runner_extractors.py:2032 makes the same choice."""
    _, name = SiteRunner._stream_route("/hls/scene/2.m3u8", _PAGE)
    assert name.endswith(".mp4"), f"suggested name was {name!r}"
    assert "m3u8" not in name, (
        f"the destination is named after the manifest ({name!r}); ffmpeg is "
        f"remuxing to MP4, so a .m3u8 file name would be a lie about the "
        f"content and would defeat skip_if_exists on the next run.")


def test_the_name_carries_something_identifying():
    """A directory full of 'download.mp4' is not navigable. The manifest path
    has a scene number in it and it should survive."""
    _, name = SiteRunner._stream_route("/hls/scene/2.m3u8", _PAGE)
    assert "2" in name, f"nothing identifying survived into {name!r}"


def test_a_dash_manifest_routes_too():
    url, name = SiteRunner._stream_route("/dash/scene/2.mpd", _PAGE)
    assert url and url.endswith(".mpd"), f"DASH href gave {url!r}"
    assert name.endswith(".mp4"), f"DASH name gave {name!r}"


@pytest.mark.parametrize("href", [
    "/direct/media/2.mp4",
    "https://x.invalid/a/b.mkv",
    "/download?id=7",
    "",
    None,
])
def test_a_non_manifest_href_is_not_routed(href):
    """The ordinary path must be untouched: this branch is only for streams."""
    url, name = SiteRunner._stream_route(href, _PAGE)
    assert url is None and name is None, (
        f"{href!r} was routed to the segmented downloader as {url!r}")


def test_the_predicate_is_hls_downloaders_and_not_a_local_copy():
    """Same rule as #72: hls_downloader owns _HLS_EXTS/_DASH_EXTS, and a second
    copy of a denominator drifts. Asserted over AST string constants, so the
    prose of a comment cannot trip it -- that mistake was made in #72 and caught
    by its own gate."""
    tree = ast.parse(_RT.read_text(encoding="utf-8"))
    docstrings = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if (isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    offenders = [f"line {n.lineno}: {n.value[:50]!r}" for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in docstrings
                 and any(e in n.value for e in (".m3u8", ".mpd", ".m3u"))]
    assert not offenders, (
        "runner_transport.py carries streaming extensions as its own string "
        "constants:\n  " + "\n  ".join(offenders))


# ── the wiring: skip the click, and transfer through ffmpeg ──────────────────

def test_the_click_is_skipped_for_a_manifest():
    """THE 60 SECONDS. `page.expect_download(timeout=60000)` can never fire for
    a manifest, so every seeded HLS URL burns a full minute of the capture
    before recording that nothing happened. The route has to be decided BEFORE
    the click, not after the timeout.
    """
    fn = _rt_fn("_do_download")
    assert fn is not None, "_do_download not found"

    # AST NODE POSITIONS, not text offsets in the unparsed source. The first
    # draft compared `src.index(...)` and failed, because _do_download's own
    # DOCSTRING says "Skips Playwright's expect_download entirely" -- so the
    # comparison was against prose at offset 504, not against the call. Third
    # time this session that a text search matched an explanation of the thing
    # instead of the thing; Call nodes cannot be written in a comment.
    def _first_call_line(pred):
        lines = [n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and pred(n)]
        return min(lines) if lines else None

    route_at = _first_call_line(
        lambda n: (isinstance(n.func, ast.Attribute)
                   and n.func.attr == "_stream_route"))
    click_at = _first_call_line(
        lambda n: (isinstance(n.func, ast.Attribute)
                   and n.func.attr == "expect_download"))

    assert route_at is not None, (
        "_do_download never CALLS _stream_route, so a manifest still goes to "
        "expect_download and still waits 60000ms for an event that cannot "
        "arrive.")
    assert click_at is not None, (
        "expect_download is no longer called at all -- re-derive this gate, "
        "because the ordering it protects may no longer be meaningful.")
    assert route_at < click_at, (
        f"_stream_route is called at line {route_at} but expect_download at "
        f"{click_at}: the decision happens AFTER the click, so the 60s timeout "
        f"is still paid before the manifest is recognised.")


def test_the_transfer_goes_through_hls_downloader():
    fn = _rt_fn("_do_download")
    src = ast.unparse(fn)
    # Row 439: the arm now reaches ffmpeg through `self._hls_download(_hls, ...)`,
    # the fail-closed egress seam, rather than calling `_hls.download` itself.
    # Both shapes count -- what this gate is about is that the manifest reaches
    # the segmented downloader at all, not which name the hop wears.
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("download", "_hls_download")]
    assert calls, (
        "_do_download never calls a .download(...) / ._hls_download(...) -- the "
        "manifest is recognised and then handed to the ordinary HTTP transfer, "
        "which would save the manifest text as if it were the video.")
    assert "hls_downloader" in src or "_hls" in src, (
        "_do_download does not reference hls_downloader")


def test_the_real_byte_count_is_recorded_not_the_manifest_length():
    """#63's contract. `bytes_written` is what ffmpeg actually transferred; the
    manifest is 204 bytes and recording that would make a stream look like a
    trivial file. `bytes_fetched` must come from the result, not from the
    destination's size alone.
    """
    fn = _rt_fn("_do_download")

    # THE ASSIGNMENT, not the mere presence of the word. `"bytes_written" in src`
    # survived a mutation that set bytes_fetched to the muxed file's size,
    # because bytes_written still appeared in an unrelated fallback line. What
    # matters is which expression lands in bytes_fetched.
    assigns = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if isinstance(t, ast.Name) and t.id == "bytes_fetched":
                assigns.append((n.lineno, ast.unparse(n.value)))
    assert assigns, "_do_download never assigns bytes_fetched"
    from_result = [a for a in assigns if "bytes_written" in a[1]]
    assert from_result, (
        f"no assignment to bytes_fetched reads bytes_written off the "
        f"DownloadResult. Assignments found: {assigns}. The muxed file's size on "
        f"disk is NOT what crossed the wire -- ffmpeg remuxes, so the two "
        f"differ -- and #63's contract is the transferred count.")


def test_a_missing_ffmpeg_is_named_not_silently_skipped():
    """hls_downloader returns error='ffmpeg_not_installed' as a distinct code.
    ffmpeg IS on the deploy host, but a host without it must get a verdict that
    says so rather than a generic failure -- otherwise the operator cannot tell
    a missing dependency from a broken stream.
    """
    fn = _rt_fn("_do_download")

    # THE COMPARISON, not the word. `... or "ffmpeg" in src.lower()` survived a
    # mutation that removed the branch entirely, because the note text still
    # said "ffmpeg" -- an `or` makes either half sufficient, which is the
    # wrong-quantifier defect, hit four separate times in this session's cuts.
    compares = [n for n in ast.walk(fn) if isinstance(n, ast.Compare)]
    tested = [ast.unparse(c) for c in compares
              if any(isinstance(x, ast.Constant)
                     and x.value == "ffmpeg_not_installed"
                     for x in ast.walk(c))]
    assert tested, (
        "_do_download never COMPARES against the 'ffmpeg_not_installed' error "
        "code, so a missing dependency is reported the same way as a broken "
        "stream. hls_downloader returns that code precisely so the two can be "
        "told apart, and an operator cannot act on them the same way.")


def test_hls_downloader_contract_holds():
    """Name resolution, not parsing (section 6). If any of these move, the
    wiring above is asserting against an API that no longer exists."""
    assert callable(hls_downloader.download)
    assert callable(hls_downloader.is_available)
    assert callable(hls_downloader.is_streaming_url)
    fields = hls_downloader.DownloadResult.__dataclass_fields__
    for f in ("ok", "bytes_written", "error", "error_detail", "output_path"):
        assert f in fields, f"DownloadResult lost {f!r}: {sorted(fields)}"


# ── L12's verdict was true only while this was broken ────────────────────────

def test_l12_no_longer_claims_the_generic_path_has_no_hls_handling():
    """CORRECTED EXPECTATION.

    L12 currently returns NA with:

        "BD's generic scrape-and-click path has no HLS handling -- it lives only
         in site-specific extractors -- so absence here is not evidence of a
         fault"

    That was accurate and is now false. Leaving it would make the check assert
    the absence of a capability that exists, which is the same class of stale
    claim as the comments #73 had to correct.
    """
    from live_tests import checks
    src = ast.unparse(next(
        n for n in ast.walk(ast.parse((ROOT / "live_tests" / "checks.py")
                                      .read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef) and n.name.startswith("l12")))
    assert "no HLS handling" not in src, (
        "L12 still says the generic scrape-and-click path has no HLS handling. "
        "It does now, so that sentence tells the operator a capability is "
        "absent when it is present.")
    assert callable(checks.l12_hls_dash_segmented_download)


# ── the crash #75 shipped, and the control-flow property that prevents it ─────

def test_the_stream_branch_and_the_http_branch_are_one_chain():
    """THE REGRESSION #75 SHIPPED, caught on the box and not by this file.

    Measured on the deploy host, with the routing working exactly as intended:

        download: streaming manifest -> segmented downloader
                  (http://127.0.0.1:8899/hls/scene/2.m3u8)
        pending: Retry 1/2 in 10m --
                 worker error: '_DLStub' object has no attribute 'save_as'

    The route fired, ffmpeg was reached, and then the SUCCESS path fell into the
    Playwright fallback. The cause was two independent `if`s:

        if is_stream:            # transfer via ffmpeg, then...
            use_http = False     # ...clear the flag
        if use_http:             # a SEPARATE if
            ...
        else:
            self._pw_save(dl, final_path)   # <- reached, with dl a _DLStub

    Clearing the flag did not skip the transfer selection, it selected the ELSE.
    `_pw_save` calls `dl.save_as`, and the stream path's `dl` is the `_DLStub`
    stand-in (url + suggested_filename + cancel only), so every streamed download
    crashed after transferring its bytes.

    THE PROPERTY, and it is why this is structural rather than behavioural:
    exactly one of the three transfer paths may run, which means they must be ONE
    if/elif/else chain. #75's gate asserted that _stream_route is called and that
    hls_downloader.download is called -- both true, both insufficient, because
    neither says anything about what happens AFTER. Control flow through a
    500-line function is not visible to a presence assertion, and that limitation
    was written into #75's own PR description before it bit.
    """
    fn = _rt_fn("_do_download")
    assert fn is not None

    stream_if = None
    for n in ast.walk(fn):
        if (isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                and n.test.id == "is_stream"):
            stream_if = n
            break
    assert stream_if is not None, "no `if is_stream:` branch in _do_download"

    def _mentions_use_http(node):
        return any(isinstance(x, ast.Name) and x.id == "use_http"
                   for x in ast.walk(node))

    chained = [s for s in stream_if.orelse
               if isinstance(s, ast.If) and _mentions_use_http(s.test)]
    assert chained, (
        "the `if is_stream:` branch does not chain to the use_http branch, so "
        "they are independent `if`s and the stream path falls through into the "
        "HTTP selection -- and on success into its `else`, which calls "
        "_pw_save(dl, ...) with the _DLStub. That is the exact crash the deploy "
        "host reported: \"'_DLStub' object has no attribute 'save_as'\".")


def test_the_stream_branch_does_not_fake_the_skip_with_a_flag():
    """`use_http = False` inside the stream branch was the broken fix. With a
    real if/elif chain it is not merely redundant -- it is misleading, because it
    looks like it prevents something it never prevented."""
    fn = _rt_fn("_do_download")
    stream_if = next((n for n in ast.walk(fn)
                      if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                      and n.test.id == "is_stream"), None)
    assert stream_if is not None
    assigns = [ast.unparse(s) for s in ast.walk(stream_if)
               if isinstance(s, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "use_http"
                       for t in s.targets)]
    assert not assigns, (
        f"the stream branch still assigns use_http: {assigns}. Clearing the flag "
        f"was the broken fix -- it selected the else rather than skipping the "
        f"chain. The chain structure is what makes the paths exclusive.")


def test_pw_save_is_only_reachable_from_the_browser_path():
    """_pw_save takes a real Playwright Download. The stream path's `dl` is a
    stub, so any route to _pw_save that a stream can reach is a crash."""
    fn = _rt_fn("_do_download")
    stream_if = next((n for n in ast.walk(fn)
                      if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
                      and n.test.id == "is_stream"), None)
    # THE BRANCH'S BODY, not its whole subtree. ast.walk(If) descends into
    # `orelse` too, and once the fix made these an if/elif/else chain the final
    # `else: self._pw_save(...)` became a NESTED node of the is_stream If -- so
    # walking the node found it and the assertion failed about correct code. The
    # question is what the stream branch EXECUTES, which is `.body`.
    calls = [n.lineno for stmt in stream_if.body for n in ast.walk(stmt)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_pw_save"]
    assert not calls, (
        f"_pw_save is called inside the stream branch's body at line(s) "
        f"{calls}; its `dl` is a _DLStub with no save_as.")
