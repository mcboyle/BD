"""Phase 9.4 -- LLM cache keyed by input hash.

First-class caching for LLM results. The cache KEY is a composite hash over:
task id, prompt id, prompt version, schema version, provider, model, the normalized
input hash (from `llm_exec.input_hash`), a relevant config hash, and a deterministic
preprocessor version. Because the key includes prompt/schema/model, a change to any
of them naturally misses (invalidation by construction).

Safety rules (enforced here + in `llm_exec.execute`):
  * the cache stores the VALIDATED OUTPUT only -- never the raw input, so raw
    secrets are never persisted (the input only ever appears as a hash in the key);
  * a cache hit does NOT bypass review gates (the served LLMResult keeps the spec's
    review_required / advisory flags);
  * cached output is re-validated against the schema on read (see execute);
  * the cache is clearable.

Storage is in-process (sufficient for the sandbox and for a single worker); a
persistent backend can replace `_store` later without changing the interface.
"""

import hashlib
import time
from typing import Any, Dict, Optional

# key -> {"value", "stored_at", "schema_version", "prompt_version"}
_store: Dict[str, Dict[str, Any]] = {}
_stats = {"hits": 0, "misses": 0, "sets": 0}


def cache_key(spec, *, schema_version: Optional[str] = None,
              config_hash: Optional[str] = None,
              preproc_version: Optional[str] = None) -> str:
    """Composite cache key for a spec. Pulls components from the spec, with
    optional explicit overrides. Uses the normalized input hash (never raw input)."""
    from . import llm_exec
    ih = llm_exec.input_hash(spec)
    provider, model = llm_exec._resolve_provider_model(spec)
    parts = [
        spec.task_id,
        spec.prompt_id,
        spec.prompt_version,
        schema_version if schema_version is not None else getattr(spec, "schema_version", ""),
        provider,
        model,
        ih,
        config_hash if config_hash is not None else getattr(spec, "config_hash", ""),
        preproc_version if preproc_version is not None else getattr(spec, "preproc_version", ""),
    ]
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def get(key: str) -> Optional[Dict[str, Any]]:
    entry = _store.get(key)
    if entry is None:
        _stats["misses"] += 1
        return None
    _stats["hits"] += 1
    return entry


def set(key: str, value: Any, *, schema_version: str = "",
        prompt_version: str = "") -> None:
    """Store a VALIDATED output value. Never pass raw input here."""
    _store[key] = {
        "value": value,
        "stored_at": time.time(),
        "schema_version": schema_version,
        "prompt_version": prompt_version,
    }
    _stats["sets"] += 1


def clear() -> None:
    _store.clear()
    _stats.update({"hits": 0, "misses": 0, "sets": 0})


def size() -> int:
    return len(_store)


def stats() -> Dict[str, int]:
    total = _stats["hits"] + _stats["misses"]
    out = dict(_stats)
    out["hit_rate"] = (_stats["hits"] / total) if total else 0.0
    out["size"] = len(_store)
    return out
