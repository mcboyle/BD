"""O1876 item 4: the vault key is per install, never the public constant seed.

OWNS: bulk_downloader/vault_sync.py, tests/test_o1876_vault_per_install_key.py

On the defective base, get_vault_sync() with no BD_VAULT_KEY and no
config/.vault.key derived its AES-256-GCM key from the public constant
"bd-vault-cluster-shared-seed", so every default install shared one guessable
key. Entries written under that key must still read once and are migrated: the
legacy token is backed up, then re-encrypted under the per-install key.
No live vault and no real Redis: _redis_command is a dict-backed fake.

FIX-R-150 (r2): the migration is one compare-and-set EVAL, so a set_session
that lands between the read and the migration is never overwritten (F1); a new
key file is published whole with link(), so a concurrent first open never reads
an empty file (F2). The fake emulates vault_sync._MIGRATE_LEGACY_LUA only.

FIX-R-163 (r3): on the in-process fallback cache the migration's compare,
backup and store hold the same lock as set_session, invalidate_session and
expiry eviction: a writer that lands after the compare waits, never lost.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import stat
import threading
import time

import pytest
from cryptography.exceptions import InvalidTag

from bulk_downloader import vault_sync
from bulk_downloader.vault_sync import SessionCrypto

BD_GATE_SCOPE = "module"

LEGACY_KEY = hashlib.sha256(b"bd-vault-cluster-shared-seed").digest()
BACKUP_TTL = 72 * 3600  # operator O1895
MISMATCH = "vault key mismatch: provision config/.vault.key"
SITE, ACCOUNT = "o1876site", "3"
MAIN_KEY = f"bd:vaultsync:{SITE}:{ACCOUNT}"
BACKUP_KEY = f"bd:vaultsync-legacy-backup:{SITE}:{ACCOUNT}"
PAYLOAD = {"cookies": [{"name": "sid", "value": "v1", "domain": ".example.com"}]}


class FakeRedis:
    """GET / SET [EX n] / TTL / DEL / EVAL(migration script) over a dict.

    Every command is recorded in calls, every key written in writes.
    before_write runs once, just before the first SET or EVAL executes: a
    concurrent client acting between the migration's read and its write."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}
        self.calls: list[list[str]] = []
        self.writes: list[str] = []
        self.refuse = False
        self.fail_op: str | None = None  # this command raises RuntimeError
        self.fail_write: str | None = None  # a write to this key raises
        self.before_write = None

    def _write(self, key: str, value: str, ttl: int) -> None:
        if key == self.fail_write:
            raise RuntimeError("Redis error: fake write failure")
        self.writes.append(key)
        self.data[key], self.ttl[key] = value.encode("ascii"), ttl

    def __call__(self, host, port, cmd_args, timeout=2.0, require_reply=False):
        args = [a.decode() if isinstance(a, bytes) else str(a) for a in cmd_args]
        self.calls.append(args)
        if self.refuse:
            raise ConnectionRefusedError("fake redis: refused")
        op, key = args[0], args[1]
        if op in ("SET", "EVAL") and self.before_write is not None:
            hook, self.before_write = self.before_write, None
            hook()
        if op == self.fail_op:
            raise RuntimeError("Redis error: fake failure")
        if op == "GET":
            return self.data.get(key)
        if op == "SET":
            ttl = int(args[4]) if len(args) >= 5 and args[3] == "EX" else -1
            self._write(key, args[2], ttl)
            return b"OK"
        if op == "EVAL":
            assert args[1] == vault_sync._MIGRATE_LEGACY_LUA and args[2] == "2", args[:3]
            entry, backup, read, new, backup_ttl = args[3:8]
            if self.data.get(entry) != read.encode("ascii"):
                return b"0"
            ttl = self.ttl.get(entry, -1)
            if ttl == 0 or ttl < -1:
                return b"0"
            self._write(backup, read, int(backup_ttl))
            self._write(entry, new, ttl)
            return b"1"
        if op == "TTL":
            return str(self.ttl.get(key, -2) if key in self.data else -2).encode()
        if op == "DEL":
            gone = self.data.pop(key, None) is not None
            self.ttl.pop(key, None)
            return b"1" if gone else b"0"
        raise AssertionError(f"unexpected redis command {args}")

    def sets_to(self, key: str) -> int:
        return self.writes.count(key)


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A default install: no BD_VAULT_KEY, no config/.vault.key, empty cwd."""
    monkeypatch.chdir(tmp_path)
    for k in ("BD_VAULT_KEY", "BD_REDIS_HOST", "BD_REDIS_PORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(vault_sync, "_VAULT_SYNC_INSTANCE", None)
    fake = FakeRedis()
    monkeypatch.setattr(vault_sync, "_redis_command", fake)
    return fake


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def migrated(self) -> list[str]:
        return [r.getMessage() for r in self.records if "migrated" in r.getMessage()]


@pytest.fixture
def vlog():
    """vault_sync's own records, independent of propagation (log.py sets
    propagate=False on the package logger once logging is configured)."""
    lg = logging.getLogger("bulk_downloader.vault_sync")
    handler, level = _Records(), lg.level
    lg.addHandler(handler)
    lg.setLevel(logging.INFO)
    yield handler
    lg.removeHandler(handler)
    lg.setLevel(level)


def _key_file(root):
    return root / "state" / "vault.key"


def test_fresh_install_key_is_random_owner_only_and_not_the_seed(fresh, tmp_path, monkeypatch):
    vs = vault_sync.get_vault_sync()
    assert vs.crypto.key != LEGACY_KEY, "default install still uses the public constant-seed key"
    kf = _key_file(tmp_path)
    assert kf.is_file()
    assert stat.S_IMODE(kf.stat().st_mode) == 0o600
    assert kf.read_bytes() == vs.crypto.key and len(vs.crypto.key) == 32

    # Same install, new process: the file is read back, not regenerated.
    monkeypatch.setattr(vault_sync, "_VAULT_SYNC_INSTANCE", None)
    assert vault_sync.get_vault_sync().crypto.key == vs.crypto.key

    # A second install draws its own key.
    other = tmp_path / "install2"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setattr(vault_sync, "_VAULT_SYNC_INSTANCE", None)
    key2 = vault_sync.get_vault_sync().crypto.key
    assert key2 not in (vs.crypto.key, LEGACY_KEY)
    assert _key_file(other).read_bytes() == key2


def _legacy_token(ttl=600.0, target=None):
    return SessionCrypto(LEGACY_KEY).encrypt(PAYLOAD, target_host=target, ttl_seconds=ttl)


def _assert_migrated(token_now: str, vs) -> None:
    assert vs.crypto.decrypt(token_now) == PAYLOAD
    with pytest.raises(InvalidTag):
        SessionCrypto(LEGACY_KEY).decrypt(token_now)


def test_legacy_entry_reads_once_is_backed_up_then_reencrypted(fresh, tmp_path, vlog):
    legacy = _legacy_token()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = legacy.encode(), 500
    vs = vault_sync.get_vault_sync()
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD

    assert fresh.data.get(BACKUP_KEY) == legacy.encode(), "legacy token was not backed up"
    assert fresh.ttl[BACKUP_KEY] == BACKUP_TTL, "legacy backup does not expire after 72 h"
    assert fresh.ttl[MAIN_KEY] == 500
    _assert_migrated(fresh.data[MAIN_KEY].decode(), vs)
    # Backup is written before the entry is overwritten, in one atomic EVAL
    # that only replaces the token it read.
    assert fresh.writes == [BACKUP_KEY, MAIN_KEY]
    evals = [c for c in fresh.calls if c[0] == "EVAL"]
    assert len(evals) == 1 and evals[0][3:6] == [MAIN_KEY, BACKUP_KEY, legacy]
    assert evals[0][7] == str(BACKUP_TTL)
    assert "'SET', KEYS[2], ARGV[1], 'EX', ARGV[3]" in vault_sync._MIGRATE_LEGACY_LUA
    assert "redis.call('GET', KEYS[1]) ~= ARGV[1]" in vault_sync._MIGRATE_LEGACY_LUA

    migrated = vlog.migrated()
    assert len(migrated) == 1
    assert SITE in migrated[0] and ACCOUNT in migrated[0]
    text = "\n".join(r.getMessage() for r in vlog.records)
    assert vs.crypto.key.hex() not in text and LEGACY_KEY.hex() not in text

    # A second read decrypts with the new key: no fallback, no second backup.
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD
    assert fresh.sets_to(BACKUP_KEY) == 1 and fresh.sets_to(MAIN_KEY) == 1
    assert len(vlog.migrated()) == 1


def test_legacy_fingerprint_check_uses_the_legacy_key(fresh):
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = _legacy_token().encode(), 400
    vs = vault_sync.get_vault_sync()
    assert vs.get_session(SITE, ACCOUNT, validate_fingerprint=True) == PAYLOAD
    assert fresh.sets_to(BACKUP_KEY) == 1
    _assert_migrated(fresh.data[MAIN_KEY].decode(), vs)


def test_legacy_entry_without_expiry_keeps_no_expiry(fresh):
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = _legacy_token().encode(), -1
    vs = vault_sync.get_vault_sync()
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD
    assert fresh.ttl[BACKUP_KEY] == BACKUP_TTL and fresh.ttl[MAIN_KEY] == -1
    _assert_migrated(fresh.data[MAIN_KEY].decode(), vs)


def test_lookup_session_migrates_a_legacy_entry(fresh):
    legacy = _legacy_token()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = legacy.encode(), 300
    vs = vault_sync.get_vault_sync()
    assert vs.lookup_session(SITE, ACCOUNT) == ("found", PAYLOAD)
    assert fresh.data[BACKUP_KEY] == legacy.encode() and fresh.ttl[BACKUP_KEY] == BACKUP_TTL
    _assert_migrated(fresh.data[MAIN_KEY].decode(), vs)
    assert fresh.sets_to(BACKUP_KEY) == 1


@pytest.mark.parametrize("reader", ["lookup_session", "get_session"])
def test_local_fallback_cache_migrates_a_legacy_entry(fresh, reader):
    fresh.refuse = True
    vs = vault_sync.get_vault_sync()
    legacy = _legacy_token()
    exp = 10**12
    vs._local_cache[MAIN_KEY] = (exp, legacy)
    got = getattr(vs, reader)(SITE, ACCOUNT)
    assert got == (("found", PAYLOAD) if reader == "lookup_session" else PAYLOAD)
    backup_exp, backup_token = vs._local_cache[BACKUP_KEY]
    assert backup_token == legacy
    assert abs(backup_exp - (time.time() + BACKUP_TTL)) < 60, "local backup does not expire after 72 h"
    assert vs._local_cache[MAIN_KEY][0] == exp
    _assert_migrated(vs._local_cache[MAIN_KEY][1], vs)


@pytest.mark.parametrize("gone", ["deleted", "expiring"])
def test_legacy_entry_gone_before_migration_is_read_not_rewritten(fresh, vlog, gone):
    legacy = _legacy_token()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = legacy.encode(), 500

    def vanish():
        if gone == "deleted":
            del fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY]
        else:
            fresh.ttl[MAIN_KEY] = 0

    fresh.before_write = vanish
    vs = vault_sync.get_vault_sync()
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD
    assert fresh.writes == [], "a deleted or expiring entry was rewritten"
    assert BACKUP_KEY not in fresh.data
    assert vlog.migrated() == []


FRESH = {"cookies": [{"name": "sid", "value": "fresh-login", "domain": ".example.com"}]}


def test_concurrent_set_session_during_migration_keeps_the_fresh_entry(fresh, vlog):
    legacy = _legacy_token(ttl=900.0)
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = legacy.encode(), 900
    vs = vault_sync.get_vault_sync()
    fresh.before_write = lambda: vs.set_session(SITE, ACCOUNT, FRESH, ttl_seconds=1800)
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD  # the read preceded the write

    assert vs.crypto.decrypt(fresh.data[MAIN_KEY].decode()) == FRESH, (
        "migration overwrote a concurrent set_session with the stale legacy entry")
    assert fresh.ttl[MAIN_KEY] == 1800, "migration replaced the fresh entry's TTL"
    assert BACKUP_KEY not in fresh.data and vlog.migrated() == []
    assert vs.get_session(SITE, ACCOUNT) == FRESH


@pytest.mark.parametrize("reader", ["lookup_session", "get_session"])
def test_concurrent_set_session_during_local_migration_keeps_the_fresh_entry(
        fresh, monkeypatch, reader):
    fresh.refuse = True
    vs = vault_sync.get_vault_sync()
    vs._local_cache[MAIN_KEY] = (10**12, _legacy_token())
    real_open = vs.legacy_crypto.decrypt_envelope

    def open_then_concurrent_set(*a, **k):
        envelope = real_open(*a, **k)
        assert vs.set_session(SITE, ACCOUNT, FRESH, ttl_seconds=1800) is True
        return envelope

    monkeypatch.setattr(vs.legacy_crypto, "decrypt_envelope", open_then_concurrent_set)
    getattr(vs, reader)(SITE, ACCOUNT)

    exp, token = vs._local_cache[MAIN_KEY]
    assert vs.crypto.decrypt(token) == FRESH, (
        "local migration overwrote a concurrent set_session with the stale legacy entry")
    assert exp < 10**12 and BACKUP_KEY not in vs._local_cache


class _PausingCache(dict):
    """The fallback cache; the "reader" thread pauses once at its first write
    ("set") or delete ("del") of key, or ("lock", None) just before it takes
    the cache lock."""

    def __init__(self, op: str, key: str | None) -> None:
        super().__init__()
        self.op, self.key = op, key
        self.paused, self.resume = threading.Event(), threading.Event()

    def _gate(self, op: str, key: str | None) -> None:
        if ((op, key) == (self.op, self.key) and threading.current_thread().name == "reader"
                and not self.paused.is_set()):
            self.paused.set()
            assert self.resume.wait(10), "the paused reader was never resumed"

    def __setitem__(self, key, value):
        self._gate("set", key)
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self._gate("del", key)
        super().__delitem__(key)


class _SignalLock:
    """vs._local_lock: signals once the "writer" thread asks for it; the
    "reader" thread passes the cache's ("lock", None) gate before taking it."""

    def __init__(self, inner, cache: _PausingCache, asked: threading.Event) -> None:
        self.inner, self.cache, self.asked = inner, cache, asked

    def __enter__(self):
        name = threading.current_thread().name
        if name == "writer":
            self.asked.set()
        elif name == "reader":
            self.cache._gate("lock", None)
        return self.inner.__enter__()

    def __exit__(self, *exc):
        return self.inner.__exit__(*exc)


