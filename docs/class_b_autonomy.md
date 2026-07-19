# Controlled Class B Autonomy (Phase F)

*v3.66.125. The first step of the approved F–H roadmap. Operationalises Class B
(reversible housekeeping) so it can run autonomously — while Class C and D stay
human-controlled.*

## What Phase F is

Class B itself (the reversible actions) was built in Phase B. Phase F adds the
*operating layer* (`tools/autonomy_center.py`):

- **An autonomy cycle** (`run_autonomy_cycle`) — explicitly invoked by the HOST
  (cron/timer/CLI), **not** a daemon thread inside the app. It applies only when ALL of:
  not frozen (kill switch), Class B at `auto_with_guardrails`, and the host has set
  `BD_AUTONOMY_ENABLED`. Otherwise it runs as a **dry-run** (suggest). The default
  posture (fresh policy, no env flag) is therefore a dry-run. Each cycle records a
  decision snapshot (Phase A), is logged, and explains the mode it ran in.
- **Extra Class B actions** — `artifact_maintenance` (archives review packets beyond a
  retention count; reversible) plus three **read-only monitors**:
  `freshness_monitoring`, `governance_monitoring`, `review_deadline_tracking`.
- **Six read-only operating views** — Autonomy Center, Queue Intelligence, Review
  Operations, Notification Center, Governance Health, Automation Metrics.

## The gate chain (why it's safe by default)

```
frozen?                        -> SKIPPED   (kill switch wins over everything)
Class B not at auto?           -> SUGGEST   (dry-run)
BD_AUTONOMY_ENABLED not set?   -> SUGGEST   (dry-run — the host's final apply switch)
otherwise                      -> APPLY
```

Every branch is explainable and surfaced in the cockpit. Class C and D are never
touched — the cycle runs only Class B actions (no selector/template/login/workflow/
corpus/debt changes, no capture/login execution, no third-party interaction).

## Boundaries (enforced, tested)

- no live config writes, no corpus writes, no debt changes;
- no external activity (no requests/urllib/httpx/socket/smtp/webhook/push);
- no browser actions; no capture/login execution; no subprocess;
- no in-process background scheduler (the cycle is an explicit host-scheduled tick);
- monitoring actions are strictly read-only (mode `monitor`, never `apply`);
- artifact maintenance is reversible and logged; kill-switch-gated.

## Running it (host-scheduled)

The cockpit shows what cycles did; it does not trigger them (no new POST). The host
schedules the tick, e.g. cron:

```
# dry-run by default; applies only once Class B is opted in AND the flag is exported
BD_AUTONOMY_ENABLED=1 bd python3 -c \
  "from tools.autonomy_center import run_autonomy_cycle; print(run_autonomy_cycle('cron'))"
```

To preview without applying anything: `cycle_preview()` (a forced dry-run).

## Cockpit

A new **Autonomy** nav section with the six views. Endpoints (GET):
`/api/autonomy/center`, `/queue`, `/review-ops`, `/notifications`,
`/governance-health`, `/metrics`. Cockpit route count 124; POST surface unchanged.

## Success criterion (met)

Class B operates autonomously (when opted in + the host flag is set) while Class C and
D remain human-controlled at Approve-each.

## Next

Phase G — limited correctness-automation *infrastructure*: per-site eligibility
evaluation + decay, rollback automation, impact analysis, validation scheduling,
promotion auditing, trust scoring. It evaluates whether Class C is safe per-site
*without broadly enabling it*. Phase H — the trust/governance/self-regulation layer
(trust may only ever decrease automatically). Each ships independently, review-gated.
