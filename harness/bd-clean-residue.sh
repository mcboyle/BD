#!/bin/bash
# Archive the IRREPRODUCIBLE residue + a manifest of everything else, then delete.
# Operator ruling 2026-08-26: ~60G of /tmp/bd-* across the fleet is mostly repo
# copies and pytest scratch that git can regenerate. Storing 60G of that buys
# nothing; storing the LOGS and a full manifest of what was removed buys the
# whole record. Archive lands OUTSIDE /tmp and OUTSIDE BulkDownloader.
#   usage: bd-clean-residue.sh <host-label>        (runs on the CURRENT host)
set -u
H=$(hostname)
A="$HOME/bd-archive/2026-08-26"; mkdir -p "$A"
M="$A/$H-residue-manifest.txt"
K="$A/$H-irreproducible.tar.gz"

# 1. MANIFEST FIRST -- every path we are about to touch, with size and mtime.
{ echo "# $H residue manifest, recorded $(date -u +%Y-%m-%dT%H:%M:%SZ) BEFORE deletion"
  echo "# bytes|mtime|path"
  for d in /tmp/bd-*; do
    [ -e "$d" ] || continue
    printf '%s|%s|%s\n' "$(du -sb "$d" 2>/dev/null | cut -f1)" \
      "$(date -u -d @"$(stat -c %Y "$d" 2>/dev/null || echo 0)" +%Y-%m-%dT%H:%M:%SZ)" "$d"
  done
} > "$M"

# 2. KEEP WHAT CANNOT BE REGENERATED: logs, inventories, suite output, evidence.
#    Repo copies and pytest scratch are excluded by construction -- they are
#    byte-for-byte reproducible from git and from a rerun.
find /tmp/bd-* -maxdepth 2 \( -name '*.log' -o -name '*.txt' -o -name '*.json' \
     -o -name '*.xml' -o -name 'summary*' -o -name '*-out' \) -type f -size -50M \
     -print0 2>/dev/null > /tmp/.keeplist.$$ || true
KEPT_ANY=0
if [ -s /tmp/.keeplist.$$ ]; then
  KEPT_ANY=1
  # `|| true` HERE PRECEDED AN rm -rf. A tar that failed -- a file that vanished
  # or changed while being read, a full disk, a permission -- was swallowed, the
  # durability gate below only ever inspected the MANIFEST, and step 4 then
  # deleted the only copies. Found by audit 2026-09-01. An archive that did not
  # write is not permission to delete what it was archiving.
  if ! tar --null -czf "$K" --files-from=/tmp/.keeplist.$$ 2>"$K.err"; then
    echo "$H: KEEP-ARCHIVE FAILED -- refusing to delete anything" >&2
    sed 's/^/    /' "$K.err" 2>/dev/null | head -5 >&2
    rm -f /tmp/.keeplist.$$
    exit 4
  fi
  rm -f "$K.err"
fi
rm -f /tmp/.keeplist.$$

# 3. PROVE THE EVIDENCE IS DURABLE BEFORE THE IRREVERSIBLE STEP (CLAUDE.md A7).
[ -s "$M" ] || { echo "$H: MANIFEST EMPTY -- refusing to delete"; exit 1; }
# The manifest proves the NAMES were recorded. It says nothing about the BYTES.
# If there was anything to keep, the keep-archive must exist and be readable.
if [ "$KEPT_ANY" = 1 ]; then
  [ -s "$K" ] || { echo "$H: keep-archive is empty or absent -- refusing to delete"; exit 4; }
  tar -tzf "$K" >/dev/null 2>&1 || { echo "$H: keep-archive does not read back -- refusing to delete"; exit 4; }
fi
echo "$H: manifest $(wc -l < "$M") lines, $(du -h "$M" | cut -f1); keep-archive $( [ -f "$K" ] && du -h "$K" | cut -f1 || echo none)"

# 4. DELETE, by exact name shape only, never a bare glob into rm -rf.
freed=0
for d in /tmp/bd-*; do
  [ -e "$d" ] || continue
  case "$d" in /tmp/bd-*) ;; *) echo "  REFUSE $d"; continue;; esac
  [ "$(stat -c %U "$d" 2>/dev/null)" = "$(id -un)" ] || { echo "  REFUSE $d not ours"; continue; }
  sz=$(du -sk "$d" 2>/dev/null | cut -f1); rm -rf -- "$d" && freed=$((freed + ${sz:-0}))
done
# 5. stale git worktree registrations
[ -d "$HOME/BulkDownloader" ] && git -C "$HOME/BulkDownloader" worktree prune 2>/dev/null
echo "$H: freed $((freed/1024))M; /tmp/bd-* now $(ls -d /tmp/bd-* 2>/dev/null | wc -l) dirs; git worktrees $(git -C "$HOME/BulkDownloader" worktree list 2>/dev/null | wc -l)"
