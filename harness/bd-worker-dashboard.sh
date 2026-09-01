#!/usr/bin/env bash
# Read-only live dashboard for the current integration lane and capacity workers.
# Attach with: tmux attach -t bd-workers
set -u

roles=/home/mboyle/.config/bd/roles
repo=/home/mboyle/BulkDownloader
status_file=/home/mboyle/fleet-run-artifacts/2026-08-30/managed-workers.tsv
interval="${BD_WORKER_DASH_INTERVAL:-10}"

one_line() {
  tr '\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g; s/^ //; s/ $//'
}

while :; do
  printf '\033[H\033[2J'
  printf 'BULKDOWNLOADER WORKERS  %s UTC  refresh=%ss\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$interval"
  printf 'main=%s version=%s local-load=%s\n' \
    "$(git -C "$repo" rev-parse --short=12 origin/main 2>/dev/null || echo UNKNOWN)" \
    "$(git -C "$repo" show origin/main:bulk_downloader/__init__.py 2>/dev/null | sed -n 's/^__version__ = "\(.*\)"/\1/p' || echo UNKNOWN)" \
    "$(cut -d' ' -f1-3 /proc/loadavg)"

  printf '\nMANAGED TASKS\n'
  if [ -r "$status_file" ]; then
    awk -F '\t' 'NF >= 3 {printf "  %-24s %-12s %s\n", $1, $2, $3}' "$status_file"
  else
    printf '  status file absent: %s\n' "$status_file"
  fi

  printf '\nLOCAL INTEGRATION / AUTOMATION\n'
  local_rows=$(tmux list-sessions -F '#S' 2>/dev/null | awk '/^cx-row/ {printf "%s ", $0}')
  printf '  tmux workers: %s\n' "${local_rows:-none}"
  ps -eo pid=,etimes=,comm=,args= 2>/dev/null \
    | awk '$3 == "bash" && ($0 ~ /bd-(verify-cut|row-chain|integrate-row|drain|night|watchdog)/) {printf "  pid=%-8s age=%-7ss %s\n", $1, $2, substr($0, index($0,$4), 118)}'

  printf '\nCAPACITY WORKERS (authoritative roles)\n'
  if [ ! -r "$roles" ]; then
    printf '  UNKNOWN: roles file unreadable: %s\n' "$roles"
  else
    while read -r role name ip _rest; do
      [ "$role" = capacity ] || continue
      probe=$(timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=4 "$ip" 'bash -s' 2>/dev/null <<'REMOTE'
host=$(hostname)
load=$(cut -d' ' -f1-3 /proc/loadavg)
agents=$(ps -eo comm= 2>/dev/null | awk '$1 == "codex" {n++} END {print n+0}')
sessions=$(tmux list-sessions -F '#S' 2>/dev/null | awk '/^(bd-agent-|cx-)/ {printf "%s,", $0}' | sed 's/,$//')
heads=""
for d in /home/mboyle/.cache/bd-agent-worktrees/*; do
  [ -d "$d/.git" ] || git -C "$d" rev-parse --git-dir >/dev/null 2>&1 || continue
  h=$(git -C "$d" rev-parse --short=8 HEAD 2>/dev/null || echo UNKNOWN)
  heads="${heads}$(basename "$d")=${h},"
done
heads=${heads%,}
latest=$(find /home/mboyle/.cache/bd-agent-logs -maxdepth 1 -type f -name '*.log' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)
tail_line=""
[ -n "$latest" ] && tail_line=$(tail -1 "$latest" 2>/dev/null | tr '\n' ' ' | cut -c1-120)
printf 'host=%s|load=%s|agents=%s|sessions=%s|heads=%s|log=%s|tail=%s\n' "$host" "$load" "$agents" "${sessions:-none}" "${heads:-none}" "${latest:-none}" "${tail_line:-none}"
REMOTE
)
      if [ -z "$probe" ]; then
        printf '  %-5s %-13s UNREACHABLE/UNKNOWN\n' "$name" "$ip"
      else
        printf '  %-5s %-13s %s\n' "$name" "$ip" "$(printf '%s' "$probe" | one_line)"
      fi
    done < "$roles"
  fi

  printf '\nAttach: tmux attach -t bd-workers   Detach: Ctrl-b d\n'
  sleep "$interval"
done
