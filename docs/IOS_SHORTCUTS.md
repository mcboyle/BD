# iOS Shortcuts for Bulk Downloader

Pre-built Shortcuts you can install on iPhone or iPad to control Bulk
Downloader without opening the web UI. Works with Siri voice commands.

The Bulk Downloader API exposes the same endpoints `bdctl` uses, so any
HTTP-aware client can talk to it. iOS Shortcuts has solid HTTP support
via "Get Contents of URL".

## Setup

1. Find your downloader's URL. Default is `http://<host>:5555`. From
   inside the LAN you can use the host IP; for external access put it
   behind Tailscale or a reverse proxy with auth.

2. If you've enabled global auth (Phase 26), get your token from the
   Bulk Downloader settings drawer. Add it to each shortcut as a header:
   `X-BD-Token: <your-token>`.

3. CSRF: the API exempts shortcuts when they send the token header.
   No CSRF dance needed for token-authenticated requests.

## Shortcut 1 — "Add to Bulk Downloader"

Replaces the manual "open the web UI, paste URL" flow. Use it via the
iOS Share sheet on any link.

### Setup
1. Open Shortcuts → "+" → name it "Add to Bulk Downloader"
2. Tap "Add Action" → search "Get Contents of URL"
3. Configure:
   - URL: `http://YOUR_HOST:5555/api/quick_add`
   - Method: `POST`
   - Headers: `Content-Type: application/json` (and `X-BD-Token` if auth enabled)
   - Request Body: JSON with one field — `url` set to the Shortcut input
4. Toggle "Show in Share Sheet" in shortcut details
5. Set "Receive: URLs"

### Use
Long-press any link in Safari, share sheet, "Add to Bulk Downloader".
Auto-routes to the matching site based on URL patterns.

## Shortcut 2 — "What's downloading?"

Spoken status summary. Run from Siri: "Hey Siri, what's downloading?"

### Setup
1. New shortcut named "What's downloading"
2. Action: "Get Contents of URL"
   - URL: `http://YOUR_HOST:5555/api/dashboard`
   - Method: `GET`
3. Action: "Get Dictionary Value"
   - Dictionary: previous result
   - Get value for: `totals`
4. Action: "Speak Text"
   - Text: build with magic variables, e.g.
     "Running [running count]. Queued [pending count]. [needs_review] need review."

### Use
"Hey Siri, what's downloading."

## Shortcut 3 — "Review needs"

Returns a count of URLs awaiting review. Pairs well with iOS Focus mode —
silence notifications except this one.

### Setup
1. New shortcut "Review needs"
2. GET `http://YOUR_HOST:5555/api/dashboard`
3. Get value `totals.needs_review`
4. If > 0: send notification, "X URLs need review"
5. Open URL: `http://YOUR_HOST:5555/` (deep-link to web UI)

### Use
Automate it to run every 4 hours during work hours. Shortcut → Automation
→ Time of Day.

## Shortcut 4 — "Start site / Stop site"

Voice-control individual sites. Useful when you want to pause downloads
during a video call.

### Setup
1. New shortcut "Stop downloads"
2. Action: "Choose from List" — pre-fill with your site IDs
3. Action: "Get Contents of URL"
   - URL: `http://YOUR_HOST:5555/api/sites/SELECTED/pause`
   - Method: `POST`
4. Confirmation: "Stopped"

For start, same shape with `/start` instead of `/pause`.

### Use
"Hey Siri, stop downloads" → pick site from list → done.

## Shortcut 5 — "Send clipboard URLs"

Bulk add from clipboard. Pair with the desktop clipboard sync apps
(Universal Clipboard, Pushbullet, etc.) for cross-device URL stashing.

### Setup
1. New shortcut "Send clipboard URLs"
2. Action: "Get Clipboard"
3. Action: "Get Contents of URL"
   - URL: `http://YOUR_HOST:5555/api/route_urls`
   - Method: `POST`
   - JSON body: `{ "text": <clipboard contents>, "ai_filter": true }`
4. Get value `summary` from response
5. "Show Result" — print the summary

The `ai_filter: true` parameter drops listing/category URLs before
routing (Phase 75). Helpful when you copy a chunk of links from a page.

## Tips

- **Authentication**: don't expose the API to the public internet without
  global auth turned on. Even with auth, prefer Tailscale or WireGuard
  over a public hostname.

- **Multiple devices**: copy a working shortcut via AirDrop to share with
  iPad or other iPhones. Settings travel.

- **Notifications**: when you POST to `/api/sites/<sid>/load_urls`, the
  push hook fires according to that site's `push_on_*` flags. If you've
  enabled mobile push pairing (Phase 24.7), the shortcut effectively
  becomes a webhook trigger for your other devices.

- **Folder action**: pair the "Add" shortcut with a Files folder watcher
  to ingest URL lists dropped into an iCloud folder. The Bulk Downloader
  already has a folder watcher (folder_watcher.py if enabled), but the
  Shortcuts route gives you per-list "send to specific site" control.

## Limitations

- No bidirectional websockets. Shortcuts polls; the web UI uses SSE.
  This means notifications via shortcuts have ~poll-interval latency.

- No file uploads from Shortcuts (cookies, learned profiles). Use the
  web UI for those one-shot operations.

- Siri parsing of site names is brittle for multi-word names. Numeric
  IDs work reliably; consider naming sites with short single words.
