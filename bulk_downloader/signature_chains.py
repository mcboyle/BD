"""Asymmetric Cryptographic Signature Chains for Audit Provenance.

Provides cryptographic tamper-evident audit logging with Ed25519 asymmetric
key signatures, sequential hash chaining (Merkle-style lineage), multi-seat
signer verification, and JSON/JSONL export/import capabilities.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


def _canonical_json_bytes(obj: Any) -> bytes:
    """Produce deterministic byte representation of JSON data."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


class AuditSigner:
    """Ed25519 asymmetric key signer for audit trail provenance."""

    def __init__(
        self,
        signer_id: str,
        private_key: Optional[ed25519.Ed25519PrivateKey] = None,
    ) -> None:
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography library is required for AuditSigner")
        self.signer_id = signer_id
        if private_key is None:
            self._private_key = ed25519.Ed25519PrivateKey.generate()
        else:
            self._private_key = private_key
        self._public_key = self._private_key.public_key()

    @property
    def public_key_bytes(self) -> bytes:
        from cryptography.hazmat.primitives import serialization
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    def sign(self, data: bytes) -> bytes:
        return self._private_key.sign(data)

    def private_key_bytes(self) -> bytes:
        """Raw 32-byte Ed25519 seed, for the key store (row1002 H1)."""
        from cryptography.hazmat.primitives import serialization
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @classmethod
    def from_private_key_bytes(cls, signer_id: str, raw: bytes) -> "AuditSigner":
        return cls(signer_id, ed25519.Ed25519PrivateKey.from_private_bytes(raw))


class AuditVerifier:
    """Verifies Ed25519 asymmetric signatures for audit records."""

    def __init__(
        self,
        signer_id: str,
        public_key_hex: Optional[str] = None,
        public_key_bytes: Optional[bytes] = None,
    ) -> None:
        if not _CRYPTO_AVAILABLE:
            raise RuntimeError("cryptography library is required for AuditVerifier")
        self.signer_id = signer_id
        if public_key_bytes is None:
            if public_key_hex is None:
                raise ValueError("Either public_key_hex or public_key_bytes must be provided")
            public_key_bytes = bytes.fromhex(public_key_hex)
        self.public_key_bytes = public_key_bytes
        self._public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)

    @property
    def public_key_hex(self) -> str:
        return self.public_key_bytes.hex()

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self._public_key.verify(signature, data)
            return True
        except (InvalidSignature, Exception):
            return False


