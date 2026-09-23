# CENSUS-row1029.md
Caller and consumer census for Row 1029 (Generational Garbage Collection Tuning and Dynamic Cycle Collection Pauser - AdaptiveGCController).

## Preserved Interfaces
- `bulk_downloader/dev_suite/introspection.py` is untouched (the unreferenced `run_adaptive_gc` wrapper of the first build is withdrawn; `force_gc` unchanged).
- Zero new `BD_` environment variables and zero new config keys; config parity ratchet preserved.

## Concrete Callers by File Path
- `bulk_downloader/runner.py` `SiteRunner._worker_loop`: every download worker enters
  `get_adaptive_gc_controller().workload("<site>/<idx>")` on its ExitStack just before browser launch;
  the lease is released in the worker's existing `finally` (`_ns_stack.close()`), after the browser has closed.
- New import edge: `bulk_downloader.runner -> bulk_downloader.adaptive_gc` (module level, beside `netns_isolation`).

## New Interfaces & Components (`bulk_downloader.adaptive_gc`)
- `decide_gen0(gc_seconds, wall_seconds, gen0, baseline_gen0, config)`: pure policy from measured collector overhead.
- `AdaptiveGCController.begin_workload/end_workload/workload`: reference-counted lease. First lease: `gc.callbacks`
  monitor + high-throughput gen0. Measured windows adapt gen0 (`adapt`). Last release: monitor removed, baseline restored,
  deferred full collection (`collect_adaptive(2)`).
- `stats()`, `active_workloads`; existing `tune_for_workload`, `critical_section_gc_paused`, `collect_adaptive` kept.

## Scope & CI Shard
- `tests/test_row1029_adaptive_gc_controller.py` declared with `BD_GATE_SCOPE = "module"`.
