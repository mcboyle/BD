# SANDBOX SPEC AND FILE PLACEMENT — measured @ v3.66.805

<!-- verified-against: v3.66.805 (bdsuite rev-810) -->

**Provenance.** Every figure below was obtained by RUNNING a command in this
sandbox on 2026-07-19, after a full bootstrap (base + expansion E/F/G/G2/H +
test-tools + verification kit). Nothing here is quoted from `SANDBOX.md`,
`SANDBOX_CAPABILITY_LAYER.md`, `PRESTAGE_GUIDE.md`, the next-session prompt, or
memory. Where a figure disagrees with an existing document, the disagreement is
called out in §11 rather than silently reconciled.

**Scope limit, stated up front.** This describes ONE sandbox instance at one
moment. Sizes, free space, and PIDs are volatile by construction. The stable
parts are the *layout*, the *env contract* (§6), and the *lifecycle* (§7). Treat
the numbers as a snapshot and the structure as durable.

---

## 1 | Host

| Property | Measured value | Instrument |
| --- | --- | --- |
| OS | Ubuntu 24.04.4 LTS | `/etc/os-release` |
| Kernel | Linux 6.18.5 x86_64 | `uname -srm` |
| CPU | 1 core, Intel Xeon @ 2.10GHz | `nproc`, `/proc/cpuinfo` |
| RAM | 3.9 GiB total, 3.7 GiB available | `free -h` |
| Root disk | 252 GB device, **but see §3** | `df -h /` |
| User | `root` (uid 0) | `id -u` |
| `/bin/sh` | **dash** (not bash) | `ls -l /bin/sh` |
| Python | 3.12.3 (system and `work/venv`) | `python3 -V` |
| Node | **22.22.2**, npm 10.9.7 | `node -v` |

**One core.** This is the single most under-appreciated spec. Every "run it in
parallel to save time" instinct is wrong here — `bd-parband --jobs N` buys
almost nothing on 1 CPU, and the full `tsc + vitest + vite` chain is serial-bound.
It is also why the retired suite builder took ~9-11 minutes and why the documented
"run the three FE steps independently under `timeout 600`" workaround exists.

**Capabilities.** The bounding set includes `cap_net_admin`, `cap_sys_admin`,
`cap_net_raw`, `cap_bpf`. `/dev/net/tun` exists. This is what makes the netns /
nftables egress proofs possible. It does NOT imply a WireGuard kernel module —
that is still absent, and veth stands in.

---

## 2 | Mounts

```
/dev/vda        /              ext4-ish, rw     <- everything real lives here
/dev/vdc        /mnt/skills/public     squashfs ro
/dev/vdd        /mnt/skills/examples   squashfs ro
rclone fuse     /mnt/user-data/uploads      ro
rclone fuse     /mnt/user-data/outputs      rw   <- the ONLY path the operator sees
rclone fuse     /mnt/user-data/tool_results ro
rclone fuse     /mnt/transcripts            ro
```

**`/mnt/project` is NOT in this list.** That is the single most important
placement fact in this document — see §9.

`/mnt/user-data/uploads` is a read-only rclone FUSE mount. It did **not** evict
during this session. A local copy deleted to save disk is therefore always
recoverable from the original, which is what makes the "drop consumed packs"
rule safe.

---

## 3 | Disk budget — the real constraint

The device is 252 GB but the **usable allowance is far smaller**. Measured
progression this session:

| Moment | Used | Avail |
| --- | --- | --- |
| Session start, nothing extracted | 8.6 G | **10 G** |
| After prestage (22 kits) | 13 G | 5.8 G |
| After packs E/F/H | 15 G | 4.0 G |
| After pack G `--apt` | 16 G | 3.5 G |
| After test-tools + verification kit | 16 G | 3.2 G |
| Now (idle) | 16 G | **2.7 G** |

A full bootstrap consumes roughly **7.3 GB** and leaves under 3 GB of headroom.
That is enough for read-only analysis and targeted bands. It is **thin for a
source cut**, which additionally needs the FE build chain plus a ~20 MB release
zip plus its extraction.

**Reclaimable right now (~330 MB), verified by `du`:**

- `/tmp/bdmut_e6lhacan` — 162 M (stale `bd-mutation-test` scratch)
- `/tmp/bdmut_as83ctcl` — 162 M (ditto)
- `/tmp/bdchk` — 2.5 M (my own bdsuite comparison dir)

