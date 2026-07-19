"""site_templates._data_studios_b -- verbatim TEMPLATES slice [54:62] (8 elements). Do not reformat; element literals copied byte-for-byte from templates.py @v447."""

ITEMS = [
{
        "id": "nubiles_network",
        "name": "Nubiles Network (Nubile, Nubile.net, NubileFilms, NubilePorn)",
        "description": "Nubiles Inc operator family. Covers Nubiles, Nubile Films, Nubile Porn, MomsTeachSex, MomsLickTeens, MomsBangTeens, Step Siblings Caught, and related brands. (Speculative — patterns derived from Nubiles network's typical member-area structure. Run a teach pass on first use to confirm selectors.)",
        "patterns": [
            r"nubile\.com",
            r"nubile\.net",
            r"nubiles\.net",
            r"nubilefilms\.com",
            r"nubileporn\.com",
            r"nubilesporn\.com",
            r"momsteachsex\.com",
            r"momslickteens\.com",
            r"momsbangteens\.com",
            r"stepsiblingscaught\.com",
            r"myveryfirsttime\.com",
            r"detentiongirls\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#email",
                    "#username",
                    "input[name='username']",
                    "input[name='email']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password",
                    "input[name='password'][type='password']",
                    "input[type='password']",
                ],
                "submit_btn": [
                    "button[type='submit']",
                    "input[type='submit']",
                    "button:has-text('Login')",
                    "button:has-text('Log in')",
                    "button:has-text('Sign in')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                    ".download-button",
                ],
                "row_selectors": [
                    # Nubiles member area typically exposes a "Movies"
                    # block with per-resolution download buttons. The
                    # selectors below are broad — match either an a-tag
                    # or button with a resolution label.
                    "a[href*='/download/']",
                    "a[href*='download.php']",
                    "a[download][href*='.mp4']",
                    "a:has-text('4K')",
                    "a:has-text('2160p')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
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
        },
    },
{
        "id": "nookies",
        "name": "Nookies",
        "description": "Nookies (nookies.com). (Speculative — created without HTML samples. Run a teach pass on first use to refine. If the site uses a different download URL pattern than /download/, update row_selectors via the site edit form.)",
        "patterns": [
            r"nookies\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#email", "#username",
                    "input[name='username']", "input[name='email']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Login')", "button:has-text('Sign in')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[download][href*='.mp4']",
                    "a:has-text('4K')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "1080p", "720p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "new_sensations",
        "name": "New Sensations",
        "description": "New Sensations studio (newsensations.com). (Speculative — patterns derived from typical premium-studio member area. Run a teach pass to refine.)",
        "patterns": [
            r"newsensations\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#username", "#email",
                    "input[name='username']", "input[name='email']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Login')", "button:has-text('Log in')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[download][href*='.mp4']",
                    "a.download-link",
                    "a:has-text('4K')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "1080p", "720p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "bang_originals",
        "name": "Bang.com (BangOriginals)",
        "description": "Bang.com / BangOriginals network. Distinct from the Bang Bros network (which has its own template). (Speculative — created without HTML samples.) Network includes Bang Originals, Bang Glamkore, Bang Trickery, Bang Real MILFs, and related brands.",
        "patterns": [
            r"bang\.com",
            r"bangglamkore\.com",
            r"bangtrickery\.com",
            r"bangrealmilfs\.com",
            r"bangrealteens\.com",
            r"bangcasting\.com",
            r"bangsurprise\.com",
            r"bangconfessions\.com",
            r"bangroadside\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#email", "#username",
                    "input[name='email']", "input[name='username']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Sign In')",
                    "button:has-text('Login')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='download.php']",
                    "a[download][href*='.mp4']",
                    "a:has-text('4K')", "a:has-text('2160p')",
                    "a:has-text('1080p')", "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "teen_mega_world",
        "name": "Teen Mega World",
        "description": "Teen Mega World (teenmegaworld.net/.com) — Russian-operated network with ~20 sub-brands. Network sites typically share the same member-area template. (Speculative — created without HTML samples.)",
        "patterns": [
            r"teenmegaworld\.net",
            r"teenmegaworld\.com",
            r"tmwvrnet\.com",
            r"old-n-young\.com",
            r"sexywhitekitten\.com",
            r"creampie-angels\.com",
            r"first-bgg\.com",
            r"anal-beauty\.com",
            r"beauty-angels\.com",
            r"x-angels\.com",
            r"sheisnerdy\.com",
            r"tutor4k\.com",
            r"agentmegan\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#username", "#email", "#login",
                    "input[name='username']", "input[name='login']",
                    "input[name='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Login')",
                    "button:has-text('Log in')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='download.php']",
                    "a[download][href*='.mp4']",
                    "a:has-text('4K')", "a:has-text('2160p')",
                    "a:has-text('1080p')", "a:has-text('720p')",
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
        },
    },
{
        "id": "dogfart_network",
        "name": "Dogfart Network (BlacksOnBlondes, CuckoldSessions, etc.)",
        "description": "Dogfart Network covers ~30+ brands sharing a single login + member area. Major brands: Blacks On Blondes, Cuckold Sessions, Watching My Mom Go Black, We Fuck Black Girls, Interracial Pickups, Zebra Girls, Glory Hole, Spring Break Life, plus the main dogfartnetwork.com hub. (Speculative — created without HTML samples.)",
        "patterns": [
            r"dogfartnetwork\.com",
            r"blacksonblondes\.com",
            r"cuckoldsessions\.com",
            r"watchingmymomgoblack\.com",
            r"wefuckblackgirls\.com",
            r"interracialpickups\.com",
            r"zebragirls\.com",
            r"gloryhole\.com",
            r"gloryholeinitiations\.com",
            r"springbreaklife\.com",
            r"blacksoncougars\.com",
            r"blackmeatwhitefeet\.com",
            r"blackdickstinywhitechicks\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#username", "#email",
                    "input[name='username']",
                    "input[name='email']",
                    "input[name='user']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                    "input[name='pass']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Login')", "button:has-text('Sign In')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='download.php']",
                    "a[download][href*='.mp4']",
                    "a:has-text('HD')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
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
            "use_persistent_profile": True,
        },
    },
{
        "id": "teamskeet_network",
        "name": "TeamSkeet Network (TeamSkeet, Exxxtra Small, Innocent High, etc.)",
        "description": "TeamSkeet (owned by Paper Street Media) covers ~25 brands sharing the same member-area template. Major brands: TeamSkeet itself, Exxxtra Small, Innocent High, BFFs, Step Siblings, Foster Tapes, Detention Girls, Latina Sex Tapes, plus several others. (Speculative — created without HTML samples.)",
        "patterns": [
            r"teamskeet\.com",
            r"exxxtrasmall\.com",
            r"innocenthigh\.com",
            r"bffs\.com",
            r"fostertapes\.com",
            r"latinasextapes\.com",
            r"thisgirlsucks\.com",
            r"sislovesme\.com",
            r"daughterswap\.com",
            r"bigtitcreampie\.com",
            r"oyeloca\.com",
            r"poundedpetite\.com",
            r"shesnew\.com",
            r"teampilation\.com",
            r"tinyteengirls\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#email", "#username",
                    "input[name='email']", "input[name='username']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Sign In')",
                    "button:has-text('Login')",
                    "button:has-text('Log In')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                    "[class*='download']",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='/dl/']",
                    "a[download][href*='.mp4']",
                    "a:has-text('4K')",
                    "a:has-text('2160p')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "ultrafilms",
        "name": "UltraFilms",
        "description": "UltraFilms (ultrafilms.com) — premium 4K adult studio. ACTIVE in user's live queue (~777 URLs pending). (Speculative — selectors here are best-guess, send the download-button HTML for a real URL and the login form HTML to refine. Workers can still teach selectors via the manual takeover flow.)",
        "patterns": [
            r"ultrafilms\.com",
            r"ultrafilms\.net",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "#email", "#username", "#login",
                    "input[name='username']", "input[name='email']",
                    "input[name='login']",
                    "input[type='email']",
                ],
                "pass_field": [
                    "#password", "input[type='password']",
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button[type='submit']", "input[type='submit']",
                    "button:has-text('Login')",
                    "button:has-text('Sign in')",
                    "button:has-text('Log in')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                    ".download-button",
                    "[class*='download']:not([class*='count'])",
                ],
                "row_selectors": [
                    "a[href*='/download/']",
                    "a[href*='download.php']",
                    "a[download][href*='.mp4']",
                    # Common premium-4K-studio buttons
                    "a:has-text('4K')",
                    "a:has-text('2160p')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p"],
            },
        },
        "config_defaults": {
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
]