def _race_local(vs, read, write) -> None:
    """read() runs in a thread that pauses at the vs._local_cache
    (_PausingCache) gate; write() then runs in a second thread. The reader
    resumes once the writer has finished or is waiting on the cache lock."""
    asked = threading.Event()
    vs._local_lock = _SignalLock(
        getattr(vs, "_local_lock", threading.Lock()), vs._local_cache, asked)

    def writer():
        try:
            write()
        finally:
            asked.set()

    r = threading.Thread(target=read, name="reader")
    r.start()
    assert vs._local_cache.paused.wait(10), "the reader never reached its pause point"
    w = threading.Thread(target=writer, name="writer")
    w.start()
    assert asked.wait(10), "the writer neither finished nor waited on the cache lock"
    vs._local_cache.resume.set()
    r.join(10)
    w.join(10)
    assert not r.is_alive() and not w.is_alive(), "reader or writer deadlocked"


@pytest.mark.parametrize("reader", ["lookup_session", "get_session"])
def test_set_session_after_the_local_compare_waits_for_the_migration(fresh, reader):
    fresh.refuse = True
    vs = vault_sync.get_vault_sync()
    vs._local_cache = _PausingCache("set", BACKUP_KEY)
    vs._local_cache[MAIN_KEY] = (time.time() + 600, _legacy_token())
    stored = []
    _race_local(vs, lambda: getattr(vs, reader)(SITE, ACCOUNT),
                lambda: stored.append(vs.set_session(SITE, ACCOUNT, FRESH, ttl_seconds=1800)))
    assert stored == [True]
    exp, token = vs._local_cache[MAIN_KEY]
    assert vs.crypto.decrypt(token) == FRESH, (
        "local migration overwrote a set_session that landed after its compare")
    assert exp - time.time() > 1700, "local migration replaced the fresh entry's TTL"


