"""media_route -- the two transport routing decisions, as pure functions of (href, page_url).

o1673-t154-edge1 (T154 edge #1). These lived on runner_transport.TransportMixin
as _stream_route / _direct_media_route. detect.py asks the same two questions
(_fetched_without_click, dl95-eporner-3) and imported the runner to do it -- the
only recognizer -> runner edge T154 measured. Both take two strings and return
two, touch no browser and no runner state, so they live here in core; detect.py
imports this module and TransportMixin keeps staticmethod delegates, so every
transport caller and test keeps its spelling.

hls_downloader stays the owner of the streaming table: both functions ask
is_streaming_url and carry no extension list of their own.
"""
from pathlib import Path

__all__ = ["stream_route", "direct_media_route"]


def stream_route(href, page_url):
    """(manifest_url, destination_name) if `href` is a stream, else (None, None).

    v3.66.819 -- THE ROUTING DECISION, AS A PURE FUNCTION.

    BD scrapes the right link and then cannot use it. Measured on the deploy
    host six times over four days, and reproduced locally against the
    fixture: clicking `<a href='/hls/scene/2.m3u8'>` NAVIGATES to the
    manifest and fires no download event, so `expect_download(timeout=60000)`
    waits a full minute per URL to record that nothing happened. The link was
    never the problem -- BD scored it correctly as the 1080p HLS download --
    the problem is that a browser does not download a manifest.

    Two things this has to get right, both learned elsewhere in this file:

    RESOLVE THE RELATIVE HREF. The measured value is `/hls/scene/2.m3u8`. A
    browser resolves that natively on click; ffmpeg receives a string and
    cannot. Phase 19.fix records the same trap on the direct-URL path
    ("Request URL is missing scheme", and worse, hitting a wrong host).

    NAME THE DESTINATION .mp4. ffmpeg remuxes the segments into an MP4
    container, so a `.m3u8` destination would be a lie about the content and
    would also defeat skip_if_exists on the next run. runner_extractors.py
    makes the same choice for the extractor HLS paths.

    Pure by design: _do_download is ~500 lines of browser-coupled code whose
    transfer point sits well below its detection point, so a decision buried
    in there could only be checked by asserting over source. This takes two
    strings and returns two.
    """
    if not href or not isinstance(href, str):
        return None, None
    try:
        from . import hls_downloader as _hls
        if not _hls.is_streaming_url(href):
            return None, None
    except Exception:
        return None, None      # never break the download path
    url = href
    if not url.startswith(("http://", "https://")):
        try:
            from urllib.parse import urljoin
            url = urljoin(page_url or "", url)
        except Exception:
            return None, None  # unresolvable is not routable
    if not url.startswith(("http://", "https://")):
        return None, None
    try:
        from urllib.parse import urlparse
        stem = Path(urlparse(url).path).stem or "stream"
    except Exception:
        stem = "stream"
    return url, f"{stem}.mp4"


def direct_media_route(href, page_url):
    """(media_url, destination_name) if `href` IS the file, else (None, None).

    v3.66.x row 384 -- THE SECOND ROUTING DECISION, AS A PURE FUNCTION.

    The direct-URL fast path above is gated on `_via_learned and url_attr`,
    and `_via_learned` is set at exactly ONE place (detect.py, the learned
    branch). So a WIDE-SWEEP winner can never reach it, however perfect its
    href, and falls through to expect_download(timeout=60000). Measured on
    test6 at v3.66.1342 with both ranking fixes deployed: BD chose
    A[href=https://content2a.nubilefilms.com/.../..._3840.mp4?st=..&e=..&dl=..],
    score 2160, size 5,368,709,120 -- the right link -- clicked it, the
    browser navigated a signed cross-host .mp4, and the job recorded
    "no dl event; scored ok but no download fired" a full minute later.

    MANIFESTS ARE NOT OURS. _stream_route is consulted first by the caller,
    and this function ALSO refuses .m3u8/.mpd outright, so the ordering
    cannot silently invert and hand ffmpeg's work to httpx.

    RESOLVE THE RELATIVE HREF -- Phase 19.fix's lesson and _stream_route's:
    a browser resolves it natively on click, httpx gets a string and cannot.

    PREFER THE SITE'S OWN NAME. `dl=` (and `filename=`) is what the site
    intends the file to be called, and it is what skip_if_exists compares
    on the next run; the path basename is the fallback.
    """
    if not href or not isinstance(href, str):
        return None, None
    raw = href.strip()
    if not raw or raw.startswith(("#", "javascript:", "mailto:", "data:")):
        return None, None
    try:
        from urllib.parse import urljoin, urlparse, parse_qs, unquote
        absolute = raw if raw.startswith(("http://", "https://")) \
            else urljoin(page_url or "", raw)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None, None
        # dl95-justporn-1: KVS sites (justporn, ok.xxx) serve the file at
        # /get_file/<n>/<hash>/<id>/<id>_720p.mp4/?...&download=true -- the
        # file name is followed by a slash. The extension is still the last
        # path segment's; without this the winner was clicked, fired nothing,
        # and the job ended "looks like a modal-trigger button".
        file_path = (parsed.path or "").rstrip("/")
        path = file_path.lower()
        # Refuse a manifest even though the caller asks _stream_route first.
        # ASK THE OWNER. hls_downloader.is_streaming_url holds the streaming
        # table; a local ".m3u8"/".mpd" tuple here would be a SECOND COPY of
        # it, which tests/test_a_manifest_is_not_a_finished_download.py
        # refuses by name -- and it caught this function doing exactly that.
        # A soft import, like the PWTimeout branch below: if hls_downloader
        # cannot be reached we cannot rule a manifest out, so we decline the
        # direct fetch and fall back to the CLICK path, which is today's
        # behaviour. Never guess our way into handing ffmpeg's work to httpx.
        try:
            from . import hls_downloader as _hls
        except Exception:
            return None, None
        if _hls.is_streaming_url(absolute):
            return None, None
        if not path.endswith((".mp4", ".m4v", ".mkv", ".mov", ".webm",
                              ".avi", ".wmv")):
            return None, None
        qs = parse_qs(parsed.query or "")
        name = ""
        for key in ("dl", "filename", "file", "download_filename"):
            if qs.get(key) and qs[key][0].strip():
                name = unquote(qs[key][0].strip())
                break
        if not name:
            name = unquote(Path(file_path).name or "")
        if not name:
            return None, None
        return absolute, name
    except Exception:
        # An unparseable href is not a direct fetch. Fail to the CLICK
        # path, which is what happens today -- never to a wrong URL.
        return None, None
