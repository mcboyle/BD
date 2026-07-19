# Setup — Fresh Machine

How to get a working install on a new machine after cloning this
repo. Geared at future-you on a new laptop, not strangers.

---

## Linux / macOS

```bash
git clone <your-private-repo-url> bulk-downloader
cd bulk-downloader

# Python venv (Python 3.9+)
python3 -m venv venv
source venv/bin/activate

# Dependencies
pip install -r requirements.txt
playwright install chromium

# Optional extras (web push, QR codes, TLS impersonation, etc.)
pip install -r requirements-optional.txt

# Configure (copy the example, edit to taste)
cp sites_config.example.json sites_config.json

# First run
python downloader_ui.py
```

Open <http://localhost:5555>.

---

## Windows

Two paths:

### Dev install (recommended for your own machine)

```cmd
git clone <your-private-repo-url> bulk-downloader
cd bulk-downloader
install_dev.bat
```

Installs to `%USERPROFILE%\BulkDownloader-dev`. Pulls in **every**
dependency — base + optional + dev — plus PyInstaller and pytest.
Always builds the standalone `.exe` (no prompt). Creates three
shortcuts: `launch.bat`, `run_tests.bat`, `preflight.bat`.

Use this when:

- You're setting up your own working machine
- You want all the library extractors available (some may fail to
  install if their PyPI package was pulled; the script reports
  which ones and proceeds)
- You want the test runner + preflight handy
- You actually need pyinstaller for `.exe` builds

This script is **DEV-ONLY**. It's not in the public-release zip and
its existence isn't documented in the user-facing README. The
extractor packages it installs include some adult-content site APIs;
keep this file out of any distribution channel you don't fully
control.

### User install (what you'd send a friend)

```cmd
git clone <your-private-repo-url> bulk-downloader
cd bulk-downloader
install_windows.bat
```

Installs to `%USERPROFILE%\BulkDownloader`. Pulls only base +
optional deps. Prompts before building the `.exe`. Friendlier
output, more skip paths. This is the path the public-style README
documents.

### Manual

```cmd
git clone <your-private-repo-url> bulk-downloader
cd bulk-downloader
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy sites_config.example.json sites_config.json
python downloader_ui.py
```

---

## First-boot checks

The selftest runs automatically on boot. Look for these lines in
stderr:

```
[selftest] ran 5 checks in 3.4ms
[selftest] ✓ database: WAL mode, N tables, integrity ok
[selftest] ✓ cookies_dir: ...
[selftest] ✓ sites_config: ...
[selftest] ✓ playwright: installed
[selftest] ✓ loopback: 127.0.0.1 reachable
[selftest] N ok · 0 warn · 0 fail
```

Any `warn` or `fail` is worth investigating before you start using it.

---

## Environment variables

These are all optional; defaults are sane.

| Variable | Default | What it does |
|---|---|---|
| `BD_HOST` | `127.0.0.1` | Bind address. Set to `0.0.0.0` for LAN access. |
| `BD_PORT` | `5555` | Listening port |
| `BD_INSTALL_DIR` | current working dir | Where DB, cookies, logs, locales live |
| `BD_AUTH_TOKEN` | (none) | Bearer token; if set, every API request needs `Authorization: Bearer <token>` |
| `BD_DISABLE_KEEPALIVE` | `0` | Set to `1` for testing — disables persistent-browser keepalive |
| `BD_BROWSER_BACKEND` | (auto) | `cloakbrowser` or `playwright`. Overrides the Settings value. Default: `cloakbrowser` when importable, else `playwright`. |

---

## Running tests

```bash
# Full suite (unit only)
python run_tests.py

# Single file
python run_tests.py tests/test_phases_195_199.py

# E2E (requires Playwright + chromium)
python -m unittest tests.test_e2e_smoke._RealE2ESmoke
```

Expected at v3.47.0: **3013 unit pass / 3 cleanly skipping / 0 failing**;
**6 E2E tests passing**.

---

## Updating from prior versions

When you pull updates:

```bash
git pull
pip install -r requirements.txt   # pick up any new deps
python preflight.py               # confirms migrations + sanity
python downloader_ui.py
```

The DB schema migrates itself on boot via `PRAGMA table_info`
introspection — no manual migration step. Your `sites_config.json`,
`cookies/`, `state/`, etc. are gitignored and stay put across pulls.

---

## Building a frozen Windows .exe

```cmd
venv\Scripts\activate
pip install pyinstaller
pyinstaller downloader.spec
```

Output lands in `dist/BulkDownloader/`. The `.spec` file is tuned
to bundle templates, locales, and the extension folder.

---

## Backups

