"""BH-RB-3: harness candidates for findings kimi-audit-2-005, 012, 014.

Covers:
- Finding 005: bd-sentinel.sh alert path, exit code handling, and history dedup
- Finding 012: bd-usage-forecaster.py stub constant-zero and tripwire
- Finding 014: bd-usage-monitor.sh census schema drift and NA marking

Candidates live outside the repo under harness-work/FIX/bh-rb-3-bd-agy-fixer-1/.
Test suite opts in via BD_BH_RB_3_CANDIDATE_DIR (or individual candidate paths).
Hermetic stubs (fleet, say, census) are used exclusively.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CAND_DIR = os.environ.get("BD_BH_RB_3_CANDIDATE_DIR", "")
SENTINEL_CAND = os.environ.get("BD_BH_RB_3_SENTINEL_CANDIDATE") or (
    os.path.join(CAND_DIR, "bd-sentinel.sh") if CAND_DIR else ""
)
FORECASTER_CAND = os.environ.get("BD_BH_RB_3_FORECASTER_CANDIDATE") or (
    os.path.join(CAND_DIR, "bd-usage-forecaster.py") if CAND_DIR else ""
)
USAGEMON_CAND = os.environ.get("BD_BH_RB_3_USAGEMON_CANDIDATE") or (
    os.path.join(CAND_DIR, "bd-usage-monitor.sh") if CAND_DIR else ""
)

pytestmark = pytest.mark.skipif(
    not (CAND_DIR or SENTINEL_CAND or FORECASTER_CAND or USAGEMON_CAND),
    reason=(
        "candidate opt-in required: set BD_BH_RB_3_CANDIDATE_DIR or"
        " BD_BH_RB_3_{SENTINEL,FORECASTER,USAGEMON}_CANDIDATE"
    ),
)


def _run(
    cmd: list[str],
    env: dict[str, str],
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    e = dict(os.environ)
    e["LC_ALL"] = "C"
    e.update(env)
    return subprocess.run(
        cmd,
        env=e,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def _stub(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _prepare_script(script_path: Path, tmp_path: Path) -> Path:
    """Ensure script execution is 100% hermetic.

    If the script already has env var seams (the candidate), execute directly.
    For orig scripts lacking seams, rewrite hardcoded live paths to isolated tmp_path
    so running RED against orig never touches live logs or live tools.
    """
    assert script_path.exists(), f"script does not exist: {script_path}"
    content = script_path.read_text()
    if "BD_SENTINEL_LOG" in content or "BD_USAGEMON_OUT" in content:
        return script_path
    seamed = (
        content.replace(
            "/home/mboyle/bd-persist/logs/usage-timeseries.csv",
            str(tmp_path / "out.csv"),
        )
        .replace("/home/mboyle/bd-persist/sentinel.log", str(tmp_path / "sentinel.log"))
        .replace(
            "/home/mboyle/BulkDownloader/toolchain/bin/bd-fleet",
            str(tmp_path / "bin/bd-fleet"),
        )
        .replace("/home/mboyle/bd-persist/logs", str(tmp_path / "logs"))
        .replace(
            "/home/mboyle/bd-persist/harness/bd-usage-census.py",
            str(tmp_path / "census.py"),
        )
    )
    run_file = tmp_path / script_path.name
    run_file.write_text(seamed)
    run_file.chmod(0o755)
    return run_file


# ---------------------------------------------------------------- Finding 005 Sentinel
class TestSentinel:
    @pytest.fixture(autouse=True)
    def _check_cand(self) -> None:
        if not SENTINEL_CAND:
            pytest.skip("SENTINEL_CAND not set")

    def test_sentinel_handles_informational_fleet_rc_and_alerts(
        self, tmp_path: Path
    ) -> None:
        script = _prepare_script(Path(SENTINEL_CAND), tmp_path)
        fleet = tmp_path / "bin/bd-fleet"
        _stub(fleet, "#!/bin/bash\necho 'host1 UNREACHABLE ssh failed'\nexit 1\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        log = tmp_path / "sentinel.log"
        state = tmp_path / "state.sha1"
        env = {
            "BD_SENTINEL_FLEET": str(fleet),
            "BD_SENTINEL_LOG": str(log),
            "BD_SENTINEL_SAY": str(say),
            "BD_SENTINEL_STATE": str(state),
        }
        r = _run(["bash", str(script)], env)
        assert r.returncode == 0, (
            f"sentinel must exit 0 on informational rc: {r.stderr}"
        )
        assert log.exists(), "sentinel log must be created"
        log_content = log.read_text()
        assert "unreachable:1" in log_content
        assert saylog.exists(), "alert must be sent via say"
        assert "fleet alert: unreachable:1" in saylog.read_text()

    def test_sentinel_clean_run_does_not_alert_even_with_stale_history(
        self, tmp_path: Path
    ) -> None:
        script = _prepare_script(Path(SENTINEL_CAND), tmp_path)
        fleet = tmp_path / "bin/bd-fleet"
        _stub(fleet, "#!/bin/bash\necho 'fleet nominal, drift noted'\nexit 0\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        log = tmp_path / "sentinel.log"
        # stale history in the pre-fix writer's format: bare "ALERT:<class>" lines,
        # which its whole-log grep "^ALERT:" re-matched on every later run
        _write(log, "ALERT:unreachable\n")
        state = tmp_path / "state.sha1"
        env = {
            "BD_SENTINEL_FLEET": str(fleet),
            "BD_SENTINEL_LOG": str(log),
            "BD_SENTINEL_SAY": str(say),
            "BD_SENTINEL_STATE": str(state),
        }
        r = _run(["bash", str(script)], env)
        assert r.returncode == 0
        tail = log.read_text().splitlines()[-1]
        assert "would_alert" not in tail, "historical alerts must not cause re-alert"
        assert not saylog.exists(), "say must not be called when clean"

    def test_sentinel_unchanged_alert_set_deduped(self, tmp_path: Path) -> None:
        script = _prepare_script(Path(SENTINEL_CAND), tmp_path)
        fleet = tmp_path / "bin/bd-fleet"
        _stub(fleet, "#!/bin/bash\necho 'host1 UNREACHABLE'\nexit 1\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        log = tmp_path / "sentinel.log"
        state = tmp_path / "state.sha1"
        env = {
            "BD_SENTINEL_FLEET": str(fleet),
            "BD_SENTINEL_LOG": str(log),
            "BD_SENTINEL_SAY": str(say),
            "BD_SENTINEL_STATE": str(state),
        }
        _run(["bash", str(script)], env)
        _run(["bash", str(script)], env)
        assert saylog.exists(), "say must have been called"
        assert saylog.read_text().count("fleet alert") == 1, (
            "unchanged alert set must not re-send alert"
        )

    def test_sentinel_fleet_empty_output_and_nonzero_exits_1(
        self, tmp_path: Path
    ) -> None:
        script = _prepare_script(Path(SENTINEL_CAND), tmp_path)
        fleet = tmp_path / "bin/bd-fleet"
        _stub(fleet, "#!/bin/bash\nexit 1\n")
        saylog = tmp_path / "say.log"
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {saylog}\nexit 0\n')
        log = tmp_path / "sentinel.log"
        env = {
            "BD_SENTINEL_FLEET": str(fleet),
            "BD_SENTINEL_LOG": str(log),
            "BD_SENTINEL_SAY": str(say),
            "BD_SENTINEL_STATE": str(tmp_path / "state.sha1"),
        }
        r = _run(["bash", str(script)], env)
        assert r.returncode == 1, "empty output on failure must exit 1"
        assert "FLEET_FAILED" in log.read_text()
        assert not saylog.exists()


# ------------------------------------------------------------- Finding 012 Forecaster
class TestForecaster:
    @pytest.fixture(autouse=True)
    def _check_cand(self) -> None:
        if not FORECASTER_CAND:
            pytest.skip("FORECASTER_CAND not set")

    def _csv(self, tmp_path: Path, totals: list[int]) -> Path:
        rows = ["timestamp,total_tokens,landed_cuts,tokens_per_cut"]
        rows += [f"2026-09-28T0{i}:00:00Z,{t},1,100" for i, t in enumerate(totals)]
        return _write(tmp_path / "usage-timeseries.csv", "\n".join(rows) + "\n")

    def _env(self, tmp_path: Path, csv: Path | None) -> dict[str, str]:
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {tmp_path}/say.log\nexit 0\n')
        return {
            "BD_FORECASTER_CSV": str(csv) if csv else str(tmp_path / "absent.csv"),
            "BD_FORECASTER_STATE": str(tmp_path / "state.count"),
            "BD_FORECASTER_SAY": str(say),
            "BD_FORECASTER_UNIMPL_ALERT_N": "2",
        }

    def test_forecaster_output_changes_with_input(self, tmp_path: Path) -> None:
        script = Path(FORECASTER_CAND)
        lo = self._csv(tmp_path / "a", [100, 200, 300, 400])
        hi = self._csv(tmp_path / "b", [9_000_000, 9_500_000, 10_000_000, 10_500_000])
        r1 = _run(["python3", str(script)], self._env(tmp_path / "a", lo))
        r2 = _run(["python3", str(script)], self._env(tmp_path / "b", hi))
        assert r1.returncode == 0 and r2.returncode == 0
        m1 = re.search(r"~(\d+) tokens", r1.stdout)
        m2 = re.search(r"~(\d+) tokens", r2.stdout)
        assert m1 is not None and m2 is not None, (
            f"forecast numbers must be present: r1={r1.stdout!r}, r2={r2.stdout!r}"
        )
        assert int(m2.group(1)) > int(m1.group(1)), (
            "higher usage input must yield higher forecast"
        )

    def test_forecaster_missing_data_exits_3(self, tmp_path: Path) -> None:
        script = Path(FORECASTER_CAND)
        r = _run(["python3", str(script)], self._env(tmp_path, None))
        assert r.returncode == 3, f"missing data must exit 3, got rc={r.returncode}"
        assert "UNIMPLEMENTED" in r.stdout
        assert "Forecasting usage: 0 tokens expected" not in r.stdout

    def test_forecaster_tripwire_alerts_after_n_ticks(self, tmp_path: Path) -> None:
        script = Path(FORECASTER_CAND)
        env = self._env(tmp_path, None)
        _run(["python3", str(script)], env)
        r2 = _run(["python3", str(script)], env)
        assert r2.returncode == 3
        saylog = tmp_path / "say.log"
        assert saylog.exists(), "N consecutive UNIMPLEMENTED ticks must alert"
        assert "UNIMPLEMENTED" in saylog.read_text()

    def test_forecaster_success_clears_tripwire(self, tmp_path: Path) -> None:
        script = Path(FORECASTER_CAND)
        env = self._env(tmp_path, None)
        _run(["python3", str(script)], env)
        assert (tmp_path / "state.count").exists()
        good_csv = self._csv(tmp_path, [1000, 2000, 3000])
        env2 = self._env(tmp_path, good_csv)
        r = _run(["python3", str(script)], env2)
        assert r.returncode == 0
        assert not (tmp_path / "state.count").exists(), (
            "successful forecast clears state"
        )

    def test_forecaster_mid_window_daily_reset_does_not_print_negative(
        self, tmp_path: Path
    ) -> None:
        script = Path(FORECASTER_CAND)
        # 00:00Z reset drops from 4.1B to 10k, followed by normal positive increments
        csv = self._csv(
            tmp_path,
            [4_000_000_000, 4_100_000_000, 10_000, 20_000, 30_000, 40_000],
        )
        r = _run(["python3", str(script)], self._env(tmp_path, csv))
        assert r.returncode == 0, f"daily reset should not crash: {r.stdout} {r.stderr}"
        assert "-" not in r.stdout, f"negative forecast prohibited: {r.stdout}"
        m = re.search(r"~(\d+) tokens", r.stdout)
        assert m is not None, f"forecast expected in stdout: {r.stdout}"
        assert int(m.group(1)) >= 0

    def test_forecaster_insufficient_usable_deltas_after_reset_exits_3(
        self, tmp_path: Path
    ) -> None:
        script = Path(FORECASTER_CAND)
        # Window with only one delta which is a negative reset -> <2 usable deltas
        csv = self._csv(tmp_path, [4_000_000_000, 10_000])
        r = _run(["python3", str(script)], self._env(tmp_path, csv))
        assert r.returncode == 3, f"insufficient deltas must exit 3: {r.stdout}"
        assert "UNIMPLEMENTED" in r.stdout

    def test_forecaster_single_row_exits_3(self, tmp_path: Path) -> None:
        script = Path(FORECASTER_CAND)
        csv = self._csv(tmp_path, [1000])
        r = _run(["python3", str(script)], self._env(tmp_path, csv))
        assert r.returncode == 3, f"single row must exit 3: {r.stdout}"
        assert "UNIMPLEMENTED" in r.stdout


# ---------------------------------------------------------- Finding 014 Usage Monitor
class TestUsageMonitor:
    @pytest.fixture(autouse=True)
    def _check_cand(self) -> None:
        if not USAGEMON_CAND:
            pytest.skip("USAGEMON_CAND not set")

    CENSUS_FULL = (
        "census v1\nTOTAL (metered)|all|7d|1,234,567\n"
        "Landed cuts today: 3\nMetered tokens per landed cut: 411,522\n"
    )
    CENSUS_DRIFTED = (
        "census v1\nTOTAL (metered)|all|7d|1,234,567\n(no landings section)\n"
    )

    def _env(self, tmp_path: Path, census_text: str) -> dict[str, str]:
        census = tmp_path / "census.sh"
        _stub(census, f"#!/bin/bash\nprintf '%s' '{census_text}'\n")
        # orig scripts lack the BD_USAGEMON_CENSUS seam and run the census.py that
        # _prepare_script points at: same text, so they reach the field guards
        _write(
            tmp_path / "census.py",
            f"import sys\nsys.stdout.write({census_text!r})\n",
        )
        say = tmp_path / "say.sh"
        _stub(say, f'#!/bin/bash\necho "$@" >> {tmp_path}/say.log\nexit 0\n')
        return {
            "BD_USAGEMON_PERSIST": str(tmp_path / "persist"),
            "BD_USAGEMON_OUT": str(tmp_path / "out.csv"),
            "BD_USAGEMON_CENSUS": f"bash {census}",
            "BD_USAGEMON_STATE": str(tmp_path / "drift.count"),
            "BD_USAGEMON_SAY": str(say),
            "BD_USAGEMON_SCHEMA_ALERT_N": "2",
        }

    def test_usage_monitor_drifted_census_marks_na(self, tmp_path: Path) -> None:
        script = _prepare_script(Path(USAGEMON_CAND), tmp_path)
        env = self._env(tmp_path, self.CENSUS_DRIFTED)
        r = _run(["bash", str(script)], env)
        assert r.returncode == 0
        out = tmp_path / "out.csv"
        assert out.exists(), "out.csv must be appended"
        row = out.read_text().splitlines()[-1]
        assert row.endswith(",NA,NA"), f"drifted row must end with ,NA,NA: {row}"
        assert "schema drift" in r.stderr
        saylog = tmp_path / "say.log"
        assert not saylog.exists(), "first drift sample must not alert"

    def test_usage_monitor_full_census_appends_clean_row(self, tmp_path: Path) -> None:
        script = _prepare_script(Path(USAGEMON_CAND), tmp_path)
        env = self._env(tmp_path, self.CENSUS_FULL)
        r = _run(["bash", str(script)], env)
        assert r.returncode == 0
        out = tmp_path / "out.csv"
        assert out.exists()
        row = out.read_text().splitlines()[-1]
        assert row.endswith(",3,411522"), f"row must match full census: {row}"
        assert not (tmp_path / "drift.count").exists()

    def test_usage_monitor_drift_alerts_after_n_samples(self, tmp_path: Path) -> None:
        script = _prepare_script(Path(USAGEMON_CAND), tmp_path)
        env = self._env(tmp_path, self.CENSUS_DRIFTED)
        _run(["bash", str(script)], env)
        _run(["bash", str(script)], env)
        saylog = tmp_path / "say.log"
        assert saylog.exists(), "second consecutive drift sample must alert"
        assert "schema drift x2" in saylog.read_text()

    def test_usage_monitor_clean_sample_clears_drift_state(
        self, tmp_path: Path
    ) -> None:
        script = _prepare_script(Path(USAGEMON_CAND), tmp_path)
        env_drift = self._env(tmp_path, self.CENSUS_DRIFTED)
        _run(["bash", str(script)], env_drift)
        assert (tmp_path / "drift.count").exists()
        env_clean = self._env(tmp_path, self.CENSUS_FULL)
        r = _run(["bash", str(script)], env_clean)
        assert r.returncode == 0
        assert not (tmp_path / "drift.count").exists(), (
            "clean sample clears drift state"
        )
