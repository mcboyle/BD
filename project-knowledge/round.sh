#!/usr/bin/env bash
# Max-verify battery for the SPA migration phases P0-P6 + the release band.
# One invocation = one full round. Prints a compact per-suite table + a verdict.
set -u
cd /home/claude/work
PB=/tmp/prestaged_site_packages
PWP=/home/claude/.cache/ms-playwright

# --- suite battery, grouped by migration phase --------------------------------
SUITES="
P0:test_csrf_session_bootstrap.py
P0:test_csrf_origin_guard.py
P1:test_spa_root_routing_contract.py
P3:test_phase3_coverage_fills.py
P3:test_v3_50_phase3.py
P4:test_cockpit_route_contract.py
P4:test_v3_51_phase4.py
P4:test_cockpit_navigation_contract.py
P6:test_v3_53_phase6.py
P6:test_settings_center_slice4.py
P6:test_global_config_defaults.py
P6:test_cockpit_appearance.py
P6:test_cockpit_shell_redesign_slice456.py
BAND:test_gui_parity.py
BAND:test_contracts.py
BAND:test_parity_method_aware.py
BAND:test_v3_66_336_consolidated.py
BAND:test_pin_index_in_sync.py
BAND:test_route_index_in_sync.py
BAND:test_v3_66_302_gui_parity_reconcile.py
BAND:test_config_parity_ratchet.py
"

TOTAL=0; PASS=0; FAIL=0; SKIP=0; SUITEFAIL=0
printf "%-6s %-42s %5s %5s %5s %5s  %s\n" PHASE SUITE TOT PASS FAIL SKIP STATUS
printf -- "--------------------------------------------------------------------------------------\n"
for line in $SUITES; do
  ph="${line%%:*}"; t="${line#*:}"
  out=$(timeout 110 env BD_HOME=$(mktemp -d) BD_DISABLE_KEEPALIVE=1 \
        PYTHONPATH=$PB PLAYWRIGHT_BROWSERS_PATH=$PWP python3 run_tests.py "tests/$t" 2>&1)
  rc=$?
  sm=$(echo "$out" | grep -oE 'Total: [0-9]+ \| Passed: [0-9]+ \| Failed: [0-9]+ \| Skipped: [0-9]+' | tail -1)
  if [ -z "$sm" ]; then
    st="ERR/TIMEOUT(rc=$rc)"; SUITEFAIL=$((SUITEFAIL+1))
    printf "%-6s %-42s %5s %5s %5s %5s  %s\n" "$ph" "$t" "?" "?" "?" "?" "$st"
    continue
  fi
  tt=$(echo "$sm" | grep -oE 'Total: [0-9]+' | grep -oE '[0-9]+')
  pp=$(echo "$sm" | grep -oE 'Passed: [0-9]+' | grep -oE '[0-9]+')
  ff=$(echo "$sm" | grep -oE 'Failed: [0-9]+' | grep -oE '[0-9]+')
  kk=$(echo "$sm" | grep -oE 'Skipped: [0-9]+' | grep -oE '[0-9]+')
  TOTAL=$((TOTAL+tt)); PASS=$((PASS+pp)); FAIL=$((FAIL+ff)); SKIP=$((SKIP+kk))
  if [ "$ff" -gt 0 ] || [ "$rc" -ne 0 ]; then st="FAIL"; SUITEFAIL=$((SUITEFAIL+1)); else st="ok"; fi
  printf "%-6s %-42s %5s %5s %5s %5s  %s\n" "$ph" "$t" "$tt" "$pp" "$ff" "$kk" "$st"
done
printf -- "--------------------------------------------------------------------------------------\n"
printf "BATTERY  suites=%d  tests=%d  pass=%d  fail=%d  skip=%d  suite_fails=%d\n" \
       "$(echo "$SUITES" | grep -c :)" "$TOTAL" "$PASS" "$FAIL" "$SKIP" "$SUITEFAIL"

# --- in-sync gates ------------------------------------------------------------
echo ""
echo "=== in-sync gates ==="
GATEFAIL=0
g() { echo -n "  $1: "; if eval "$2" >/tmp/g.out 2>&1; then echo "PASS"; else echo "FAIL (rc=$?)"; GATEFAIL=$((GATEFAIL+1)); tail -4 /tmp/g.out|sed 's/^/      /'; fi; }
g "route_counts(G12)"       "env PYTHONPATH=$PB python3 tools/check_route_counts.py"
g "config_surface --check"  "env PYTHONPATH=$PB python3 tools/config_surface_inventory.py --check"

# --- cockpit render_check (headless chromium, computed-layout gate) ------------
echo ""
echo "=== render_check (cockpit shell, 30 checks) ==="
rcout=$(env BD_RENDER_ROOT=/home/claude/work PYTHONPATH=$PB PLAYWRIGHT_BROWSERS_PATH=$PWP \
        timeout 120 python3 /home/claude/render_check.py 2>&1)
echo "$rcout" | grep -iE 'PASS|FAIL|checks|RESULT|[0-9]+/[0-9]+' | tail -6
echo "$rcout" | grep -qiE 'FAIL' && RCFAIL=1 || RCFAIL=0

echo ""
echo "######## ROUND VERDICT: battery_fail=$SUITEFAIL gate_fail=$GATEFAIL render_check_fail=$RCFAIL ########"
# belt+suspenders: ensure no runtime-db leak survives the round
rm -f /home/claude/work/downloader_history.db /home/claude/work/downloader_history.db-wal /home/claude/work/downloader_history.db-shm 2>/dev/null
echo "tree-clean: $(find /home/claude/work -name '*.db' | tr '\n' ' ')"
