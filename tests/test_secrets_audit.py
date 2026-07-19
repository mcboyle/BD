"""Tests for the opt-in secrets-store audit log (NEW-8).

Covers the two configurable forks (capture scope: mutations-only vs all;
sink: dedicated JSONL file vs app logger) plus the invariants that make
the feature safe to ship default-off: byte-identical off-path, redaction
(secret value never written), fail-open (a logging failure never breaks a
credential op), single-generation rotation, transparent delegation of all
non-audited backend methods, and object-identity stability of the proxy.

Plain functions + a context manager for env so the suite is green under
both real pytest and the custom runner (no local fixtures).
"""
from contextlib import contextmanager
from pathlib import Path
import json
import logging
import os
import tempfile

from bulk_downloader import secrets_store as ss
from bulk_downloader import secrets_audit as sa


# ─── helpers ─────────────────────────────────────────────────────────

class _FakeBackend:
    """Minimal duck-typed backend for isolating audit behaviour."""
    name = "fake"

    def __init__(self):
        self._store = {}
        self.changed = False

    def set(self, key, password):
        self._store[key] = password

    def get(self, key):
        return self._store.get(key)

    def delete(self, key):
        existed = key in self._store
        self._store.pop(key, None)
        return existed  # exact bool, like the real backends

    def list_keys(self):
        return list(self._store)

    def change_password(self, old, new):
        self.changed = True
        return True


@contextmanager
def _env(**kw):
    """Set env vars for the block, restoring prior values after."""
    prior = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextmanager
def _fake_active():
    """Install a fresh _FakeBackend as the active backend; reset globals."""
    prior_be, prior_pref, prior_cache = ss._backend, ss._backend_pref, ss._audited_cache
    fake = _FakeBackend()
    ss._backend = fake
    ss._backend_pref = "plaintext"
    ss._audited_cache = None
    try:
        yield fake
    finally:
        ss._backend, ss._backend_pref, ss._audited_cache = prior_be, prior_pref, prior_cache


def _read_lines(path):
    return [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]


# ─── config / mode parsing ───────────────────────────────────────────

def test_mode_off_by_default():
    with _env(BD_SECRETS_AUDIT=None):
        assert sa.mode() == "off"
        assert sa.is_enabled() is False


def test_mode_mutations_values():
    for v in ("1", "on", "true", "yes", "mutations", "MUTATE", " On "):
        with _env(BD_SECRETS_AUDIT=v):
            assert sa.mode() == "mutations", v
            assert sa.is_enabled() is True


def test_mode_all_values():
    for v in ("all", "reads", "READ", "verbose"):
        with _env(BD_SECRETS_AUDIT=v):
            assert sa.mode() == "all", v


def test_mode_garbage_is_off():
    with _env(BD_SECRETS_AUDIT="nonsense"):
        assert sa.mode() == "off"


# ─── off-path: byte-identical, no wrapper, no file ───────────────────

def test_off_returns_raw_backend():
    with _env(BD_SECRETS_AUDIT=None), _fake_active() as fake:
        assert ss.get_backend() is fake  # not wrapped


def test_off_writes_no_file():
    with tempfile.TemporaryDirectory() as td:
        af = str(Path(td) / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT=None, BD_SECRETS_AUDIT_FILE=af), _fake_active():
            be = ss.get_backend()
            be.set("k", "v")
            be.get("k")
            be.delete("k")
            assert not Path(af).exists()


# ─── mutations mode (the friendly default) ───────────────────────────

def test_mutations_logs_set_and_delete_not_get():
    with tempfile.TemporaryDirectory() as td:
        af = str(Path(td) / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT="1", BD_SECRETS_AUDIT_FILE=af,
                  BD_SECRETS_AUDIT_SINK="file"), _fake_active():
            be = ss.get_backend()
            be.set("acct", "hunter2")
            be.get("acct")          # read — must NOT be logged in mutations mode
            be.delete("acct")
        rows = _read_lines(af)
        actions = [r["action"] for r in rows]
        assert actions == ["set", "delete"]
        assert all(r["key"] == "acct" for r in rows)
        assert rows[0]["backend"] == "fake"
        assert rows[0]["ok"] is True          # set succeeded
        assert rows[1]["ok"] is True          # delete found+removed the key


def test_all_mode_logs_get_too():
    with tempfile.TemporaryDirectory() as td:
        af = str(Path(td) / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT="all", BD_SECRETS_AUDIT_FILE=af,
                  BD_SECRETS_AUDIT_SINK="file"), _fake_active():
            be = ss.get_backend()
            be.set("acct", "hunter2")
            be.get("acct")
            be.delete("acct")
        actions = [r["action"] for r in _read_lines(af)]
        assert actions == ["set", "get", "delete"]


