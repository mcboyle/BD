h=$(hostname); cd ~/BulkDownloader 2>/dev/null || { echo "$h NO_REPO"; exit 0; }
echo "HOST $h"
echo "  head=$(git rev-parse --short HEAD) ver=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' bulk_downloader/__init__.py|head -1) dirty=$(git status --porcelain|wc -l)"
echo "  load=$(cut -d' ' -f1-3 /proc/loadavg) up=$(uptime -p|sed 's/up //') disk=$(df -Pk ~|awk 'NR==2{printf "%dG", $4/1048576}')"
echo "  node=$(node -v 2>/dev/null||echo none) npm=$(npm -v 2>/dev/null||echo none)"
echo "  svc=$(systemctl is-active bulkdownloader 2>/dev/null||echo n/a) health=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health 2>/dev/null||echo n/a)"
echo "  bundle_marker=$([ -f frontend/dist/.bd-built-from ] && cut -c1-8 frontend/dist/.bd-built-from || echo ABSENT)"
tot=0; unk=0
for d in /tmp/bd-testrun-*; do [ -d "$d" ] || continue; tot=$((tot+1)); { [ -e "$d/.bd-testrun" ] || [ -e "$d/.bd-testrun.lock" ]; } || { unk=$((unk+1)); echo "  UNKNOWN_ROOT $(basename "$d") birth=$(stat -c %W "$d" 2>/dev/null)"; }; done
echo "  testruns=$tot unknown=$unk"
echo "  proc pytest=$(ps -eo args=|grep -F 'python -m pytest'|grep -cv grep) codex=$(ps -eo args=|grep -c '[c]odex')  tmux=$(tmux ls 2>/dev/null|wc -l)"
k=~/.ssh/authorized_keys
if [ -f "$k" ]; then
  echo "  keys total=$(grep -cve '^\s*$' "$k") distinct=$(ssh-keygen -lf "$k" 2>/dev/null|awk '{print $2}'|sort -u|wc -l) mode=$(stat -c %a "$k")"
  ssh-keygen -lf "$k" 2>/dev/null | awk '{print "    key " $2 " " $3}'
  ssh-keygen -lf "$k" 2>/dev/null | awk '{print $2}' | sort | uniq -d | sed 's/^/    DUPLICATE /'
  grep -nE '^(from=|command=|no-)' "$k" | sed 's/^/    RESTRICTED /' || true
fi
