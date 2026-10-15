# Fleet topology -- 27-Node Measured Swarm Architecture

What the 27 cluster machines are, how each one gets code, host roles, hardware profiles,
and access protocols. Every number and address here reflects the real, measured 27-node
Boylenet topology (measured across origin/main and cluster telemetry). Re-measure before
relying on any of it; CLAUDE.md A1 applies to this document as much as to any other.

`~/.config/bd/roles` is the authoritative host population, and `~/.config/bd/hosts`
is derived from it. This file explains the topology; it does not replace the roles file.
A missing or unreadable roles file refuses passes with exit 3.

## The 27-Node Cluster Topology

| Host Name | IP Address | Cluster Role | Hardware / Spec | BD Serving | Deployment Protocol / Access |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **test5** (hub-mesh01) | `10.0.70.164` | `integrator` / Hub | 48 vCPUs, 256 GB RAM | Yes | NEVER deployed by bd-fleet-deploy (Hub controller) |
| **wrk-test04** (test3big) | `10.0.70.82` | `runner` / RAG & AST | 64 cores (84c vCenter), 128 GB RAM | Yes | Offload target: RAG indexing, AST parsing |
| **ai-infer01** | `10.0.70.125` | `satellite-ai` | Tesla T4 GPU, 16 GB VRAM | No | Satellite inference: Llama-Guard 3 8B (:11434) |
| **ai-ollama01** | `10.0.70.228` | `satellite-ai` | Tesla T4 GPU, 48 GB RAM | No | Satellite inference: Ollama Qwen2.5-Coder (:11434) |
| **battlestation** | `10.0.10.137` | `oracle` / `gpu` | Ryzen 9 3900X (24T), RTX 2080 Ti 11GB | No | CDP bridge (:9222); Rule 84: CPU embed, GPU coder |
| **bittorrent** | `10.0.70.181` | `oracle` / `vm` | Windows VM (32 vCPUs, 128 GB RAM) | No | CDP bridge (:9222) flat-rate oracle fanout |
| **test2** | `10.0.70.95` | `deploy` | 16 vCPUs, 64 GB RAM | Yes | Deployed via `bd-fleet-deploy.sh` (live production) |
| **test3** | `10.0.70.80` | `runner` | 8 cores, 31 GB RAM | Yes | Runner (held back during routine deploy) |
| **test4** | `10.0.70.85` | `runner` | 8 cores, 31 GB RAM | Yes | Runner (held back during routine deploy) |
| **test6** | `10.0.70.249` | `runner` | 16 cores, 62 GB RAM | Yes | Runner (held back during routine deploy) |
| **test7** | `10.0.70.84` | `runner` | 8 cores, 31 GB RAM | Yes | Runner (held back during routine deploy) |
| **test1** | `10.0.70.194` | `runner` | 16 vCPUs, 64 GB RAM | Yes | Runner |
| **bd** | `10.0.70.50` | `deploy` | 48 cores, 344 GB RAM | Yes | Deployed via `bd-fleet-deploy.sh` |
| **bd1** | `10.0.70.51` | `runner` | 48 cores, 344 GB RAM | Yes | Runner |
| **bd2** | `10.0.70.52` | `runner` | 48 cores, 344 GB RAM | Yes | Runner / bands & precut capacity |
| **bd3** | `10.0.70.53` | `runner` | 48 cores, 344 GB RAM | Yes | Runner / bands & precut capacity |
| **bd4** | `10.0.70.54` | `runner` | 48 cores, 344 GB RAM | Yes | Runner / bands & precut capacity |
| **fresh149** (test) | `10.0.70.149` | `capacity` | 16 vCPUs, 64 GB RAM | Yes | Capacity / agent work (never deployed) |
| **spare1** | `10.0.70.197` | `runner` / Hot Spare | 16 vCPUs, 32 GB RAM | Yes | Hot spare; dedicated Pool A VM (Rule 61) |
| **spare2** | `10.0.70.193` | `runner` / Hot Spare | 16 vCPUs, 32 GB RAM | Yes | Hot spare; dedicated Pool B VM (Rule 61) |
| **spare3** | `10.0.70.122` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Dedicated Pool C VM; booted via `bd-spare-boot.sh` |
| **spare4** | `10.0.70.81` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Dedicated Pool D VM; booted via `bd-spare-boot.sh` |
| **spare5** | `10.0.70.185` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare6** | `10.0.70.186` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare7** | `10.0.70.120` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare8** | `10.0.70.184` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare9** | `10.0.70.187` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare10** | `10.0.70.196` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare11** | `10.0.70.195` | `spare-pool` / Cold | 16 vCPUs, 32 GB RAM | No (Cold) | Cold spare (ESXi spr-pool, Rule 61) |
| **spare12** | `10.0.70.183` | `spare-pool` / Cold | 32 cores, 96 GB RAM | No (Cold) | Cold spare (esxi02, Rule 61) |
| **cir1** | `10.0.70.121` | `ci-runner` | 16 vCPUs, 32 GB RAM | No | Ephemeral CI runner clone |
| **cir2** | `10.0.70.86` | `ci-runner` | 16 vCPUs, 32 GB RAM | No | Ephemeral CI runner clone |
| **seatc1** | `10.0.70.35` | `seat` | 16 vCPUs, 32 GB RAM | No | Seat VM clone (O1752 batch-3) |
| **seatc2** | `10.0.70.171` | `seat` | 16 vCPUs, 32 GB RAM | No | Seat VM clone (O1752 batch-3) |
| **kvcache-cx1** | `10.0.70.198` | `cache` | 16 vCPUs, 32 GB RAM | No | KV cache clone (spare2 clone) |