Larger reclaim, if a cut needs room: `/tmp/bd_cloak_pack_extracted` (279 M) is
dead weight once `bd-venv` has run, and `/home/claude/audit_tools` (748 M) is
only needed for `bd-rev` audit work.

**Rule.** `df -h /` before extracting anything large. A full disk produces a
*silently truncated* `unzip` tree, not an error — the 808 incident where 231 of
251 files landed with no failure surfaced.

---

## 4 | `/home/claude` — top level

Sizes measured with `du -sh`.

| Path | Size | Role |
| --- | --- | --- |
| `work/` | 852 M | **the source tree** (§5) |
| `audit_tools/` | 748 M | pack_H offline audit venv; `rev` symlinks here |
| `netdeps_kit/` | 349 M | pack_G staged `.debs` (29, incl. 9 from G2) |
| `testtools_pack/` | 288 M | raw test-tools pack (extractable, then consumable) |
| `pypy_kit/` | 267 M | alternate interpreter |
| `fe_test/` | 136 M | node a11y stack: axe, lighthouse, pa11y |
| `rsuite_kit/` | 133 M | R toolchain |
| `testtools/` | 108 M | installed test-tool binaries (`bin/`) |
| `webproxy_kit/` | 47 M | Caddy |
| `pyext_kit/` | 40 M | pack_F wheelhouse (a-la-carte, NOT auto-installed) |
| `precommit_kit/` | 22 M | |
| `datastores_kit/` | 20 M | psql and friends |
| `audit_carry/` | 15 M | **unpacked `audit_state_v3_66_805.zip`** |
| `lsp_kit/` | 11 M | |
| `netdeps_kit_g2/` | 3.2 M | pack_G2, absorbed by G at install |
| `bin/` | 2.5 M | **the bdsuite toolchain** (§8) |
| `nextsess/` | 1.2 M | **unpacked version pack** (§10) |
| `browsers_fixtures/` | 1008 K | pack_E capture fixtures |
| `verification/` | 936 K | verification kit |
| `supervisord_kit/`, `recordings_kit/` | ~330 K ea | |
| `bd_home/` | 196 K | **runtime `BD_HOME`**: app_config.json, history db, screenshots |
| `mocks_kit/`, `apprise_kit/` | ~24 K ea | mock service scripts |

Files at top level worth knowing:

- `bdenv.sh` — the env contract, sourced by `bd` (§6)
- `install_bdsuite.sh` — carries a 1980 timestamp; it came from the PK bundle
- `CHANGELOG.md` — **the bdsuite changelog**, not the app's. This is how you
  identify the toolchain rev by CONTENT rather than by zip filename.
- `BulkDownloader_v3_66_805.zip`, `BulkDL_next_session_v3_66_805.zip`,
  `BulkDownloader_project_files_3_66_805.zip`, `audit_state_v3_66_805.zip`
  — kept, per the never-drop rule
- `rev -> /home/claude/audit_tools/venv` (symlink)

---

## 5 | `/home/claude/work` — the source tree

Refreshed from the release zip by `bd-boot`; verified byte-for-byte against it
(`2620 tracked files`, sha OK, 7 guards match).

| Subtree | Size | Measured denominator |
| --- | --- | --- |
| `venv/` | 378 M | the **service venv** (Python 3.12.3) |
| `frontend/` | 358 M | React/TS SPA + node_modules |
| `tests/` | 97 M | **1073 test files** |
| `bulk_downloader/` | 8.9 M | **561 `.py` modules** |
| `tools/` | 3.8 M | **216 `.py` tools** |
| `reports/`, `docs/`, `kb/`, `spa/`, `live_tests/` | smaller | |

**The "216 vs 249" trap, concretely.** `work/tools/*.py` = **216**.
The bdsuite toolchain = **249 tools** in **252 files** under `/home/claude/bin`.
These are different populations sharing the word "tools". `bd-factcheck` counts
the former; STATE prose usually means the latter. This is a denominator mismatch,
not rot (register §3.5), and it is worth disambiguating in prose rather than
"fixing" either number.

Derived artifacts that must be regenerated in a specific order live at the work
root: `DEPENDENCY_GRAPH.json`, `FUNCTION_INDEX.md`, `ROUTE_INDEX.json`,
`PIN_INDEX.json`, `ENDPOINT_CATALOG.md`, and `build_info.json`. OpenAPI is now
generated dynamically at `/api/openapi.json`; transient exports use
`tools/build_openapi.py --out PATH` and are not checked in.