def test_invalidate_after_the_local_compare_is_not_undone_by_the_migration(fresh):
    fresh.refuse = True
    vs = vault_sync.get_vault_sync()
    vs._local_cache = _PausingCache("set", BACKUP_KEY)
    vs._local_cache[MAIN_KEY] = (time.time() + 600, _legacy_token())
    removed = []
    _race_local(vs, lambda: vs.get_session(SITE, ACCOUNT),
                lambda: removed.append(vs.invalidate_session(SITE, ACCOUNT)))
    assert removed == [True]
    assert MAIN_KEY not in vs._local_cache, "local migration resurrected an invalidated entry"


@pytest.mark.parametrize("pause", [("del", MAIN_KEY), ("lock", None)], ids=["at-delete", "before-lock"])
@pytest.mark.parametrize("reader", ["get_session", "get_ttl"])
def test_expiry_eviction_spares_an_entry_stored_after_its_read(fresh, reader, pause):
    fresh.refuse = True
    vs = vault_sync.get_vault_sync()
    vs._local_cache = _PausingCache(*pause)
    vs._local_cache[MAIN_KEY] = (time.time() - 1, vs.crypto.encrypt(PAYLOAD, ttl_seconds=1.0))
    _race_local(vs, lambda: getattr(vs, reader)(SITE, ACCOUNT),
                lambda: vs.set_session(SITE, ACCOUNT, FRESH, ttl_seconds=1800))
    assert vs.get_session(SITE, ACCOUNT) == FRESH, (
        "expiry eviction deleted a set_session stored after its read")


