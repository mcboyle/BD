<!-- verified-against: v3.66.539 -->
# Sandbox capability layer — definitive (durable, version-agnostic)

*Read this before doing OPV / harness / render work. It records what the sandbox
CAN do (proven), how to turn it on, and the footguns already paid for. Companion
to the live `bd-tools --bin toolchain/bin` inventory and command help, plus
`TESTING_ETHICS_FRAME` (the
harness ethics). The specific check count drifts; the capabilities don't.*

---

## 0. One command to restore it all
After a green `bd-boot`:
```
bd-sbcap            # provision the whole advanced layer (idempotent)
bd-sbcap --check    # verify without changing anything
DISPLAY=:99 bd-opv  # run the operator-verification suite
```
`bd-sbcap` rides the version.zip `kit/` overlay, so it self-persists. Everything
below is what it sets up.

---

## 1. What the sandbox can actually do (proven, supersedes the old "offline/no-browser" premise)
1. **Full outbound egress** — pip/npm/GitHub/CDN all reachable. Deps + repos pull live.
2. **Headless AND headed Chromium** — headless for corpus/recognizer; headed on Xvfb
   `:99` renders the REAL cockpit (screenshot, interact, axe, OCR).
3. **The real Flask app in-process** — `bulk_downloader.app` runs on a live port via
   `app.run()` in a background thread; a headed browser drives the live SPA.
4. **Network namespaces + nftables + wireguard-tools** — netns create/exec, veth pairs,
   nft default-drop egress ALL work. This is what proves the VPN egress-fails-closed
   killswitch in-sandbox (OPV-VPNKILL) — previously fully operator-gated.
5. **OCR (tesseract)** — verify the cockpit paints real text to pixels.
6. **Prometheus parsing (prometheus_client)** — validate /metrics is real exposition.
7. **SMTP/webpush/QR mocks** — aiosmtpd sink, pywebpush VAPID, pyzbar QR decode.

## 2. Still genuinely operator-gated (do NOT pretend these are sandboxable)
- A **real phone** (PWA share-target). A **real challenge** acceptance (we DETECT and
  hand off; we never solve). A **real VPN tunnel/provider handshake** (netns proves the
  iface-scoped egress POLICY, not a live handshake). A **literal week-long** soak. A
  **live element-pick** on a forwarded display. Real accumulated **multi-site failure
  history** for the worst-site correlation.

## 3. The layer, piece by piece (what bd-sbcap installs)
- **Display stack:** Xvfb `:99` + fluxbox + x11vnc(5900) + websockify/noVNC(6080).
  Headed browser renders the cockpit; a human can watch via noVNC if :6080 is forwarded.
- **netns tooling:** iproute2 (`ip`), nftables (`nft`), wireguard-tools (`wg`), tcpdump.
- **OCR/DB/JSON:** tesseract-ocr, sqlite3, jq.
- **pip (into BOTH venv and `/tmp/prestaged_site_packages`):** aiosmtpd, pywebpush,
  pyzbar (+libzbar0), freezegun, locust, memray, qrcode, pillow, prometheus-client,
  pytesseract.
- **repos/assets:** noVNC (`/opt/novnc`), axe-core 4.10.2 (`~/.sbcap/axe/axe.min.js`).