---

## 6 | The `bd` environment contract

`bd` is a **bash** wrapper (`#!/bin/bash`) that sources `bdenv.sh` and execs.
Three forms: `bd <cmd>`, `bd -c '<snippet>'`, `bd` (interactive subshell).

Measured env inside `bd`:

```
PYTHONPATH=/tmp/prestaged_site_packages:
DISPLAY=:99
PATH=/tmp/tools_bin:/tmp/media/tools_bin:/home/claude/.local/node/bin:
     /home/claude/.local/bin:/home/claude/bin:/home/claude/.npm-global/bin:...
```

Why each matters:

- **`PYTHONPATH=/tmp/prestaged_site_packages`** (272 M) is where flask/pytest and
  the rest actually resolve from. This is the origin of the documented false
  positive: `venv/bin/python -c "import pytest"` succeeds *under `bd`* even if the
  venv itself has no pytest. To interrogate the venv's own site-packages you must
  use `env -u PYTHONPATH venv/bin/python …` — which in turn strips the work tree
  from `sys.path` and breaks anything needing it. Both halves of that trade-off
  are real; pick deliberately.
- **`/tmp/media/tools_bin` precedes system paths** and holds the static
  ffmpeg/ffprobe. System ffmpeg is 6.1.1 at `/usr/bin/ffmpeg`; the static build
  segfaults on HLS+HTTPS in-sandbox, which is why `bd-venv` shadows it via PATH.
- **`/tmp/tools_bin`** holds `fd`, `jq`, `rg`, `shellcheck`, `sqlite3`.
- **`DISPLAY=:99`** is required by the GTK module-import gate.

`bash_tool` itself is **dash**, gets a **fresh shell per call**, and inherits
none of this. Every call must re-export PATH, and any bash-ism (arrays, brace
expansion, process substitution) must be wrapped in `bd bash -c '…'`.

---

## 7 | Service lifecycle — measured, and it is not what boot reports

`bd-boot` reported these as OK with PIDs: Xvfb :99 (3575), apprise (3685, :8765),
mock_plex (3700, :32400), mock_jellyfin (3701, :8096), mock_stash (3702, :9999).

**Every one of those PIDs is now gone.** Verified by `/proc/<pid>` existence
check, not by `pgrep`. A later `ss -lntp` showed only ports 2024/2025
(`process_api`, the harness itself).

**Background processes do not survive between tool calls.** A service confirmed
running in one call cannot be assumed running in the next.

**Recovery is automatic but not instant.** `bdenv.sh` respawns anything not
already running, so `bd <anything>` restores them. But they need **~5 seconds to
bind**: an `ss` run immediately inside the same `bd -c` returned **0** listeners;
the same command after `sleep 6` returned **4** listeners plus Xvfb. A test that
touches a mock service and fails instantly may be a race, not a regression.

**`pgrep -f` matched its own wrapper during this very check** — the documented
footgun, reproduced live. Never read `pgrep -f "<cmd>"` as "still running";
check `/proc/<pid>` or a written exit marker.

---

## 8 | Toolchain placement

- **`/home/claude/bin`** — 252 files, 251 installed by `install_bdsuite.sh`,
  **249 counted as tools** (`bd-selfcheck`: *all 249 structurally sound*; the
  spread is `bdtools_*.py` helper libs plus a `__pycache__` dir generated by use).
- **`/usr/local/bin`** — 251 symlinks pointing back at `bin/` (0 failed).
- The same tools are **mirrored into the static PK**; `bd-pk-mirror` exists to
  keep those honest. Mirroring is by design (`PROJECT_KNOWLEDGE_IS_STATIC.md`
  calls PK "machinery"), not duplication to purge.
- `install_bdsuite.sh` **exits 1** on a harmless self-`cp` of `bdenv.sh`. Expected;
  not a failure.

**Identify the toolchain by content, never by filename.** This session's zip was
named `bdsuite_v3_66_805.zip` and its `CHANGELOG.md` header reads
`bdsuite rev-810`. Filenames lag; changelog headers do not.

Test tooling installs to `/home/claude/testtools/bin` (+ `/usr/local/bin`
symlinks): `ffuf`, `gitleaks`, `mp4dump`, `mp4fragment`, `mp4info`, `nuclei`
(v3.3.0 confirmed), `oha`, `websocat`.

---

## 9 | `/mnt/project` — materialized, not mounted

