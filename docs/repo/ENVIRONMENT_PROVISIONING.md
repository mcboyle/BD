# Environment provisioning — network install (no offline packs)

<!-- verified-against: v3.66.805 -->

**Scope.** Everything the offline packs (A–H, G2, cloak, nuitka, test-tools,
verification kit) provided, installed instead from upstream over the network.
Written for the Claude Code cloud environment with **Network access: Full**.

**Do not upload or use the offline packs here.** They exist to provision a
sandbox that had no egress. With full network access they are 2.5 GB of
redundant transfer, and they cannot reach a cloud environment anyway — there is
no uploads directory, `pack_A.zip` alone (460 MB) exceeds GitHub's 100 MB file
limit, and a private repo's release assets need a token that must not be pasted
into an environment-variables panel that is visible to everyone using it.

---

## 1 | The one real cost of going upstream: version drift

The packs pinned exact builds. Upstream gives you *current* builds. Measured
today against the pinned versions:

| Component | Pack pinned | Upstream now | Drift |
| --- | --- | --- | --- |
| nuitka | 4.1.3 | **4.1.3** | none |
| semgrep | 1.168.0 | **1.170.0** | 2 minor |
| cloakbrowser | 0.4.5 | **0.4.12** | 7 patch |
| playwright (python) | 1.61.0 | floor is `>=1.61,<2.0` | pin holds |

Two of these matter and one does not.

- **`cloakbrowser` is the one to be careful with.** It ships a *stealth* Chromium,
  and the whole point of that build is its fingerprint. A different patch build
  can plausibly present a different fingerprint surface. `requirements-cloak.txt`
  floors it at `>=0.4.5`, so 0.4.12 satisfies the constraint while not being the
  build the captures were recorded against. If a capture or recognizer result
  disagrees with a known-good baseline, **suspect this before suspecting the
  code.** Pin it explicitly (§6) when reproducing a historical result.
- **`semgrep` drift shifts audit findings.** New rules appear between minor
  versions, so a finding count will not match a historical audit. That is
  expected and is not a regression — say which version produced a count.
- `playwright` is floored, not pinned, and `requirements.txt` explains why:
  the `.spec` hard-codes paths under `playwright._impl.*`, so the ceiling
  `<2.0` is the load-bearing half of that constraint.

**Rule:** record the version that produced any number you intend to compare
later. A count without its toolchain version is the same failure shape as a
baseline nobody measured.

---

## 2 | Everything installs at session start

`scripts/cloud-setup.sh` provisions **all** of the below on every new session.
Nothing is opt-in. **Measured: 181 s, ~3.6 GB.**

Opt *out* of a group only when you have a reason, with `BD_SKIP_<GROUP>=1`:
`BROWSERS`, `AUDIT`, `NET`, `SECTOOLS`, `EXTRAS`, `CLOAK`. Every skip is written
to the report as a WARN naming what can no longer run, so a skipped capability
can never be read later as a passing suite.

The sections below are the reference for *what each group provides and why* —
useful when a step WARNs and you need to know what you lost, not a menu to
choose from.

**Two operational notes from running it end to end:**

- **Disk is the binding constraint, not time.** The full install consumed
  ~3.6 GB. Check headroom before assuming a session can also build the
  frontend and a release artifact.
- **Never resolve "latest" through `api.github.com`.** Unauthenticated calls are
  rate-limited and return **403**, which is precisely how the first version of
  this script failed — intermittently, on two tools, with an error that never
  named the cause. Every binary is now pinned to a direct asset URL with no API
  call. That is also the correct answer for reproducibility: `gitleaks` decides
  CI outcomes here and `nuclei`'s finding count is a function of its template
  pack, so an unpinned upgrade silently changes results.

## 3 | Tier 0 — core runtime

Replaces: `pack_A` (core/venv kits), `pack_D` (dev kit), `pack_C` frontend kit.

```bash
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
./venv/bin/pip install -q "pytest>=7.0,<9.0" pyflakes
cd frontend && npm ci --no-audit --no-fund && cd ..
```

**Verified:** both requirements files resolve cleanly against a clean PyPI index
(pip dry-run exit 0 — 20 and 27 packages), and this exact sequence provisions a
working tree that runs a real suite.

**Do not install `requirements-dev.txt` wholesale** unless you are doing
packaging work. It pulls `pyinstaller` and `nuitka`, which exist for a build
path (MOD-7) that is closed. `pytest` is the only part of it a dev box needs.

**Never set `NODE_ENV=production`.** Verified empirically: it makes `npm ci` omit
devDependencies, silently removing vite, typescript and vitest, after which the
build fails with an error that does not name the cause. Note that
`npm ci --dry-run` does *not* reveal this — the dry run installed the dev
dependency under both settings and only a real install exposed the omission.

