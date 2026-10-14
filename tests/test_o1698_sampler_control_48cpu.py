"""O1698 sampler control, calibrated for the 48-CPU hub (row o1698-sampler-control-48cpu).

The PM's live control (4 x `yes` for 30 s, judged on load1 rising > 1.0 within 60 s) returned rc=1 on the hub:
load1 is a 1-minute exponential average, 4 spinners add only ~0.39*4 to it in 30 s, and a falling baseline
(12 -> 6.59) swallows that. The candidate judges the RUNNABLE COUNT recovered from consecutive 10 s load1 rows
(n = (L2 - L1*d)/(1 - d), d = e^(-dt/60)) and spins nproc/4 for 30 s.

BD_O1698_SAMPLER_CONTROL_CANDIDATE is the absolute path of the candidate bd-hub-spike-detect.py;
positive-control-live.sh sits beside it. Fixtures replay REAL hub rows (bd-hub-sampler service, 2026-10-03) from
a 12-spinner run and a no-spinner run, plus a simulated kernel load average with a falling baseline.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_SAMPLER_CONTROL_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

HEADER = "utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n"

# Live hub, 48 CPUs: SPINNERS=12 SPIN_S=30 started 02:20:38Z (epoch 1790994038).
HUB_SPIN = """02:19:27 5.96 02:19:37 6.23 02:19:47 5.64 02:19:57 5.24 02:20:07 7.15 02:20:17 7.03 02:20:28 6.56
02:20:38 6.08 02:20:48 7.60 02:20:58 8.89 02:21:08 11.64 02:21:18 10.54 02:21:28 9.77 02:21:38 8.88"""
HUB_SPIN_START = 1790994038
# Same hub, BD_CONTROL_NO_SPIN=1 started 02:22:54Z (epoch 1790994174): the negative control.
HUB_QUIET = """02:21:48 8.60 02:21:58 8.46 02:22:08 7.94 02:22:19 7.80 02:22:29 7.29 02:22:39 6.78 02:22:49 6.28
02:22:59 5.92 02:23:09 5.48 02:23:19 5.34 02:23:29 5.44 02:23:39 5.67 02:23:49 5.49 02:23:59 5.41"""
HUB_QUIET_START = 1790994174
# Same hub, BD_CONTROL_NO_SPIN=1 started 02:35:30Z (epoch 1790994930): other work put ONE 10 s interval at +6.9
# runnable over baseline. Judged on the peak this was a false PASS; a 30 s burst spans three intervals.
HUB_BLIP = """02:34:15 8.04 02:34:25 6.88 02:34:35 6.29 02:34:45 5.32 02:34:55 4.80 02:35:06 5.63 02:35:16 5.68
02:35:26 5.04 02:35:36 4.49 02:35:46 3.95 02:35:56 3.65 02:36:06 4.45 02:36:16 4.29 02:36:26 3.86"""
HUB_BLIP_START = 1790994930


def _detector() -> Path:
    cand = Path(CANDIDATE)
    assert cand.is_file() and os.access(cand, os.X_OK), (
        f"BD_O1698_SAMPLER_CONTROL_CANDIDATE supplied but not an executable file: {cand}")
    return cand


def _hub_tsv(path: Path, pairs: str) -> Path:
    f = pairs.split()
    lines = [f"2026-10-03T{t}Z\t{l}\t2200\t0\t0.0\t26.0\n" for t, l in zip(f[::2], f[1::2])]
    path.write_text(HEADER + "".join(lines))
    return path


def _detect(tsv: Path, start: float, threshold: float = 6.0, spin: float = 30) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(_detector()), str(tsv), "--start", str(start), "--spin", str(spin),
                        "--threshold", str(threshold)], capture_output=True, text=True, timeout=60, check=False)
    return r.returncode, r.stdout + r.stderr


def _kernel_loadavg(t0: datetime, seconds: int, background, spinners: int, spin_from: int, spin_s: int,
                    l0: float) -> list[str]:
    """Rows every 10 s from the kernel's own recurrence: every 5 s, L = L*e^(-5/60) + n*(1 - e^(-5/60))."""
    d5 = math.exp(-5 / 60)
    load, rows = l0, []
    for s in range(0, seconds + 1, 5):
        n = background(s) + (spinners if spin_from <= s < spin_from + spin_s else 0)
        load = load * d5 + n * (1 - d5)
        if s % 10 == 0:
            rows.append(f"{(t0 + timedelta(seconds=s)).strftime('%Y-%m-%dT%H:%M:%SZ')}\t{load:.2f}\t900\t0\t0.0\t20.0\n")
    return rows


