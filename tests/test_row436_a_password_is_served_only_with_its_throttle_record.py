"""Row 436: a plaintext password is not served without a durable throttle record.

``check_and_record_fetch`` allowed the fetch and then called ``_save_tokens``
with the bool DISCARDED, though ``_save_tokens`` returns False on a failed write
per AF3 and every revocation caller honours it.  Rate-limit state lives ONLY in
``vault_tokens.json`` and every call re-runs ``_load_tokens`` from disk, so while
the store is unwritable (disk full, EACCES, read-only filesystem) each recorded
fetch evaporates and the next call sees pristine cooldowns and an empty window.
The 5s per-entry cooldown and the 30-per-60s token cap -- the only brake on a
leaked vault token -- vanish exactly when the host degrades, while the fetch
itself is an irreversible plaintext password disclosure through
``/api/secrets/extension/fetch_one``.  That is the fail-open inverse of the
module's own AF3/NEW-4 contract and of CLAUDE.md A7's rule that an irreversible
action proves its evidence record writable BEFORE acting.

WHY THE DIRECTORY, NOT THE FILE.  ``_save_tokens`` writes a sibling tempfile and
renames it over the target, so a read-only FILE does not fail it -- only a
directory the process cannot create an entry in does.  These gates make the
store DIRECTORY unwritable and assert the injected failure actually fired, by
counting ``_save_tokens``'s own stderr line.

WHAT IS AND IS NOT CLAIMED ABOUT DURABILITY.  The contract asserted here is that
the ALLOW is gated on ``_save_tokens`` reporting success -- i.e. that the
tmpfile write and the rename both completed, which is what makes the record
survive PROCESS death via the page cache.  Nothing here claims fsync: neither
``_save_tokens`` nor this cut fsyncs the tempfile or the containing directory,
so MACHINE death can still lose a recorded fetch.  That remains open and is not
asserted by any name in this file.

ISOLATION.  ``VAULT_TOKENS_FILE`` and ``VAULT_AUDIT_LOG`` are redirected into
pytest's tmp_path for every gate; the operator's own vault store is never named,
and the directory mode is restored in a finally so a failing assertion cannot
leave an unwritable tree behind.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from bulk_downloader import extension_vault as ev

BD_GATE_SCOPE = "module"

_ENTRY = "bulkdl-site-row436"
_LABEL = "row436-extension"
# The row's exact arithmetic: 31 attempts is one MORE than PER_TOKEN_RATE_LIMIT
# and every one of them lands inside the 5s per-entry cooldown, so on a healthy
# store attempt 1 allows and attempts 2..31 deny. 31 allows proves both limits
# dead.
_BURST = 31

_SAVE_FAILED_MARKER = "vault_tokens save failed"

# The deny reason an unpersistable throttle record must carry, pinned as a
# LITERAL rather than read from the module. The negative controls below must be
# green on the DEFECTIVE base too -- they prove the genuine limits still fire
# and that the fix did not manufacture the refusal -- so they may not reference
# a symbol that only the fix introduces, or they would fail with AttributeError
# instead of for their own subject. One dedicated gate binds this literal to the
# production constant, so the two cannot drift.
_UNPERSISTED = "throttle record not persisted"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A redeemed vault token, persisted while the store is still writable."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", store_dir / "vault_tokens.json")
    monkeypatch.setattr(ev, "VAULT_AUDIT_LOG", tmp_path / "vault_access.log")

    pairing = ev.issue_pairing_token()
    token = ev.redeem_pairing_token(pairing, _LABEL)

    # PRECONDITIONS. The token must be REAL and already ON DISK, or the
    # "invalid token" early refusal at the top of check_and_record_fetch would
    # launder every result below into a deny that has nothing to do with the
    # throttle record.
    assert token, "redeem_pairing_token did not mint a vault token"
    assert ev.VAULT_TOKENS_FILE.exists(), "the fixture never wrote a store"
    on_disk = json.loads(ev.VAULT_TOKENS_FILE.read_text(encoding="utf-8"))
    assert token in on_disk.get("redeemed", {}), on_disk
    assert ev.store_state() == "ok", ev.store_state()
    return token, store_dir


def _freeze(monkeypatch, t: float = 1_000_000.0):
    """Pin the clock so every attempt is unambiguously inside the 5s cooldown
    AND inside the 60s window -- the burst must not be able to age out."""
    monkeypatch.setattr(ev, "_now", lambda: t)
    return t


def _unwritable(store_dir):
    os.chmod(store_dir, stat.S_IRUSR | stat.S_IXUSR)   # r-x, no write
    assert stat.S_IMODE(store_dir.stat().st_mode) == 0o500, (
        "the chmod did not take; the injected failure would never fire")


def _save_failures(capsys) -> int:
    return sum(1 for line in capsys.readouterr().err.splitlines()
               if _SAVE_FAILED_MARKER in line)


# ── 1. the defect: both limits die when the store cannot be written ─────────

def test_an_unpersistable_throttle_record_refuses_the_fetch(
        vault, monkeypatch, capsys):
    token, store_dir = vault
    _freeze(monkeypatch)
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")

    _unwritable(store_dir)
    try:
        outcomes = [ev.check_and_record_fetch(token, _ENTRY)
                    for _ in range(_BURST)]
    finally:
        os.chmod(store_dir, stat.S_IRWXU)

    allows = [i for i, (ok, _) in enumerate(outcomes) if ok]
    denies = [reason for ok, reason in outcomes if not ok]

    # PRECONDITION: the injected failure really fired, once per attempt.
    assert _save_failures(capsys) == _BURST, (
        "the store was writable after all; nothing was injected")

    # THE VERDICT. Not one allow may be served over an unrecordable throttle.
    assert allows == [], (
        f"{len(allows)} of {_BURST} fetches of ONE entry were allowed inside "
        f"the 5s cooldown while the throttle record could not be persisted "
        f"(> the {ev.PER_TOKEN_RATE_LIMIT}/min cap): both limits are dead")
    assert len(denies) == _BURST

    # The diagnostic must be DISTINCT from cooldown, window and invalid-token,
    # or the operator cannot tell a degraded host from a working rate limit.
    assert all(r == _UNPERSISTED for r in denies), denies
    assert "cooldown" not in _UNPERSISTED
    assert "too many fetches" not in _UNPERSISTED
    assert _UNPERSISTED != "invalid token"
    # Bind the literal above to the production constant so they cannot drift.
    assert ev.THROTTLE_UNPERSISTED_REASON == _UNPERSISTED

    # And nothing was durably recorded, which is the whole reason to refuse.
    meta = json.loads(
        ev.VAULT_TOKENS_FILE.read_text(encoding="utf-8"))["redeemed"][token]
    assert meta.get("recent_fetches", []) == [], meta
    assert meta.get("entry_cooldowns", {}) == {}, meta


def test_the_refusal_is_not_a_one_shot_that_a_retry_walks_past(
        vault, monkeypatch, capsys):
    """A degraded store must keep refusing for as long as it is degraded, and
    must resume serving the instant it is repaired -- with the limits intact."""
    token, store_dir = vault
    t = _freeze(monkeypatch)
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")

    _unwritable(store_dir)
    try:
        for _ in range(3):
            ok, reason = ev.check_and_record_fetch(token, _ENTRY)
            assert ok is False and reason == _UNPERSISTED
    finally:
        os.chmod(store_dir, stat.S_IRWXU)
    assert _save_failures(capsys) == 3

    # Repaired: the very next call allows AND persists, no restart needed.
    ok, reason = ev.check_and_record_fetch(token, _ENTRY)
    assert ok is True and reason == "", reason
    assert _save_failures(capsys) == 0
    meta = json.loads(
        ev.VAULT_TOKENS_FILE.read_text(encoding="utf-8"))["redeemed"][token]
    assert meta["recent_fetches"] == [t], meta
    assert meta["entry_cooldowns"] == {_ENTRY: t}, meta


# ── 2. negative controls: the genuine limits still fire ─────────────────────

def test_a_writable_store_allows_exactly_one_then_denies_on_cooldown(
        vault, monkeypatch, capsys):
    """NEGATIVE CONTROL. Exactly 1 allow, then the 5s per-entry cooldown."""
    token, _ = vault
    _freeze(monkeypatch)
    ok1, reason1 = ev.check_and_record_fetch(token, _ENTRY)
    ok2, reason2 = ev.check_and_record_fetch(token, _ENTRY)
    assert (ok1, reason1) == (True, "")
    assert ok2 is False and "cooldown" in reason2, reason2
    assert reason2 != _UNPERSISTED
    assert _save_failures(capsys) == 0, "nothing should have failed to save"


def test_a_writable_store_denies_the_thirty_first_in_window_fetch(
        vault, monkeypatch, capsys):
    """NEGATIVE CONTROL. The per-token window cap still fires at exactly 31,
    on DISTINCT entries so the per-entry cooldown cannot be what denies."""
    token, _ = vault
    _freeze(monkeypatch)
    outcomes = [ev.check_and_record_fetch(token, f"{_ENTRY}-{i}")
                for i in range(_BURST)]
    allows = [i for i, (ok, _) in enumerate(outcomes) if ok]
    assert len(allows) == ev.PER_TOKEN_RATE_LIMIT == 30, len(allows)
    last_ok, last_reason = outcomes[-1]
    assert last_ok is False
    assert "too many fetches" in last_reason, last_reason
    assert last_reason != _UNPERSISTED
    assert _save_failures(capsys) == 0


def test_an_invalid_token_is_still_refused_for_its_own_reason(
        vault, monkeypatch, capsys):
    """NEGATIVE CONTROL. The invalid-token arm keeps its own distinct
    diagnostic and never reaches the store write at all."""
    _, store_dir = vault
    _freeze(monkeypatch)
    ok, reason = ev.check_and_record_fetch("row436-not-a-token", _ENTRY)
    assert ok is False
    assert reason == "invalid token", reason
    assert reason != _UNPERSISTED
    assert _save_failures(capsys) == 0


def test_an_unreadable_store_still_raises_rather_than_denying(
        vault, monkeypatch, capsys):
    """NEGATIVE CONTROL / boundary. An UNREADABLE store is a different failure
    from an UNWRITABLE one: it raises VaultTokensUnreadableError, which
    app_secrets maps to 503, and the new refusal must not swallow it into a
    429 (CLAUDE.md A7: a diagnostic that collapses distinct failures costs the
    investigation)."""
    token, _ = vault
    _freeze(monkeypatch)
    ev.VAULT_TOKENS_FILE.write_text("{ row436 not json", encoding="utf-8")
    assert ev.store_state() == "unreadable"
    with pytest.raises(ev.VaultTokensUnreadableError):
        ev.check_and_record_fetch(token, _ENTRY)
    capsys.readouterr()


# ── 3. the endpoint seam: a 200 never carries a password past this ──────────

def test_the_fetch_endpoint_does_not_serve_a_password_it_cannot_throttle(
        vault, monkeypatch, capsys):
    """The consequence the row names, at the surface that discloses plaintext.

    ``app_secrets`` maps a not-allowed result to 429 and audits the reason, so
    the distinctive diagnostic reaches the audit log even though the HTTP code
    is shared with an ordinary rate-limit deny.
    """
    token, store_dir = vault
    _freeze(monkeypatch)
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")

    audited: list = []
    monkeypatch.setattr(
        ev, "audit_fetch",
        lambda meta, key, origin, success, reason="": audited.append(
            (key, success, reason)))

    _unwritable(store_dir)
    try:
        allowed, reason = ev.check_and_record_fetch(token, _ENTRY)
        if not allowed:
            ev.audit_fetch({}, _ENTRY, "https://row436.invalid/", False, reason)
    finally:
        os.chmod(store_dir, stat.S_IRWXU)

    assert allowed is False
    assert _save_failures(capsys) == 1
    assert audited == [(_ENTRY, False, _UNPERSISTED)], audited
