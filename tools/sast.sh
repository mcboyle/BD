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
rm -f "$OUT"/*.txt "$OUT"/*.json "$OUT"/*.sarif 2>/dev/null

echo
echo "[sast] Ensuring SAST tools are installed..."
"$PY" -m pip install --quiet --disable-pip-version-check \
    'bandit>=1.7' 'semgrep>=1.50' 'pip-audit>=2.6' 'ruff>=0.4' \
    'detect-secrets>=1.4' 2>&1 \
    | grep -v 'DEPRECATION\|already satisfied' || true

FINDINGS=0

# ─── 1/7 Bandit ───────────────────────────────────────────────────────────
echo
echo "[sast] === 1/7 Bandit (Python security linter) ==="
"$PY" -m bandit -r bulk_downloader -ll -f json -o "$OUT/bandit.json" 2>/dev/null || true
"$PY" -m bandit -r bulk_downloader -ll > "$OUT/bandit.txt" 2>&1 || true
n=$(grep -c '^>> Issue:' "$OUT/bandit.txt" 2>/dev/null || echo 0)
if [ "$n" -gt 0 ]; then
    echo "  bandit findings: $n"; FINDINGS=$((FINDINGS+n))
else
    echo "  bandit: clean"
fi

# ─── 2/7 Semgrep ──────────────────────────────────────────────────────────
echo
echo "[sast] === 2/7 Semgrep (security rule packs) ==="
# First run needs internet for rule packs; subsequent runs use the cache
# under ~/.semgrep
"$PY" -m semgrep \
    --config p/security-audit \
    --config p/python \
    --config p/javascript \
    --config p/owasp-top-ten \
    --severity ERROR --severity WARNING \
    --json --output "$OUT/semgrep.json" \
    --quiet --exclude tests --exclude __pycache__ \
    bulk_downloader >/dev/null 2>&1
SG_FINDINGS=$("$PY" -c "
import json,sys
try: d=json.load(open('$OUT/semgrep.json'))
except Exception: print(0); sys.exit()
print(len(d.get('results',[])))
" 2>/dev/null || echo 0)
"$PY" -m semgrep \
    --config p/security-audit \
    --config p/python \
    --config p/javascript \
    --severity ERROR --severity WARNING \
    --quiet --exclude tests --exclude __pycache__ \
    bulk_downloader > "$OUT/semgrep.txt" 2>&1 || true
if [ "$SG_FINDINGS" -gt 0 ]; then
    echo "  semgrep findings: $SG_FINDINGS"
    FINDINGS=$((FINDINGS+SG_FINDINGS))
else
    echo "  semgrep: clean"
fi

# ─── 3/7 pip-audit ────────────────────────────────────────────────────────
echo
echo "[sast] === 3/7 pip-audit (dependency CVE scan) ==="
"$PY" -m pip_audit -r requirements.txt --desc on \
    --format json --output "$OUT/pip-audit.json" 2>/dev/null || true
"$PY" -m pip_audit -r requirements.txt --desc on > "$OUT/pip-audit.txt" 2>&1 || true
if grep -q "Found [0-9]" "$OUT/pip-audit.txt" 2>/dev/null; then
    echo "  pip-audit: vulnerabilities reported"
    FINDINGS=$((FINDINGS+1))
else
    echo "  pip-audit: no known CVEs"
fi

# ─── 4/7 Ruff with security rules ─────────────────────────────────────────
echo
echo "[sast] === 4/7 Ruff (security + bugbear + syntax) ==="
"$PY" -m ruff check --select S,B,E9,F63 --exit-zero \
    --output-format json bulk_downloader > "$OUT/ruff.json" 2>&1 || true
"$PY" -m ruff check --select S,B,E9,F63 --exit-zero \
    bulk_downloader > "$OUT/ruff.txt" 2>&1 || true
n=$(grep -cE "S[0-9]+" "$OUT/ruff.txt" 2>/dev/null || echo 0)
if [ "$n" -gt 0 ]; then
    echo "  ruff security findings: $n"; FINDINGS=$((FINDINGS+n))
else
    echo "  ruff: clean"
fi

# ─── 5/7 ESLint with security plugin ──────────────────────────────────────
echo
echo "[sast] === 5/7 ESLint on static JS ==="
if command -v npx >/dev/null 2>&1; then
    if [ ! -d "$REPO_DIR/node_modules/eslint" ]; then
        echo "  installing eslint + eslint-plugin-security..."
        (cd "$REPO_DIR" && npm init -y >/dev/null 2>&1 \
         && npm install --save-dev --silent \
              'eslint@^8' 'eslint-plugin-security@^2' \
              2>"$OUT/eslint-install.log")
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
    npx eslint --no-eslintrc -c .eslintrc.sast.json \
        --format json -o "$OUT/eslint.json" \
        bulk_downloader/static/*.js 2>/dev/null || true
    npx eslint --no-eslintrc -c .eslintrc.sast.json \
        bulk_downloader/static/*.js > "$OUT/eslint.txt" 2>&1 || true
    if grep -qE "[0-9]+ problems?" "$OUT/eslint.txt" 2>/dev/null; then
        echo "  eslint: findings reported"; FINDINGS=$((FINDINGS+1))
    else
        echo "  eslint: clean"
    fi
else
    echo "  eslint: SKIPPED (Node.js / npx not on PATH)" > "$OUT/eslint.txt"
    echo "  eslint: SKIPPED (Node.js not installed)"
fi

# ─── 6/7 Project preflight ────────────────────────────────────────────────
echo
echo "[sast] === 6/7 Project preflight (cross-cutting integration) ==="
BD_DISABLE_KEEPALIVE=1 "$PY" preflight.py > "$OUT/preflight.txt" 2>&1 || true
if grep -q "NO CRITICAL FINDINGS" "$OUT/preflight.txt"; then
    echo "  preflight: clean"
else
    echo "  preflight: findings present"
    FINDINGS=$((FINDINGS+1))
fi

# ─── 7/7 detect-secrets ───────────────────────────────────────────────────
echo
echo "[sast] === 7/7 detect-secrets (committed-credential scanner) ==="
# Scan working tree for high-entropy strings, keywords, common API key shapes.
# Excludes virtualenv, generated artifacts, the results dir itself, the
# project's own .pristine reference if present, and binary/media test
# fixtures. Findings should be triaged via 'detect-secrets audit' once;
# this is the daily sweep.
"$PY" -m detect_secrets scan \
    --exclude-files '\.venv/|/__pycache__/|tools/sast_results/|node_modules/|\.git/|tests/fixtures/.*\.(png|jpg|mp4|webp|zip|gz)$' \
    --exclude-secrets 'EXAMPLE|placeholder|sample_|test_token_' \
    > "$OUT/detect-secrets.json" 2>&1 || true
SECRET_COUNT=$("$PY" -c "
import json, sys
try: d = json.load(open('$OUT/detect-secrets.json'))
except Exception: print(0); sys.exit()
total = sum(len(v) for v in d.get('results', {}).values())
print(total)
" 2>/dev/null || echo 0)
"$PY" -m detect_secrets scan \
    --exclude-files '\.venv/|/__pycache__/|tools/sast_results/|node_modules/|\.git/|tests/fixtures/.*\.(png|jpg|mp4|webp|zip|gz)$' \
    --exclude-secrets 'EXAMPLE|placeholder|sample_|test_token_' \
    | "$PY" -m json.tool > "$OUT/detect-secrets.txt" 2>&1 || true
if [ "$SECRET_COUNT" -gt 0 ]; then
    echo "  detect-secrets: $SECRET_COUNT candidate(s) — triage with: \
${PY} -m detect_secrets audit $OUT/detect-secrets.json"
    FINDINGS=$((FINDINGS+SECRET_COUNT))
else
    echo "  detect-secrets: clean"
fi

# ─── Summary ──────────────────────────────────────────────────────────────
echo
echo "[sast] === Summary ==="
{
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
} > "$OUT/SUMMARY.txt"

echo "Reports written to: $OUT"
echo
grep -E '^---' "$OUT/SUMMARY.txt"
echo
if [ "$FINDINGS" -gt 0 ]; then
    echo "[sast] $FINDINGS finding categories. Review tools/sast_results/."
    exit 1
else
    echo "[sast] Clean — all SAST tools reported no findings."
    exit 0
fi