def test_real_hub_burst_of_12_spinners_is_flagged(tmp_path):
    rc, out = _detect(_hub_tsv(tmp_path / "spin.tsv", HUB_SPIN), HUB_SPIN_START)
    assert rc == 0, f"O1698-CONTROL-CANNOT-SAY-YES: real 12-spinner burst not flagged: {out}"
    assert '"result": "PASS"' in out


def test_real_hub_quiet_window_is_not_flagged(tmp_path):
    rc, out = _detect(_hub_tsv(tmp_path / "quiet.tsv", HUB_QUIET), HUB_QUIET_START)
    assert json.loads(out.splitlines()[0])["rows_before"] >= 3, out   # the FAIL is about real rows, not an empty window
    assert rc == 1 and '"result": "FAIL"' in out, f"O1698-CONTROL-FALSE-SPIKE: quiet hub flagged: {out}"


def test_real_hub_single_interval_blip_is_not_a_burst(tmp_path):
    rc, out = _detect(_hub_tsv(tmp_path / "blip.tsv", HUB_BLIP), HUB_BLIP_START)
    info = json.loads(out.splitlines()[0])
    assert info["peak_delta_n"] >= 6, f"fixture premise: the blip clears the threshold on its own: {info}"
    assert rc == 1 and info["result"] == "FAIL", f"O1698-CONTROL-FALSE-SPIKE: one-interval blip flagged: {info}"


def test_falling_baseline_hides_the_burst_from_load1_but_not_from_runnable_count(tmp_path):
    # The PM's 02:32Z shape: load1 12 and falling (background 3 runnable), a burst at t=70 s for 30 s.
    t0 = datetime(2026, 10, 3, 2, 0, 0, tzinfo=timezone.utc)
    start = (t0 + timedelta(seconds=70)).timestamp()
    tsv = tmp_path / "falling.tsv"
    # Premise (the PM's rc=1, reproduced): 4 spinners, old judge "load1 > first row + 1.0 in >= 2 rows" -> 0 rows.
    four = [float(r.split("\t")[1]) for r in _kernel_loadavg(t0, 140, lambda s: 3, 4, 70, 30, l0=12.0)]
    assert sum(1 for x in four[8:14] if x > four[7] + 1.0) == 0
    # Calibrated: nproc/4 = 12 spinners, judged on the runnable count -> flagged.
    tsv.write_text(HEADER + "".join(_kernel_loadavg(t0, 140, lambda s: 3, 12, 70, 30, l0=12.0)))
    rc, out = _detect(tsv, start)
    assert rc == 0, f"O1698-CONTROL-CANNOT-SAY-YES: runnable-count judge missed the burst: {out}"
    # Pure decay from 12 with no burst is not a spike.
    tsv.write_text(HEADER + "".join(_kernel_loadavg(t0, 140, lambda s: 3, 0, 70, 30, l0=12.0)))
    rc, out = _detect(tsv, start)
    assert rc == 1, f"O1698-CONTROL-FALSE-SPIKE: pure decay flagged as a burst: {out}"


def test_four_spinners_are_below_the_noise_the_hub_measured(tmp_path):
    # Why not 4: in the kernel model 4 spinners give a runnable step of ~4, which the real quiet window's own
    # swing (~2-3) can match at a threshold of 2; the calibrated threshold (SPINNERS/2 = 6) needs >= 12.
    t0 = datetime(2026, 10, 3, 2, 0, 0, tzinfo=timezone.utc)
    rows = _kernel_loadavg(t0, 140, lambda s: 5, spinners=4, spin_from=70, spin_s=30, l0=5.0)
    tsv = tmp_path / "four.tsv"
    tsv.write_text(HEADER + "".join(rows))
    rc, _ = _detect(tsv, (t0 + timedelta(seconds=70)).timestamp(), threshold=6.0)
    assert rc == 1


def test_baseline_is_only_the_rows_before_the_burst(tmp_path):
    # Three baseline intervals then the burst (real hub rows from 02:20:07Z): if burst rows leaked into the
    # baseline median, the 12-spinner step would vanish against itself.
    rows = HUB_SPIN.split()
    tsv = _hub_tsv(tmp_path / "short-base.tsv", " ".join(rows[rows.index("02:20:07"):]))
    rc, out = _detect(tsv, HUB_SPIN_START)
    assert json.loads(out.splitlines()[0])["rows_before"] == 3, out
    assert rc == 0, f"O1698-CONTROL-CANNOT-SAY-YES: short baseline: {out}"


def test_too_few_rows_is_could_not_look(tmp_path):
    tsv = _hub_tsv(tmp_path / "short.tsv", "02:20:28 6.56 02:20:38 6.08 02:20:48 7.60")
    rc, out = _detect(tsv, HUB_SPIN_START)
    assert rc == 2 and '"result": "COULD NOT LOOK"' in out, out


