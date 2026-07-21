# Task 5 report: real-PostgreSQL CI

## Change

Added the `postgres-integration` job to `.github/workflows/ci.yml`.
It starts an isolated `postgres:16` service with the disposable `mod3_ci`
database and non-production credentials, waits for `pg_isready`, installs
`requirements-dev.txt` plus `psycopg[binary]>=3.1,<4`, and scopes
`MOD3_PG_TEST_DSN` to the test command.

The command names only these four files:

1. `tests/test_v3_66_800_mod3_dual_write.py`
2. `tests/test_v3_66_801_mod3_shadow_read.py`
3. `tests/test_v3_66_803_mod3_migration_rehearsal.py`
4. `tests/test_v3_66_804_mod3_cutover.py`

The existing `gates` job is unchanged.

## Validation

- `actionlint v1.7.12 .github/workflows/ci.yml` exited 0.
- With `MOD3_PG_TEST_DSN` and `MOD3_PG_DSN` unset, the exact four-file pytest
  command collected and ran successfully: `23 passed, 15 skipped in 1.65s`.
- No local Docker or Podman executable was available, so the 15 real-Postgres
  tests could not be run against a local disposable service. GitHub Actions
  will provide the isolated PostgreSQL 16 service for that run.

No stash or production database was contacted.
