# Fleet topology

What the twelve machines are, how each one gets code, and how to tell whether a
claim about them is true. Every number here was MEASURED on 2026-08-31 against
origin/main 8b179d656203 (v3.66.1378). Re-measure before relying on any of it;
CLAUDE.md A1 applies to this document as much as to any other.

`~/.config/bd/roles` is the authoritative host population. This file explains
that file; it does not replace it, and it must never be used to derive a host
list. A missing or unreadable roles file deploys EVERYWHERE, deliberately.

## The twelve

    role        host   ip             serves BD   deployed by bd-fleet-deploy
    integrator  test5  10.0.70.164    yes         NEVER -- operator ruling
    runner      test2  10.0.70.95     yes         only with BD_DEPLOY_ALL=1
    runner      test3  10.0.70.80     yes         only with BD_DEPLOY_ALL=1
    runner      test6  10.0.70.249    yes         only with BD_DEPLOY_ALL=1
    deploy      test   10.0.70.83     yes         yes
    deploy      test4  10.0.70.85     yes         yes
    deploy      test7  10.0.70.84     yes         yes
    deploy      bd     10.0.70.50     yes         yes
    deploy      bd1    10.0.70.51     yes         yes
    capacity    bd2    10.0.70.52     no          never
    capacity    bd3    10.0.70.53     no          never
    capacity    bd4    10.0.70.54     no          never

WHY RUNNERS ARE HELD BACK. Deploying onto a host mid-run restarts the service
underneath a measurement, which is the boundary A6 draws. The filter matches the
role word EXACTLY, so renaming a host to `runner` to add band capacity silently
turns it into something the deploy skips -- and renaming one away from `runner`
silently turns it into a deploy target.

WHY CAPACITY HOSTS RUN NO BD. They exist so measurement never runs on the tree
the integrator is editing. Bands run in per-SHA worktrees under ~/.bd-bands and
never touch the host checkout, so a runner can lend cores while it serves.

## How each host gets code, and the trap in it

    github origin   ALL TWELVE, as of 2026-08-31
    local mirror    none are configured to fetch from one any more, though a
                    bare mirror still exists on disk at ~/bd.git on bd, bd1,
                    bd2, bd3 and bd4. It is inert; nothing points at it.

A FETCH FROM A MIRROR EXITING 0 IS NOT DELIVERY. deploy.sh refuses on this in
two distinct shapes, and both fire in practice:

  ORIGIN-NOT-AUTHORITATIVE   origin is not mcboyle/BD, so origin/main here is a
                             mirror of unknown currency and the commit to land
                             is UNMEASURED. Re-run with --expect-commit.
  INTENDED-COMMIT-ABSENT     --expect-commit named a commit, the fetch
                             SUCCEEDED, and the commit is still not present --
                             so push it into the mirror first.

Measured 2026-08-31: bd and bd1 failed the second way on v3.66.1378 and had
failed the same way on 2026-08-30, because nothing in the release path pushed
into the mirror they fetched from. The mirror was NOT load-bearing -- both hosts
reach github.com/mcboyle/BD directly, confirmed by `git ls-remote` -- so the
recurring failure was vestigial configuration, not a network boundary. Both were
repointed at the authoritative origin the same day and the intended commit
proven PRESENT on each afterwards, which is the assertion INTENDED-COMMIT-ABSENT
exists to make. Row 471 keeps the general contract: the release path must either
deliver to every remote it will later fetch from, or refuse before it starts.

The same root cause was quieter on the capacity hosts, which each pointed at
their OWN bare mirror and sat at v3.66.1349, v3.66.1360 and v3.66.1371 while
every fetch exited 0. They were repointed on 2026-08-31 as well. A stale
checkout that reports itself current is the shape to watch for, and no gate
looks for it. Row 474.

THE MIRRORS STILL EXIST ON DISK. Nothing fetches from them now, so they will
drift and are a trap for anyone who repoints a host at one in future.

## The credential vault

Every serving host keeps its site logins in `BulkDownloader/secrets.json`,
AES-GCM under a key derived by PBKDF2-SHA256 from a master password and the
vault's own stored salt, at 600000 iterations.

THE MASTER KEY LIVES IN PROCESS MEMORY ONLY, so every restart -- including every
deploy -- starts LOCKED, and a locked vault makes configured logins report
"missing password". `bd-fleet-deploy.sh` treats that window as SERVING-DEGRADED
rather than an incident and unlocks through `bd-vault-unlock.sh`.

THE PASSWORD IS THE FILENAME of a zero-byte file in `~/.bd-import/vault-master`,
never the contents. Do not print it, do not pass it in argv, do not log it.

As of 2026-08-31 all nine serving vaults share ONE password. They did not
before: the fleet was split six to three across two different passwords with
nothing recording the fact, and test, test2 and test3 were rotated onto the
majority password through /api/secrets/change_password.

### Verifying a password WITHOUT spending an unlock attempt

/api/secrets/unlock and /api/secrets/change_password share an escalating
back-off, so testing a password by trying it can lock you out of the vault you
are trying to open. Do not do that. Each vault carries a top-level `verifier`:
an AES-GCM envelope around the fixed PUBLIC sentinel `bd-vault-verifier-v1`
(secrets_store._VERIFIER_PLAINTEXT), under the same derived key. Derive the key
offline from the vault's salt and iterations and decrypt the verifier: success
proves the password, the plaintext is a public constant, and nothing is
transmitted or charged. A vault with no verifier field predates the mechanism
and reads UNKNOWN -- never NO. Row 475 asks for this as a first-class tool.

## Reading a deploy result

    OK                  post-version matches, health 200
    SERVING-DEGRADED    health 503 with GET / = 200 and the vault locked after a
                        restart. Expected, and self-heals via the unlock.
    FAILED / INCIDENT   preserved per-host log under fleet-run-artifacts. A
                        FAILED DEPLOY IS NOT A NO-OP: it can leave the service
                        down after the stop and cache steps, so read the log and
                        the system state before claiming health.

`ok=N healed=N bad=N of N` is the only summary to trust, and the denominator is
the target list the roles file produced -- not the number of hosts you expected.
