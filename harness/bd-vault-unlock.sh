#!/usr/bin/env bash
# Unlock a host's secrets vault from the operator-supplied master password file.
#
# WHY THIS EXISTS: MasterPasswordBackend keeps the derived key in process memory
# only, so every service restart -- including every deploy -- starts LOCKED, and
# a locked vault makes all configured logins report "missing password" (see row
# 369: the message even advises re-entering credentials, which would overwrite
# intact ones). This re-arms them without a human at the console.
#
# The password is read on the REMOTE host and posted to that host's own
# loopback API. It is never echoed, never written to a log, and never leaves the
# host it belongs to.
#
#   usage: bd-vault-unlock.sh <host|local> [...] (default: the two site hosts)
set -uo pipefail
HOSTS=("$@"); [ ${#HOSTS[@]} -eq 0 ] && HOSTS=(10.0.70.249 10.0.70.51)
PWDIR="${BD_VAULT_PWDIR:-\$HOME/.bd-import/vault-master}"

failed=0
for h in "${HOSTS[@]}"; do
  printf '%-14s ' "$h"
  remote_rc=0
  if [ "$h" = "local" ] || [ "$h" = "localhost" ]; then
    # The integrator intentionally has no loopback SSH authorization.  `local`
    # still runs the identical stdin payload and preserves the same secret
    # boundary: the password filename is read only inside that payload and is
    # posted only to the service's loopback API.
    transport=(bash -s)
  else
    transport=(ssh -o BatchMode=yes -o ConnectTimeout=8 "$h" "bash -s")
  fi
  # shellcheck disable=SC2087 # PWDIR expands locally; all remote values are escaped.
  "${transport[@]}" <<REMOTE 2>/dev/null || remote_rc=$?
set -uo pipefail
PWD_="$PWDIR"
T=\$(ls ~/.bd-import 2>/dev/null | grep '^bdapi_' | head -1)
B=http://127.0.0.1:5555
if [ -z "\$T" ]; then
  # A host without a scoped API-token marker can still establish an operator
  # session locally: pair/redeem is the supported bootstrap and returns both
  # the cookie and the CSRF token required by the unlock route.  Keep this
  # entirely in one remote Python process so the password never reaches argv.
  python3 - "\$PWD_" <<'PY'
import http.cookiejar
import json
import pathlib
import sys
import urllib.error
import urllib.request

B = "http://127.0.0.1:5555"

def require_object(raw):
    value = json.loads(raw.decode())
    if not isinstance(value, dict):
        raise ValueError("response is not an object")
    return value

def request_json(opener, request):
    with opener.open(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError("unexpected HTTP status")
        return require_object(response.read())

STEP = "pairing"
try:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    STEP = "GET /api/pair"
    pair = request_json(opener, urllib.request.Request(B + "/api/pair"))
    token = pair.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("malformed pairing token")
    STEP = "POST /api/pair/redeem"
    redeem = request_json(opener, urllib.request.Request(
        B + "/api/pair/redeem",
        data=json.dumps({"token": token}, separators=(",", ":")).encode(),
        method="POST", headers={"Content-Type": "application/json"}))
    csrf = redeem.get("csrf_token")
    if redeem.get("ok") is not True or not isinstance(csrf, str) or not csrf:
        raise ValueError("malformed pairing redemption")
    STEP = "GET /api/secrets/status (before)"
    before = request_json(opener, urllib.request.Request(B + "/api/secrets/status"))
    if before.get("is_unlocked") is True:
        print("already unlocked")
        raise SystemExit(0)
    if before.get("is_unlocked") is not False:
        raise ValueError("malformed vault status")
    d = pathlib.Path(sys.argv[1])
    if not d.is_dir():
        print(f"LOCKED -- no password dir at {d}")
        raise SystemExit(2)
    names = [f.name for f in d.iterdir()]
    if len(names) != 1:
        print(f"expected exactly 1 entry in the vault dir, found {len(names)}")
        raise SystemExit(2)
    password = names[0]
    STEP = "POST /api/secrets/unlock"
    unlock = request_json(opener, urllib.request.Request(
        B + "/api/secrets/unlock",
        data=json.dumps({"password": password}, separators=(",", ":")).encode(),
        method="POST", headers={"Content-Type": "application/json", "X-CSRF-Token": csrf}))
    if unlock.get("ok") is not True:
        raise ValueError("unlock rejected")
    STEP = "GET /api/secrets/status (after)"
    after = request_json(opener, urllib.request.Request(B + "/api/secrets/status"))
    if after.get("is_unlocked") is True:
        print("-> UNLOCKED")
        raise SystemExit(0)
    if after.get("is_unlocked") is not False:
        raise ValueError("malformed final vault status")
    print("-> STILL LOCKED")
    raise SystemExit(1)
except SystemExit:
    raise
except urllib.error.HTTPError as exc:
    detail = ""
    try:
        payload = json.loads(exc.read().decode())
        if isinstance(payload, dict):
            detail = str(payload.get("error") or payload.get("message") or "")
    except Exception:
        detail = ""
    print(f"{STEP} FAILED: HTTP {exc.code}" + (f" -- {detail}" if detail else ""))
    raise SystemExit(1)
except Exception as exc:
    print(f"{STEP} FAILED: {type(exc).__name__}: {exc}")
    raise SystemExit(1)
PY
  exit \$?
fi
before=\$(curl -s -H "Authorization: Bearer \$T" \$B/api/secrets/status \
         | python3 -c 'import json,sys; print(json.load(sys.stdin).get("is_unlocked"))' 2>/dev/null)
if [ "\$before" = "True" ]; then echo "already unlocked"; exit 0; fi
if [ ! -d "\$PWD_" ]; then echo "LOCKED -- no password dir at \$PWD_"; exit 2; fi
# The operator's convention (same as the API token): the password is the
# FILENAME, not the file's contents. Read the name; never echo it.
python3 - "\$PWD_" "\$T" <<'PY'
import json, pathlib, sys, urllib.request, urllib.error
d = pathlib.Path(sys.argv[1])
names = [f.name for f in d.iterdir()]
if len(names) != 1:
    print(f"expected exactly 1 entry in the vault dir, found {len(names)}", end=" ")
    raise SystemExit(2)
pw = names[0]
req = urllib.request.Request("http://127.0.0.1:5555/api/secrets/unlock",
    data=json.dumps({"password": pw}).encode(), method="POST",
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + sys.argv[2]})
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("unlock HTTP", r.status, end=" ")
except urllib.error.HTTPError as e:
    print("unlock HTTP", e.code, end=" ")
except Exception as e:
    print("unlock error", type(e).__name__, end=" ")
PY
after=\$(curl -s -H "Authorization: Bearer \$T" \$B/api/secrets/status \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("is_unlocked"))' 2>/dev/null)
# Report the STATE, not the call's exit code -- a 200 that left it locked is a
# failure, and that distinction is the whole point of this script.
if [ "\$after" = "True" ]; then
  echo "-> UNLOCKED"
  exit 0
fi
echo "-> STILL LOCKED"
exit 1
REMOTE
  if [ "$remote_rc" -ne 0 ]; then
    if [ "$remote_rc" -eq 255 ]; then
      echo "ssh failed"
    else
      echo "remote command failed"
    fi
    failed=1
  fi
done
exit "$failed"
