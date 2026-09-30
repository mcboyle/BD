"""site_templates._data_extractor_hosts -- built-in templates for 7 sites that pass through the
page-media / SPA extractors, not a DOM download control (ORDER-TEMPLATES-TO-REPO cut 2).

Every entry is derived from the site's last PASS in the fleet history (/api/history on the VM that
owns it, 2026-09-30): the URL in each description is that PASS URL verbatim and "path" is the
done-row message. None of those passes used a download selector, so none is recorded here, and
there is deliberately no learned.download block: the load-time gap-fill only applies a template
that carries one, so the sites already passing on the VMs keep their current config."""

ITEMS = [
{
    "id": "dailymotion",
    "name": "Dailymotion",
    "description": "Dailymotion (www.dailymotion.com) -- public video host, no login. Last PASS test2 2026-09-30T06:00:11Z: https://www.dailymotion.com/video/xbe8y8e?ref=t165_test2_run, path spa-api source=scene-stream tier=1280 (the player's own HLS master, fx-dailymotion-player).",
    "patterns": [
        r"dailymotion\.com",
    ],
    "learned": {},
},
{
    "id": "ok_ru",
    "name": "OK.ru video",
    "description": "OK.ru (ok.ru) -- public video host, no login. Last PASS spare12 2026-09-30T06:40:43Z: https://ok.ru/video/15848648215119, path spa-api source=okru-player tier=1920 (the player's data-options quality list).",
    "patterns": [
        r"(^|[/.])ok\.ru/video",
    ],
    "learned": {},
},
{
    "id": "fullporner",
    "name": "FullPorner",
    "description": "FullPorner (fullporner.com) -- public tube, no login; the scene player is a cross-origin embed iframe whose own <source> list is ranked (dl95-fullporner-1). Last PASS wrk-191 (results/wrk-191/SWEEP-T165.md): 6aba5725ee50424b5f304d89.mp4, from https://fullporner.com/watch/6aba5725ee50424b5f304d89 (download-95 B6-B probe).",
    "patterns": [
        r"fullporner\.com",
    ],
    "learned": {},
},
{
    "id": "porndoe",
    "name": "PornDoe",
    "description": "PornDoe (porndoe.com) -- public tube; guest tier tops out at 480p (1080p needs a premium account), so runs park in needs_review until approved. Last PASS test6 2026-09-30T06:04:21Z: https://porndoe.com/watch/pd0a1a9s7v1x, path spa-api source=page-media tier=480.",
    "patterns": [
        r"porndoe\.com",
    ],
    "learned": {},
},
{
    "id": "pussyspace",
    "name": "PussySpace",
    "description": "PussySpace (www.pussyspace.com) -- public tube, no login; HLS scenes (fx-pussyspace). Last PASS test6 2026-09-30T06:02:09Z: https://www.pussyspace.com/vid-6169108-fubuki-cosplay-full-video-one-punch-man-sweet-darling/, path spa-api source=page-media tier=1080.",
    "patterns": [
        r"pussyspace\.com",
    ],
    "learned": {},
},
{
    "id": "scrolller",
    "name": "Scrolller",
    "description": "Scrolller (scrolller.com) -- public media, no login needed; the Download control opens a Register/Log in modal (fx-scrolller-login-modal), the media comes from the page's JSON-LD. Video posts only. Last PASS test2 2026-09-30T06:00:18Z: https://scrolller.com/yeah-fuck-it-3uxzsyef64?ref=t165_test2_run, path spa-api source=jsonld-scene-video tier=1080.",
    "patterns": [
        r"scrolller\.com",
    ],
    "learned": {},
},
{
    "id": "hustlerunlimited",
    "name": "Hustler Unlimited",
    "description": "Hustler Unlimited (hustlerunlimited.com) -- members site, app-held login. Last PASS wrk-191 2026-09-30T06:16:05Z: https://hustlerunlimited.com/videos/milf-tutor-schooled-my-tool/, path spa-api source=page-media tier=1080 (HLS manifest, fx-hustler-mse-manifest-wait).",
    "patterns": [
        r"hustlerunlimited\.com",
    ],
    "learned": {},
},
]
