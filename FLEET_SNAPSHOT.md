# Fleet snapshot 2026-09-01T01:05:16Z

ROLE   IP             NAME   CHECKOUT     SERVING    HEALTH
integrator 10.0.70.164    test5  3.66.1388    (integrator) NEVER DEPLOYED -- this host, no ssh to self
runner 10.0.70.95     test2  3.66.1380    3.66.1379  200
runner 10.0.70.80     test3  3.66.1380    3.66.1379  200
runner 10.0.70.249    test6  3.66.1380    3.66.1379  200
deploy 10.0.70.83     test   3.66.1380    3.66.1379  200
deploy 10.0.70.85     test4  3.66.1380    3.66.1379  200
deploy 10.0.70.84     test7  3.66.1380    3.66.1379  200
deploy 10.0.70.50     bd     3.66.1380    3.66.1379  200
deploy 10.0.70.51     bd1    3.66.1380    3.66.1379  200
capacity 10.0.70.52     bd2    3.66.1380    none       000
capacity 10.0.70.53     bd3    3.66.1380    none       000
capacity 10.0.70.54     bd4    3.66.1380    none       000

## What this snapshot says

main is at v3.66.1388. Eight serving hosts run v3.66.1379 with checkouts at
v3.66.1380; test5, the integrator, serves 3.66.1378 and is never deployed.

COUNT RELEASES, NOT VERSION NUMBERS. v3.66.1383, 1386 and 1387 DO NOT EXIST --
they were burned by renumbering when cuts collided at merge. The real sequence
after 1379 is 1380, 1381, 1382, 1384, 1385, 1388. So the fleet is SIX releases
behind on the running service and FIVE on the checkouts. An earlier version of
this file said eight and nine; that was subtraction, not counting.

v3.66.1380 was deliberately not deployed -- operator ruling 42, it changes no
runtime path. That reasoning DOES NOT extend to what came after:

  v3.66.1388  bulk_downloader/db.py, runner_transport.py, staging_claim.py
  v3.66.1385  scripts/bd_candidate_replay.py (tooling, not the service)
  v3.66.1384  bulk_downloader/secrets_store.py, app_secrets.py

Those are runtime paths and they carry the day's CONFIRMED CRITICAL fixes: a
reservation minted over foreign bytes and called a resume, a done row recording
no transfer accepted as proof of ownership, and a vault destroyed by any
password after a backup restore. All three are fixed on main and running
NOWHERE.

THE COMMAND, with both preconditions:

    ls ~/.config/bd/DEPLOY_HOLD          # if present, the deploy refuses, exit 4
    BD_DEPLOY_ALL=1 bash ~/bd-fleet-deploy.sh

BD_DEPLOY_ALL=1 IS REQUIRED, or the script silently skips every runner-role host
-- test2, test3 and test6, three of the eight stale servers. NEVER deploy test5.

Read docs/repo/FLEET_TOPOLOGY.md, which was measured on 2026-08-31 and says ALL
TWELVE hosts now fetch from the github origin; the per-host bare mirrors are
inert. CLAUDE.md A6 still says two hosts fetch from a mirror -- that sentence is
STALE and FLEET_TOPOLOGY.md wins. The lesson behind it stands: a fetch exiting 0
is not delivery, so prove the intended commit is PRESENT before deploying it.

Every deploy passes through the locked-vault 503 window of row 478;
bd-vault-unlock.sh clears it automatically.
