"""O1698 M9: bd-guard load receipts -- hooks/receipt.ts (session.start row) and bd-plugin-parity.sh --receipts.

The candidates are HARNESS files (O1045/O1066) under harness-work/FIX/o1698-mod-m9-load-receipts;
BD_TEST_O1698_MOD_M9_LOAD_RECEIPTS=1 opts in (unset, every test here skips) and
BD_O1698_MOD_M9_LOAD_RECEIPTS_CANDIDATE may name another candidate dir. The --receipts cases run the
script against a fixture ROLE-OCCUPANCY.tsv, a fake /proc (BD_PROC_ROOT), a fixture receipts dir and a
fixture installed bd-guard, so nothing reads the fleet's state. The chain case runs the hook's own
session.start under `claude plugin test` (no session, no seat: the kit's engine), takes the append argv it
built, runs that argv for real into a temp file, and feeds the row to --receipts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

OPT_IN = os.environ.get("BD_TEST_O1698_MOD_M9_LOAD_RECEIPTS") == "1"
FIX = Path(os.environ.get("BD_O1698_MOD_M9_LOAD_RECEIPTS_CANDIDATE",
                          "/home/mboyle/bd-persist/harness-work/FIX/o1698-mod-m9-load-receipts"))
pytestmark = pytest.mark.skipif(not OPT_IN, reason="candidate opt-in required (BD_TEST_O1698_MOD_M9_LOAD_RECEIPTS=1)")

PARITY = FIX / "bd-plugin-parity.sh"
GUARD = FIX / "bd-guard"
HOST = "hostA"
CLAIMED = "2026-10-03T03:00:42Z"


def _hash(guard: Path) -> str:
    return hashlib.sha256((guard / "hooks/register.tsx").read_bytes() + (guard / "hooks/hooks.json").read_bytes()).hexdigest()


def _guard(tmp_path: Path, name: str = "guard", mutate: bool = False) -> Path:
    g = tmp_path / name
    shutil.copytree(GUARD, g)
    if mutate:
        reg = g / "hooks/register.tsx"
        reg.write_bytes(reg.read_bytes() + b" ")
    return g


def _proc(tmp_path: Path, seats: dict[int, str | None]) -> Path:
    """A fake /proc: seat shell pid -> its one child's comm (None: the pid is dead)."""
    proc = tmp_path / "proc"
    for pid, comm in seats.items():
        if comm is None:
            continue
        child = pid + 1
        (proc / str(pid) / "task" / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "task" / str(pid) / "children").write_text(f"{child} \n")
        (proc / str(child)).mkdir(parents=True)
        (proc / str(child) / "comm").write_text(f"{comm}\n")
    return proc


def _occupancy(tmp_path: Path, rows: list[tuple[str, int, str]]) -> Path:
    occ = tmp_path / "ROLE-OCCUPANCY.tsv"
    occ.write_text("# role\tseat\tpid\thost\tclaimed_at\n" + "".join(f"worker\t{s}\t{p}\t{h}\t{CLAIMED}\n" for s, p, h in rows))
    return occ


def _row(seat: str, sha: str, utc: str = "2026-10-03T03:00:50.123Z", host: str = HOST) -> str:
    return f"{utc}\t{seat}\t{host}\t4242\tbd-guard\t0.1.0\t{sha}\t2.1.288\n"


def _receipts(tmp_path: Path, guard: Path, occ: Path, proc: Path, rdir: Path, vm: Path | None = None,
              host: str = HOST):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update({"BD_ROLE_OCCUPANCY": str(occ), "BD_GUARD_DIR": str(guard), "BD_RECEIPTS_DIR": str(rdir),
                "BD_RECEIPTS_VM": str(vm or tmp_path / "no-vm.tsv"), "BD_PROC_ROOT": str(proc),
                "BD_RECEIPTS_HOST": host, "LC_ALL": "C"})
    return subprocess.run(["bash", str(PARITY), "--receipts"], capture_output=True, text=True, env=env, timeout=60,
                          check=False)


@pytest.fixture
def world(tmp_path):
    rdir = tmp_path / "plugin-receipts"
    rdir.mkdir()
    return tmp_path, _guard(tmp_path), rdir


def test_receipt_with_installed_hash_is_ok(world):
    tmp, guard, rdir = world
    (rdir / "2026-10-03.tsv").write_text(_row("bd-worker-X", _hash(guard)))
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RECEIPT ok bd-worker-X hostA bd-guard 0.1.0" in r.stdout
    assert "RECEIPTS: 1 ok of 1 live Claude seats" in r.stdout


