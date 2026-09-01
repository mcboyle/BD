#!/usr/bin/env bash
# Behavioral regression test for bd-vault-unlock's no-bdapi_ pairing fallback.
# It runs the real SSH payload locally through a fake SSH boundary; the fake
# loopback client accepts only the documented pair/redeem session+CSRF flow.
set -euo pipefail

tool=${BD_VAULT_UNLOCK_UNDER_TEST:-/home/mboyle/bd-persist/harness/bd-vault-unlock.sh}
root=$(mktemp -d /tmp/bd-vault-unlock-selftest.XXXXXX)
trap 'rm -rf -- "$root"' EXIT
fakebin=$root/bin
pyroot=$root/python
mkdir -p "$fakebin" "$pyroot"
test_path=$fakebin:$PATH

cat > "$fakebin/ssh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "${FAKE_SSH_ARGV:?}"
case " $* " in
  *' bash -s '*)
    HOME="${FAKE_REMOTE_HOME:?}" PATH="${FAKE_REMOTE_PATH:?}" \
      PYTHONPATH="${FAKE_REMOTE_PYTHONPATH:?}" bash -s
    ;;
  *) exit 97 ;;
esac
SH
chmod 700 "$fakebin/ssh"

cat > "$fakebin/curl" <<'PY'
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
url = next((a for a in reversed(args) if a.startswith("http://")), "")
headers = []
method = "GET"
i = 0
while i < len(args):
    a = args[i]
    if a in ("-H", "--header"):
        i += 1; headers.append(args[i])
    elif a in ("-d", "--data", "--data-raw"):
        i += 1; method = "POST"
    elif a == "-X":
        i += 1; method = args[i]
    i += 1

trace = Path(os.environ["FAKE_VAULT_TRACE"])
state = Path(os.environ["FAKE_VAULT_STATE"])
bearer = "Authorization: Bearer bdapi_bearer-fixture"

def event(name):
    trace.write_text(trace.read_text() + name + "\n")

def body(value):
    print(json.dumps(value))

if url.endswith("/api/secrets/status") and method == "GET" and bearer in headers:
    event("bearer-status")
    body({"ok": True, "is_unlocked": state.read_text() == "unlocked"})
else:
    raise SystemExit(97)
PY
chmod 700 "$fakebin/curl"

cat > "$pyroot/sitecustomize.py" <<'PY'
import json
import os
from pathlib import Path
import urllib.request

TRACE = Path(os.environ["FAKE_VAULT_TRACE"])
STATE = Path(os.environ["FAKE_VAULT_STATE"])

def event(name):
    TRACE.write_text(TRACE.read_text() + name + "\n")

class Response:
    status = 200
    def __init__(self, value): self.value = value
    def read(self): return json.dumps(self.value).encode()
    def __enter__(self): return self
    def __exit__(self, *_): return False

class Opener:
    def __init__(self): self.session = False
    def open(self, req, timeout=0):
        url = req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        data = req.data
        if url.endswith("/api/pair") and req.get_method() == "GET":
            event("pair")
            if os.environ.get("FAKE_VAULT_MALFORMED") == "1":
                return Response([])
            return Response({"token": "pair-fixture"})
        if url.endswith("/api/pair/redeem") and req.get_method() == "POST":
            if data != b'{"token":"pair-fixture"}': raise OSError("bad redeem")
            self.session = True
            event("redeem")
            return Response({"ok": True, "csrf_token": "csrf-fixture"})
        if url.endswith("/api/secrets/status") and req.get_method() == "GET":
            if not self.session: raise OSError("missing session")
            event("session-status")
            return Response({"ok": True, "is_unlocked": STATE.read_text() == "unlocked"})
        if url.endswith("/api/secrets/unlock") and req.get_method() == "POST":
            if not self.session or headers.get("x-csrf-token") != "csrf-fixture":
                raise OSError("missing csrf")
            if data != b'{"password":"selftest-secret"}': raise OSError("bad password body")
            STATE.write_text("unlocked")
            event("session-unlock")
            return Response({"ok": True})
        raise OSError("unexpected session request")

def bearer_urlopen(req, timeout=0):
    headers = {k.lower(): v for k, v in req.header_items()}
    if (req.full_url.endswith("/api/secrets/unlock")
            and headers.get("authorization") == "Bearer bdapi_bearer-fixture"
            and json.loads(req.data.decode()) == {"password": "selftest-secret"}):
        if os.environ.get("FAKE_VAULT_BEARER_NOOP") != "1":
            STATE.write_text("unlocked")
        event("bearer-unlock")
        return Response({"ok": True})
    raise OSError("unexpected bearer request")

urllib.request.build_opener = lambda *_: Opener()
urllib.request.urlopen = bearer_urlopen
PY

