#!/usr/bin/env bash
# install_bdsuite -- land the bd-* toolchain into an operator-owned bin dir,
# symlinks -> /usr/local/bin (on PATH). Idempotent; safe to re-run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${BD_SUITE_BIN:-$HOME/.local/bin}"
LINK_DEST="${BD_SUITE_LINK_BIN:-/usr/local/bin}"
ENV_DEST="${BD_ENV_FILE_DEST:-$(dirname "$DEST")/bdenv.sh}"
WORK_ROOT="${BD_WORK_TREE:-$(git -C "$HERE/.." rev-parse --show-toplevel 2>/dev/null)}"
VALID_ROOT="$(git -C "$WORK_ROOT" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: cannot install bdenv.sh without a valid BD_WORK_TREE checkout" >&2
  exit 2
}

# Validate the physical contract before the first mkdir, copy, chmod, write, or
# link. A refusal must leave an existing installation byte-identical.
DEST="$(realpath -m -- "$DEST")" || exit 2
LINK_DEST="$(realpath -m -- "$LINK_DEST")" || exit 2
ENV_DEST="$(realpath -m -- "$ENV_DEST")" || exit 2
EXPECTED_ENV="$(dirname "$DEST")/bdenv.sh"
[ "$ENV_DEST" = "$EXPECTED_ENV" ] || {
  echo "ERROR: BD_ENV_FILE_DEST must be $EXPECTED_ENV for installed wrappers" >&2
  exit 2
}
[ "$DEST" != "$LINK_DEST" ] || {
  echo "ERROR: BD_SUITE_BIN and BD_SUITE_LINK_BIN must be distinct" >&2
  exit 2
}
[ -d "$HERE/bin" ] && [ -f "$HERE/bdenv.sh" ] || {
  echo "ERROR: incomplete source toolchain at $HERE" >&2
  exit 2
}

mkdir -p "$DEST" "$LINK_DEST" "$(dirname "$ENV_DEST")"
cp -f "$HERE"/bin/* "$DEST"/ 2>/dev/null
chmod +x "$DEST"/* 2>/dev/null || true
cp "$HERE/bdenv.sh" "$ENV_DEST"
printf '%s\n' "$VALID_ROOT" > "$(dirname "$ENV_DEST")/.bd-work-tree"

n=0; fail=0
for f in "$DEST"/*; do
  b="$(basename "$f")"
  if ln -sf "$f" "$LINK_DEST/$b" 2>/dev/null; then n=$((n+1)); else fail=$((fail+1)); fi
done
echo "bdsuite installed: $(ls "$DEST" | wc -l) tools in $DEST; $n $LINK_DEST symlinks${fail:+ ($fail failed)}"
command -v bd-cut >/dev/null 2>&1 || { echo "WARN: bd-cut not on PATH -- add $DEST to PATH: export PATH=$DEST:\$PATH"; }
command -v bd-boot >/dev/null 2>&1 && echo "  ready: bd-boot, bd-cut, bd-kb-sync, bd-opv, ..."
command -v bd-tools >/dev/null 2>&1 && echo "  run 'bd-tools' for the categorized toolchain map ($(ls "$DEST"/bd* 2>/dev/null | wc -l) tools)"

# The wrappers resolve their own physical path, then source this shared file
# from the parent of the installed bin directory.
echo "installed bdenv.sh for $VALID_ROOT"
