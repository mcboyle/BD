"""player_families.py — Wave 169 family pack A. Brand recognizers that plug into
the Wave-168 registry scaffold. Builder-side, pure/stdlib. Each recognizer
scores DOM class/id/custom-element + script-src signals and contributes
family-specific selector SHAPES. Signatures are CANDIDATES — verify against the
capture corpus; vendors rename classes across versions.

extraction_core.py untouched. Nothing here is enabled; selectors are review-only.
"""
from __future__ import annotations

import re

import player_recognition as pr


def _has(p, html):
    return re.search(p, html or "", re.I | re.S) is not None


def _script(srcs, *needles):
    blob = " ".join(str(s).lower() for s in (srcs or []))
    return any(n in blob for n in needles)


# ── individual family detectors (return a score 0..1) ───────────────────────
def _videojs(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bvideo-js\b', html) or _has(r'\bvjs-', html):
        s += 0.4
    if _has(r'\bvjs-big-play-button\b|\bvjs-control-bar\b', html):
        s += 0.3
    if _script(scripts, "video.js", "videojs", "video-js"):
        s += 0.4
    if _has(r'data-vjs-player', html):
        s += 0.2
    return min(s, 1.0)


def _theoplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'theoplayer-skin|\btheo-|\boptiview\b|theolive', html):
        s += 0.4
    if _has(r'Set video quality to\s*\d', html) or _has(r'video quality settings menu', html):
        s += 0.5
    if _script(scripts, "theoplayer", "optiview"):
        s += 0.4
    return min(s, 1.0)


def _jwplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bjwplayer\b|\bjw-reset\b|\bjw-icon', html):
        s += 0.4
    if _has(r'id=["\']jwplayer', html):
        s += 0.3
    if _script(scripts, "jwplayer"):
        s += 0.4
    return min(s, 1.0)