run_case() {
  local name=$1 marker=$2 output rc
  local home=$root/$name/home trace=$root/$name/trace state=$root/$name/state argv=$root/$name/ssh.argv
  mkdir -p "$home/.bd-import/vault-master"
  : > "$home/.bd-import/vault-master/selftest-secret"
  [ -z "$marker" ] || : > "$home/.bd-import/$marker"
  : > "$trace"
  printf 'locked' > "$state"
  set +e
  output=$(PATH="$test_path" \
    FAKE_REMOTE_HOME="$home" FAKE_REMOTE_PATH="$test_path" \
    FAKE_REMOTE_PYTHONPATH="$pyroot" \
    FAKE_SSH_ARGV="$argv" FAKE_VAULT_TRACE="$trace" FAKE_VAULT_STATE="$state" \
    BD_VAULT_PWDIR="$home/.bd-import/vault-master" \
    bash "$tool" selftest-host 2>&1)
  rc=$?
  set -e
  if [ "$rc" -ne 0 ] || ! grep -Fq -- '-> UNLOCKED' <<<"$output"; then
    printf 'FAIL %s: rc=%s output=%s\n' "$name" "$rc" "$output" >&2
    return 1
  fi
  if grep -Fq -- 'selftest-secret' <<<"$output" || grep -Fq -- 'selftest-secret' "$argv"; then
    printf 'FAIL %s: password escaped the remote payload\n' "$name" >&2
    return 1
  fi
  case "$name" in
    pairing)
      diff -u <(printf 'pair\nredeem\nsession-status\nsession-unlock\nsession-status\n') "$trace"
      ;;
    bearer)
      diff -u <(printf 'bearer-status\nbearer-unlock\nbearer-status\n') "$trace"
      ;;
  esac
  printf 'ok - %s\n' "$name"
}

run_case pairing ''
run_case bearer bdapi_bearer-fixture

# The integrator is itself a runtime service, but loopback SSH is deliberately
# not provisioned there.  `local` must execute the exact same payload without
# touching SSH; otherwise the canonical tool cannot re-arm the service it is
# installed on after a restart.
local_home=$root/local/home
mkdir -p "$local_home/.bd-import/vault-master"
: > "$local_home/.bd-import/vault-master/selftest-secret"
: > "$root/local.trace"
printf 'locked' > "$root/local.state"
set +e
local_output=$(HOME="$local_home" PATH="$test_path" PYTHONPATH="$pyroot" \
  FAKE_SSH_ARGV="$root/local.ssh.argv" \
  FAKE_VAULT_TRACE="$root/local.trace" FAKE_VAULT_STATE="$root/local.state" \
  BD_VAULT_PWDIR="$local_home/.bd-import/vault-master" \
  bash "$tool" local 2>&1)
local_rc=$?
set -e
if [ "$local_rc" -ne 0 ] \
    || ! grep -Fq -- '-> UNLOCKED' <<<"$local_output" \
    || [ -e "$root/local.ssh.argv" ] \
    || grep -Fq -- 'selftest-secret' <<<"$local_output"; then
  printf 'FAIL local pairing: rc=%s output=%s\n' "$local_rc" "$local_output" >&2
  exit 1
fi
diff -u \
  <(printf 'pair\nredeem\nsession-status\nsession-unlock\nsession-status\n') \
  "$root/local.trace"
printf 'ok - local pairing without SSH\n'

locked_bearer_home=$root/locked-bearer/home
mkdir -p "$locked_bearer_home/.bd-import/vault-master" "$locked_bearer_home/.bd-import"
: > "$locked_bearer_home/.bd-import/vault-master/selftest-secret"
: > "$locked_bearer_home/.bd-import/bdapi_bearer-fixture"
: > "$root/locked-bearer.trace"
printf 'locked' > "$root/locked-bearer.state"
set +e
locked_bearer_output=$(PATH="$test_path" \
  FAKE_REMOTE_HOME="$locked_bearer_home" FAKE_REMOTE_PATH="$test_path" \
  FAKE_REMOTE_PYTHONPATH="$pyroot" FAKE_SSH_ARGV="$root/locked-bearer.argv" \
  FAKE_VAULT_TRACE="$root/locked-bearer.trace" FAKE_VAULT_STATE="$root/locked-bearer.state" \
  FAKE_VAULT_BEARER_NOOP=1 BD_VAULT_PWDIR="$locked_bearer_home/.bd-import/vault-master" \
  bash "$tool" selftest-host 2>&1)
locked_bearer_rc=$?
set -e
if [ "$locked_bearer_rc" -eq 0 ] \
    || ! grep -Fq -- '-> STILL LOCKED' <<<"$locked_bearer_output" \
    || ! grep -Fq 'remote command failed' <<<"$locked_bearer_output" \
    || grep -Fq -- '-> UNLOCKED' <<<"$locked_bearer_output"; then
  printf 'FAIL bearer final locked state: rc=%s output=%s\n' "$locked_bearer_rc" "$locked_bearer_output" >&2
  exit 1
fi
printf 'ok - bearer final locked state fails closed\n'

malformed_home=$root/malformed/home
mkdir -p "$malformed_home/.bd-import/vault-master"
: > "$malformed_home/.bd-import/vault-master/selftest-secret"
: > "$root/malformed.trace"
printf 'locked' > "$root/malformed.state"
set +e
malformed_output=$(PATH="$test_path" \
  FAKE_REMOTE_HOME="$malformed_home" FAKE_REMOTE_PATH="$test_path" \
  FAKE_REMOTE_PYTHONPATH="$pyroot" FAKE_SSH_ARGV="$root/malformed.argv" \
  FAKE_VAULT_TRACE="$root/malformed.trace" FAKE_VAULT_STATE="$root/malformed.state" \
  FAKE_VAULT_MALFORMED=1 BD_VAULT_PWDIR="$malformed_home/.bd-import/vault-master" \
  bash "$tool" selftest-host 2>&1)
malformed_rc=$?
set -e
if [ "$malformed_rc" -eq 0 ] \
    || ! grep -Fq 'pairing fallback failed' <<<"$malformed_output" \
    || grep -Fq -- '-> UNLOCKED' <<<"$malformed_output"; then
  printf 'FAIL malformed pairing response: rc=%s output=%s\n' "$malformed_rc" "$malformed_output" >&2
  exit 1
fi
printf 'ok - malformed pairing response fails closed\n'
printf 'vault unlock fallback self-test: PASS\n'
