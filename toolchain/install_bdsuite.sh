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
EXPECTED_ENV_DEST="$(realpath -m -- "$DEST/.bdenv.sh")" || exit 2
[ "$ENV_DEST" = "$EXPECTED_ENV_DEST" ] || {
  echo "ERROR: BD_ENV_FILE_DEST must be $EXPECTED_ENV_DEST" >&2
  exit 2
}
[ "$DEST" != / ] || {
  echo "ERROR: BD_SUITE_BIN may not be filesystem root" >&2
  exit 2
}
[ "$LINK_DEST" != / ] || {
  echo "ERROR: BD_SUITE_LINK_BIN may not be filesystem root" >&2
  exit 2
}
[ "$DEST" != "$LINK_DEST" ] || {
  echo "ERROR: BD_SUITE_BIN and BD_SUITE_LINK_BIN must be distinct" >&2
  exit 2
}
path_is_within() {
  [ "$1" = "$2" ] && return 0
  case "$1" in "$2"/*) return 0;; esac
  return 1
}
if path_is_within "$DEST" "$LINK_DEST"; then
  echo "ERROR: BD_SUITE_BIN may not be inside BD_SUITE_LINK_BIN" >&2
  exit 2
fi
if path_is_within "$LINK_DEST" "$DEST"; then
  echo "ERROR: BD_SUITE_LINK_BIN may not be inside BD_SUITE_BIN" >&2
  exit 2
fi
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

# The prior manifest is the ownership boundary for obsolete public links.  A
# target under DEST alone is not ownership evidence: operators may deliberately
# keep aliases into the suite.
OLD_PUBLIC_NAMES=()
if [ -e "$DEST/.bdsuite-manifest.json" ] || [ -L "$DEST/.bdsuite-manifest.json" ]; then
  [ -f "$DEST/.bdsuite-manifest.json" ] && [ ! -L "$DEST/.bdsuite-manifest.json" ] || {
    echo "ERROR: prior suite manifest is not a regular file" >&2
    exit 2
  }
  old_public="$(python3 - "$DEST/.bdsuite-manifest.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
try:
    value = json.loads(path.read_text(encoding="utf-8"))
    names = value["public_commands"]
except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
    raise SystemExit(f"invalid prior suite manifest: {exc}")
if value.get("schema") != "bdsuite-manifest/1" or not isinstance(names, list):
    raise SystemExit("invalid prior suite manifest schema")
if any(not isinstance(n, str) or not n or Path(n).name != n or
       not (n == "bd" or n.startswith("bd-")) for n in names):
    raise SystemExit("invalid prior public command roster")
if len(names) != len(set(names)):
    raise SystemExit("duplicate prior public command")
print("\n".join(names))
PY
  )" || { echo "ERROR: invalid prior suite manifest" >&2; exit 2; }
  while IFS= read -r name; do [ -z "$name" ] || OLD_PUBLIC_NAMES+=("$name"); done <<< "$old_public"
elif [ -d "$DEST" ] && [ -d "$LINK_DEST" ]; then
  # Compatibility with an install predating manifests: only basename-preserving
  # bd command links are attributable to the old installer.
  for public in "$LINK_DEST"/bd "$LINK_DEST"/bd-*; do
    [ -L "$public" ] || continue
    name="$(basename -- "$public")"
    [ "$(readlink -- "$public")" = "$DEST/$name" ] || continue
    OLD_PUBLIC_NAMES+=("$name")
  done
fi

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
STAGE=
TXN_DIR=
PUBLISHED=0
EXCHANGED=0
COMMITTED=0
PREPARED_LINKS=()
BACKUP_ORIGINALS=()
BACKUP_PATHS=()
NEW_LINKS=()

owned_path_exists() {
  local owned_path="${1:-}"
  [ -n "$owned_path" ] && { [ -e "$owned_path" ] || [ -L "$owned_path" ]; }
}

rollback() {
  local status=$?
  trap - EXIT
  local rollback_ok=1
  if [ "$COMMITTED" -ne 1 ]; then
    local index original backup
    if [ "$PUBLISHED" -eq 1 ]; then
      if [ "$EXCHANGED" -eq 1 ]; then
        python3 "$HERE/install_exchange.py" "$DEST" "$STAGE" >/dev/null 2>&1 || rollback_ok=0
      else
        [ ! -e "$DEST" ] || mv -T -- "$DEST" "$STAGE" >/dev/null 2>&1 || rollback_ok=0
      fi
    fi
    for index in "${!BACKUP_PATHS[@]}"; do
      original="${BACKUP_ORIGINALS[$index]}"; backup="${BACKUP_PATHS[$index]}"
      if [ -e "$backup" ] || [ -L "$backup" ]; then
        mv -Tf -- "$backup" "$original" >/dev/null 2>&1 || rollback_ok=0
      fi
    done
    for original in "${NEW_LINKS[@]}"; do
      [ ! -e "$original" ] && [ ! -L "$original" ] || rm -f -- "$original" || rollback_ok=0
    done
  fi
  for record in "${PREPARED_LINKS[@]}"; do
    if owned_path_exists "$record"; then
      rm -f -- "$record" || rollback_ok=0
    fi
  done
  if [ "$rollback_ok" -eq 1 ]; then
    if owned_path_exists "${STAGE:-}"; then
      rm -rf -- "$STAGE" || rollback_ok=0
    fi
    if owned_path_exists "${TXN_DIR:-}"; then
      rm -rf -- "$TXN_DIR" || rollback_ok=0
    fi
  fi
  if [ "$rollback_ok" -ne 1 ]; then
    local retained=() path
    for path in "${STAGE:-}" "${TXN_DIR:-}"; do
      owned_path_exists "$path" && retained+=("$path")
    done
    printf 'BD-INSTALL-ROLLBACK-INCOMPLETE: recovery data retained at' >&2
    if [ "${#retained[@]}" -gt 0 ]; then
      printf ' %s' "${retained[@]}" >&2
    else
      printf ' no remaining owned path (restoration or cleanup still failed)' >&2
    fi
    printf '; live destination %s requires recovery\n' "$DEST" >&2
  fi
  exit "$status"
}
trap rollback EXIT

STAGE="$(mktemp -d "$DEST_PARENT/.bdsuite-stage.XXXXXX")" || {
  echo "ERROR: staging directory creation failed" >&2
  exit 2
}
TXN_DIR="$(mktemp -d "$LINK_DEST/.bdsuite-txn.XXXXXX")" || {
  echo "ERROR: transaction directory creation failed" >&2
  exit 2
}
[ "$(stat -c '%a' -- "$TXN_DIR")" = 700 ] || {
  echo "ERROR: transaction directory is not mode 0700: $TXN_DIR" >&2
  exit 2
}
if [ -d "$DEST" ]; then
  DEST_MODE="$(stat -c '%a' -- "$DEST")" || exit 2
else
  DEST_MODE=755
fi
case "$DEST_MODE" in *[!0-7]*|'') echo "ERROR: invalid suite directory mode" >&2; exit 2;; esac
chmod "$DEST_MODE" -- "$STAGE" || { echo "ERROR: staging chmod failed for suite directory" >&2; exit 2; }

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
validate_generation() {
  python3 - "$HERE/bin" "$1" "$VALID_ROOT" "$DEST_MODE" "$LINK_DEST" "$2" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

source, generation = map(Path, sys.argv[1:3])
expected_root = sys.argv[3]
expected_dir_mode = int(sys.argv[4], 8)
links = Path(sys.argv[5])
check_links = sys.argv[6] == "links"
source_names = sorted(p.name for p in source.iterdir() if p.is_file() and not p.is_symlink())
public = sorted(n for n in source_names if n == "bd" or n.startswith("bd-"))
expected_entries = set(source_names) | {".bdenv.sh", ".bd-work-tree", ".bdsuite-manifest.json"}
if set(p.name for p in generation.iterdir()) != expected_entries:
    raise SystemExit("generation population does not exactly match source and metadata")
info = generation.lstat()
if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != expected_dir_mode:
    raise SystemExit("generation directory mode mismatch")
for name in source_names:
    info = (generation / name).lstat()
    expected_mode = 0o755 if name in public else 0o644
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != expected_mode:
        raise SystemExit(f"generation file type or mode mismatch: {name}")
for name, expected_mode in ((".bdenv.sh", 0o644), (".bd-work-tree", 0o600),
                            (".bdsuite-manifest.json", 0o644)):
    info = (generation / name).lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != expected_mode:
        raise SystemExit(f"generation metadata type or mode mismatch: {name}")
pointer = generation / ".bd-work-tree"
if pointer.stat().st_uid != os.getuid() or pointer.stat().st_nlink != 1:
    raise SystemExit("checkout pointer ownership or link count mismatch")
if pointer.read_bytes() != (expected_root + "\n").encode():
    raise SystemExit("checkout pointer content mismatch")
manifest = json.loads((generation / ".bdsuite-manifest.json").read_text(encoding="utf-8"))
if manifest != {"schema": "bdsuite-manifest/1", "source_basenames": source_names,
               "public_commands": public}:
    raise SystemExit("generation manifest mismatch")
if check_links:
    for name in public:
        link = links / name
        if not link.is_symlink() or link.readlink() != generation / name:
            raise SystemExit(f"live public link mismatch: {name}")
PY
}
validate_generation "$STAGE" no-links || exit 2
STAGED_ROOT="$(env -u BD_WORK_TREE python3 "$STAGE/_bd_work_tree.py")" || {
  echo "ERROR: staged checkout pointer did not resolve" >&2; exit 2;
}
[ "$STAGED_ROOT" = "$VALID_ROOT" ] || {
  echo "ERROR: staged checkout pointer resolved to $STAGED_ROOT, expected $VALID_ROOT" >&2
  exit 2
}

for name in "${PUBLIC_NAMES[@]}"; do
  prepared="$TXN_DIR/link.$name"
  [ ! -e "$prepared" ] && [ ! -L "$prepared" ] || { echo "ERROR: link staging collision: $prepared" >&2; exit 2; }
  ln -s -- "$DEST/$name" "$prepared" || { echo "ERROR: preparing public link failed: $name" >&2; exit 2; }
  PREPARED_LINKS+=("$prepared")
done

if [ -e "$DEST" ]; then
  python3 "$HERE/install_exchange.py" "$DEST" "$STAGE" || exit 2
  EXCHANGED=1
else
  mv -T -- "$STAGE" "$DEST" || {
    echo "ERROR: publishing suite generation failed" >&2; exit 2;
  }
fi
PUBLISHED=1

for name in "${PUBLIC_NAMES[@]}"; do
  public="$LINK_DEST/$name"
  prepared="$TXN_DIR/link.$name"
  if [ -e "$public" ] || [ -L "$public" ]; then
    # Prevalidation proved this is already the exact stable link.  Keep it in
    # place so upgrades never introduce a missing-public-command interval.
    rm -f -- "$prepared" || exit 2
  else
    NEW_LINKS+=("$public")
    mv -T -- "$prepared" "$public" || {
      echo "ERROR: publishing public link failed: $name" >&2; exit 2;
    }
  fi
done

# Remove only names in the prior owned roster. Their stable target names may no
# longer exist in the new exact generation, which is why readlink is used.
for name in "${OLD_PUBLIC_NAMES[@]}"; do
  keep=0
  for required in "${PUBLIC_NAMES[@]}"; do [ "$name" != "$required" ] || keep=1; done
  [ "$keep" -eq 0 ] || continue
  public="$LINK_DEST/$name"
  [ -L "$public" ] || continue
  target="$(readlink -- "$public")" || continue
  case "$target" in "$DEST/$name")
    backup="$TXN_DIR/backup.$name"
    mv -T -- "$public" "$backup" || {
      echo "ERROR: retiring obsolete public link failed: $name" >&2; exit 2;
    }
    BACKUP_ORIGINALS+=("$public")
    BACKUP_PATHS+=("$backup")
  esac
done

LIVE_ROOT="$(env -u BD_WORK_TREE python3 "$DEST/_bd_work_tree.py")" || {
  echo "ERROR: live checkout pointer did not resolve" >&2; exit 2;
}
[ "$LIVE_ROOT" = "$VALID_ROOT" ] || {
  echo "ERROR: live checkout pointer resolved to $LIVE_ROOT, expected $VALID_ROOT" >&2
  exit 2
}
validate_generation "$DEST" links || exit 2

COMMITTED=1
cleanup_ok=1
for backup in "${BACKUP_PATHS[@]}"; do rm -f -- "$backup" || cleanup_ok=0; done
[ "$EXCHANGED" -eq 0 ] || rm -rf -- "$STAGE" || cleanup_ok=0
rmdir -- "$TXN_DIR" || cleanup_ok=0
trap - EXIT
[ "$cleanup_ok" -eq 1 ] || {
  echo "BD-INSTALL-CLEANUP-INCOMPLETE: published generation is valid; transaction residue requires cleanup" >&2
  exit 2
}
echo "bdsuite installed: ${#SOURCE_NAMES[@]} tools in $DEST; ${#PUBLIC_NAMES[@]} public links"
echo "installed .bdenv.sh and checkout authority for $VALID_ROOT"
exit 0
