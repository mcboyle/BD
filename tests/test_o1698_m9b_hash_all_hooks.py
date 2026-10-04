"""O1698 M9b: the bd-guard load-receipt hash covers every hook module, and MISSING names the receipt files it read.

The candidates are HARNESS files (O1045/O1066) under harness-work/FIX/o1698-m9b-hash-all-hooks:
bd-plugin-parity.sh and a bd-guard/ plugin tree (live plugins/bd-guard with the candidate receipt.ts).
BD_TEST_O1698_M9B_HASH_ALL_HOOKS=1 opts in (unset, every test here skips); BD_O1698_M9B_HASH_ALL_HOOKS_CANDIDATE
may name another dir of the same shape. The recipe both sides must share:
  in hooks/, every regular file named *.ts *.tsx *.mts *.cts *.js *.jsx *.mjs *.cjs or hooks.json, minus *.test.*,
  sorted bytewise; hash = sha256 of their `sha256sum` lines (`<hex>  <name>`, one per file, newline-terminated).
_recipe() below is that recipe in Python, a third implementation the other two are held to.
The chain cases run bd-guard's own session.start under `claude plugin test` (the kit's engine, no seat), take the
append argv it builds, run that argv for real into a temp dir, then judge the row with --receipts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

OPT_IN = os.environ.get("BD_TEST_O1698_M9B_HASH_ALL_HOOKS") == "1"
FIX = Path(os.environ.get("BD_O1698_M9B_HASH_ALL_HOOKS_CANDIDATE",
                          "/home/mboyle/bd-persist/harness-work/FIX/o1698-m9b-hash-all-hooks"))
pytestmark = pytest.mark.skipif(not OPT_IN, reason="candidate opt-in required (BD_TEST_O1698_M9B_HASH_ALL_HOOKS=1)")

PARITY = FIX / "bd-plugin-parity.sh"
GUARD = FIX / "bd-guard"
CLAIMED = "2026-10-03T03:00:42Z"
MODULE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def _recipe(guard: Path) -> str:
    hooks = guard / "hooks"
    names = sorted((p.name for p in hooks.iterdir()
                    if p.is_file() and ".test." not in p.name
                    and (p.name.endswith(MODULE_SUFFIXES) or p.name == "hooks.json")), key=os.fsencode)
    listing = "".join(f"{hashlib.sha256((hooks / n).read_bytes()).hexdigest()}  {n}\n" for n in names)
    return hashlib.sha256(listing.encode()).hexdigest()


def _copy(tmp_path: Path, name: str, change: str | None = None) -> Path:
    g = tmp_path / name
    shutil.copytree(GUARD, g)
    if change:
        f = g / "hooks" / change
        f.write_bytes(f.read_bytes() + b" ")
    return g


def _fixture(tmp_path: Path, seat: str, host: str):
    occ = tmp_path / "ROLE-OCCUPANCY.tsv"
    occ.write_text(f"# role\tseat\tpid\thost\tclaimed_at\nworker\t{seat}\t100\t{host}\t{CLAIMED}\n")
    proc = tmp_path / "proc"
    (proc / "100/task/100").mkdir(parents=True, exist_ok=True)
    (proc / "100/task/100/children").write_text("101 \n")
    (proc / "101").mkdir(exist_ok=True)
    (proc / "101/comm").write_text("claude\n")
    return occ, proc


def _receipts(tmp_path: Path, guard: Path, occ: Path, proc: Path, rdir: Path, host: str, vm: Path | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update({"BD_ROLE_OCCUPANCY": str(occ), "BD_GUARD_DIR": str(guard), "BD_RECEIPTS_DIR": str(rdir),
                "BD_RECEIPTS_VM": str(vm or tmp_path / "no-vm.tsv"), "BD_PROC_ROOT": str(proc),
                "BD_RECEIPTS_HOST": host, "LC_ALL": "C"})
    return subprocess.run(["bash", str(PARITY), "--receipts"], capture_output=True, text=True, env=env, timeout=60,
                          check=False)


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
  on('clock.now', () => ({ value: Date.UTC(2026, 9, 3, 4, 30, 0) }))
  on('session.version', () => ({ value: { version: '2.1.288' } }))
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('process.run', (_$, e) => {
    argv = e.argv
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  await $.session.start({ cwd: '/tmp', surface: null, isInteractive: false })
  console.log('M9ARGV=' + JSON.stringify(argv))
})
"""


