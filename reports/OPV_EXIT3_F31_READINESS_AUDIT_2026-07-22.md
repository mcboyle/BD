# EXIT-3 and OPV-F3.1 cutover/readiness audit

**Audit time:** 2026-07-22 20:16 EDT
**Scope:** read-only stash inspection; no service configuration, database, saved-search rule, cutover flag, or observation clock was changed.
**Disposition:** both tracker rows remain open. This document contains no DSN or credential value.

## Executive result

| Gate | Current result | Earliest legitimate next state |
|---|---|---|
| EXIT-3 | Not ready to start the two-week soak. A Postgres engine is available, but BulkDownloader has no operator-owned target/DSN, shadow reads are off, cutover is off, and there is no in-process surface for the live shadow denominator. | Operator selects and provisions the target, stores the DSN in a root-readable environment file, enables dual-write plus shadow-read without cutover, and adds/uses a secret-free in-process status surface. Rehearsal and preflight must pass before cutover. |
| OPV-F3.1 | Scheduler is healthy, but there are zero saved-search rules. No seven-day clock has started. | Operator approves one bounded, sanctioned `action=enqueue` rule. After it is created, record seven complete uninterrupted days of the evidence below. |

## EXIT-3: real-Postgres cutover readiness

### Read-only evidence observed

- `bulkdownloader.service` was active and `/api/health` returned `ok=true`, version `3.66.817`, with queue depth 962.
- The running service environment had no `MOD3_PG_DSN`, `MOD3_SHADOW_READ` was false, and `MOD3_CUTOVER` was false.
- The fallback SQLite source database exists at `/home/mboyle/BulkDownloader/downloader_history.db`; `history` contained 23 rows. The source is therefore non-empty and can produce a meaningful rehearsal denominator.
- The service virtual environment can import `psycopg`.
- Host `psql` and `pg_isready` are not installed, and `postgresql.service` is inactive. Those facts are not the primary blocker because TCP port 5432 is listening and two running Postgres containers were found:
  - `pump-tracker-db-1`: `postgres:16-alpine`, bound to `127.0.0.1:5432`, running and healthy.
  - `bd-opv-postgres`: `postgres:16-alpine`, bound to `127.0.0.1:55432`, running; no container healthcheck is defined.
- Neither container is assumed to be authorized for BulkDownloader. Container credentials and environment values were deliberately not inspected.

### Exact blocker

The blocker is ownership and observability, not merely package installation:

1. No operator-approved Postgres database/role and secure DSN have been selected for MOD3.
2. With no DSN, dual-write is disabled; with shadow-read disabled, the live service has made zero shadow comparisons.
3. The shadow comparison counters are process-local memory. The repository exposes no route or command that calls `pg_backend.stats()`, `shadow_stats()`, or `preflight_cutover()` in the running service process. Importing `pg_backend` in a separate one-shot Python process creates fresh counters, so that process cannot honestly attest the service's live shadow denominator.
4. Consequently, no preflight-approved cutover or two-week post-cutover soak can begin yet.

### Preflight sequence (run only after operator authorization)

The commands below avoid printing the DSN. `/etc/bulkdownloader/mod3.env` is an example root-owned path; the operator may choose another service environment file.

1. Verify the secure file and source without revealing values:

```bash
sudo test -r /etc/bulkdownloader/mod3.env
sudo grep -q '^MOD3_PG_DSN=' /etc/bulkdownloader/mod3.env
sudo stat -c 'mode=%a owner=%U group=%G' /etc/bulkdownloader/mod3.env
test -s /home/mboyle/BulkDownloader/downloader_history.db
```

Expected: the environment file is root-owned and not world-readable; the SQLite file is non-empty.

2. Rehearse the migration against an automatically removed scratch schema. Keep `MOD3_CUTOVER` absent/false:

```bash
cd /home/mboyle/BulkDownloader
sudo -u mboyle bash -lc '
  set -a
  . /etc/bulkdownloader/mod3.env
  set +a
  venv/bin/python - <<"PY"
from bulk_downloader import pg_backend as pg
print({
    "dsn_present": bool(pg.pg_dsn()),
    "shadow_requested": pg.shadow_read_enabled(),
    "rehearsal": pg.rehearse_migration(),
})
PY
'
```

Required result: DSN present, rehearsal `ok=true`, a positive source-row count, equal source/target counts, content match, and no retained scratch schema.

3. Before any cutover, configure the service for DSN-backed dual-write and `MOD3_SHADOW_READ=1` while leaving `MOD3_CUTOVER` absent/false. Restart only in an operator-approved maintenance window. Exercise representative history reads and writes long enough to create a non-zero live comparison denominator.

4. Add or use a **read-only, secret-free, in-process** status endpoint/CLI. It must call the running process's `pg_backend.stats()`, `shadow_stats()`, and `preflight_cutover()`; a separate Python import is not equivalent. Required evidence immediately before cutover:

```text
dual_write = true
shadow_read = true
shadow_compared > 0
shadow_diverged = 0
postgres reachable = true
degraded_reason = empty
preflight ok = true
rehearsal ok = true
```

5. Only after step 4 passes, set `MOD3_CUTOVER=1`, restart in the approved window, verify the service and health endpoint, and run the full on-stash suite. The EXIT-3 soak starts only when the cutover and that suite are green and the T0 evidence bundle is saved.

### Rollback contract

