"""site_templates._data_studios_a -- verbatim TEMPLATES slice [33:50] (17 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "vip4k_family",
        "name": "VIP4K / Black4K / Tushy4K family",
        "description": "VERIFIED 2026-09-15 (row 722): login https://vip4k.com/en/login (in-form Cloudflare Turnstile checkbox, clicked by the runtime's container click; no puzzle), lands https://members.vip4k.com/en/ (cross-origin from the login host, declared via success_url); listing https://members.vip4k.com/en/videos; scene 'Download' button.player-actions__item--download toggles a hidden menu of a.download__item rows (Mobile 320p .. 4K 2160p) whose hrefs are javascript:void(0) -- the JS click issues a tokenised video.vip4k.com/secure/<uuid>/<tier>.mp4 URL, so the runner follows the click, never the href; picked Full HD 1080p (the 4K file is ~19 GB), file 'Be True _ Vip4k [1080p].mp4'. Same operator as WowGirls (see wowgirls_network); this entry is the VIP4K-family quick-select with the verified vip4k.com config.",
        "patterns": [
            r"vip4k\.com",
            r"black4k\.com",
            r"tushy4k\.com",
            r"4kteens\.com",
            # PM-handoff 2026-09-06: tiny4k removed -- PornPros / Fame Digital,
            # see the `pornpros_tiny4k` template.
            r"teens4k\.com",
            r"hot4k\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    # Row 722 (2026-09-15), measured on members.vip4k.com/en/videos/<id>.
                    "button.player-actions__item--download",
                    "span.player-actions__text:has-text('Download')",
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    # Row 722: the revealed menu rows carry no usable href
                    # (javascript:void(0)); empty attribute = click-and-capture.
                    "a.download__item",
                    "div.download-button[data-href]",
                    "a.ct_dl_button[href]",
                    "a.download__item[data-download]",
                    "#exDownloadMenu .exp-menu-item",
                ],
                "url_attribute": ["", "data-href", "href", "data-download", ""],
                "tier_labels_seen": ["4K 2160p", "Full HD 1080p", "HD 720p", "High 540p", "Mobile 320p"],
            },
        },
        "config_defaults": {
            # Row 722 verified vip4k.com config (:5555, 2026-09-15). 1080p-first
            # on purpose: the 4K tier is ~19 GB per scene.
            "login_url": "https://vip4k.com/en/login",
            "success_url": "https://members.vip4k.com/en/",
            "crawler_listing_url": "https://members.vip4k.com/en/videos",
            "trigger_selector": "button.player-actions__item--download",
            "dl_selector": "a.download__item",
            "quality_preference": "1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
        },
    },
{
        "id": "vixen_network",
        "name": "Vixen Network (Vixen/Blacked/Tushy/Deeper/vixenplus)",
        "description": "Vixen Media Group sites behind the login.vixen.com SSO. VERIFIED 2026-09-06 (PM-handoff template gap report): the scene page's DOWNLOAD control opens a DownloadModal whose tier buttons are labeled '4K MP4 UHD' / 'HD MP4 1080P' / 'HD MP4 720P' / 'SD MP4 480P'; the resulting cdn-download-* URLs are IP-BOUND (an ip= parameter pinned to the egress that fetched them) AND cookie-bound, with a per-period download quota. The login POST is Cloudflare-Turnstile gated, so an unattended login needs a Turnstile solver; that is NOT part of this template.",
        "patterns": [
            r"vixen\.com",
            r"blacked\.com",
            r"blackedraw\.com",
            r"tushy\.com",
            r"tushyraw\.com",
            r"deeper\.com",
            r"slayed\.com",
            r"milfy\.com",
            # PM-handoff 2026-09-06: the members host and the one brand the
            # pattern list was missing.
            r"vixenplus\.com",
            r"wifey\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "input[name='username']",
                ],
                "pass_field": [
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button:has-text('LOGIN')",
                ],
            },
            "download": {
                "row_selectors": [
                    "button:has-text('4K MP4 UHD')",
                    "button:has-text('HD MP4 1080P')",
                    "a[href*='cdn-download-']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K MP4 UHD", "HD MP4 1080P", "HD MP4 720P", "SD MP4 480P"],
                "trigger_selectors": [
                    "a:has-text('DOWNLOAD')",
                    "[class*=DownloadButton]",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_real_chrome": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "mindgeek_family",
        "name": "MindGeek (Brazzers / Reality Kings / Digital Playground)",
        "description": "MindGeek shared backend. Download UI varies by brand but the underlying structure is similar. Speculative — auth/DRM frequently blocks direct fetch.",
        "patterns": [
            r"brazzers\.com",
            r"realitykings\.com",
            r"digitalplayground\.com",
            r"twistys\.com",
            r"babes\.com",
            r"mofos\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.scene-download-link[href]",
                    "a[data-resolution][href*='.mp4']",
                    ".video-actions a[href*='download']",
                ],
                "url_attribute": ["href", "href", "href"],
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download Full')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720,480",
            "min_resolution": 720,
            "use_curl_cffi": True,
        },
    },
{
        "id": "adulttime_network",
        "name": "Adult Time network",
        "description": "Adult Time umbrella (covers many sub-brands). Speculative on selector specifics; download UI changes per brand.",
        "patterns": [
            r"adulttime\.com",
            r"gendx\.com",
            r"girlsway\.com",
            r"transangels\.com",
            r"pure-taboo\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[data-dl-url]",
                    "a.download-button[href]",
                    "a:has-text('4K Ultra HD')",
                    "a:has-text('Full HD')",
                ],
                "url_attribute": ["data-dl-url", "href", "href", "href"],
                "trigger_selectors": [
                    "button:has-text('Download')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 1080,
            # Measured scene-request upsell: dismissal lands on members home;
            # the runtime therefore re-requests the original scene afterward.
            "dismiss_selectors": (
                "button:text-is('No Thanks'), a:text-is('No Thanks')"
            ),
        },
    },
{
        "id": "evilangel",
        "name": "Evil Angel",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.evilangel.com/en/login (Gamma form; cookie consent bar, then the post-login SkipPageButton / 'Continue to Members Area' wall cleared via dismiss_selectors_login), lands https://members.evilangel.com/en (cross-origin from www, declared via success_url); scene page 'Download' is div.download > button, opening 8 a.VideoJSPlayer-DownloadOption-Link rows 160p..2160p whose hrefs are host-relative /movieaction/download/<id>/<res>/mp4 (resolved against the members origin by the runtime); picked 4K 2160p (3840x2160). The per-URL consent/close group ('I Agree', close, 'No Thanks') is inherited from gamma_kosmos, which the wizard ranks first for this host; this entry keeps only the measured scene upsell control per URL.",
        "patterns": [r"evilangel\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    # Row 722 (2026-09-15): the measured Gamma modal rows.
                    "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']",
                    "a.download-link[href*='.mp4']",
                    "a[data-quality][href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "div.download button",
                ],
                "tier_labels_seen": ["4K 2160p", "1080p", "720p", "480p", "360p", "288p", "160p"],
            },
        },
        "config_defaults": {
            # Row 722 verified evilangel config (:5555, 2026-09-15).
            "login_url": "https://www.evilangel.com/en/login",
            "success_url": "https://members.evilangel.com/en",
            "dismiss_selectors_login": (
                "a.SkipPageButton-ButtonLink, "
                "a:has-text('No Thanks. Continue'), "
                "a:has-text('Continue to Members Area')"
            ),
            "trigger_selector": "div.download button",
            "dl_selector": "a.VideoJSPlayer-DownloadOption-Link[href*=\"2160p\"]",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            # Same measured Gamma/Kosmos scene upsell as Adult Time.
            "dismiss_selectors": (
                "button:text-is('No Thanks'), a:text-is('No Thanks')"
            ),
        },
    },
{
        "id": "kink_network",
        "name": "Kink.com network",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.kink.com/login; gates met in order: CookieYes 'Accept All' consent, then the 'WARNING: ADULTS ONLY' wall whose safe control is ENTER KINK (#enter-actual-site; the runtime's ENTER + brand-token affordance clears it, the 'I Disagree, Exit Here' sibling stays denylisted), then the visible login form (a hidden form#loginPopup also exists; the submit anchors on the filled password field), then a 'SKIP FOR NOW' interstitial; lands same-origin www.kink.com; shoot page 'Download' is a Bootstrap dropdown toggle button.buy-shoot whose hidden a.dropdown-item rows (4K/1080p/720p/480p) link /shoot/<id>/download?filename=...; picked 4K, file '108453_shoot_4k.mp4'.",
        "patterns": [r"kink\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    # Row 722 (2026-09-15): the measured dropdown rows.
                    "a.dropdown-item[href*='/download?filename=']",
                    "a.movieDownload[href]",
                    "a[data-fileurl]",
                    ".download-options a[href*='.mp4']",
                ],
                "url_attribute": ["href", "href", "data-fileurl", "href"],
                "trigger_selectors": [
                    "button.buy-shoot",
                    "button:has-text('Download')",
                ],
                "tier_labels_seen": ["4K", "1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            # Row 722 verified kink config (:5555, 2026-09-15).
            "login_url": "https://www.kink.com/login",
            "trigger_selector": "button.buy-shoot",
            "dl_selector": "a.dropdown-item[href*=\"/download?filename=\"]",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            # Encounter order measured on Kink: CookieYes banner, adult-entry
            # overlay, then the header control that renders the login modal.
            # The unsafe sibling ("I Disagree, Exit Here") is deliberately
            # absent and is independently refused by the runtime denylist.
            "dismiss_selectors": (
                "button[data-cky-tag='accept-button'], button.cky-btn-accept\n"
                "button:text-is('I Agree, Enter Here'), "
                "a:text-is('I Agree, Enter Here')\n"
                "button:text-is('LOG IN'), a:text-is('LOG IN')"
            ),
        },
    },
{
        "id": "gammafilms_network",
        "name": "Gamma Films / 21Sextury / 21Naturals",
        "description": "Gamma Entertainment umbrella. Speculative.",
        "patterns": [
            r"21sextury\.com",
            r"21naturals\.com",
            r"sex-art\.com",
            r"viv-thomas\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.dl-link[href]",
                    "a[data-download][href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "button:has-text('Download')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
        },
    },
{
        "id": "score_group",
        "name": "Score Group network",
        "description": "Score / Scoreland / Big Tits family. Speculative.",
        "patterns": [
            r"scoreland\.com",
            r"pornmegaload\.com",
            r"40somethingmag\.com",
            r"50plusmilfs\.com",
            r"naughtymag\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.zipdl[href]",
                    "a[href*='.mp4'][href*='download']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "1080,720,480",
        },
    },
{
        "id": "wicked_pictures",
        "name": "Wicked Pictures",
        "description": "Wicked Pictures direct. Speculative.",
        "patterns": [r"wicked\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download-btn[href]",
                    "a[data-download-link]",
                ],
                "url_attribute": ["href", "data-download-link"],
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
        },
    },
{
        "id": "bangbros_network",
        "name": "Bang Bros network",
        "description": "VERIFIED 2026-09-15 (row 722): login https://site-ma.bangbros.com/login (Aylo members app; www/members.bangbros.com/login redirect here); gates met: 'ACCEPT ALL COOKIES' consent (cleared by the runtime after its control re-measured), post-login /store upsell page whose bundle checkboxes are verified unchecked, then 'Continue to Members Area' -- a <div> inside div[data-test-id=\"secrev-submit\"], not a button, hence dismiss_selectors_login; lands https://site-ma.bangbros.com/ (member state 'My Points'); scenes live under /scene/<id>/<slug> (the /videos hub has no cards, /scenes does); scene 'Download' is an href-less BUTTON revealing href-less BUTTONs 'h264 - 2160p/1080p/720p/480p' whose click fires a signed download event (click-and-capture, no attribute); picked h264 - 1080p (CDN hash filename).",
        "patterns": [
            r"bangbros\.com",
            r"bangbus\.com",
        ],
        "learned": {
            "login": {
                # Row 722 (2026-09-15), measured on site-ma.bangbros.com/login.
                "user_field": [
                    "input[autocomplete='username']",
                ],
                "pass_field": [
                    "input[type='password']",
                ],
                "submit_btn": [
                    "button[type='submit']",
                ],
            },
            "download": {
                "row_selectors": [
                    # Row 722: the revealed tier controls are BUTTONs without
                    # an href; empty attribute = click-and-capture.
                    "button:has-text('h264 - 2160p')",
                    "button:has-text('h264 - 1080p')",
                    "a.download-link[href]",
                    "a[data-dl-quality]",
                ],
                "url_attribute": ["", "", "href", "data-dl-quality"],
                "trigger_selectors": [
                    "button:text-is('Download')",
                ],
                "tier_labels_seen": ["h264 - 2160p", "h264 - 1080p", "h264 - 720p", "h264 - 480p"],
            },
        },
        "config_defaults": {
            # Row 722 verified site-ma-bangbros config (:5555, 2026-09-15).
            "login_url": "https://site-ma.bangbros.com/login",
            "success_url": "https://site-ma.bangbros.com/",
            "dismiss_selectors_login": (
                "div[data-test-id=\"secrev-submit\"] "
                "div:text-is(\"Continue to Members Area\")"
            ),
            "trigger_selector": "button:text-is(\"Download\")",
            "dl_selector": "button:has-text(\"h264 - 1080p\")",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
        },
    },
{
        "id": "naughtyamerica",
        "name": "Naughty America",
        "description": "VERIFIED 2026-09-15 (row 722): login https://members.naughtyamerica.com/login (same-origin form with a Cloudflare Turnstile checkbox: the runtime clicks the widget container, the cf-turnstile-response token populates in ~5 s, then the form submits in-page); lands the authenticated members feed on the same host (no success_url needed); scene pages are /scene/<slug>-<id>; the download control is a tab anchor a.ui-tabs-anchor 'Download' (href #download-options-menu) opening a panel of a.download-title rows (QuickTime / 4K / HD1080p / HD720p / HD480p / Mobile, plus 5-minute clips and a 'Download All' zip); the generic anchor walk picked the full 4K movie, file 'mfhmaderesmax_4k.mp4' (no trigger/dl selector config was needed).",
        "patterns": [r"naughtyamerica\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    # Row 722 (2026-09-15): the measured download panel rows.
                    "a.download-title:has-text('4K')",
                    "a.download-title[href*='.mp4']",
                    "a.scene-dl[href]",
                    "a[data-stream-quality][href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "a.ui-tabs-anchor:has-text('Download')",
                ],
                "tier_labels_seen": ["4K", "HD1080p", "HD720p", "HD480p", "Mobile"],
            },
        },
        "config_defaults": {
            # Row 722 verified naughtyamerica config (:5555, 2026-09-15).
            "login_url": "https://members.naughtyamerica.com/login",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
        },
    },
{
        "id": "tnaflix_family",
        "name": "TNAFlix / Empflix / MovieFap family",
        "description": "TNAFlix shared backend across its tube properties. Speculative.",
        "patterns": [
            r"tnaflix\.com",
            r"empflix\.com",
            r"moviefap\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download[href]",
                    "a[href*='.mp4?']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "a:has-text('Download')",
                ],
            },
        },
    },
{
        "id": "metart_network",
        "name": "MetArt / SexArt / EternalDesire family",
        "description": "MetArt Network. Premium fine-art adult sites.",
        "patterns": [
            r"metart\.com",
            r"metartx\.com",
            r"sexart\.com",
            r"eternaldesire\.com",
            r"thelifeerotic\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download-link[href*='.mp4']",
                    "a[data-quality][href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "button:has-text('Download')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
        },
    },
{
        "id": "x_art_premium",
        "name": "X-Art / Bellesa premium",
        "description": "X-Art and similar artistic-premium adult sites. Speculative.",
        "patterns": [
            r"x-art\.com",
            r"bellesafilms\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.dl-quality[href]",
                    "a[data-download-quality]",
                ],
                "url_attribute": ["href", "data-download-quality"],
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
        },
    },
{
        "id": "atk_galleries",
        "name": "ATK galleries (ATKingdom etc.)",
        "description": "ATK Galleries network. Layout is older-style with separate download buttons per resolution.",
        "patterns": [
            r"atkgalleria\.com",
            r"atkhairy\.com",
            r"atkpetites\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.atk-download[href]",
                    "a[href*='.mp4']:has-text('Download')",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "amateur_tube_generic",
        "name": "Generic amateur-tube layout",
        "description": "Common layout on amateur/community video sites: download link in a sidebar or below the player, typically inside <div class='download'> or similar.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".download a[href]",
                    ".video-download a[href]",
                    ".download-options a[href*='.mp4']",
                    "a.download-btn[href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "premium_studio_generic",
        "name": "Premium studio generic",
        "description": "Common pattern on premium-studio sites: per-resolution download buttons in a row, each with the resolution label in text and URL on href.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download-link[data-quality]",
                    "a[data-resolution][href]",
                    ".download-row a[href*='.mp4']",
                    "a:has-text('Full HD')",
                    "a:has-text('4K')",
                ],
                "url_attribute": ["href", "href", "href", "href", "href"],
                "trigger_selectors": [
                    "button:has-text('Download Full Movie')",
                    "a:has-text('Download Full Movie')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720,480",
            "min_resolution": 720,
        },
    },
{
        # Row 722 (2026-09-15): dedicated FilthyKings entry. The heuristic
        # `videojsplayer_download_option` still matches the host for the
        # anchor shape; this entry carries the verified site config.
        "id": "filthykings",
        "name": "Filthy Kings",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.filthykings.com/en/login (tour-site 'Enter' age gate and 'Yes, I understand' cookie bar cleared by the runtime; the form submits by navigation before any submit click), lands https://members.filthykings.com/en (cross-origin from www, declared via success_url; the login-host cookies do not cover the members host, the members session cookies do); listing https://members.filthykings.com/en/videos; scene 'Download' is button.ScenePlayerHeaderPlus-IconItem-Button opening Modal.VideoJSPlayer-Modal with a.VideoJSPlayer-DownloadOption-Link rows 160p..'4K 2160p' whose hrefs are host-relative /movieaction/download/<id>/<res>/mp4 (resolved against the members origin); picked 4K 2160p, 3.0 GB.",
        "patterns": [
            r"filthykings\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "button.ScenePlayerHeaderPlus-IconItem-Button:has-text('Download')",
                ],
                "row_selectors": [
                    "a.VideoJSPlayer-DownloadOption-Link:has-text('2160p')",
                    "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K 2160p", "1080p", "720p", "480p", "360p", "288p", "160p"],
            },
        },
        "config_defaults": {
            "login_url": "https://www.filthykings.com/en/login",
            "success_url": "https://members.filthykings.com/en",
            "crawler_listing_url": "https://members.filthykings.com/en/videos",
            "trigger_selector": "button.ScenePlayerHeaderPlus-IconItem-Button:has-text('Download')",
            "dl_selector": "a.VideoJSPlayer-DownloadOption-Link:has-text('2160p')",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
        },
    },
{
        # Row 722 (2026-09-15): dedicated Dfxtra entry. `gamma_kosmos` still
        # matches the host for the shared player shape and login selectors;
        # this entry carries the verified site config.
        "id": "dfxtra",
        "name": "Dfxtra",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.dfxtra.com/en/login (Gamma form; per-URL consent/close/'No Thanks' group and the post-login SkipPageButton / 'No Thanks. Continue' / 'Continue to Members Area' wall cleared via the two dismiss scopes), lands https://members.dfxtra.com/en (cross-origin from www, declared via success_url); scene 'Download' is div.download > button (six buttons share the ScenePlayerHeaderPlus-IconItem class, so the plain class picks 'Favorites' -- use the div.download scope), opening 6 a.VideoJSPlayer-DownloadOption-Link rows 288p..2160p whose hrefs are host-relative /movieaction/download/<id>/<res>/mp4 (resolved against the members origin); picked 4K 2160p (3840x2160, 4.5 GB).",
        "patterns": [
            r"dfxtra\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "div.download button",
                ],
                "row_selectors": [
                    "a.VideoJSPlayer-DownloadOption-Link[href*='2160p']",
                    "a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K 2160p", "1080p", "720p", "480p", "360p", "288p"],
            },
        },
        "config_defaults": {
            "login_url": "https://www.dfxtra.com/en/login",
            "success_url": "https://members.dfxtra.com/en",
            "dismiss_selectors": (
                "button:has-text('I Agree'), "
                "button[aria-label*='close' i], "
                "button:text-is('No Thanks'), "
                "a:text-is('No Thanks')"
            ),
            "dismiss_selectors_login": (
                "a.SkipPageButton-ButtonLink, "
                "a:has-text('No Thanks. Continue'), "
                "a:has-text('Continue to Members Area')"
            ),
            "trigger_selector": "div.download button",
            "dl_selector": "a.VideoJSPlayer-DownloadOption-Link[href*=\"2160p\"]",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
        },
    },
{
        # Row 722 (2026-09-15): dedicated Brazzers entry for the site-ma
        # members app. `mindgeek_family` still matches brazzers.com for the
        # shared-backend guess; this entry carries the verified config.
        "id": "brazzers",
        "name": "Brazzers (site-ma members app)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://site-ma.brazzers.com/login (www.brazzers.com/login is a 404; the form has no upsell checkboxes and a reCAPTCHA badge only); the submit first hops to www.brazzers.com (same brand, accepted by the runtime) and lands https://site-ma.brazzers.com/store, declared via success_url; the members root then shows 'My Points' and scene links /scene/<id>/<slug>; scene 'Download' is an href-less BUTTON whose click reveals a 'Download Formats' dropdown of BUTTON.sc-o8a1bb-1 items 'h264 - 2160p/1080p/720p/480p/320p' (styled-components hash class -- JS-driven, no href, click-and-capture); picked h264 - 2160p, 5.6 GB (CDN hash filename).",
        "patterns": [
            r"brazzers\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                ],
                "row_selectors": [
                    "button.sc-o8a1bb-1:has-text('h264 - 2160p')",
                    "button.sc-o8a1bb-1",
                ],
                # EMPTY ON PURPOSE: the tier buttons carry no href; the click
                # fires the signed download (see reptyle_teamskeet).
                "url_attribute": ["", ""],
                "tier_labels_seen": ["h264 - 2160p", "h264 - 1080p", "h264 - 720p", "h264 - 480p", "h264 - 320p"],
            },
        },
        "config_defaults": {
            "login_url": "https://site-ma.brazzers.com/login",
            "success_url": "https://site-ma.brazzers.com/store",
            "trigger_selector": "button:has-text(\"Download\")",
            "dl_selector": "button.sc-o8a1bb-1",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
        },
    },
]