def test_failed_backup_leaves_the_legacy_entry_untouched(fresh, vlog):
    legacy = _legacy_token()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = legacy.encode(), 500
    fresh.fail_write = BACKUP_KEY
    vs = vault_sync.get_vault_sync()
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD
    assert fresh.data[MAIN_KEY] == legacy.encode() and fresh.sets_to(MAIN_KEY) == 0
    assert vlog.migrated() == []


@pytest.mark.parametrize("op", ["EVAL"])
def test_redis_error_during_migration_still_returns_the_session(fresh, vlog, op):
    legacy = _legacy_token()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = legacy.encode(), 500
    fresh.fail_op = op
    vs = vault_sync.get_vault_sync()
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD
    assert vs.lookup_session(SITE, ACCOUNT) == ("found", PAYLOAD)
    assert fresh.data[MAIN_KEY] == legacy.encode() and BACKUP_KEY not in fresh.data
    assert vlog.migrated() == []


def test_key_file_creation_race_reads_the_winners_key(fresh, tmp_path, monkeypatch):
    winner = os.urandom(32)
    real_link = os.link
    raced = []

    def racing_link(src, dst, *a, **k):
        if str(dst).endswith("vault.key") and not raced:
            raced.append(dst)
            fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.write(fd, winner)
            os.close(fd)
        return real_link(src, dst, *a, **k)

    monkeypatch.setattr(vault_sync.os, "link", racing_link)
    assert vault_sync.get_vault_sync().crypto.key == winner
    assert len(raced) == 1 and _key_file(tmp_path).read_bytes() == winner
    assert os.listdir(tmp_path / "state") == ["vault.key"], "temp key file left behind"


