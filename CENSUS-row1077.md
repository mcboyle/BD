# CENSUS-row1077.md
Caller and consumer census for Row 1077 (Connection Liveness Monitoring & Health Probing).

## Owned Paths
- `bulk_downloader/connection_liveness.py`: Active TCP socket probe with high-resolution latency measurement, configurable failure and recovery hysteresis thresholds, state machine (HEALTHY, DEGRADED, DOWN, UNKNOWN), state transition event listener callback dispatch, background thread monitoring daemon, telemetry aggregation summary, and process-wide singleton accessors.

## Integrated Callers
- `bulk_downloader/runner_transport.py` `TransportMixin._try_multi_conn_download`: after `should_use_multi_conn` approves, calls `_mconn.check_connection_liveness(pr.final_url or file_url)`; anything but `healthy` (or a raised check) falls back to single-connection. Skipped when `proxy_url` is set or row 1065's egress router is configured (`multi_homed_egress.get_multi_homed_router().is_configured()`): a raw TCP connect would bypass that proxy/VPN/interface egress, and the guarded HTTP probe above already went through it.
- `bulk_downloader/multi_conn.py`: `check_connection_liveness` / `get_connection_liveness_monitor` wrappers (exported).
- SSRF: `probe_socket` refuses any resolved non-public address via `provider_resolve_impl._common._classify_ip` (same predicate as multi_conn's guard) unless the monitor is built with `allow_private=True` (tests only). Only vetted addresses are connected: each in resolver order until one connects (dual-stack fallback); latency is timed from the first connect attempt, not from name resolution.

## Preserved Interfaces
- All existing function signatures and behaviors in `multi_conn.py` preserved intact (`download`, `probe`, `plan_chunks`, `adaptive_chunk_count`).
- Zero new `BD_` environment variables introduced; config parity ratchet preserved intact.

## New Interfaces & Components
- `bulk_downloader.connection_liveness`:
  - `LivenessState`: Enum ("healthy", "degraded", "down", "unknown").
  - `EndpointTarget`: Dataclass target representation with `from_target()` parsing URLs, host:port, bare hosts and bare IPv6 literals; a target with no host raises `ValueError`.
  - `ProbeResult`: Probe outcome dataclass with `to_dict()` conversion.
  - `probe_socket(host, port, timeout_s=2.0, allow_private=False) -> (bool, float, str | None)`.
  - `ConnectionLivenessMonitor`: Thread-safe monitor orchestrator with registration, hysteresis, callback dispatch, daemon execution, and summary aggregation.
  - `get_connection_liveness_monitor() -> ConnectionLivenessMonitor`.
  - `reset_connection_liveness_monitor() -> None`.
- `bulk_downloader.multi_conn`:
  - `check_connection_liveness(target: str, default_port: int = 80, timeout_s: float = 2.0) -> dict`.
  - `get_connection_liveness_monitor() -> ConnectionLivenessMonitor`.

## Scope & CI Shard
- `tests/test_row1077_connection_liveness_health_probing.py` declares `BD_GATE_SCOPE = "repo-wide"`; `tools/ci_shards.py` selects it in exactly one shard, `gates-rows-a` (glob `tests/test_row[0-5]*.py`).
- Passes the CI shard gates (`tests/test_ci_shards.py`, `tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py`).
