<!-- verified-against: v3.66.682 -->
# PRE-STAGE GUIDE — how to keep waiting chats ready

You keep several chats pre-loaded with the heavy offline files, then drop in the new
version when a cut is ready. This is the supported pattern. Here's the clean split.

## What to PRE-STAGE (stable across releases — load these into any waiting chat)
The consolidated upload set. It does **not** change on a routine release:
- `pack_A.zip`, `pack_B.zip`, `pack_C.zip`, `pack_D.zip` — the `bd-install` packs.
  `bd-install` globs `pack_*.zip` and indexes the `bulkdl_*_kit.zip` inside each.
  Nothing exceeds `pack_C`'s cap (~517 MB). `pack_D` = `bulkdl_dev_kit`
  (pytest + pyinstaller wheelhouse). (The kit→pack distribution is arbitrary — do
  NOT assume "pack_B = chromium".)
- `pack_E.zip`–`pack_H.zip` — the **optional expansion tier** (see next section).
  Pre-stage them too, but they are **install-on-demand**: `bd-install` globs them
  and indexes their contents, but installs nothing automatically (no kit handler),
  so they add ~544 MB of upload but zero bootstrap cost until a task uses one.
- `bd_cloak_pack_v3_66_<n>.zip` — the `bd-venv` cloak consumable: pip-wheels
  (cloakbrowser + playwright + websockets + **authlib/joserfc**, added @682 for
  OIDC/SSO) + a stealth-Chromium tarball + Windows-fonts. **Not** globbed by
  `bd-install` (different naming scheme: `bd_*_pack_*.zip` are `bd-venv`
  consumables, `pack_*.zip` are `bd-install` packs). **Current: `v3_66_682`.**
  (The 539 pack predated authlib and forced a live fetch — see the guard below.)

## OPTIONAL expansion packs — pack_E–H (situational; install-on-demand)
Net-new capability beyond the base bootstrap. Named `pack_*.zip` for the upload
set, but each is a **flat single-capability pack** (not a multi-kit bundle like
A–D) and has **no `bd-install` handler**, so bd-install indexes them harmlessly
and installs nothing — you install manually per each pack's `README.md` when a
task needs it. Skip entirely for a routine cut.
- `pack_E.zip` (bd_browsers_pack, 306 MB) — playwright **firefox-1532 +
  webkit-2311** (base ships chromium only; revs match playwright 1.61.0 exactly)
  + capture fixtures. Extract `ms-playwright/*` into `$PLAYWRIGHT_BROWSERS_PATH`.
- `pack_F.zip` (bd_pyext_pack, 40 MB) — optional extractor/parser wheels
  (gallery-dl, streamlink, m3u8, mpegdash, lxml, pillow, pycryptodome, pyzbar,
  qrcode, trio, textual, memray). A-la-carte; install only what a feature needs.
- `pack_G.zip` (bd_netdeps_pack, 58 MB) — Ubuntu 24.04/amd64 system `.deb`s
  (wireguard-tools, nftables, dnsmasq, aria2, libzbar0, jq, sqlite3,
  jellyfin-server). Offline apt/dpkg. Pairs with pack_F's pyzbar (needs libzbar0).
- `pack_H.zip` (bd_audit_pack, 140 MB) — offline `bd-rev` audit toolchain
  (semgrep, bandit, vulture, radon, detect-secrets, libcst, hypothesis, coverage
  wheels + jscpd + a11y node stack + fd/shellcheck bins + GUI-audit scripts).
  Makes audit sessions egress-free.

DROPPED as redundant — do NOT re-upload these older files: `bulkdl_offline_deps`,
`bd_cloak_combined_offline_pack` (stale backend), a standalone `bulkdl_lsp_kit`
(already inside a pack), and the old standalone venv kit.

`install_bulkdl_kits.sh` and `setup.sh` live in project knowledge (static), so
they're present once you've re-added the project-files zip. The `bd-*` scripts now
ride the **version.zip `kit/` overlay** (self-persisting; they win over the static
copies), so re-adding the project zip + the next-session pack is what makes a
waiting chat "ready" without re-attaching machinery.

## What to ADD LAST (the only per-version files)
When the cut is ready, drop these into the waiting chat:
1. `BulkDownloader_v3_66_<n>.zip` — the source/release zip
2. `BulkDL_next_session_v3_66_<n+1>.zip` — the next-session pack (carries STATE.json
   **and** the `kit/` overlay)

Bootstrap then runs `bd-boot` (budgeted + checkpointed: re-run until it prints
READY; internally prestage → install → venv → preflight → state → status →
footguns → kbsync) and the offline set is picked up automatically — from the
`/home/claude` intake copies first, the evicting uploads mount second. Bootstrap
halts if no `KB_HANDOFF_v3_66_*.md` is present in uploads (or /home/claude).

## The one guard that makes pre-staging SAFE
A pre-staged offline set is valid **only while dependencies haven't changed.** If a
release bumps a Python or npm dependency, the stale offline set silently builds
against old deps (a known footgun). So before trusting a pre-staged chat with a new
source zip, confirm the new zip's fingerprints match the pinned ones in
`STATE.dep_fingerprints`:

```
# against the new source zip, compare to STATE.dep_fingerprints:
unzip -p BulkDownloader_v3_66_<n>.zip frontend/package-lock.json | sha256sum | cut -c1-16
unzip -p BulkDownloader_v3_66_<n>.zip requirements.txt          | sha256sum | cut -c1-16
unzip -p BulkDownloader_v3_66_<n>.zip requirements-cloak.txt    | sha256sum | cut -c1-16
```
- **All match** → the pre-staged offline set is valid; proceed.
- **package-lock differs** → refresh the wheels in the affected `pack_*` (and
  re-stage) before building; `bd-preflight` will also flag the node_modules/
  package-lock mismatch.
- **requirements.txt OR requirements-cloak differs** → refresh `bd_cloak_pack_*`.
  The cloak pack ships **core + cloak** wheels, so a change to *either* file can
  stale it — do not watch only `requirements-cloak.txt`. **Worked example (@681→682):**
  `authlib>=1.3,<2.0` was added to **requirements.txt** (core, for OIDC/SSO); the
  539 cloak pack had no authlib wheel, so an offline `bd-venv` failed with
  `No matching distribution found for authlib` and had to live-fetch it. The fix
  was `bd_cloak_pack_v3_66_682` (authlib + joserfc added, cryptography held at the
  packed 45.0.7 by the `<46` pin). Watch requirements.txt, not just the cloak file.

`bd-install`/`bd-preflight` enforce the package-lock match at bootstrap, and
`bd-venv` re-validates the cloak/venv layer, so a stale pre-stage fails loudly
rather than building wrong.

## Why fingerprints live in STATE.json, not here
Per `PROJECT_KNOWLEDGE_IS_STATIC.md`: this guide is version-agnostic machinery, so
it stays put. The actual shas are per-cut state, so they ride in
`STATE.dep_fingerprints` (the pack) and are refreshed each release.

## TL;DR
Waiting chat = the consolidated pack set (`pack_A`–`pack_D` + the cloak pack, plus
the optional `pack_E`–`pack_H` tier if you want capture/optional-deps/netdeps/audit
capability on hand) + the
re-added project-files zip. When the cut lands, add the source zip + the
next-session pack. If the dep fingerprints still match, you're good; if they don't,
refresh the one pack that changed.
