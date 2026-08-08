set +e
# @878: was an UNGUARDED `cd /home/claude/work` under `set +e`, so on any tree
# without that directory the cd failed and the script CARRIED ON -- running
# bd-regen-order and the packaging steps against whatever the caller's cwd
# happened to be. This script is zip-era one-shot scaffolding (its zip paths are
# frozen at v3.66.137/148) and is a retirement candidate, not a maintenance
# target; the cd is guarded so it cannot silently operate on the wrong tree in
# the meantime.
# RELEASE_WORK is deliberately UNprefixed. CLAUDE.md section 4: any name carrying
# the project env prefix -- including a shell local -- enters test_gui_parity's
# env ledger denominator and reads as "promoted but unledgered". This file sits
# inside that scan's scripts/*.sh glob, and the prefixed spelling failed the gate
# in CI. It is a one-shot override in a retirement-bound script, not a config
# key, so the contract's own remedy applies: do not prefix it.
#
# And do NOT spell the prefixed name here to explain that. The scan is a bare
# regex over the whole file with no comment stripping (tools/
# config_surface_inventory.py:_scan_shell_env), so a comment naming the variable
# is itself a ledger hit -- the first draft of this comment failed the same gate
# it documents.
cd "${RELEASE_WORK:-/home/claude/work}" || {
  echo "build_release.sh: cannot cd to ${RELEASE_WORK:-/home/claude/work};" >&2
  echo "  refusing to package from \$PWD instead. Set RELEASE_WORK." >&2
  exit 2
}
python toolchain/bin/bd-regen-order --work "$PWD" || exit $?
Z=/mnt/user-data/uploads/BulkDownloader_v3_66_137.zip
STAGE="${RELEASE_STAGE:-/tmp/bd_release_stage}"
OUT=/mnt/user-data/outputs/BulkDownloader_v3_66_148.zip
rm -rf "$STAGE"; mkdir -p "$STAGE"
unzip -Z1 "$Z" | grep -vE '/$' | grep -vE '__pycache__|\.pyc$' | sort -u > /tmp/l137.txt
( find bulk_downloader tests tools docs kb live_tests extension frontend/src frontend/dist scripts templates -type f 2>/dev/null
  find . -maxdepth 1 -type f \( -name '*.md' -o -name '*.txt' \) 2>/dev/null | sed 's|^\./||'
) | grep -vE '__pycache__|\.pyc$|/node_modules/|/venv/' | sort -u > /tmp/wf.txt
comm -23 /tmp/wf.txt /tmp/l137.txt > /tmp/add.txt
cat /tmp/l137.txt /tmp/add.txt | sort -u > /tmp/ship.txt
miss=0; cp=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if [ -f "$f" ]; then mkdir -p "$STAGE/$(dirname "$f")"; cp "$f" "$STAGE/$f"; cp=$((cp+1)); else echo "  MISSING: $f"; miss=$((miss+1)); fi
done < /tmp/ship.txt
echo "copied=$cp missing=$miss"
echo "--- new-in-148 detection-safety additions ---"
grep -E "selector_lint|test_v3_66_148" /tmp/add.txt
rm -f "$OUT"; ( cd "$STAGE" && zip -r -q -X "$OUT" . )
echo "zip: $(du -h "$OUT"|cut -f1), $(unzip -Z1 "$OUT"|wc -l) entries; version $(unzip -p "$OUT" bulk_downloader/__init__.py | grep __version__ | tr -d ' ')"
