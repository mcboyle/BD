#!/usr/bin/env bash
# install_bdsuite -- failure-atomic exact publication of the bd tool suite.
set -u

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 2
DEST="${BD_SUITE_BIN:-$HOME/.local/bin}"
LINK_DEST="${BD_SUITE_LINK_BIN:-/usr/local/bin}"
ENV_DEST="${BD_ENV_FILE_DEST:-$DEST/.bdenv.sh}"

if [ "${BD_WORK_TREE+x}" = x ]; then
  WORK_ROOT="$BD_WORK_TREE"
else
  WORK_ROOT="$(git -C "$HERE/.." rev-parse --show-toplevel 2>/dev/null)" || WORK_ROOT=""
fi
[ -n "$WORK_ROOT" ] && [ "$WORK_ROOT" = "${WORK_ROOT#${WORK_ROOT%%[![:space:]]*}}" ] || {
  echo "ERROR: cannot install without a non-empty, unambiguous BD_WORK_TREE" >&2
  exit 2
}
[ -d "$WORK_ROOT" ] || {
  echo "ERROR: cannot install without a valid BD_WORK_TREE checkout" >&2
  exit 2
}
VALID_ROOT="$(git -C "$WORK_ROOT" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "ERROR: cannot install without a valid BD_WORK_TREE checkout" >&2
  exit 2
}
WORK_REAL="$(realpath -- "$WORK_ROOT")" || exit 2
VALID_ROOT="$(realpath -- "$VALID_ROOT")" || exit 2
[ "$WORK_REAL" = "$VALID_ROOT" ] || {
  echo "ERROR: BD_WORK_TREE must name the Git top level: $VALID_ROOT" >&2
  exit 2
}

# Normalize and validate every destination and collision before mutation.
DEST="$(realpath -m -- "$DEST")" || exit 2
LINK_DEST="$(realpath -m -- "$LINK_DEST")" || exit 2
ENV_DEST="$(realpath -m -- "$ENV_DEST")" || exit 2
[ "$ENV_DEST" = "$DEST/.bdenv.sh" ] || {
  echo "ERROR: BD_ENV_FILE_DEST must be $DEST/.bdenv.sh" >&2
  exit 2
}
[ "$DEST" != "$LINK_DEST" ] || {
  echo "ERROR: BD_SUITE_BIN and BD_SUITE_LINK_BIN must be distinct" >&2
  exit 2
}
case "$DEST/" in "$LINK_DEST/"*)
  echo "ERROR: BD_SUITE_BIN may not be inside BD_SUITE_LINK_BIN" >&2; exit 2;;
esac
case "$LINK_DEST/" in "$DEST/"*)
  echo "ERROR: BD_SUITE_LINK_BIN may not be inside BD_SUITE_BIN" >&2; exit 2;;
esac
[ -d "$HERE/bin" ] && [ -f "$HERE/bdenv.sh" ] && [ -f "$HERE/install_exchange.py" ] || {
  echo "ERROR: incomplete source toolchain at $HERE" >&2
  exit 2
}
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  [ -d "$DEST" ] && [ ! -L "$DEST" ] || {
    echo "ERROR: suite destination is not a physical directory: $DEST" >&2
    exit 2
  }
fi
if [ -e "$LINK_DEST" ] || [ -L "$LINK_DEST" ]; then
  [ -d "$LINK_DEST" ] && [ ! -L "$LINK_DEST" ] || {
    echo "ERROR: public link destination is not a physical directory: $LINK_DEST" >&2
    exit 2
  }
fi

