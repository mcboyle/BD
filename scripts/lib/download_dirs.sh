# shellcheck shell=bash
# download_dirs.sh -- create the download directories the host's config names.
#
# THE DEFECT IT CLOSES, MEASURED 2026-08-14. `sites_config.json` was copied from
# one host to four others to give them a real site. It carries a `download_dir`
# that existed on the source machine and nowhere else, and the next capture
# round went 2/6:
#
#     CAPTURE VERDICT: FAIL - selftest exit=1
#       FAIL  disk_space: /home/mboyle/d: can't check
#             (FileNotFoundError: No such file or directory)
#
# Unit was 15942 pass / 0 fail on all six and live had 0 fail everywhere. The
# only thing wrong was a directory nobody created.
#
# NOTHING CREATED IT. `download_dir` is READ in at least four places
# (admission.py, app_capacity.py, alerts_engine.py, app_health.py) and created
# in none; the provisioner and install_linux.sh do not touch it either. So any
# host given a real site config fails identically, and the failure names a PATH
# rather than the cause -- the operator sees FileNotFoundError and has to work
# backwards to "the config I installed points at a directory that only exists on
# the machine I copied it from".
#
# WHY DEPLOY AND NOT THE PROVISIONER. Provisioning runs ONCE, on a bare host,
# before any operator config exists -- a fresh box has an empty 2-byte
# sites_config.json, so there is nothing to create and the provisioner cannot
# help. The failure arises when a config is installed LATER, which is exactly
# what happened. Deploy runs every time, and its job is "make this host ready to
# serve": a service that cannot write its downloads is not ready.
#
# WHY IT READS THE CONFIG RATHER THAN NAMING A PATH. The directory in question
# is one operator's, and this repo is public. Reading whatever the host's own
# config names is correct for any deployment and leaks nothing.
#
# EVERY REFUSAL IS A NO-OP, DELIBERATELY. A missing config is the normal state
# of a freshly provisioned box; a malformed one is the operator's problem to
# see elsewhere, not a reason to abort a deploy that is otherwise fine. This
# function creates directories or does nothing, and never fails a deploy.

# bd_ensure_download_dirs <python> <sites_config.json>
bd_ensure_download_dirs() {
  local py="${1:-}" cfg="${2:-}" made=0 d

  [ -n "$py" ] || { echo "download_dirs: no interpreter given; skipping"; return 0; }
  [ -f "$cfg" ] || return 0

  # Parsing is delegated to python because the config is JSON and a shell-side
  # parse would be a second, worse implementation of one. Anything unreadable
  # prints nothing and the loop below simply does not run -- a malformed config
  # must not fail a deploy.
  while IFS= read -r d; do
    # A blank download_dir is the COMMON case: several config keys default to
    # "" (watch_folder, storage_tier_dir and ytdlp_archive_path all did on the
    # real config). `mkdir -p ""` is an error, and worse, a bare relative value
    # would be created against whatever directory the deploy happens to be
    # standing in.
    [ -n "$d" ] || continue
    if [ -d "$d" ]; then
      continue
    fi
    if mkdir -p "$d" 2>/dev/null; then
      echo "download_dirs: created $d"
      made=$((made + 1))
    else
      # Not fatal: an unwritable path is a real problem, but it is the box
      # selftest's job to report it, and aborting the deploy here would take the
      # service down over a directory.
      echo "download_dirs: WARNING could not create $d -- the selftest's" \
           "disk_space check will report this" >&2
    fi
  done < <("$py" - "$cfg" <<'PY' 2>/dev/null
import json, os, sys
try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
except Exception:
    sys.exit(0)
sites = data if isinstance(data, list) else list(data.values())
for s in sites:
    if not isinstance(s, dict):
        continue
    d = s.get("download_dir") or ""
    if isinstance(d, str) and d.strip():
        print(os.path.expanduser(d.strip()))
PY
)

  [ "$made" -eq 0 ] || echo "download_dirs: $made directory(ies) created"
  return 0
}
