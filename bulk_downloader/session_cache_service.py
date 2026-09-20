"""session_cache_service -- distributed session state cache replication.

Row 956 (v3.66.1585): intercept HTTP state mutation events (Set-Cookie,
Authorization header changes), serialize updated session state, and
replicate to a shared cache for cross-node coherence.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class SessionMutation:
    source: str
    key: str
    value: str


_COOKIE_PAIR_RE = re.compile(r"([^=;\s]+)\s*=\s*([^;,]*)")


def detect_mutations(headers: Dict[str, str]) -> List[SessionMutation]:
    mutations: List[SessionMutation] = []

    set_cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    if set_cookie:
        for segment in set_cookie.split(","):
            segment = segment.strip()
            m = _COOKIE_PAIR_RE.match(segment)
            if m:
                name, val = m.group(1).strip(), m.group(2).strip()
                # Skip cookie attributes that look like directives
                if name.lower() not in (
                    "path", "domain", "expires", "max-age",
                    "samesite", "secure", "httponly",
                ):
                    mutations.append(SessionMutation(
                        source="set-cookie", key=name, value=val,
                    ))

    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.strip():
        mutations.append(SessionMutation(
            source="authorization", key="authorization", value=auth.strip(),
        ))

    return mutations


def serialize_session(session: dict) -> bytes:
    return json.dumps(session, ensure_ascii=False, sort_keys=True).encode("utf-8")


def deserialize_session(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


class SessionCacheService:
    def __init__(self) -> None:
        self._store: Dict[str, bytes] = {}

    @staticmethod
    def _key(node_id: str, site_id: str) -> str:
        return f"session:{site_id}"

    def write(self, node_id: str, site_id: str, session: dict) -> None:
        key = self._key(node_id, site_id)
        self._store[key] = serialize_session(session)

    def read(self, node_id: str, site_id: str) -> Optional[dict]:
        key = self._key(node_id, site_id)
        data = self._store.get(key)
        if data is None:
            return None
        return deserialize_session(data)
