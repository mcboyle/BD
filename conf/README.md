# webproxy kit -- caddy + nginx for local proxy testing

## Caddy

Single static binary. Run with:
```bash
/home/claude/webproxy_kit/tools_bin/caddy run \
    --config /home/claude/webproxy_kit/conf/Caddyfile
```

Default conf proxies localhost:8080 -> localhost:5555 (BulkDL).

## Nginx

Lifted from the Ubuntu .deb. Will need a config (no default shipped).
Useful only if you specifically need nginx behavior vs caddy.

## Honest caveats
- Both proxies need PORT 8080 free.
- Caddy's auto-SSL won't work in the sandbox (no public DNS).