Worth backing up periodically (these are gitignored, so git won't):

- `bulk_downloader.db` (or wherever your `BD_INSTALL_DIR` points)
- `cookies/`
- `sites_config.json`

Everything else is rebuilt from git on a fresh clone.

---

## Verifying what's actually active (System Status)

When the GUI's behaviour is unclear, the cockpit shows what's live without
launching anything. Open the cockpit and click **System Status** (under
System / Ops), or hit the endpoint directly:

```bash
curl -s localhost:5555/cockpit/api/status | python -m json.tool
```

It reports, read-only:

- **Browser backend** — the selected backend and whether CloakBrowser is
  importable (with its version, or the import error if not).
- **Local capture assets** — whether the vendored rrweb / snapdom bundles are
  present on disk (the bytes), i.e. whether offline DOM capture can run.
- **Manual-login session handoff** — per site, whether a `manual` profile
  exists and which runtime profiles (`main` / `w<N>` / `keepalive_<N>`)
  currently carry a session, plus any `.sync_backups`.
- **Keepalive** — the backend keepalive would use and the live keeper states.
- **Capture / template corpus** — the captures root and how many capture files
  exist (if the corpus is present).

Each block is independently guarded, so one missing subsystem can't blank the
panel — it shows an `error` for that block and the rest still resolve.

### Browser backend selection

Choose the backend in **Settings → `browser_backend`** or via the
`BD_BROWSER_BACKEND` env var (env wins). Values: `cloakbrowser` (default when
the package is importable) or `playwright`. A request for `cloakbrowser`
downgrades to `playwright` automatically when the package isn't installed.
`playwright` reproduces exact pre-v3.66.141 launch behaviour — use it as the
escape hatch if a CloakBrowser path misbehaves. Every flow logs its choice:

```
  [browser] runner_worker: cloakbrowser — ...
  [browser] keepalive: cloakbrowser — ...
```

### Manual-login session handoff

After a manual login completes, the freshly-logged-in session is copied from
`profiles/<site>/manual` into the runtime profiles (`main`, and `keepalive_0`
when the keeper is enabled) so downloads and keepalive reuse the login. Only
login-continuity storage is copied (cookies + web/session/IDB storage), the
leveldb `LOCK` is skipped, and the prior copy is moved into
`profiles/<site>/.sync_backups/<timestamp>/` first. The keeper can't relaunch
that profile mid-copy. Watch for:

```
  profile_sync[<site>]: main <- manual: copied Cookies, Local Storage, ...
  manual login: synced session into runtime profiles: main, keepalive_0
```

### Safer template generation (candidate filtering)

The site-template flow rejects obvious non-download links so it no longer
produces a bad `download.bin` from a homepage or nav link. A download row must
carry a real site signal (media extension / manifest URL / download path /
resolution label / known API pattern); generic `a[href]`/`[href]` selectors,
homepage links, nav/header/footer, search/settings/login/logout,
share/favorite/comment/vote, and unrelated external-service links are rejected.
URL-less quality/download menu buttons still survive as triggers. When nothing
carries a clean signal, the draft is flagged `review_required` rather than
silently picking a bad link, and the rejected candidates are surfaced.

### Multi-capture template generation

To compare several approved captures of one site instead of trusting one, POST
role-tagged captures to `build-multi-template`:

```bash
curl -s -X POST localhost:5555/cockpit/api/captures/build-multi-template \
  -H 'Content-Type: application/json' \
  -d '{"captures":[{"role":"download_menu","name":"a.json"},
                   {"role":"download_result","name":"b.json"}]}'
```

Roles: `login` / `player` / `quality_menu` / `download_menu` /
`download_result` (filenames are under `BD_CAPTURES_ROOT`; inline `capture`
dicts are also accepted). The review-required draft returns selector **support
counts**, **rejected** candidates, reusable **network patterns**, and
**resolution priority**.

---

## Offline install / bundle flow

The app runs fully offline once dependencies are present — no runtime CDN
fetches. DOM capture injects the **vendored** rrweb + snapdom bundles from
`bulk_downloader/vendor/` (there is no remote-CDN fallback by design; a missing
bundle fails capture rather than fetching). The release zip already contains
them.

To stand up an air-gapped box:

```bash
# On a networked machine, pre-download wheels + the browser:
pip download -r requirements.txt -d ./offline_wheels
pip download -r requirements-optional.txt -d ./offline_wheels   # optional extras
# (CloakBrowser is optional; if you want it offline, vendor its wheel too —
#  otherwise the app runs on Playwright.)

# Copy the repo + ./offline_wheels to the air-gapped box, then:
python3 -m venv venv && source venv/bin/activate
pip install --no-index --find-links ./offline_wheels -r requirements.txt
# Playwright's chromium must be staged too — set PLAYWRIGHT_BROWSERS_PATH to a
# directory you copied the browser into, or run `playwright install chromium`
# once on a networked machine with the same PLAYWRIGHT_BROWSERS_PATH and copy it.

cp sites_config.example.json sites_config.json
python downloader_ui.py
```

Verify the offline-critical pieces from System Status (or
`/cockpit/api/status`): **Local capture assets** should show rrweb + snapdom
present, and **Browser backend** should show the backend you expect (it falls
back to `playwright` if CloakBrowser isn't installed).
