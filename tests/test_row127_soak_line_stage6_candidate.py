"""Row PG-CUTOVER-SOAK-COMPLETION deliverable 3: Stage 6 metrics columns on the soak probe.

The deployed bd-pg-soak-line.sh (bd-persist/harness/) writes 8 TSV columns per
soak run. The app's /api/health .mod3 block is gaining `.proven` and
`.metrics` keys in this row (built in parallel; this test pins the contract).
The FIX candidate under harness-work/FIX/pg-soak-line-stage6/ appends 9 more
columns sourced from those keys: p50_ms, p95_ms, p99_ms, read_error_rate,
write_error_rate, fallback_incidents, proven, unproven, divergent -- printing
"?" wherever an older build's health payload lacks the key.

Hermetic: a fake `ssh` first on PATH prints a canned health JSON (its args are
ignored -- no real ssh, no network), and BD_SOAK_DIR / BD_SOAK_SAY point the
candidate at tmp_path instead of the live /home/mboyle/bd-persist tree and the
live bd-say.sh.
"""
from __future__ import annotations

import os
import stat
import subprocess

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get(
    "BD_PG_SOAK_LINE_STAGE6_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/pg-soak-line-stage6/bd-pg-soak-line.sh",
)
DEPLOYED = "/home/mboyle/bd-persist/harness/bd-pg-soak-line.sh"

if not os.path.exists(CANDIDATE):
    pytest.skip(
        f"candidate not present at {CANDIDATE} (set "
        "BD_PG_SOAK_LINE_STAGE6_CANDIDATE to opt in)",
        allow_module_level=True,
    )
elif not os.access(CANDIDATE, os.X_OK):
    pytest.fail(f"candidate at {CANDIDATE} exists but is not executable")


NEW_BUILD_HEALTH = """{
  "build": {"sha": "abc1234"},
  "mod3": {
    "dual_write": true, "shadow_read": true,
    "stats": {"degraded_reason": null},
    "shadow": {"diverged": 0, "errors": 0, "compared": 10, "skipped": 1},
    "proven": {"threshold": 50, "proven": 10, "unproven": 2, "divergent": 0},
    "metrics": {
      "latency_ms": {"p50": 1.5, "p95": 3.2, "p99": 9.9, "samples": 10},
      "reads": {"attempts": 10, "errors": 0, "error_rate": 0.0},
      "writes": {"attempts": 10, "errors": 0, "error_rate": 0.0},
      "fallback_incidents": 0
    }
  }
}"""

OLD_BUILD_HEALTH = """{
  "build": {"sha": "old0001"},
  "mod3": {
    "dual_write": true, "shadow_read": false,
    "stats": {"degraded_reason": null},
    "shadow": {"diverged": 0, "errors": 0, "compared": 5, "skipped": 0}
  }
}"""

RED_HEALTH = """{
  "build": {"sha": "red0001"},
  "mod3": {
    "dual_write": true, "shadow_read": true,
    "stats": {"degraded_reason": null},
    "shadow": {"diverged": 1, "errors": 0, "compared": 5, "skipped": 0}
  }
}"""


