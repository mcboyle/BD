# D3 React UI — opt-in retirement landed (U9 + D4)

As of **v3.65.1**, the `/m` mobile route is an unconditional 302 redirect
to `/m2/` (the React SPA). The legacy `mobile.html` template still ships
in the repo as emergency rollback; it is removed in v3.66.0.

This doc covers the original v3.64.0 opt-in mechanism (now historical)
and the v3.65.1 retirement.

## Current behaviour (v3.65.1+)

Visit `http://<your-host>:5555/m` — you'll land on the SPA via a 302.
The `?ui=v1` opt-out path is gone: the legacy UI is no longer reachable
via routing. If you need the legacy template back in an emergency, see
the rollback section below.

You can also bookmark `/m2` directly — same destination.

## Historical: how the opt-in worked (v3.64.0 – v3.65.0)

D3 originally shipped behind an opt-in cookie so early adopters could
try the new SPA without disrupting users who had `/m` bookmarked or
scripted.

## How it works (v3.65.1+)

```
GET /m            → 302 → /m2/  (unconditional)
GET /m/           → 302 → /m2/
GET /m?ui=v2      → 302 → /m2/  (no cookie set on /m — the cookie path
                                  retired with the opt-in branch)
GET /m?ui=v1      → 302 → /m2/  (no opt-out path any more)
GET /m/ops        → 302 → /m2/  (companion admin page also retires)

GET /m2           → serves SPA
GET /m2?ui=v2     → serves SPA + sets bd_ui=v2 cookie (back-compat —
                                  harmless preserve of user preference)
GET /m2?ui=v1     → serves SPA (the v1 opt-out branch was removed —
                                redirecting back to /m would loop)
```

How it worked before v3.65.1 (historical):

```
GET /m            → if bd_ui=v2 cookie: 302 → /m2/
                    otherwise:           serve mobile.html (legacy)

GET /m?ui=v2      → sets cookie + 302 → /m2/
GET /m?ui=v1      → sets cookie + serves mobile.html
GET /m2?ui=v1     → sets cookie + 302 → /m
GET /m2?ui=v2     → sets cookie + serves SPA
GET /m2           → serves SPA regardless of cookie
```

The cookie is named `bd_ui`. It carries no auth — `bd_session` is a
separate, security-sensitive cookie. `bd_ui` is purely a UI preference
and is now largely vestigial — the SPA is the only mobile UI.

## Why this opt-in scheme

D3 is a substantial UI change (5 tabs, React SPA, new design language).
Forcing the new UI on day one would break muscle memory for anyone who
already has `/m` bookmarked or scripted. The opt-in lets early adopters
try it without disruption, then we flip the default once it's been
real-world tested.

## Retirement schedule

**v3.64.0**: both UIs available, legacy `/m` was default. Opt into the
SPA via `?ui=v2`.

**v3.64.x**: SPA became more capable through D3 V1–V6 (visual redesign,
new dashboard endpoints, AddSiteWizard, DesktopShell, etc.).

**v3.65.0** (originally planned cutover): the visual redesign track
absorbed the release. The D4 retirement slipped one release. `/m`
still served the legacy default in v3.65.0.

**v3.65.1** (actual cutover — D4 landed): `/m` is now an unconditional
302 to `/m2/`. The `/m/ops` companion admin page also retires. The
legacy `mobile.html` and `m_ops.html` templates remain on disk as
emergency rollback.

**v3.66.0** (next milestone): `mobile.html` and `m_ops.html` templates
removed from disk. The `/m` and `/m/ops` routes stay as permanent
redirects to `/m2/` (so old bookmarks keep working forever). The
`_m2_opt_state` / `_m2_apply_opt_cookie` helpers may retire at the
same time, since the only remaining caller (the `?ui=v2` back-compat
path on `/m2`) is itself vestigial.

## Emergency rollback (v3.65.1)

If something is wrong with `/m2` that needs the legacy UI back
immediately, the `mobile.html` template is still in the repo at
`bulk_downloader/templates/mobile.html`. Restoring it as a working
route requires a code change — there is no runtime toggle. The
minimum-viable hotfix is to restore the pre-D4 `serve_mobile_view`
body that read `mobile.html` and served it as 200. The opt-in/opt-out
plumbing is more than is needed; a 5-line "always serve legacy"
restore is enough until `/m2` is fixed.

After v3.66.0 the template is removed from disk, so emergency rollback
will require git-archaeology to recover it. If you need a longer-lived
escape hatch, file an issue before v3.66.0 ships.
