"""Row 435: the hold writer must not destroy the store its own reader refuses.

``download_hold.hold()`` / ``lift()`` route through
``global_config.set_config``, which re-reads ``app_config.json`` and, on the
defective base, swallowed every read/parse failure as ``current = {}`` before
merging and atomically replacing the file.  So precisely when ``hold_state()``
fail-closes to UNKNOWN -- the refusal the reader exists for -- the operator's
natural remediation (``POST /api/download_hold`` or ``/lift``) silently
discarded every other global setting and persisted a file holding only the hold
record.  ``global_config`` itself documents that this file may carry tokens and
credentials, so one remediation POST could strip the settings authenticated
capture depends on while the operator believed they only cleared a hold.  The
writer reproduced the fail-open shape the reader was built to refuse
(CLAUDE.md A7).

WHICH ARM OF THE ACCEPTANCE THIS TAKES.  Row 435 permits the writer to refuse
OR to move the unreadable file aside as a ``.corrupt-*`` sibling, citing
``secrets_store`` and ``extension_vault`` as the house pattern.  That house
pattern no longer exists: ``extension_vault._load_tokens`` (extension_vault.py,
the AF4 paragraph) records that the rename WAS ITSELF THE DEFECT -- ``replace``
needs DIRECTORY permission, so a chmod-000 file or a transient EIO renamed the
operator's live store away exactly as readily as a torn write did -- and both
modules now preserve the file IN PLACE, byte-identical, under its own name.  A
tree-wide search for ``corrupt-`` in ``bulk_downloader/`` returns only that
retirement note.  These gates therefore take the REFUSE arm, and assert the
store is byte-identical afterwards, which is strictly stronger than the aside.

WHAT IS AND IS NOT CLAIMED ABOUT DURABILITY.  These gates are about a write
that must not DESTROY data, not about a write surviving power loss.  The
refusal happens before any temporary file is created, so nothing is claimed
about ``flush()`` (process death) or ``fsync()`` (machine death); ``set_config``
does neither, before or after this cut, and that remains open.

ISOLATION.  ``global_config._CONFIG_FILE`` is the relative ``Path`` the
production launcher resolves by cwd'ing into INSTALL_DIR, so every gate chdirs
into pytest's tmp_path rather than rewriting the module global, and the module's
read cache is cleared before and after each one.  The operator's own
app_config.json is never named.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from bulk_downloader import download_hold as dh
from bulk_downloader import global_config as gc

BD_GATE_SCOPE = "module"

# N >= 3 known keys, per the row's acceptance. Zero-entropy, obviously-fake
# values (CLAUDE.md A4: security fixtures use documented zero-entropy values).
_OTHER_KEYS: dict = {
    "oidc_client_secret": "row435-not-a-real-secret",
    "oidc_issuer": "https://row435.invalid/",
    "queue_hk_max_retries": 9,
    "queue_hk_stale_hours": 3,
}
_N = len(_OTHER_KEYS)

# The distinctive refusal marker the fix must emit. Pinned here so a refusal
# for an UNRELATED reason cannot launder these results.
_REFUSE_MARKER = "REFUSING to overwrite an unreadable app_config"


@pytest.fixture
def store(tmp_path, monkeypatch):
    """cwd in an isolated tree with a VALID app_config.json holding _N keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gc, "_cached", None, raising=False)
    monkeypatch.setattr(gc, "_cached_mtime", 0.0, raising=False)
    p = tmp_path / "app_config.json"
    p.write_text(json.dumps(_OTHER_KEYS, indent=2), encoding="utf-8")
    # PRECONDITION: the fixture really built the shape this file assumes.
    assert p.exists(), "fixture did not create app_config.json"
    readback = json.loads(p.read_text(encoding="utf-8"))
    assert readback == _OTHER_KEYS, readback
    assert len(readback) == _N >= 3, readback
    assert gc._CONFIG_FILE.resolve() == p.resolve(), (
        "the module's relative store path did not resolve into tmp_path; "
        "the chdir isolation is not in force and this gate would touch the "
        "operator's real app_config.json")
    return p


def _corrupt(p) -> bytes:
    """Replace the store with non-JSON. Returns the exact bytes written."""
    raw = b"{ row435 this is not json at all"
    p.write_bytes(raw)
    assert p.read_bytes() == raw
    return raw


def _refusals(capsys) -> int:
    """How many times the writer's distinctive refusal fired."""
    return sum(1 for line in capsys.readouterr().err.splitlines()
               if _REFUSE_MARKER in line)


# ── 1. the defect itself: a hold over a corrupt store erases the settings ────