# ---- the live control script: gate, freshness, and no spinner left behind (fixtures only, no real load) ----

def _script() -> Path:
    p = _detector().parent / "positive-control-live.sh"
    assert p.is_file(), f"control script missing beside the detector: {p}"
    return p


def _proc(tmp: Path, load1: str) -> Path:
    root = tmp / "proc"
    root.mkdir()
    (root / "loadavg").write_text(f"{load1} 1.00 1.00 1/100 1\n")
    return root


def _fresh_samples(tmp: Path) -> Path:
    now = datetime.now(timezone.utc)
    rows = [f"{(now - timedelta(seconds=10 * k)).strftime('%Y-%m-%dT%H:%M:%SZ')}\t3.00\t900\t0\t0.0\t20.0\n"
            for k in range(8, 0, -1)]
    p = tmp / "day.tsv"
    p.write_text(HEADER + "".join(rows))
    return p


def _run_script(tmp: Path, **env) -> subprocess.CompletedProcess:
    e = dict(os.environ)
    e.update({k: str(v) for k, v in env.items()})
    return subprocess.run(["bash", str(_script())], env=e, capture_output=True, text=True, timeout=60, check=False)


def _spinners_alive(mark: str = "yes bdspin-") -> list[str]:
    ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, check=True).stdout
    # the spinners themselves (`yes <mark>`), not their `timeout` wrappers
    return [l for l in ps.splitlines() if re.match(r"\s*\d+ yes ", l) and mark in l]


def test_gate_refuses_a_loaded_host_and_starts_nothing(tmp_path):
    r = _run_script(tmp_path, BD_PROC_ROOT=_proc(tmp_path, "30.00"), SAMPLES=_fresh_samples(tmp_path),
                    SPINNERS=2, SPIN_S=1, POST_S=0)
    assert r.returncode == 3 and r.stdout.startswith("GATE: load1 30.00 >= 12"), (r.returncode, r.stdout)
    assert not _spinners_alive()


def test_stale_sampler_rows_are_could_not_look(tmp_path):
    stale = tmp_path / "stale.tsv"
    stale.write_text(HEADER + "2026-10-01T00:00:00Z\t3.00\t900\t0\t0.0\t20.0\n")
    r = _run_script(tmp_path, BD_PROC_ROOT=_proc(tmp_path, "1.00"), SAMPLES=stale, SPINNERS=2, SPIN_S=1, POST_S=0)
    assert r.returncode == 2 and r.stdout.startswith("COULD NOT LOOK: sampler rows stale"), (r.returncode, r.stdout)


def test_spinners_are_bounded_and_none_survive(tmp_path):
    # 2 spinners for 1 s against fixture rows; the fixture has no rows after the start, so the verdict is COULD NOT
    # LOOK (exit 2) -- this test is about the spinners: they ran, and none is left.
    r = _run_script(tmp_path, BD_PROC_ROOT=_proc(tmp_path, "1.00"), SAMPLES=_fresh_samples(tmp_path),
                    SPINNERS=2, SPIN_S=1, POST_S=0)
    assert "spinners=2 spin_s=1" in r.stdout and "left_spinners=0" in r.stdout, r.stdout + r.stderr
    assert r.returncode == 2, (r.returncode, r.stdout)
    assert not _spinners_alive(), _spinners_alive()


def test_spin_seconds_are_capped_at_60(tmp_path):
    text = _script().read_text()
    assert '[ "$SPIN_S" -gt 60 ] && SPIN_S=60' in text
    assert "trap cleanup EXIT" in text and "trap 'cleanup; exit 130' INT TERM" in text
    assert 'timeout "$SPIN_S" yes' in text


def test_term_mid_burst_kills_the_spinners_at_once(tmp_path):
    e = dict(os.environ, BD_PROC_ROOT=str(_proc(tmp_path, "1.00")), SAMPLES=str(_fresh_samples(tmp_path)),
             SPINNERS="2", SPIN_S="30", POST_S="0")
    p = subprocess.Popen(["bash", str(_script())], env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    mark = f"yes bdspin-{p.pid}"
    try:
        deadline = time.monotonic() + 10
        while len(_spinners_alive(mark)) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(_spinners_alive(mark)) == 2, "fixture premise: the 2 spinners started"
        p.terminate()
        p.wait(timeout=5)
        time.sleep(0.5)
        assert not _spinners_alive(mark), f"O1698-CONTROL-SPINNERS-LEFT: TERM left {_spinners_alive(mark)}"
        assert p.returncode == 130, p.returncode
    finally:
        if p.poll() is None:
            p.kill()
        subprocess.run(["pkill", "-f", "--", "^" + mark], check=False)
