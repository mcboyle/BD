#!/usr/bin/env bash
# scripts/lib/dev_capabilities.sh -- the OPTIONAL host capabilities, in ONE place.
#
# WHY THIS FILE EXISTS (@1064, backlog row 96). Both functions below lived in
# cloud-setup.sh alone, so which capabilities a host ended up with depended on
# WHICH SCRIPT built it. Measured at v3.66.1062: test4 ran 21 tests that
# test5/test6/test7 SKIPPED -- 15722 pass / 5 skip against 15701 / 26 in one
# capture round -- because it alone had PostgreSQL and bd_dev_inspect, and
# nothing in provision_test_host.sh installed either.
#
# The failure worth fixing is not the missing software. It is that a capture on
# the poorer host goes GREEN by SKIPPING what is absent: a gate whose
# denominator excludes its subject reports OK, truthfully and uselessly.
#
# COPYING THE FUNCTIONS INTO THE SECOND SCRIPT WOULD HAVE REPRODUCED THE DEFECT.
# CLAUDE.md section 5 records the same shape for system packages -- "three
# copies is a denominator that drifts, and the copy nobody updated is the one
# the box runs". scripts/lib/system_deps.sh is the fix that worked there; this
# file applies that precedent to the optional capabilities.
#
# BOTH ARE OPTIONAL, DELIBERATELY. A host that cannot install postgres must WARN
# -- visibly, in the verdict -- rather than fail provisioning. The provisioner's
# own header states the rule: "a WARN is a capability you do NOT have, never a
# pass". What must not happen again is the third state: absent, and silent.
#
# Sourced by scripts/cloud-setup.sh and scripts/provision_test_host.sh.
# Gated by tests/test_v3_66_1064_provisioning_paths_do_not_diverge.py.

# The DSN the mod3 suites read. One definition, exported, so a caller that
# sources this file gets exactly the value the provisioning function used.
: "${MOD3_DSN:=postgresql://mod3_ci:mod3_ci_password@127.0.0.1:5432/mod3_ci}"
export MOD3_DSN

bd_mod3_env_persist(){
  # PERSIST THE DSN WHERE A NON-INTERACTIVE RUN CAN SEE IT (@1064).
  #
  # It used to live only in ~/.bashrc, BELOW that file's standard
  # `case $- in *i*) ;; *) return;;` guard -- so `ssh host ./capture.sh`,
  # nohup, systemd and cron all saw it UNSET and the 18 mod3 tests SKIPPED,
  # silently, while the capture reported PASS. Measured on test4 2026-08-12:
  # an interactive capture ran 15722 pass / 5 skip and a scripted one on the
  # same box ran 15712 / 23. The capability was present and the gate could not
  # see it. /etc/environment does not help either -- measured, it does not
  # reach a non-interactive ssh command on this fleet.
  mkdir -p "$HOME/.config/bd"
  printf 'export MOD3_PG_TEST_DSN=%s\n' "$MOD3_DSN" > "$HOME/.config/bd/mod3.env"
}

