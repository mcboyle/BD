"""Row 956: DISTRIBUTED-SESSION-STATE-CACHE-REPLICATION-AND-COHERENCE-SERVICE

Tests for bulk_downloader/session_cache_service.py.
Verifies:
(1) Session store mutation detection
(2) Distributed key-value serialization
(3) Atomic cache read/write roundtrip across worker nodes
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.session_cache_service import (
        SessionCacheService,
        SessionMutation,
        detect_mutations,
        serialize_session,
        deserialize_session,
    )
except ImportError:
    SessionCacheService = None
    SessionMutation = None
    detect_mutations = None
    serialize_session = None
    deserialize_session = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_no_session_mutation_detection():
    """Behavioral RED: without the service, HTTP state mutations in
    Set-Cookie and Authorization headers go undetected across nodes."""
    headers = {
        "Set-Cookie": "session_id=abc123; Path=/; HttpOnly",
        "Authorization": "Bearer token-xyz",
    }
    assert len(headers) == 2

    if detect_mutations is None:
        detected = []
        assert len(detected) == 2, (
            f"BEHAVIORAL RED: no session mutation detection; "
            f"0 of 2 state mutations detected (assert 0 == 2)"
        )

    mutations = detect_mutations(headers)
    assert len(mutations) >= 1


# ---------------------------------------------------------------------------
# (1) Session store mutation detection
# ---------------------------------------------------------------------------

def test_detect_set_cookie_mutation():
    """Detect session mutation from Set-Cookie header."""
    assert detect_mutations is not None
    headers = {"Set-Cookie": "sid=value123; Path=/; Secure"}
    mutations = detect_mutations(headers)
    assert len(mutations) >= 1
    cookie_mut = [m for m in mutations if m.source == "set-cookie"]
    assert len(cookie_mut) == 1
    assert cookie_mut[0].key == "sid"
    assert cookie_mut[0].value == "value123"


def test_detect_authorization_mutation():
    """Detect session mutation from Authorization header change."""
    assert detect_mutations is not None
    headers = {"Authorization": "Bearer new-token-abc"}
    mutations = detect_mutations(headers)
    auth_mut = [m for m in mutations if m.source == "authorization"]
    assert len(auth_mut) == 1
    assert "new-token-abc" in auth_mut[0].value


def test_detect_multiple_cookies():
    """Detect mutations from multiple Set-Cookie headers."""
    assert detect_mutations is not None
    headers = {"Set-Cookie": "a=1; Path=/, b=2; Path=/"}
    mutations = detect_mutations(headers)
    cookie_muts = [m for m in mutations if m.source == "set-cookie"]
    assert len(cookie_muts) == 2


def test_no_mutation_on_empty_headers():
    """No mutations detected when headers contain no state changes."""
    assert detect_mutations is not None
    mutations = detect_mutations({"Content-Type": "text/html"})
    assert len(mutations) == 0


# ---------------------------------------------------------------------------
# (2) Distributed key-value serialization
# ---------------------------------------------------------------------------

def test_serialize_session_roundtrip():
    """Serialized session can be deserialized back identically."""
    assert serialize_session is not None
    assert deserialize_session is not None
    session = {
        "cookies": {"sid": "abc123", "csrf": "tok456"},
        "auth_header": "Bearer xyz",
        "node_id": "worker-3",
    }
    data = serialize_session(session)
    assert isinstance(data, bytes)
    restored = deserialize_session(data)
    assert restored == session


def test_serialize_handles_unicode():
    """Serialization handles unicode cookie values."""
    assert serialize_session is not None
    session = {"cookies": {"name": "café"}, "auth_header": ""}
    data = serialize_session(session)
    restored = deserialize_session(data)
    assert restored["cookies"]["name"] == "café"


def test_serialize_empty_session():
    """Empty session serializes and deserializes correctly."""
    assert serialize_session is not None
    session = {"cookies": {}, "auth_header": ""}
    data = serialize_session(session)
    restored = deserialize_session(data)
    assert restored == session


# ---------------------------------------------------------------------------
# (3) Atomic cache read/write roundtrip
# ---------------------------------------------------------------------------

def test_cache_write_read_roundtrip():
    """Write a session to cache and read it back atomically."""
    assert SessionCacheService is not None
    svc = SessionCacheService()
    session = {"cookies": {"sid": "v1"}, "auth_header": "Bearer t1"}

    svc.write("worker-1", "site-A", session)
    result = svc.read("worker-1", "site-A")
    assert result == session


def test_cache_cross_node_replication():
    """Session written by one node is readable by another."""
    assert SessionCacheService is not None
    svc = SessionCacheService()
    session = {"cookies": {"sid": "shared"}, "auth_header": "Bearer shared-tok"}

    svc.write("worker-1", "site-B", session)
    result = svc.read("worker-2", "site-B")
    assert result == session


def test_cache_overwrite():
    """Later write overwrites earlier value."""
    assert SessionCacheService is not None
    svc = SessionCacheService()
    svc.write("w1", "site-C", {"cookies": {"sid": "old"}, "auth_header": ""})
    svc.write("w1", "site-C", {"cookies": {"sid": "new"}, "auth_header": ""})
    result = svc.read("w1", "site-C")
    assert result["cookies"]["sid"] == "new"


def test_cache_read_missing_returns_none():
    """Reading a non-existent key returns None."""
    assert SessionCacheService is not None
    svc = SessionCacheService()
    assert svc.read("w99", "site-Z") is None


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def test_negative_control_mutation_and_cache_exact_counts():
    """Negative control: prove mutation detection and cache operations
    produce both positive and negative outcomes with exact counts."""
    assert detect_mutations is not None
    assert SessionCacheService is not None

    # Positive: detect 3 mutations
    headers_with = {
        "Set-Cookie": "a=1; Path=/, b=2; Path=/",
        "Authorization": "Bearer tok",
    }
    muts = detect_mutations(headers_with)
    assert len(muts) == 3, f"Expected 3 mutations, got {len(muts)}"

    # Negative: detect 0 mutations
    headers_without = {"Content-Length": "42"}
    muts_none = detect_mutations(headers_without)
    assert len(muts_none) == 0, f"Expected 0 mutations, got {len(muts_none)}"

    # Cache: 2 writes, 2 reads, 1 miss
    svc = SessionCacheService()
    svc.write("n1", "s1", {"cookies": {"x": "1"}, "auth_header": ""})
    svc.write("n2", "s2", {"cookies": {"y": "2"}, "auth_header": ""})
    assert svc.read("n1", "s1") is not None
    assert svc.read("n2", "s2") is not None
    assert svc.read("n3", "s3") is None