@pytest.mark.parametrize("op", ["hold", "lift"])
def test_a_hold_or_lift_over_an_unreadable_store_erases_nothing(
        store, capsys, op):
    raw = _corrupt(store)

    # PRECONDITION: this is exactly the state the reader refuses for.
    st = dh.hold_state()
    assert st["state"] == dh.UNKNOWN, st
    assert st["reason"] == "store_corrupt", st
    assert st["held"] is True, st          # UNKNOWN refuses

    written = dh.hold("operator", "row435 remediation") if op == "hold" \
        else dh.lift("row435 remediation")

    # The write must REFUSE, and say so exactly once.
    assert written is False, (
        f"{op}() reported the durable record persisted over a store it could "
        f"not read")
    assert _refusals(capsys) == 1, "the refusal did not fire exactly once"

    # The store is untouched, BYTE-IDENTICAL -- stronger than the aside arm.
    assert store.read_bytes() == raw, "the unreadable store was rewritten"

    # No partial state is observable: no temp file, no aside sibling, and the
    # only permitted extra member is the writer's permanent lock sibling.
    residue = sorted(q.name for q in store.parent.iterdir()
                     if q.name not in {"app_config.json",
                                       "app_config.json.lock"})
    assert residue == [], f"the refused write left residue: {residue}"

    # And the operator is not told the hold is clear: the reader still refuses.
    after = dh.hold_state()
    assert after["state"] == dh.UNKNOWN, after
    assert dh.downloads_allowed()[0] is False, after


def test_a_lift_over_an_unreadable_store_cannot_report_a_clean_clear(
        store, capsys):
    """The laundering arm of the row, asserted at the operator's own seam.

    ``api_download_hold_lift`` returns 500 + ``lift not persisted`` when the
    write refuses, and reports ``hold_state()`` alongside it, so UNKNOWN
    reaches the operator instead of a durable CLEAR.
    """
    _corrupt(store)
    assert dh.hold_state()["state"] == dh.UNKNOWN

    from flask import Flask
    from bulk_downloader import app_download_hold as adh

    app = Flask(__name__)
    app.config["TESTING"] = True
    monkeyed = adh._check_csrf
    adh._check_csrf = lambda *a, **k: None
    try:
        assert adh.register_routes(app) >= 2
        resp = app.test_client().post("/api/download_hold/lift", json={})
    finally:
        adh._check_csrf = monkeyed

    assert resp.status_code == 500, resp.status_code
    body = resp.get_json()
    assert body["ok"] is False, body
    assert body["error"] == "lift not persisted", body
    assert body["state"] == dh.UNKNOWN, body
    assert _refusals(capsys) == 1
    # Nothing durable claims CLEAR.
    assert dh.hold_state()["state"] == dh.UNKNOWN


# ── 2. every unreadable classification, not just corrupt JSON ───────────────

@pytest.mark.parametrize("label,raw", [
    ("corrupt_json", b"{ row435 not json"),
    ("truncated_json", b'{"oidc_issuer": "https://row435.invalid/'),
    ("store_is_a_list", b'["row435"]'),
    ("store_is_a_scalar", b'"row435"'),
    ("undecodable_bytes", b"\xff\xfe\x00row435"),
])
def test_every_unmeasurable_store_refuses_the_write(store, capsys, label, raw):
    store.write_bytes(raw)
    assert dh.hold_state()["state"] == dh.UNKNOWN, label
    assert gc.set_config({"row435_probe": True}) is False, label
    assert _refusals(capsys) == 1, label
    assert store.read_bytes() == raw, label


def test_an_unopenable_store_refuses_the_write(store, capsys):
    """EACCES on the store is unmeasurable, so the write refuses too."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")
    os.chmod(store, 0)
    try:
        # PRECONDITION: the chmod actually took.
        with pytest.raises(OSError):
            store.read_text(encoding="utf-8")
        assert dh.hold_state()["reason"] == "store_unreadable"
        assert gc.set_config({"row435_probe": True}) is False
        assert _refusals(capsys) == 1
    finally:
        os.chmod(store, stat.S_IRUSR | stat.S_IWUSR)
    assert json.loads(store.read_text(encoding="utf-8")) == _OTHER_KEYS


def test_an_unstattable_store_refuses_and_still_loses_nothing(store, capsys):
    """An unreadable PARENT DIRECTORY refuses too -- at an earlier boundary.

    MEASURED, not assumed. ``_read_current_or_refuse`` classifies a ``stat``
    step because ``Path.exists()`` swallows ENOENT/ENOTDIR/EBADF/ELOOP but not
    EACCES. In THIS writer that branch is not reachable by removing the
    directory's permissions: ``app_config_transaction`` opens the sibling
    ``app_config.json.lock`` before the classifier runs, so the EACCES lands
    there first and the generic outer handler reports it. The ``stat`` branch
    is retained as belt-and-braces for an OSError the lock open does not
    also hit (EIO, ENAMETOOLONG), and this gate deliberately does NOT claim to
    exercise it -- it asserts the property that actually matters and is
    actually measurable: the write REFUSES and the store is byte-identical,
    whichever boundary catches it.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")
    parent = store.parent
    before = stat.S_IMODE(parent.stat().st_mode)
    raw = store.read_bytes()
    os.chmod(parent, stat.S_IRUSR)          # r--, no execute: stat() is EACCES
    try:
        # PRECONDITION: the chmod took -- stat on the store really raises.
        with pytest.raises(OSError):
            os.stat(store)
        assert gc.set_config({"row435_probe": True}) is False
        errs = capsys.readouterr().err
        assert "[global_config]" in errs and "Permission denied" in errs, errs
    finally:
        os.chmod(parent, before)
    assert store.read_bytes() == raw, "an unwritable directory lost the store"
    assert json.loads(store.read_text(encoding="utf-8")) == _OTHER_KEYS


