"""O1789 bughunt backup-replication F6: gold-template "restore latest" must restore the NEWEST generation.

``template_backup._new_ts`` named a generation ``<second>-<monotonic_ns & 0xFFFFFF as hex>`` and
``list_backups`` / ``latest_backup`` sorted the names. The suffix wraps every 16.7 ms, so of two
backups in one wall-clock second the later one could sort first, and ``restore_template(host)``
(what ``lifecycle_drift`` calls to undo a bad write) then restored the OLDER generation.

The order is now the creation order recorded in each manifest (per-host ``seq``), not the name.
Every test works in ``tmp_path`` with an explicit ``reviewed_dir``; no app, DB or BD_HOME is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from bulk_downloader import template_backup as tb

BD_GATE_SCOPE = "module"
HOST = "example.com"
SUFFIX = ".template.json"


def _write_live(reviewed: Path, n: int) -> bytes:
    reviewed.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"host": HOST, "version": "3.66.1842", "n": n}, indent=2).encode()
    (reviewed / f"{HOST}{SUFFIX}").write_bytes(raw)
    return raw


def _backup(reviewed: Path, n: int) -> tuple[str, bytes]:
    raw = _write_live(reviewed, n)
    bk = tb.backup_template(HOST, reviewed_dir=reviewed, reason=f"gen{n}")
    assert bk.get("ok") and bk.get("backed_up"), f"BRF6-BACKUP-FAILED: gen{n}: {bk}"
    return bk["ts"], raw


def _freeze_second(monkeypatch, seconds: list[str]) -> None:
    it = iter(seconds)
    last = [seconds[-1]]

    def fake_strftime(_fmt, *_a):
        last[0] = next(it, last[0])
        return last[0]

    monkeypatch.setattr(tb.time, "strftime", fake_strftime)


def _wrapping_monotonic(monkeypatch, values: list[int]) -> None:
    it = iter(values)
    monkeypatch.setattr(tb.time, "monotonic_ns", lambda: next(it, values[-1]))


def test_latest_is_the_newer_generation_when_the_old_suffix_wraps(tmp_path: Path, monkeypatch) -> None:
    """The finding's repro: two backups in one second, the monotonic suffix wraps between them."""
    reviewed = tmp_path / "templates" / "reviewed"
    _freeze_second(monkeypatch, ["20261013-120000"])
    _wrapping_monotonic(monkeypatch, [0xFFFFF0, 0x1000010])  # & 0xFFFFFF: fffff0 then 000010
    ts1, raw1 = _backup(reviewed, 1)
    ts2, raw2 = _backup(reviewed, 2)
    assert ts1 != ts2
    gens = tb.list_backups(HOST, reviewed_dir=reviewed)
    assert gens == [ts1, ts2], f"BRF6-ORDER: list_backups is not oldest-first by creation: {gens}, created {[ts1, ts2]}"
    latest = tb.latest_backup(HOST, reviewed_dir=reviewed)
    assert latest == ts2, f"BRF6-LATEST-IS-OLDER: latest_backup={latest}, newest generation is {ts2}"
    _write_live(reviewed, 99)  # the bad write that restore-latest must undo
    rb = tb.restore_template(HOST, reviewed_dir=reviewed)
    assert rb.get("ok") and rb.get("ts") == ts2, f"BRF6-RESTORED-OLDER: restored {rb.get('ts')}, expected {ts2}"
    live = (reviewed / f"{HOST}{SUFFIX}").read_bytes()
    assert live == raw2 and live != raw1, "BRF6-RESTORED-OLDER: live bytes are not the newest generation's"


def test_sixty_generations_in_one_second_keep_creation_order(tmp_path: Path, monkeypatch) -> None:
    reviewed = tmp_path / "templates" / "reviewed"
    _freeze_second(monkeypatch, ["20261013-120000"])
    _wrapping_monotonic(monkeypatch, [(i * 0x4FFFFF) for i in range(1, 400)])  # wraps the 24-bit mask many times
    made = [_backup(reviewed, n)[0] for n in range(1, 61)]
    assert len(set(made)) == 60, f"BRF6-NAME-COLLISION: {60 - len(set(made))} generation names repeat"
    gens = tb.list_backups(HOST, reviewed_dir=reviewed)
    assert gens == made, f"BRF6-ORDER: first difference at index {next(i for i, (a, b) in enumerate(zip(gens, made)) if a != b)}"
    seqs = [tb.read_manifest(HOST, ts, reviewed_dir=reviewed)["seq"] for ts in made]
    assert seqs == list(range(1, 61)), f"BRF6-SEQ: {seqs[:8]}"


def test_latest_survives_a_wall_clock_step_back(tmp_path: Path, monkeypatch) -> None:
    """The order must not be the wall clock either: the second backup is named and stamped EARLIER."""
    reviewed = tmp_path / "templates" / "reviewed"
    _freeze_second(monkeypatch, ["20261013-120500", "20261013-115900"])
    clock = iter([1_800_000_300.0, 1_800_000_000.0])
    clock_ns = iter([1_800_000_300_000_000_000, 1_800_000_000_000_000_000])
    monkeypatch.setattr(tb.time, "time", lambda: next(clock, 1_800_000_000.0))
    monkeypatch.setattr(tb.time, "time_ns", lambda: next(clock_ns, 1_800_000_000_000_000_000))
    ts1, _ = _backup(reviewed, 1)
    ts2, raw2 = _backup(reviewed, 2)
    assert ts2 < ts1, "fixture: the newer generation must have the lexically smaller name"
    assert tb.latest_backup(HOST, reviewed_dir=reviewed) == ts2, (
        f"BRF6-CLOCK-STEP-BACK: latest is {tb.latest_backup(HOST, reviewed_dir=reviewed)}, newest generation is {ts2}")
    _write_live(reviewed, 99)
    assert tb.restore_template(HOST, reviewed_dir=reviewed).get("ts") == ts2
    assert (reviewed / f"{HOST}{SUFFIX}").read_bytes() == raw2


