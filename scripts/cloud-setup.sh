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
# Opt OUT of a group by setting its flag to 1 (each named in full so the
# config-surface scanner can ledger them):
#   BD_SKIP_BROWSERS  BD_SKIP_AUDIT  BD_SKIP_NET  BD_SKIP_SECTOOLS  BD_SKIP_EXTRAS  BD_SKIP_CLOAK  BD_SKIP_ARCHB
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

# NAMED PROBES ONLY -- there is deliberately no filesystem search.
#
# A `find / -maxdepth 6` fallback used to rank candidates by path depth and take
# the shallowest. On any host that has run the test suite, /tmp fills with two-
# and three-file pytest fixtures containing bulk_downloader/__init__.py, and
# those are SHALLOWER than the real checkout. Measured on a working container:
# 70 candidates, and the winner was a 3-file fixture directory. Provisioning
# against a fixture is not a degraded success -- every later step then reports
# OK about the wrong tree.
#
# Sets REPO and REPO_VIA directly rather than echoing. The old version was
# invoked as `REPO="$(find_repo)"`, so anything it assigned (notably the
# candidate count) died with the command-substitution subshell and the
# ambiguity WARN that read it could never fire.
REPO=""
REPO_VIA=""
find_repo() {
  local name path
  for name in BD_REPO CLAUDE_PROJECT_DIR PWD; do
    path="${!name:-}"
    if [ -n "$path" ] && [ -f "$path/$MARKER" ]; then
      REPO="$(cd "$path" && pwd)"; REPO_VIA="\$$name"; return 0
    fi
  done
  # This list must cover every rung scripts/cloud-bootstrap.sh is willing to
  # hand over; tests/test_cloud_bootstrap_is_thin.py asserts the containment.
  # When `/home/*/BD` was added there and not here, a checkout the bootstrap
  # could resolve was invisible to this function -- and the failure is silent,
  # because HAVE_REPO=0 still provisions the system half and still reports.
  #
  # Both markers are required, not just $MARKER: these rungs now include globs,
  # and /tmp-style two-file fixtures carry bulk_downloader/__init__.py alone.
  for path in /workspace /repo /src /app \
              "$HOME/BD" "$HOME/BulkDownloader" "$HOME/bulkdownloader" "$HOME/repo" \
              /home/*/BD /home/*/BulkDownloader /home/*/bulkdownloader; do
    if [ -f "$path/$MARKER" ] && [ -f "$path/scripts/cloud-setup.sh" ]; then
      REPO="$(cd "$path" && pwd)"; REPO_VIA="$path"; return 0
    fi
  done
  return 1
}

find_repo || true
if [ -n "$REPO" ]; then cd "$REPO"; HAVE_REPO=1; else HAVE_REPO=0; fi

# Report lives in HOME so it survives not knowing where the repo is; copied into
# the repo at the end when there is one.
REPORT="$HOME/.claude-env-report.md"

BIN="$HOME/.local/bin"; mkdir -p "$BIN"
export PATH="$BIN:$PATH"
export DEBIAN_FRONTEND=noninteractive
CORE_FAILED=0
START=$(date +%s)

: > "$REPORT"

# Provenance. A wall-clock timestamp cannot answer "is this still true?" -- and
# this report tells its reader, in its own header, to trust it. One was found
# seven days stale on a live container, asserting a version the tree had long
# since moved past, while a session read it as current. So the report records
# the TREE it was generated against, not merely when. Content, not bytes: a
# reader compares version+commit, and re-running on an unchanged tree produces
# the same provenance rather than a spurious diff.
if [ "$HAVE_REPO" = 1 ]; then
  GEN_VERSION="$(grep -oE '__version__ *= *"[^"]+"' bulk_downloader/__init__.py 2>/dev/null \
                 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
  GEN_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
else
  GEN_VERSION=""
  GEN_COMMIT=""
fi

{
  echo "# Environment provisioning report"
  echo
  echo "\`scripts/cloud-setup.sh\` — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo '```'
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "generated_against_version=${GEN_VERSION:-UNKNOWN}"
  echo "generated_against_commit=${GEN_COMMIT:-UNKNOWN}"
  echo '```'
  echo
  echo "If \`generated_against_version\`/\`generated_against_commit\` do not match"
  echo "the tree you are reading this in, every row below describes a DIFFERENT"
  echo "tree. Re-run the provisioner rather than trusting it. UNKNOWN means the"
  echo "provenance could not be determined, which is not the same as current."
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
      # Report what the command SAID, exactly as the core branch does. The old
      # text was the fixed sentence "absent; dependent work cannot run", which
      # is true for an uninstalled package and false for everything else -- a
      # drifted guard, a syntax error, an HTTP 403. Naming the wrong cause in
      # the one document a session is told to trust is worse than naming none,
      # because it ends the investigation.
      row "$label" "WARN" "exit $rc — $(tail -n3 "$log" | tr '\n' ' ' | tr '|' '/' | cut -c1-80)"
      echo "[warn] $label (exit $rc)"
    fi
  fi
  rm -f "$log"
}

# skip <GROUP> -> true when that group's opt-out flag is set to 1. Each flag is
# named EXPLICITLY (rather than eval'ing a dynamically-built name) so the
# config-surface scanner sees the real per-group env vars, not a bare prefix it
# cannot ledger.
skip(){
  case "$1" in
    BROWSERS) [ "${BD_SKIP_BROWSERS:-0}" = "1" ] ;;
    CLOAK)    [ "${BD_SKIP_CLOAK:-0}"    = "1" ] ;;
    AUDIT)    [ "${BD_SKIP_AUDIT:-0}"    = "1" ] ;;
    NET)      [ "${BD_SKIP_NET:-0}"      = "1" ] ;;
    SECTOOLS) [ "${BD_SKIP_SECTOOLS:-0}" = "1" ] ;;
    EXTRAS)   [ "${BD_SKIP_EXTRAS:-0}"   = "1" ] ;;
    ARCHB)    [ "${BD_SKIP_ARCHB:-0}"    = "1" ] ;;
    *)        return 1 ;;
  esac
}

# sudo may be absent when already root; wrap it.
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
apt_i(){ $SUDO apt-get install -y -qq "$@"; }

# --- single source of truth for system packages -------------------------------
# scripts/lib/system_deps.sh is a SOURCEABLE fragment (no main, no side effects)
# shared by this script, install_linux.sh and scripts/provision_test_host.sh so
# the three can never disagree about what "the system deps" ARE. Three private
# copies of a package list is a denominator that drifts: each script then answers
# "are the deps present?" over its own idea of what they are, and every one can
# report OK while the host is missing something another considers mandatory
# (CLAUDE.md 0). The names are therefore deliberately NOT repeated below.
#
# It lives IN the repo, so it exists only when find_repo() succeeded, and it
# reaches an already-deployed host only on the next overlay. A missing or broken
# fragment must record a WARN and continue: this script deliberately runs without
# `set -e`, and dying here would destroy the report that is its whole point.
HAVE_SYSDEPS=0
if [ "$HAVE_REPO" != 1 ]; then
  row "system_deps fragment" "WARN" "no repo at setup time -- shared package groups unavailable"
elif [ ! -r "$REPO/scripts/lib/system_deps.sh" ]; then
  row "system_deps fragment" "WARN" "scripts/lib/system_deps.sh absent or unreadable -- shared package groups unavailable"
else
  # shellcheck source=scripts/lib/system_deps.sh
  . "$REPO/scripts/lib/system_deps.sh"
  # Sourcing cleanly proves NOTHING about what the file defined: `.` returns the
  # status of the last command it ran, and a file that parses fine while defining
  # nothing sources with exit 0 (CLAUDE.md 6 -- parsing is not name resolution).
  # Check the names, both of them, before trusting either.
  if declare -F bd_system_pkgs >/dev/null 2>&1 \
     && declare -F bd_start_display >/dev/null 2>&1; then
    HAVE_SYSDEPS=1
    row "system_deps fragment" "OK" "sourced scripts/lib/system_deps.sh (single dep denominator)"
  else
    row "system_deps fragment" "WARN" "sourced but bd_system_pkgs/bd_start_display undefined -- shared package groups unavailable"
  fi
fi

# Which checkout was provisioned, and how it was chosen. Without this a reader
# has to infer the tree from incidental evidence (a stack trace, a path in a log
# tail) -- and every row below is a claim ABOUT that tree.
if [ "$HAVE_REPO" = 1 ]; then
  row "repo location" "OK" "$REPO (located via ${REPO_VIA:-unknown})"
else
  row "repo location" "**FAILED**" "no checkout found; probed \$BD_REPO, \$CLAUDE_PROJECT_DIR, \$PWD and the conventional paths"
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

# Git identity. Every in-session commit must be attributable to Claude with the
# noreply@anthropic.com committer email, or the verified-commit stop-hook flags
# it as Unverified. Set --global so it holds before the repo is even located.
# (GitHub's own PR-merge commit is authored by GitHub and will still read
# Unverified -- that is expected and is NOT a local commit to rewrite.)
step "git identity" optional bash -c 'git config --global user.email noreply@anthropic.com && git config --global user.name Claude'

# ================================================================ 1. core app
# Everything here needs the checkout. If the repo is not present at setup time
# (a legitimate sequencing fact, not a provisioning failure) these are DEFERRED
# to the `bd-provision` helper installed at the end, and the report says so.
if [ "$HAVE_REPO" = 0 ]; then
  row "app provisioning" "**DEFERRED**" "repo not present at setup time -- run \`bd-provision\` once checked out"
  echo "[defer] app provisioning -- repo not found; run bd-provision after checkout"
else
  # Match the box/CI interpreter (Python 3.12), NOT the sandbox default python3
  # (3.11). This is load-bearing: regenerating graph/parity artifacts or running
  # the band under 3.11 SILENTLY diverges from the 3.12 box. Every AST tool here
  # swallows per-file SyntaxErrors, so a file that 3.11 cannot parse is dropped
  # from their denominators without a word -- which is what read green in the
  # sandbox but FAILED the box on v3.66.807 (the graph/index/import-graph
  # artifacts came out short). The file that carried the 3.12-only f-string was
  # retired at v3.66.824 and, measured over all 2097 tracked .py at that cut,
  # NO file now parses on 3.12 but not 3.11. Do not read that as the rule being
  # obsolete: it is one 3.12-only syntax away from returning, and it returns
  # SILENTLY. requirements
  # are already proven on 3.12 (the box runs them), so 3.12 is strictly more
  # faithful. Fall back to python3 only if 3.12 is genuinely absent.
  PYBIN="$(command -v python3.12 || command -v python3)"
  # Guard against a stale ./venv on the WRONG interpreter (a 3.11 venv carried
  # over from a prior provisioning). `python3.12 -m venv venv` will NOT relocate
  # an existing interpreter binary -- the dir keeps its old python -- so remove it
  # first and rebuild genuinely on 3.12. This is the ROOT CAUSE of the /tmp/venv312
  # workaround seen through v3.66.811: ./venv came up 3.11, so every graph/parity
  # regen had to dodge it. With this guard, ./venv/bin/python IS the box 3.12.
  if [ -x ./venv/bin/python ] && command -v python3.12 >/dev/null 2>&1 \
     && ! ./venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2]==(3,12) else 1)' 2>/dev/null; then
    row "venv rebuild" "OK" "removed stale $(./venv/bin/python --version 2>&1) venv; rebuilding on 3.12"
    rm -rf venv
  fi
  row "python interp" "OK" "venv built on $("$PYBIN" --version 2>&1) (box/CI parity)"
  step "python venv"  core     "$PYBIN" -m venv venv
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

  # npm ci installs the TOOLCHAIN. It does not produce frontend/dist, and that
  # directory is gitignored with ZERO tracked files -- so `git reset --hard`
  # never delivers it and a fresh container has none (CLAUDE.md section 7).
  # Two tests then fail and neither names the cause:
  #   test_v3_66_790_nuitka_config::test_data_dirs_all_exist_in_tree
  #       -> "declared data dir does not exist: frontend/dist"
  #   test_phase1_root_flip::test_missing_asset_is_404_not_spa_html
  #       -> 503, because bulk_downloader/app.py cannot serve an absent bundle
  # Measured here: both fail before this step and pass after it, nothing else
  # changed. They were the last two failures a session had to wave away as
  # "environmental", so building the bundle is also what turns a future
  # occurrence into real signal instead of noise.
  step "frontend build" core bash -c 'cd frontend && npm run build'

  # Exit 0 is NOT the property. `tsc -b && vite build` can exit 0 having
  # written nothing the app can serve, and the thing every consumer needs is
  # the entry point. Read the artifact back -- the same discipline as step
  # [7]'s route_source read-back and the graph pin's --check-hash. A
  # provisioner that trusts an exit code reports a green host for a container
  # whose asset routes 503.
  if [ -f frontend/dist/index.html ]; then
    row "frontend bundle" "OK" "frontend/dist/index.html present ($(du -sh frontend/dist 2>/dev/null | cut -f1 | tr -d ' '))"
  else
    row "frontend bundle" "**FAILED**" "npm run build exited 0 but frontend/dist/index.html is absent -- the SPA cannot be served and asset routes 503"
    echo "[FAIL] frontend bundle missing after build"
    CORE_FAILED=1
  fi
fi

# ================================================================ 2. browsers
if skip BROWSERS; then
  # Say which is true, rather than assuming the worst. The old text asserted
  # "capture/recognizer/e2e CANNOT run" for every skip -- false on a host where
  # the browsers are preinstalled, which is the normal case here
  # (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers, populated). A WARN that
  # overstates is how a reader learns to skim WARNs; over-sensitivity is a
  # soundness bug, not a safe default.
  if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ] && [ -d "${PLAYWRIGHT_BROWSERS_PATH:-/nonexistent}" ] \
     && [ -n "$(ls -A "${PLAYWRIGHT_BROWSERS_PATH}" 2>/dev/null)" ]; then
    row "browsers" "OK" "skipped via BD_SKIP_BROWSERS; preinstalled at $PLAYWRIGHT_BROWSERS_PATH"
  else
    row "browsers" "WARN" "skipped via BD_SKIP_BROWSERS and no preinstalled pool found -- capture/recognizer/e2e CANNOT run"
  fi
else
  if [ "$HAVE_REPO" = 1 ]; then
    # The engine names come from scripts/lib/system_deps.sh, sourced above --
    # this script, install_linux.sh and scripts/provision_test_host.sh all
    # install browsers, and a private copy of the list in each is the drift the
    # fragment exists to stop. The core/extra split is preserved rather than
    # collapsed into one command: a combined install reports ONE exit status, so
    # a webkit mirror failure would be graded exactly like losing chromium.
    _pw_core=""
    _pw_extra=""
    if declare -F bd_playwright_engines >/dev/null 2>&1; then
      _pw_core="$(bd_playwright_engines core)"   || _pw_core=""
      _pw_extra="$(bd_playwright_engines extra)" || _pw_extra=""
    fi
    if [ -z "$_pw_core" ] || [ -z "$_pw_extra" ]; then
      # UNKNOWN, not "none". `playwright install` with no engine argument
      # installs every default browser and exits 0, so running it on an empty
      # list would report a success nobody asked for.
      row "browsers" "WARN" "bd_playwright_engines undefined -- engine list UNKNOWN, nothing installed"
    else
      # shellcheck disable=SC2086  # word splitting is the point: one arg per engine
      step "playwright core"  optional ./venv/bin/python -m playwright install --with-deps $_pw_core
      # shellcheck disable=SC2086
      step "playwright extra" optional ./venv/bin/python -m playwright install $_pw_extra
    fi
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
  # The lint tooling comes from the shared fragment (group `lint`) rather than
  # being named inline: it is now a fragment-owned package, and a second copy
  # here is exactly the drift the fragment exists to end. fd-find is NOT
  # fragment-owned -- nothing else installs it -- so it stays local to this step.
  # (Do not begin this comment with the linter's own name; a comment whose first
  # word after "# " is that name parses as a malformed directive and aborts the
  # whole file -- the SC1073/SC1072 regression this branch already fixed once.)
  if [ "$HAVE_SYSDEPS" = 1 ]; then
    LINT_PKGS="$(bd_system_pkgs lint)" || LINT_PKGS=""
  else
    LINT_PKGS=""
  fi
  if [ -n "$LINT_PKGS" ]; then
    # Word splitting is the point: one arg per package.
    # shellcheck disable=SC2086
    step "fd+lint tools" optional apt_i fd-find $LINT_PKGS
  else
    step "fd" optional apt_i fd-find
    row "lint tools" "WARN" "bd_system_pkgs lint unavailable -- shellcheck not installed, so the suite's parse gates will report themselves unrunnable"
  fi
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
  #
  # The list is bd_system_pkgs' to own; naming it again here would make this file
  # a second opinion. Capture into a variable and refuse an empty one: command
  # substitution DISCARDS the function's non-zero exit, and `apt-get install`
  # with zero package arguments exits 0 -- so the obvious one-liner would install
  # nothing after a failed lookup and step() would record OK (CLAUDE.md 0).
  if [ "$HAVE_SYSDEPS" = 1 ]; then
    GTK_PKGS="$(bd_system_pkgs gtk)" || GTK_PKGS=""
    if [ -n "$GTK_PKGS" ]; then
      # Word splitting is the point: one arg per package.
      # shellcheck disable=SC2086
      step "GTK + Xvfb" optional apt_i $GTK_PKGS
    else
      row "GTK + Xvfb" "WARN" "bd_system_pkgs gtk returned nothing -- refusing to run apt on an empty package list"
    fi
  else
    row "GTK + Xvfb" "WARN" "system_deps fragment unavailable -- the GTK/display packages are named only there, so they were NOT installed; the module-import gate cannot run"
  fi
  step "misc tooling" optional apt_i pypy3 caddy postgresql-client patchelf
  step "profiling"    optional ./venv/bin/pip install -q py-spy
fi

# ========================================================= 7b. arch b (kasmvnc)
# MOD-1 remote_vnc captcha takeover: the KasmVNC display server + web client.
# Deps are ordinary X libs (no kernel module), so it installs on any host. Two
# pieces are load-bearing and were learned by breaking them:
#   - kasmvncserver reads /etc/ssl/private/ssl-cert-snakeoil.key at startup and
#     DIES (no log, no display) if the invoking user cannot read it -- the key is
#     root:ssl-cert 640, so generate it AND add the user to the ssl-cert group.
#   - the github .deb is repo-scope blocked in the Claude sandbox (403) -> this is
#     `optional`, so a blocked pull degrades to a WARN instead of failing the run;
#     on a normal host (stash) it installs.
if skip ARCHB; then
  row "arch b (kasmvnc)" "WARN" "skipped via BD_SKIP_ARCHB -- remote_vnc takeover CANNOT run"
else
  step "kasmvnc" optional bash -c "command -v kasmvncserver >/dev/null || { curl -sSLf -o /tmp/kasm.deb https://github.com/kasmtech/KasmVNC/releases/download/v1.4.0/kasmvncserver_noble_1.4.0_amd64.deb && $SUDO apt-get install -y /tmp/kasm.deb; }"
  step "snakeoil cert"  optional bash -c "$SUDO make-ssl-cert generate-default-snakeoil --force-overwrite"
  step "ssl-cert group" optional bash -c "$SUDO usermod -aG ssl-cert \"\$(id -un)\""
fi

# ==================================================== 7c. reconcile inventories
# gui_parity_inventory.json is gitignored and build-time generated; the
# gui-parity RECONCILE gate compares the shipped inventory to a fresh regen, so a
# stale one (any new tool / route / env var, e.g. BD_SKIP_ARCHB above) reads as
# drift and fails the full suite. Regenerate it HERE, in the deploy venv, so the
# shipped artifact matches a same-environment regen by construction. Needs the app
# venv (full deps), so it runs after the browser/dep steps.
if [ "$HAVE_REPO" = 1 ] && [ -x ./venv/bin/python ]; then
  step "gui-parity inventory" optional ./venv/bin/python tools/gui_parity_inventory.py
fi

# ================================================================ 8. runtime
mkdir -p "${BD_HOME:-/tmp/bd_home}" 2>/dev/null \
  && row "BD_HOME" "OK" "${BD_HOME:-/tmp/bd_home} (outside the repo, as required)" \
  || row "BD_HOME" "WARN" "could not create ${BD_HOME:-/tmp/bd_home}"

# Idempotency lives in bd_start_display, in one place, for the same reason the
# package list does. The check this replaces was `pgrep -x Xvfb`, which tests a
# PROCESS NAME while the subject is a DISPLAY -- wrong in both directions: an X
# server on :0 made it report ":99 is up" when nothing held :99, and a non-Xvfb
# server on :99 (kasmvnc, installed in 7b above) made it report ":99 is free"
# when it was not, which is how "Fatal server error: Server is already active
# for display 99" happens. The old row was also unconditional: `(cmd &)` inside
# `||` reports the fork, never the server, so it wrote OK for a display that
# might not exist. Called DIRECTLY, never through step(): step() redirects
# stdout into a temp log and would swallow the DISPLAY value it echoes.
if [ "$HAVE_SYSDEPS" = 1 ]; then
  if DISPLAY_VALUE="$(bd_start_display :99)"; then
    export DISPLAY="$DISPLAY_VALUE"
    row "Xvfb :99" "OK" "display $DISPLAY_VALUE active — export DISPLAY=$DISPLAY_VALUE for the GTK gate"
  else
    row "Xvfb :99" "WARN" "no X display could be provided — test_v3_43_80_modules::test_all_modules_import WILL false-fail"
  fi
else
  row "Xvfb :99" "WARN" "system_deps fragment unavailable -- bd_start_display undefined; no display started"
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
   "tools/build_release.py":"be25241eb867b85a"}
bad=[f for f,w in G.items() if hashlib.sha256(open(f,'rb').read()).hexdigest()[:16]!=w]
print(f"{len(G)-len(bad)}/{len(G)} guard files match")
sys.exit(1 if bad else 0)
PY2

  # `pip install -r` exiting 0 is not proof that every requirement is present:
  # this container reported "runtime deps OK" while beautifulsoup4 and
  # pytest-xdist were both absent, and pytest-xdist is what capture.sh's
  # --workers lane needs. `pip check` cannot see this -- its denominator is the
  # set of INSTALLED packages, which structurally excludes an uninstalled
  # requirement. Parse the requirements file and ask for each name.
  # The parse-and-resolve logic now lives in tools/check_requirements.py, so this
  # provisioner and scripts/deploy.sh ask the IDENTICAL question of the IDENTICAL
  # interpreter. CLAUDE.md section 5 records what three inlined copies of a
  # package list cost: they drift, and the copy nobody updated is the one the box
  # runs. That applies to this check exactly as it did to system_deps.sh.
  #
  # It must run AS ./venv/bin/python -- importlib.metadata answers for the
  # interpreter executing it, and this container has three that disagree.
  #
  #   exit 0  everything resolves; stdout silent
  #   exit 1  the unresolved names, space-separated, on stdout
  #   exit 2  UNEVALUABLE -- unreadable requirements.txt, or the helper absent on
  #           an older tree. NOT a softer exit 0.
  REQ_MISSING=""
  REQ_RC=0
  REQ_MISSING="$(./venv/bin/python tools/check_requirements.py 2>/dev/null)" || REQ_RC=$?

  if [ "$REQ_RC" -eq 2 ]; then
    # Unknown is a third state and it fails. "Could not evaluate" must never be
    # rendered as "satisfied".
    row "requirements satisfied" "**FAILED**" "could not evaluate requirements.txt -- treat as NOT satisfied"
    echo "[FAIL] requirements satisfied (unevaluable)"; CORE_FAILED=1
  elif [ "$REQ_RC" -ne 0 ] || [ -n "$REQ_MISSING" ]; then
    row "requirements satisfied" "**FAILED**" "MISSING: $(echo "$REQ_MISSING" | cut -c1-70)"
    echo "[FAIL] requirements missing: $REQ_MISSING"; CORE_FAILED=1
  else
    row "requirements satisfied" "OK" "every requirements.txt entry resolves in the venv"
    echo "[ ok ] requirements satisfied"
  fi
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
  # ffprobe is a SEPARATE binary and is what the media-integrity path actually
  # shells out to. Probing only ffmpeg answers a question nobody asked.
  printf '%-15s %s\n' "ffprobe"       "$(ffprobe -version 2>/dev/null | head -1 | cut -d' ' -f3 || echo absent)"
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
# Build on 3.12 to match the box/CI (the 3.11 sandbox default diverges on
# artifact regen -- see cloud-setup.sh's "python interp" note). 3.12 or bust-to-3.11.
PYBIN="$(command -v python3.12 || command -v python3)"
echo "python interp: $("$PYBIN" --version 2>&1)"
"$PYBIN" -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt || exit 1
./venv/bin/pip install -q "pytest>=7.0,<9.0" pyflakes
[ "${NODE_ENV:-}" = "production" ] && { echo "FATAL: NODE_ENV=production omits devDependencies"; exit 1; }
( cd frontend && npm ci --no-audit --no-fund ) || echo "WARN: frontend deps failed"
# Browsers. This heredoc body is a STANDALONE script installed into ~/bin, so
# it sources the fragment itself rather than inheriting it -- but it has just
# cd'd into the checkout, so the path is known. The engine names are never
# written here: this file, install_linux.sh and scripts/provision_test_host.sh
# all install browsers, and a fourth private copy of the list is exactly the
# drift scripts/lib/system_deps.sh exists to stop.
_pw_core=""
_pw_extra=""
# shellcheck source=/dev/null
if [ -r "$R/scripts/lib/system_deps.sh" ] && . "$R/scripts/lib/system_deps.sh" \
   && declare -F bd_playwright_engines >/dev/null 2>&1; then
  _pw_core="$(bd_playwright_engines core)"   || _pw_core=""
  _pw_extra="$(bd_playwright_engines extra)" || _pw_extra=""
fi
if [ -z "$_pw_core" ]; then
  # UNKNOWN, and it is named. `playwright install` with no engine argument
  # installs every default browser and exits 0, so guessing here would report
  # a success the operator never asked for.
  echo "WARN: bd_playwright_engines unavailable -- no browser installed"
else
  # shellcheck disable=SC2086
  ./venv/bin/python -m playwright install --with-deps $_pw_core || echo "WARN: $_pw_core failed"
  # shellcheck disable=SC2086
  [ -n "$_pw_extra" ] && { ./venv/bin/python -m playwright install $_pw_extra || echo "WARN: $_pw_extra failed (optional; live check L4 will report the install as incomplete)"; }
fi
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
  echo "host), and the \`bd-*\` toolchain -- a substantial share of those tools"
  echo "hardcode sandbox paths and need porting, which no amount of provisioning"
  echo "fixes. (Deliberately unquantified: the previous text carried a hardcoded"
  echo "'155 of 249' that matched no measurable count and had no way to stay true."
  echo "Measure it at decision time.)"
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
# A run that found no checkout installed no venv, no deps and no browsers. It is
# NOT a success, and it must not share an exit code with one -- the caller has
# only the code to go on, and the "APP DEFERRED" explanation is prose no machine
# reads. Distinct code 3 so a wrapper can tell "nothing to provision against"
# from "provisioned, and it worked".
if [ "$CORE_FAILED" -eq 1 ]; then
  exit 1
elif [ "$HAVE_REPO" = 0 ]; then
  exit 3
fi
exit 0
