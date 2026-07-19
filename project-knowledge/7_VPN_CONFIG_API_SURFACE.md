<!-- verified-against: v3.66.185 -->
# #7 — `vpn_config` public API surface (from source)

Explains the **empty VPN summary endpoint** on the 160 build in one glance. From
`bulk_downloader/vpn_config.py`:

## Public functions (the entire surface)
```
get_tunnel_config(tunnel_id) -> Optional[dict]      # line ~200
update_tunnel_config(tunnel_id, **fields) -> Optional[dict]   # ~237
get_global_settings() -> dict                       # ~249
update_global_settings(**fields) -> dict            # ~254
```

## The key absence

There is **no `get_config()`**. The Settings Center `_vpn_summary` reads `vpn_config.get_config()` behind a
`hasattr` guard — so on this build the guard is False and the endpoint returns **empty**. This is **safe and by
design** (no leak), not a bug. It lights up only once `get_config()` is added (a Horizon-2 item).

```
grep -nE 'def (get_|update_)' bulk_downloader/vpn_config.py   # confirm the surface
grep -c 'def get_config\b' bulk_downloader/vpn_config.py      # expect 0 on 160
```

## Bounded-write fact

`update_global_settings` is **bounded server-side**; the *read* path `_load()` merges arbitrary on-disk keys,
which is why `_vpn_summary` defensively filters `global_settings` to `_DEFAULT_GLOBAL_SETTINGS`
(`leak_test_interval_s`, `kill_switch_auto_recover`, `system_killswitch_default`,
`system_killswitch_allow_ports`, `enable_per_site_tunnels`, `max_concurrent_tunnels`).