def _kit(tmp_path: Path, guard: Path, keep: tuple[str, ...] = ()):
    """`claude plugin test` on a copy of `guard` with the chain probe added; test files other than `keep` removed.
    Returns the run dir, the kit's result and the append argv bd-guard's session.start built ([] if none)."""
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not on PATH (the plugin test kit runs under it)")
    run = tmp_path / f"run-{guard.name}"
    shutil.copytree(guard, run)
    for t in (run / "hooks").glob("*.test.ts"):
        if t.name not in keep:
            t.unlink()
    files = {k: base64.b64encode((run / k).read_bytes()).decode()
             for k in ("hooks/register.tsx", "hooks/hooks.json", ".claude-plugin/plugin.json")}
    (run / "hooks/chain.test.ts").write_text(CHAIN_TEST.replace("__FILES__", json.dumps(files))
                                             .replace("__ROOT__", json.dumps(str(run))))
    r = subprocess.run(["claude", "plugin", "test", str(run)], capture_output=True, text=True, timeout=180,
                       cwd=tmp_path, check=False)
    line = next((ln for ln in (r.stdout + r.stderr).splitlines() if ln.startswith("M9ARGV=")), "")
    (run / "hooks/chain.test.ts").unlink()        # the probe file is not part of the tree that loaded
    return run, r, (json.loads(line[len("M9ARGV="):]) if line else None)


def _chain_row(tmp_path: Path, guard: Path) -> list[str]:
    """bd-guard's own session.start (copy `guard`) -> its append argv -> run for real into tmp -> the row's fields."""
    _run, r, argv = _kit(tmp_path, guard)
    assert r.returncode == 0 and argv and len(argv) > 5, r.stdout[-1500:] + r.stderr[-1500:]
    rdir = tmp_path / "plugin-receipts"
    real = list(argv)
    real[4], real[5] = str(rdir), str(rdir / "2026-10-03.tsv")
    subprocess.run(real, check=True, timeout=10)
    rows = (rdir / "2026-10-03.tsv").read_text().splitlines()
    fields = rows[-1].split("\t")
    assert len(fields) == 8, rows
    return fields


@pytest.fixture
def chain(tmp_path):
    """One receipt row written by an unchanged copy of the candidate tree, plus its fixture world."""
    loaded = _copy(tmp_path, "loaded")
    fields = _chain_row(tmp_path, loaded)
    occ, proc = _fixture(tmp_path, fields[1], fields[2])
    return tmp_path, fields, occ, proc, tmp_path / "plugin-receipts"


def test_unchanged_tree_is_ok_and_both_sides_hash_alike(chain):
    """Positive control: receipt hash == --receipts' installed hash == the documented recipe."""
    tmp, fields, occ, proc, rdir = chain
    installed = _copy(tmp, "installed")
    assert fields[6] == _recipe(installed)
    r = _receipts(tmp, installed, occ, proc, rdir, fields[2])
    assert r.returncode == 0, r.stdout
    assert f"RECEIPT ok bd-worker-CHAIN {fields[2]} bd-guard" in r.stdout and fields[6][:12] in r.stdout


@pytest.mark.parametrize("module", ["stateline.ts", "receipt.ts", "register.tsx", "hooks.json"])
def test_one_byte_changed_in_any_loaded_module_is_hash_mismatch(chain, module):
    tmp, fields, occ, proc, rdir = chain
    installed = _copy(tmp, f"installed-{module}", change=module)
    r = _receipts(tmp, installed, occ, proc, rdir, fields[2])
    assert r.returncode == 4 and "RECEIPT HASH-MISMATCH bd-worker-CHAIN" in r.stdout, r.stdout


def test_a_changed_test_file_or_backup_is_not_a_loaded_module(chain):
    """Recipe choice: *.test.* and non-module names (.pre-* backups) are outside the hash -- engines load neither."""
    tmp, fields, occ, proc, rdir = chain
    installed = _copy(tmp, "installed-tests", change="receipt.test.ts")
    (installed / "hooks/register.tsx.pre-20261003T000000Z").write_text("old\n")
    r = _receipts(tmp, installed, occ, proc, rdir, fields[2])
    assert r.returncode == 0 and "RECEIPT ok bd-worker-CHAIN" in r.stdout, r.stdout


