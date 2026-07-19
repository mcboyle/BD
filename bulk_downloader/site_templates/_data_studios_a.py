"""site_templates._data_studios_a -- verbatim TEMPLATES slice [33:50] (17 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "vip4k_family",
        "name": "VIP4K / Black4K / Tushy4K family",
        "description": "Same operator as WowGirls; identical HTML pattern. Already covered by wowgirls_network template, this is just a quick-select alias for these specific domains.",
        "patterns": [
            r"vip4k\.com",
            r"black4k\.com",
            r"tushy4k\.com",
            r"4kteens\.com",
            r"tiny4k\.com",
            r"teens4k\.com",
            r"hot4k\.com",
        ],
        "learned": {
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
                "url_attribute": ["data-href", "href", "data-download", ""],
            },
        },
        "config_defaults": {
            "quality_preference": "4320,2160,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
        },
    },
{
        "id": "vixen_network",
        "name": "Vixen / Blacked / Tushy / Deeper network",
        "description": "Vixen Media Group sites. Resolution buttons typically labeled '4K' / '1080p' / '720p'. Speculative — site refreshes layouts often.",
        "patterns": [
            r"vixen\.com",
            r"blacked\.com",
            r"blackedraw\.com",
            r"tushy\.com",
            r"tushyraw\.com",
            r"deeper\.com",
            r"slayed\.com",
            r"milfy\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.video-download-link[href]",
                    "a[data-download-url]",
                    "a:has-text('4K')[href*='.mp4']",
                    "a:has-text('1080p')[href*='.mp4']",
                ],
                "url_attribute": ["href", "data-download-url", "href", "href"],
                "trigger_selectors": [
                    "button:has-text('Download')",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
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
        },
    },
{
        "id": "evilangel",
        "name": "Evil Angel",
        "description": "Evil Angel direct site. Speculative — verify against current layout.",
        "patterns": [r"evilangel\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download-link[href*='.mp4']",
                    "a[data-quality][href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
        },
    },
{
        "id": "kink_network",
        "name": "Kink.com network",
        "description": "Kink.com and sub-brands. Speculative.",
        "patterns": [r"kink\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.movieDownload[href]",
                    "a[data-fileurl]",
                    ".download-options a[href*='.mp4']",
                ],
                "url_attribute": ["href", "data-fileurl", "href"],
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
        "description": "Bang Bros and related sites. Speculative.",
        "patterns": [
            r"bangbros\.com",
            r"bangbus\.com",
        ],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download-link[href]",
                    "a[data-dl-quality]",
                ],
                "url_attribute": ["href", "data-dl-quality"],
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
        },
    },
{
        "id": "naughtyamerica",
        "name": "Naughty America",
        "description": "Naughty America direct. Speculative.",
        "patterns": [r"naughtyamerica\.com"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.scene-dl[href]",
                    "a[data-stream-quality][href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
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
]
