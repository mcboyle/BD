#!/usr/bin/env bash
# =============================================================================
# sast.sh — Bulk Downloader static analysis pipeline (POSIX)
# =============================================================================
# Runs every available SAST tool and writes per-tool reports + a combined
# summary under tools/sast_results/.
#
# Auto-installs each tool via pip on first run if missing.
#
# Exit code:
#   0 — clean
#   1 — findings present (see tools/sast_results/SUMMARY.txt)
#   2 — setup error
# =============================================================================
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Locate python via the SHARED ladder. This script used to probe only
# "$REPO_DIR/.venv/bin/python" -- absent in this repo -- and fall through to
# bare python3, which is 3.11 without the project dependencies. See
# scripts/lib/python_resolve.sh.
PY=""
# shellcheck source=scripts/lib/python_resolve.sh
if [ -r "$REPO_DIR/scripts/lib/python_resolve.sh" ]; then
    . "$REPO_DIR/scripts/lib/python_resolve.sh"
    if bd_resolve_python "$REPO_DIR"; then
        PY="$BD_PYTHON_RESOLVED"
    fi
fi
if [ -z "$PY" ]; then
    echo "[sast] ERROR: no Python found. Activate venv or install Python 3.10+."
    exit 2
fi
echo "[sast] Python: $PY"