def test_a_new_module_file_is_in_the_hash(chain):
    tmp, fields, occ, proc, rdir = chain
    installed = _copy(tmp, "installed-new")
    (installed / "hooks/extra.ts").write_text("export const x = 1\n")
    r = _receipts(tmp, installed, occ, proc, rdir, fields[2])
    assert r.returncode == 4 and "RECEIPT HASH-MISMATCH" in r.stdout


def test_parity_installed_hash_equals_the_recipe_on_the_shared_fixture(tmp_path):
    """No claude CLI needed: a python-written row with _recipe()'s hash reads ok, so parity computes the same recipe."""
    installed = _copy(tmp_path, "installed")
    rdir = tmp_path / "plugin-receipts"
    rdir.mkdir()
    (rdir / "2026-10-03.tsv").write_text(
        f"2026-10-03T03:30:00.000Z\tbd-worker-X\thostA\t4242\tbd-guard\t0.1.0\t{_recipe(installed)}\t2.1.288\n")
    occ, proc = _fixture(tmp_path, "bd-worker-X", "hostA")
    r = _receipts(tmp_path, installed, occ, proc, rdir, "hostA")
    assert r.returncode == 0 and "RECEIPT ok bd-worker-X" in r.stdout, r.stdout


def test_missing_names_the_unreadable_receipt_file(tmp_path):
    installed = _copy(tmp_path, "installed")
    rdir = tmp_path / "plugin-receipts"
    rdir.mkdir()
    day = rdir / "2026-10-03.tsv"
    day.write_text("x\n")
    day.chmod(0)
    try:
        if os.access(day, os.R_OK):
            pytest.skip("running as a user who can read mode-000 files")
        occ, proc = _fixture(tmp_path, "bd-worker-X", "hostA")
        r = _receipts(tmp_path, installed, occ, proc, rdir, "hostA")
    finally:
        day.chmod(0o644)
    assert r.returncode == 4 and "RECEIPT MISSING bd-worker-X" in r.stdout
    assert f"UNREADABLE {day}" in r.stdout, r.stdout


def test_missing_names_where_it_looked(tmp_path):
    installed = _copy(tmp_path, "installed")
    rdir = tmp_path / "plugin-receipts"
    rdir.mkdir()
    (rdir / "2026-10-02.tsv").write_text("")
    occ, proc = _fixture(tmp_path, "bd-worker-X", "hostA")
    vm = tmp_path / "vm-receipts.tsv"
    r = _receipts(tmp_path, installed, occ, proc, rdir, "hostA", vm)
    assert r.returncode == 4
    assert str(rdir / "2026-10-02.tsv") in r.stdout and f"{vm} absent" in r.stdout, r.stdout


URL_READ = "const moduleUrl = (): unknown => (import.meta as unknown as { url?: unknown }).url"


@pytest.mark.parametrize("url_set", [True, False])
def test_an_unset_module_url_fails_the_receipt_never_the_guard(tmp_path, url_set):
    """Lens C4 r1: with import.meta.url unset the module still loads, every guard test passes, and no row is built.
    url_set=True is the positive control: the same probe sees the append when the URL is there."""
    guard = _copy(tmp_path, "url-set" if url_set else "url-unset")
    rec = guard / "hooks/receipt.ts"
    assert rec.read_text().count(URL_READ) == 1
    if not url_set:
        rec.write_text(rec.read_text().replace(URL_READ, "const moduleUrl = (): unknown => undefined"))
    keep = tuple(t.name for t in (guard / "hooks").glob("*.test.ts") if t.name != "receipt.test.ts")
    assert "guard.test.ts" in keep
    _run, r, argv = _kit(tmp_path, guard, keep)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "did not load" not in out and " 0 fail" in out
    assert "(pass) Bash: a tripwire refusal (exit 2) denies" in out      # a guard hook still answers
    assert argv is not None
    assert (len(argv) > 5) is url_set, argv