def _legacy_generation(reviewed: Path, ts: str, created: int, n: int) -> None:
    """A generation as the old code wrote it: hex-suffixed name, manifest without seq / created_ns."""
    gen = reviewed.parent / ".gold_backups" / HOST / ts
    gen.mkdir(parents=True)
    raw = json.dumps({"host": HOST, "n": n}).encode()
    (gen / f"{HOST}{SUFFIX}").write_bytes(raw)
    (gen / "manifest.json").write_text(json.dumps({"host": HOST, "ts": ts, "sha256": tb._sha256_bytes(raw), "created": created}), "utf-8")


def test_existing_generations_sort_first_and_a_new_backup_is_latest(tmp_path: Path, monkeypatch) -> None:
    reviewed = tmp_path / "templates" / "reviewed"
    _legacy_generation(reviewed, "20261013-120000-fffff0", 1_800_000_000, 1)
    _legacy_generation(reviewed, "20261013-120009-000010", 1_800_000_001, 2)  # created disagrees with the name
    _legacy_generation(reviewed, "20261013-120003-0a0000", 1_800_000_003, 3)
    assert tb.list_backups(HOST, reviewed_dir=reviewed) == [
        "20261013-120000-fffff0", "20261013-120009-000010", "20261013-120003-0a0000"], (
        f"BRF6-LEGACY-ORDER: {tb.list_backups(HOST, reviewed_dir=reviewed)}")
    _freeze_second(monkeypatch, ["20260101-000000"])  # a name that sorts before every legacy one
    ts_new, raw_new = _backup(reviewed, 4)
    assert tb.latest_backup(HOST, reviewed_dir=reviewed) == ts_new, (
        f"BRF6-NEW-NOT-LATEST: latest is {tb.latest_backup(HOST, reviewed_dir=reviewed)} after a new backup {ts_new}")
    assert tb.read_manifest(HOST, ts_new, reviewed_dir=reviewed)["seq"] == 1
    _write_live(reviewed, 99)
    assert tb.restore_template(HOST, reviewed_dir=reviewed).get("ts") == ts_new
    assert (reviewed / f"{HOST}{SUFFIX}").read_bytes() == raw_new


def test_a_taken_name_gets_the_next_sequence_not_a_failed_backup(tmp_path: Path, monkeypatch) -> None:
    """A dir left without a manifest (crashed backup) holds the name the next backup would take."""
    reviewed = tmp_path / "templates" / "reviewed"
    _freeze_second(monkeypatch, ["20261013-120000"])
    ts1, _ = _backup(reviewed, 1)
    orphan = reviewed.parent / ".gold_backups" / HOST / "20261013-120000-000002"
    orphan.mkdir()
    raw2 = _write_live(reviewed, 2)
    bk = tb.backup_template(HOST, reviewed_dir=reviewed, reason="after-orphan")
    assert bk.get("ok") and bk.get("backed_up"), f"BRF6-TAKEN-NAME-FAILED: {bk}"
    assert bk["ts"] not in (ts1, orphan.name) and not any(orphan.iterdir()), f"BRF6-TAKEN-NAME-REUSED: {bk['ts']}"
    assert tb.list_backups(HOST, reviewed_dir=reviewed) == [ts1, bk["ts"]]
    _write_live(reviewed, 99)
    assert tb.restore_template(HOST, reviewed_dir=reviewed).get("ts") == bk["ts"]
    assert (reviewed / f"{HOST}{SUFFIX}").read_bytes() == raw2


def test_an_unreadable_manifest_never_becomes_latest_and_explicit_ts_still_works(tmp_path: Path, monkeypatch) -> None:
    """Controls: a corrupt generation does not break listing or outrank a good one; restore by ts is unchanged."""
    reviewed = tmp_path / "templates" / "reviewed"
    _freeze_second(monkeypatch, ["20261013-120000"])
    ts1, raw1 = _backup(reviewed, 1)
    ts2, _ = _backup(reviewed, 2)
    bad = reviewed.parent / ".gold_backups" / HOST / "20261013-235959-999999"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json", "utf-8")
    gens = tb.list_backups(HOST, reviewed_dir=reviewed)
    assert sorted(gens) == sorted([ts1, ts2, bad.name]) and gens[-1] == ts2, f"BRF6-CORRUPT-IS-LATEST: {gens}"
    _write_live(reviewed, 99)
    rb = tb.restore_template(HOST, ts=ts1, reviewed_dir=reviewed)
    assert rb.get("ok") and rb.get("ts") == ts1 and (reviewed / f"{HOST}{SUFFIX}").read_bytes() == raw1
    assert tb.restore_template(HOST, ts="20200101-000000-000000", reviewed_dir=reviewed).get("ok") is False