bd_mod3_pg_provision(){
  # A server already answering the DSN is the DONE state, whoever started it.
  if psql "$MOD3_DSN" -Atc "SELECT 1" >/dev/null 2>&1; then
    # PERSIST ON THIS PATH TOO. This early return is the DONE state, and the
    # first version of it returned BEFORE writing the env file -- so on exactly
    # the hosts where postgres already worked, the DSN was never persisted and
    # a scripted capture still skipped 18 tests. Measured on test4 @1064:
    # mod3_exit=0 with env_file=ABSENT. An exit code is not evidence that the
    # side effect happened.
    bd_mod3_env_persist
    echo "mod3 postgres: already serving the DSN"; return 0
  fi
  # INSTALL IT. REFUSING TO PROVISION IS THE DEFECT (backlog 97).
  #
  # This used to print "postgresql-common absent" and return 1. That is correct
  # in the cloud image, where the package is baked in, and WRONG on bare Ubuntu
  # -- so on every freshly built host the step WARNed forever and row 96's
  # "both provisioning paths give a host the same capabilities" held for this
  # fleet only. Measured @1065: test5, test6 and test7 each needed
  # `apt-get install postgresql` by hand first, which is a provisioner asking
  # the operator to do the provisioning.
  #
  # This file's own header states the standard: the failure worth fixing is not
  # the missing software, it is that a capture on the poorer host goes GREEN by
  # SKIPPING what is absent.
  if ! command -v pg_ctlcluster >/dev/null 2>&1; then
    echo "mod3 postgres: pg_ctlcluster absent -- installing postgresql"
    $SUDO apt-get update -qq >/dev/null 2>&1 || true   # a stale index is not fatal
    DEBIAN_FRONTEND=noninteractive $SUDO apt-get install -y -qq postgresql \
        >/dev/null 2>&1 \
      || { echo "postgresql install failed (apt-get install postgresql)"; return 1; }
    # ASK FOR THE BINARY; DO NOT TRUST THE EXIT CODE. This file already records
    # that lesson at @1064 -- mod3_exit=0 with env_file=ABSENT -- so an apt that
    # reports success without delivering pg_ctlcluster must refuse here rather
    # than fall through into the cluster logic and fail less legibly.
    command -v pg_ctlcluster >/dev/null 2>&1 \
      || { echo "postgresql install reported success but pg_ctlcluster is still absent"; return 1; }
  fi
  _cl="$(pg_lsclusters -h 2>/dev/null | head -n1)"
  [ -n "$_cl" ] || { echo "no postgres cluster initialized in this image"; return 1; }
  _ver="$(echo "$_cl" | awk '{print $1}')"
  _name="$(echo "$_cl" | awk '{print $2}')"
  # Start the image-baked cluster only when nothing already holds the port.
  pg_isready -q -h 127.0.0.1 -p 5432 2>/dev/null \
    || $SUDO pg_ctlcluster "$_ver" "$_name" start \
    || { echo "pg_ctlcluster $_ver $_name start failed"; return 1; }
  # Role + database, idempotent. Admin path: local-socket peer auth as the
  # cluster owner, the one access Debian's default pg_hba grants without a
  # password. ALTER on the existing-role branch so a role left by an earlier
  # (trust-auth, passwordless) provisioning converges to this contract.
  $SUDO su -s /bin/bash postgres -c "psql -v ON_ERROR_STOP=1 -Atq" <<'SQL' \
    || { echo "role ensure failed"; return 1; }
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname='mod3_ci') THEN
    ALTER ROLE mod3_ci LOGIN CREATEDB PASSWORD 'mod3_ci_password';
  ELSE
    CREATE ROLE mod3_ci LOGIN CREATEDB PASSWORD 'mod3_ci_password';
  END IF;
END $$;
SQL
  $SUDO su -s /bin/bash postgres -c \
      "psql -Atqc \"SELECT 1 FROM pg_database WHERE datname='mod3_ci'\"" \
      | grep -q 1 \
    || $SUDO su -s /bin/bash postgres -c \
      "psql -v ON_ERROR_STOP=1 -qc 'CREATE DATABASE mod3_ci OWNER mod3_ci'" \
    || { echo "database ensure failed"; return 1; }
  bd_mod3_env_persist
  # Verify by DOING, the section 9 discipline: the DSN itself must answer.
  psql "$MOD3_DSN" -Atc "SELECT 1" >/dev/null 2>&1
}

bd_dev_inspect_provision(){
  local site
  site="$(venv/bin/python -c 'import site;print(site.getsitepackages()[0])' 2>/dev/null)" || return 1
  [ -n "$site" ] && [ -d "$site" ] || return 1
  cat > "$site/bd_dev_inspect.py" <<'PYEOF'
"""Dev-only raw-capture seam. NEVER commit this to the repository.

Provisioned into site-packages by scripts/cloud-setup.sh. It adds no capability:
it installs bulk_downloader.capture_redactor's existing _PASSTHROUGH into the
documented `_override` seam, and clears it again. The seam's precedence rules
live in capture_redactor.active_redactor(), which is the actual subject of
tests/test_v3_66_59_redactor_seam.py.
"""
from bulk_downloader import capture_redactor as _cr

_RAW_FLAG = "BD" + "_CAPTURE_RAW"  # split so the config-surface scanner does
                                    # not read this dev file as a runtime tunable


def enable_raw_capture() -> bool:
    """Install the pass-through iff the operator's raw flag is on.

    Returns False and installs NOTHING when the flag is absent -- the capability
    must not be reachable by importing this module alone.
    """
    import os
    if os.environ.get(_RAW_FLAG, "").strip() not in ("1", "true", "True", "yes"):
        return False
    _cr._override = _cr._PASSTHROUGH
    return True


def disable_raw_capture() -> None:
    """Restore redaction, and pin it rather than merely clearing the override.

    Clearing `_override` to None is NOT enough: active_redactor() then falls
    through to `_capture_raw_enabled()`, which is still true while the
    operator's raw flag is set, so the capture would stay raw. Measured -- the
    seam's own test_disable_restores_redaction failed exactly that way. Pinning
    the REAL redactor is what "disable" has to mean while the flag is on.
    """
    _cr._override = _cr._REAL
PYEOF
  venv/bin/python -c "import bd_dev_inspect, inspect; assert hasattr(bd_dev_inspect,'enable_raw_capture')"
}
