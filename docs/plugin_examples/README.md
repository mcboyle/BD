# BulkDownloader plugin examples

Native, first-party example plugins demonstrating every extension kind. They
live here in `docs/plugin_examples/` and are **not auto-loaded** — copy the ones
you want into `INSTALL_DIR/plugins/` and hit **Reload** on the Maintenance page
(or restart). Every example is **self-gating**: with nothing configured it does
nothing, so it is safe to drop in unconfigured.

Validate any plugin before deploying it:

```
python3 tools/plugin_lint.py docs/plugin_examples/media_server_refresh.py
python3 tools/plugin_lint.py docs/plugin_examples/      # lint the whole dir
```

## The five extension kinds

| Kind | Decorator | Signature | Fires |
|------|-----------|-----------|-------|
| Extractor | `@extractor("site")` | `fn(url, context) -> dict` | library-extractor fast path |
| Hook | `@hook("event")` | `fn(payload)` | `download.{done,failed,needs_review}` |
| Processor | `@processor(priority=…)` | `fn(payload) -> dict\|None` | after `download.done` |
| Config provider | `@config_provider(…)` | `fn(site_id, cfg) -> dict` | at browser launch |
| Lifecycle (gated) | `@lifecycle("event")` | `fn(context, page, site_id)` | launch/capture, **full-access only** |

Processors and config providers run in **priority order** (lower first).

## The examples

- **media_server_refresh.py** (processor) — scan Plex / Jellyfin / Stash on
  every finished download. The headline media-workflow plugin. Env:
  `PLEX_URL/PLEX_TOKEN/PLEX_SECTION`, `JELLYFIN_URL/JELLYFIN_TOKEN`, `STASH_URL`.
- **move_to_nas.py** (processor, priority 50) — copy/move the finished file to a
  NAS/archive dir before the library refresh runs. Env: `NAS_DEST`, `NAS_MODE`,
  `NAS_PER_SITE`.
- **nfo_sidecar.py** (processor) — write a Kodi/Jellyfin `.nfo` sidecar beside
  the file. Env: `NFO_SIDECAR=1`.
- **notify_events.py** (hook) — publish events to MQTT (Home Assistant) and/or a
  webhook (ntfy/Discord/Slack). Env: `MQTT_HOST/MQTT_PORT/MQTT_TOPIC`,
  `EVENT_WEBHOOK`.
- **per_site_proxy.py** (config provider) — route specific sites through your
  own proxy/VPN at launch. Edit `SITE_PROXIES` or set `SITE_PROXIES_JSON`.
- **vault_cookies.py** (lifecycle, **gated**) — inject your own exported cookies
  into the live context. Requires `allow_full_access`. Env: `COOKIE_VAULT`.

## Enabling + ordering (`plugins.json`)

Drop a `plugins.json` next to your plugins to control which load and in what
order (see `plugins.json.example`). With no `plugins.json`, every non-`_`
`*.py` loads alphabetically.

```json
{ "enabled": ["move_to_nas.py", "media_server_refresh.py"],
  "allow_full_access": false }
```

## Manifest + API version

Each plugin declares a `PLUGIN` dict with `api_version` (currently **2**) and
`capabilities`. A plugin whose `api_version` does not match the running BD is
**skipped** at load (surfaced on the status page), so a stale plugin can't break
silently when an event payload changes.

## Full-access — read before enabling

Lifecycle plugins (`vault_cookies.py`) get the **live browser context/page** and
raw launch kwargs — the same access BD has, **no sandbox**. They are **off by
default** and load only when you set `allow_full_access`. The full disclaimer
appears on the status page when the gate is on and in `plugins.py`. In short:
plugins run with full access; what you do with it is your responsibility and
must stay within each site's ToS, the law, and BD's capture charter (no
access-control bypass, no DRM, no challenge-solving). BD ships no such plugins.

## Robustness

Every callback is error-isolated; a callback that fails 5 times is
**quarantined** (no longer invoked) and shown on the status page until cleared.
Processors and lifecycle hooks support a per-registration `timeout` so a hang
can't stall a download.
