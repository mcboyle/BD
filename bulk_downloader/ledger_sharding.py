"""bulk_downloader.ledger_sharding -- Multi-Tenant Ledger Partition Sharding by Epoch & Domain.

Provides horizontal partitioning and time-based epoch sharding for download provenance
and audit ledgers. Isolates multi-tenant ledger entries into discrete partition shards
by (tenant_id, domain, epoch), eliminating lock contention, enabling independent
tamper-evident Merkle/hash chains, and facilitating immutable epoch sealing.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import time
import urllib.parse
from typing import Any, Optional


@dataclasses.dataclass(frozen=True)
class ShardKey:
    """Routing key uniquely identifying a ledger partition shard."""
    tenant_id: str
    domain: str
    epoch: int

    @property
    def partition_id(self) -> str:
        return f"{self.tenant_id}::{self.domain}::epoch_{self.epoch}"


@dataclasses.dataclass(frozen=True)
class PartitionMetadata:
    """Descriptive metadata and current state of a partition shard."""
    partition_id: str
    tenant_id: str
    domain: str
    epoch: int
    created_at: float
    row_count: int
    chain_head: str
    is_sealed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_id": self.partition_id,
            "tenant_id": self.tenant_id,
            "domain": self.domain,
            "epoch": self.epoch,
            "created_at": self.created_at,
            "row_count": self.row_count,
            "chain_head": self.chain_head,
            "is_sealed": self.is_sealed,
        }


@dataclasses.dataclass
class PartitionedLedgerEntry:
    """Discrete immutable ledger entry within a partition shard."""
    id: int
    tenant_id: str
    domain: str
    epoch: int
    timestamp: float
    data: dict[str, Any]
    content_hash: str
    chain_hash: str

    def to_dict(self) -> dict[str, Any]:
        res = dict(self.data)
        res.update({
            "_partition_id": f"{self.tenant_id}::{self.domain}::epoch_{self.epoch}",
            "tenant_id": self.tenant_id,
            "domain": self.domain,
            "epoch": self.epoch,
            "content_hash": self.content_hash,
            "chain_hash": self.chain_hash,
            "timestamp": self.timestamp,
        })
        return res


def _compute_content_hash(data: dict[str, Any]) -> str:
    """Deterministic SHA-256 digest of entry payload excluding chain hash."""
    clean = {k: v for k, v in sorted(data.items()) if k not in ("chain_hash", "content_hash")}
    payload = json.dumps(clean, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_chain_hash(prev_chain: str, content_hash: str) -> str:
    """Deterministic chain hash = SHA256(prev_chain || content_hash)."""
    return hashlib.sha256(((prev_chain or "") + content_hash).encode("utf-8")).hexdigest()


class LedgerPartitionShard:
    """Isolated, thread-safe partition shard holding an independent tamper-evident ledger chain."""

    def __init__(self, key: ShardKey):
        self.key = key
        self.created_at = time.time()
        self.is_sealed = False
        self._lock = threading.RLock()
        self._entries: list[PartitionedLedgerEntry] = []
        self._seq = 0
        self.chain_head = ""

    def append(self, data: dict[str, Any], timestamp: Optional[float] = None) -> PartitionedLedgerEntry:
        """Append an entry to this partition shard. Rejects appends if partition is sealed."""
        with self._lock:
            if self.is_sealed:
                raise RuntimeError(f"Cannot append to sealed partition shard: {self.key.partition_id}")

            ts = float(timestamp if timestamp is not None else time.time())
            self._seq += 1
            entry_id = self._seq

            c_hash = _compute_content_hash(data)
            ch_hash = _compute_chain_hash(self.chain_head, c_hash)
            self.chain_head = ch_hash

            entry = PartitionedLedgerEntry(
                id=entry_id,
                tenant_id=self.key.tenant_id,
                domain=self.key.domain,
                epoch=self.key.epoch,
                timestamp=ts,
                data=dict(data),
                content_hash=c_hash,
                chain_hash=ch_hash,
            )
            self._entries.append(entry)
            return entry

    def seal(self) -> str:
        """Seal partition shard against future writes. Returns the definitive root chain hash."""
        with self._lock:
            self.is_sealed = True
            return self.chain_head

    def query(
        self,
        sha256: Optional[str] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query entries within this partition shard."""
        with self._lock:
            results: list[dict[str, Any]] = []
            for entry in reversed(self._entries):
                d = entry.data
                if sha256 and d.get("sha256") != sha256:
                    continue
                if filename and d.get("final_filename") != filename:
                    continue
                if url and d.get("source_url") != url:
                    continue
                results.append(entry.to_dict())
                if len(results) >= limit:
                    break
            return results

    def verify_chain(self) -> tuple[bool, str]:
        """Verify the integrity of this shard's cryptographic chain."""
        with self._lock:
            prev = ""
            for idx, entry in enumerate(self._entries):
                expected_content = _compute_content_hash(entry.data)
                if entry.content_hash != expected_content:
                    return False, f"Entry {idx} in {self.key.partition_id} content hash mismatch"
                expected_chain = _compute_chain_hash(prev, entry.content_hash)
                if entry.chain_hash != expected_chain:
                    return False, f"Entry {idx} in {self.key.partition_id} chain hash mismatch"
                prev = entry.chain_hash
            return True, ""

    def get_metadata(self) -> PartitionMetadata:
        with self._lock:
            return PartitionMetadata(
                partition_id=self.key.partition_id,
                tenant_id=self.key.tenant_id,
                domain=self.key.domain,
                epoch=self.key.epoch,
                created_at=self.created_at,
                row_count=len(self._entries),
                chain_head=self.chain_head,
                is_sealed=self.is_sealed,
            )