def test_concurrent_first_open_creator_and_reader_share_one_full_key(fresh, tmp_path, monkeypatch):
    kdir = (tmp_path / "state").resolve()
    real_open = os.open
    seen: dict = {}

    def open_then_reader(path, flags, mode=0o777, *a, **k):
        fd = real_open(path, flags, mode, *a, **k)
        if "ran" not in seen and Path(os.fspath(path)).resolve().parent == kdir:
            seen["ran"] = True  # the reader's own opens pass straight through
            seen["mode"] = stat.S_IMODE(os.fstat(fd).st_mode)
            try:
                seen["reader"] = vault_sync._load_or_create_install_key(vault_sync.INSTALL_KEY_FILE)
            except Exception as exc:  # asserted below
                seen["reader"] = exc
        return fd

    monkeypatch.setattr(vault_sync.os, "open", open_then_reader)
    creator = vault_sync._load_or_create_install_key(vault_sync.INSTALL_KEY_FILE)

    assert seen.get("ran"), "no key-file open observed"
    assert not isinstance(seen["reader"], Exception), (
        f"concurrent reader raised on a key file still being written: {seen['reader']}")
    assert seen["reader"] == creator and len(creator) == 32
    assert seen["mode"] & 0o077 == 0, "key bytes were written to a group/other-readable file"
    kf = _key_file(tmp_path)
    assert kf.read_bytes() == creator and stat.S_IMODE(kf.stat().st_mode) == 0o600
    assert os.listdir(kdir) == ["vault.key"], "temp key file left behind"


