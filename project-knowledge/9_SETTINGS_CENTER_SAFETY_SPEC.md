<!-- verified-against: v3.66.185 -->
# #9 — Settings Center safety spec (expected invariants)

Use as the **expected** spec so an audit is confirmation, not discovery. All verified from the 160 zip.
`bulk_downloader/app_settings_center.py` is an **additive, read/edit-only** blueprint (GUI Phase 3, Slices 1-5).

## Routes — 11 total, exactly one non-GET
```
GET  /cockpit/settings
GET  /cockpit/settings/secrets
GET  /cockpit/settings/site/<sid>
GET  /api/settings/schema
GET  /api/settings/site/<sid>/effective
GET  /api/settings/site/<sid>/editable
GET  /api/settings/global/effective        # masked via _mask_secrets
GET  /api/settings/vpn/summary             # read-only; filtered (see #7)
GET  /api/settings/env/effective
GET  /api/settings/secrets/health
POST /api/settings/site/<sid>/validate     # the ONLY non-GET
```

## Safety invariants
- **No direct persistence** in the blueprint — only a comment references `_save_sites_config()`. (Writes go
  through the existing `api_update` PUT path, which is now range-backstopped — see the PUT below.)
- **Secret classifier:** `_SECRET_RE = (password|token|api_key|secret)`. `is_secret("cookie") = False`;
  `is_secret("cookie_file") = True`. **9 secret fields.** `cookie_max_age_hours` is **non-secret / general /
  gui-safe / editable**.
- **No secret value ever leaves a read endpoint** — presence-only. Sweep all 7 read endpoints with a sentinel
  secret value; expect zero leaks. `global/effective` is masked via `_mask_secrets`.
- **Slice-5 editable JSON has no `label` / `help` keys** (only `description`, from Slice-4).

## PUT numeric-range backstop (in `app.py::api_update`, via `site_editor.validate_numeric_updates`)
- Out-of-range **int or number → 400, not persisted** (e.g. `max_concurrent=9999` rejected, stays 4;
  `wait=200` rejected, stays 5).
- Valid PUT persists.
- Secret + sticky (`username`, `login_url`) **preserve-on-blank**.
- `api_update` **fail-opens** to prior behavior if the `site_editor` import fails.

## Quick re-verify
```
bd bash -c 'cd <extracted> && python3 - <<PY
from flask import Flask
from bulk_downloader import app_settings_center as sc
a=Flask(__name__); sc.register_routes(a)
rs=[(str(x.rule), ",".join(sorted((x.methods or set())-{"HEAD","OPTIONS"}))) for x in a.url_map.iter_rules() if x.endpoint!="static"]
print(len(rs), "routes; non-GET:", [r for r,m in rs if m!="GET"])
print(sc._SECRET_RE.pattern, sc._is_secret("cookie"), sc._is_secret("cookie_file"))
PY'
```