**Provenance (don't be misled):** these deps come from **`bd-sbcap`**, NOT `bd-venv`/the
bootstrap. A fresh sandbox has NONE of freezegun/aiosmtpd/pyzbar/prometheus_client/`ip`
until `bd-sbcap` (or the offline pack) runs. Older STATE/handoff wording said "freezegun
installed into the venv" — misleading; it's an sbcap dep. Also: **apt needs `apt-get
update` first** (a stale index can 404 on iproute2), and `bd-sbcap` pins **cryptography**
to the cloak-stack version during the venv install so pywebpush can't bump it and risk
`resolve_backend()`/cloakbrowser (that mutation is a known footgun — if you ever need
pywebpush's newer crypto, use a throwaway venv, not the service venv).

**Offline path:** `bd_sbcap_offline_pack_v3_66_539.zip` provisions this whole layer with
zero network (wheels + .debs + noVNC + axe + render_check files + a cryptography pin +
`install_sbcap_offline.sh --check`). Use it instead of live `bd-sbcap` when determinism/
offline matters; it applies the same G1/G2/G3 fixes.

## 4. The footgun encyclopedia (paid for; don't rediscover)
| Symptom | Cause | Fix |
|---|---|---|
| script vanished between calls (even under ~/.sbcap) | sandbox FS evicts some paths per call | **write + run in the SAME bash_tool call**; `bd python3 <script>` runs synchronously |
| call returns -1, server gone next call | shell-`&` server reaped at call end; setsid/nohup fight `bd`'s `exec` | **app-in-thread in ONE process**, or `bd-render`/`capture_gui` (own the lifecycle via `subprocess.Popen(start_new_session=True)`) |
| dep imports under `bd python3` but not in `bd-opv` | venv-only pip is invisible to the `bd` env (uses prestaged path) | **FIXED in bd-sbcap @539 (G1):** it now installs prometheus_client + pyzbar (+PIL/qrcode) into `/tmp/prestaged_site_packages` too, not just the venv. (If you hit it with an older bd-sbcap: install to the prestaged path too.) |
| headed/headless launch: "Executable doesn't exist at chromium-NNNN" | `playwright install` bumped the venv's pinned chromium rev past the cached build | **FIXED in bd-sbcap @539 (G3):** it symlinks the wanted build dir to the installed one (`chromium-1228 -> chromium-1223`; recent builds are layout-compatible). The check pins `PLAYWRIGHT_BROWSERS_PATH=~/.cache/ms-playwright` so it targets the right cache. (Older bd-sbcap: symlink manually, OR finish the download.) |
| OPV-RENDER fails with an empty error (exit 1) | render_check_sb.py was never staged to `~/.sbcap/` → it fell back to the wrong copy | **FIXED in bd-sbcap @539 (G2):** it now stages render_check_sb.py (+render_check.py/spa_serve.py) to `~/.sbcap/` from the toolkit render/ dir or /mnt/project. |
| render_check wants a build that isn't there even after symlink | the **prestaged path ships a NEWER playwright** that shadows the venv's | run render_check with `PYTHONPATH=<work tree only>` (drop prestaged), under the venv python |
| bd-opv browser checks SKIP under a bare shell | system python3 carries a different playwright than the venv | bd-opv **re-execs once under the venv python** when the ambient build is missing — works bare or under `bd` |
| `mkdir -p a/{x,y}` made a literal `{x,y}` dir | `bash_tool` is **dash** (no brace expansion) | explicit dirs, or `bd bash -c '...'` |
| netns nft load "syntax error unexpected '}'" | one-line ruleset with `; }` | multi-line ruleset: newline (not `;`) before `accept`/`counter`/closing braces |
| :5599 returns 000 right after boot | spa_serve does FTS/migrations (~3 s) before bind | poll up to ~12 s |
| `/metrics` shot blank | it's Prometheus **text** | `curl`/parse it, don't screenshot |
| OCR/A11Y sparse render when run after many browser checks | sequential `sync_playwright` contexts + shared display contention | treat a too-sparse render as SKIP (transient), not FAIL; each check passes standalone |
| uploads vanish mid-session | `/mnt/user-data/uploads` is read-only AND evicts | copy uploaded files to `$BD_ARTIFACT_ROOT` immediately |

## 5. OPV check families (what's proven in-sandbox)
The runner is `bd-opv`; each check drives REAL BD code with synthetic/mock inputs (never
stubs the logic under test). Families:
- **API/security floor:** HEALTH, METRICS, CSRF, F4.3 (token scope), FLOOR (credential
  redaction floor), VPNKILL (egress fail-closed).
- **Admission/relogin/drift (pure):** F1.1 (retry-window), F1.3 (cookie-expiry), F1.4
  (predictive relogin), F2.6 (selector eval), F3.2 (drift→review-only draft), F3.3
  (synthetic canary), B2 (template draft-override invariants).
- **Display/visual:** A11Y (axe WCAG), OCR (tesseract render proof), RENDER (render_check
  computed-layout squish gate), PICK (headless element-pick).
- **Pipelines/mocks:** NOTIFY (SMTP round-trip), PUSH (VAPID), QR (pairing-QR),
  RECOG (live recognizer), FIXTURE (synthetic challenge/download site + live vendor pages),
  SOAK (leak + time-warp), BASE (baseline JSON).
- **Only gated check in the runner:** F4.1 (real phone).

Run: `bd-opv` (all), `bd-opv --only OPV-X` (one), `bd-opv --list` (registry).
The live PASS/GATED counts live in `STATE.json opv_session`, never hard-coded in docs.

## 6. Ethics (harness work)
All harness/OPV work follows `TESTING_ETHICS_FRAME` + `SANCTIONED_TEST_URLS`:
detect→handoff never solve, no solving service, official vendor test pages only, no DRM
defeat, redaction-on, public/non-adult/purpose-built sources, SSRF/egress floors hold.
OPV-FIXTURE (synthetic mounts) and OPV-VPNKILL (no real tunnel/creds/external egress) are
both compliant by construction.
