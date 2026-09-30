"""site_templates._data_learned_o1517 -- the O1517 learned download templates, seeded as built-ins.

Source: user_templates.json on the fleet VMs (the 34-entry O1517 set, identical on all 15; sha256
3b20f743f4ba9ce2ebc147c91c2b120f1f17478e0bfc04a200027726f25abcb1), which was never tracked.
Kept here: the 12 whose site has no built-in template, or whose learned entry is the
applied_template on the VMs today. The other 22 duplicate a built-in for the same site and
are not copied (the built-in resolves those hosts). Ids keep their user_ prefix so every
persisted applied_template still resolves through get() with the VM file absent. created_ts
and source are dropped (built-in shape); no credential field was present. One pattern is
narrowed: the stepsiblingscaught entry also claimed nubiles-porn.com, a site the built-in
nubiles_network owns alone (applied_template there is None on every VM), so that claim is
deduped to the built-in."""

ITEMS = [
{
    "id": "user_b6b_cumlouder_o1517_v2_1790641428",
    "name": "B6B cumlouder O1517 v2",
    "description": "O1517 phase-2 template for cumlouder.com v2 (bd-worker-B6-B): video.js moves id cum_player to its wrapper div and plays via video#cum_player_html5_api.vjs-tech; the scene source is its <source label=1080p> child (the other page mp4 is a bkcdn.net ad preroll). Rendered-DOM check: cumlouder-rendered-dom.txt.",
    "patterns": [
        r"cumlouder\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "video.vjs-tech > source[label]",
            ],
            "url_attribute": "src",
        },
    },
},
{
    "id": "user_b4b_whoreshub_o1517_1790641782",
    "name": "B4B whoreshub O1517",
    "description": "O1517 phase-2 template for whoreshub.com (KVS tube), bd-worker-B4-B, from the live scene DOM 2026-09-29: the 'Download Video:' list holds one a.btn per rendition ('MP4 2160p 4k, 3.06 Gb', 'MP4 1080p, ...', 'MP4 720p, ...', 'MP4 480p, ...'). Logged out they point at /login-required/; with the app's session they are the get_file links. The kt_player flashvars video_url/video_alt_url hold the same get_file links.",
    "patterns": [
        r"whoreshub\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "a.btn[href*='/get_file/']",
                "a.btn:has-text('MP4 2160p')",
                "a.btn:has-text('MP4 1080p')",
                "a.btn:has-text('MP4 720p')",
                "a.btn:has-text('MP4 480p')",
            ],
            "url_attribute": "href",
            "tier_labels_seen": [
                "2160p",
                "1080p",
                "720p",
                "480p",
            ],
        },
    },
},
{
    "id": "user_b4b_stepsiblingscaught_o1517_1790641966",
    "name": "B4B stepsiblingscaught O1517",
    "description": "O1517 phase-2 template for stepsiblingscaught (served from members.nubiles-porn.com, app-held login), bd-worker-B4-B, from campaign/stepsiblingscaught/1/page.scrubbed.html (logged-in scene /video/watch/<id>/<slug>): the downloads dropdown holds a.dropdown-downloads-link per file; the video ones end _<width>.mp4 (3840, 1920, 1280, 960, 640, 480) on content2a.nubiles-porn.com; the same list also carries photo .zip sets, which must never be picked.",
    "patterns": [
        r"stepsiblingscaught\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "a.dropdown-downloads-link[href*='/videos/'][href*='.mp4']",
                "video source[type='video/mp4'][res]",
            ],
            "url_attribute": "href",
            "tier_labels_seen": [
                "3840",
                "1920",
                "1280",
                "960",
                "640",
                "480",
            ],
        },
    },
},
{
    "id": "user_b4b_teenfidelity_o1517_1790644224",
    "name": "B4B teenfidelity O1517",
    "description": "O1517 phase-2 template for teenfidelity (members.kellymadisonmedia.com, session seeded by the operator's cookie import; the login form is behind an invisible reCAPTCHA), bd-worker-B4-B. Measured on a logged-in scene /episodes/<id> (tpl-teenfidelity run 01:0xZ): the page lists one download link per rendition with text 'MP4  4k (820.44 MB)', 'MP4  1080p (393.39 MB)', ...; the scorer instead took the player's '1080p' quality-selector menu (no download event). Rows: the MP4 links only.",
    "patterns": [
        r"kellymadisonmedia\.com",
        r"teenfidelity\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "a:has-text('MP4') >> nth=0",
                "a[href*='.mp4']:has-text('MP4')",
                "a:has-text('MP4')",
            ],
            "url_attribute": "href",
            "tier_labels_seen": [
                "4k",
                "1080p",
                "720p",
                "480p",
            ],
        },
    },
},
{
    "id": "user_b4b_pornhoarder_o1517_1790644732",
    "name": "B4B pornhoarder O1517",
    "description": "O1517 phase-2 template for pornhoarder (.tv/.net/.gd aggregator), bd-worker-B4-B, from the live scene DOM 2026-09-29: scenes are /pornvideo/<slug>/<id>; the scene embeds iframe pornhoarder.net/player.php?video=<id>, whose page shows a poster and #play-button (a POST form) before the hoster player loads; the scene page itself has no <video> and no download link. Rows: the hoster player's <video>/<source> after the play click.",
    "patterns": [
        r"pornhoarder\.(tv|net|gd)",
    ],
    "learned": {
        "download": {
            "trigger_selectors": [
                "#play-button",
                "div.play-button",
            ],
            "row_selectors": [
                "video source[src]",
                "video[src]",
            ],
            "url_attribute": "src",
        },
    },
},
{
    "id": "user_b6b_porndig_o1517_1790650668",
    "name": "B6B porndig O1517",
    "description": "O1517 phase-2 template for porndig, bd-worker-B6-B, from .95 campaign/porndig/1/page.scrubbed.html (scene /videos/<id>/<slug>.html): button.btn_download_post_action toggles .post_download_wrapper, which lists a.post_download_link -> https://videos.porndig.com/download/index/<...>/<n>/porndig.com_<slug>_{360p,540p,720p,1080p,UHD4K}; a hidden #video_full_download_btn points at the site root and must never be picked.",
    "patterns": [
        r"porndig\.com",
    ],
    "learned": {
        "download": {
            "trigger_selectors": [
                "button.btn_download_post_action",
            ],
            "row_selectors": [
                ".post_download_wrapper a.post_download_link[href*='/download/index/']",
                "a.post_download_link[href*='videos.porndig.com/download/index/']",
            ],
            "url_attribute": "href",
            "tier_labels_seen": [
                "360p",
                "540p",
                "720p",
                "1080p",
                "UHD4K",
            ],
        },
    },
},
{
    "id": "user_b6b_justporn_o1517_1790650668",
    "name": "B6B justporn O1517",
    "description": "O1517 phase-2 template for justporn (KVS), bd-worker-B6-B, from the public scene /video/22959/... rendered in Chromium 02:5xZ: ul.fav-drop holds a.download-link -> /get_file/5/<hash>/<k>/<id>/<id>_720p.mp4/?v-acctoken=... and /<id>.mp4/ (original); a.screen-img /get_file/0/.../screenshots/<n>.jpg and related-card data-preview .../<id>_preview.mp4 are not the scene.",
    "patterns": [
        r"justporn\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "ul.fav-drop a.download-link[href*='/get_file/'][href*='.mp4']",
                "a.download-link[href*='/get_file/'][href*='.mp4']",
            ],
            "url_attribute": "href",
            "tier_labels_seen": [
                "720p",
                "original",
            ],
        },
    },
},
{
    "id": "user_a9a_naughtyamerica_o1517_1790650832",
    "name": "A9A naughtyamerica O1517",
    "description": "O1517 phase-2 template for members.naughtyamerica.com (login site, app-held credentials), bd-worker-A9-A, from the .95 campaign scene DOM (campaign/naughtyamerica/1/page.scrubbed.html, scene 34000): the Download tab (a.ui-tabs-anchor #download-options-menu) lists full-movie tiers as a.download-title[data-name] with signed naughtycdn.com hrefs (4K, HD 1080p, 720p, 480p, QuickTime, Mobile); the 5-minute highlight rows (href *5min*) are excluded; take the href.",
    "patterns": [
        r"naughtyamerica\.com",
    ],
    "learned": {
        "download": {
            "trigger_selectors": [
                "a.ui-tabs-anchor[href='#download-options-menu']",
            ],
            "row_selectors": [
                "a.download-title[data-name][href*='naughtycdn.com']:not([href*='5min'])",
            ],
            "url_attribute": "href",
        },
    },
},
{
    "id": "user_b1b_xnxx_o1517_1790650872",
    "name": "B1B xnxx O1517",
    "description": "O1517 phase-2 template for xnxx.com by bd-worker-B1-B from the built-in wgcz_tubes reference. Direct MP4s live in the player script (setVideoUrlHigh/Low); the 'Download' tab and 'HD' text links are dropped because on a tube scene page they match related-video cards (dl95-xvideos-2).",
    "patterns": [
        r"xnxx\.com",
        r"xnxx\d+\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "video source[src*='.mp4']",
                "video[src*='.mp4']",
                "a[href*='xnxx-cdn.com'][href*='.mp4']",
            ],
            "url_attribute": [
                "src",
                "src",
                "href",
            ],
            "tier_labels_seen": [
                "High",
                "Low",
            ],
        },
    },
    "config_defaults": {
        "quality_preference": "1080,720,480",
        "use_library_extractor": True,
        "use_curl_cffi": True,
    },
},
{
    "id": "user_b7b_dfxtra_o1517_1790651319",
    "name": "B7B dfxtra O1517",
    "description": "O1517 phase-2 template for dfxtra (Gamma), bd-worker-B7-B, from the built-in VERIFIED dfxtra reference and the phase-1 journal (window.open grant /movieaction/download/<id>/<tier>/mp4): Download gadget trigger, 1080p option row first, any movieaction option row second.",
    "patterns": [
        r"dfxtra\.com",
    ],
    "learned": {
        "download": {
            "trigger_selectors": [
                "div.download button",
                "div.download",
            ],
            "row_selectors": [
                "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/'][href*='/1080p/']",
                "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']",
            ],
            "url_attribute": "href",
        },
    },
},
{
    "id": "user_b9b_nubiles_o1517_1790651353",
    "name": "B9B nubiles O1517",
    "description": "O1517 phase-2 template for members.nubiles.net (app-held login), bd-worker-B9-B, from the member scene DOM (campaign/nubiles/1/page.scrubbed.html on .95): each scene's own tiers are .edge-download-item a.btn tiles (href content4.nubiles.net ..._<width>_full.mp4, label '3840x2160 4K MP4 (2 GB)'); the header dropdown a.dropdown-downloads-link carries the same hrefs (hidden until opened). Related cards reuse dropdown links for OTHER scenes, so the tile is first.",
    "patterns": [
        r"members\.nubiles\.net",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                ".edge-download-item a.btn[href*='_full.mp4']",
                "ul.dropdown-downloads a.dropdown-downloads-link[href*='_full.mp4']",
            ],
            "url_attribute": "href",
        },
    },
},
{
    "id": "user_b9b_wowgirls_o1517_1790651781",
    "name": "B9B wowgirls O1517",
    "description": "O1517 phase-2 template for venus.wowgirls.com film pages (app-held login), bd-worker-B9-B. The member film page's full downloads are anchors to content-video*.wowgirls.com/download/<film-id>/<WxH>[_60FPS].mp4?filename=...; the film id is the /film/<id>/ path segment, so the work-affinity check can tell this film's tiers from another film's (phase-1 D1: film fc43abef saved ba91fb1f's media). Built from the phase-1 journal, not a DOM capture (no capture exists; teach flow needs noVNC).",
    "patterns": [
        r"venus\.wowgirls\.com",
    ],
    "learned": {
        "download": {
            "row_selectors": [
                "a[href*='wowgirls.com/download/'][href*='.mp4']",
            ],
            "url_attribute": "href",
        },
    },
},
]
