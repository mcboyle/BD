"""fx-pornhoarder-hoster-embed (O1567, bd1 10.0.70.51, 2026-09-29 21:36Z).

pornhoarder scene .../sellyourgf-jenny-manson/... ("video duration is 28:30")
embeds pornhoarder.net/player.php, whose #play-button POST loads the hoster
player luluvdo.com/e/<id> (LuluStream, JW player). The reviewed template's
learned ``video[src]`` matched a banner ad's <video> in an ad frame
(rtbbtr.com -> neonvanta.com/daourl.php), not the hoster's player:

    download: learned hit via [video[src]] (applied template user_b4b_pornhoarder_o1517_...)
    download: direct URL extracted from [src] -> kjbennet-a2080c25.mp4
    done: Saved: kjbennet-a2080c25.mp4  (9,170,402 B, 960x540, 30.0 s -- an ad)

The hoster page ships its JW setup Dean-Edwards-packed; unpacked it names the
scene's ``sources:[{file:"https://<cdn>/hls2/.../master.m3u8?t=..."}]``. The
fix: a learned <video>/<source> in a child frame yields to the media a packed
player config names -- that frame's own, else a visible player frame's. The
configuring page is re-fetched by the app's HTTP client and its token is
handed to ffmpeg with that client's UA: measured on bd1, a token issued to the
BROWSER answers 403 to ffmpeg even with the browser's exact UA, while one
issued to a plain HTTP client plays.
"""
from __future__ import annotations

import re

BD_GATE_SCOPE = "module"

from bulk_downloader import spa_media_extract as spa
from bulk_downloader import runner_transport as rt

HOSTER = "https://luluvdo.example/e/vcm60yj822wt"
MASTER = ("https://7db6af2i1kzb.tnmr.example/hls2/02/04396/6c40zbehszrj_h/"
          "master.m3u8?t=tok&s=1790718336&e=28800")