def _fake_ssh(bin_dir, health_json):
    """A fake `ssh` first on PATH: prints canned health JSON, ignores args."""
    path = os.path.join(bin_dir, "ssh")
    with open(path, "w") as fh:
        fh.write("#!/usr/bin/env bash\ncat <<'JSON'\n" + health_json + "\nJSON\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _fake_say(bin_dir, record_file):
    """A fake say executable that records its argv to record_file."""
    path = os.path.join(bin_dir, "bd-say.sh")
    with open(path, "w") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "$@" >> "{record_file}"\n'
        )
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run(script, bin_dir, soak_dir, say_path, env=None):
    full_env = dict(os.environ)
    full_env["PATH"] = f"{bin_dir}:{full_env.get('PATH', '')}"
    full_env["BD_SOAK_DIR"] = str(soak_dir)
    full_env["BD_SOAK_SAY"] = str(say_path)
    if env:
        full_env.update(env)
    return subprocess.run(
        ["bash", script],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def rig(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    soak_dir = tmp_path / "persist"
    soak_dir.mkdir()
    say_record = soak_dir / "say.log"
    say_path = _fake_say(str(bin_dir), str(say_record))
    return {
        "bin_dir": str(bin_dir),
        "soak_dir": soak_dir,
        "say_path": say_path,
        "say_record": say_record,
    }


def _log_lines(soak_dir):
    log = soak_dir / "PG-SOAK-LOG.tsv"
    assert log.exists(), f"no log written to {soak_dir}"
    return log.read_text().splitlines()


def test_new_build_green_appends_nine_stage6_columns(rig):
    _fake_ssh(rig["bin_dir"], NEW_BUILD_HEALTH)
    result = _run(CANDIDATE, rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 0, result.stderr

    lines = _log_lines(rig["soak_dir"])
    assert len(lines) == 1, lines
    fields = lines[0].split("\t")
    assert len(fields) == 17, fields

    # First 8 columns identical in meaning to the deployed format.
    assert fields[1] == "abc1234 soak"
    assert fields[2] == "True"          # dual_write
    assert fields[3] == "True"          # shadow_read
    assert fields[4] == "0"             # degraded
    assert fields[5] == "0"             # diverged (shadow)
    assert fields[6] == "0"             # errors (shadow)
    assert fields[7] == "bd-pg-soak-line cmp=10 skip=1"

    # Appended Stage 6 columns, in spec order.
    p50, p95, p99, rerr, werr, fallback, proven, unproven, divergent = fields[8:17]
    assert p50 == "1.5"
    assert p95 == "3.2"
    assert p99 == "9.9"
    assert rerr == "0.0"
    assert werr == "0.0"
    assert fallback == "0"
    assert proven == "10"
    assert unproven == "2"
    assert divergent == "0"

    assert not list(rig["soak_dir"].glob("FINDING-*")), "no FINDING on GREEN"
    assert not rig["say_record"].exists(), "say must not be called on GREEN"


def test_old_build_without_proven_or_metrics_appends_nine_question_marks(rig):
    _fake_ssh(rig["bin_dir"], OLD_BUILD_HEALTH)
    result = _run(CANDIDATE, rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 0, result.stderr

    lines = _log_lines(rig["soak_dir"])
    fields = lines[0].split("\t")
    assert len(fields) == 17, fields
    assert fields[8:17] == ["?"] * 9, fields[8:17]


def test_red_diverged_writes_finding_and_calls_say(rig):
    _fake_ssh(rig["bin_dir"], RED_HEALTH)
    result = _run(CANDIDATE, rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 0, result.stderr

    lines = _log_lines(rig["soak_dir"])
    fields = lines[0].split("\t")
    assert fields[5] == "1", "diverged column must reflect RED"

    findings = list(rig["soak_dir"].glob("FINDING-ROW127-SOAK-RED-*.md"))
    assert len(findings) == 1, findings
    assert rig["say_record"].exists(), "say must be called on RED"
    say_args = rig["say_record"].read_text()
    assert "bd-pm-A" in say_args
    assert str(findings[0]) in say_args


def test_deployed_script_negative_control_lacks_stage6_columns(rig, tmp_path):
    """Negative control: the deployed script, hermeticized only for
    BD_SOAK_DIR/BD_SOAK_SAY via sed, still emits the OLD 8-column format --
    proving test_new_build_green_appends_nine_stage6_columns can say NO
    against a script that has not implemented this deliverable.
    """
    with open(DEPLOYED) as fh:
        deployed_src = fh.read()
    patched = deployed_src.replace(
        "P=/home/mboyle/bd-persist;",
        "P=${BD_SOAK_DIR:-/home/mboyle/bd-persist};",
    ).replace(
        "/home/mboyle/bd-say.sh",
        '${BD_SOAK_SAY:-/home/mboyle/bd-say.sh}',
    )
    assert patched != deployed_src, "sed patch matched nothing -- deployed script shape changed"

    copy_path = tmp_path / "deployed-copy.sh"
    copy_path.write_text(patched)
    copy_path.chmod(0o755)

    _fake_ssh(rig["bin_dir"], NEW_BUILD_HEALTH)
    result = _run(str(copy_path), rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 0, result.stderr

    lines = _log_lines(rig["soak_dir"])
    fields = lines[0].split("\t")
    # Deployed format: exactly 8 fields -- proves the appended-column
    # assertion in test_new_build_green_appends_nine_stage6_columns (which
    # requires len(fields) == 17) FAILS against this unmodified format.
    assert len(fields) == 8, fields
    with pytest.raises(AssertionError):
        assert len(fields) == 17, fields


def test_empty_ssh_output_writes_could_not_look_with_seventeen_fields(rig):
    ssh_path = os.path.join(rig["bin_dir"], "ssh")
    with open(ssh_path, "w") as fh:
        fh.write("#!/usr/bin/env bash\ntrue\n")
    os.chmod(ssh_path, 0o755)

    result = _run(CANDIDATE, rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 2, result.stderr

    lines = _log_lines(rig["soak_dir"])
    fields = lines[0].split("\t")
    assert len(fields) == 17, fields
    assert fields[1] == "COULD-NOT-LOOK"
    assert fields[8:17] == ["?"] * 9, fields[8:17]
    assert not rig["say_record"].exists()


@pytest.mark.parametrize("body", [
    "<html><body>502 Bad Gateway</body></html>",
    '{"mod3": {"shadow": ',              # truncated JSON
    "[]",                                # valid JSON, not an object
], ids=["html", "truncated", "not-an-object"])
def test_malformed_health_body_writes_could_not_look_not_a_reading(rig, body):
    """A nonempty body that is not a health object is not a reading: same
    COULD-NOT-LOOK row and exit 2 as the empty body -- never a row of empty
    fields that reads as RED."""
    _fake_ssh(rig["bin_dir"], body)
    result = _run(CANDIDATE, rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 2, (result.returncode, result.stderr)
    lines = [ln for ln in _log_lines(rig["soak_dir"]) if ln.strip()]
    assert len(lines) == 1, lines
    fields = lines[0].split("\t")
    assert len(fields) == 17, fields
    assert fields[1] == "COULD-NOT-LOOK"
    assert fields[7] == "bd-pg-soak-line: health body malformed"
    assert fields[8:17] == ["?"] * 9, fields[8:17]
    assert not list(rig["soak_dir"].glob("FINDING-*")), "malformed is not RED"
    assert not rig["say_record"].exists()

def test_degraded_reason_with_spaces_keeps_columns_aligned(rig):
    """A real degraded_reason ("mirror write failed (OperationalError)") has
    spaces; split on whitespace it shifted every later column."""
    health = NEW_BUILD_HEALTH.replace(
        '"degraded_reason": null',
        '"degraded_reason": "mirror write failed (OperationalError)"')
    assert health != NEW_BUILD_HEALTH
    _fake_ssh(rig["bin_dir"], health)
    result = _run(CANDIDATE, rig["bin_dir"], rig["soak_dir"], rig["say_path"])
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in _log_lines(rig["soak_dir"]) if "\tabc1234 soak" in ln]
    assert len(lines) == 1, lines
    fields = lines[0].split("\t")
    assert len(fields) == 17, fields
    assert fields[4] == "1"                        # degraded
    assert fields[7].startswith("bd-pg-soak-line cmp=")
    assert fields[8:] == ["1.5", "3.2", "9.9", "0.0", "0.0", "0", "10", "2",
                          "0"], fields
    finding = list(rig["soak_dir"].glob("FINDING-ROW127-SOAK-RED-*.md"))
    assert len(finding) == 1
    assert "mirror_write_failed_(OperationalError)" in finding[0].read_text()