@dataclass
class ProvenanceBlock:
    sequence: int
    timestamp: float
    event_type: str
    payload: Dict[str, Any]
    payload_hash: str
    prev_hash: str
    signer_id: str
    block_hash: str
    signature: bytes

    @staticmethod
    def compute_payload_hash(payload: Dict[str, Any]) -> str:
        return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()

    @staticmethod
    def compute_block_hash(
        sequence: int,
        timestamp: float,
        event_type: str,
        payload_hash: str,
        prev_hash: str,
        signer_id: str,
    ) -> str:
        # row1002 F2: repr(float) is what json.dumps stores, so the committed
        # bytes and the serialized bytes are the same; ".6f" left a sub-
        # microsecond window in which the stored value could move unhashed.
        header = f"{sequence}:{timestamp!r}:{event_type}:{payload_hash}:{prev_hash}:{signer_id}"
        return hashlib.sha256(header.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
            "prev_hash": self.prev_hash,
            "signer_id": self.signer_id,
            "block_hash": self.block_hash,
            "signature_hex": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProvenanceBlock:
        return cls(
            sequence=int(data["sequence"]),
            timestamp=float(data["timestamp"]),
            event_type=str(data["event_type"]),
            payload=dict(data.get("payload", {})),
            payload_hash=str(data["payload_hash"]),
            prev_hash=str(data["prev_hash"]),
            signer_id=str(data["signer_id"]),
            block_hash=str(data["block_hash"]),
            signature=bytes.fromhex(data["signature_hex"]),
        )


@dataclass
class ChainVerificationResult:
    valid: bool
    block_count: int
    errors: List[str] = field(default_factory=list)
    head_hash: str = ""


class SignatureProvenanceChain:
    """Cryptographic audit chain linked by SHA-256 and asymmetric signatures."""

    def __init__(self, signer: Optional[AuditSigner] = None) -> None:
        self.blocks: List[ProvenanceBlock] = []
        self._verifiers: Dict[str, AuditVerifier] = {}
        if signer:
            self._register_signer(signer)
            self._create_genesis(signer)

    def _register_signer(self, signer: AuditSigner) -> None:
        verifier = AuditVerifier(
            signer_id=signer.signer_id,
            public_key_bytes=signer.public_key_bytes,
        )
        self._verifiers[signer.signer_id] = verifier

    def register_verifier(self, verifier: AuditVerifier) -> None:
        self._verifiers[verifier.signer_id] = verifier

    @property
    def head_hash(self) -> str:
        """block_hash of the last block; the trust anchor to persist out of
        band next to the public key (row1002 R1: a chain whose anchor is
        only a public key cannot see a deleted suffix)."""
        return self.blocks[-1].block_hash if self.blocks else ""

    def _create_genesis(self, signer: AuditSigner) -> ProvenanceBlock:
        seq = 0
        ts = time.time()
        ev_type = "GENESIS"
        payload: Dict[str, Any] = {"message": "Audit chain initialized", "signer": signer.signer_id}
        payload_hash = ProvenanceBlock.compute_payload_hash(payload)
        prev_hash = "0" * 64
        block_hash = ProvenanceBlock.compute_block_hash(
            sequence=seq,
            timestamp=ts,
            event_type=ev_type,
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            signer_id=signer.signer_id,
        )
        sig = signer.sign(block_hash.encode("utf-8"))
        block = ProvenanceBlock(
            sequence=seq,
            timestamp=ts,
            event_type=ev_type,
            payload=payload,
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            signer_id=signer.signer_id,
            block_hash=block_hash,
            signature=sig,
        )
        self.blocks.append(block)
        return block

    def append_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        signer: AuditSigner,
    ) -> ProvenanceBlock:
        if not self.blocks:
            # An empty chain has no trust anchor yet: the genesis signer is it.
            self._register_signer(signer)
            self._create_genesis(signer)
        # row1002 F1: appending never registers the signer. Trust comes from
        # the constructor's signer or register_verifier(); otherwise a rogue
        # writer's block would verify against the key it brought with it.
        prev_block = self.blocks[-1]
        seq = len(self.blocks)
        ts = time.time()
        payload_hash = ProvenanceBlock.compute_payload_hash(payload)
        prev_hash = prev_block.block_hash
        block_hash = ProvenanceBlock.compute_block_hash(
            sequence=seq,
            timestamp=ts,
            event_type=event_type,
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            signer_id=signer.signer_id,
        )
        sig = signer.sign(block_hash.encode("utf-8"))
        block = ProvenanceBlock(
            sequence=seq,
            timestamp=ts,
            event_type=event_type,
            payload=payload,
            payload_hash=payload_hash,
            prev_hash=prev_hash,
            signer_id=signer.signer_id,
            block_hash=block_hash,
            signature=sig,
        )
        self.blocks.append(block)
        return block

    def verify_chain(
        self,
        verifiers: Optional[Dict[str, AuditVerifier]] = None,
        *,
        expected_head: Optional[str] = None,
    ) -> ChainVerificationResult:
        """Verify every block against the trusted keys (the chain's own
        signer plus register_verifier()/``verifiers``) and, when the caller
        holds the head hash out of band, that the chain still ENDS there.

        Without ``expected_head`` a deleted suffix is invisible: every
        surviving block is genuinely signed and linked (row1002 R1). Persist
        ``head_hash`` next to the public key and pass it back here.
        """
        errors: List[str] = []
        active_verifiers = dict(self._verifiers)
        if verifiers:
            active_verifiers.update(verifiers)
        if not self.blocks:
            # row1002 H2: a live chain always holds its genesis; zero blocks
            # needs no out-of-band anchor to be known wrong.
            errors.append("NO_GENESIS: chain has no blocks")
        if expected_head is not None and self.head_hash != expected_head:
            errors.append(
                f"TRUNCATED_OR_DIVERGED: head {self.head_hash[:12] or '(empty)'} != "
                f"expected {expected_head[:12]} ({len(self.blocks)} blocks present)"
            )

        for i, block in enumerate(self.blocks):
            # 1. Sequence check
            if block.sequence != i:
                errors.append(f"SEQUENCE_INVALID: block index {i} declares sequence {block.sequence}")

            # 2. Payload hash check
            expected_payload_hash = ProvenanceBlock.compute_payload_hash(block.payload)
            if block.payload_hash != expected_payload_hash:
                errors.append(
                    f"PAYLOAD_HASH_MISMATCH: block {i} (sequence {block.sequence}) "
                    f"stored={block.payload_hash[:12]} computed={expected_payload_hash[:12]}"
                )

            # 3. Prev hash linkage
            if i == 0:
                if block.prev_hash != "0" * 64:
                    errors.append(f"GENESIS_LINKAGE_INVALID: block 0 prev_hash is {block.prev_hash}")
            else:
                expected_prev = self.blocks[i - 1].block_hash
                if block.prev_hash != expected_prev:
                    errors.append(
                        f"PREV_HASH_MISMATCH / LINKAGE_BROKEN: block {i} (sequence {block.sequence}) "
                        f"prev_hash={block.prev_hash[:12]} expected={expected_prev[:12]}"
                    )

            # 4. Block hash check
            expected_block_hash = ProvenanceBlock.compute_block_hash(
                sequence=block.sequence,
                timestamp=block.timestamp,
                event_type=block.event_type,
                payload_hash=block.payload_hash,
                prev_hash=block.prev_hash,
                signer_id=block.signer_id,
            )
            if block.block_hash != expected_block_hash:
                errors.append(
                    f"BLOCK_HASH_MISMATCH: block {i} (sequence {block.sequence}) "
                    f"stored={block.block_hash[:12]} computed={expected_block_hash[:12]}"
                )

            # 5. Asymmetric signature check
            verifier = active_verifiers.get(block.signer_id)
            if not verifier:
                errors.append(f"SIGNER_KEY_MISSING: no verifier available for signer '{block.signer_id}' on block {i}")
            else:
                is_valid = verifier.verify(block.block_hash.encode("utf-8"), block.signature)
                if not is_valid:
                    errors.append(
                        f"SIGNATURE_INVALID: cryptographic verification failed on block {i} (sequence {block.sequence})"
                    )

        return ChainVerificationResult(
            valid=(len(errors) == 0),
            block_count=len(self.blocks),
            errors=errors,
            head_hash=self.head_hash,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_count": len(self.blocks),
            "blocks": [b.to_dict() for b in self.blocks],
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def from_dict(self, data: Dict[str, Any]) -> SignatureProvenanceChain:
        self.blocks = [ProvenanceBlock.from_dict(b) for b in data.get("blocks", [])]
        return self

    def from_json(self, json_str: str) -> SignatureProvenanceChain:
        data = json.loads(json_str)
        return self.from_dict(data)

    def __len__(self) -> int:
        return len(self.blocks)

    def __getitem__(self, idx: int) -> ProvenanceBlock:
        return self.blocks[idx]


def create_audit_signer(signer_id: str) -> AuditSigner:
    return AuditSigner(signer_id=signer_id)


def create_provenance_chain(signer: Optional[AuditSigner] = None) -> SignatureProvenanceChain:
    return SignatureProvenanceChain(signer=signer)
