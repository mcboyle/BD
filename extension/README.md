# Bulk Downloader — Browser Extension

A minimal Chrome/Firefox MV3 extension to send URLs to your Bulk Downloader
instance with one click.

## Install (Chrome / Edge / Brave)

1. Open `chrome://extensions/`
2. Toggle **Developer mode** on (top right)
3. Click **Load unpacked**
4. Select this `extension/` folder
5. Click the extension's icon → **Options** → set your server URL

## Install (Firefox)

Firefox MV3 extensions need either signing or `about:debugging`:

1. Open `about:debugging`
2. **This Firefox** → **Load Temporary Add-on**
3. Select `manifest.json`
4. The extension stays loaded until Firefox restarts. For permanent
   install, sign via Mozilla Add-ons or use the Firefox Developer Edition.

## Features

- **Toolbar button**: send the current tab's URL
- **Right-click on link**: send that link
- **Right-click on page**: send the page URL, or scrape all video-looking
  links on the page and send them as a batch (uses Phase 75 AI-filter)

## Configuration

Open the extension options:
- **Server URL** — your Bulk Downloader root, e.g. `http://10.0.70.181:5555`
  or `http://tailscale-name:5555`
- **Auth Token** — only needed when global auth is enabled in Bulk Downloader.
  Sent as the `X-BD-Token` header.

## Privacy

The extension only talks to the server URL you configure. No telemetry,
no external services. The icon8/48/128 PNG files are placeholders —
replace them with whatever you want.

## Icons

You can drop in any PNG files named `icon16.png`, `icon48.png`, `icon128.png`.
Or use a single SVG and convert with ImageMagick:

    convert -background none -resize 16x16 yours.svg icon16.png
    convert -background none -resize 48x48 yours.svg icon48.png
    convert -background none -resize 128x128 yours.svg icon128.png