Media tooling (`pack_C` media kit) — **verify, do not assume.** This paragraph
previously stated that `ffmpeg 6.1.1` was already present at `/usr/bin/ffmpeg`.
That was measured false on the Claude cloud container on 2026-07-27: `ffmpeg`
and `ffprobe` were both **absent**, and because this document said otherwise, no
provisioning path installed them. `bulk_downloader/integrity.py` shells out to
`ffprobe`, and its absence makes media verification fail open.

`ffmpeg` is now in the `media` group of `scripts/lib/system_deps.sh`, so all
three provisioning paths install it, and both `ffmpeg` and `ffprobe` are probed
separately in the capability block (probing only `ffmpeg` answers a question
nobody asked — `ffprobe` is the binary the integrity path actually invokes).

When a distribution ffmpeg IS present, do not install a static build over it:
the static ffmpeg is precisely what segfaults on HLS+HTTPS.

---

## 4 | Tier 1 — browsers

Replaces: `pack_B` chromium kit, `pack_E` (firefox + webkit).

```bash
./venv/bin/python -m playwright install --with-deps chromium   # ~150 MB
./venv/bin/python -m playwright install firefox webkit         # ~+300 MB
```

`--with-deps` pulls the system libraries via apt and is what makes this work on a
bare container. Apply it to the first invocation only.

**Configuration that matters more than the install:** set
`PLAYWRIGHT_BROWSERS_PATH` explicitly and know which pool you are using. In the
old sandbox two pools coexisted at different chromium revisions (1194 vs 1223)
and which one a launch resolved depended on the environment wrapper. If a
capture behaves differently in two contexts, check the browser build before
suspecting a code path.

```bash
export PLAYWRIGHT_BROWSERS_PATH=$HOME/.cache/ms-playwright
./venv/bin/python -c "from playwright.sync_api import sync_playwright
with sync_playwright() as p: b=p.chromium.launch(headless=True); print(b.version); b.close()"
```

Headless webkit needs a GL stack many containers lack; run it headful under Xvfb
(§8) or accept that it is unavailable and **record that** rather than letting the
suite skip quietly.

---

## 5 | Tier 2 — audit toolchain

Replaces: `pack_H` (wheels + jscpd + a11y node stack + fd/shellcheck).

```bash
python3 -m venv audit-venv
./audit-venv/bin/pip install -q semgrep bandit vulture radon \
    detect-secrets libcst hypothesis coverage
sudo apt-get install -y fd-find shellcheck
npm install -g jscpd
npm install -g axe-core pa11y lighthouse
```

Keep the audit tools in their **own venv**. `semgrep` drags a large dependency
tree (pydantic, opentelemetry, starlette, glom) and co-installing it with the
app venv risks resolving BD's own pins differently. The pack kept them separate
for this reason; preserve that.

On Ubuntu the binary from `fd-find` is called **`fdfind`**, not `fd`. Symlink it
if a tool expects `fd`:

```bash
mkdir -p ~/.local/bin && ln -sf "$(command -v fdfind)" ~/.local/bin/fd
```

---

## 6 | Cloak / stealth browser

Replaces: `bd_cloak_pack` (pip-wheels + stealth-chromium tarball + win-fonts).

```bash
./venv/bin/pip install "cloakbrowser[geoip]>=0.4.5"
./venv/bin/python -m cloakbrowser install    # fetches + verifies stealth Chromium
```

`cloakbrowser install` downloads the stealth Chromium that the pack bundled, and
verifies it (Ed25519 + SHA-256) at fetch time. That is the same artifact; the
pack existed only to avoid the download.

**To reproduce a historical capture, pin the build:**

```bash
./venv/bin/pip install "cloakbrowser[geoip]==0.4.5"
```

The pack's Chromium was `146.0.7680.177.5`. If you need that exact build and
0.4.5 no longer fetches it, that is a genuine reproducibility limit — say so
rather than substituting a different build and calling the result comparable.

**Windows font tell.** The pack shipped Carlito and Caladea plus a fontconfig
alias, because a Linux host missing metric-compatible Windows fonts is a
fingerprinting signal. Upstream equivalent:

```bash
sudo apt-get install -y fonts-crosextra-carlito fonts-crosextra-caladea
fc-cache -f
```

Without these the browser is still functional; it is *more identifiable*. That
distinction matters for capture work and nothing else.

---

## 7 | Tier 3 — network tooling

Replaces: `pack_G` + `pack_G2` (29 debs).

