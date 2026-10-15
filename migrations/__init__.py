from __future__ import annotations
from typing import Any, Dict, List, Optional
from . import m001_ensure_history_indexes as m001, m002_add_history_retry_after as m002, m003_create_library_table as m003, m004_create_tags_tables as m004, m005_add_history_library_back_ref as m005, m006_add_history_removed_at as m006, m007_add_history_honeypot_score as m007, m008_add_history_bytes_fetched as m008, m009_add_history_transfer_mode as m009, m010_add_library_title_source as m010, m011_add_history_egress_ip as m011, m012_add_covering_indexes as m012
MODULES = [m001, m002, m003, m004, m005, m006, m007, m008, m009, m010, m011, m012]
MIGRATIONS: List[Dict[str, Any]] = [{"version": mod.VERSION, "name": mod.NAME, "fn": mod.up, "down_fn": mod.down} for mod in MODULES]

def get_migration(version: int) -> Optional[Dict[str, Any]]:
    for m in MIGRATIONS:
        if m["version"] == version:
            return m
    return None
