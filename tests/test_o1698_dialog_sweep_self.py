import os
import subprocess
import textwrap
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_DIALOG_SWEEP_SELF_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
NOW = 1791029056


@pytest.fixture
def sweep(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), "CANDIDATE_INVALID"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    clock = tmp_path / "clock"
    clock.write_text(str(NOW))
    rows = tmp_path / "panes"
    rows.write_text("")
    calls = tmp_path / "calls"
    calls.write_text("")
    alert = tmp_path / "alerts"
    content = tmp_path / "content"
    content.write_text("Do you want to proceed?\n1. Yes\n")
    scripts = {
        "tmux": """\
            #!/usr/bin/env python3
            import os, pathlib, sys
            if sys.argv[1] == 'list-panes':
                fmt = sys.argv[sys.argv.index('-F') + 1]
                for row in pathlib.Path(os.environ['FIXTURE_ROWS']).read_text().splitlines():
                    seat, session, window, pane = row.split('|')
                    values = {'session_name': seat, 'window_index': '0', 'pane_index': '0',
                              'session_activity': session, 'window_activity': window,
                              'pane_activity': pane}
                    for name, value in values.items():
                        fmt_row = fmt if name == 'session_name' else fmt_row
                        fmt_row = fmt_row.replace('#{' + name + '}', value)
                    print(fmt_row)
            elif sys.argv[1] == 'capture-pane':
                print(pathlib.Path(os.environ['FIXTURE_CONTENT']).read_text(), end='')
            else:
                sys.exit(91)
            """,
        "date": """\
            #!/usr/bin/env python3
            import datetime, os, pathlib, sys
            now = int(pathlib.Path(os.environ['FIXTURE_CLOCK']).read_text())
            if sys.argv[1:] == ['+%s']:
                print(now)
            elif sys.argv[1:] == ['-u', '+%FT%TZ']:
                print(datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
            elif len(sys.argv) == 4 and sys.argv[1] == '-d' and sys.argv[3] == '+%s':
                print(int(datetime.datetime.fromisoformat(sys.argv[2]).timestamp()))
            else:
                sys.exit(92)
            """,
        "bd-say": """\
            #!/usr/bin/env python3
            import os, pathlib, sys
            with pathlib.Path(os.environ['FIXTURE_CALLS']).open('a') as stream:
                stream.write('|'.join(sys.argv[1:]) + '\\n')
            sys.exit(int(os.environ['FIXTURE_SAY_RC']))
            """,
    }
    for name, source in scripts.items():
        path = bindir / name
        path.write_text(textwrap.dedent(source))
        path.chmod(0o755)
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "LC_ALL": "C",
        "TZ": "UTC",
        "BD_DIALOG_P": str(tmp_path),
        "BD_DIALOG_ALERT_FILE": str(alert),
        "BD_DIALOG_SAY": str(bindir / "bd-say"),
        "BD_DIALOG_PM": "bd-pm-other",
        "BD_DIALOG_REALERT": "60",
        "FIXTURE_CLOCK": str(clock),
        "FIXTURE_ROWS": str(rows),
        "FIXTURE_CONTENT": str(content),
        "FIXTURE_CALLS": str(calls),
        "FIXTURE_SAY_RC": "0",
    }

    class Sweep:
        def panes(self, session, window, pane="", seat="bd-worker-fixture"):
            rows.write_text(f"{seat}|{session}|{window}|{pane}\n")
            assert rows.stat().st_size > 0
            assert "Do you want to proceed?" in content.read_text()

        def run(self, offset=0):
            clock.write_text(str(NOW + offset))
            result = subprocess.run(
                ["bash", str(candidate), "300"], env=env, cwd=tmp_path,
                capture_output=True, text=True, timeout=10, check=False,
            )
            assert result.returncode == 0, result.stderr
            assert result.stderr == ""
            return result.stdout.strip()

        def sends(self):
            return calls.read_text().splitlines()

        def alerts(self):
            return alert.read_text().splitlines() if alert.exists() else []

    runner = Sweep()
    runner.env = env
    return runner


