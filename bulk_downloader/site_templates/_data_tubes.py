"""site_templates._data_tubes -- verbatim TEMPLATES slice [62:83] (21 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "erome",
        "name": "Erome",
        "description": "Erome (erome.com) — UGC adult albums. Direct MP4 (no HLS). VERIFIED: built from the serpapps/erome-downloader 2,033-line research doc; selectors and CDN patterns are documented, not guesswork. Public content typically does not require login. Albums can contain multiple videos under one URL.",
        "patterns": [
            r"erome\.com",
            r"v\d+\.erome\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "video source", "video[data-src]",
                ],
                "row_selectors": [
                    "video source[src*='.mp4']",
                    "video source[type='video/mp4']",
                    "video[data-src*='.mp4']",
                    ".media-group video source[src]",
                    "div.video-lg video source[src]",
                ],
                "url_attribute": ["src", "src", "data-src", "src", "src"],
                "tier_labels_seen": ["1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 360,
            "use_curl_cffi": False,
            "use_persistent_profile": False,
        },
    },
{
        "id": "alpha_porno",
        "name": "Alpha Porno",
        "description": "Alpha Porno (alphaporno.com) — tube site with HLS + MP4 dual delivery. VERIFIED: built from the serpapps/alpha-porno-downloader 1,862-line research doc. Has JSON-LD metadata, flashvars JS config, dedicated /embed/ endpoints, and API endpoints (/api/video/{id}, /api/videofile.php). Video ID is 8 digits; URL pattern is /videos/{title-slug}-{id}/.",
        "patterns": [
            r"alphaporno\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a[href*='/download/']",
                ],
                "row_selectors": [
                    # JSON-LD VideoObject contentUrl is the canonical
                    # source per the research doc.
                    "script[type='application/ld+json']",
                    "video source[src*='.mp4']",
                    "video[data-src*='.mp4']",
                    "a[href*='.mp4']",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                    "a:has-text('480p')",
                ],
                "url_attribute": ["data-jsonld", "src", "data-src", "href", "href", "href", "href"],
                "tier_labels_seen": ["1080p", "720p", "480p", "360p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "chaturbate",
        "name": "Chaturbate (live recording)",
        "description": "Chaturbate (chaturbate.com) — LIVE CAM site. VERIFIED architecture (HLS streaming over WSS push). v3.43.62: live recording runtime available via streamlink. URLs route here; the worker pipeline detects live URLs and arms a recording watch instead of attempting a file download. Requires streamlink (preferred) or ffmpeg on PATH; if neither is installed the wizard surfaces a 'not configured' hint.",
        "patterns": [
            r"chaturbate\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [],
                "row_selectors": [],
                "url_attribute": "href",
                "tier_labels_seen": [],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720",
            "min_resolution": 480,
            "use_curl_cffi": True,
            "use_live_recorder": True,
        },
    },
{
        # SpankBang — free tube, HLS + progressive MP4 per their README.
        "id": "spankbang",
        "name": "SpankBang",
        "description": "SpankBang (spankbang.com) — free tube, HLS + progressive MP4. Speculative selectors from the spankbang-downloader README architecture hints. Send watch-page HTML to refine.",
        "patterns": [
            r"spankbang\.com", r"spankbang\.party",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')", "a.btn-download",
                    "a[href*='/download/']",
                ],
                "row_selectors": [
                    "a[href*='/download/'][href*='.mp4']",
                    "a[data-quality][href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('4K')", "a:has-text('2160p')",
                    "a:has-text('1080p')", "a:has-text('720p')",
                    "a:has-text('480p')", "a:has-text('360p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p", "480p", "360p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        # Aylo (formerly MindGeek) free-tube family. Distinct from
        # premium MindGeek brands in mindgeek_family. All four share the
        # flashvars_{id} JS variable carrying `mediaDefinitions` with
        # HLS + MP4 progressive URLs.
        "id": "aylo_free_tubes",
        "name": "Aylo free tubes (RedTube / PornHub / YouPorn / Tube8)",
        "description": "Aylo's free-tube family — share the flashvars + mediaDefinitions player config (HLS + MP4 progressive). Distinct from mindgeek_family which covers paid premium brands. Speculative — JS-variable extraction likely needs a teach pass or AI-assisted selector capture.",
        "patterns": [
            r"redtube\.com", r"pornhub\.com", r"pornhubpremium\.com",
            r"youporn\.com", r"tube8\.com", r"xtube\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a.tab_download", "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a.downloadBtn[href]",
                    "a[href*='/download_video.php']",
                    "a[data-quality][href]",
                    "a[href*='.mp4?']",
                    "a:has-text('1080p')", "a:has-text('720p')",
                    "a:has-text('480p')", "a:has-text('240p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "1080p", "720p", "480p", "240p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        # WGCZ Holding: xvideos and xnxx share the same setVideoUrl*
        # JavaScript scaffolding.
        "id": "wgcz_tubes",
        "name": "WGCZ tubes (XVideos / XNXX)",
        "description": "WGCZ Holding's free-tube family — XVideos and XNXX share player infrastructure. Both use setVideoUrlHigh / setVideoUrlLow / setVideoHLS JS functions. Speculative — likely needs a teach pass to capture the JS-extracted .mp4 URLs.",
        "patterns": [
            r"xvideos\.com", r"xvideos\d+\.com", r"xvideos-cdn\.",
            r"xnxx\.com", r"xnxx\d+\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='.mp4']",
                    "a[href*='setVideoUrlHigh']",
                    "video source[src*='.mp4']",
                    "a:has-text('HD')", "a:has-text('High')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["HD", "1080p", "720p", "Low"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        "id": "beeg",
        "name": "Beeg",
        "description": "Beeg (beeg.com) — free tube, HLS + direct MP4 per the beeg-downloader README. Has /api/v6/{ts}/video/{id} JSON endpoint that returns source URLs. Speculative selectors.",
        "patterns": [
            r"beeg\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')", "a:has-text('Download')",
                ],
                "row_selectors": [
                    "video source[src*='.mp4']",
                    "a[href*='.mp4']",
                    "a[data-quality]",
                    "a:has-text('1080p')", "a:has-text('720p')",
                    "a:has-text('480p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["1080p", "720p", "480p", "240p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        "id": "motherless",
        "name": "Motherless",
        "description": "Motherless (motherless.com) — UGC tube, older KVS-style platform. Per the motherless-downloader README: flashvars, HTML5 video, inject script monitoring (suggests JS-injected sources). URLs are short uppercase-alphanumeric IDs. Speculative selectors; teach pass recommended.",
        "patterns": [
            r"motherless\.com",
            r"motherlessmedia\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='cdn'][href*='.mp4']",
                    "a[href*='motherlessmedia.com']",
                    "video source[src*='.mp4']",
                    "video[data-src*='.mp4']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["HD", "SD"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 360,
            "use_curl_cffi": True,
        },
    },
{
        "id": "eporner",
        "name": "Eporner",
        "description": "Eporner (eporner.com) — free tube with 4K UHD and VR content support per the eporner-downloader README. URL pattern eporner.com/video-{id}/{slug} or /hd-porn/{id}/. Hash-based API call for sources. Speculative selectors.",
        "patterns": [
            r"eporner\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                    ".dload-btn",
                ],
                "row_selectors": [
                    "a.dload[href]",
                    "a[href*='/download/']",
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('4K')", "a:has-text('2160p')",
                    "a:has-text('1080p')", "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p", "VR"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        "id": "xhamster",
        "name": "xHamster",
        "description": "xHamster (xhamster.com) — large free tube, flashvars + mediaDefinitions architecture (similar to Aylo tubes but separate operator). Speculative selectors.",
        "patterns": [
            r"xhamster\.com", r"xhamster\d+\.com",
            r"xhamsterlive\.com",  # live arm, will fail like other lives
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a.video-download-btn[href]",
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('1080p')", "a:has-text('720p')",
                    "a:has-text('480p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["1080p", "720p", "480p", "240p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        # TXXX network: txxx.com / txxx.tube and many sibling tubes
        # often run on the same player engine (HQporner, hclips, etc).
        "id": "txxx_network",
        "name": "TXXX network",
        "description": "TXXX (txxx.com) and sibling tubes running the same player engine. Free tube architecture (HLS + MP4). Speculative selectors.",
        "patterns": [
            r"txxx\.com", r"txxx\.tube", r"hclips\.com",
            r"hqporner\.com", r"hdzog\.com", r"upornia\.com",
            r"voyeurhit\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('1080p')", "a:has-text('720p')",
                    "a:has-text('480p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        "id": "thisvid",
        "name": "ThisVid",
        "description": "ThisVid (thisvid.com) — UGC tube. Speculative selectors based on standard tube architecture.",
        "patterns": [
            r"thisvid\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a.download[href]",
                    "a[href*='/get_file/']",
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('720p')", "a:has-text('480p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["720p", "480p", "360p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 360,
            "use_curl_cffi": True,
        },
    },
{
        "id": "porntrex",
        "name": "PornTrex",
        "description": "PornTrex (porntrex.com) — free tube, HD-focused. Speculative selectors.",
        "patterns": [
            r"porntrex\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a.download[href]",
                    "a[href*='/download/']",
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('1080p')", "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
            # v3.43.63: enable library-extractor fast path
            "use_library_extractor": True,
       },
    },
{
        "id": "yespornplease",
        "name": "YesPornPlease",
        "description": "YesPornPlease (yespornplease.com) — tube site. Speculative selectors.",
        "patterns": [
            r"yespornplease\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
        },
    },
{
        "id": "youjizz",
        "name": "YouJizz",
        "description": "YouJizz (youjizz.com) — older free tube. Speculative selectors.",
        "patterns": [
            r"youjizz\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='.mp4']",
                    "video source[src*='.mp4']",
                    "a:has-text('720p')", "a:has-text('480p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["720p", "480p", "360p"],
            },
        },
        "config_defaults": {
            "quality_preference": "720,480,360",
            "min_resolution": 360,
            "use_curl_cffi": True,
        },
    },
{
        "id": "redgifs",
        "name": "RedGifs",
        "description": "RedGifs (redgifs.com) — short-clip platform built on a JSON API. URL pattern /watch/{slug-id}. Different architecture from tube sites: fetch redgifs.com/v2/gifs/{id} → returns gif object with mp4 URLs in `urls.hd` / `urls.sd`. Speculative — confirm token-fetch flow on real URL.",
        "patterns": [
            r"redgifs\.com",
            r"v\d+\.redgifs\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "a:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "video source[src*='.mp4']",
                    "video[src*='.mp4']",
                    "a[href*='.mp4']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["HD", "SD"],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
            "min_resolution": 360,
            "use_curl_cffi": True,
        },
    },
{
        "id": "stripchat_live",
        "name": "Stripchat (live recording)",
        "description": "Stripchat (stripchat.com) — LIVE CAM site. v3.43.62: live recording runtime available (streamlink + ffmpeg fallback). Worker pipeline detects the live URL and arms a recording watch via the live_recorder module. Requires streamlink (preferred) or ffmpeg on PATH. Saved-video pages on Stripchat may also work after a teach pass — those go through the standard worker pipeline.",
        "patterns": [
            r"stripchat\.com",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"use_live_recorder": True},
    },
{
        "id": "bongacams_live",
        "name": "BongaCams (live recording)",
        "description": "BongaCams (bongacams.com) — LIVE CAM site. v3.43.62: live recording runtime available via streamlink. Worker pipeline detects the live URL and arms a recording watch. Requires streamlink (preferred) or ffmpeg on PATH.",
        "patterns": [
            r"bongacams\.com", r"bongacams\d+\.com",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"use_live_recorder": True},
    },
{
        "id": "fansly_live",
        "name": "Fansly Live (live recording)",
        "description": "Fansly (fansly.com) live broadcasts. v3.43.62: live recording runtime available for the public live-stream layer via streamlink. Saved-video pages on Fansly remain paywalled per-creator and need a teach pass plus an authenticated session — those go through the standard worker pipeline, not the live recorder.",
        "patterns": [
            r"fansly\.com",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"use_live_recorder": True},
    },
{
        "id": "manyvids",
        "name": "ManyVids (LIMITATION)",
        "description": "ManyVids (manyvids.com) — creator paywall (OnlyFans-style). Each creator has their own purchase flow; downloads require an authenticated session with the buyer's account and content varies per page structure. URL routing works; downloads need a teach pass per creator and a session that has actually purchased the content.",
        "patterns": [
            r"manyvids\.com",
        ],
        "learned": {"download": {"trigger_selectors": [
            "a:has-text('Download')", "button:has-text('Download')",
        ], "row_selectors": [
            "a[href*='/download/']", "a[href*='.mp4']",
        ], "url_attribute": "href", "tier_labels_seen": ["HD", "Full HD"]}},
        "config_defaults": {"_limitation": "paywalled_creator_per_purchase"},
    },
{
        "id": "justforfans",
        "name": "JustForFans (LIMITATION)",
        "description": "JustForFans (justfor.fans, justforfans.app) — OnlyFans-style creator paywall. Requires per-creator subscription. URL routing works; downloads need a teach pass + active subscription session per creator.",
        "patterns": [
            r"justfor\.fans", r"justforfans\.app",
        ],
        "learned": {"download": {"trigger_selectors": [
            "a:has-text('Download')",
        ], "row_selectors": [
            "a[href*='.mp4']", "video source[src*='.mp4']",
        ], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "paywalled_creator_per_subscription"},
    },
]
