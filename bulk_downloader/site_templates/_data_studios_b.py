"""site_templates._data_studios_b -- TEMPLATES slice [54:62] verbatim from templates.py @v447 (8 elements), plus the four templates the PM-handoff 2026-09-06 template gap report added at the END of the slice (africancasting, pegasproductions, pornpros_tiny4k, reptyle_teamskeet). Do not reformat the original eight; their element literals are copied byte-for-byte."""

ITEMS = [
{
        "id": "nubiles_network",
        "name": "Nubiles Network (Nubile, Nubile.net, NubileFilms, NubilePorn)",
        "description": "VERIFIED 2026-09-15 (row 722): three brands completed the login->download cycle on this template -- nubiles: login https://members.nubiles.net/login ('Please sign in' form, no captcha), lands same-origin after the 'CONTINUE TO MEMBERS AREA' interstitial (cleared by the runtime), listing /video/gallery, scenes /video/watch/<id>/<slug>, generic Download rows, picked the 3840 tier ('..._full.mp4', 2.5 GB); nubilefilms: login https://members.nubilefilms.com/login (username field input[name='username'], submit button[type=submit]), same interstitial, picked 3840x2160 (2.7 GB); stepsiblingscaught: see the dedicated `stepsiblingscaught` entry (login host behind a Cloudflare 'I am human' page, members host members.nubiles-porn.com). Nubiles Inc operator family. Covers Nubiles, Nubile Films, Nubile Porn, MomsTeachSex, MomsLickTeens, MomsBangTeens, Step Siblings Caught, and related brands. Download flow VERIFIED 2026-09-06 (PM-handoff template gap report): the scene page's Download button reveals rows whose `span.dimensions` carry DIRECT signed hrefs on the content2a/content4 CDNs (st/e signed, cookie-fetchable, not IP-bound). No login_url default: each brand has its own members host.",
        "patterns": [
            r"nubile\.com",
            r"nubile\.net",
            r"nubiles\.net",
            r"nubilefilms\.com",
            r"nubileporn\.com",
            r"nubilesporn\.com",
            # PM-handoff 2026-09-06: the members host is members.nubiles-porn.com
            # -- HYPHENATED. The unhyphenated pattern above matched nothing there.
            r"nubiles-porn\.com",
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
                    # PM-handoff 2026-09-06, VERIFIED: the tier rows are
                    # span.dimensions carrying the direct signed .mp4?st= href.
                    "span.dimensions",
                    "a[href*='.mp4?st=']",
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
            # Row 722 (2026-09-15): verified tier ladder on nubiles/nubilefilms/stepsiblingscaught.
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "nookies",
        "name": "Nookies",
        "description": "VERIFIED 2026-09-15 (row 722): login https://nookies.com/login ('I AGREE' age gate cleared by the runtime; uid/pwd form, submitted via Tab+Enter), lands the /membersarea/gateway partner-deals page whose forward control is 'ACCESS NOOKIES' -> https://nookies.com/membersarea (declared via success_url; /video/<id> URLs are TOUR pages even with a session, members scenes are /membersarea/video/<id>); a promo modal is closed via button[aria-label=\"Close\"]; the scene's bare 'Download' button reveals a hidden a[href*='/membersarea/video/stream/<id>'] which 302s to the session-protected high.mp4 (the '4k' TAG link is a filter, not the download); picked the full-quality stream (2.9 GB, saved as high.mp4). Nookies (nookies.com). VERIFIED 2026-09-06 (PM-handoff template gap report): the login form posts /auth.form with uid/pwd field names; the scene page's #downloadTrigger opens #downloadModal, whose 'Full quality video' row points at /membersarea/video/stream/<id> and 302s to the session-cookie-protected /protected/content/<studio>/<slug>/high.mp4. GOTCHA: an #inboxModal appears on scene load and intercepts clicks; the grid preview.mp4 links are previews, not the download.",
        "patterns": [
            r"nookies\.com",
        ],
        "learned": {
            "login": {
                # PM-handoff 2026-09-06, VERIFIED against the live form: the
                # field names are uid/pwd, which none of the previous guesses
                # (#email / #username / name='username') could ever match.
                "user_field": [
                    "input[name='uid']",
                ],
                "pass_field": [
                    "input[name='pwd']",
                ],
                "submit_btn": [
                    "button:has-text('LOGIN')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "#downloadTrigger",
                ],
                "row_selectors": [
                    "#downloadModal a:has-text('Full quality video')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["Full quality video"],
            },
        },
        "config_defaults": {
            # Row 722 verified nookies config (:5555, 2026-09-15).
            "login_url": "https://nookies.com/login",
            "success_url": "https://nookies.com/membersarea",
            "dismiss_selectors": "button[aria-label=\"Close\"]",
            "trigger_selector": "button:has-text(\"Download\")",
            "dl_selector": "a[href*=\"/membersarea/video/stream/\"]",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "new_sensations",
        "name": "New Sensations",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.newsensations.com/members/ ('Members Login' form, username field input[placeholder*='user' i], submit button[type=submit], no captcha), lands the same-origin /members/offers.php cross-sell whose safe forward control is 'TAKE ME TO MY MEMBERSHIP' (dismiss_selectors; the runtime re-requests the original URL afterwards); scenes are /members/gallery.php?id=<id>&type=vids; the scene's button.ex-iconbtn--download opens #exDownloadMenu.exp-menu-floating with JS-only .exp-menu-item rows 2160p/1080p/720p/360p (no href -- the tiers are time-signed nsnetworkmembers mp4 URLs in the player config, which the runtime's API/media fallback reads); picked 2160p (2.9 GB, '..._2160_NS.mp4').",
        "patterns": [
            r"newsensations\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    # Row 722 (2026-09-15): the measured username field first.
                    "input[placeholder*='user' i]",
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
                    "button.ex-iconbtn--download",
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    # Row 722: the floating menu rows have no href (empty
                    # attribute = click-and-capture / API fallback).
                    "#exDownloadMenu .exp-menu-item",
                    "a[href*='/download/']",
                    "a[download][href*='.mp4']",
                    "a.download-link",
                    "a:has-text('4K')",
                    "a:has-text('1080p')",
                    "a:has-text('720p')",
                ],
                "url_attribute": ["", "href", "href", "href", "href", "href", "href"],
                "tier_labels_seen": ["2160p", "1080p", "720p", "360p"],
            },
        },
        "config_defaults": {
            # Row 722 verified newsensations config (:5555, 2026-09-15).
            "login_url": "https://www.newsensations.com/members/",
            "dismiss_selectors": "a:has-text(\"TAKE ME TO MY MEMBERSHIP\")",
            "trigger_selector": "button.ex-iconbtn--download",
            "dl_selector": "#exDownloadMenu .exp-menu-item:has-text('2160p')",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "bang_originals",
        "name": "Bang.com (BangOriginals)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.bang.com/login (the form-scoped selectors below are REQUIRED: the generic submit hit 'Login with Google'), success https://www.bang.com/; scene download verified on :5555. Bang.com / BangOriginals network. Distinct from the Bang Bros network (which has its own template). Network includes Bang Originals, Bang Glamkore, Bang Trickery, Bang Real MILFs, and related brands. VERIFIED 2026-09-06 (PM-handoff template gap report): the submit control MUST be scoped to the login_check form -- a bare button:has-text('LOGIN') also matches 'LOGIN WITH GOOGLE' and sends the worker to Google OAuth. The Download button exposes direct per-tier <a> links on bngcdn.com whose token self-authorizes (no cookies needed).",
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
                    # PM-handoff 2026-09-06: form-scoped ON PURPOSE. A bare
                    # button:has-text('LOGIN') matches 'LOGIN WITH GOOGLE'.
                    "form[action*=login_check] button[type=submit]",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "a[href*='bngcdn.com']",
                    "a[href*='cd=attachment']",
                    "a:has-text('2160p')", "a:has-text('1080p')",
                    "a:has-text('720p')", "a:has-text('540p')",
                    "a:has-text('480p')",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["2160p", "1080p", "720p", "540p", "480p"],
            },
        },
        "config_defaults": {
            "login_url": "https://www.bang.com/login",
            "success_url": "https://www.bang.com/",
            "user_field": "form[action*=login_check] input[name=_username]",
            "pass_field": "form[action*=login_check] input[name=_password]",
            "submit_btn": "form[action*=login_check] button[type=submit]",
            "quality_preference": "2160,1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        "id": "teen_mega_world",
        "name": "Teen Mega World",
        "description": "VERIFIED 2026-09-15 (row 722): login https://members.teenmegaworld.net/authentication.php (the 'Member Login' form lives on the members host; teenmegaworld.net/login is the homepage with an age gate; username field input[name='username'], submit button[type=submit], no captcha); lands same-origin on members.teenmegaworld.net (success_url deliberately empty: members.teenmegaworld.net alone also matched the authentication.php form page); scenes are /scenes/*_vids.html; the generic Download walk picked the 3840x2160 tier (2.3 GB, 'Beauty-Angels_..._3840x2160.mp4'). Teen Mega World (teenmegaworld.net/.com) — Russian-operated network with ~20 sub-brands. Network sites typically share the same member-area template.",
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
                    # Row 722 (2026-09-15): the measured username field first.
                    "input[name='username']",
                    "#username", "#email", "#login",
                    "input[name='login']",
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
            # Row 722 verified teenmegaworld config (:5555, 2026-09-15).
            "login_url": "https://members.teenmegaworld.net/authentication.php",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
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
        "description": "VERIFIED 2026-09-15 (row 722): login https://ultrafilms.com/login ('MEMBER LOGIN' form, E-Mail / Password / GET INSIDE, no captcha, no interstitial; submitted via the submit selector click), lands same-origin /members/home (no success_url needed); items are /members/content/item/<uuid>-<slug> (photo sets are zips -- pick Movie items); the generic Download walk picked the 7680x4320 tier (6.5 GB, 'insane-desire_..._7680x4320.mp4'). UltraFilms (ultrafilms.com) — premium 4K adult studio.",
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
            # Row 722 verified ultrafilms config (:5555, 2026-09-15).
            "login_url": "https://ultrafilms.com/login",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
{
        # PM-handoff 2026-09-06 template gap report, section B1. Every selector
        # below was read off the live authenticated pages during that session;
        # the login field names are the whole point of the entry -- the generic
        # username/password/type=password matchers MISS the ahd_ prefix.
        "id": "africancasting",
        "name": "African Casting (Torx/MojoHost 'ahd' CMS + Fluid Player)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://members.africancasting.com/login lands on https://members.africancasting.com/paysites/ (a ROOT success_url refuses every same-origin member landing, so the member path is the success_url); scene download verified on :5555. Earlier 2026-09-06: custom ahd_ login field names; the scene page is a Fluid Player whose tiers are <source> elements rather than click-download links, so there is no trigger to press. Token URLs are served from an mjedge.net edge, are NOT IP-bound, and expire about an hour after the page loads.",
        "patterns": [
            r"africancasting\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "input[name='ahd_username']",
                ],
                "pass_field": [
                    "input[name='ahd_password']",
                ],
                "submit_btn": [
                    "button:has-text('Sign In')",
                ],
            },
            "download": {
                # Fluid Player: no click-download; the tiers ARE the <source>
                # elements, so trigger_selectors is deliberately empty.
                "trigger_selectors": [],
                "row_selectors": [
                    "video source",
                    "source[title]",
                ],
                "url_attribute": "src",
                "tier_labels_seen": ["2160p", "1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "login_url": "https://members.africancasting.com/login",
            "success_url": "https://members.africancasting.com/paysites/",
            "quality_preference": "2160,1080,720,480",
            "min_resolution": 480,
            "use_curl_cffi": True,
        },
    },
{
        # PM-handoff 2026-09-06 template gap report, section B2. The login
        # template already existed (host www.pegasproductions.com); this adds
        # the download half. NOTE: no selectors JSON was archived for this site,
        # so the download row_selectors below are the report's values and are
        # the least-confirmed entry in this cut -- a teach pass should pin them.
        "id": "pegasproductions",
        "name": "Pegas Productions (Quebec, French UI)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://pegasproductions.com/login/ (POST form nom_util_vod/pass_vod) lands on www.pegasproductions.com/nouveautes-2 (the segment-exact success_url); scene pages /<slug>-1/ carry direct wizkey-dl.com MP4 links (1080p/720p/320p); 1.61 GB 1080p file verified on :5555. Earlier 2026-09-06: French login field names (nom_util_vod / pass_vod); maximum quality is 1080p, there is no 4K tier; the signed mp4 href is cookie-fetchable. GOTCHAS: the remember-me control input[name='infos'] is PRE-CHECKED in the markup, so verify it rather than toggling it, and input[name='login'] / input[name='usern'] are decoys that must NOT be filled. A VIP4K ad popup appears after login and is dismissed by its CLOSE text.",
        "patterns": [
            r"pegasproductions\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "input[name='nom_util_vod']",
                ],
                "pass_field": [
                    "input[name='pass_vod']",
                ],
                "submit_btn": [
                    "input[type='submit'].bouton-connexion2",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "a:has-text('1080')",
                    "a:has-text('DOWNLOAD')",
                ],
                "row_selectors": [
                    "a[href*='.mp4']",
                    "a[href*='download']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["1080P HD"],
            },
        },
        "config_defaults": {
            "login_url": "https://pegasproductions.com/login/",
            "success_url": "https://www.pegasproductions.com/nouveautes-2",
            "user_field": "input[name=nom_util_vod]",
            "pass_field": "input[name=pass_vod]",
            "submit_btn": "form:has(input[name=pass_vod]) [type=submit]",
            "quality_preference": "1080,720",
            "min_resolution": 720,
            "use_curl_cffi": True,
        },
    },
{
        # PM-handoff 2026-09-06 template gap report, section B4. tiny4k was
        # previously matched by wowgirls_network AND vip4k_family, which are a
        # different operator entirely; both patterns were removed in the same
        # cut so this template is the only match for the host.
        "id": "pornpros_tiny4k",
        "name": "PornPros / Fame Digital (Tiny4K, Exotic4K)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://tiny4k.com/login ('Member login' form, username field input[placeholder*='user' i], submit button[type=submit]; the site warns that rapid repeated logins block the account -- one attempt), lands same-origin with member CDN cookies (CloudFront-Policy/Signature/Key-Pair-Id) after the 'No thanks, continue' coin-modal interstitial; scenes are /members/video/<slug> (Nuxt SPA: cards are JS click divs, no <a>, and no download control renders headless) -- the runtime's API/media path reads /api/members/releases/<slug> downloadOptions and picked 2160p (5.9 GB, 'tiny4k-<slug>-2160.mp4'). tiny4k.com and exotic4k.com -- a Vue SPA, so it needs real Chrome. VERIFIED 2026-09-06: the member scene page carries clear 'DOWNLOAD HD' tier buttons whose direct mp4 links are served from the PornPros CDN with a self-authorizing token good for roughly three hours; use the download_mp4_* variants, NOT stream_mp4_*. NOT WowGirls -- do not apply vip4k_family here. The coin modal on load concerns bonus channels only and is dismissed with its 'No Thanks' text.",
        "patterns": [
            r"tiny4k\.com",
            r"exotic4k\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "input[placeholder*='user' i]",
                    "input.app-input__field[type='text']",
                ],
                "pass_field": [
                    "input[type='password']",
                ],
                "submit_btn": [
                    "button[type='submit']",
                    "button:has-text('Log')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "text=Downloads",
                ],
                "row_selectors": [
                    "a[href*='download_mp4_']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["DOWNLOAD HD 2160", "1080", "720", "480"],
            },
        },
        "config_defaults": {
            # Row 722 verified tiny4k config (:5555, 2026-09-15).
            "login_url": "https://tiny4k.com/login",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            "use_real_chrome": True,
            "use_persistent_profile": True,
        },
    },
{
        # PM-handoff 2026-09-06 template gap report, section B5. The report's
        # draft claimed teamskeet.com as well; that host is already served by
        # `teamskeet_network` and a second claim would return two ids for it,
        # so this entry claims only the Reptyle app hosts. See DONE.md.
        "id": "reptyle_teamskeet",
        "name": "Reptyle (new TeamSkeet app: MYLF/FamilyStrokes/FreeUse/PervMom/Swappz)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://auth.reptyle.com/oauth/login?referer=spa (form action login-user, email/password; the 'Sign in with Google' and magic-link buttons are NOT the submit; Turnstile is invisible and the site accepted the submit) lands on https://app.reptyle.com/; movie page app.reptyle.com/movies/<id>; 1.91 GB 2160 MP4 verified on :5555. Earlier 2026-09-06: playback is Cloudflare Stream and is NOT the download. The download control is a down-arrow ICON in the action toolbar which opens a 'SELECT DOWNLOAD QUALITY' modal (Standard 720p / High 1080p / Ultra 2160p); choosing a tier fires a BROWSER DOWNLOAD EVENT to a CacheFly URL rather than exposing a re-fetchable <a href>. url_attribute is deliberately empty for that reason: the runner must take the URL from the download event (page.expect_download), not from an attribute. Login carries an in-form Cloudflare Turnstile.",
        "patterns": [
            r"reptyle\.com",
        ],
        "learned": {
            "login": {
                "user_field": [
                    "input[name='email']",
                ],
                "pass_field": [
                    "input[name='password']",
                ],
                "submit_btn": [
                    "button:has-text('Login')",
                ],
            },
            "download": {
                "trigger_selectors": [
                    "[aria-label*='download' i]",
                ],
                "row_selectors": [
                    "button:has-text('Ultra')",
                    "button:has-text('High')",
                    "button:has-text('Standard')",
                ],
                # EMPTY ON PURPOSE: the tier click fires a browser download
                # event (CacheFly); there is no attribute to read.
                "url_attribute": "",
                "tier_labels_seen": ["Ultra", "High", "Standard"],
            },
        },
        "config_defaults": {
            "login_url": "https://auth.reptyle.com/oauth/login?referer=spa",
            "success_url": "https://app.reptyle.com/",
            "user_field": "form[action*=login-user] input[name=email]",
            "pass_field": "form[action*=login-user] input[name=password]",
            "submit_btn": "form[action*=login-user] button[type=submit]",
            "quality_preference": "2160,1080,720",
            "min_resolution": 1080,
            "use_real_chrome": True,
            "use_persistent_profile": True,
        },
    },
{
        # Row 722 (2026-09-15): dedicated Step Siblings Caught entry. The host
        # is also listed under `nubiles_network` (same operator, same download
        # rows); this entry carries the verified brand-specific login config.
        "id": "stepsiblingscaught",
        "name": "Step Siblings Caught (Nubiles brand)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://stepsiblingscaught.com/login (302s to /turnstile/challenge?r=/login, a full-page Cloudflare 'Security Check' with a custom <div role=button aria-label=\"Verify you are human\"> 'I am human' control that the runtime clicks before the form renders; members.stepsiblingscaught.com does not exist); the submit hops to https://members.nubiles-porn.com/ -- a different brand host, admitted only because success_url declares that origin; the shared Nubiles members host lists every network brand (scenes /video/watch/<id>/<slug>, listing pages such as /video/toprated/ are not scenes); Download rows are the Nubiles span.dimensions direct signed hrefs; picked the 3840 tier (4.8 GB, 'myfamilypies_..._3840.mp4').",
        "patterns": [
            r"stepsiblingscaught\.com",
        ],
        "learned": {
            "download": {
                "trigger_selectors": [
                    "button:has-text('Download')",
                    "a:has-text('Download')",
                ],
                "row_selectors": [
                    "span.dimensions",
                    "a[href*='.mp4?st=']",
                ],
                "url_attribute": "href",
                "tier_labels_seen": ["4K", "2160p", "1080p", "720p", "480p"],
            },
        },
        "config_defaults": {
            "login_url": "https://stepsiblingscaught.com/login",
            "success_url": "https://members.nubiles-porn.com/",
            "quality_preference": "4320,3160,2880,2160,1440,1080,720",
            "min_resolution": 1080,
            "use_curl_cffi": True,
            "use_persistent_profile": True,
        },
    },
    {
        "id": "dorcelclub",
        "name": "Dorcel Club",
        "description": "VERIFIED 2026-09-15 (row 722): login on the shared account host https://www.account-dorcel.com/login?s=dorcelclub&lang=en&url=https%3A%2F%2Fwww.dorcelclub.com%2Fen%2F&tp=dark lands on https://www.dorcelclub.com/en/ (cross-brand landing); scene page: a.btn-dl opens a qualities panel whose rows are href-less div.filter[data-quality] (the file URL is read from the row's own attributes); 1080p MP4 verified on :5555. The page text 'Explore forbidden sexual desires!' must never read as a rate limit.",
        "patterns": [
            r"dorcelclub\.com",
            r"account-dorcel\.com",
        ],
        "learned": {
            "login": {
                "user_field": ["form[action*='/login'] input[name='username']"],
                "pass_field": ["form[action*='/login'] input[name='password']"],
                "submit_btn": ["form[action*='/login'] button[type='submit']"],
            },
            "download": {
                "row_selectors": ["div.qualities div.filter[data-quality]"],
                "url_attribute": "data-slug",
                "trigger_selectors": ["a.btn-dl"],
                "tier_labels_seen": ["2160", "1080", "720", "480"],
            },
        },
        "config_defaults": {
            "login_url": "https://www.account-dorcel.com/login?s=dorcelclub&lang=en&url=https%3A%2F%2Fwww.dorcelclub.com%2Fen%2F&tp=dark",
            "success_url": "https://www.dorcelclub.com/en/",
            "user_field": "form[action*=/login] input[name=username]",
            "pass_field": "form[action*=/login] input[name=password]",
            "submit_btn": "form[action*=/login] button[type=submit]",
            "trigger_selector": "a.btn-dl",
            "dl_selector": "div.qualities div.filter[data-quality]",
            "quality_preference": "1080,720",
        },
    },
    {
        "id": "xempire",
        "name": "XEmpire (Gamma)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://www.xempire.com/en/login (Gamma form username/password, single input[type=submit]) lands on https://members.xempire.com/en (the members host, not www); scene /en/video/<studio>/<slug>/<id>: the 'Download' button opens the Gamma quality menu (a.VideoJSPlayer-DownloadOption-Link 288p..2160p, href /movieaction/download/<id>/<q>/mp4); 697 MB 1080p file verified on :5555. Dedicated entry so the shared gamma_kosmos template (which also covers sites that never completed) stays unstamped.",
        "patterns": [
            r"xempire\.com",
        ],
        "learned": {
            "login": {
                "user_field": ["input[name='username']"],
                "pass_field": ["input[name='password']"],
                "submit_btn": ["form[action*='/login'] input[type='submit']"],
            },
            "download": {
                "row_selectors": ["a.VideoJSPlayer-DownloadOption-Link[href*='/movieaction/download/']"],
                "url_attribute": "href",
                "trigger_selectors": ["button.ScenePlayerHeaderPlus-IconItem-Button"],
                "tier_labels_seen": ["4K 2160p", "Full HD 1080p", "HD 720p", "Web HD 576p", "High 432p", "Small 288p"],
            },
        },
        "config_defaults": {
            "login_url": "https://www.xempire.com/en/login",
            "success_url": "https://members.xempire.com/en",
            "user_field": "input[name=username]",
            "pass_field": "input[name=password]",
            "submit_btn": "form[action*=/login] input[type=submit]",
            "trigger_selector": "button.ScenePlayerHeaderPlus-IconItem-Button:has-text(\"Download\")",
            "dl_selector": "a.VideoJSPlayer-DownloadOption-Link",
            "quality_preference": "1080,720",
            "dismiss_selectors_login": "a.SkipPageButton-ButtonLink, a:has-text('No Thanks. Continue'), a:has-text('Continue to Members Area')",
        },
    },
    {
        "id": "pornone",
        "name": "PornOne (ex vPorn)",
        "description": "VERIFIED 2026-09-15 (row 722): login https://pornone.com/login/ (username/password, JS requestSubmit) lands on https://pornone.com/ (root; the home page also shows a 'log in' affordance in the footer, so the root success_url is what proves the session); scene /<category>/<slug>/<id>/: direct a.block download anchors on s<NNN>.pornone.com/newdl/... (720p 244 MB, 1080p 1.64 GB) -- dl_selector is REQUIRED because the unscoped ranker picked a duration element labelled 4K; 1080p file verified on :5555.",
        "patterns": [
            r"pornone\.com",
        ],
        "learned": {
            "login": {
                "user_field": ["form input[name='username']"],
                "pass_field": ["form input[name='password']"],
                "submit_btn": ["form:has(input[name='password']) button"],
            },
            "download": {
                "row_selectors": ["a[href*='/newdl/']"],
                "url_attribute": "href",
                "trigger_selectors": [],
                "tier_labels_seen": ["1920x1080_4000k", "720x406_500k"],
            },
        },
        "config_defaults": {
            "login_url": "https://pornone.com/login/",
            "success_url": "https://pornone.com/",
            "user_field": "form input[name=username]",
            "pass_field": "form input[name=password]",
            "submit_btn": "form:has(input[name=password]) button",
            "dl_selector": "a[href*=\"/newdl/\"]",
            "quality_preference": "1080,720",
        },
    },
]