AD_CLIP = "https://ads.example/vast/kjbennet-a2080c25.mp4"
UA = "Mozilla/5.0 (X11; Linux x86_64) BrowserUnderTest/1.0"
APP_UA = "Mozilla/5.0 (Windows NT 10.0) AppClient/1.0"
PLAYER_PHP = "https://pornhoarder.example/player.php?video=abc"
FRESH = MASTER.replace("t=tok", "t=app-issued")

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _word(n, radix):
    return (_word(n // radix, radix) if n >= radix else "") + _DIGITS[n % radix]


def _pack(js, radix=36):
    """A minimal p,a,c,k,e,d packer: every word becomes its table index."""
    words = []
    for w in re.findall(r"\b\w+\b", js):
        if w not in words:
            words.append(w)
    index = {w: _word(i, radix) for i, w in enumerate(words)}
    payload = re.sub(r"\b\w+\b", lambda m: index[m.group(0)], js).replace("'", "\\'")
    return ("eval(function(p,a,c,k,e,d){e=function(c){return c.toString(a)};"
            "if(!''.replace(/^/,String)){while(c--)d[c.toString(a)]=k[c]||c.toString(a);"
            "k=[function(e){return d[e]}];e=function(){return'\\\\w+'};c=1};"
            "while(c--)if(k[c])p=p.replace(new RegExp('\\\\b'+e(c)+'\\\\b','g'),k[c]);return p}"
            "('%s',%d,%d,'%s'.split('|')))" % (payload, radix, len(words), "|".join(words)))


SETUP = ('jwplayer("vplayer").setup({sources:[{file:"%s"}],image:"https://img.example/p.jpg",'
         'tracks:[{file:"/srt/empty.srt",kind:"captions"}],width:"100%%"});' % MASTER)
HOSTER_HTML = ("<html><head><title>Jenny_Manson - Lulustream.mp4</title></head><body>"
               "<div id='vplayer'><video class='jw-video' src='%s'></video></div>"
               "<script>%s</script></body></html>" % (AD_CLIP, _pack(SETUP)))


class _Frame:
    def __init__(self, url, html, parent=None):
        self.url, self._html, self.parent_frame = url, html, parent

    def content(self):
        return self._html

    def evaluate(self, js):
        assert "navigator.userAgent" in js
        return UA


class _Page:
    def __init__(self, frames):
        self.frames = frames
        self.url = "https://pornhoarder.example/pornvideo/sellyourgf-jenny-manson/x"


class _Loc:
    def __init__(self, tag):
        self._tag = tag

    def evaluate(self, js):
        return self._tag


def _best(tag="VIDEO", frame_url=HOSTER):
    best = {"locator": _Loc(tag), "_via_learned": True, "_learned_sel": "video[src]"}
    if frame_url:
        best["_frame_url"] = frame_url
    return best


def _page(html=HOSTER_HTML):
    top = _Frame("https://pornhoarder.example/", "<html></html>")
    player = _Frame(PLAYER_PHP, "<html></html>", parent=top)
    return _Page([top, player, _Frame(HOSTER, html, parent=player)])


def _fresh_get(calls):
    def get(url, headers):
        calls.append((url, dict(headers)))
        return 200, "<html><script>%s</script></html>" % _pack(SETUP.replace(MASTER, FRESH))
    return get


def _down_get(url, headers):
    raise OSError("hoster unreachable")


def _override(page, best, direct, http_get=_down_get):
    return rt._frame_player_config_override(page, best, direct, user_agent=APP_UA,
                                            http_get=http_get)


def test_non_2xx_refetch_falls_back_to_the_browser_config():
    got = _override(_page(), _best(), AD_CLIP, http_get=lambda u, h: (403, ""))
    assert got == (MASTER, UA), got


def test_packed_player_setup_is_unpacked_to_its_media_source():
    assert spa.packed_player_media_urls(HOSTER_HTML) == [MASTER]


def test_learned_frame_video_playing_an_ad_yields_to_an_app_fetched_config():
    calls = []
    got = _override(_page(), _best(), AD_CLIP, http_get=_fresh_get(calls))
    assert got == (FRESH, APP_UA), (
        "FX_PORNHOARDER_AD_CLIP_TRANSFERRED: the learned <video> kept its ad or "
        f"used the browser-issued token; got {got!r}")
    assert calls == [(HOSTER, {"User-Agent": APP_UA, "Referer": PLAYER_PHP})], calls


def test_unreachable_hoster_falls_back_to_the_browser_config_and_ua():
    got = _override(_page(), _best(), AD_CLIP)
    assert got == (MASTER, UA), (
        "FX_PORNHOARDER_AD_CLIP_TRANSFERRED: the learned <video> in the hoster "
        f"frame kept its preroll ad {AD_CLIP!r}; the frame's packed player config "
        f"names {MASTER!r}; got {got!r}")


def test_learned_link_is_never_second_guessed():
    assert _override(_page(), _best(tag="A"), AD_CLIP) is None


def test_main_document_element_keeps_the_learned_url():
    assert _override(_page(), _best(frame_url=""), AD_CLIP) is None


def test_frame_without_a_packed_config_keeps_the_learned_url():
    plain = HOSTER_HTML.split("<script>")[0] + "</body></html>"
    assert _override(_page(plain), _best(), AD_CLIP) is None


def test_element_already_playing_the_configured_source_is_unchanged():
    assert _override(_page(), _best(), MASTER) is None


def test_unpacker_ignores_pages_without_packed_scripts():
    assert spa.unpack_packed_scripts("<script>var x = 1;</script>") == []
    assert spa.packed_player_media_urls("") == []


AD_FRAME = "https://neonvanta.example/daourl.php"


def test_learned_video_in_an_ad_frame_yields_to_the_visible_hoster_config(monkeypatch):
    top = _Frame("https://pornhoarder.example/", "<html></html>")
    player = _Frame(PLAYER_PHP, "<html></html>", parent=top)
    hoster = _Frame(HOSTER, HOSTER_HTML, parent=player)
    ad = _Frame(AD_FRAME, "<html><video src='%s'></video></html>" % AD_CLIP, parent=top)
    page = _Page([top, player, ad, hoster])
    monkeypatch.setattr(spa, "_child_frames", lambda _page: [hoster])
    got = _override(page, _best(frame_url=AD_FRAME), AD_CLIP, http_get=_fresh_get([]))
    assert got == (FRESH, APP_UA), (
        "FX_PORNHOARDER_AD_CLIP_TRANSFERRED: the learned <video> in the AD frame "
        f"kept {AD_CLIP!r} although the visible hoster frame configures {MASTER!r}; got {got!r}")


def test_ad_frame_video_with_no_configured_player_on_the_page_is_unchanged(monkeypatch):
    ad = _Frame(AD_FRAME, "<html><video src='%s'></video></html>" % AD_CLIP)
    page = _Page([_Frame("https://pornhoarder.example/", "<html></html>"), ad])
    monkeypatch.setattr(spa, "_child_frames", lambda _page: [])
    assert _override(page, _best(frame_url=AD_FRAME), AD_CLIP) is None