def _shaka(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bshaka-', html):
        s += 0.5
    if _script(scripts, "shaka-player", "shaka.player", "shaka"):
        s += 0.4
    return min(s, 1.0)


def _hlsjs(html, scripts, iframes, net):
    s = 0.0
    if _script(scripts, "hls.js", "hls.min.js", "/hls."):
        s += 0.6
    if _has(r'<video', html):
        s += 0.1
    return min(s, 1.0)


def _dashjs(html, scripts, iframes, net):
    s = 0.0
    if _script(scripts, "dash.all", "dash.js", "dashjs", "dash.min"):
        s += 0.6
    if _has(r'<video', html):
        s += 0.1
    return min(s, 1.0)


def _plyr(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bplyr\b|\bplyr__', html):
        s += 0.4
    if _has(r'\bplyr__controls\b|\bplyr__menu\b', html):
        s += 0.3
    if _has(r'data-plyr', html):
        s += 0.2
    if _script(scripts, "plyr"):
        s += 0.2
    return min(s, 1.0)


def _flowplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bflowplayer\b|\bfp-controls\b|\bfp-menu\b', html):
        s += 0.5
    if _script(scripts, "flowplayer"):
        s += 0.4
    return min(s, 1.0)


def _clappr(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bclappr', html) or _has(r'\bmedia-control\b', html):
        s += 0.4
    if _script(scripts, "clappr"):
        s += 0.4
    if _has(r'\bdata-player\b', html):
        s += 0.1
    return min(s, 1.0)


def _mediaelement(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bmejs__|\bmejs-', html):
        s += 0.5
    if _script(scripts, "mediaelement"):
        s += 0.4
    return min(s, 1.0)


def _wordpress_mejs(html, scripts, iframes, net):
    # WordPress core ships MediaElement.js; wp-video alongside mejs is the tell.
    if _has(r'\bwp-video\b', html) and _has(r'\bmejs__|\bmejs-', html):
        return 0.7
    if _has(r'\bwp-video\b', html):
        return 0.35
    return 0.0


# ── selector contributions (brand-specific shapes; review-only) ─────────────
def _sel_videojs(html):
    s = {"player": {"container": ".video-js"}}
    if _has(r'vjs-big-play-button', html):
        s["player"]["play_button"] = "button.vjs-big-play-button"
    s["settings"] = ".vjs-menu-button"
    return s


def _sel_theoplayer(html):
    s = {"player": {"container": ".theoplayer-skin"}, "quality": {}}
    if _has(r'video quality settings menu', html):
        s["quality"]["open_menu"] = '[aria-label="Open the video quality settings menu"]'
    if _has(r'Set video quality to', html):
        s["quality"]["resolution_option"] = '[aria-label="Set video quality to {resolution}"]'
    return s


def _sel_jwplayer(html):
    s = {"player": {"container": ".jwplayer"}, "settings": ".jw-icon-settings"}
    if _has(r'jw-icon-display|jw-icon-playback|jw-display-icon-container', html):
        s["player"]["play_button"] = ".jw-icon-display, .jw-icon-playback"
    return s


def _sel_shaka(html):
    return {"player": {"container": ".shaka-video-container"},
            "settings": ".shaka-overflow-menu-button"}


def _sel_plyr(html):
    return {"player": {"container": ".plyr"}, "settings": ".plyr__menu"}


def _sel_flowplayer(html):
    return {"player": {"container": ".flowplayer"}, "settings": ".fp-menu"}


def _sel_clappr(html):
    return {"player": {"container": "[data-player]"}}


def _sel_mediaelement(html):
    return {"player": {"container": ".mejs__container"}}


def _sel_wordpress_mejs(html):
    return {"player": {"container": ".wp-video, .mejs__container"}}


# ── pack B: inline players ──────────────────────────────────────────────────
def _bitmovin(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bbmpui-|\bbitmovinplayer-', html): s += 0.5
    if _script(scripts, "bitmovinplayer", "bitmovin"): s += 0.4
    return min(s, 1.0)

def _brightcove(html, scripts, iframes, net):
    s = 0.0
    if _has(r'data-account=', html) and _has(r'\bvideo-js\b|\bvjs-|<video-js', html): s += 0.5
    if _has(r'data-video-id=', html): s += 0.2
    if _script(scripts, "players.brightcove.net", "brightcove"): s += 0.5
    return min(s, 1.0)

def _kaltura(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bplaykit-|kaltura_player|\bmwEmbed', html): s += 0.4
    if _has(r'id=["\']kaltura_player', html): s += 0.2
    if _script(scripts, "mwembed", "kwidget", "playkit", "kaltura"): s += 0.4
    return min(s, 1.0)

def _mux(html, scripts, iframes, net):
    s = 0.0
    if _has(r'<mux-player|<mux-video', html): s += 0.7
    if _script(scripts, "mux-player", "@mux/"): s += 0.3
    return min(s, 1.0)

def _media_chrome(html, scripts, iframes, net):
    s = 0.0
    if _has(r'<media-controller|<media-control-bar|<media-play-button', html): s += 0.5
    if _script(scripts, "media-chrome"): s += 0.3
    return min(s, 1.0)

def _dplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bdplayer\b|\bdplayer-', html): s += 0.5
    if _script(scripts, "dplayer"): s += 0.3
    return min(s, 1.0)

def _artplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bartplayer-app\b|\bart-video-player\b|\bart-control', html): s += 0.5
    if _script(scripts, "artplayer"): s += 0.3
    return min(s, 1.0)

def _xgplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bxgplayer\b|\bxgplayer-', html): s += 0.5
    if _script(scripts, "xgplayer"): s += 0.3
    return min(s, 1.0)

def _ovenplayer(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bovenplayer\b', html): s += 0.5
    if _script(scripts, "ovenplayer"): s += 0.4
    return min(s, 1.0)

def _openplayerjs(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bop-player\b|\bopenplayer\b|\bop-controls', html): s += 0.4
    if _script(scripts, "openplayer"): s += 0.4
    return min(s, 1.0)

def _fluid_player(html, scripts, iframes, net):
    s = 0.0
    if _has(r'fluid_video_wrapper|\bfluid_control', html): s += 0.5
    if _script(scripts, "fluidplayer", "fluid-player"): s += 0.4
    return min(s, 1.0)

def _flvjs_mpegts(html, scripts, iframes, net):
    s = 0.0
    if _script(scripts, "flv.min.js", "flv.js", "mpegts.js", "mpegts.min.js", "/flv."): s += 0.6
    if _has(r'<video', html): s += 0.1
    return min(s, 1.0)


# ── pack C: third-party embeds (recognize; never introspect) ────────────────
def _embed_host(html, iframes, *needles):
    blob = ((html or "") + " " + " ".join(str(h) for h in (iframes or []))).lower()
    return any(n in blob for n in needles)

def _vimeo(html, scripts, iframes, net):
    return 0.7 if _embed_host(html, iframes, "player.vimeo.com", "vimeo.com/showcase", "vimeo.com/video") else 0.0

def _youtube(html, scripts, iframes, net):
    return 0.7 if _embed_host(html, iframes, "youtube.com/embed", "youtube-nocookie.com/embed", "youtu.be/") else 0.0

def _twitch(html, scripts, iframes, net):
    return 0.7 if _embed_host(html, iframes, "player.twitch.tv", "clips.twitch.tv") else 0.0

def _dailymotion(html, scripts, iframes, net):
    return 0.7 if _embed_host(html, iframes, "dailymotion.com/embed", "geo.dailymotion.com") else 0.0

def _facebook_video(html, scripts, iframes, net):
    return 0.7 if _embed_host(html, iframes, "facebook.com/plugins/video") else 0.0

def _cloudflare_stream(html, scripts, iframes, net):
    s = 0.0
    if _has(r'<stream\b', html): s += 0.5
    if _embed_host(html, iframes, "iframe.cloudflarestream.com", "videodelivery.net", "cloudflarestream.com"): s += 0.5
    return min(s, 1.0)

def _wistia(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bwistia_embed\b|\bwistia_async_', html): s += 0.5
    if _embed_host(html, iframes, "fast.wistia.com", "fast.wistia.net", "wistia.com") or _script(scripts, "wistia"): s += 0.4
    return min(s, 1.0)

def _react_player(html, scripts, iframes, net):
    return 0.4 if _has(r'\breact-player\b', html) else 0.0


def _video_react(html, scripts, iframes, net):
    s = 0.0
    if _has(r'\bvideo-react\b|\bvideo-react-', html): s += 0.5
    if _script(scripts, "video-react"): s += 0.3
    return min(s, 1.0)

def _webrtc_live(html, scripts, iframes, net):
    s = 0.0
    if _script(scripts, "webrtc-adapter", "peerjs", "simple-peer"): s += 0.5
    if _has(r'RTCPeerConnection|getUserMedia|\bsrcObject\b', html): s += 0.3
    return min(s, 1.0)

def _webtorrent(html, scripts, iframes, net):
    s = 0.0
    if _script(scripts, "webtorrent"): s += 0.5
    if _has(r'magnet:\?xt=urn:btih:|\bWebTorrent\b', html): s += 0.3
    return min(s, 1.0)



# ── Pack D: modern/open web players ─────────────────────────────────────────
def _vidstack(h,sc,i,n):
    x=0.0
    if _has(r'<media-player\b|\bvidstack\b|data-media-player', h): x+=0.6
    if _script(sc,"vidstack"): x+=0.3
    return min(x,1.0)
def _radiant_media_player(h,sc,i,n):
    x=0.0
    if _has(r'\brmp-|radiantmediaplayer|radiant-media', h): x+=0.5
    if _script(sc,"radiantmediaplayer","rmp"): x+=0.3
    return min(x,1.0)
def _cloudinary_video_player(h,sc,i,n):
    x=0.0
    if _has(r'\bcld-video-player\b|cloudinary-video-player|data-cld', h): x+=0.6
    if _script(sc,"cloudinary-video-player","cloudinary-core"): x+=0.3
    return min(x,1.0)
def _playerjs(h,sc,i,n):
    x=0.0
    if _has(r'\bpjsdiv\b|class=["\'][^"\']*playerjs', h): x+=0.5
    if _script(sc,"playerjs"): x+=0.3
    return min(x,1.0)
def _jplayer(h,sc,i,n):
    x=0.0
    if _has(r'\bjp-jplayer\b|\bjp-video\b|\bjPlayer\b', h): x+=0.5
    if _script(sc,"jplayer"): x+=0.3
    return min(x,1.0)
def _able_player(h,sc,i,n):
    x=0.0
    if _has(r'\bable-player\b|\bableplayer\b|data-able-player', h): x+=0.5
    if _script(sc,"ableplayer"): x+=0.3
    return min(x,1.0)
def _paella_player(h,sc,i,n):
    x=0.0
    if _has(r'\bpaella\b|paellaplayer', h): x+=0.5
    if _script(sc,"paella"): x+=0.3
    return min(x,1.0)
def _fv_player(h,sc,i,n):
    x=0.0
    if _has(r'\bfv-player\b|\bfvplayer\b|\bfvfp\b', h): x+=0.6
    if _script(sc,"fvplayer","fv-player"): x+=0.3
    return min(x,1.0)
def _presto_player(h,sc,i,n):
    x=0.0
    if _has(r'\bpresto-player\b|prestoplayer', h): x+=0.6
    if _script(sc,"presto-player","prestoplayer"): x+=0.3
    return min(x,1.0)
def _brid_tv(h,sc,i,n):
    # Inline Brid.TV player only: an on-page container/class or the brid SDK.
    # NOT the bare brid.tv domain — that appears in a HOSTED iframe src and is
    # handled by _brid_tv_hosted (third_party). v3.66.170 edge fix.
    x=0.0
    if _has(r'\bbrid_?tv\b', h): x+=0.4
    if _script(sc,"brid"): x+=0.3
    return min(x,1.0)
def _brid_tv_hosted(h,sc,i,n):
    # Brid.TV embedded as a hosted iframe (player.brid.tv / services.brid.tv):
    # internals are cross-origin and not in the capture — third_party/review.
    return 0.7 if _embed_host(h, i, "player.brid.tv", "services.brid.tv", "brid.tv/player") else 0.0

# ── Pack E: commercial/enterprise SDKs (+ aliases folded into canonicals) ────
def _akamai_amp(h,sc,i,n):
    x=0.0
    if _has(r'\bamp-flush\b|azuremediaplayer|\bampplayer\b|\bamp-default-skin\b', h): x+=0.5
    if _script(sc,"azuremediaplayer","akamai-amp","amp.min"): x+=0.3
    return min(x,1.0)
def _castlabs_prestoplay(h,sc,i,n):
    x=0.0
    if _has(r'\bclpp-|prestoplay|castlabs', h): x+=0.5
    if _script(sc,"prestoplay","castlabs"): x+=0.4
    return min(x,1.0)
def _nexplayer_html5(h,sc,i,n):
    x=0.0
    if _has(r'\bnexplayer\b|\bnex-player\b|nexplayer-', h): x+=0.5
    if _script(sc,"nexplayer"): x+=0.4
    return min(x,1.0)
def _vdocipher(h,sc,i,n):
    x=0.0
    if _has(r'\bvdocipher\b|\bvdo-?player\b|data-vdo', h): x+=0.6
    if _script(sc,"vdocipher"): x+=0.4
    return min(x,1.0)

# ── Pack F: live/low-latency platforms (live=True, review_only, non-override) ─
def _red5_pro(h,sc,i,n):
    return 0.6 if (_has(r'red5pro|red5-pro', h) or _script(sc,"red5pro","red5-pro")) else 0.0
def _ant_media(h,sc,i,n):
    return 0.6 if (_has(r'ant-?media|antmedia', h) or _script(sc,"antmedia","ant-media")) else 0.0
def _millicast(h,sc,i,n):
    return 0.6 if (_has(r'\bmillicast\b', h) or _script(sc,"millicast")) else 0.0
def _wowza_player(h,sc,i,n):
    return 0.6 if (_has(r'\bwowza\b|wowzaplayer', h) or _script(sc,"wowza")) else 0.0
def _softvelum_sldp(h,sc,i,n):
    return 0.6 if (_has(r'\bsldp\b|softvelum', h) or _script(sc,"sldp","softvelum")) else 0.0
def _livekit(h,sc,i,n):
    return 0.6 if (_has(r'\blivekit\b', h) or _script(sc,"livekit")) else 0.0
def _twilio_video(h,sc,i,n):
    return 0.6 if (_has(r'twilio-video', h) or _script(sc,"twilio-video")) else 0.0
def _agora_rtc(h,sc,i,n):
    return 0.6 if (_has(r'\bagorartc\b|agora-rtc', h) or _script(sc,"agora-rtc","agorartc")) else 0.0
def _daily_webrtc(h,sc,i,n):
    return 0.6 if (_has(r'daily\.co|daily-js', h) or _script(sc,"daily-js")) else 0.0
def _janus_webrtc(h,sc,i,n):
    return 0.6 if (_has(r'\bjanus\b', h) or _script(sc,"janus")) else 0.0
def _jitsi_meet(h,sc,i,n):
    return 0.6 if (_has(r'jitsi|meet\.jit\.si', h) or _script(sc,"jitsi","lib-jitsi-meet")) else 0.0

# ── Pack G: hosted video platforms (third_party_review_only, no introspection) ─
def _dacast(h,sc,i,n):
    return 0.7 if _embed_host(h,i,"dacast.com","player.dacast.com") else 0.0
def _sproutvideo(h,sc,i,n):
    return 0.7 if _embed_host(h,i,"sproutvideo.com","vids.io") else 0.0
def _loom(h,sc,i,n):
    return 0.7 if _embed_host(h,i,"loom.com/embed","loom.com/share") else 0.0
def _bunny_stream(h,sc,i,n):
    return 0.7 if _embed_host(h,i,"iframe.mediadelivery.net","mediadelivery.net","b-cdn.net/embed") else 0.0
def _brightcove_gallery(h,sc,i,n):
    return 0.6 if (_embed_host(h,i,"players.brightcove.net/gallery") or _has(r'bc-gallery|brightcove-gallery', h)) else 0.0
def _jwplatform_hosted(h,sc,i,n):
    return 0.6 if _embed_host(h,i,"content.jwplatform.com","cdn.jwplayer.com/players","jwpcdn.com") else 0.0
def _mux_hosted(h,sc,i,n):
    if _has(r'<mux-player|<mux-video', h): return 0.0   # inline -> normalize to mux
    return 0.6 if _embed_host(h,i,"stream.mux.com") else 0.0


_BUILTINS = [
    pr.PlayerFamily("videojs", "Video.js", _videojs, _sel_videojs, delivery="hls"),
    pr.PlayerFamily("theoplayer", "THEOplayer", _theoplayer, _sel_theoplayer, delivery="hls"),
    pr.PlayerFamily("jwplayer", "JW Player", _jwplayer, _sel_jwplayer, delivery="hls"),
    pr.PlayerFamily("shaka", "Shaka Player", _shaka, _sel_shaka, delivery="dash"),
    pr.PlayerFamily("hlsjs", "hls.js", _hlsjs, lambda h: {}, delivery="hls"),
    pr.PlayerFamily("dashjs", "dash.js", _dashjs, lambda h: {}, delivery="dash"),
    pr.PlayerFamily("plyr", "Plyr", _plyr, _sel_plyr, delivery="progressive"),
    pr.PlayerFamily("flowplayer", "Flowplayer", _flowplayer, _sel_flowplayer, delivery="hls"),
    pr.PlayerFamily("clappr", "Clappr", _clappr, _sel_clappr, delivery="hls"),
    pr.PlayerFamily("mediaelement", "MediaElement.js", _mediaelement, _sel_mediaelement, delivery="progressive"),
    pr.PlayerFamily("wordpress_mejs", "WordPress (MediaElement.js)", _wordpress_mejs, _sel_wordpress_mejs, delivery="progressive"),
    # ── pack B (inline) ──
    pr.PlayerFamily("bitmovin", "Bitmovin", _bitmovin, lambda h: {"player": {"container": ".bitmovinplayer-container"}, "settings": ".bmpui-ui-settingstogglebutton"}, delivery="hls"),
    pr.PlayerFamily("brightcove", "Brightcove", _brightcove, lambda h: {"player": {"container": ".video-js"}, "settings": ".vjs-menu-button"}, delivery="hls"),
    pr.PlayerFamily("kaltura", "Kaltura", _kaltura, lambda h: {"player": {"container": ".playkit-player, #kaltura_player"}}, delivery="hls"),
    pr.PlayerFamily("mux", "Mux Player", _mux, lambda h: {"player": {"container": "mux-player"}}, delivery="hls"),
    pr.PlayerFamily("media_chrome", "Media Chrome", _media_chrome, lambda h: {"player": {"container": "media-controller", "play_button": "media-play-button"}}, delivery="hls"),
    pr.PlayerFamily("dplayer", "DPlayer", _dplayer, lambda h: {"player": {"container": ".dplayer"}, "settings": ".dplayer-setting"}, delivery="hls"),
    pr.PlayerFamily("artplayer", "ArtPlayer", _artplayer, lambda h: {"player": {"container": ".artplayer-app"}}, delivery="hls"),
    pr.PlayerFamily("xgplayer", "xgplayer", _xgplayer, lambda h: {"player": {"container": ".xgplayer"}, "quality": {"open_menu": ".xgplayer-definition"}}, delivery="hls"),
    pr.PlayerFamily("ovenplayer", "OvenPlayer", _ovenplayer, lambda h: {"player": {"container": ".ovenplayer"}}, delivery="hls"),
    pr.PlayerFamily("openplayerjs", "OpenPlayerJS", _openplayerjs, lambda h: {"player": {"container": ".op-player"}}, delivery="hls"),
    pr.PlayerFamily("fluid_player", "Fluid Player", _fluid_player, lambda h: {"player": {"container": '[id^="fluid_video_wrapper"]'}}, delivery="progressive"),
    pr.PlayerFamily("flvjs_mpegts", "flv.js / mpegts.js", _flvjs_mpegts, lambda h: {}, delivery="progressive"),
    # ── pack C (third-party embeds; recognize, never introspect) ──
    pr.PlayerFamily("vimeo", "Vimeo (embed)", _vimeo, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("youtube", "YouTube (embed)", _youtube, lambda h: {}, delivery="mse", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("twitch", "Twitch (embed)", _twitch, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("dailymotion", "Dailymotion (embed)", _dailymotion, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("facebook_video", "Facebook Video (embed)", _facebook_video, lambda h: {}, delivery="dash", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("cloudflare_stream", "Cloudflare Stream", _cloudflare_stream, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("wistia", "Wistia", _wistia, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("react_player", "ReactPlayer (wrapper)", _react_player, lambda h: {"player": {"container": ".react-player"}}, delivery="unknown"),
    # ── finish-set: extra inline + edge transports ──
    pr.PlayerFamily("video_react", "video-react", _video_react, lambda h: {"player": {"container": ".video-react"}}, delivery="progressive"),
    pr.PlayerFamily("webrtc_live", "WebRTC (live)", _webrtc_live, lambda h: {}, delivery="webrtc", policy="review_only"),
    pr.PlayerFamily("webtorrent", "WebTorrent (p2p)", _webtorrent, lambda h: {}, delivery="mse_blob", policy="review_only"),
    # ── Pack D ──
    pr.PlayerFamily("vidstack", "Vidstack", _vidstack, lambda h: {"player": {"container": "media-player"}}, delivery="hls"),
    pr.PlayerFamily("radiant_media_player", "Radiant Media Player", _radiant_media_player, lambda h: {"player": {"container": ".rmp-container"}}, delivery="hls"),
    pr.PlayerFamily("cloudinary_video_player", "Cloudinary Video Player", _cloudinary_video_player, lambda h: {"player": {"container": ".cld-video-player"}}, delivery="hls"),
    pr.PlayerFamily("playerjs", "Player.js (Tjoeb)", _playerjs, lambda h: {"player": {"container": "[id^=player]"}}, delivery="hls"),
    pr.PlayerFamily("jplayer", "jPlayer", _jplayer, lambda h: {"player": {"container": ".jp-jplayer"}}, delivery="progressive"),
    pr.PlayerFamily("able_player", "AblePlayer", _able_player, lambda h: {"player": {"container": ".able"}}, delivery="progressive"),
    pr.PlayerFamily("paella_player", "Paella Player", _paella_player, lambda h: {"player": {"container": "#paellaPlayer"}}, delivery="hls"),
    pr.PlayerFamily("fv_player", "FV Player", _fv_player, lambda h: {"player": {"container": ".fvplayer, .fv-player"}}, delivery="hls"),
    pr.PlayerFamily("presto_player", "Presto Player", _presto_player, lambda h: {"player": {"container": ".presto-player"}}, delivery="hls"),
    pr.PlayerFamily("brid_tv", "Brid.TV", _brid_tv, lambda h: {"player": {"container": "[class*=brid]"}}, delivery="hls"),
    pr.PlayerFamily("brid_tv_hosted", "Brid.TV (hosted)", _brid_tv_hosted, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    # ── Pack E ──
    pr.PlayerFamily("akamai_amp", "Akamai/Azure Media Player", _akamai_amp, lambda h: {"player": {"container": ".amp-default-skin"}}, delivery="hls"),
    pr.PlayerFamily("castlabs_prestoplay", "castLabs PRESTOplay", _castlabs_prestoplay, lambda h: {"player": {"container": "[class*=clpp]"}}, delivery="dash"),
    pr.PlayerFamily("nexplayer_html5", "NexPlayer", _nexplayer_html5, lambda h: {"player": {"container": ".nexplayer"}}, delivery="hls"),
    pr.PlayerFamily("vdocipher", "VdoCipher (protected)", _vdocipher, lambda h: {}, delivery="hls", policy="review_only"),
    # ── Pack F: live/RTC (non-override) ──
    pr.PlayerFamily("red5_pro", "Red5 Pro", _red5_pro, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("ant_media", "Ant Media", _ant_media, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("millicast", "Millicast", _millicast, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("wowza_player", "Wowza", _wowza_player, lambda h: {}, delivery="hls", policy="review_only", live=True),
    pr.PlayerFamily("softvelum_sldp", "Softvelum SLDP", _softvelum_sldp, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("livekit", "LiveKit", _livekit, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("twilio_video", "Twilio Video", _twilio_video, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("agora_rtc", "Agora RTC", _agora_rtc, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("daily_webrtc", "Daily", _daily_webrtc, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("janus_webrtc", "Janus WebRTC", _janus_webrtc, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    pr.PlayerFamily("jitsi_meet", "Jitsi Meet", _jitsi_meet, lambda h: {}, delivery="webrtc", policy="review_only", live=True),
    # ── Pack G: hosted (third_party_review_only) ──
    pr.PlayerFamily("dacast", "Dacast (hosted)", _dacast, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("sproutvideo", "SproutVideo (hosted)", _sproutvideo, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("loom", "Loom (hosted)", _loom, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("bunny_stream", "Bunny Stream (hosted)", _bunny_stream, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("brightcove_gallery", "Brightcove Gallery (hosted)", _brightcove_gallery, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("jwplatform_hosted", "JW Platform (hosted)", _jwplatform_hosted, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
    pr.PlayerFamily("mux_hosted", "Mux (hosted stream)", _mux_hosted, lambda h: {}, delivery="hls", embed="iframe", policy="third_party_review_only"),
]

_registered = False


def ensure_registered() -> None:
    """Idempotently register pack A into the recognizer's FAMILIES list."""
    global _registered
    if _registered:
        return
    have = {f.id for f in pr.FAMILIES}
    for fam in _BUILTINS:
        if fam.id not in have:
            pr.register_family(fam)
    _registered = True