def test_dangling_symlink_key_file_is_refused(fresh, tmp_path):
    kf = _key_file(tmp_path)
    kf.parent.mkdir()
    kf.symlink_to(tmp_path / "unmounted" / "vault.key")
    with pytest.raises(vault_sync.VaultKeyFileError, match="does not resolve"):
        vault_sync.get_vault_sync()
    assert kf.is_symlink() and not (tmp_path / "unmounted").exists()


def test_new_key_entry_needs_no_fallback_and_no_backup(fresh, vlog):
    vs = vault_sync.get_vault_sync()
    assert vs.set_session(SITE, ACCOUNT, PAYLOAD, ttl_seconds=200) is True
    assert vs.get_session(SITE, ACCOUNT) == PAYLOAD
    assert vs.lookup_session(SITE, ACCOUNT) == ("found", PAYLOAD)
    assert BACKUP_KEY not in fresh.data
    assert fresh.sets_to(MAIN_KEY) == 1
    assert vlog.migrated() == []


def test_foreign_key_entry_is_not_accepted_or_backed_up(fresh, vlog):
    foreign = SessionCrypto(os.urandom(32)).encrypt(PAYLOAD, ttl_seconds=600)
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = foreign.encode(), 500
    vs = vault_sync.get_vault_sync()
    with pytest.raises(InvalidTag):
        vs.get_session(SITE, ACCOUNT)
    assert vs.lookup_session(SITE, ACCOUNT) == ("unreadable", None)
    assert BACKUP_KEY not in fresh.data and fresh.data[MAIN_KEY] == foreign.encode()
    assert fresh.writes == [], "a peer's entry was overwritten"
    lines = [r.getMessage() for r in vlog.records if MISMATCH in r.getMessage()]
    assert len(lines) == 2, f"expected one key-mismatch line per failed read, got {lines}"
    assert all(SITE in m and ACCOUNT in m for m in lines)
    assert vs.crypto.key.hex() not in "\n".join(lines)


