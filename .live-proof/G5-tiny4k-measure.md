# G5 -- tiny4k.com SPA scene page: where the download actually is (2026-09-15, test2, DISPLAY :98)
Stepper: bd-live-step.py --site tiny4k --url https://tiny4k.com/members/video/enchanting-pixie --settle 8 (3 runs; PNGs ~/campaign/tiny4k/g5-{1,2,3}.png, outputs g5-*.out on test2)
Run 1 (resource/DOM enumeration): landed on the scene URL, title "Enchanting Pixie | Members Area", no login wall.
  DOM: 0 download controls, 0 h1, body text 609 chars (nav + ad strips only); window.__NUXT__ absent.
  Only <video> on the page is an ad offer clip (stan-uploads .../offer_video/...; no media extension) -- NOT the scene.
  104 resource entries; same-site API fetches: /api/sites, /api/members/releases/enchanting-pixie,
  .../actors/<slug>/related_releases, favorites|notifications|subscriptions?count=true, offers, /api/deals.
  The release request carries headers x-site: tiny4k.com, accept: application/json (plus normal UA/referer).
Run 2 (in-page fetch of the release JSON, same session): 200, 2678 bytes. Keys incl. title, streams,
  downloadOptions, galleryZipUrl, trailerUrl(""), videoId.
  streams = 3 direct signed mp4 URLs (cdn-videos.r1.cdn.pornpros.com/.../stream_mp4_{1080,480,720}.mp4?validfrom&validto&hash).
  downloadOptions = 4 entries {label:"DOWNLOAD HD", format:"mp4", quality:"1080"|"2160"|"480"|"720", filename:"tiny4k-enchanting-pixie-<q>.mp4"} -- NO url field.
Nuxt chunks (curl, no cookies): video page component DxNxk7ID.js -> ReleaseDownloadsModal in BTK4A7Jn.js:
  for each downloadOption it GETs /api/members/releases/<cachedSlug>/downloads?filename=<filename> and uses data.url.
  The Downloads button renders only when accessLevel is active/lifetime (ve computed) -- explains the empty DOM.
Run 3 (endpoint check): GET .../downloads?filename=tiny4k-enchanting-pixie-2160.mp4 WITH x-site -> 200 {"url": https://cdn-videos.r1.cdn.pornpros.com/.../download_mp4_2160.mp4?validfrom&validto(+~3h)&hash&download=1&filename=...}
  the same GET WITHOUT x-site -> 404 JSON; the release record without x-site -> 404. In-page HEAD on the CDN URL: CORS-blocked (expected; runner fetches server-side).
Conclusion: the download is reachable only via the SPA's API (record -> downloads?filename= -> signed CDN URL), never via the DOM.
Product change: ApiCapture remembers same-site /api/ JSON + replayable headers (x-*, accept); the runner consults it only when
find_best_download yields nothing; ranks 2160 download > 1080 stream; resolves the filename-only option through the page's own session.
Not verified live: an actual transfer through the patched product (not deployed; operator's call). No credentials or cookies recorded here.
