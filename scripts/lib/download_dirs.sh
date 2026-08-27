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
# A missing config is the known-empty state of a freshly provisioned box. An
# existing config that cannot be parsed is different: its directory population
# is UNKNOWN. Likewise, a path that exists but cannot accept a file is not
# ready. Both states fail so deploy.sh's EXIT recovery can restart the service
# and report the partial deploy instead of claiming readiness.

# bd_ensure_download_dirs <python> <sites_config.json>
bd_require_current_deploy_inode() {
  local running_id path_id
  case "$0" in
    scripts/deploy.sh|*/scripts/deploy.sh)
      running_id="$(stat -Lc '%d:%i' "/proc/$$/fd/255" 2>/dev/null)" || {
        echo "download_dirs: UNKNOWN -- cannot identify deploy.sh's open script descriptor" >&2
        return 1
      }
      path_id="$(stat -Lc '%d:%i' "$0" 2>/dev/null)" || {
        echo "download_dirs: UNKNOWN -- cannot identify the deploy.sh path inode: $0" >&2
        return 1
      }
      if [ "$running_id" != "$path_id" ]; then
        echo "download_dirs: STALE-DEPLOY-SCRIPT-INODE -- the running script" \
             "descriptor is $running_id but $0 is $path_id after git reset;" \
             "refusing readiness so EXIT recovery can restart the service." >&2
        return 1
      fi
      ;;
  esac
  return 0
}

bd_ensure_download_dirs() {
  local py="${1:-}" cfg="${2:-}" made=0 checked=0 failed=0 d listing probe

  bd_require_current_deploy_inode || return 1
  [ -n "$py" ] || {
    echo "download_dirs: UNKNOWN -- no interpreter given" >&2
    return 1
  }
  [ -e "$cfg" ] || return 0
  [ -f "$cfg" ] || {
    echo "download_dirs: UNKNOWN -- config is not a regular file: $cfg" >&2
    return 1
  }

  listing="$(mktemp "${TMPDIR:-/tmp}/bd-download-dirs.XXXXXX")" || {
    echo "download_dirs: UNKNOWN -- cannot create the config measurement" >&2
    return 1
  }

  # Parsing is delegated to python because the config is JSON and a shell-side
  # parse would be a second, worse implementation of one. NUL framing preserves
  # every valid path byte except NUL itself, which POSIX paths cannot contain.
  if ! "$py" - "$cfg" >"$listing" <<'PY'
import json, os, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as exc:
    print("config read/parse failed: %s" % exc, file=sys.stderr)
    raise SystemExit(2)
if isinstance(data, list):
    sites = data
elif isinstance(data, dict):
    sites = list(data.values())
else:
    print("config root must be a list or object", file=sys.stderr)
    raise SystemExit(2)
seen = set()
for index, site in enumerate(sites):
    if not isinstance(site, dict):
        print("site %d is not an object" % index, file=sys.stderr)
        raise SystemExit(2)
    value = site.get("download_dir")
    if value in (None, ""):
        continue
    if not isinstance(value, str):
        print("site %d download_dir is not a string" % index, file=sys.stderr)
        raise SystemExit(2)
    path = os.path.expanduser(value.strip())
    if not path:
        continue
    if not os.path.isabs(path):
        print("site %d download_dir is not absolute: %s" % (index, path),
              file=sys.stderr)
        raise SystemExit(2)
    if path not in seen:
        seen.add(path)
        sys.stdout.buffer.write(os.fsencode(path) + b"\0")
PY
  then
    if ! rm -f -- "$listing"; then
      echo "download_dirs: UNKNOWN -- failed to remove config measurement $listing" >&2
    fi
    echo "download_dirs: UNKNOWN -- cannot read the configured directory population from $cfg" >&2
    return 1
  fi

  while IFS= read -r -d '' d; do
    checked=$((checked + 1))
    if [ ! -d "$d" ] && mkdir -p "$d" 2>/dev/null; then
      echo "download_dirs: created $d"
      made=$((made + 1))
    fi
    if [ ! -d "$d" ]; then
      echo "download_dirs: NOT READY -- could not create $d" >&2
      failed=$((failed + 1))
      continue
    fi
    probe="$(mktemp "$d/.bd-write-probe.XXXXXX" 2>/dev/null)" || probe=""
    if [ -z "$probe" ]; then
      echo "download_dirs: NOT READY -- configured directory is not writable: $d" >&2
      failed=$((failed + 1))
      continue
    fi
    if ! rm -f -- "$probe" || [ -e "$probe" ]; then
      echo "download_dirs: NOT READY -- could not remove write probe: $probe" >&2
      failed=$((failed + 1))
    fi
  done <"$listing"
  if ! rm -f -- "$listing" || [ -e "$listing" ]; then
    echo "download_dirs: UNKNOWN -- failed to remove config measurement $listing" >&2
    return 1
  fi

  [ "$made" -eq 0 ] || echo "download_dirs: $made directory(ies) created"
  [ "$failed" -eq 0 ] || return 1
  echo "download_dirs: verified $checked configured directory(ies) writable"
  return 0
}