## Access Protocols & Fabric Invariants

- **SSH Access**: Port 22 over the private `10.0.70.0/24` mesh. Commands executed with `BatchMode=yes` and `ConnectTimeout=3`.
- **Network MTU**: MTU 9000 (Jumbo frames) across all 25GbE interfaces. Verify with `ping -c 1 -M do -s 8972 <host_ip>`.
- **Chrome DevTools Protocol (CDP)**: Port 9222 on Windows Oracle hosts (`10.0.70.181`, `10.0.10.137`).
- **Satellite AI Ports**: LiteLLM Proxy on `:4000`, Ollama instances on `:11434`, HF TEI Embeddings on `:8081` (`bge-m3`), HF TEI Reranker on `:8082` (`bge-reranker-large`).
- **Shared Storage (NFSv4)**: Hub 164 exports `/mnt/bd` checkouts and review worktrees with `nfs-share.log` logging.
- **Rule 61 Cold Spare Elasticity**: spare1 and spare2 stay powered on. spare3 through spare12 remain powered off cold spares to conserve 448 GB RAM and 176 vCPUs. Automated elasticity is driven by `bd-spare-boot.sh`.
- **Rule 84 Hardware Segregation**: Node 137 strictly isolates embedding models to the Ryzen 9 CPU and high-throughput Coder models to the RTX 2080 Ti GPU.
- **PTY Kernel Headroom**: Sub-second PTY descriptor monitoring via `bd-pty-guard.py` (/proc/sys/kernel/pty/nr vs max), with throttle at 80% and backoff at 85%.

WHY RUNNERS ARE HELD BACK. Deploying onto a host mid-run restarts the service
underneath a measurement, which is the boundary A6 draws. The filter matches the
role word EXACTLY, so renaming a host to `runner` to add band capacity silently
turns it into something the deploy skips -- and renaming one away from `runner`
silently turns it into a deploy target.

WHY CAPACITY HOSTS RUN NO BD. They exist so measurement never runs on the tree
the integrator is editing. Bands run in per-SHA worktrees under ~/.bd-bands and
never touch the host checkout, so a runner can lend cores while it serves.

## How each host gets code, and the trap in it

    github origin   ALL CHECKOUTS
    local mirror    NO host fetches its checkout from one any more -- MEASURED
                    2026-09-02 across all twelve: 396 clones enumerated, ZERO
                    with any remote pointing at a bare mirror. But the mirrors
                    are NOT inert and MUST NOT BE DELETED. ~/bd.git exists on
                    EIGHT hosts -- bd, bd1, bd2, bd3, bd4, test2, test3, test6 --
                    and on six of them it is the LIVE BAND TRANSPORT:
                    bd-band-remote.sh pushes refs/heads/bd-band/<SHA> into it.
                    test2 alone held 85 band refs when this was measured.
                    `rm -rf ~/bd.git` on those six BREAKS BAND DISPATCH.
                    The only residue is a vestigial refs/heads/main on the five
                    hosts bd-freshhost-50.sh and bd-vm-bringup.sh created; a ref
                    delete is safe, a repo delete is not.

                    THIS ENTRY PREVIOUSLY SAID "five hosts ... inert; nothing
                    points at it", and both halves were wrong. A reader who
                    trusted it and tidied an "inert" mirror would have broken
                    six hosts. Re-measure before acting on any claim that
                    something is unused (CLAUDE.md A1).

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

## Windows Virtual Browser Oracle Nodes

    bittorrent     10.0.70.181   Windows VM (32 vCPUs, 128 GB RAM), port 9222 CDP bridge
    battlestation  10.0.10.137   Windows GPU workstation (Ryzen 3900X 24T, RTX 2080 Ti 11GB)

Persistent Chrome DevTools Protocol bridges on port 9222 provide flat-rate consumer
subscription oracle fanout (Gemini Ultra/Pro, ChatGPT Plus/Pro, Claude Account A/B).