def test_no_receipt_is_missing_could_not_look(world):
    tmp, guard, rdir = world
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert r.returncode == 4
    assert "RECEIPT MISSING bd-worker-X hostA" in r.stdout and "COULD NOT LOOK" in r.stdout


def test_one_byte_changed_in_the_installed_copy_is_hash_mismatch(world):
    tmp, guard, rdir = world
    (rdir / "2026-10-03.tsv").write_text(_row("bd-worker-X", _hash(guard)))
    changed = _guard(tmp, "changed", mutate=True)
    assert _hash(changed) != _hash(guard)
    r = _receipts(tmp, changed, _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert r.returncode == 4
    assert f"RECEIPT HASH-MISMATCH bd-worker-X hostA got {_hash(guard)[:12]} want {_hash(changed)[:12]}" in r.stdout


def test_a_receipt_from_before_the_claim_is_a_previous_occupant(world):
    tmp, guard, rdir = world
    (rdir / "2026-10-02.tsv").write_text(_row("bd-worker-X", _hash(guard), utc="2026-10-02T22:00:00.000Z"))
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert r.returncode == 4 and "RECEIPT MISSING bd-worker-X" in r.stdout


def test_the_newest_row_decides_and_another_hosts_row_does_not_count(world):
    tmp, guard, rdir = world
    good, bad = _hash(guard), "0" * 64
    (rdir / "2026-10-03.tsv").write_text(_row("bd-worker-X", good, utc="2026-10-03T03:01:00.000Z")
                                         + _row("bd-worker-X", bad, utc="2026-10-03T03:02:00.000Z")
                                         + _row("bd-worker-X", good, utc="2026-10-03T03:03:00.000Z", host="hostB"))
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert r.returncode == 4 and "RECEIPT HASH-MISMATCH bd-worker-X" in r.stdout


def test_vm_receipts_file_is_read(world):
    tmp, guard, rdir = world
    vm = tmp / "receipts.tsv"
    vm.write_text(_row("bd-worker-V", _hash(guard)))
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-V", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir, vm)
    assert r.returncode == 0 and "RECEIPT ok bd-worker-V" in r.stdout


def test_denominator_is_live_claude_seats_only(world):
    tmp, guard, rdir = world
    (rdir / "2026-10-03.tsv").write_text(_row("bd-worker-X", _hash(guard)))
    occ = _occupancy(tmp, [("bd-worker-X", 100, HOST), ("bd-cx-worker1", 200, HOST), ("bd-worker-dead", 300, HOST)])
    r = _receipts(tmp, guard, occ, _proc(tmp, {100: "claude", 200: "codex", 300: None}), rdir)
    assert r.returncode == 0, r.stdout
    assert "1 ok of 1 live Claude seats" in r.stdout
    assert "bd-cx-worker1" not in r.stdout and "bd-worker-dead" not in r.stdout


def test_zero_live_claude_seats_is_could_not_look_not_ok(world):
    tmp, guard, rdir = world
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-cx-worker1", 200, HOST)]), _proc(tmp, {200: "codex"}), rdir)
    assert r.returncode == 3 and "0 live Claude seats" in r.stdout


def test_a_seat_on_another_host_is_could_not_look(world):
    tmp, guard, rdir = world
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-R", 100, "hostB")]), _proc(tmp, {}), rdir)
    assert r.returncode == 4 and "RECEIPT COULD-NOT-LOOK bd-worker-R hostB" in r.stdout


def test_no_installed_guard_is_could_not_look(world):
    tmp, _, rdir = world
    r = _receipts(tmp, tmp / "absent", _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert r.returncode == 3 and "no installed" in r.stdout


def test_receipts_mode_never_reaches_the_host_sweep(world):
    """--receipts exits before the inventory's OUT dir and ssh fan-out: a hosts file that would refuse is never read."""
    tmp, guard, rdir = world
    (rdir / "2026-10-03.tsv").write_text(_row("bd-worker-X", _hash(guard)))
    r = _receipts(tmp, guard, _occupancy(tmp, [("bd-worker-X", 100, HOST)]), _proc(tmp, {100: "claude"}), rdir)
    assert "parsed zero hosts" not in r.stdout and "detail:" not in r.stdout and "host\tip" not in r.stdout


# ---- the chain: bd-guard's own session.start -> the argv it builds -> a real append -> --receipts ----

CHAIN_TEST = """import { test, expect } from 'claude-code/testing'
const F: Record<string, string> = __FILES__
const ROOT = __ROOT__
test('chain: session.start builds the append argv', async ($, on) => {
  let argv: readonly string[] = []
  on('fs.read', (_$, e) => {
    const k = Object.keys(F).find(k => e.path === `${ROOT}/${k}`)
    if (!k) throw new Error('ENOENT ' + e.path)
    const b = F[k] ?? ''
    return { value: e.as === 'bytes' ? { base64: b } : atob(b) } as never
  })
  on('fs.exists', () => ({ value: true }))
  on('env.get', (_$, e) => ({ value: e.name === 'BD_SEAT' ? 'bd-worker-CHAIN' : undefined }))
  on('clock.now', () => ({ value: Date.UTC(2026, 9, 3, 3, 30, 0) }))
  on('session.version', () => ({ value: { version: '2.1.288' } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('process.run', (_$, e) => {
    argv = e.argv
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  await $.session.start({ cwd: '/tmp', surface: null, isInteractive: false })
  console.log('M9ARGV=' + JSON.stringify(argv))
  expect(argv[10]).toBe('__WANT__')
})
"""


def _chain_argv(tmp_path: Path, guard: Path, want: str):
    """Run the copy's session.start under `claude plugin test`; its fs.read answers the copy's own bytes."""
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH (the plugin test kit runs under it)")
    for t in (guard / "hooks").glob("*.test.ts"):
        t.unlink()
    files = {k: base64.b64encode((guard / k).read_bytes()).decode()
             for k in ("hooks/register.tsx", "hooks/hooks.json", ".claude-plugin/plugin.json")}
    (guard / "hooks/chain.test.ts").write_text(CHAIN_TEST.replace("__FILES__", json.dumps(files)).replace("__ROOT__", json.dumps(str(guard))).replace("__WANT__", want))
    r = subprocess.run(["claude", "plugin", "test", str(guard)], capture_output=True, text=True, timeout=180,
                       cwd=tmp_path, check=False)
    line = next((ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("M9ARGV=")), "")
    return r, (json.loads(line[len("M9ARGV="):]) if line else [])


def test_chain_hook_hash_equals_sha256sum_and_its_real_append_reads_ok(tmp_path):
    guard = _guard(tmp_path)
    want = subprocess.run(f"cat '{guard}/hooks/register.tsx' '{guard}/hooks/hooks.json' | sha256sum", shell=True,
                          capture_output=True, text=True, check=True).stdout.split()[0]
    r, argv = _chain_argv(tmp_path, guard, want)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert argv[:2] == ["/bin/sh", "-c"] and argv[5].endswith("/plugin-receipts/2026-10-03.tsv"), argv
    # the same argv, run for real into temp: dir/file redirected, every other byte the hook's
    rdir = tmp_path / "plugin-receipts"
    real = list(argv)
    real[4], real[5] = str(rdir), str(rdir / "2026-10-03.tsv")
    t0 = time.monotonic()
    subprocess.run(real, check=True, timeout=5)
    assert time.monotonic() - t0 < 1.0
    row = (rdir / "2026-10-03.tsv").read_text()
    fields = row.rstrip("\n").split("\t")
    assert row.count("\n") == 1 and len(fields) == 8, row
    assert fields[1] == "bd-worker-CHAIN" and fields[2] == os.uname().nodename and fields[3] == str(os.getpid())
    assert fields[6] == want == _hash(guard)
    # --receipts on that row: ok against this copy, HASH-MISMATCH against a one-byte-changed install
    occ, proc = _occupancy(tmp_path, [("bd-worker-CHAIN", 100, fields[2])]), _proc(tmp_path, {100: "claude"})
    ok = _receipts(tmp_path, guard, occ, proc, rdir, host=fields[2])
    assert ok.returncode == 0 and "RECEIPT ok bd-worker-CHAIN" in ok.stdout, ok.stdout
    changed = _guard(tmp_path, "changed", mutate=True)
    bad = _receipts(tmp_path, changed, occ, proc, rdir, host=fields[2])
    assert bad.returncode == 4 and "RECEIPT HASH-MISMATCH bd-worker-CHAIN" in bad.stdout, bad.stdout


def test_chain_probe_can_say_no(tmp_path):
    """Positive control for the chain: the same generated test fails when the expected hash is wrong."""
    guard = _guard(tmp_path)
    r, argv = _chain_argv(tmp_path, guard, "0" * 64)
    assert r.returncode != 0 and argv and argv[10] == _hash(guard)

