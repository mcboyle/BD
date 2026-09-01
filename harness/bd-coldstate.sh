#!/bin/bash
# Refresh everything a cold successor needs. Idempotent, safe to run mid-cut.
cd /home/mboyle/BulkDownloader || exit 1
S=/home/mboyle/FLEET_RUN_STATE.json
python3 - "$S" <<'PY'
import json, subprocess, sys, pathlib, datetime
p = pathlib.Path(sys.argv[1]); d = json.loads(p.read_text())
g = lambda *a: subprocess.run(["git","-C","/home/mboyle/BulkDownloader",*a],
                              capture_output=True,text=True).stdout.strip()
d["last_updated_utc"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
d["live"] = {
  "branch": g("rev-parse","--abbrev-ref","HEAD"),
  "head": g("rev-parse","HEAD"),
  "tree": g("rev-parse","HEAD^{tree}"),
  "origin_main": g("rev-parse","origin/main"),
  "dirty_files": len([l for l in g("status","--porcelain=v1").splitlines() if l]),
  "unpushed": g("rev-list","--count","origin/main..HEAD"),
  "version": next((l.split('"')[1] for l in open("bulk_downloader/__init__.py")
                   if l.startswith("__version__")), "?"),
}
p.write_text(json.dumps(d, indent=2) + "\n")
print("state:", d["live"]["branch"], d["live"]["head"][:8], "dirty", d["live"]["dirty_files"])
PY
# fleet reality, measured not remembered
{ echo "# Fleet snapshot $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "origin/main $(git rev-parse origin/main)"
  for spec in test5:127.0.0.1 test2:10.0.70.95 test3:10.0.70.80 test4:10.0.70.85 \
              test6:10.0.70.249 test7:10.0.70.84 test:10.0.70.83; do
    n=${spec%%:*}; ip=${spec##*:}
    c='cd $HOME/BulkDownloader; printf "%s %s %s\n" "$(git rev-parse --short HEAD)" "$(curl -s -m 6 http://localhost:5555/api/health|grep -oP "(?<=\"version\":\")[^\"]+")" "$(curl -s -o /dev/null -w "%{http_code}" -m 6 http://localhost:5555/)"'
    if [ "$n" = test5 ]; then o=$(bash -c "$c"); else o=$(timeout 15 ssh -o BatchMode=yes "$ip" "$c" 2>/dev/null|tail -1); fi
    printf '%-6s %s\n' "$n" "${o:-UNREACHABLE}"
  done; } > /home/mboyle/FLEET_SNAPSHOT.txt 2>/dev/null
echo "snapshot written"
