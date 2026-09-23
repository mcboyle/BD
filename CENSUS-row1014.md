# CENSUS-row1014.md
Caller and consumer census for Row 1014 (Asynchronous Non-Blocking Event-Driven WAL Flusher Pipeline).

## Preserved Interfaces
- All existing methods and functions in `bulk_downloader/db_maintenance.py` preserved byte-compatible.
- Zero existing function or method signatures modified.
- Config panel and environment: Zero new `BD_` environment variables introduced; config parity ratchet preserved intact.

## Concrete Callers by File Path
- `bulk_downloader/bg_scheduler.py` `register_default_tasks` -> task `db.wal_flush` (300 s) -> `db_maintenance.run_scheduled_wal_flush` (NEW EDGE bg_scheduler -> db_maintenance, lazy import; db_maintenance -> db, lazy import).
- `bulk_downloader/db_maintenance.py`:
  - `run_scheduled_wal_flush`: enqueues a PASSIVE flush of the app DB; raises if the previous scheduled flush failed or never resolved.
  - `schedule_async_wal_flush`: enqueues on `async_wal_flusher.get_async_wal_flusher()` (the ONE singleton).
  - `trigger_async_wal_flush`: same singleton; returns False when no database is named.
- The db_maintenance copy of the singleton (`_GLOBAL_WAL_FLUSHER`, `get_async_wal_flusher`) is removed.

## New Interfaces & Components
- `bulk_downloader.async_wal_flusher`:
  - `FlushEvent`: Event model encapsulating event id, db path, mode, and timestamp.
  - `FlushResult`: Execution result tracking busy status, log frames, checkpointed frames, and latency.
  - `FlusherConfig`: Configuration container for intervals, batch sizes, queue depth, and defaults.
  - `FlusherMetrics`: Accounting metrics for enqueued, pending, flushed, and coalesced events.
  - `AsyncWalFlusherPipeline`: Core background flusher pipeline executing coalesced checkpoints on SQLite WAL databases.
  - `get_async_wal_flusher() -> AsyncWalFlusherPipeline`: Global singleton accessor.
  - `reset_async_wal_flusher() -> None`: Test and reset lifecycle helper.

## Scope & CI Shard
- `tests/test_row1014_async_event_wal_flusher.py` declared with `BD_GATE_SCOPE = "module"`.
- Isolated from CI shard collisions; passes full gate suite cleanly.
