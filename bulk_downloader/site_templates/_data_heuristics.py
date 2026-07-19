"""site_templates._data_heuristics -- verbatim TEMPLATES slice [50:54] (4 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "data_attribute_modern",
        "name": "Modern lazy-loaded data-* attributes",
        "description": "Sites that hide real URLs behind data-href, data-url, data-src, data-download, data-video, data-link to defeat naive scrapers. Tries all common variants.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "[data-href*='.mp4']",
                    "[data-url*='.mp4']",
                    "[data-src*='.mp4']",
                    "[data-download*='.mp4']",
                    "[data-video*='.mp4']",
                    "[data-link*='.mp4']",
                ],
                "url_attribute": ["data-href", "data-url", "data-src",
                                   "data-download", "data-video", "data-link"],
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "resolution_button_text",
        "name": "Resolution-labeled button (4K / 1080p / 720p)",
        "description": "Sites that label download buttons with resolution text. Works on sites where the worker should pick the highest available quality.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a:has-text('7680 x 4320')",
                    "a:has-text('5760 x 3240')",
                    "a:has-text('3840 x 2160')",
                    "a:has-text('1920 x 1080')",
                    "a:has-text('4K')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                    "a[href*='.mp4']:has-text('Download')",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a.download-button",
                ],
            },
        },
        "config_defaults": {
            "quality_preference": "4320,2160,1080,720,480",
        },
    },
{
        "id": "universal_fallback",
        "name": "Universal fallback — any .mp4 link",
        "description": "Catch-all for sites with <a href='....mp4'> download links. LOWEST precision, HIGHEST coverage. Use only as last resort or before teaching. Will probably pick the wrong link on sites with multiple videos per page.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href$='.mp4']",
                    "a[href$='.mkv']",
                    "a[href$='.webm']",
                    "a[href*='.mp4?']",
                    "a[href*='.mp4&']",
                    "video[src$='.mp4']",
                    "source[src$='.mp4']",
                ],
                "url_attribute": ["href", "href", "href", "href", "href",
                                   "src", "src"],
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                    ".download-link",
                ],
            },
        },
    },
{
        # v3.43.20: FilthyKings + any site using the VideoJSPlayer
        # "DownloadOption-Link" anchor pattern. Resolution links are
        # exposed inline (no modal trigger), so trigger_selectors is
        # empty. The resolution-specific entries at the top win the
        # priority ladder so 4K is picked over 1080p when both are
        # present. URL pattern includes the path prefix so the suggest
        # filter recognizes filthykings.com/movieaction/download URLs.
        "id": "videojsplayer_download_option",
        "name": "VideoJSPlayer download links",
        "description": "Sites exposing per-resolution download anchors with the VideoJSPlayer-DownloadOption-Link class (e.g. filthykings.com). The CSS-in-JS hash classes are skipped automatically by _looks_hashed; the semantic class is stable across rebuilds.",
        "patterns": [r"filthykings\.com", r"/movieaction/download/"],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.VideoJSPlayer-DownloadOption-Link:has-text('2160p')",
                    "a.VideoJSPlayer-DownloadOption-Link:has-text('1080p')",
                    "a.VideoJSPlayer-DownloadOption-Link:has-text('720p')",
                    "a.VideoJSPlayer-DownloadOption-Link",
                    "a[class*='VideoJSPlayer-DownloadOption-Link']",
                    "a[href*='/movieaction/download/']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
]