```bash
sudo apt-get update
sudo apt-get install -y \
    wireguard-tools nftables iproute2 iptables dnsmasq \
    aria2 jq sqlite3 zbar-tools libzbar0t64
```

**Two package names are traps on Ubuntu 24.04.** The `t64` ABI transition
renamed them: `libzbar0` → **`libzbar0t64`**, and `libgtk-3-0` →
**`libgtk-3-0t64`**. The old names resolve to nothing and apt reports them as
uninstallable rather than suggesting the rename. Verified against noble.

**`jq` and `sqlite3` are not in the base image.** They are widely assumed to be
always present and they are not; they arrive here.

### 7.1 What the network capabilities actually are

**`CAP_NET_ADMIN` and `/dev/net/tun` are present in the Claude Code cloud
environment — operator-reported, not measured by me.** Treat that as reliable
but keep the attribution: it is the kind of fact that changes when an image is
rebuilt, and the probe in §11 costs nothing.

That unlocks Tier 3 work that was previously assumed host-only. Verified in an
equivalent environment with the same two capabilities:

| Capability | Result | How established |
| --- | --- | --- |
| create/delete a netns | **works** | `ip netns add` succeeded |
| nftables rules + counters | **works** | package installs, tooling present |
| `/dev/net/tun` | present | operator-reported; file exists in the twin env |
| **WireGuard interface** | **DOES NOT WORK** | `ip link add type wireguard` → *Unknown device type* |

**The WireGuard kernel module is absent.** `CAP_NET_ADMIN` plus `/dev/net/tun`
does **not** imply it, and the two are easy to conflate. A `veth` pair stands in,
which proves **iface-scoped egress policy and leak prevention** — it does not
prove a live handshake. Do not let a green egress proof be reported as "the VPN
works"; it demonstrates that traffic is dropped when the named interface is
down, which is a different and narrower claim.

**Probe by doing, not by asking.** `capsh --print` lists `cap_net_admin` in the
bounding set in environments where it is nonetheless unusable. The only honest
test is to create a namespace and delete it. §11 does exactly that.

### 7.2 A tool that asserts this instead of deriving it

`bd-netns-proof` runs clean against a clone (exit 0) and prints:

> `netns toolchain present: True; creating a netns needs CAP_NET_ADMIN (stash-only)`

**That parenthetical is false here.** Creating a netns succeeded in an
environment with these capabilities. The tool *declares* the capability
host-only rather than *deriving* it, so it under-reports what can be tested and
routes work to the host that does not need to go there. This is the §0 shape in
its plain form — an assertion standing in for a measurement — and it is a good
first target for the port-as-you-go work: replace the hardcoded verdict with an
actual `ip netns add` probe and report three states (works / lacks capability /
could not determine).

---

## 8 | Tier 4 — security / load tooling

Replaces: `bd_test_tools_pack` (nuclei, ffuf, gitleaks, oha, websocat, Bento4).

All ship as GitHub release binaries; all five endpoints verified reachable.

```bash
mkdir -p ~/.local/bin && cd /tmp

# nuclei — pin the version; template packs change findings between releases
curl -sSL -o n.zip https://github.com/projectdiscovery/nuclei/releases/download/v3.3.0/nuclei_3.3.0_linux_amd64.zip
unzip -o -q n.zip nuclei -d ~/.local/bin/

# the rest: take the latest linux amd64 asset from each release page
#   github.com/ffuf/ffuf          github.com/gitleaks/gitleaks
#   github.com/hatoo/oha          github.com/vi/websocat
#   github.com/axiomatic-systems/Bento4   (mp4dump / mp4info / mp4fragment)

export PATH="$HOME/.local/bin:$PATH"
nuclei -version && gitleaks version
```

**Pin nuclei deliberately.** Its finding count is a function of its template
pack, so an unpinned upgrade silently changes results. The project standardised
on **v3.3.0**; changing it is a decision, not an upgrade.

`gitleaks` is also the tool CI depends on, and CI runs it against
`.gitleaks-baseline.json`. A newer gitleaks may detect findings the baseline
does not contain, which fails CI legitimately — regenerate the baseline in its
own commit so the diff is reviewable.

---

## 9 | Tier 5 — extras, each rarely needed

Replaces: `pack_C` (pypy, R, profiling, precommit, lsp), `pack_B` (webproxy,
supervisord), `bd_nuitka_pack`, GTK from `pack_A`.

```bash
# GTK — only for the tray/module-import gate
sudo apt-get install -y xvfb libgtk-3-0t64 gir1.2-gtk-3.0 python3-gi libcairo2

# packaging (MOD-7, closed — install only if you reopen it)
./venv/bin/pip install "nuitka>=4.0,<5.0" "pyinstaller>=6.0,<7.0" zstandard
sudo apt-get install -y patchelf

# occasional
sudo apt-get install -y pypy3 r-base caddy postgresql-client
./venv/bin/pip install py-spy pre-commit python-lsp-server
```