def test_get_ok_reflects_hit_vs_miss():
    with tempfile.TemporaryDirectory() as td:
        af = str(Path(td) / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT="all", BD_SECRETS_AUDIT_FILE=af), _fake_active():
            be = ss.get_backend()
            be.set("present", "x")
            be.get("present")   # hit
            be.get("absent")    # miss
        rows = [r for r in _read_lines(af) if r["action"] == "get"]
        assert [r["ok"] for r in rows] == [True, False]


# ─── redaction ───────────────────────────────────────────────────────

def test_secret_value_never_written():
    with tempfile.TemporaryDirectory() as td:
        af = str(Path(td) / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT="all", BD_SECRETS_AUDIT_FILE=af), _fake_active():
            be = ss.get_backend()
            be.set("bulkdl-site-x", "S3CRET-PASSWORD-VALUE")
            be.get("bulkdl-site-x")
        blob = Path(af).read_text(encoding="utf-8")
        assert "S3CRET-PASSWORD-VALUE" not in blob
        assert "bulkdl-site-x" in blob   # key NAME is fine to record


# ─── fail-open ───────────────────────────────────────────────────────

def test_failopen_unwritable_path_does_not_break_set():
    with tempfile.TemporaryDirectory() as td:
        # Parent is a *file*, so mkdir(parents=True) raises NotADirectoryError.
        blocker = Path(td) / "iam_a_file"
        blocker.write_text("x", encoding="utf-8")
        bad = str(blocker / "sub" / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT="1", BD_SECRETS_AUDIT_FILE=bad), _fake_active() as fake:
            be = ss.get_backend()
            be.set("k", "v")                 # must not raise
            assert fake.get("k") == "v"      # the real op still happened
            assert be.delete("k") is True    # delete still works + returns real value


# ─── rotation ────────────────────────────────────────────────────────

def test_single_generation_rotation():
    with tempfile.TemporaryDirectory() as td:
        af = Path(td) / "secrets_audit.jsonl"
        with _env(BD_SECRETS_AUDIT="1", BD_SECRETS_AUDIT_FILE=str(af),
                  BD_SECRETS_AUDIT_MAX_BYTES="200"), _fake_active():
            be = ss.get_backend()
            for i in range(40):
                be.set(f"key-{i}", "v")   # each set writes one JSONL line
        assert af.exists()
        assert af.with_name(af.name + ".1").exists()  # rotated generation present


# ─── log sink ────────────────────────────────────────────────────────

def test_log_sink_routes_to_logger_and_writes_no_file():
    records = []

    class _Cap(logging.Handler):
        def emit(self, rec):
            records.append(rec.getMessage())

    lg = logging.getLogger("bulk_downloader.secrets_audit")
    h = _Cap()
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as td:
            af = str(Path(td) / "secrets_audit.jsonl")
            with _env(BD_SECRETS_AUDIT="1", BD_SECRETS_AUDIT_SINK="log",
                      BD_SECRETS_AUDIT_FILE=af), _fake_active():
                be = ss.get_backend()
                be.set("k", "v")
            assert not Path(af).exists()      # log sink -> no dedicated file
            assert any("secrets_audit" in m and '"action":"set"' in m for m in records)
    finally:
        lg.removeHandler(h)


# ─── transparent delegation + identity ───────────────────────────────

def test_delegation_passthrough_and_hasattr():
    with _env(BD_SECRETS_AUDIT="1"), _fake_active() as fake:
        be = ss.get_backend()
        assert hasattr(be, "change_password")     # app.py:13245 relies on this
        assert be.change_password("old", "new") is True
        assert fake.changed is True                # delegated to the real backend
        assert be.list_keys() == fake.list_keys()
        assert be.name == "fake"
        assert not hasattr(be, "totally_made_up_method")


def test_delete_returns_real_value_unaltered():
    with tempfile.TemporaryDirectory() as td:
        af = str(Path(td) / "secrets_audit.jsonl")
        with _env(BD_SECRETS_AUDIT="1", BD_SECRETS_AUDIT_FILE=af), _fake_active():
            be = ss.get_backend()
            be.set("here", "v")
            assert be.delete("here") is True    # key existed
            assert be.delete("gone") is False   # key did not exist
        rows = [r for r in _read_lines(af) if r["action"] == "delete"]
        assert [r["ok"] for r in rows] == [True, False]


def test_wrapper_identity_is_stable_when_enabled():
    with _env(BD_SECRETS_AUDIT="1"), _fake_active():
        first = ss.get_backend()
        second = ss.get_backend()
        assert first is second                  # cached wrapper, stable identity
        assert isinstance(first, ss._AuditedBackend)
