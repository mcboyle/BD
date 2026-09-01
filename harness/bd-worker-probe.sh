#!/usr/bin/env bash
# One read-only, delimiter-safe worker/host probe. Output is one pipe-delimited line.
set -u
repo=/home/mboyle/BulkDownloader
host=$(hostname)
if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
  head=$(git -C "$repo" rev-parse --short=8 HEAD 2>/dev/null || echo UNKNOWN)
  ver=$(git -C "$repo" show HEAD:bulk_downloader/__init__.py 2>/dev/null \
    | sed -n 's/^__version__ = "\(.*\)"/\1/p')
  dirty=$(git -C "$repo" status --porcelain 2>/dev/null | wc -l)
else
  head=-; ver=-; dirty=-
fi
svc=$(systemctl is-active bulkdownloader 2>/dev/null || true)
[ -n "$svc" ] || svc=-
body=$(curl -sS --max-time 3 http://127.0.0.1:5555/api/health 2>/dev/null || true)
if [ -n "$body" ]; then
  http=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \
    http://127.0.0.1:5555/api/health 2>/dev/null || echo 000)
  degraded=$(printf '%s' "$body" \
    | sed -n 's/.*"degraded":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  health="$http${degraded:+:$degraded}"
else
  health=-
fi
load=$(cut -d' ' -f1-3 /proc/loadavg)
agents=$(ps -eo comm=,args= 2>/dev/null \
  | awk '$1 == "codex" && $0 ~ /[ ]exec[ ]/ {n++} END {print n+0}')
pytest=$(ps -eo comm=,args= 2>/dev/null \
  | awk '$1 ~ /^python/ && $0 ~ /-m[ ]pytest/ {n++} END {print n+0}')
if [ -x "$repo/venv/bin/python" ] && [ -f "$repo/toolchain/bin/bd-jobs" ]; then
  jobs=$("$repo/venv/bin/python" "$repo/toolchain/bin/bd-jobs" list 2>/dev/null \
    | awk '$1 == "LIVE" {n++} END {print n+0}')
else
  jobs=-
fi
sessions=$(tmux list-sessions -F '#S' 2>/dev/null \
  | awk '/^(bd-agent-|cx-)/ {printf "%s,", $0}' | sed 's/,$//')
[ -n "$sessions" ] || sessions=-
heads=""
for dir in /home/mboyle/.cache/bd-agent-worktrees/*; do
  git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || continue
  sha=$(git -C "$dir" rev-parse --short=8 HEAD 2>/dev/null || echo UNKNOWN)
  heads="${heads}$(basename "$dir")=$sha,"
done
heads=${heads%,}
[ -n "$heads" ] || heads=-
latest=$(find /home/mboyle/.cache/bd-agent-logs -maxdepth 1 -type f -name '*.log' \
  -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | cut -d' ' -f2-)
tail_line=""
[ -n "$latest" ] && tail_line=$(tail -1 "$latest" 2>/dev/null \
  | tr '|\n' '/ ' | cut -c1-88)
[ -n "$tail_line" ] || tail_line=-
printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
  "$host" "$head" "${ver:--}" "$dirty" "$svc" "$health" "$load" \
  "$agents" "$pytest" "$jobs" "$sessions" "$heads" "$tail_line"
