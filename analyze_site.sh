#!/usr/bin/env bash
# analyze_site.sh - run the full per-site analysis chain in one command.
#
# Replaces the 4-step hand-wiring (site_learning -> ops_intelligence health ->
# trust_intelligence forecast -> freshness) with a single call. Each step's
# output is wired into the next; optional inputs that don't exist yet (DOM-less
# captures have no selector/workflow history) are simply skipped, so the chain
# degrades gracefully instead of erroring.
#
# Recognition-only: this only RUNS the existing analysis tools over captures you
# already have. It captures nothing, drives no browser, writes no corpus.
#
# Usage:
#   ./analyze_site.sh <site-name> <portfolio-root> <capture.wacz> [more.wacz ...]
#
# Example (one site, a 3x quality series):
#   ./analyze_site.sh ultrafilms framework_reports/portfolio \
#       caps/ultrafilms_1080p.wacz caps/ultrafilms_fhd.wacz caps/ultrafilms_4k.wacz
#
# After running for each site, roll up the cockpit:
#   python3 tools/operator_layer.py cockpit --portfolio-root framework_reports/portfolio --out-dir framework_reports
#
# Honor $PY to override the interpreter (default: python3, or DISPLAY-prefixed
# venv python if you export PY). Run from the repo root.

set -u
set -o pipefail

PY="${PY:-python3}"

if [ "$#" -lt 3 ]; then
    echo "usage: $0 <site-name> <portfolio-root> <capture.wacz> [more.wacz ...]" >&2
    echo "  e.g. $0 ultrafilms framework_reports/portfolio caps/u_1080p.wacz caps/u_fhd.wacz caps/u_4k.wacz" >&2
    exit 2
fi

SITE="$1"; shift
PORTFOLIO="$1"; shift
CAPTURES=("$@")

OUT="${PORTFOLIO%/}/${SITE}"
mkdir -p "$OUT" || { echo "ERROR: cannot create $OUT" >&2; exit 1; }

# sanity: captures exist
for c in "${CAPTURES[@]}"; do
    [ -f "$c" ] || { echo "ERROR: capture not found: $c" >&2; exit 1; }
done

echo "================================================================"
echo "  analyze_site: $SITE  (${#CAPTURES[@]} capture(s)) -> $OUT"
echo "================================================================"

# helper: run a step, report, never abort the whole chain on a soft failure
step() {
    local label="$1"; shift
    echo "--- $label ---"
    if "$@"; then
        echo "    ok"
    else
        echo "    (skipped/failed: $label — continuing; downstream steps tolerate missing inputs)"
    fi
}

# only pass an optional --flag FILE if the file exists (graceful degradation)
optf() { [ -f "$2" ] && printf '%s %s' "$1" "$2"; }

# 1) site_learning — the base profile + drift + rendition + health-seed
step "1/4 site_learning (profile, rendition, drift, draft corpus entry)" \
    $PY tools/site_learning.py --captures "${CAPTURES[@]}" --site "$SITE" --out-dir "$OUT"

SP="$OUT/site_profile.json"
DH="$OUT/drift_history.json"
SHS="$OUT/site_health_score.json"
CH="$OUT/confidence_history.json"

# 2) ops_intelligence health — scores; consumes site-profile (+ drift-history if present)
step "2/4 ops_intelligence health (maturity/health scoring)" \
    $PY tools/ops_intelligence.py health --site "$SITE" \
        $(optf --site-profile "$SP") $(optf --drift-history "$DH") --out-dir "$OUT"

# 3) trust_intelligence forecast — drift/fragility probability; consumes drift-history + health
step "3/4 trust_intelligence forecast (drift/fragility forecast)" \
    $PY tools/trust_intelligence.py forecast --site "$SITE" \
        $(optf --drift-history "$DH") $(optf --site-health-score "$SHS") --out-dir "$OUT"

# 4) trust_intelligence freshness — evidence freshness; consumes the histories present
step "4/4 trust_intelligence freshness (evidence freshness)" \
    $PY tools/trust_intelligence.py freshness --site "$SITE" \
        $(optf --drift-history "$DH") $(optf --confidence-history "$CH") --out-dir "$OUT"

echo "================================================================"
echo "  done. artifacts in $OUT:"
ls "$OUT" | sed 's/^/    /'
echo
echo "  next: roll up the cockpit across the whole portfolio:"
echo "    $PY tools/operator_layer.py cockpit --portfolio-root ${PORTFOLIO%/} --out-dir ${PORTFOLIO%/}/.."
echo "================================================================"