**`/mnt/project` did not exist at session start.** It is absent from `mount`
output and sits on `/dev/vda` — i.e. it is an ordinary directory I created and
populated from `BulkDownloader_project_files_3_66_805.zip`.

Consequences, all of which matter:

1. **The project-files upload is load-bearing.** Without it `bd-install` halts at
   "install_bulkdl_kits.sh not found", because that script is PK content.
2. **It is writable.** Nothing enforces read-only. Re-verify before trusting canon
   and again before close.
3. **Every file carries a `Jan 1 1980` timestamp.** That is rev-810's normalized
   stager (mtime is not part of the manifest, which hashes CONTENT), so 1980
   timestamps are a *correctness* signal here, not corruption.

Measured contents: **365 entries, 8 JSON files.** The manifest tracks **364** —
`STATIC_KB_MANIFEST.json` cannot appear in its own manifest, and that off-by-one
is expected rather than drift.

Canon state, re-verified after all installs:

```
bd-kb-sync verify /mnt/project  -> exit 0, INTEGRITY OK
bd-kb-sync check  /mnt/project  -> exit 0, IN-SYNC
canon pin: b6b65208be30 (content pin)
```

The pin's anchor is genuinely external: `_default_state()` reaches *into*
`BulkDL_next_session_v3_66_805.zip` and reads `STATE.json`, which independently
records `content_sha256 = b6b65208be30, files: 364`. Worth having checked, because
no `STATE.json` existed on disk at that point — a self-referential pin would have
been the classic "gate that cannot see" shape.

**Name-set diff against the PK as pasted in context: 365 vs 365, both directions
empty.** Contents were not compared — context supplies paths, not bytes — so this
establishes the file *set* only.

---

## 10 | `/home/claude/nextsess` — the version pack

Unpacked from `BulkDL_next_session_v3_66_805.zip`. `bd-boot` does **not** extract
it; tools reach into the zip directly. Key members:

- `STATE.json` — 66 KB; guard SHAs, ratchet values, `static_kb_manifest_pin`
- `KB_HANDOFF_v3_66_805.md` — 51 KB; **exactly one** is allowed, named for the
  BUILT APP version. Toolchain revs prepend a section *inside* it.
- `OPEN_ITEMS_REGISTER_v3_66_805.md`, `Backlog.md`, `Roadmap.md`,
  `PK_CLEANUP_MANIFEST.json`, consolidated tracker/roadmap/deferred docs

---

## 11 | Divergences from documented expectations

Recorded rather than reconciled. Each is measured.

| Document says | Measured here | Read |
| --- | --- | --- |
| bdsuite rev-809, 249 tools | **rev-810, 251 files / 249 tools** | zip filename lags content by two revs |
| canon `bfb2b02e6ca5`, 372→364 files | **`b6b65208be30`, 364 files** | later generation; version pack agrees independently |
| sandbox node **v20.18.0** (register, MOD-8) | **v22.22.2** | see below |
| `/mnt/project` mounted read-only | **absent, then materialized rw** | halt-guard-4 scenario, confirmed |
| pack_G2 a separate optional target | **absorbed by G** (9 of 29 debs) | the historical installer registered only E-H |
| coupling `0.3967889` → 0.397 | `bd-ratchet` prints **0.398** | unresolved; see below |

**Node.** MOD-8 was closed as mis-scoped on the reasoning that
`engines.node >= 18.0.0` is accurate because stash runs 18.19.1 and the sandbox
runs 20.18.0. The sandbox half of that pair is now **22.22.2**. The conclusion is
unchanged — 22 still satisfies `>= 18`, and the rule "do not pin ahead of stash"
still holds — but the *evidence* has moved, and a sandbox two majors ahead of the
box is a wider gap than the one that was reasoned about.

**Ratchet baseline.** `bd-boot` found **no** baseline and seeded one *cold from
live values* (`/root/.bd_metrics_baseline.json`: coupling ≤ 0.398,
defect_DP_total ≤ 2279, spa_wired ≥ 442, unwired_operator_endpoints ≤ 223).
So `bd-ratchet` exit 0 means the tree matches a number derived from that same
tree minutes earlier. It will catch drift introduced *from here*; it cannot tell
you whether anything regressed between 805 and now. With decomposition #12 sitting
at **zero headroom**, a display-rounding artifact and a real regression are
currently indistinguishable — resolve the 0.397/0.398 question before extracting.

---

