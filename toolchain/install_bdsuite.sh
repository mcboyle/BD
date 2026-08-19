#!/usr/bin/env bash
# install_bdsuite -- land the bd-* toolchain into an operator-owned bin dir,
# symlinks -> /usr/local/bin (on PATH). Idempotent; safe to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${BD_SUITE_BIN:-$HOME/.local/bin}"
LINK_DEST="${BD_SUITE_LINK_BIN:-/usr/local/bin}"
mkdir -p "$DEST"
mkdir -p "$LINK_DEST"
cp -f "$HERE"/bin/* "$DEST"/ 2>/dev/null
chmod +x "$DEST"/* 2>/dev/null || true
n=0; fail=0
for f in "$DEST"/*; do
  b="$(basename "$f")"
  if ln -sf "$f" "$LINK_DEST/$b" 2>/dev/null; then n=$((n+1)); else fail=$((fail+1)); fi
done
echo "bdsuite installed: $(ls "$DEST" | wc -l) tools in $DEST; $n $LINK_DEST symlinks${fail:+ ($fail failed)}"
command -v bd-cut >/dev/null 2>&1 || { echo "WARN: bd-cut not on PATH -- add $DEST to PATH: export PATH=$DEST:\$PATH"; }
command -v bd-boot >/dev/null 2>&1 && echo "  ready: bd-boot, bd-cut, bd-kb-sync, bd-opv, ..."
command -v bd-tools >/dev/null 2>&1 && echo "  run 'bd-tools' for the categorized toolchain map ($(ls "$DEST"/bd* 2>/dev/null | wc -l) tools)"

# The wrappers resolve their own physical path, then source bdenv.sh from the
# parent of the installed bin directory. Keep that layout and its checkout
# pointer atomic so direct and symlinked invocation share one contract.
ENV_DEST="${BD_ENV_FILE_DEST:-$(dirname "$DEST")/bdenv.sh}"
WORK_ROOT="${BD_WORK_TREE:-$(git -C "$HERE/.." rev-parse --show-toplevel 2>/dev/null)}"
VALID_ROOT="$(git -C "$WORK_ROOT" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: cannot install bdenv.sh without a valid BD_WORK_TREE checkout" >&2
  exit 2
}
mkdir -p "$(dirname "$ENV_DEST")"
cp "$HERE/bdenv.sh" "$ENV_DEST"
printf '%s\n' "$VALID_ROOT" > "$(dirname "$ENV_DEST")/.bd-work-tree"
echo "installed bdenv.sh for $VALID_ROOT"
