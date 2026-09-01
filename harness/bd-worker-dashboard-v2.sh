#!/usr/bin/env bash
# Read-only operations dashboard for managed tasks and all 12 authoritative hosts.
# Attach: tmux attach -t bd-workers
set -u
roles="${BD_DASH_ROLES:-/home/mboyle/.config/bd/roles}"
worker_roles="${BD_DASH_WORKER_ROLES:-/home/mboyle/.config/bd/worker-roles}"
repo="${BD_DASH_REPO:-/home/mboyle/BulkDownloader}"
managed="${BD_DASH_MANAGED:-/home/mboyle/fleet-run-artifacts/2026-08-30/managed-workers.tsv}"
artifact_root="${BD_DASH_ARTIFACT_ROOT:-/home/mboyle/fleet-run-artifacts/2026-08-30}"
probe="${BD_DASH_PROBE:-/home/mboyle/bd-persist/harness/bd-worker-probe.sh}"
interval="${BD_WORKER_DASH_INTERVAL:-10}"
scratch=$(mktemp -d /tmp/bd-worker-dashboard.XXXXXX)
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM

reset=$'\033[0m'; bold=$'\033[1m'; dim=$'\033[2m'
blue=$'\033[38;5;75m'; cyan=$'\033[38;5;81m'; green=$'\033[38;5;78m'
yellow=$'\033[38;5;220m'; red=$'\033[38;5;203m'; magenta=$'\033[38;5;141m'

state_color() {
  case "$1" in
    RUNNING|DONE|GREEN|READY|COMPLETE) printf '%s' "$green";;
    QUEUED|WAITING|REVIEW|STALE) printf '%s' "$yellow";;
    FAILED|BLOCKED|UNKNOWN) printf '%s' "$red";;
    *) printf '%s' "$reset";;
  esac
}

artifact_for_task() {
  case "$1" in
    row402-final-gates) printf '%s/row402-final-gates.log' "$artifact_root";;
    vm-audit-row244) printf '%s/vm-agent-results/audit-row244.log' "$artifact_root";;
    vm-audit-row245) printf '%s/vm-agent-results/audit-row245.log' "$artifact_root";;
    vm-audit-row285) printf '%s/vm-agent-results/audit-row285.log' "$artifact_root";;
    vm-audit-partials) printf '%s/vm-agent-results/audit-partial-prose.log' "$artifact_root";;
    vm-audit-ledger31) printf '%s/vm-agent-results/audit-ledger31.log' "$artifact_root";;
    vm-audit-release) printf '%s/vm-agent-results/audit-release-reconcile.log' "$artifact_root";;
    *) return 1;;
  esac
}

managed_task_state() {
  # A TSV declaration is not proof that work is active.  RUNNING is emitted
  # only for a currently observable local process; rc artifacts are terminal.
  local task=$1 declared=$2 detail=$3 artifact rc live source_mtime age now
  live=$(ps -eo pid=,etimes=,comm=,args= 2>/dev/null | awk -v needle="$task" \
    '$3 !~ /^(awk|ps|grep)$/ && index($0, needle) {printf "pid=%s age=%ss", $1, $2; exit}')
  if [ -n "$live" ]; then
    printf 'RUNNING\tlive %s; %s' "$live" "$detail"
    return
  fi
  artifact=$(artifact_for_task "$task" 2>/dev/null || true)
  if [ -n "$artifact" ] && [ -r "$artifact.rc" ]; then
    rc=$(tr -d '[:space:]' < "$artifact.rc")
    if [ "$rc" = 0 ]; then
      printf 'COMPLETE\trc=0 (%s); %s' "$(basename "$artifact")" "$detail"
    else
      printf 'FAILED\trc=%s (%s); %s' "${rc:-unreadable}" "$(basename "$artifact")" "$detail"
    fi
    return
  fi
  source_mtime=$(stat -c %Y "$managed" 2>/dev/null || echo 0)
  now=$(date +%s)
  age=$(( now - source_mtime ))
  printf 'STALE\tno local process/artifact; declared=%s; source age=%ss; %s' \
    "$declared" "$age" "$detail"
}

role_color() {
  case "$1" in
    integrator) printf '%s' "$yellow";;
    runner) printf '%s' "$magenta";;
    deploy) printf '%s' "$blue";;
    capacity) printf '%s' "$cyan";;
    *) printf '%s' "$reset";;
  esac
}