SOURCE_NAMES=()
PUBLIC_NAMES=()
for source_path in "$HERE"/bin/*; do
  [ ! -L "$source_path" ] || {
    echo "ERROR: source tool population contains a symlink: $source_path" >&2
    exit 2
  }
  [ -f "$source_path" ] && [ ! -L "$source_path" ] || continue
  name="$(basename -- "$source_path")"
  SOURCE_NAMES+=("$name")
  case "$name" in bd|bd-*) PUBLIC_NAMES+=("$name");; esac
done
[ "${#SOURCE_NAMES[@]}" -gt 0 ] && [ "${#PUBLIC_NAMES[@]}" -gt 0 ] || {
  echo "ERROR: source tool or public command population is empty" >&2
  exit 2
}

for name in "${PUBLIC_NAMES[@]}"; do
  public="$LINK_DEST/$name"
  if [ -e "$public" ] || [ -L "$public" ]; then
    if [ ! -L "$public" ]; then
      echo "ERROR: refusing unrelated public collision: $public" >&2
      exit 2
    fi
    target="$(readlink -- "$public")" || exit 2
    case "$target" in
      "$DEST/$name") ;;
      *) echo "ERROR: refusing unrelated public symlink: $public -> $target" >&2; exit 2;;
    esac
  fi
done

DEST_PARENT="$(dirname -- "$DEST")"
mkdir -p -- "$DEST_PARENT" "$LINK_DEST" || exit 2
STAGE="$(mktemp -d "$DEST_PARENT/.bdsuite-stage.XXXXXX")" || exit 2
PUBLISHED=0
EXCHANGED=0
COMMITTED=0
PREPARED_LINKS=()
BACKUP_LINKS=()
NEW_LINKS=()

rollback() {
  local status=$?
  trap - EXIT
  if [ "$COMMITTED" -ne 1 ]; then
    local record original backup
    for record in "${BACKUP_LINKS[@]}"; do
      original="${record%%|*}"; backup="${record#*|}"
      [ ! -e "$backup" ] && [ ! -L "$backup" ] || mv -Tf -- "$backup" "$original" >/dev/null 2>&1 || true
    done
    for original in "${NEW_LINKS[@]}"; do
      [ ! -e "$original" ] && [ ! -L "$original" ] || rm -f -- "$original"
    done
    if [ "$PUBLISHED" -eq 1 ]; then
      if [ "$EXCHANGED" -eq 1 ]; then
        python3 "$HERE/install_exchange.py" "$DEST" "$STAGE" >/dev/null 2>&1 || true
      else
        [ ! -e "$DEST" ] || mv -T -- "$DEST" "$STAGE" >/dev/null 2>&1 || true
      fi
    fi
  fi
  for record in "${PREPARED_LINKS[@]}"; do
    [ ! -e "$record" ] && [ ! -L "$record" ] || rm -f -- "$record"
  done
  [ ! -e "$STAGE" ] || rm -rf -- "$STAGE"
  exit "$status"
}
trap rollback EXIT

for name in "${SOURCE_NAMES[@]}"; do
  cp -p -- "$HERE/bin/$name" "$STAGE/$name" || {
    echo "ERROR: staging copy failed for $name" >&2; exit 2;
  }
  case "$name" in
    bd|bd-*) chmod 755 -- "$STAGE/$name" || { echo "ERROR: staging chmod failed for $name" >&2; exit 2; };;
    *) chmod 644 -- "$STAGE/$name" || { echo "ERROR: staging chmod failed for $name" >&2; exit 2; };;
  esac
done
cp -p -- "$HERE/bdenv.sh" "$STAGE/.bdenv.sh" || {
  echo "ERROR: staging copy failed for bdenv.sh" >&2; exit 2;
}
chmod 644 -- "$STAGE/.bdenv.sh" || { echo "ERROR: staging chmod failed for bdenv.sh" >&2; exit 2; }
printf '%s\n' "$VALID_ROOT" > "$STAGE/.bd-work-tree" || exit 2
chmod 600 -- "$STAGE/.bd-work-tree" || exit 2

python3 - "$HERE/bin" "$STAGE" <<'PY' || exit 2
import json
import os
from pathlib import Path
import stat
import sys

source, stage = map(Path, sys.argv[1:])
source_names = sorted(p.name for p in source.iterdir() if p.is_file() and not p.is_symlink())
public = sorted(n for n in source_names if n == "bd" or n.startswith("bd-"))
installed = sorted(p.name for p in stage.iterdir() if not p.name.startswith("."))
if not public or installed != source_names:
    raise SystemExit("staged population does not equal source population")
for name in source_names:
    mode = (stage / name).stat().st_mode
    if not stat.S_ISREG(mode):
        raise SystemExit(f"staged entry is not regular: {name}")
    if name in public and not (mode & stat.S_IXUSR):
        raise SystemExit(f"staged command is not executable: {name}")
manifest = {
    "schema": "bdsuite-manifest/1",
    "source_basenames": source_names,
    "public_commands": public,
}
(stage / ".bdsuite-manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
PY
chmod 644 -- "$STAGE/.bdsuite-manifest.json" || exit 2
STAGED_ROOT="$(env -u BD_WORK_TREE python3 "$STAGE/_bd_work_tree.py")" || {
  echo "ERROR: staged checkout pointer did not resolve" >&2; exit 2;
}
[ "$STAGED_ROOT" = "$VALID_ROOT" ] || {
  echo "ERROR: staged checkout pointer resolved to $STAGED_ROOT, expected $VALID_ROOT" >&2
  exit 2
}

txn="$$"
for name in "${PUBLIC_NAMES[@]}"; do
  prepared="$LINK_DEST/.bdsuite-link.$txn.$name"
  [ ! -e "$prepared" ] && [ ! -L "$prepared" ] || { echo "ERROR: link staging collision: $prepared" >&2; exit 2; }
  ln -s -- "$DEST/$name" "$prepared" || { echo "ERROR: preparing public link failed: $name" >&2; exit 2; }
  PREPARED_LINKS+=("$prepared")
done

if [ -e "$DEST" ]; then
  python3 "$HERE/install_exchange.py" "$DEST" "$STAGE" || exit 2
  EXCHANGED=1
else
  mv -T -- "$STAGE" "$DEST" || exit 2
fi
PUBLISHED=1

for name in "${PUBLIC_NAMES[@]}"; do
  public="$LINK_DEST/$name"
  prepared="$LINK_DEST/.bdsuite-link.$txn.$name"
  if [ -e "$public" ] || [ -L "$public" ]; then
    backup="$LINK_DEST/.bdsuite-backup.$txn.$name"
    mv -T -- "$public" "$backup" || exit 2
    BACKUP_LINKS+=("$public|$backup")
  else
    NEW_LINKS+=("$public")
  fi
  mv -T -- "$prepared" "$public" || exit 2
done

# Remove obsolete installer-owned public links only. Their stable target names
# may no longer exist in the new exact generation, which is why readlink is used.
for public in "$LINK_DEST"/*; do
  [ -L "$public" ] || continue
  name="$(basename -- "$public")"
  keep=0
  for required in "${PUBLIC_NAMES[@]}"; do [ "$name" != "$required" ] || keep=1; done
  [ "$keep" -eq 0 ] || continue
  target="$(readlink -- "$public")" || continue
  case "$target" in "$DEST/"*)
    backup="$LINK_DEST/.bdsuite-backup.$txn.$name"
    mv -T -- "$public" "$backup" || exit 2
    BACKUP_LINKS+=("$public|$backup")
  esac
done

env -u BD_WORK_TREE python3 "$DEST/_bd_work_tree.py" >/dev/null || exit 2
python3 - "$DEST" "$LINK_DEST" <<'PY' || exit 2
import json
from pathlib import Path
import sys

dest, links = map(Path, sys.argv[1:])
manifest = json.loads((dest / ".bdsuite-manifest.json").read_text(encoding="utf-8"))
installed = sorted(p.name for p in dest.iterdir() if not p.name.startswith("."))
if installed != manifest["source_basenames"] or not manifest["public_commands"]:
    raise SystemExit("live manifest or population mismatch")
for name in manifest["public_commands"]:
    link = links / name
    if not link.is_symlink() or link.readlink() != dest / name:
        raise SystemExit(f"live public link mismatch: {name}")
PY

COMMITTED=1
for record in "${BACKUP_LINKS[@]}"; do rm -f -- "${record#*|}"; done
[ "$EXCHANGED" -eq 0 ] || rm -rf -- "$STAGE"
trap - EXIT
echo "bdsuite installed: ${#SOURCE_NAMES[@]} tools in $DEST; ${#PUBLIC_NAMES[@]} public links"
echo "installed .bdenv.sh and checkout authority for $VALID_ROOT"
exit 0