## 12 | What this document does NOT establish

- **Content equality between the pasted PK and the on-disk bundle.** Names only.
- **That any of this matches stash.** Nothing here was measured on
  `mboyle@10.0.70.20`. Test counts, node version, service behaviour, and disk are
  sandbox facts exclusively.
- **Whether any bdsuite tool has ever been *run*.** `bd-consumer-graph
  --dead-tools` reports 249 tools / used 162 / listed-only 87 / **orphan 0**, and
  says plainly it is not a usage record. No invocation log has ever existed.
- **Golden/mutation baselines.** They do not survive a session boundary.
- **Long-run stability.** Sizes and free space were sampled once, at idle.

---

## 13 | Base image vs. what the packs added

**How the split was derived.** The base image was built **2026-04-18**
(`/var/log/apt/history.log` ends 18:12 that day; 98 apt transactions, none since).
Everything dated **2026-07-19** in `/var/log/dpkg.log` is this session. So the
attribution below is by install timestamp, not by guessing from names.

**893 dpkg packages total = 862 base image + 31 added today by pack_G.**

### 13.1 Repositories — there are only two, and no git repos at all

APT sources (`/etc/apt/sources.list.d/`):

| Repo | URI | Suites |
| --- | --- | --- |
| Ubuntu | `http://archive.ubuntu.com/ubuntu/` | noble, noble-updates, noble-backports |
| Ubuntu security | `http://security.ubuntu.com/ubuntu/` | noble-security |
| NodeSource | `https://deb.nodesource.com/node_22.x` | nodistro |

The NodeSource entry is why node is **22.22.2** rather than Ubuntu's 18.x — the
base image deliberately pulls node 22 from an external repo.

Language registries: PyPI (default, no index override; cache
`/home/claude/.cache/pip`) and npm (default registry, `prefix=/home/claude/.npm-global`).

**There are no git repositories.** `git` exists as a binary, but
`git -C /home/claude/work status` returns *"not a git repository"*, and a
`find` for `.git` under `/home/claude` and `/usr/local` returns nothing. The work
tree is an **unzip of the release zip**, not a checkout. There is no local
history, no branches, no diff-against-HEAD. This is why the retired ZIP-diff helper (work tree vs
pinned zip) exists and why the overlay/deploy-manifest machinery has to reason
about deletions manually — nothing else is tracking them.

### 13.2 Base image: system binaries

Present and base (verified via `dpkg -S` + install date): `git`, `gcc`/`g++`
(13.x), `make`, `curl`, `wget`, `unzip`, `zip`, `tar`, `gzip`, `xz`, `sed`,
`awk`, `grep`, `patch`, `diff`, `file`, `nc`, `openssl`, `perl`,
**`ffmpeg` 6.1.1** (`/usr/bin/ffmpeg`), `java` (OpenJDK 21 JRE headless),
`python3` 3.12.3, `pip3`, `node`/`npm`/`npx`.

**Added today by pack_G, NOT base:** `jq` and `sqlite3` — both are routinely
assumed to be always-present. They are not; they arrive with pack_G. Also from
pack_G: `aria2`, `nftables`, `iproute2`, `iptables`, `wireguard-tools`,
`dnsmasq`, `zbar-tools`, `jellyfin-server`, `dbus`, plus supporting libs.

**Absent entirely** (worth knowing before reaching for them): `cmake`, `rsync`,
`ssh`, `vim`, `nano`, `go`, `rustc`, `ruby`, `php`, `yarn`, `docker`, `gh`,
`strace`, `ltrace`, `gdb`, `tcpdump`, `socat`, `redis-cli`.

### 13.3 Base image: system Python — 229 packages, and it is domain-tailored

`pip3 list` reports **229** packages. Only one is mine (`pyflakes`, installed
this session); pack_F stages a wheelhouse without installing, pack_H builds its
own venv at `audit_tools/venv`, and the test-tools wheels went into the *service*
venv. So ~228 of these ship in the image.

This is **not a generic image**. It already contains BD's problem domain:

- **Browser automation:** `playwright==1.56.0`, `playwright-stealth==2.0.3`
- **Media:** `yt-dlp==2026.3.17`, `m3u8==5.3.0`, `imageio-ffmpeg`, `videohash`
- **Proxy/capture:** `mitmproxy==12.2.3` (+ `mitmproxy_rs`, `mitmproxy_linux`)
- **Web stack:** `Flask==3.1.3`, `Werkzeug`, `Jinja2`, `itsdangerous`, `apprise`,
  `supervisor`