**GTK is the one with a documented trap.** `test_v3_43_80_modules::test_all_modules_import`
false-fails with `tray_app: Namespace Gtk not available` unless the typelibs are
present *and* a display exists. It is **environmental, not a regression** — do
not chase it as a code defect.

```bash
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
export GI_TYPELIB_PATH=/usr/lib/x86_64-linux-gnu/girepository-1.0
```

The **verification kit** needs no install at all — it is ~240 KB of
vulnerable/fixed corpus pairs (`DP-01`…`DP-14`). Commit it to the repo under
`tests/verification/` and delete the zip from your workflow entirely.

---

## 10 | Configuration

Environment variables — the panel is **visible to everyone using the
environment**, so nothing secret goes in it:

```ini
BD_HOME=/tmp/bd_home
BD_DISABLE_KEEPALIVE=1
PYTHONDONTWRITEBYTECODE=1
PYTHONUNBUFFERED=1
PIP_DISABLE_PIP_VERSION_CHECK=1
PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
NPM_CONFIG_FUND=false
NPM_CONFIG_AUDIT=false
DISPLAY=:99
# NODE_ENV — deliberately absent. See section 3.
```

`BD_HOME` outside the tree is load-bearing: runtime state written into the
deployed code tree is a bug this project has already shipped and fixed once
(v3.66.805, plugin quarantine state).

**Mock services.** The old sandbox ran apprise (:8765), plex (:32400), jellyfin
(:8096) and stash (:9999) fakes, respawned automatically by an env wrapper.
There is no equivalent here — start them from `mocks_kit`-style scripts if a
suite needs them, and allow ~5 seconds to bind. A service confirmed up in one
command is not necessarily up in the next; check `/proc/<pid>`, never `pgrep -f`,
which matches its own wrapper.

---

## 11 | Probe what actually works — do not assume

Several capabilities above cannot be confirmed from outside the target
environment. Run this first and record the output; a capability that is absent
must be **reported as absent**, never rounded up to a skipped test.

```bash
cat > /tmp/probe.sh <<'EOF'
#!/bin/bash
r(){ printf '%-26s %s\n' "$1" "$2"; }
r "root"          "$([ "$(id -u)" = 0 ] && echo yes || echo "no (uid $(id -u))")"
r "sudo"          "$(command -v sudo >/dev/null && echo yes || echo NO)"
r "apt writable"  "$(apt-get -s install -y jq >/dev/null 2>&1 && echo yes || echo NO)"
r "cpu cores"     "$(nproc)"
r "mem"           "$(free -h | awk '/^Mem:/{print $2}')"
r "disk free"     "$(df -h / | awk 'NR==2{print $4}')"
r "/dev/net/tun"  "$([ -e /dev/net/tun ] && echo present || echo ABSENT)"
r "CAP_NET_ADMIN" "$(capsh --print 2>/dev/null | grep -q cap_net_admin && echo yes || echo NO)"
r "netns create"  "$(ip netns add _p 2>/dev/null && ip netns del _p 2>/dev/null && echo yes || echo NO)"
r "wireguard mod" "$(ip link add _wg type wireguard 2>/dev/null && ip link del _wg 2>/dev/null && echo yes || echo "NO (veth stands in; no live handshake)")"
r "nft"           "$(command -v nft >/dev/null && echo present || echo absent)"
r "outbound 443"  "$(curl -sI -o /dev/null -w %{http_code} https://pypi.org 2>/dev/null)"
r "Xvfb"          "$(command -v Xvfb >/dev/null && echo present || echo absent)"
r "node"          "$(node -v 2>/dev/null || echo absent)"
r "ffmpeg"        "$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3 || echo absent)"
EOF
bash /tmp/probe.sh
```

Expected differences from the old sandbox, worth checking rather than assuming:
**CPU count** (the sandbox had 1 core, which is why parallel test jobs bought
nothing), **disk allowance**, and **`CAP_NET_ADMIN`**, which gates all VPN and
egress proof work.

---

## 12 | What this does not give you

- **Byte-identical reproduction of a historical run.** Upstream moves. If a
  number must match a past measurement, pin the tool version and say which.
- **A working `bd-*` toolchain.** 155 of 249 tools hardcode sandbox paths and
  will not run against a clone regardless of what is installed. That is a
  porting task, not a provisioning one.
- **Anything about the host.** Provisioning here tells you nothing about stash.