while :; do
  [ "${BD_DASH_NO_CLEAR:-0}" = 1 ] || printf '\033[H\033[2J'
  main_sha=$(git -C "$repo" rev-parse --short=12 origin/main 2>/dev/null || echo UNKNOWN)
  main_ver=$(git -C "$repo" show origin/main:bulk_downloader/__init__.py 2>/dev/null \
    | sed -n 's/^__version__ = "\(.*\)"/\1/p')
  printf '%s%s BULKDOWNLOADER OPERATIONS%s  %s%s UTC%s  refresh %ss\n' \
    "$bold" "$blue" "$reset" "$dim" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$reset" "$interval"
  printf ' main %s%s%s  version %s%s%s  test5 load %s\n' \
    "$green" "$main_sha" "$reset" "$green" "${main_ver:-UNKNOWN}" "$reset" \
    "$(cut -d' ' -f1-3 /proc/loadavg)"

  printf '\n%s%s MANAGED TASKS%s\n' "$bold" "$blue" "$reset"
  printf ' %-25s %-11s %s\n' TASK STATE DETAIL
  printf ' %-25s %-11s %s\n' '-------------------------' '-----------' '---------------------------------------------------------'
  if [ -r "$managed" ]; then
    while IFS=$'\t' read -r task declared detail; do
      [ -n "${task:-}" ] || continue
      IFS=$'\t' read -r state rendered_detail < <(managed_task_state "$task" "$declared" "$detail")
      color=$(state_color "$state")
      printf ' %-25s %s%-11s%s %.78s\n' "$task" "$color" "$state" "$reset" "$rendered_detail"
    done < "$managed"
  else
    printf ' %-25s %s%-11s%s %s\n' dashboard "$red" UNKNOWN "$reset" "$managed unreadable"
  fi

  printf '\n%s%s LOCAL INTEGRATION LANE%s\n' "$bold" "$blue" "$reset"
  local_workers=$(tmux list-sessions -F '#S' 2>/dev/null | awk '/^cx-row/ {printf "%s ", $0}')
  printf ' workers: %s%s%s\n' "$yellow" "${local_workers:-none}" "$reset"
  lane=$(ps -eo pid=,etimes=,comm=,args= 2>/dev/null \
    | awk '$3 == "bash" && $0 ~ /bd-(verify-cut|row-chain|integrate-row|drain|night|watchdog)/ {printf " pid=%-8s age=%-7ss %s\n", $1, $2, substr($0, index($0,$4), 100)}')
  printf '%s\n' "${lane:- no active integration automation}"

  printf '\n%s%s ALL 12 HOSTS — PRIMARY ROLE / WORKER POLICY%s\n' "$bold" "$blue" "$reset"
  printf ' %-22s %-6s %-13s %-9s %-18s %-8s %-24s %-14s %s\n' \
    ROLE/WORK HOST ADDRESS STATE HEAD/VER DIRTY SERVICE/HEALTH LOAD A/P/J
  printf ' %-22s %-6s %-13s %-9s %-18s %-8s %-24s %-14s %s\n' \
    '----------------------' '------' '-------------' '---------' '------------------' '--------' '------------------------' '--------------' '-----'

  pids=""
  if [ -r "$roles" ]; then
    while read -r role name ip _rest; do
      case "$role" in integrator|runner|deploy|capacity) ;; *) continue;; esac
      if [ "$role" = integrator ]; then
        bash "$probe" > "$scratch/$name" 2>/dev/null &
      else
        timeout 8 ssh -o BatchMode=yes -o ConnectTimeout=4 "$ip" bash -s \
          < "$probe" > "$scratch/$name" 2>/dev/null &
      fi
      pids="$pids $!"
    done < "$roles"
    for pid in $pids; do wait "$pid" 2>/dev/null || true; done

    while read -r role name ip _rest; do
      case "$role" in integrator|runner|deploy|capacity) ;; *) continue;; esac
      policy=$(awk -v n="$name" '$2 == n {print $1 ":" $4}' "$worker_roles" 2>/dev/null)
      [ -n "$policy" ] || policy=UNKNOWN
      rolework="$role/$policy"
      color=$(role_color "$role")
      line=$(head -1 "$scratch/$name" 2>/dev/null || true)
      if [ -z "$line" ]; then
        printf ' %s%-22s%s %-6s %-13s %s%-9s%s %-18s %-8s %-24s %-14s %s\n' \
          "$color" "$rolework" "$reset" "$name" "$ip" "$red" UNKNOWN "$reset" '-' '-' '-' '-' '-'
        continue
      fi
      IFS='|' read -r host head ver dirty svc health load agents pytest jobs sessions heads tail_line <<< "$line"
      status_color=$green
      case "$health" in *credential_missing*|000*) status_color=$red;; *credential_vault_locked*) status_color=$yellow;; esac
      [ "$role" = capacity ] && status_color=$cyan
      printf ' %s%-22s%s %-6s %-13s %s%-9s%s %-18s %-8s %s%-24s%s %-14s %s/%s/%s\n' \
        "$color" "$rolework" "$reset" "$name" "$ip" "$status_color" READY "$reset" \
        "$head/${ver:--}" "$dirty" "$status_color" "$svc/$health" "$reset" "$load" \
        "$agents" "$pytest" "$jobs"
    done < "$roles"
  else
    printf ' %sUNKNOWN: authoritative roles file unreadable: %s%s\n' "$red" "$roles" "$reset"
  fi

  printf '\n%s%s ACTIVE CAPACITY AGENTS%s\n' "$bold" "$cyan" "$reset"
  while read -r role name ip _rest; do
    [ "$role" = capacity ] || continue
    line=$(head -1 "$scratch/$name" 2>/dev/null || true)
    if [ -z "$line" ]; then printf ' %-5s %sUNREACHABLE%s\n' "$name" "$red" "$reset"; continue; fi
    IFS='|' read -r host head ver dirty svc health load agents pytest jobs sessions heads tail_line <<< "$line"
    printf ' %-5s agents=%-2s sessions=%-29.29s worktrees=%-45.45s\n' "$name" "$agents" "$sessions" "$heads"
    [ "$tail_line" != - ] && printf '       %s↳ %s%s\n' "$dim" "$tail_line" "$reset"
  done < "$roles"

  printf '\n%sAttach:%s tmux attach -t bd-workers    %sDetach:%s Ctrl-b d\n' "$bold" "$reset" "$bold" "$reset"
  [ "${BD_DASH_ONCE:-0}" = 1 ] && break
  sleep "$interval"
done