def _extract_domain(url_or_domain: str) -> str:
    """Normalize URL or domain string to canonical lowercased domain."""
    if not url_or_domain:
        return "default"
    if "://" in url_or_domain:
        try:
            parsed = urllib.parse.urlsplit(url_or_domain)
            netloc = parsed.netloc.split(":")[0].strip().lower()
            return netloc if netloc else "default"
        except Exception:
            return "default"
    return url_or_domain.split(":")[0].strip().lower()


class MultiTenantLedgerRouter:
    """Coordinates routing, creation, querying, and verification across partition shards."""

    def __init__(self, epoch_duration_seconds: float = 86400.0):
        self.epoch_duration_seconds = max(60.0, float(epoch_duration_seconds))
        self._lock = threading.RLock()
        self._shards: dict[str, LedgerPartitionShard] = {}

    def resolve_shard_key(
        self,
        tenant_id: str,
        url_or_domain: str,
        timestamp: Optional[float] = None,
    ) -> ShardKey:
        """Derive ShardKey from tenant, URL/domain, and timestamp epoch."""
        t_id = (tenant_id or "default").strip().lower()
        domain = _extract_domain(url_or_domain)
        ts = float(timestamp if timestamp is not None else time.time())
        epoch = int(ts // self.epoch_duration_seconds)
        return ShardKey(tenant_id=t_id, domain=domain, epoch=epoch)

    def get_or_create_shard(self, key: ShardKey) -> LedgerPartitionShard:
        """Fetch existing shard or create new partition shard thread-safely."""
        pid = key.partition_id
        with self._lock:
            shard = self._shards.get(pid)
            if shard is None:
                shard = LedgerPartitionShard(key)
                self._shards[pid] = shard
            return shard

    def get_shard(self, key: ShardKey) -> Optional[LedgerPartitionShard]:
        with self._lock:
            return self._shards.get(key.partition_id)

    def record_entry(
        self,
        tenant_id: str,
        url: str,
        data: dict[str, Any],
        timestamp: Optional[float] = None,
    ) -> PartitionedLedgerEntry:
        """Route an entry to its destination partition shard and append."""
        key = self.resolve_shard_key(tenant_id, url, timestamp)
        shard = self.get_or_create_shard(key)
        return shard.append(data, timestamp=timestamp)

    def seal_partition(
        self,
        tenant_id: str,
        domain: str,
        epoch: int,
    ) -> Optional[LedgerPartitionShard]:
        """Explicitly seal a partition shard."""
        key = ShardKey(tenant_id=tenant_id.strip().lower(), domain=_extract_domain(domain), epoch=int(epoch))
        with self._lock:
            shard = self._shards.get(key.partition_id)
            if shard is not None:
                shard.seal()
            return shard

    def list_partitions_metadata(
        self,
        tenant_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return list of partition metadata, optionally filtered by tenant."""
        with self._lock:
            t_filter = tenant_id.strip().lower() if tenant_id else None
            meta_list = []
            for shard in self._shards.values():
                if t_filter and shard.key.tenant_id != t_filter:
                    continue
                meta_list.append(shard.get_metadata().to_dict())
            return sorted(meta_list, key=lambda p: (p["tenant_id"], p["domain"], p["epoch"]))

    def query_entries(
        self,
        tenant_id: Optional[str] = None,
        domain: Optional[str] = None,
        epoch_from: Optional[int] = None,
        epoch_to: Optional[int] = None,
        sha256: Optional[str] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query entries across matching partition shards."""
        with self._lock:
            t_filter = tenant_id.strip().lower() if tenant_id else None
            d_filter = _extract_domain(domain) if domain else None
            matching_shards = []

            for shard in self._shards.values():
                if t_filter and shard.key.tenant_id != t_filter:
                    continue
                if d_filter and shard.key.domain != d_filter:
                    continue
                if epoch_from is not None and shard.key.epoch < epoch_from:
                    continue
                if epoch_to is not None and shard.key.epoch > epoch_to:
                    continue
                matching_shards.append(shard)

            # Sort shards descending by epoch
            matching_shards.sort(key=lambda s: s.key.epoch, reverse=True)

            results: list[dict[str, Any]] = []
            for s in matching_shards:
                entries = s.query(sha256=sha256, filename=filename, url=url, limit=limit - len(results))
                results.extend(entries)
                if len(results) >= limit:
                    break
            return results

    def verify_all_partitions(
        self,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Verify chain integrity across all or tenant-specific partition shards."""
        with self._lock:
            t_filter = tenant_id.strip().lower() if tenant_id else None
            tampered = []
            verified_count = 0
            is_valid = True

            for shard in self._shards.values():
                if t_filter and shard.key.tenant_id != t_filter:
                    continue
                ok, err = shard.verify_chain()
                verified_count += len(shard._entries)
                if not ok:
                    is_valid = False
                    tampered.append(shard.key.partition_id)

            return {
                "valid": is_valid,
                "verified_count": verified_count,
                "tampered_partitions": tampered,
            }

    def chain_heads(self, tenant_id: Optional[str] = None) -> dict[str, tuple[int, str]]:
        """partition_id -> (entry count, chain head); two routers built from
        the same entries in the same order compare equal."""
        with self._lock:
            t_filter = tenant_id.strip().lower() if tenant_id else None
            return {pid: (len(shard._entries), shard.chain_head)
                    for pid, shard in self._shards.items()
                    if not t_filter or shard.key.tenant_id == t_filter}

    def reset(self) -> None:
        """Clear all active partition shards."""
        with self._lock:
            self._shards.clear()


# ─── Singleton & Module-Level Convenience Functions ─────────────────────────

_ROUTER_LOCK = threading.Lock()
_GLOBAL_ROUTER: Optional[MultiTenantLedgerRouter] = None


def get_ledger_router() -> MultiTenantLedgerRouter:
    """Retrieve global MultiTenantLedgerRouter singleton."""
    global _GLOBAL_ROUTER
    if _GLOBAL_ROUTER is None:
        with _ROUTER_LOCK:
            if _GLOBAL_ROUTER is None:
                _GLOBAL_ROUTER = MultiTenantLedgerRouter()
    return _GLOBAL_ROUTER


def reset_ledger_router() -> None:
    """Reset the global singleton ledger router."""
    global _GLOBAL_ROUTER
    with _ROUTER_LOCK:
        if _GLOBAL_ROUTER is not None:
            _GLOBAL_ROUTER.reset()
        _GLOBAL_ROUTER = None


def record_sharded_ledger_entry(
    tenant_id: str,
    url: str,
    data: dict[str, Any],
    timestamp: Optional[float] = None,
) -> PartitionedLedgerEntry:
    """Route and record a ledger entry in its designated partition shard."""
    return get_ledger_router().record_entry(tenant_id, url, data, timestamp=timestamp)
