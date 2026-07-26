# OPV live evidence update — 2026-07-23

This report records the secret-free disposition of the 2026-07-23 live work.
It does not promote or enable a template, cut over a database, or modify the
production download queue.

## Outcomes

| Work item | Result | Remaining gate |
| --- | --- | --- |
| `OPV-F3.1` | Seven-day clock started at `2026-07-23T01:37:15Z` with one fixture-only daily rule (`daily_cap=1`). The first natural scheduler run enqueued one fixture item; downstream dedup wrote zero bytes. The production FilthyKings queue remained untouched at 962. | Preserve an uninterrupted 168-hour observation through `2026-07-30T01:37:15Z`. |
| `EXIT-3` | Selected the dedicated PostgreSQL 16 `bd-opv-postgres` target on loopback port 55432. Backup/restore, an isolated 38-test MOD3 suite, and a bounded 23/23 history rehearsal passed. | Full schema/data parity failed, live shadow diverged, and fail-closed preflight refused cutover. No cutover or 14-day soak clock started. |
| `P3-T12-CALLSITE` | A real Reptyle challenge reached detector pause and human handoff; the human then completed CAPTCHA/login and authenticated play/download activity followed in the same Cloak capture. | The journal has no explicit detector-cleared/resume event. Keep the row open; later authenticated activity is not substituted for that event. |
| `RPTYL` | Authenticated persistent-Cloak capture verified home, play, and download. The redacted WACZ has zero residual secret findings. A high-confidence disabled/review-only HLS draft was produced. | The draft has `api_patterns=0`, so the legacy `api_patterns>=1` close criterion remains unmet. |
| `2c-DATA` | Re-capture produced five selector kinds: download, login, player, quality, and settings. | Review and replay-test those selectors before any use. |
| `CORPUS-DISPOSITION` | All 445 capture-SHA rows in eight batches received the explicit `retain_review_required` disposition under `retain_review_required_non_circumvention`: 445 retained, 0 rejected, 0 promoted, 0 auto-enabled. | Separate per-template review and tests are still required before any future promotion. |

## Evidence ledger

| Evidence | Size | SHA-256 |
| --- | ---: | --- |
| `/home/mboyle/.bd21-backups/opv-f31-20260723T012540Z/RESULT.json` | 1,359 bytes | `1d74f3338c78bb73754ddb3a8a4c44153b1ee4674042e60c5a86ee18d9e80d02` |
| `/home/mboyle/.bd21-backups/20260723T012708Z-exit3-postgres/EXIT3_EVIDENCE_REPORT.md` | 8,096 bytes | `81cb9fa1fde9a8d7b2beb7dd68ba21d15a06fb299d4e34d965418af52fa36362` |
| `/home/mboyle/.bd21-quarantine/RPTYL-auth-20260723T012112Z/reptyle-auth.redacted.wacz` | 4,191,695 bytes | `c9c4a59455808798d4b7687b0c7e97f0cf66eec54d3fa1cb62a8a98fdf6be723` |
| `/home/mboyle/.bd21-backups/RPTYL-auth-20260723T012112Z/reptyle-auth.template-draft.json` | 35,288 bytes | `8d4ea97cd5e29d4fb18a28ed95750d796911ac0728f69af6d2ccd6a136360fb7` |
| `/home/mboyle/.bd21-backups/corpus-semantic-review-445.json` | 424,462 bytes | `547d5d8d281de0e995e48de436271d1df8592495a4dfae227e651e38ecee1e93` |

The raw Reptyle capture remains quarantine-only. The unrelated
`pump-tracker-db-1` PostgreSQL service was not selected, connected to, or
changed. No production DSN/service environment was created, the
BulkDownloader service was not restarted for EXIT-3, and
`cutover_engaged()` remained false.