@pytest.mark.parametrize("fresh", ["session", "window", "pane"])
def test_recent_activity_prevents_false_alarm(sweep, fresh):
    activity = dict.fromkeys(("session", "window", "pane"), NOW - 24000)
    activity[fresh] = NOW
    sweep.panes(activity["session"], activity["window"], activity["pane"])
    stdout = sweep.run()
    assert stdout == "H545 dialog sweep: hits=0 alerted=0 suppressed=0 failed=0", "FRESH_ACTIVITY_FALSE_ALARM"
    assert sweep.sends() == []
    assert sweep.alerts() == []


def test_self_target_is_skipped_once_per_realert(sweep):
    sweep.panes(NOW - 24000, NOW - 24000, seat="bd-pm-other")
    outputs = [sweep.run(offset) for offset in (0, 59, 60, 61)]
    assert sweep.sends() == [], "SELF_TARGET_DELIVERY_ATTEMPT"
    assert len(sweep.alerts()) == 2, "SELF_SKIP_REALERT_COUNT"
    assert all("SELF-SKIP" in line and "bd-pm-other:0.0" in line for line in sweep.alerts())
    assert outputs[1] == outputs[3] == "H545 dialog sweep: hits=1 alerted=0 suppressed=1 failed=0"


def test_failed_escalation_obeys_realert(sweep):
    sweep.panes(NOW - 24000, NOW - 24000)
    sweep.env["FIXTURE_SAY_RC"] = "6"
    outputs = [sweep.run(offset) for offset in (0, 59, 60, 61)]
    assert len(sweep.sends()) == 2, "FAILED_ESCALATION_RETRY_EVERY_TICK"
    assert len(sweep.alerts()) == 2
    assert all("ESCALATION FAILED" in line and "rc=6" in line for line in sweep.alerts())
    assert outputs[0] == outputs[2] == "H545 dialog sweep: hits=1 alerted=0 suppressed=0 failed=1"
    assert outputs[1] == outputs[3] == "H545 dialog sweep: hits=1 alerted=0 suppressed=1 failed=0"


def test_stale_other_seat_still_alerts_and_realerts(sweep):
    sweep.panes(NOW - 24000, NOW - 24000, NOW - 24000)
    outputs = [sweep.run(offset) for offset in (0, 59, 60)]
    assert len(sweep.sends()) == 2
    assert all(line.startswith("bd-pm-other|H545 DIALOG STUCK: bd-worker-fixture:0.0 ") for line in sweep.sends())
    assert len(sweep.alerts()) == 2
    assert all(" DIALOG STUCK: bd-worker-fixture:0.0 " in line for line in sweep.alerts())
    assert outputs[0] == outputs[2] == "H545 dialog sweep: hits=1 alerted=1 suppressed=0 failed=0"
    assert outputs[1] == "H545 dialog sweep: hits=1 alerted=0 suppressed=1 failed=0"


@pytest.mark.parametrize("age,expected", [(299, 0), (300, 1)])
def test_age_threshold_with_unsupported_pane_activity(sweep, age, expected):
    sweep.panes(NOW - age, NOW - age)
    stdout = sweep.run()
    assert stdout == f"H545 dialog sweep: hits={expected} alerted={expected} suppressed=0 failed=0"
    assert len(sweep.sends()) == expected


def test_stale_pane_without_dialog_is_ignored(sweep):
    sweep.panes(NOW - 24000, NOW - 24000)
    Path(sweep.env["FIXTURE_CONTENT"]).write_text("tool finished successfully\n")
    stdout = sweep.run()
    assert stdout == "H545 dialog sweep: hits=0 alerted=0 suppressed=0 failed=0"
    assert sweep.sends() == []
    assert sweep.alerts() == []