The implementation continues SQLite writes while Postgres is authoritative. The first rollback action is therefore to remove `MOD3_CUTOVER` or set it false; do not delete or rewrite either database.

```bash
# Edit only the root-owned service environment so MOD3_CUTOVER is absent/false.
sudo systemctl daemon-reload
sudo systemctl restart bulkdownloader.service
sudo systemctl is-active bulkdownloader.service
curl -fsS http://127.0.0.1:5555/api/health
```

Then verify a known history read and one reversible test write through the normal application path, capture SQLite row/content evidence, and preserve Postgres for diagnosis. Re-enable cutover only after the same rehearsal and live in-process preflight gates pass again.

### Required two-week soak evidence

Save a secret-free evidence bundle with exact UTC timestamps.

**T0 (the clock starts here, not at this audit):**

- deployed version and Git SHA; service-unit/config file hashes (never contents); chosen target identifier without credentials;
- successful scratch-schema rehearsal and live in-process preflight JSON;
- SQLite and Postgres row/content comparison, including a positive comparison denominator and zero divergences;
- active service, healthy API, queue/status baseline, and full on-stash suite green after cutover;
- exact soak start time.

**At least once per UTC day for 14 complete 24-hour intervals:**

- service active state, `/api/health`, queue depth/active work, scheduler health, and relevant resource/error summary;
- dual-write/shadow/cutover state, Postgres reachability, degraded reason, live compared/diverged counters, and preflight result;
- SQLite/Postgres row and content parity evidence;
- journal errors/timeouts/reconnects since the previous sample;
- any restart, outage, configuration change, rollback, or missing sample, with its exact interval and disposition.

**Exit evidence:**

- end timestamp at least 336 hours after T0 with uninterrupted evidence coverage;
- zero unresolved shadow divergences or degraded periods;
- final parity/preflight evidence and a final full on-stash suite green;
- an operator sign-off that the soak was representative. A clock interruption or cutover rollback resets the soak unless the operator's approved criterion explicitly documents otherwise.

## OPV-F3.1: seven-day saved-search enqueue readiness

### Read-only evidence observed

- Background scheduler endpoint: HTTP 200; scheduler `running=true`, not idle, poll interval 30 seconds, 22 tasks.
- `saved_searches.run_due`: enabled, interval 300 seconds, last status `ok`, run count 1, last duration 0.068 seconds, no error, and next run due normally at the sample time.
- Saved-search endpoint: HTTP 200; total rules 0, enabled rules 0, enqueue rules 0.
- The scheduler is therefore ready, but no rule exists and **the seven-day observation clock has not started**.

The scheduler polls due rules every 300 seconds; each rule's own schedule controls actual execution. Supported schedules are hourly, daily, weekly, and manual. Hourly is the recommended observation schedule because daily or weekly yields too few samples for a meaningful seven-day gate.

### Exact sanctioned rule fields needed

The operator must approve the query/site scope and cap before rule creation. Use the normal `POST /api/saved_searches` surface with:

| Field | Required value/decision |
|---|---|
| `name` | Unique, secret-free evidence name |
| `query` | Non-empty operator-sanctioned query; do not place credentials or tokens here |
| `site_id` | Optional operator-sanctioned site restriction |
| `status` | Optional sanctioned result-status restriction |
| `schedule` | `hourly` recommended for this seven-day observation |
| `notify_via` | Empty unless the operator separately authorizes notifications |
| `action` | `enqueue` |
| `daily_cap` | Explicit low positive operator-selected bound; source default is 25 |
| `enabled` | `true` |

No rule was created by this audit. Once approved, save the returned rule ID and creation timestamp as F3.1 T0. Confirm its serialized fields before allowing the first due run.

### Seven-day observation checklist

Record the following at T0 and at least daily for seven complete 24-hour intervals; hourly snapshots around the first run and UTC cap reset are preferable.

- Scheduler: `running`, target task `enabled`, `last_status`, `run_count`, `last_run_seconds_ago`, `last_error`, and last duration.
- Rule: `enabled`, `action`, `schedule`, `daily_cap`, `last_run_ts`, `last_seen_id`, `new_since_last`, `enqueued_count`, `enqueued_day`, and `enqueued_total`.
- Queue: depth and status counts, accepted-count delta, and the identifiers/URLs admitted by this rule in a redacted evidence form.
- Controls: duplicates rejected, gate/policy rejections, cap-hit/capped counts, and downstream admission failures.
- Daily-cap invariant: accepted enqueue count never exceeds `daily_cap` for a UTC day; `enqueued_count` resets on the next UTC day while `enqueued_total` remains cumulative.
- Dedup invariant: the same normalized URL is never accepted twice, including across scheduler runs and day boundaries.
- Gate invariant: saved-search enqueue uses the ordinary downstream admission/safety gates; no special bypass occurs.
- Continuity: exact timestamps for service/scheduler outages, rule edits, disabling, or missing samples. Any material interruption restarts the seven-day gate unless the operator's approved criterion explicitly says otherwise.

At the end, save an exact end timestamp at least 168 hours after T0, a rule/queue summary, cap/dedup/gate conclusions, and operator sign-off. Disable or delete the evidence rule only after the full observation bundle is preserved.

## Tracker disposition

- `EXIT-3`: remain **AWAITING OPERATOR**. No cutover or two-week clock started.
- `OPV-F3.1`: remain **AWAITING OPERATOR (week-long live verify)**. No sanctioned rule or seven-day clock started.