def test_key_mismatch_without_legacy_fallback_is_logged(fresh, vlog, monkeypatch):
    monkeypatch.setenv("BD_VAULT_KEY", "operator-cluster-key")
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = (
        SessionCrypto(os.urandom(32)).encrypt(PAYLOAD, ttl_seconds=600).encode(), 500)
    vs = vault_sync.get_vault_sync()
    assert vs.lookup_session(SITE, ACCOUNT) == ("unreadable", None)
    assert sum(MISMATCH in r.getMessage() for r in vlog.records) == 1


@pytest.mark.parametrize("umask", [0o000, 0o022, 0o277])
def test_new_key_directory_is_owner_only_whatever_the_umask(fresh, tmp_path, umask):
    old = os.umask(umask)
    try:
        vault_sync.get_vault_sync()
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700, (
        "new key directory is not owner-only")


def test_existing_open_key_directory_is_kept_and_warned(fresh, tmp_path, vlog):
    kdir = tmp_path / "state"
    kdir.mkdir()
    os.chmod(kdir, 0o755)
    vault_sync.get_vault_sync()
    assert stat.S_IMODE(kdir.stat().st_mode) == 0o755
    warned = [r.getMessage() for r in vlog.records if r.levelno == logging.WARNING]
    assert any("key directory" in m and "755" in m for m in warned), warned
    assert stat.S_IMODE(_key_file(tmp_path).stat().st_mode) == 0o600


def test_bd_vault_key_precedence_unchanged_and_no_legacy_fallback(fresh, tmp_path, monkeypatch):
    monkeypatch.setenv("BD_VAULT_KEY", "operator-cluster-key")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".vault.key").write_bytes(b"k" * 32)
    vs = vault_sync.get_vault_sync()
    assert vs.crypto.key == hashlib.sha256(b"operator-cluster-key").digest()
    assert not _key_file(tmp_path).exists()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = _legacy_token().encode(), 500
    assert vs.lookup_session(SITE, ACCOUNT) == ("unreadable", None)
    assert BACKUP_KEY not in fresh.data


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(b"k" * 32, b"k" * 32), (b"short-operator-key\n", hashlib.sha256(b"short-operator-key").digest())],
)
def test_config_vault_key_precedence_unchanged(fresh, tmp_path, raw, expected):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".vault.key").write_bytes(raw)
    vs = vault_sync.get_vault_sync()
    assert vs.crypto.key == expected
    assert not _key_file(tmp_path).exists()
    fresh.data[MAIN_KEY], fresh.ttl[MAIN_KEY] = _legacy_token().encode(), 500
    assert vs.lookup_session(SITE, ACCOUNT) == ("unreadable", None)


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o620, 0o602, 0o644])
def test_loose_permission_key_file_is_refused_not_regenerated(fresh, tmp_path, mode):
    kf = _key_file(tmp_path)
    kf.parent.mkdir()
    kf.write_bytes(b"x" * 32)
    os.chmod(kf, mode)
    with pytest.raises(vault_sync.VaultKeyFileError, match="vault.key") as exc:
        vault_sync.get_vault_sync()
    assert oct(mode)[2:] in str(exc.value)
    assert "78" * 8 not in str(exc.value)  # key bytes never in the message
    assert kf.read_bytes() == b"x" * 32 and stat.S_IMODE(kf.stat().st_mode) == mode
    assert vault_sync._VAULT_SYNC_INSTANCE is None


def test_wrong_length_key_file_is_refused(fresh, tmp_path):
    kf = _key_file(tmp_path)
    kf.parent.mkdir()
    kf.write_bytes(b"x" * 31)
    os.chmod(kf, 0o600)
    with pytest.raises(vault_sync.VaultKeyFileError, match="31 bytes"):
        vault_sync.get_vault_sync()
    assert kf.read_bytes() == b"x" * 31
