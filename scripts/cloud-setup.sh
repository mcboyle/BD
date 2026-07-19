#!/bin/bash
# BulkDownloader — Claude Code cloud environment setup.
#
# Runs once at session start, BEFORE Claude Code launches.
# Installs EVERYTHING from upstream. No offline packs. Network access must be Full.
#
# DESIGN NOTE (CLAUDE.md section 0): the failure this exists to prevent is a
# SILENTLY DEGRADED environment — a step failed, nothing said so, and a later
# "0 failures" was really "0 of 0 examined". So every step records its outcome
# to .claude-env-report.md, which Claude Code reads before trusting any result.
# Load-bearing steps hard-fail. Everything else degrades to a recorded WARN and
# is never silently skipped.
#
# Opt OUT of a group with BD_SKIP_<GROUP>=1:
#   BROWSERS  AUDIT  NET  SECTOOLS  EXTRAS  CLOAK
# Nothing is opt-in; the default installs the lot (~10-14 min, several GB).

set -uo pipefail   # deliberately NOT -e: a failed step must be RECORDED, not
                   # abort provisioning and leave no explanation behind.

# --- repo discovery -----------------------------------------------------------
# DO NOT derive the repo from ${BASH_SOURCE[0]}. The setup-script panel executes
# this from a temp path (or stdin), so `dirname "$0"/..` resolves to "/" and every
# repo-relative step then fails while the system steps still report OK -- the
# exact "denominator cannot contain the subject" shape this project exists to
# avoid. Find the repo by its MARKER instead, and if it is genuinely not present
# yet, say so loudly rather than provisioning against "/".
MARKER="bulk_downloader/__init__.py"
find_repo() {
  local c
  for c in "${BD_REPO:-}" "${CLAUDE_PROJECT_DIR:-}" "$PWD" \
           /workspace /repo /src /app "$HOME/bulkdownloader" "$HOME/repo"; do
    [ -n "$c" ] && [ -f "$c/$MARKER" ] && { (cd "$c" && pwd); return 0; }
  done
  # bounded search. Collect ALL matches: taking the first silently picks a stale
  # clone when more than one exists. Choose the shallowest deterministically and
  # record the ambiguity rather than hiding it.
  local hits
  hits="$(find / -maxdepth 6 -type f -path "*/bulk_downloader/__init__.py" \
        -not -path "/proc/*" -not -path "/sys/*" -not -path "*/venv/*" \
        -not -path "*/site-packages/*" -not -path "*/node_modules/*" 2>/dev/null \
        | awk '{print gsub(/\//,"/"), $0}' | sort -n | cut -d" " -f2-)"
  [ -z "$hits" ] && return 1
  BD_REPO_CANDIDATES="$(echo "$hits" | wc -l)"
  c="$(echo "$hits" | head -1)"
  (cd "$(dirname "$(dirname "$c")")" && pwd); return 0
}

REPO="$(find_repo)" || REPO=""
if [ -n "$REPO" ]; then cd "$REPO"; HAVE_REPO=1; else HAVE_REPO=0; fi
BD_REPO_CANDIDATES="${BD_REPO_CANDIDATES:-1}"

# Report lives in HOME so it survives not knowing where the repo is; copied into
# the repo at the end when there is one.
REPORT="$HOME/.claude-env-report.md"

BIN="$HOME/.local/bin"; mkdir -p "$BIN"
export PATH="$BIN:$PATH"
export DEBIAN_FRONTEND=noninteractive
CORE_FAILED=0
START=$(date +%s)

: > "$REPORT"
{
  echo "# Environment provisioning report"
  echo
  echo "\`scripts/cloud-setup.sh\` — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "**Read this before trusting any test result.** A WARN row is a capability"
  echo "that is ABSENT, not one that passed. A suite depending on it will skip or"
  echo "fail for environmental reasons, which is not evidence about the code."
  echo
  echo "| Step | Result | Detail |"
  echo "| --- | --- | --- |"
} >> "$REPORT"

row(){ printf '| %s | %s | %s |\n' "$1" "$2" "$3" >> "$REPORT"; }

step(){                       # step <label> <core|optional> <command...>
  local label="$1" kind="$2"; shift 2
  local log; log="$(mktemp)"
  if "$@" > "$log" 2>&1; then
    row "$label" "OK" "$(tail -n1 "$log" | tr '|' '/' | cut -c1-80)"
    echo "[ ok ] $label"
  else
    local rc=$?
    if [ "$kind" = core ]; then
      row "$label" "**FAILED**" "exit $rc — $(tail -n3 "$log" | tr '\n' ' ' | tr '|' '/' | cut -c1-80)"
      echo "[FAIL] $label (exit $rc)"; sed 's/^/       /' "$log" | tail -n 12
      CORE_FAILED=1
    else
      row "$label" "WARN" "exit $rc — absent; dependent work cannot run"
      echo "[warn] $label (exit $rc)"
    fi
  fi
  rm -f "$log"
}

skip(){ [ "$(eval echo \${BD_SKIP_$1:-0})" = "1" ]; }

# sudo may be absent when already root; wrap it.
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
apt_i(){ $SUDO apt-get install -y -qq "$@"; }

if [ "${BD_REPO_CANDIDATES:-1}" -gt 1 ] 2>/dev/null; then
  row "repo discovery" "WARN" "$BD_REPO_CANDIDATES checkouts found; chose the shallowest: $REPO. Set BD_REPO to be explicit."
fi
echo "=== BulkDownloader provisioning (full upstream install) ==="
if [ "$HAVE_REPO" = 1 ]; then
  echo "repo: $REPO"
else
  echo "repo: NOT FOUND -- system tooling will install; app steps DEFERRED"
fi
df -h / | awk 'NR==2{print "disk free at start: "$4}'

# ============================================================ 0. system base
step "apt update" optional bash -c "$SUDO apt-get update -qq"

# ================================================================ 1. core app
# Everything here needs the checkout. If the repo is not present at setup time
# (a legitimate sequencing fact, not a provisioning failure) these are DEFERRED
# to the `bd-provision` helper installed at the end, and the report says so.
if [ "$HAVE_REPO" = 0 ]; then
  row "app provisioning" "**DEFERRED**" "repo not present at setup time -- run \`bd-provision\` once checked out"
  echo "[defer] app provisioning -- repo not found; run bd-provision after checkout"
else
  step "python venv"  core     python3 -m venv venv
  step "pip upgrade"  optional ./venv/bin/pip install -q --upgrade pip
  step "runtime deps" core     ./venv/bin/pip install -q -r requirements.txt
  step "test deps"    core     ./venv/bin/pip install -q "pytest>=7.0,<9.0" pyflakes

  # NODE_ENV=production makes `npm ci` omit devDependencies, silently removing
  # vite/typescript/vitest. Verified empirically; `npm ci --dry-run` does NOT
  # reveal it.
  if [ "${NODE_ENV:-}" = "production" ]; then
    row "NODE_ENV guard" "**FAILED**" "NODE_ENV=production omits devDependencies -- unset it"
    echo "[FAIL] NODE_ENV=production would strip the frontend toolchain"; CORE_FAILED=1
  fi
  step "frontend deps" core bash -c 'cd frontend && npm ci --no-audit --no-fund'
fi

# ================================================================ 2. browsers
if skip BROWSERS; then
  row "browsers" "WARN" "skipped via BD_SKIP_BROWSERS — capture/recognizer/e2e CANNOT run"
else
  if [ "$HAVE_REPO" = 1 ]; then
    step "playwright chromium" optional ./venv/bin/python -m playwright install --with-deps chromium
    step "playwright ff+wk"    optional ./venv/bin/python -m playwright install firefox webkit
  else
    row "browsers" "**DEFERRED**" "needs the app venv -- bd-provision installs them"
  fi
fi

# =========================================================== 3. cloak browser
# Ships a STEALTH chromium whose fingerprint is the point. Floor is >=0.4.5;
# upstream runs ahead of the build historical captures were recorded against,
# so pin explicitly when reproducing a past result.
if skip CLOAK; then
  row "cloakbrowser" "WARN" "skipped via BD_SKIP_CLOAK"
else
  if [ "$HAVE_REPO" = 1 ]; then
    step "cloakbrowser"     optional ./venv/bin/pip install -q "cloakbrowser[geoip]>=0.4.5"
    step "stealth chromium" optional ./venv/bin/python -m cloakbrowser install
  else
    row "cloakbrowser" "**DEFERRED**" "needs the app venv -- bd-provision installs it"
  fi
  # Metric-compatible Windows fonts: absence is a fingerprinting tell, not a
  # functional break.
  step "win-metric fonts" optional apt_i fonts-crosextra-carlito fonts-crosextra-caladea
fi

# ================================================================== 4. audit
# Separate venv on purpose: semgrep drags pydantic/opentelemetry/starlette and
# co-installing it risks resolving BD's own pins differently.
if skip AUDIT; then
  row "audit toolchain" "WARN" "skipped via BD_SKIP_AUDIT — bd-rev / code audit cannot run"
else
  step "audit venv"    optional python3 -m venv audit-venv
  step "audit wheels"  optional ./audit-venv/bin/pip install -q semgrep bandit vulture \
                                    radon detect-secrets libcst hypothesis coverage
  step "fd+shellcheck" optional apt_i fd-find shellcheck
  # Ubuntu names the fd binary 'fdfind'; tools expect 'fd'.
  command -v fdfind >/dev/null 2>&1 && ln -sf "$(command -v fdfind)" "$BIN/fd"
  step "jscpd"         optional npm install -g --silent jscpd
  step "a11y stack"    optional npm install -g --silent axe-core pa11y lighthouse
fi

# ============================================================ 5. net tooling
# Ubuntu 24.04 t64 rename: libzbar0 -> libzbar0t64. The old name resolves to
# nothing and apt does not suggest the replacement.
if skip NET; then
  row "net tooling" "WARN" "skipped via BD_SKIP_NET — VPN/egress proofs cannot run"
else
  step "net packages" optional apt_i wireguard-tools nftables iproute2 iptables \
                                     dnsmasq aria2 jq sqlite3 zbar-tools libzbar0t64
fi

# ======================================================= 6. security tooling
if skip SECTOOLS; then
  row "security tooling" "WARN" "skipped via BD_SKIP_SECTOOLS"
else
  # nuclei is PINNED: its finding count is a function of its template pack, so
  # an unpinned upgrade silently changes results.
  step "nuclei v3.3.0" optional bash -c '
    set -e; cd "$(mktemp -d)"
    curl -sSLf -o n.zip https://github.com/projectdiscovery/nuclei/releases/download/v3.3.0/nuclei_3.3.0_linux_amd64.zip
    unzip -oq n.zip nuclei -d "$HOME/.local/bin/"; chmod +x "$HOME/.local/bin/nuclei"'
  # PINNED, with a direct asset URL and NO GitHub API call. Two reasons:
  # (1) api.github.com is rate-limited and returns 403 unauthenticated — that is
  #     exactly how the first version of this script failed, intermittently and
  #     for a reason the error text did not name;
  # (2) resolving "latest" makes the toolchain unpinned, and gitleaks decides CI
  #     outcomes here — a silent upgrade can fail the baseline legitimately.
  step "gitleaks 8.30.1" optional bash -c '
    set -e; cd "$(mktemp -d)"
    curl -sSLf -o g.tgz https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz
    tar xzf g.tgz gitleaks; mv gitleaks "$HOME/.local/bin/"; chmod +x "$HOME/.local/bin/gitleaks"'
  step "ffuf 2.2.1" optional bash -c '
    set -e; cd "$(mktemp -d)"
    curl -sSLf -o f.tgz https://github.com/ffuf/ffuf/releases/download/v2.2.1/ffuf_2.2.1_linux_amd64.tar.gz
    tar xzf f.tgz ffuf; mv ffuf "$HOME/.local/bin/"; chmod +x "$HOME/.local/bin/ffuf"'
  step "bento4 (mp4*)" optional bash -c '
    set -e; cd "$(mktemp -d)"
    curl -sSLf -o b.zip https://www.bok.net/Bento4/binaries/Bento4-SDK-1-6-0-641.x86_64-unknown-linux.zip
    unzip -oq b.zip
    for t in mp4dump mp4info mp4fragment; do
      f=$(find . -type f -name "$t" | head -1); [ -n "$f" ] && cp "$f" "$HOME/.local/bin/"
    done'
fi

# ================================================================= 7. extras
if skip EXTRAS; then
  row "extras" "WARN" "skipped via BD_SKIP_EXTRAS — GTK module-import gate cannot run"
else
  # GTK: test_v3_43_80_modules::test_all_modules_import false-fails without
  # typelibs AND a display. Environmental, never a code regression.
  step "GTK + Xvfb"   optional apt_i xvfb libgtk-3-0t64 gir1.2-gtk-3.0 python3-gi \
                                     libcairo2 libgirepository-1.0-1
  step "misc tooling" optional apt_i pypy3 caddy postgresql-client patchelf
  step "profiling"    optional ./venv/bin/pip install -q py-spy
fi

# ================================================================ 8. runtime
mkdir -p "${BD_HOME:-/tmp/bd_home}" 2>/dev/null \
  && row "BD_HOME" "OK" "${BD_HOME:-/tmp/bd_home} (outside the repo, as required)" \
  || row "BD_HOME" "WARN" "could not create ${BD_HOME:-/tmp/bd_home}"

if command -v Xvfb >/dev/null 2>&1; then
  pgrep -x Xvfb >/dev/null 2>&1 || (Xvfb :99 -screen 0 1024x768x24 >/dev/null 2>&1 &)
  sleep 2
  row "Xvfb :99" "OK" "started — export DISPLAY=:99 for the GTK gate"
fi

# =========================================================== 9. verification
# Installers exiting 0 is not proof. Prove the package imports and matches.
if [ "$HAVE_REPO" = 1 ]; then
  VER="$(./venv/bin/python -c 'import bulk_downloader;print(bulk_downloader.__version__)' 2>/dev/null)"
  PINNED="$(grep -oE '__version__ *= *"[^"]+"' bulk_downloader/__init__.py 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
  if [ -n "$VER" ] && [ "$VER" = "$PINNED" ]; then
    row "import check" "OK" "bulk_downloader $VER matches the tree"; echo "[ ok ] import $VER"
  else
    row "import check" "**FAILED**" "imported '${VER:-<none>}', tree declares '${PINNED:-<none>}'"
    echo "[FAIL] import check"; CORE_FAILED=1
  fi

  step "guard files" optional ./venv/bin/python - <<'PY2'
import hashlib,sys
G={"bulk_downloader/extraction_core.py":"5b6248a5c9e664ab",
   "bulk_downloader/session_capture.py":"547d70c95cde9377",
   "bulk_downloader/dom_capture.py":"0559903d0b159162",
   "bulk_downloader/dom_recorder.py":"1657d0a0e39917ae",
   "bulk_downloader/capture_bodies.py":"6c7f5c9a87510cca",
   "tools/capture_session.py":"27be68b965689317",
   "tools/build_release.py":"f7c220d279fcfbee"}
bad=[f for f,w in G.items() if hashlib.sha256(open(f,'rb').read()).hexdigest()[:16]!=w]
print(f"{len(G)-len(bad)}/{len(G)} guard files match")
sys.exit(1 if bad else 0)
PY2
else
  row "import check" "**DEFERRED**" "no repo at setup time"
fi

# ====================================================== 10. capability probe
# Probe by DOING. capsh lists cap_net_admin in the bounding set in environments
# where it is unusable, so the only honest test is to create and delete.
{
  echo
  echo "## Capabilities (probed, not assumed)"
  echo
  echo '```'
  printf '%-15s %s\n' "cpu cores"     "$(nproc)"
  printf '%-15s %s\n' "mem"           "$(free -h | awk '/^Mem:/{print $2}')"
  printf '%-15s %s\n' "disk free"     "$(df -h / | awk 'NR==2{print $4}')"
  printf '%-15s %s\n' "/dev/net/tun"  "$([ -e /dev/net/tun ] && echo present || echo ABSENT)"
  printf '%-15s %s\n' "netns create"  "$(ip netns add _p 2>/dev/null && ip netns del _p 2>/dev/null && echo yes || echo NO)"
  printf '%-15s %s\n' "wireguard mod" "$(ip link add _wg type wireguard 2>/dev/null && ip link del _wg 2>/dev/null && echo yes || echo 'NO - veth stands in; egress policy provable, live handshake NOT')"
  printf '%-15s %s\n' "nft"           "$(command -v nft >/dev/null && echo present || echo absent)"
  printf '%-15s %s\n' "outbound 443"  "$(curl -sI -o /dev/null -w %{http_code} https://pypi.org 2>/dev/null)"
  printf '%-15s %s\n' "node"          "$(node -v 2>/dev/null || echo absent)"
  printf '%-15s %s\n' "ffmpeg"        "$(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3 || echo absent)"
  printf '%-15s %s\n' "nuclei"        "$(nuclei -version 2>&1 | grep -oE 'v[0-9.]+' | head -1 || echo absent)"
  printf '%-15s %s\n' "semgrep"       "$(./audit-venv/bin/semgrep --version 2>/dev/null || echo absent)"
  echo '```'
} >> "$REPORT"

# ================================================= 11. bd-provision helper
# The repo half, runnable on demand once the checkout exists. Idempotent.
cat > "$BIN/bd-provision" <<'PROV'
#!/bin/bash
# Provision the BulkDownloader repo half (venv, deps, browsers) after checkout.
set -uo pipefail
R="${1:-$PWD}"
[ -f "$R/bulk_downloader/__init__.py" ] || {
  echo "not a BulkDownloader checkout: $R"; echo "usage: bd-provision [repo-path]"; exit 2; }
cd "$R"; echo "provisioning $R"
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt || exit 1
./venv/bin/pip install -q "pytest>=7.0,<9.0" pyflakes
[ "${NODE_ENV:-}" = "production" ] && { echo "FATAL: NODE_ENV=production omits devDependencies"; exit 1; }
( cd frontend && npm ci --no-audit --no-fund ) || echo "WARN: frontend deps failed"
./venv/bin/python -m playwright install --with-deps chromium || echo "WARN: chromium failed"
./venv/bin/pip install -q "cloakbrowser[geoip]>=0.4.5" && ./venv/bin/python -m cloakbrowser install || echo "WARN: cloakbrowser failed"
V=$(./venv/bin/python -c 'import bulk_downloader;print(bulk_downloader.__version__)' 2>/dev/null)
echo "import check: ${V:-FAILED}"
[ -n "$V" ] || exit 1
echo "bd-provision: READY"
PROV
chmod +x "$BIN/bd-provision"
row "bd-provision" "OK" "installed at $BIN/bd-provision (re-runnable; provisions the repo half)"

# =============================================================== 12. verdict
ELAPSED=$(( $(date +%s) - START ))
{
  echo
  if [ "$CORE_FAILED" -eq 1 ]; then
    echo "## VERDICT: INCOMPLETE"
    echo
    echo "A load-bearing step failed. Do not read test results from this"
    echo "environment as evidence about the code until it is fixed."
  elif [ "$HAVE_REPO" = 0 ]; then
    echo "## VERDICT: SYSTEM READY, APP DEFERRED  (${ELAPSED}s)"
    echo
    echo "All system tooling installed. The repository was NOT present at setup"
    echo "time, so the venv, python deps, frontend deps and browsers were not"
    echo "installed -- that is a sequencing fact, not a failure."
    echo
    echo "**Run \`bd-provision\` from the repo root before running anything that"
    echo "imports the app or touches a test.** Until then there is no venv, and a"
    echo "test command will fail for environmental reasons that say nothing about"
    echo "the code."
  else
    echo "## VERDICT: READY  (${ELAPSED}s)"
    echo
    echo "Core provisioning succeeded. Check every WARN row above before"
    echo "concluding a suite is green -- a WARN is an absent capability."
  fi
  echo
  echo "Not attempted, by design: the test suite (the operator's gate, on the"
  echo "host), and the \`bd-*\` toolchain -- 155 of 249 tools hardcode sandbox"
  echo "paths and need porting, which no amount of provisioning fixes."
} >> "$REPORT"

# surface the report inside the repo when there is one
[ "$HAVE_REPO" = 1 ] && cp "$REPORT" "$REPO/.claude-env-report.md" 2>/dev/null

if [ "$CORE_FAILED" -eq 1 ]; then
  echo "=== INCOMPLETE in ${ELAPSED}s -- report: $REPORT ==="
elif [ "$HAVE_REPO" = 0 ]; then
  echo "=== SYSTEM READY in ${ELAPSED}s; APP DEFERRED -- run bd-provision after checkout ==="
else
  echo "=== READY in ${ELAPSED}s -- report: $REPORT ==="
fi
# Exit 0 when only the repo was missing: provisioning did its job, and failing
# the session start over a checkout that had not happened yet is wrong.
exit "$CORE_FAILED"
