#!/usr/bin/env bash
# install_bdsuite -- land the bd-* toolchain into an operator-owned bin dir,
# symlinks -> /usr/local/bin (on PATH). Idempotent; safe to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${BD_SUITE_BIN:-$HOME/.local/bin}"
mkdir -p "$DEST"
cp -f "$HERE"/bin/* "$DEST"/ 2>/dev/null
chmod +x "$DEST"/* 2>/dev/null || true
n=0; fail=0
for f in "$DEST"/*; do
  b="$(basename "$f")"
  if ln -sf "$f" /usr/local/bin/"$b" 2>/dev/null; then n=$((n+1)); else fail=$((fail+1)); fi
done
echo "bdsuite installed: $(ls "$DEST" | wc -l) tools in $DEST; $n /usr/local/bin symlinks${fail:+ ($fail failed)}"
command -v bd-cut >/dev/null 2>&1 || { echo "WARN: bd-cut not on PATH -- add $DEST to PATH: export PATH=$DEST:\$PATH"; }
command -v bd-boot >/dev/null 2>&1 && echo "  ready: bd-boot, bd-cut, bd-kb-sync, bd-opv, ..."
command -v bd-tools >/dev/null 2>&1 && echo "  run 'bd-tools' for the categorized toolchain map ($(ls "$DEST"/bd* 2>/dev/null | wc -l) tools)"

# v605: install bdenv.sh so `bd` sources it (fixes the "file not found" noise).
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/bdenv.sh" ] && cp "$HERE/bdenv.sh" "${BD_ENV_FILE_DEST:-$DEST/bdenv.sh}" && echo "installed bdenv.sh"