OUT="$REPO_DIR/tools/sast_results"
mkdir -p "$OUT"
CLEANUP_OK=1
if ! rm -f "$OUT"/*.txt "$OUT"/*.json "$OUT"/*.sarif 2>/dev/null; then
    CLEANUP_OK=0
fi

FINDINGS=0
ERRORS=0
SCANNER_LAST_OK=1
SCANNER_LAST_STATUS=0

scanner_error() {
    ERRORS=$((ERRORS+1))
    echo "  scanner error: $*" >&2
}

if [ "$CLEANUP_OK" -ne 1 ]; then
    scanner_error "could not remove stale reports before scanning"
fi

record_scanner_status() {
    label=$1; max_measured_status=$2; status=$3
    SCANNER_LAST_STATUS=$status
    SCANNER_LAST_OK=1
    if [ "$SCANNER_LAST_STATUS" -gt "$max_measured_status" ]; then
        SCANNER_LAST_OK=0
        scanner_error "$label exited $SCANNER_LAST_STATUS"
    fi
}

# Scanner CLIs use 1 for a measured finding set and >=2 for execution/setup
# failures. Callers pass 0 for tools configured with an unconditional-zero
# mode such as Ruff's --exit-zero.
run_scanner() {
    label=$1; max_measured_status=$2; shift 2
    "$@"
    status=$?
    record_scanner_status "$label" "$max_measured_status" "$status"
    return 0
}

# Keep output redirection inside the helper. If opening/truncating the report
# fails, the helper still records that status instead of leaving stale globals
# from the preceding scanner.
run_scanner_to_file() {
    label=$1; max_measured_status=$2; output=$3; shift 3
    if ! exec 3> "$output"; then
        record_scanner_status "$label" "$max_measured_status" 125
        return 0
    fi
    "$@" >&3 2>&1
    status=$?
    exec 3>&-
    record_scanner_status "$label" "$max_measured_status" "$status"
    return 0
}

status_count_ok() {
    count=$1
    { [ "$SCANNER_LAST_STATUS" -eq 0 ] && [ "$count" -eq 0 ]; } || \
        { [ "$SCANNER_LAST_STATUS" -eq 1 ] && [ "$count" -gt 0 ]; }
}

json_count() {
    "$PY" - "$1" "$2" <<'PY'
import json
import sys

kind, path = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    value = json.load(handle)
if kind in ("bandit", "semgrep"):
    if (not isinstance(value, dict)
            or not isinstance(value.get("results"), list)
            or "errors" not in value
            or not isinstance(value["errors"], list)
            or value["errors"]):
        raise ValueError("incomplete scanner report")
    count = len(value["results"])
elif kind == "audit":
    dependencies = value if isinstance(value, list) else (
        value.get("dependencies") if isinstance(value, dict) else None
    )
    if not isinstance(dependencies, list):
        raise ValueError("invalid dependency list")
    count = 0
    for dependency in dependencies:
        if (not isinstance(dependency, dict)
                or "vulns" not in dependency
                or not isinstance(dependency["vulns"], list)):
            raise ValueError("invalid vulnerability list")
        count += len(dependency["vulns"])
elif kind == "ruff":
    if not isinstance(value, list):
        raise ValueError("invalid Ruff result list")
    count = len(value)
elif kind == "eslint":
    if not isinstance(value, list):
        raise ValueError("invalid ESLint file list")
    count = 0
    for result in value:
        if not isinstance(result, dict) or not isinstance(result.get("messages"), list):
            raise ValueError("invalid ESLint message list")
        count += len(result["messages"])
elif kind == "secrets":
    if not isinstance(value, dict) or not isinstance(value.get("results"), dict):
        raise ValueError("invalid detect-secrets result map")
    if not all(isinstance(items, list) for items in value["results"].values()):
        raise ValueError("invalid detect-secrets finding list")
    count = sum(len(items) for items in value["results"].values())
else:
    raise ValueError("unknown scanner report kind")
print(count)
PY
}

format_json_report() {
    if ! "$PY" -m json.tool "$1" > "$2" 2>&1; then
        scanner_error "$3 report could not be formatted"
        return 1
    fi
    return 0
}

echo
echo "[sast] Ensuring SAST tools are installed..."
if "$PY" -m pip install --quiet --disable-pip-version-check \
        'bandit>=1.7' 'semgrep>=1.50' 'pip-audit>=2.6' 'ruff>=0.4' \
        'detect-secrets>=1.4' >"$OUT/python-install.log" 2>&1; then
    grep -v 'DEPRECATION\|already satisfied' "$OUT/python-install.log" || true
else
    install_status=$?
    scanner_error "Python SAST dependency install exited $install_status"
fi

# ─── 1/7 Bandit ───────────────────────────────────────────────────────────
echo
echo "[sast] === 1/7 Bandit (Python security linter) ==="
BANDIT_OK=1
run_scanner "Bandit JSON scan" 1 "$PY" -m bandit -r bulk_downloader -ll \
    -f json -o "$OUT/bandit.json" 2>/dev/null
[ "$SCANNER_LAST_OK" -eq 1 ] || BANDIT_OK=0
BN=0
if [ "$BANDIT_OK" -eq 1 ]; then
    if ! BN=$(json_count bandit "$OUT/bandit.json" 2>/dev/null); then
        BANDIT_OK=0
        scanner_error "Bandit did not produce a complete JSON report"
    elif ! status_count_ok "$BN"; then
        BANDIT_OK=0
        scanner_error "Bandit status/report mismatch (status $SCANNER_LAST_STATUS, count $BN)"
    elif ! format_json_report "$OUT/bandit.json" "$OUT/bandit.txt" Bandit; then
        BANDIT_OK=0
    fi
fi
if [ "$BN" -gt 0 ]; then
    echo "  bandit findings: $BN"; FINDINGS=$((FINDINGS+BN))
elif [ "$BANDIT_OK" -ne 1 ]; then
    echo "  bandit: unavailable"
else
    echo "  bandit: clean"
fi

# ─── 2/7 Semgrep ──────────────────────────────────────────────────────────
echo
echo "[sast] === 2/7 Semgrep (security rule packs) ==="
# First run needs internet for rule packs; subsequent runs use the cache
# under ~/.semgrep
SEMGREP_OK=1
run_scanner "Semgrep JSON scan" 1 "$PY" -m semgrep \
    --config p/security-audit \
    --config p/python \
    --config p/javascript \
    --config p/owasp-top-ten \
    --severity ERROR --severity WARNING \
    --error --json --output "$OUT/semgrep.json" \
    --quiet --exclude tests --exclude __pycache__ \
    bulk_downloader >/dev/null 2>&1
[ "$SCANNER_LAST_OK" -eq 1 ] || SEMGREP_OK=0
SG_FINDINGS=0
if [ "$SEMGREP_OK" -eq 1 ]; then
    if ! SG_FINDINGS=$(json_count semgrep "$OUT/semgrep.json" 2>/dev/null); then
        SEMGREP_OK=0
        scanner_error "Semgrep did not produce a complete JSON report"
    elif ! status_count_ok "$SG_FINDINGS"; then
        SEMGREP_OK=0
        scanner_error "Semgrep status/report mismatch (status $SCANNER_LAST_STATUS, count $SG_FINDINGS)"
    elif ! format_json_report "$OUT/semgrep.json" "$OUT/semgrep.txt" Semgrep; then
        SEMGREP_OK=0
    fi
fi
if [ "$SG_FINDINGS" -gt 0 ]; then
    echo "  semgrep findings: $SG_FINDINGS"
    FINDINGS=$((FINDINGS+SG_FINDINGS))
elif [ "$SEMGREP_OK" -ne 1 ]; then
    echo "  semgrep: unavailable"
else
    echo "  semgrep: clean"
fi

# ─── 3/7 pip-audit ────────────────────────────────────────────────────────
echo
echo "[sast] === 3/7 pip-audit (dependency CVE scan) ==="
PIP_AUDIT_OK=1
run_scanner "pip-audit JSON scan" 1 "$PY" -m pip_audit -r requirements.txt \
    --desc on --format json --output "$OUT/pip-audit.json" 2>/dev/null
[ "$SCANNER_LAST_OK" -eq 1 ] || PIP_AUDIT_OK=0
AUDIT_COUNT=0
if [ "$PIP_AUDIT_OK" -eq 1 ]; then
    if ! AUDIT_COUNT=$(json_count audit "$OUT/pip-audit.json" 2>/dev/null); then
        PIP_AUDIT_OK=0
        scanner_error "pip-audit did not produce a complete JSON report"
    elif ! status_count_ok "$AUDIT_COUNT"; then
        PIP_AUDIT_OK=0
        scanner_error "pip-audit status/report mismatch (status $SCANNER_LAST_STATUS, count $AUDIT_COUNT)"
    elif ! format_json_report "$OUT/pip-audit.json" "$OUT/pip-audit.txt" pip-audit; then
        PIP_AUDIT_OK=0
    fi
fi
if [ "$AUDIT_COUNT" -gt 0 ]; then
    echo "  pip-audit vulnerabilities: $AUDIT_COUNT"
    FINDINGS=$((FINDINGS+AUDIT_COUNT))
elif [ "$PIP_AUDIT_OK" -ne 1 ]; then
    echo "  pip-audit: unavailable"
else
    echo "  pip-audit: no known CVEs"
fi

# ─── 4/7 Ruff with security rules ─────────────────────────────────────────
echo
echo "[sast] === 4/7 Ruff (security + bugbear + syntax) ==="
RUFF_OK=1
run_scanner_to_file "Ruff JSON scan" 0 "$OUT/ruff.json" \
    "$PY" -m ruff check --select S,B,E9,F63 --exit-zero \
    --output-format json bulk_downloader
[ "$SCANNER_LAST_OK" -eq 1 ] || RUFF_OK=0
RUFF_COUNT=0
if [ "$RUFF_OK" -eq 1 ]; then
    if ! RUFF_COUNT=$(json_count ruff "$OUT/ruff.json" 2>/dev/null); then
        RUFF_OK=0
        scanner_error "Ruff did not produce a complete JSON report"
    elif ! format_json_report "$OUT/ruff.json" "$OUT/ruff.txt" Ruff; then
        RUFF_OK=0
    fi
fi
if [ "$RUFF_COUNT" -gt 0 ]; then
    echo "  ruff findings: $RUFF_COUNT"; FINDINGS=$((FINDINGS+RUFF_COUNT))
elif [ "$RUFF_OK" -ne 1 ]; then
    echo "  ruff: unavailable"
else
    echo "  ruff: clean"
fi

# ─── 5/7 ESLint with security plugin ──────────────────────────────────────
echo
echo "[sast] === 5/7 ESLint on static JS ==="
if command -v npx >/dev/null 2>&1; then
    ESLINT_OK=1
    if [ ! -d "$REPO_DIR/node_modules/eslint" ]; then
        echo "  installing eslint + eslint-plugin-security..."
        (cd "$REPO_DIR" && npm init -y >/dev/null 2>&1 \
         && npm install --save-dev --silent \
              'eslint@^8' 'eslint-plugin-security@^2' \
              2>"$OUT/eslint-install.log") || {
            ESLINT_OK=0
            scanner_error "ESLint dependency install failed"
        }
    fi
    if [ ! -f "$REPO_DIR/.eslintrc.sast.json" ]; then
        cat > "$REPO_DIR/.eslintrc.sast.json" <<'EOF'
{
  "plugins": ["security"],
  "extends": ["plugin:security/recommended"],
  "parserOptions": {"ecmaVersion": 2022}
}
EOF
    fi
    run_scanner "ESLint JSON scan" 1 npx eslint --no-eslintrc -c .eslintrc.sast.json \
        --format json -o "$OUT/eslint.json" \
        bulk_downloader/static/*.js 2>/dev/null
    [ "$SCANNER_LAST_OK" -eq 1 ] || ESLINT_OK=0
    ESLINT_COUNT=0
    if [ "$ESLINT_OK" -eq 1 ]; then
        if ! ESLINT_COUNT=$(json_count eslint "$OUT/eslint.json" 2>/dev/null); then
            ESLINT_OK=0
            scanner_error "ESLint did not produce a complete JSON report"
        elif ! status_count_ok "$ESLINT_COUNT"; then
            ESLINT_OK=0
            scanner_error "ESLint status/report mismatch (status $SCANNER_LAST_STATUS, count $ESLINT_COUNT)"
        elif ! format_json_report "$OUT/eslint.json" "$OUT/eslint.txt" ESLint; then
            ESLINT_OK=0
        fi
    fi
    if [ "$ESLINT_COUNT" -gt 0 ]; then
        echo "  eslint findings: $ESLINT_COUNT"
        FINDINGS=$((FINDINGS+ESLINT_COUNT))
    elif [ "$ESLINT_OK" -ne 1 ]; then
        echo "  eslint: unavailable"
    else
        echo "  eslint: clean"
    fi
else
    echo "  eslint: SKIPPED (Node.js / npx not on PATH)" > "$OUT/eslint.txt"
    scanner_error "ESLint unavailable because Node.js / npx is not installed"
fi

# ─── 6/7 Project preflight ────────────────────────────────────────────────
echo
echo "[sast] === 6/7 Project preflight (cross-cutting integration) ==="
run_scanner_to_file "project preflight" 1 "$OUT/preflight.txt" \
    env BD_DISABLE_KEEPALIVE=1 "$PY" preflight.py
PREFLIGHT_STATUS=$SCANNER_LAST_STATUS
PREFLIGHT_REPORT_OK=$SCANNER_LAST_OK
PREFLIGHT_MARKER=0
if [ "$PREFLIGHT_REPORT_OK" -eq 1 ]; then
    grep -q "NO CRITICAL FINDINGS" "$OUT/preflight.txt"
    grep_status=$?
    if [ "$grep_status" -eq 0 ]; then
        PREFLIGHT_MARKER=1
    elif [ "$grep_status" -gt 1 ]; then
        PREFLIGHT_REPORT_OK=0
        scanner_error "project preflight report could not be read"
    fi
fi
if [ "$PREFLIGHT_REPORT_OK" -eq 1 ]; then
    if [ "$PREFLIGHT_STATUS" -eq 0 ] && [ "$PREFLIGHT_MARKER" -eq 1 ]; then
        echo "  preflight: clean"
    elif [ "$PREFLIGHT_STATUS" -eq 1 ] && [ "$PREFLIGHT_MARKER" -eq 0 ]; then
        echo "  preflight: findings present"
        FINDINGS=$((FINDINGS+1))
    else
        scanner_error "project preflight status/report mismatch"
    fi
fi

# ─── 7/7 detect-secrets ───────────────────────────────────────────────────
echo
echo "[sast] === 7/7 detect-secrets (committed-credential scanner) ==="
# Scan working tree for high-entropy strings, keywords, common API key shapes.
# Excludes virtualenv, generated artifacts, the results dir itself, the
# project's own .pristine reference if present, and binary/media test
# fixtures. Findings should be triaged via 'detect-secrets audit' once;
# this is the daily sweep.
SECRETS_OK=1
run_scanner_to_file "detect-secrets JSON scan" 0 "$OUT/detect-secrets.json" \
    "$PY" -m detect_secrets scan \
    --exclude-files '\.venv/|/__pycache__/|tools/sast_results/|node_modules/|\.git/|tests/fixtures/.*\.(png|jpg|mp4|webp|zip|gz)$' \
    --exclude-secrets 'EXAMPLE|placeholder|sample_|test_token_'
[ "$SCANNER_LAST_OK" -eq 1 ] || SECRETS_OK=0
SECRET_COUNT=0
if [ "$SECRETS_OK" -eq 1 ]; then
    if ! SECRET_COUNT=$(json_count secrets "$OUT/detect-secrets.json" 2>/dev/null); then
        SECRETS_OK=0
        scanner_error "detect-secrets did not produce a complete JSON report"
    elif ! format_json_report "$OUT/detect-secrets.json" \
            "$OUT/detect-secrets.txt" detect-secrets; then
        SECRETS_OK=0
    fi
fi
if [ "$SECRET_COUNT" -gt 0 ]; then
    echo "  detect-secrets: $SECRET_COUNT candidate(s) — triage with: \
${PY} -m detect_secrets audit $OUT/detect-secrets.json"
    FINDINGS=$((FINDINGS+SECRET_COUNT))
elif [ "$SECRETS_OK" -ne 1 ]; then
    echo "  detect-secrets: unavailable"
else
    echo "  detect-secrets: clean"
fi

# ─── Summary ──────────────────────────────────────────────────────────────
echo
echo "[sast] === Summary ==="
SUMMARY_OK=1
if {
    echo "Bulk Downloader SAST run — $(date)"
    echo "=========================================="
    echo
    for f in bandit semgrep pip-audit ruff eslint preflight detect-secrets; do
        echo "--- $f ---"
        if [ -s "$OUT/$f.txt" ]; then
            head -100 "$OUT/$f.txt"
            echo
        else
            echo "  (no output)"
        fi
        echo
    done
    echo "Findings categories with hits: $FINDINGS"
} > "$OUT/SUMMARY.txt"; then
    :
else
    SUMMARY_OK=0
    scanner_error "SAST summary report could not be written"
fi

echo
if [ "$SUMMARY_OK" -eq 1 ]; then
    echo "Reports written to: $OUT"
    grep -E '^---' "$OUT/SUMMARY.txt"
else
    echo "Reports incomplete in: $OUT"
fi
echo
if [ "$ERRORS" -gt 0 ]; then
    echo "[sast] $ERRORS scanner error(s). No clean verdict; review tools/sast_results/."
    exit 2
elif [ "$FINDINGS" -gt 0 ]; then
    echo "[sast] $FINDINGS finding categories. Review tools/sast_results/."
    exit 1
else
    echo "[sast] Clean — all SAST tools reported no findings."
    exit 0
fi