def test_every_reachable_refusal_step_is_named_distinctly(store, capsys):
    """A refusal that cannot be acted on is barely better than a silent one
    (CLAUDE.md A7). Each reachable boundary reports WHICH one it was, so
    'repair the JSON' is never confused with 'fix the file permissions'."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")
    seen = set()
    for raw, step in [(b"{ nope", "parse"),
                      (b'["row435"]', "not-a-json-object"),
                      (b"\xff\xfe\x00row435", "decode")]:
        store.write_bytes(raw)
        assert gc.set_config({"row435_probe": True}) is False
        errs = capsys.readouterr().err
        assert f"({step}: " in errs, (step, errs)
        seen.add(step)

    # The fourth: an unopenable FILE under a readable directory.
    store.write_text(json.dumps(_OTHER_KEYS), encoding="utf-8")
    os.chmod(store, 0)
    try:
        assert gc.set_config({"row435_probe": True}) is False
        errs = capsys.readouterr().err
        assert "(read: PermissionError)" in errs, errs
        seen.add("read")
    finally:
        os.chmod(store, stat.S_IRUSR | stat.S_IWUSR)

    assert seen == {"parse", "not-a-json-object", "decode", "read"}, seen
    # And they are four DISTINCT strings, not one message four times.
    assert len(seen) == 4


# ── 3. negative controls: the refusal cannot be manufactured ────────────────

def test_a_valid_store_still_merges_a_hold_and_keeps_every_key(store, capsys):
    """NEGATIVE CONTROL. An ordinary write over a READABLE store must still
    merge, or the fix is just a broken writer."""
    assert dh.hold_state()["state"] == dh.CLEAR
    assert dh.hold("operator", "row435 ordinary") is True
    assert _refusals(capsys) == 0, "an ordinary write must not refuse"

    on_disk = json.loads(store.read_text(encoding="utf-8"))
    survivors = {k: v for k, v in on_disk.items() if k != dh.HOLD_KEY}
    assert survivors == _OTHER_KEYS, survivors
    assert len(survivors) == _N, survivors
    assert on_disk[dh.HOLD_KEY]["held"] is True, on_disk
    assert dh.hold_state()["state"] == dh.HELD

    assert dh.lift("row435 ordinary lift") is True
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert {k: v for k, v in on_disk.items() if k != dh.HOLD_KEY} == _OTHER_KEYS
    assert dh.hold_state()["state"] == dh.CLEAR
    assert sorted(q.name for q in store.parent.iterdir()) == [
        "app_config.json", "app_config.json.lock"]


def test_an_absent_store_still_initialises(tmp_path, monkeypatch, capsys):
    """NEGATIVE CONTROL. A fresh install has no store and has never been held;
    refusing there would break every first boot and every test tmpdir."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gc, "_cached", None, raising=False)
    monkeypatch.setattr(gc, "_cached_mtime", 0.0, raising=False)
    assert not (tmp_path / "app_config.json").exists()
    assert dh.hold_state()["reason"] == "store_absent"
    assert gc.set_config({"row435_probe": True}) is True
    assert _refusals(capsys) == 0
    assert json.loads((tmp_path / "app_config.json").read_text()) == {
        "row435_probe": True}


def test_a_malformed_hold_record_inside_a_valid_store_still_merges(
        store, capsys):
    """NEGATIVE CONTROL, and the boundary that keeps the fix narrow.

    ``hold_state()`` also answers UNKNOWN for a well-formed JSON OBJECT whose
    hold RECORD is malformed. That store is perfectly readable, nothing would
    be erased by merging into it, and refusing there would make the corrupt
    record unfixable through the API. The writer keys on whether the STORE
    could be read, never on what the reader concluded about the record.
    """
    doc = dict(_OTHER_KEYS)
    doc[dh.HOLD_KEY] = "held"          # a string, not an object
    store.write_text(json.dumps(doc), encoding="utf-8")
    st = dh.hold_state()
    assert st["state"] == dh.UNKNOWN and st["reason"] == "record_malformed", st

    assert dh.lift("row435 repairing a malformed record") is True
    assert _refusals(capsys) == 0
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert {k: v for k, v in on_disk.items() if k != dh.HOLD_KEY} == _OTHER_KEYS
    assert dh.hold_state()["state"] == dh.CLEAR, "the record stayed unrepairable"


def test_the_cache_is_not_poisoned_by_a_refused_write(store, capsys):
    """A refused write must leave the reader's cache exactly as it found it."""
    gc.set_config({"row435_seed": 1})
    seeded = gc.get_config()
    assert seeded["row435_seed"] == 1 and len(seeded) == _N + 1, seeded
    cached_before, mtime_before = gc._cached, gc._cached_mtime
    assert cached_before is not None

    _corrupt(store)
    assert gc.set_config({"row435_probe": True}) is False
    assert _refusals(capsys) == 1
    assert gc._cached == cached_before, "the refused write rewrote the cache"
    assert gc._cached_mtime == mtime_before