- **Scraping:** `scrapling`, `beautifulsoup4`, `lxml`, `w3lib`, `cssselect`, `vcrpy`
- **Site-specific API clients:** `phub`, `Eporner_API`, `hqporner_api`,
  `missAV_api`, `porngo_api`, `porntrex_api`, `spankbang_api`, `xfreehd_api`,
  `xhamster_api`, `xvideos_api`, `youporn_api`
- **Analysis:** numpy 2.4.4, pandas 3.0.2, scipy, scikit-learn, scikit-image,
  opencv (3 variants), matplotlib, seaborn, sympy, networkx, onnxruntime, mediapipe
- **Quality tooling:** `black`, `ruff`, `isort`, `pyright`, `pre_commit`,
  `python-lsp-server`, `py-spy`
- **Docs/PDF/Office:** pypdf, pdfplumber, pdfminer.six, pikepdf, camelot, tabula,
  python-docx, python-pptx, openpyxl, xlsxwriter, reportlab, mkdocs-material

**The trap this creates.** `import playwright` or `import yt_dlp` at a bare
`python3` prompt **succeeds from the base image**, independent of BD's venv and
independent of the prestaged layer. A check that concludes "the dependency is
installed" from a bare interpreter has proven nothing about what BD actually
runs. Three separate resolution paths exist — see 13.5.

### 13.4 Base image: node globals

`npm ls -g --depth=0`: `typescript@6.0.3`, `ts-node@10.9.2`, `tsx@4.21.0`,
`react@19.2.5`, `react-dom@19.2.5`, `react-icons@5.6.0`, `playwright@1.56.0`,
`sharp@0.34.5`, `pdf-lib`, `pdfjs-dist`, `pptxgenjs`, `marked`, `remark-cli`,
`remark-preset-lint-recommended`.

Note the global `typescript@6.0.3` and `react@19.2.5` are **not** what
`work/frontend` builds against — the FE has its own `node_modules`. Never let a
global resolve stand in for the project's pinned version.

### 13.5 Three Python resolution paths — do not conflate them

| Path | playwright | Used by |
| --- | --- | --- |
| System `/usr/bin/python3` | **1.56.0** | bare `python3`, base-image tooling |
| `/tmp/prestaged_site_packages` (121 top-level entries) | **1.61.0** | anything under `bd` (`PYTHONPATH`) |
| `work/venv` (Python 3.12.3) | **1.61.0** | the service itself |

Also: `flask 3.1.3`, `pytest 8.4.2`, `pytest-timeout 2.4.0`, `pytest-xdist 3.8.0`
live in the prestaged layer. This is the mechanical root of the documented false
positive — under `bd`, `venv/bin/python -c "import pytest"` succeeds via
`PYTHONPATH` even when the venv itself lacks it.

`playwright-stealth` (2.0.3) is present in **system** python. Relevant to MOD-6:
`stealth.py` imports `playwright_stealth`, a *different distribution* from
`playwright` — which is exactly why the exact-module predicate returns 12 and the
substring predicate returns 13.

### 13.6 Two browser pools

| Pool | Contents | Selected by |
| --- | --- | --- |
| `/opt/pw-browsers` (base image) | chromium-1194, chromium_headless_shell-1194, ffmpeg-1011 | `~/.npmrc` sets `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` |
| `/home/claude/.cache/ms-playwright` (BD) | chromium-**1223**, chromium_headless_shell-1223, firefox-1532, webkit-2311, ffmpeg-1011 | `bdenv.sh` overrides `PLAYWRIGHT_BROWSERS_PATH` |

Different chromium revisions (**1194 vs 1223**). Which one a launch gets depends
entirely on whether it ran under `bd`. A capture that behaves differently inside
and outside `bd` may be resolving a different browser build, not a code path —
and this is the same shape as the `bd-sbcap` finding, where the check inspected
the chromium build while `launch(headless=True)` executed the headless shell.

### 13.7 Curiosity worth noting

`install_bdsuite.sh` globs `bin/*`, so `/usr/local/bin` now contains symlinks to
two **non-executable PK data files**: `BDSUITE_TOOL_BUDGET` and `FOOTGUNS.json`,
plus a `__pycache__` entry. Harmless — nothing executes them — but they inflate
any naive count of "installed commands" and would confuse a tool that treats
everything on `PATH` as a program.
