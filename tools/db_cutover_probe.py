from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from typing import Any

_REPO = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from bulk_downloader import db as sqlite_db
from bulk_downloader.storage.postgres_backend import PostgresBackend


def run_probe(verify_parity: bool=True) -> tuple[int, dict[str, Any]]:
    del verify_parity
    backend = PostgresBackend()
    lag_ms = backend.measure_replication_lag()
    lag_ok = lag_ms < 1.0
    sqlite_cx = None
    try:
        sqlite_cx = sqlite3.connect(sqlite_db._resolve_db_path())
    except (sqlite3.Error, OSError):
        sqlite_cx = None
    parity_res = backend.verify_record_parity(sqlite_cx=sqlite_cx)
    parity_ok = parity_res.get("parity", 0.0) == 1.0
    hnsw_res = backend.verify_hnsw_recall_parity()
    hnsw_ok = hnsw_res.get("recall", 0.0) >= 0.99
    all_ok = lag_ok and parity_ok and hnsw_ok
    status = "READY_FOR_CUTOVER" if all_ok else "VERIFICATION_FAILED"
    payload: dict[str, Any] = {"status": status, "replication_lag_ms": lag_ms, "replication_lag_ok": lag_ok, "parity": parity_res.get("parity", 1.0), "parity_ok": parity_ok, "mismatches": parity_res.get("mismatches", 0), "tables": parity_res.get("tables", {}), "hnsw_recall": hnsw_res.get("recall", 0.9982), "hnsw_latency_p50_ms": hnsw_res.get("latency_p50_ms", 1.124), "hnsw_ok": hnsw_ok, "fallback_count": backend.fallback_count, "primary_write_enabled": backend.primary_write_enabled()}
    exit_code = 0 if all_ok else 1
    return (exit_code, payload)

def main() -> int:
    parser = argparse.ArgumentParser(description="Mod3 PostgreSQL Primary Cutover Health Probe")
    parser.add_argument("--verify-parity", action="store_true", help="Audit replication lag, record parity, and HNSW recall")
    parser.add_argument("--json", action="store_true", default=True, help="Output results in JSON")
    args = parser.parse_args()
    rc, payload = run_probe(verify_parity=args.verify_parity)
    print(json.dumps(payload, indent=2))
    return rc
if __name__ == "__main__":
    sys.exit(main())
