#!/bin/bash
# Deferred: remove the frontend/dist scaffold from bd-verify-cut.sh once no
# instance is running. Editing a live shell script makes bash resume at a byte
# offset in the new text -- that cost 10 minutes twice on 2026-08-26.
set -u
for _ in $(seq 1 360); do
  [ "$(ps -eo args= | grep -cE '^bash (/home/mboyle/)?bd-verify-cut\.sh')" -eq 0 ] && break
  sleep 20
done
python3 - <<'PY'
import pathlib
p=pathlib.Path("/home/mboyle/bd-verify-cut.sh"); t=p.read_text()
i=t.find("# SCAFFOLD THE BUILT SPA.")
if i < 0:
    print("already reverted"); raise SystemExit
j=t.find("\nfi\n", t.find("if [ ! -e frontend/dist ]", i))
new = """# DO NOT SCAFFOLD frontend/dist. A symlink was added here at 10:02 to stop bands
# 503-ing on an empty worktree (row 267 lost 1 of 6959 to that). It made things
# WORSE: row 261's band then failed 12 tests, because
# test_frontend_dist_is_not_delivered_by_a_git_deploy asserts dist is ABSENT, and
# the thumbs/secret/share-target tests behave differently when a foreign dist
# appears. Removing the symlink made 13 of them pass immediately.
# THE RIGHT FIX IS IN THE GATES, NOT HERE: a gate that cannot see its subject must
# report UNKNOWN (row 265, merged at v3.66.1270; row 260). A verifier that
# MANUFACTURES the subject lies to every test that checks for its absence."""
p.write_text(t[:i] + new + t[j+4:])
print("scaffold reverted")
PY
bash -n /home/mboyle/bd-verify-cut.sh && echo "syntax OK after revert"
