"""site_templates._data_mainstream -- verbatim TEMPLATES slice [83:91] (8 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "netflix",
        "name": "Netflix (LIMITATION: Widevine DRM)",
        "description": "Netflix (netflix.com) — protected by Widevine L1 DRM. NO INDEPENDENT TOOL CAN BYPASS THIS LEGALLY. URL routing surfaces this limitation explicitly; do not attempt downloads. Use Netflix's official offline-download feature on supported mobile/desktop apps.",
        "patterns": [
            r"netflix\.com",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "widevine_drm_protected"},
    },
{
        "id": "hulu",
        "name": "Hulu (LIMITATION: Widevine DRM)",
        "description": "Hulu (hulu.com) — Widevine DRM-protected. Same situation as Netflix — no independent download path. URL routing surfaces this limitation; do not attempt downloads.",
        "patterns": [
            r"hulu\.com",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "widevine_drm_protected"},
    },
{
        "id": "tubi",
        "name": "Tubi (LIMITATION: DRM)",
        "description": "Tubi (tubitv.com) — legit ad-supported streaming with DRM-protected premium content. Ad-supported tier may be technically extractable via HLS scraping but is on shaky legal ground; the template routes URLs to surface the limitation rather than enable circumvention.",
        "patterns": [
            r"tubitv\.com", r"tubi\.tv",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "drm_protected_legit_streaming"},
    },
{
        "id": "movies123",
        "name": "123Movies / 123movies (LIMITATION: piracy aggregator)",
        "description": "123movies / 123movies.online and clones — piracy aggregator with constantly rotating mirror domains, heavy obfuscation, and frequent redirect chains. Selectors are unstable across mirrors. URL routing matches the operator family but downloads are best-effort at most. Recommend yt-dlp for these.",
        "patterns": [
            r"123movies\.", r"\b123movie\b",
            r"gomovies\.", r"putlocker\.",
            r"fmovies\.",  r"sflix\.",
            r"hdtoday\.", r"\bgostream\.",
        ],
        "learned": {"download": {"trigger_selectors": [
            "a:has-text('Download')",
        ], "row_selectors": [
            "a[href*='.mp4']", "iframe[src]", "video source[src]",
        ], "url_attribute": ["href", "src", "src"], "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "piracy_aggregator_unstable"},
    },
{
        "id": "youtube",
        "name": "YouTube (RECOMMEND yt-dlp)",
        "description": "YouTube (youtube.com / youtu.be) — yt-dlp is the industry-standard extractor and works far better than anything this app could re-implement. URL routing surfaces this recommendation rather than offering a half-working in-app path. Use bdctl with `--external-tool yt-dlp` or shell out to yt-dlp directly.",
        "patterns": [
            r"youtube\.com", r"youtu\.be", r"music\.youtube\.com",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "use_yt_dlp_externally"},
    },
{
        "id": "vk_video",
        "name": "VK Video (LIMITATION)",
        "description": "VK Video (vk.com/video) — Russian social network with different auth + player architecture from western tubes. yt-dlp has working extractors. URL routing here surfaces the recommendation.",
        "patterns": [
            r"vk\.com/video", r"vk\.com/clips",
            r"vkvideo\.ru",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [
            "video source[src*='.mp4']", "a[href*='.mp4']",
        ], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "use_yt_dlp_externally"},
    },
{
        "id": "reddit_media",
        "name": "Reddit media (LIMITATION)",
        "description": "Reddit (reddit.com) media — v.redd.it hosts videos as separate DASH .mpd manifests with split audio/video tracks. URL routing works; full download needs DASH-aware extraction (yt-dlp or ffmpeg) — not the standard worker path.",
        "patterns": [
            r"reddit\.com", r"old\.reddit\.com", r"v\.redd\.it", r"redd\.it",
        ],
        "learned": {"download": {"trigger_selectors": [], "row_selectors": [
            "video source[src*='v.redd.it']",
            "shreddit-player",
        ], "url_attribute": "src", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "dash_split_audio_video_needs_external_tool"},
    },
{
        "id": "deviantart",
        "name": "DeviantArt (LIMITATION)",
        "description": "DeviantArt (deviantart.com) — primarily an image gallery; some accounts host video. Login often required to access full-resolution downloads. URL routing works; video downloads need a teach pass.",
        "patterns": [
            r"deviantart\.com", r"da\.deviantart\.net",
        ],
        "learned": {"download": {"trigger_selectors": [
            "a:has-text('Download')", "button:has-text('Download')",
        ], "row_selectors": [
            "a[href*='download']", "a[href*='.mp4']",
            "img[src*='deviantart']",
        ], "url_attribute": "href", "tier_labels_seen": []}},
        "config_defaults": {"_limitation": "image_focused_with_login_required"},
    },
]
