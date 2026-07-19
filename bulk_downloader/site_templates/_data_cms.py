"""site_templates._data_cms -- verbatim TEMPLATES slice [24:33] (9 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "xfileshare_clone",
        "name": "XFileSharing / XVideoSharing clone",
        "description": "PHP-based file-host scripts. Two-step: page has a button that POSTs a form to reveal the real link. Common on legacy free-host sites.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.download_link",
                    "a.btn-download[href*='download']",
                    "a[href*='/d/']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [
                    "button[name='method_free']",
                    "button:has-text('Generate')",
                    "input[type='submit'][value*='Free Download']",
                ],
            },
        },
    },
{
        "id": "vbulletin_attachments",
        "name": "vBulletin attachment galleries",
        "description": "vBulletin forum attachment downloads. Videos posted as forum attachments with /attachment.php?attachmentid=N links. High confidence.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href*='attachment.php?attachmentid=']",
                    ".attachments a[href*='attachment']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "xenforo_media",
        "name": "XenForo media gallery",
        "description": "XenForo forums with the Media Gallery addon. Videos under /media/ with download buttons.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a.button[href*='/media/'][href*='download']",
                    ".mediaContent a[href*='.mp4']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "wordpress_video_plugins",
        "name": "WordPress video plugins (generic)",
        "description": "Catch-all for WordPress sites using common video plugins (Vidorev, Video Gallery, Easy Video Player). Speculative — WP plugin landscape is fragmented.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    ".wp-video-shortcode source",
                    ".vp-portfolio__item-video a[href$='.mp4']",
                    ".video-download-link",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "phpmotion_clone",
        "name": "PHPMotion / Clip-share style",
        "description": "Legacy PHPMotion-based community video sites. Download via /download_video.php?file=... endpoint.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href*='download_video.php']",
                    "a.video_download[href]",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "phpvibe",
        "name": "PHPVibe tube engine",
        "description": "PHPVibe video CMS. Common on smaller community sites.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href*='/download/']",
                    ".video-options a[href*='.mp4']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "verystream_legacy",
        "name": "Verystream / Streamtape style",
        "description": "Legacy file-host video players. The real URL is constructed via JS from obfuscated parts. Worker may need stream capture for these.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[id*='video'] source[type='video/mp4']",
                    "video[id='videolink']",
                ],
                "url_attribute": "src",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "directories_with_thumbnails",
        "name": "Open directory listings",
        "description": "Open Apache/Nginx directory listings with .mp4 files. The 'site' is just a folder of files. High confidence when applicable.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "a[href$='.mp4']",
                    "a[href$='.mkv']",
                    "a[href$='.avi']",
                ],
                "url_attribute": "href",
                "trigger_selectors": [],
            },
        },
    },
{
        "id": "wp_rocketloader_bypass",
        "name": "Cloudflare RocketLoader-protected video",
        "description": "Sites using Cloudflare's RocketLoader to lazy-load video URLs into data-cfsrc until JS runs. Worker needs JS execution.",
        "patterns": [],
        "learned": {
            "download": {
                "row_selectors": [
                    "video[data-cfsrc]",
                    "source[data-cfsrc*='.mp4']",
                ],
                "url_attribute": "data-cfsrc",
                "trigger_selectors": [],
            },
        },
    },
]
