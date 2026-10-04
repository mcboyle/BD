"""O1698 C6 (o1698-c6-hub-ram-measure): bd-hot-census.sh, a READ-ONLY page-cache census of the fleet's hot dirs.

The script is a harness candidate (O1045): BD_O1698_C6_HUB_RAM_MEASURE_CANDIDATE names it by absolute path; unset,
every test skips. Every vmtouch -e below targets a mktemp fixture file only; the script under test never passes a flag.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_C6_HUB_RAM_MEASURE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

NOW = 1759460400  # 2025-10-03T03:00:00Z
HEADER = "utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n"


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_HOT_")}
    env.update({"LC_ALL": "C", "BD_HUB_SAMPLES_DIR": str(tmp_path / "samples"), **extra})
    return env


def _run(env, *args):
    return subprocess.run(["bash", CANDIDATE, *args], env=env, capture_output=True, text=True, timeout=120, check=False)


def _rows(out):
    return [line.split("\t") for line in out.splitlines() if line.strip()]


def _hot_root(tmp_path, files):
    root = tmp_path / "persist"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _samples(tmp_path, rows):
    d = tmp_path / "samples"
    d.mkdir(exist_ok=True)
    (d / "2025-10-03.tsv").write_text(HEADER + "".join("\t".join(r) + "\n" for r in rows))


def _vmtouch():
    v = shutil.which("vmtouch")
    if not v:
        pytest.skip("vmtouch not installed on this host (the hub has /usr/bin/vmtouch)")
    return v


def test_positive_control_evicted_fixture_reads_0_then_100_after_cat(tmp_path):
    vmt = _vmtouch()
    root = _hot_root(tmp_path, {"hot/a.bin": "x" * (64 * 4096)})
    f = root / "hot" / "a.bin"
    fd = os.open(f, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)
    fstype = subprocess.run(["stat", "-f", "-c", "%T", str(f)], capture_output=True, text=True, check=False).stdout
    if "tmpfs" in fstype:
        pytest.skip("fixture on tmpfs: its pages cannot be evicted")
    subprocess.run([vmt, "-e", str(f)], check=True, capture_output=True)   # FIXTURE ONLY
    env = _env(tmp_path, BD_HOT_ROOT=str(root), BD_HOT_DIRS="hot")
    before = _rows(_run(env).stdout)
    assert before[0][1:5] == ["hot", "64", "0", "0.0"], before
    f.read_bytes()   # the cat
    after = _rows(_run(env).stdout)
    assert after[0][1:5] == ["hot", "64", "64", "100.0"], after


def test_vmtouch_absent_is_could_not_look_rc_2(tmp_path):
    r = _run(_env(tmp_path, BD_VMTOUCH=str(tmp_path / "no-vmtouch-here")))
    assert r.returncode == 2
    assert r.stdout.startswith("COULD-NOT-LOOK\tvmtouch not found"), r.stdout


def test_guard_vmtouch_is_never_given_a_flag(tmp_path):
    stub = tmp_path / "vmtouch"
    calls = tmp_path / "calls"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >> {calls}\n'
                    'echo "  Resident Pages: 3/4  12K/16K  75%"\n')
    stub.chmod(0o755)
    root = _hot_root(tmp_path, {"board/x.md": "x", "state/y.tsv": "y"})
    r = _run(_env(tmp_path, BD_HOT_ROOT=str(root), BD_HOT_DIRS="board state", BD_VMTOUCH=str(stub)))
    assert r.returncode == 0, r.stderr
    args = calls.read_text().split()
    assert args, "stub never called: the guard would be vacuous"
    assert not [a for a in args if a.startswith("-")], args
    assert all(a.startswith(str(root)) for a in args), args
    with open(CANDIDATE) as fh:
        src = fh.read()
    for flag in (" -l", " -L", " -t", " -e", " -d", " -dl"):
        assert f'"$VMT"{flag}' not in src and f"vmtouch{flag}" not in src, flag


def test_rows_join_the_nearest_hub_sample(tmp_path):
    _samples(tmp_path, [["2025-10-03T02:59:40Z", "5.0", "100", "3", "7.5", "20.0"],
                        ["2025-10-03T02:59:55Z", "6.0", "100", "9", "33.3", "20.0"],
                        ["2025-10-03T03:00:20Z", "7.0", "100", "1", "1.0", "20.0"]])
    stub = tmp_path / "vmtouch"
    stub.write_text('#!/bin/bash\necho "  Resident Pages: 2/4  8K/16K  50%"\n')
    stub.chmod(0o755)
    root = _hot_root(tmp_path, {"queues/q.tsv": "q"})
    r = _run(_env(tmp_path, BD_HOT_ROOT=str(root), BD_HOT_DIRS="queues missing", BD_VMTOUCH=str(stub),
                  BD_HOT_NOW=str(NOW)))
    rows = _rows(r.stdout)
    assert rows[0] == ["2025-10-03T03:00:00Z", "queues", "4", "2", "50.0", "4", "50.0", "33.3", "9",
                       "2025-10-03T02:59:55Z"], rows
    assert rows[1][1:7] == ["missing", "NA", "NA", "NA", "NA", "NA"], rows


def test_recent_columns_count_only_recently_modified_files(tmp_path):
    vmt = _vmtouch()
    root = _hot_root(tmp_path, {"state/old.tsv": "o" * 8192, "state/new.tsv": "n" * 4096})
    old = root / "state" / "old.tsv"
    t = time.time() - 3 * 3600
    os.utime(old, (t, t))
    rows = _rows(_run(_env(tmp_path, BD_HOT_ROOT=str(root), BD_HOT_DIRS="state", BD_VMTOUCH=vmt)).stdout)
    assert rows[0][2] == "3" and rows[0][5] == "1", rows   # 3 pages in the dir, 1 of them in the recent file


def _census(tmp_path, pcts, utc="2025-10-03T03:00:00Z"):
    p = tmp_path / "census.tsv"
    with p.open("a") as fh:
        for d, pct in pcts:
            fh.write(f"{utc}\t{d}\t10\t{int(float(pct) / 10)}\t{pct}\t5\t{pct}\t1.0\t0\t{utc}\n")
    return p


def _day(iowait_tail):
    rows = [[f"2025-10-03T03:00:{i:02d}Z", "1.0", "100", "0", "1.0", "20.0"] for i in range(50)]
    rows += [[f"2025-10-03T03:01:{i:02d}Z", "1.0", "100", "0", iowait_tail, "20.0"] for i in range(50)]
    return rows


def test_report_eviction_seen_with_iowait_p99_over_20_recommends_pin(tmp_path):
    _samples(tmp_path, _day("50.0"))
    _census(tmp_path, [("state", "100.0"), ("queues", "100.0")])
    c = _census(tmp_path, [("state", "60.0"), ("queues", "100.0")], utc="2025-10-03T03:01:59Z")
    r = _run(_env(tmp_path), "report", str(c))
    assert r.returncode == 0, r.stderr
    assert "VERDICT: EVICTION SEEN (state)" in r.stdout
    assert "iowait_pct p99=50.0" in r.stdout
    assert "RECOMMEND PIN (-l/-t): yes" in r.stdout


def test_report_eviction_seen_but_calm_iowait_does_not_recommend_pin(tmp_path):
    _samples(tmp_path, _day("2.0"))
    _census(tmp_path, [("state", "100.0")])
    c = _census(tmp_path, [("state", "90.0")], utc="2025-10-03T03:01:59Z")
    r = _run(_env(tmp_path), "report", str(c))
    assert "VERDICT: EVICTION SEEN (state)" in r.stdout and "RECOMMEND PIN (-l/-t): no" in r.stdout, r.stdout


def test_report_all_resident_is_not_seen(tmp_path):
    _samples(tmp_path, _day("50.0"))
    _census(tmp_path, [("state", "100.0")])
    c = _census(tmp_path, [("state", "100.0")], utc="2025-10-03T03:01:59Z")
    r = _run(_env(tmp_path), "report", str(c))
    assert "VERDICT: EVICTION NOT SEEN" in r.stdout and "RECOMMEND PIN (-l/-t): no" in r.stdout, r.stdout


def test_report_without_hub_samples_is_could_not_look(tmp_path):
    c = _census(tmp_path, [("state", "100.0")])
    r = _run(_env(tmp_path), "report", str(c))
    assert r.returncode == 2 and "COULD-NOT-LOOK" in r.stderr
