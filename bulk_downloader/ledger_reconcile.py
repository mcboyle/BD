"""ledger_reconcile -- cross-replica provenance ledger reconciliation (row 1063).

provenance.py keeps a hash chain (chain_hash = sha256(prev || content_hash));
verify_chain() proves ONE ledger is internally consistent.  Nothing on base
says whether a Litestream replica (db_replication.restore_store) or another
node's ledger is the SAME chain.  This module does, without shipping rows:

  digest      {count, head_id, head_chain_hash, checkpoints: [[id, chain_hash]...]}
              checkpoints on a fixed id grid (every N ids) + the head + any
              ``want_ids`` a peer asked for, ascending.
  reconcile   compares two digests at their shared ids.  Verdicts:
                in_sync   same head id, same head hash
                behind    every shared id agrees and OUR head is one of them
                ahead     every shared id agrees and THEIR head is one of them
                forked    a shared id disagrees; fork_after_id = last agreeing
                unknown   all shared ids agree but the shorter head is not
                          shared, so behind/forked cannot be told apart;
                          ask_ids names what the peer must include next.
              id 0 with chain "" is the implicit genesis both sides share.

Two-message protocol: A sends its digest; B answers with the verdict plus its
own digest computed with want_ids = A's checkpoint ids, so A can compute the
same verdict locally.  Read-only on both sides.
"""
from __future__ import annotations

import sqlite3

DEFAULT_CHECKPOINT_EVERY = 256


class ReplicaUnavailable(RuntimeError):
    """The replica store cannot be opened or has no provenance table."""


def local_conn():
    """The live ledger connection (a context manager); patched in tests."""
    from . import db as _db
    return _db.db_conn()


_EMPTY_DIGEST = {"count": 0, "head_id": 0, "head_chain_hash": "", "checkpoints": []}


def _has_ledger_table(cx) -> bool:
    return cx.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'provenance'"
                      ).fetchone() is not None


def digest_from_conn(cx, *, checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
                     want_ids=None, missing_table_ok: bool = True) -> dict:
    """Digest of the ledger on ``cx``.

    A node that has never recorded provenance has no table yet (provenance.py
    creates it lazily on first write); that is the empty ledger, so a fresh
    replica reconciles as ``behind`` rather than erroring. Stays read-only.
    ``missing_table_ok=False`` raises ReplicaUnavailable instead.
    """
    every = max(1, int(checkpoint_every))
    if not _has_ledger_table(cx):
        if missing_table_ok:
            return dict(_EMPTY_DIGEST, checkpoints=[])
        raise ReplicaUnavailable("no provenance table")
    head = cx.execute(
        "SELECT id, chain_hash FROM provenance ORDER BY id DESC LIMIT 1").fetchone()
    if head is None:
        return dict(_EMPTY_DIGEST, checkpoints=[])
    head_id, head_hash = int(head[0]), str(head[1])
    count = int(cx.execute("SELECT COUNT(*) FROM provenance").fetchone()[0])
    wanted = {int(i) for i in (want_ids or []) if int(i) > 0}
    wanted.update(range(every, head_id + 1, every))
    wanted.add(head_id)
    rows = cx.execute(
        "SELECT id, chain_hash FROM provenance WHERE id IN (%s) ORDER BY id ASC"
        % ",".join("?" * len(wanted)), sorted(wanted)).fetchall()
    return {"count": count, "head_id": head_id, "head_chain_hash": head_hash,
            "checkpoints": [[int(r[0]), str(r[1])] for r in rows]}


def digest_from_path(path: str, **kw) -> dict:
    """Digest of a replica sqlite file, opened read-only (never creates it)."""
    try:
        cx = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise ReplicaUnavailable(f"{path}: {e}") from e
    try:
        return digest_from_conn(cx, missing_table_ok=False, **kw)
    except sqlite3.Error as e:
        raise ReplicaUnavailable(f"{path}: {e}") from e
    finally:
        cx.close()


def validate_digest(d) -> dict:
    """Coerce a peer-supplied digest; raise ValueError on any malformed field."""
    if not isinstance(d, dict):
        raise ValueError("digest must be an object")
    try:
        count, head_id = int(d.get("count", 0)), int(d.get("head_id", 0))
    except (TypeError, ValueError) as e:
        raise ValueError(f"count/head_id must be integers: {e}") from e
    head_hash = d.get("head_chain_hash", "")
    if not isinstance(head_hash, str) or count < 0 or head_id < 0:
        raise ValueError("head_chain_hash must be a string; counts non-negative")
    cps = []
    for cp in d.get("checkpoints") or []:
        if (not isinstance(cp, (list, tuple)) or len(cp) != 2
                or not isinstance(cp[1], str)):
            raise ValueError(f"bad checkpoint {cp!r}")
        cps.append([int(cp[0]), cp[1]])
    cps.sort()
    return {"count": count, "head_id": head_id, "head_chain_hash": head_hash,
            "checkpoints": cps}


def reconcile(local: dict, remote: dict) -> dict:
    local, remote = validate_digest(local), validate_digest(remote)
    mine = {0: ""}
    mine.update({i: h for i, h in local["checkpoints"]})
    mine[local["head_id"]] = local["head_chain_hash"]
    theirs = {0: ""}
    theirs.update({i: h for i, h in remote["checkpoints"]})
    theirs[remote["head_id"]] = remote["head_chain_hash"]
    shared = sorted(set(mine) & set(theirs))
    common_id, fork_after = 0, None
    for i in shared:
        if mine[i] == theirs[i]:
            common_id = i
        else:
            fork_after = common_id
            break
    lh, rh = local["head_id"], remote["head_id"]
    out = {"local_head_id": lh, "remote_head_id": rh, "compared_ids": shared,
           "common_id": common_id, "fork_after_id": fork_after, "lag": 0, "ask_ids": []}
    if fork_after is not None:
        out["status"] = "forked"
    elif lh == rh:
        out["status"] = "in_sync"
    elif lh < rh and common_id == lh:
        out.update(status="behind", lag=rh - lh)
    elif rh < lh and common_id == rh:
        out.update(status="ahead", lag=lh - rh)
    else:
        short = min(lh, rh)
        out.update(status="unknown", ask_ids=[short])
    return out
