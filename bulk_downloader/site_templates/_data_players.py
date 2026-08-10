"""site_templates._data_players -- verbatim TEMPLATES slice [0:24] (24 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "video_js",
        "name": "Video.js player",
        "description": "Sites embedding the video.js HTML5 player. Common on news sites and CMS-published video.",
        "patterns": [],   # signature: <video class="video-js">
        "learned": {
            "download": {
                "row_selectors": [
                    "video.video-js source",
                    "video[class*='video-js'] source",
                ],
                "url_attribute": "src",
                "trigger_selectors": [
                    ".vjs-download-button",
                    "a[class*='vjs-download']",
                ],
            },
        },
    },
{
        "id": "jw_player",
        "name": "JW Player",
        "description": "Sites using the JW Player. Picks up the highest-quality stream from the player's source map.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".jw-overlays a[href$='.mp4']",
                    ".jwplayer source[label]",
                    "[data-jw-quality]",
                ],
                "url_attribute": "src",
                "trigger_selectors": [
                    ".jw-settings-content-item[aria-label*='Quality']",
                    ".jw-icon-quality",
                ],
            },
        },
    },
{
        "id": "html5_native",
        "name": "Plain HTML5 <video>",
        "description": "Direct <video> tags with <source> elements. Works on most basic embeds.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video > source[type='video/mp4']",
                    "video > source[src$='.mp4']",
                    "video[src$='.mp4']",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "resolution_anchors",
        "name": "Resolution anchor links",
        "description": "Sites that expose direct download links labeled by resolution (e.g. '4K 3840 x 2160', '1080p'). Common on adult sites and stock-video portals.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a:has-text('7680 x 4320')",
                    "a:has-text('5760 x 3240')",
                    "a:has-text('3840 x 2160')",
                    "a:has-text('1920 x 1080')",
                    "a[href*='.mp4']:has-text('Download')",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a.download-button",
                ],
            },
        },
    },
{
        "id": "data_href_attribute",
        "name": "data-href attribute pattern",
        "description": "Sites that stash the real download URL on a data-href, data-url, or data-src attribute rather than href (defeats naive scrapers).",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "[data-href*='.mp4']",
                    "[data-url*='.mp4']",
                    "[data-src*='.mp4']",
                ],
                "url_attribute": "data-href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "wowgirls_network",
        "name": "WowGirls / VIP4K-network (login + 4 download variants)",
        "description": "Sites in the WowGirls / VIP4K operator family. Covers all four HTML variants seen across their brands: <a class='ct_dl_button' href>, <div class='download-button' data-href>, <a class='download__item' data-download>, and the floating #exDownloadMenu quality menu. Uses v3.42.4 per-selector url_attribute so one site config handles every variant. v3.43.54: quality_preference defaults to 4K-first because the network's 5K (5568×3132) and 8K (7680×4320) CDN tiers are flaky — workers prefer the reliable 4K. To use higher tiers, edit the site's quality_preference to start with 4320 or 3132.",
        "patterns": [
            r"wowgirls\.com",
            r"vip4k\.com",
            r"black4k\.com",
            # v3.43.57: extended to cover the rest of the VIP4K-network
            # brands. All share the same backend + HTML markup. If a
            # specific brand site is missing from this list and you
            # add it manually, it'll still match the row_selectors
            # below — the patterns are only used for "suggested
            # template" highlighting in the wizard.
            r"tiny4k\.com",
            r"4kteens\.com",
            r"tushy4k\.com",
            r"teens4k\.com",
            r"hot4k\.com",
        ],
        "learned": {
            # v3.43.56: login selectors for the VIP4K-family login
            # form. All sites in this network share the same form
            # markup: <input id="login-username" name="_username">
            # / <input id="login-password" name="_password">
            # wrapped in a div.login__fields. The submit button is
            # in a form.login wrapper that varies by site (sometimes
            # button[type='submit'], sometimes input[type='submit'])
            # — we list both. Templates' login.* selectors are
            # consumed by do_login() as candidates AFTER user-
            # configured selectors but BEFORE the 154-entry fallback
            # list (see login.py:learned_block lookup).
            "login": {
                "user_field": [
                    "#login-username",
                    "input[name='_username']",
                    "input.input__area[name='_username']",
                ],
                "pass_field": [
                    "#login-password",
                    "input[name='_password']",
                    "input.input__area[type='password']",
                ],
                "submit_btn": [
                    "form.login button[type='submit']",
                    "button.btn--primary[type='submit']",
                    "button[type='submit']:has-text('Log in')",
                    "button[type='submit']:has-text('Sign in')",
                    "input[type='submit']",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "span.player-actions__text:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "div.download-button[data-href]",
                    "a.ct_dl_button[href]",
                    "a.download__item[data-download]",
                    "#exDownloadMenu .exp-menu-item",
                ],
                # v3.42.4: parallel list — each row_selector gets its own
                # attribute. Empty slot = click-and-capture path (the
                # floating menu serves via a JS-resolved download event,
                # no static URL attribute to read).
                "url_attribute": ["data-href", "href", "data-download", ""],
                "tier_labels_seen": ["8K", "6K", "5K", "4K", "2160p", "1080p", "720p"],
            },
        },
        "config_defaults": {
            # v3.43.54: explicit pixel-height preference order. 4K
            # (2160) is the reliable top tier on this network — the
            # 5K (5568×3132) and 8K (7680×4320) variants get served
            # from a flaky CDN that times out / 502s for some
            # accounts. Workers stick with 4K → 1080p → 720p as the
            # fallback ladder. If a user genuinely wants to chase
            # 5K/8K they can append ',4320,3132,2880' to this
            # preference list via the site edit form.
            "quality_preference": "2160,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        # v3.43.56: Gamma Entertainment's "Kosmos" React-based player.
        # Powers a long list of Gamma's brands. The HTML signatures
        # that make this identifiable:
        #   - download URLs follow /movieaction/download/<id>/<quality>/mp4
        #   - download links use `a.VideoJSPlayer-DownloadOption-Link`
        #   - icons load from gammacdn.com via CSS mask-image
        #   - the username input is `<input id="username" class="formElement">`
        #   - there's an interstitial "Skip" button (`.SkipPageButton-ButtonLink`
        #     with text "No Thanks") between login and the members area —
        #     workers must auto-dismiss it
        "id": "gamma_kosmos",
        "name": "Gamma Entertainment / Kosmos React player",
        "description": "Adult Time, Devil's Film, Dfxtra, Devious Family, Girlsway, and other Gamma Entertainment brands. Recognizable by gammacdn.com CSS-mask icons and /movieaction/download/<id>/<quality>/mp4 download URLs. Includes auto-dismiss for the SkipPageButton interstitial that appears after login. Quality up to 4K. If your specific Gamma brand isn't in the pattern list, add it via the site edit form's url_patterns field.",
        "patterns": [
            r"dfxtra\.com",
            r"devilsfilm\.com",
            r"adulttime\.com",
            r"deviantefamily\.com",
            r"deviantfamily\.com",
            r"girlsway\.com",
            r"familyhookups\.com",
            r"transangels\.com",
            r"burningangel\.com",
            r"evilangel\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#username",
                    "input[name='username'][autocomplete='username']",
                    "input.formElement[name='username']",
                    "input[name='username'][placeholder*='Email' i]",
                ],
                "pass_field": [
                    "#password",
                    "input[name='password'][type='password']",
                    "input.formElement[type='password']",
                    "input[autocomplete='current-password']",
                ],
                "submit_btn": [
                    "button[type='submit'].Button",
                    "form button[type='submit']",
                    "button:has-text('Sign In')",
                    "button:has-text('Login')",
                    "button:has-text('Log In')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button[aria-label*='download' i]",
                    "button:has-text('Download')",
                    ".VideoJSPlayer-DownloadButton",
                    "[class*='DownloadButton']",
                ],
                "row_selectors": [
                    # The most specific selector — Gamma's React class
                    # name. Hashed suffix classes change per build but
                    # the semantic class stays stable.
                    "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']",
                    # Fallback if the React class is rehashed
                    "a[href*='/movieaction/download/']",
                    "a.Link.VideoJSPlayer-DownloadOption-Link",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
            # v3.43.56: Gamma sites show a "Skip this page" interstitial
            # between successful login and the actual members area.
            # Auto-dismiss with the SkipPageButton class match. Also
            # handles a few common "consent" / "age check" patterns
            # some Gamma brands wrap their content in.
            #
            # v3.66.1016 (item E): the two scopes are now declared apart.
            # The wall below sits between the login POST and the members
            # area and cannot recur once past it, so it fires ONCE in
            # do_login -- where, until this cut, nothing dismissed it and
            # the success_url check read the WALL's url and threw a good
            # login into manual takeover.
            "dismiss_selectors_login": (
                "a.SkipPageButton-ButtonLink, "
                "a:has-text('No Thanks. Continue'), "
                "a:has-text('Continue to Members Area')"
            ),
            # These two stay per-URL on purpose: a consent gate or a close
            # button can appear on ANY content page, so they are not
            # login-wall selectors and moving them would lose real coverage.
            "dismiss_selectors": (
                "button:has-text('I Agree'), "
                "button[aria-label*='close' i]"
            ),
        },
    },
{
        "id": "blob_url_modal",
        "name": "Quality modal → blob URL",
        "description": "Player opens a quality-picker modal, then serves the file from a transient blob URL. Worker clicks the modal opener; needs your help to teach the right quality.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".quality-menu a[href$='.mp4']",
                    "[role='menuitem'][data-quality]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "button[aria-label*='quality' i]",
                    "button[aria-label*='settings' i]",
                    ".vjs-quality-selector",
                ],
            },
        },
    },
{
        "id": "vimeo_progressive",
        "name": "Vimeo Pro progressive download",
        "description": "Vimeo-hosted videos when the publisher has enabled progressive download. Worker reads the link from the share-or-download menu.",
        "patterns": ["vimeo.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href*='vimeocdn.com'][href*='.mp4']",
                    "a[download]:has-text('Download')",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "hlsjs_player",
        "name": "Hls.js player",
        "description": "Hls.js library for HLS streaming. Stream-only; the worker captures fragments and reassembles via ffmpeg when present.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[data-hls-url]",
                    "video[src*='.m3u8']",
                ],
                "url_attribute": "data-hls-url",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "shaka_player",
        "name": "Shaka Player",
        "description": "Google's Shaka Player for DASH/HLS. Like hls.js, stream-only — fragments captured at runtime.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[data-shaka-player]",
                    ".shaka-video-container video",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "mediaelement_player",
        "name": "MediaElement.js player",
        "description": "WordPress's default video player. Multi-source MP4/WebM/Ogg with quality picker.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".mejs__container source",
                    "video.mejs source",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "jwplatform",
        "name": "JW Platform CDN",
        "description": "Videos hosted on jwplatform.com / jwpltx.com / jwpcdn.com CDN. URL pattern is well-known.",
        "patterns": ["jwplatform.com", "jwpcdn.com", "jwpltx.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href*='jwplatform.com'][href*='.mp4']",
                    "a[href*='jwpcdn.com'][href*='.mp4']",
                    "video[data-jw-media-id] source",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "brightcove",
        "name": "Brightcove player",
        "description": "Brightcove video cloud (used by lots of corporate/news sites). Speculative — Brightcove typically blocks downloads via DRM, worker may need stream capture.",
        "patterns": ["brightcove.net", "brightcove.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[data-account] source[type='video/mp4']",
                    ".video-js[data-video-id] source",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "kaltura",
        "name": "Kaltura player",
        "description": "Kaltura video platform, common on educational and enterprise sites. Speculative — most Kaltura deployments require API token for direct download.",
        "patterns": ["kaltura.com", "kaltura.org"],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[data-source-type='video/mp4'] source",
                    ".kalturaPlayerContainer source[src*='.mp4']",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "wistia",
        "name": "Wistia embed",
        "description": "Wistia video host (marketing-focused). Downloads only work when publisher has enabled progressive download.",
        "patterns": ["wistia.com", "wistia.net"],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[data-wistia] source[type='video/mp4']",
                    "a[href*='wistia.com'][href*='.bin']",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "bitmovin",
        "name": "Bitmovin player",
        "description": "Bitmovin streaming player. Adaptive streaming; progressive download rare. Speculative.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".bitmovinplayer-container video source",
                    "video[data-bmpui]",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "theoplayer",
        "name": "THEOplayer",
        "description": "THEOplayer (used by broadcasters). Adaptive streaming; progressive download rare. Speculative.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".theoplayer-container video source",
                    "video.theoplayer source",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "flowplayer",
        "name": "Flowplayer",
        "description": "Flowplayer (newer versions). Multi-quality source list, usually MP4.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".fp-player source[type='video/mp4']",
                    ".flowplayer source[label]",
                ],
                "url_attribute": "src",
                "trigger_selectors": [
                    ".fp-qsel-menu .fp-qsel-entry",
                ],
            },
        },
    },
{
        "id": "plyr_io",
        "name": "Plyr.io player",
        "description": "Open-source Plyr HTML5 player, widely used by indie creators and Patreon-style sites.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".plyr source[type='video/mp4']",
                    ".plyr a[data-plyr='download']",
                    "video.plyr source",
                ],
                "url_attribute": "src",
                "trigger_selectors": [
                    ".plyr__menu__container a:has-text('Download')",
                ],
            },
        },
    },
{
        "id": "fluid_player",
        "name": "Fluid Player",
        "description": "Fluid Player is common on tube sites and small video hosts. Quality menu sits behind a gear icon.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".fluid_video_wrapper video source",
                    ".fluid_button_video_source_option[data-hd]",
                ],
                "url_attribute": "src",
                "trigger_selectors": [
                    ".fluid_button_video_source",
                    ".fluid_control_video_source",
                ],
            },
        },
    },
{
        "id": "videojs_with_quality",
        "name": "video.js with quality plugin",
        "description": "Video.js with the videojs-hls-quality-selector or contrib-quality-levels plugin. Quality menu lives behind the gear/cog icon in the player chrome.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video.video-js source[label]",
                    ".vjs-menu-item[role='menuitemradio']",
                ],
                "url_attribute": "src",
                "trigger_selectors": [
                    ".vjs-quality-selector",
                    ".vjs-icon-cog",
                ],
            },
        },
    },
{
        "id": "html5_video_multi_source",
        "name": "HTML5 <video> with multiple <source> tags",
        "description": "Plain HTML5 video element with multiple sources tagged by resolution. Works on many basic embeds and self-hosted videos. High confidence.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video source[label*='2160']",
                    "video source[label*='1080']",
                    "video source[label*='720']",
                    "video > source[type='video/mp4']",
                    "video[src$='.mp4']",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720,480",
        },
    },
{
        "id": "kvs_engine",
        "name": "KVS (Kernel Video Sharing) engine",
        "description": "Many adult tube sites run on KVS (kernel-video-sharing.com). Signatures: kt_player, video_id on body, /get_file/ endpoints. High confidence — used by dozens of major tubes.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href*='/get_file/']",
                    ".tab_video_info a[href*='.mp4']",
                    ".download-block a[href*='.mp4']",
                    "a.download_link[href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720,480",
            "min_resolution": 720,
        },
    },
]
