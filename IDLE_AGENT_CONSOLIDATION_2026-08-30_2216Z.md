# Idle-agent consolidation on test5

Captured and verified at 2026-08-30 22:16 UTC on host `test5`.

## Scope and termination result

The authoritative 12-host role population was probed in parallel. No remote
host had a live `codex exec` worker or a `bd-agent-*`/`cx-*`/Claude tmux agent
session. The three collaboration agents belonging to the active root thread
were already terminal `completed`; each received an explicit interrupt request
and reported its previous state as `completed`. They have no independent OS
process left to kill. Their concurrency slots are free.

The following were deliberately not terminated because they are not idle
workers:

- root Codex thread `01a050a2-c73d-7d53-92e1-313bc08bed38`;
- the active Claude integrator on test5;
- the then-active v3.66.1360 extracted-package gate on test3 (it subsequently
  finished and failed; final result below);
- Codex app-server/proxy, MCP, relay, dashboard, and tmux infrastructure.

Killing the shared Codex app server would also kill the root session and was
therefore neither necessary nor authorized by the idle-worker request.

## Verified immutable session snapshot

    /home/mboyle/bd-persist/codex-session-snapshots/20260830T221607Z

The snapshot inventories 103 root/descendant sessions and all 12 fleet hosts.
All four open JSONL sources were copied through complete lines with zero torn
tail bytes; each stored SHA-256 was recomputed successfully:

| Agent | Thread | SHA-256 | Lines |
|---|---|---|---:|
| root | `01a050a2-c73d-7d53-92e1-313bc08bed38` | `dc73f6d07547ddabd93504b4af5f61be49e5f903b115c2a76ab01417b2defef7` | 29,358 |
| row399 remediation | `01a053e6-7a51-7150-8aea-6ef1e909f9bc` | `3a39497afac88d2b8604274e0d48c3693a5ffe3f8e42d1f0b33d2c2666c3b3a6` | 3,952 |
| v1360 gate triage | `01a05456-2cdf-7d63-9d03-db5932e21dc6` | `6b0a831124786b10319a7c06cc58f83e8fc82a70fa42920a4d32ae6e19244d9f` | 297 |
| fleet result triage | `01a0545a-03fc-7c63-9050-93ee75768ab7` | `5efb4726ed9ef53e771a0ebf95f0ae47178630d61d3ef6f6129d81613c7dfb4b` | 102 |

The source JSONLs under `/home/mboyle/.codex/sessions` remain the official
resume authority. No authentication file or credential value was copied.

## Consolidated completed-agent results

### Row 399 remediation

    branch    candidate/row399-remediation-onmain
    worktree  /home/mboyle/BulkDownloader/.worktrees/row399-remediation-onmain
    base      de6240f454df10d912275d5b0842ae53a2f3107c
    commit    80ccbc0c51657e37fcf3c05fc681158bc7db901e
    tree      04538ac1b551b236e12d642154183385c5d1e9db

Tracked state is clean. The agent made no merge, push, deploy, register, or main
change. Final focused evidence is 124/124; affected band 210/210;
generated/sync 31/31; precut 74/74. The earlier complete 481-suite band was
472/481 before the final one-line ordering fix, so a final-tree complete band
still must run after v1360 changes the base. Full details and residual risks are
in the 18:00 handoff.

### v3.66.1360 gate triage

The original triage agent completed without writes. The root subsequently
advanced PR 657 to exact candidate `f2b80a5cfab53b3512d8f4b401e9778e84b28660`,
tree `7858821bcc80aa9f933be9cb4ce817621081e43a`. GitHub CI, precut, and the
provisioned exact-head 81-file fleet rerun are green. The final/last authorized
package attempt then finished with rc 1: 10,876 total, 9,366 passed, 1,498
failed, and 12 skipped. PR 657 is therefore HOLD and was not merged. Per the
operator's last-attempt instruction, no package retry was launched. The exact
log, artifact, and next diagnostic steps are in the final handoff.

### Fleet result triage

Canonical report:

    /home/mboyle/fleet-run-artifacts/2026-08-30/fleet-burst/RESULT_TRIAGE.md

It records 20 terminal burst workers, six clean candidates, row089 held, and
12 incomplete workers. Nothing from that set was auto-integrated or refilled.

## Primary handoff

The full operational handoff, including live safety state, recovery refs,
runner logs, exact merge conditions, register constraints, tooling defects,
and row399 next steps, is:

    /home/mboyle/bd-persist/HANDOFF_2026-08-30_1800_EST.md
