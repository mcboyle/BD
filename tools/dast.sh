#!/usr/bin/env bash
# =============================================================================
# dast.sh — Bulk Downloader dynamic analysis pipeline (POSIX)
# =============================================================================
# Runs the in-process probe (tools/dast_probe.py) PLUS — if available —
# optional external scanners against a locally-launched dev server:
#   • OWASP ZAP baseline scan (Docker)
#   • Nikto
#   • Wapiti
#   • sqlmap quick scan against known-parameterised endpoints
#
# All findings go to tools/dast_results/.
#
# Exit code:
#   0 — clean
#   1 — findings present
#   2 — setup error
# =============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# ─── Flags ───────────────────────────────────────────────────────────────
# --probe-only: skip the external scanners (ZAP/Nikto/Wapiti/sqlmap).
#               Useful for fast CI gating where only the in-process probe
#               matters.
PROBE_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --probe-only) PROBE_ONLY=1 ;;
        -h|--help)
            echo "Usage: $0 [--probe-only]"
            echo "  --probe-only   Skip external scanners (ZAP, Nikto, Wapiti, sqlmap)."
            exit 0 ;;
    esac
done

OUT="$REPO_DIR/tools/dast_results"
mkdir -p "$OUT"
rm -f "$OUT"/*.txt "$OUT"/*.json "$OUT"/*.html 2>/dev/null

# ─── Locate python ────────────────────────────────────────────────────────
# Shared ladder -- this script used to probe only "$REPO_DIR/.venv/bin/python"
# (absent here) and fall through to bare python3, i.e. 3.11 without the project
# dependencies. See scripts/lib/python_resolve.sh.
PY=""
# shellcheck source=scripts/lib/python_resolve.sh
if [ -r "$REPO_DIR/scripts/lib/python_resolve.sh" ]; then
    . "$REPO_DIR/scripts/lib/python_resolve.sh"
    if bd_resolve_python "$REPO_DIR"; then PY="$BD_PYTHON_RESOLVED"; fi
fi
if [ -z "$PY" ]; then
    echo "[dast] ERROR: no Python found. Activate venv or install Python 3.10+."
    exit 2
fi
echo "[dast] Python: $PY"

FINDINGS=0

# ============================================================================
echo
echo "[dast] === 1/6 In-process probe (dast_probe.py) ==="
# ============================================================================
# Fast, deterministic, no external deps. Catches XSS, path traversal,
# config-validation gaps, SQL injection, response-time anomalies.
BD_DISABLE_KEEPALIVE=1 "$PY" "$REPO_DIR/tools/dast_probe.py" \
    > "$OUT/probe.txt" 2>&1
PROBE_EXIT=$?
if [ "$PROBE_EXIT" -eq 0 ]; then
    echo "  probe: clean (0 bugs)"
else
    echo "  probe: findings present (see probe.txt)"
    FINDINGS=$((FINDINGS+1))
fi
# Show summary line
grep -E "^DAST:" "$OUT/probe.txt" 2>/dev/null || true

# ── --probe-only short-circuit ────────────────────────────────────────────
if [ "$PROBE_ONLY" -eq 1 ]; then
    echo
    echo "[dast] --probe-only: skipping external scanners (ZAP/Nikto/Wapiti/sqlmap)"
    echo
    echo "Reports written to: $OUT"
    if [ "$FINDINGS" -gt 0 ]; then
        echo "[dast] $FINDINGS finding categories. Review tools/dast_results/."
        exit 1
    else
        echo "[dast] Clean (probe-only)."
        exit 0
    fi
fi

# ============================================================================
echo
echo "[dast] === 2/6 Launch the app for external scanners ==="
# ============================================================================
# Start the dev server on 127.0.0.1:9876 in the background. External
# scanners point here.
APP_PORT="${BD_DAST_PORT:-9876}"
APP_HOST="127.0.0.1"
APP_URL="http://${APP_HOST}:${APP_PORT}"

# Boot the app in a tmp BD_HOME so we don't pollute the user's data
APP_HOME="$(mktemp -d -t bd_dast_app_XXXXXX)"
echo "  starting app on $APP_URL (BD_HOME=$APP_HOME)..."
BD_HOME="$APP_HOME" BD_HOST="$APP_HOST" BD_PORT="$APP_PORT" \
    BD_DISABLE_KEEPALIVE=1 \
    "$PY" downloader_ui.py > "$OUT/server.log" 2>&1 &
APP_PID=$!
trap "kill $APP_PID 2>/dev/null; rm -rf $APP_HOME" EXIT

# Wait for the server to come up (5s timeout)
for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -m 1 "$APP_URL/" >/dev/null 2>&1; then
        echo "  app responsive after ${i}*0.5s"
        break
    fi
    sleep 0.5
    if [ "$i" -eq 10 ]; then
        echo "  WARN: app did not respond on $APP_URL within 5s"
        echo "  external scanners will be SKIPPED"
        cat "$OUT/server.log" | tail -10
        APP_PID=""
    fi
done

# ============================================================================
echo
echo "[dast] === 3/6 OWASP ZAP baseline scan ==="
# ============================================================================
# Uses the official ZAP Docker image. ~2 min for a baseline pass.
if [ -n "$APP_PID" ] && command -v docker >/dev/null 2>&1; then
    docker pull -q ghcr.io/zaproxy/zaproxy:stable >/dev/null 2>&1 \
        || docker pull -q owasp/zap2docker-stable >/dev/null 2>&1
    docker run --rm --network host \
        -v "$OUT:/zap/wrk:rw" \
        ghcr.io/zaproxy/zaproxy:stable \
        zap-baseline.py -t "$APP_URL" \
        -r zap-report.html -J zap-report.json \
        > "$OUT/zap.txt" 2>&1 || true
    if [ -s "$OUT/zap-report.json" ]; then
        echo "  zap: report saved to zap-report.html"
        # Count HIGH alerts
        HIGH=$(grep -c '"riskcode":"3"' "$OUT/zap-report.json" 2>/dev/null || echo 0)
        if [ "$HIGH" -gt 0 ]; then
            echo "  zap: $HIGH HIGH-severity alerts"
            FINDINGS=$((FINDINGS+1))
        else
            echo "  zap: no HIGH alerts"
        fi
    else
        echo "  zap: scan failed (see zap.txt)"
    fi
else
    echo "  zap: SKIPPED (Docker not installed or app not running)" > "$OUT/zap.txt"
    echo "  zap: SKIPPED (Docker not installed)"
fi

# ============================================================================
echo
echo "[dast] === 4/6 Nikto web server scan ==="
# ============================================================================
if [ -n "$APP_PID" ] && command -v nikto >/dev/null 2>&1; then
    nikto -h "$APP_URL" -nointeractive -ask no \
        -output "$OUT/nikto.txt" 2>&1 | tail -30
    if grep -qi "vulnerab" "$OUT/nikto.txt" 2>/dev/null; then
        echo "  nikto: findings present"
        FINDINGS=$((FINDINGS+1))
    else
        echo "  nikto: clean"
    fi
else
    echo "  nikto: SKIPPED (not installed)" > "$OUT/nikto.txt"
    echo "  nikto: SKIPPED (install with: apt install nikto)"
fi

# ============================================================================
echo
echo "[dast] === 5/6 sqlmap targeted probe ==="
# ============================================================================
# Target a known parametrised endpoint to confirm no SQLi surface. The
# code uses sqlite3 with ? placeholders everywhere — this is a sanity
# check, not an attack.
if [ -n "$APP_PID" ] && command -v sqlmap >/dev/null 2>&1; then
    sqlmap -u "$APP_URL/api/logs/tail?lines=10" \
        --batch --level=2 --risk=2 \
        --output-dir="$OUT/sqlmap" \
        > "$OUT/sqlmap.txt" 2>&1 || true
    if grep -qE "is vulnerable|injectable" "$OUT/sqlmap.txt" 2>/dev/null; then
        echo "  sqlmap: SQL INJECTION FOUND"
        FINDINGS=$((FINDINGS+1))
    else
        echo "  sqlmap: no injection found"
    fi
else
    echo "  sqlmap: SKIPPED (not installed)" > "$OUT/sqlmap.txt"
    echo "  sqlmap: SKIPPED (install with: apt install sqlmap)"
fi

# ============================================================================
echo
echo "[dast] === 6/6 Wapiti active fuzzer ==="
# ============================================================================
# Wapiti is a pip-installable active web fuzzer. Covers XSS, SQLi, CRLF,
# file-disclosure, command-injection, weak credentials. ~3-5 min full
# scan; we cap at the routes the spider can reach from / (no auth, no
# cookie file).
if [ -n "$APP_PID" ]; then
    if ! "$PY" -m wapiti --version >/dev/null 2>&1; then
        "$PY" -m pip install --quiet --disable-pip-version-check \
            'wapiti3>=3.1' 2>>"$OUT/wapiti-install.log" || true
    fi
    if "$PY" -m wapiti --version >/dev/null 2>&1; then
        "$PY" -m wapiti -u "$APP_URL" \
            -m xss,sql,exec,file,redirect,permanentxss,htaccess,xxe \
            -f txt -o "$OUT/wapiti.txt" \
            --flush-session --max-scan-time 240 \
            > "$OUT/wapiti.log" 2>&1 || true
        if grep -qE "(High|Critical)" "$OUT/wapiti.txt" 2>/dev/null; then
            echo "  wapiti: High/Critical findings present"
            FINDINGS=$((FINDINGS+1))
        elif [ -s "$OUT/wapiti.txt" ]; then
            echo "  wapiti: no High/Critical findings"
        else
            echo "  wapiti: scan produced no output (see wapiti.log)"
        fi
    else
        echo "  wapiti: SKIPPED (install failed — see wapiti-install.log)" > "$OUT/wapiti.txt"
        echo "  wapiti: SKIPPED (install failed)"
    fi
else
    echo "  wapiti: SKIPPED (app not running)" > "$OUT/wapiti.txt"
    echo "  wapiti: SKIPPED (app not running)"
fi

# Stop the app
if [ -n "$APP_PID" ]; then
    kill "$APP_PID" 2>/dev/null
    wait "$APP_PID" 2>/dev/null
fi

# ─── Summary ──────────────────────────────────────────────────────────────
echo
echo "[dast] === Summary ==="
{
    echo "Bulk Downloader DAST run — $(date)"
    echo "=========================================="
    echo
    echo "--- probe ---"
    grep -E "^\s*[✓⚠✗🛑·]" "$OUT/probe.txt" 2>/dev/null | head -100
    echo
    for f in zap nikto sqlmap wapiti; do
        if [ -s "$OUT/$f.txt" ]; then
            echo "--- $f ---"
            head -50 "$OUT/$f.txt"
            echo
        fi
    done
    echo
    echo "Findings categories with hits: $FINDINGS"
} > "$OUT/SUMMARY.txt"

echo "Reports written to: $OUT"
echo
if [ "$FINDINGS" -gt 0 ]; then
    echo "[dast] $FINDINGS finding categories. Review tools/dast_results/."
    exit 1
else
    echo "[dast] Clean."
    exit 0
fi
