import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
LIVE = Path("/home/mboyle/bd-persist/harness/bd-fleet-watch.sh")
CANDIDATE = Path(os.environ.get(
    "BD_O1698_FLEET_WATCH_LIVENESS_CANDIDATE", str(LIVE),
))
SCRIPT_SHA256 = "39b65f749ff5bde5478f722e737ceb2af72d7ba1fdbabadeff850ec18cb3ddd2"
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_FLEET_WATCH_LIVENESS") != "1" or
    (not CANDIDATE.is_file() and not LIVE.is_file()),
    reason="explicit opt-in and installed script or retained snapshot required",
)


@pytest.fixture
def watch(tmp_path):
    assert CANDIDATE.is_file(), "FLEET-WATCH script absent"
    assert hashlib.sha256(CANDIDATE.read_bytes()).hexdigest() == SCRIPT_SHA256, "LIVE-SNAPSHOT-SHA-MISMATCH"
    root = tmp_path / "persist"
    state = root / "state"
    (state / "hub-samples").mkdir(parents=True)
    (root / "inbox/PM").mkdir(parents=True)
    (root / "logs").mkdir()
    hosts = tmp_path / "hosts"
    hosts.write_text("good 192.0.2.1\nhung 192.0.2.2\n")
    (root / "HOST-STATE.tsv").write_text("good\t192.0.2.1\tup\tthen\nhung\t192.0.2.2\tup\tthen\n")
    (root / "ROLE-OCCUPANCY.tsv").write_text("worker\tseat\t123\tgood\tnow\n")
    (root / "DISPATCH-LEDGER.tsv").write_text("")
    (root / "PLACEMENTS.tsv").write_text("time\tseat\thost\nnow\tseat\tgood\n")
    (root / "PM-SEAT").write_text("bd-pm-F-D\n")
    project = tmp_path / ".claude-d/projects/-var-tmp-bd-seats-pm"
    project.mkdir(parents=True)
    transcript = project / "session.jsonl"
    transcript.write_text("{}\n")
    sample = state / "hub-samples" / (time.strftime("%Y-%m-%d", time.gmtime()) + ".tsv")
    sample.write_text("utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\nnow\t5\t10\t0\t5\t20\n")
    tcp = tmp_path / "tcp"
    tcp.write_text("#!/bin/sh\ntest ! -e \"$BD_FLEET_WATCH_ROOT/tcp-down\"\n")
    ssh = tmp_path / "ssh"
    ssh.write_text("#!/bin/sh\ncase \"$*\" in *192.0.2.2*) exit 255;; esac\ncat \"$BD_FLEET_WATCH_ROOT/boot\"\nprintf '100.00 50.00\\nMETRICS 1.0 4 0 1024000\\n'\n")
    (root / "boot").write_text("11111111-1111-1111-1111-111111111111\n")
    say = tmp_path / "say"
    say.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$BD_FLEET_WATCH_ROOT/say.log\"\n")
    push = tmp_path / "push"
    push.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$BD_ALERTS_LOG\"\n")
    # The unmodified base's TCP command is isolated too: no live network or say.
    timeout = tmp_path / "timeout"
    timeout.write_text("#!/bin/sh\nif [ \"$1\" = 4 ]; then exit 0; fi\nexec /usr/bin/timeout \"$@\"\n")
    for executable in (tcp, ssh, say, push, timeout):
        executable.chmod(0o755)
    env = dict(os.environ, BD_FLEET_WATCH_ROOT=str(root),
               BD_FLEET_WATCH_HOSTS=str(hosts), BD_FLEET_WATCH_HOME=str(tmp_path),
               BD_FLEET_WATCH_TCP=str(tcp), BD_FLEET_WATCH_SSH=str(ssh),
               BD_SAY=str(say), BD_HOSTWATCH_SAY=str(say),
               BD_FLEET_WATCH_PUSH=str(push), BD_ALERTS_LOG=str(root / "alerts.log"),
               PATH=str(tmp_path) + ":" + os.environ["PATH"])

    def run(updates=None):
        assert CANDIDATE.is_file(), "FLEET-WATCH candidate absent"
        result = subprocess.run(["bash", str(CANDIDATE)], env=dict(env, **(updates or {})), capture_output=True,
                                text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        digest = state / "fleet-watch.md"
        assert digest.exists(), "LIVENESS-DIGEST-MISSING: TCP-only base has no fleet consumer"
        text = digest.read_text()
        assert len(text.splitlines()) <= 25 and len(digest.read_bytes()) <= 4096
        return text

    return root, state, transcript, sample, run


def test_two_pass_hung_and_up_control(watch):
    root, _, _, _, run = watch
    first = run()
    assert "SUSPECT" in first and "HUNG" not in first
    second = run()
    assert "HUNG:192.0.2.2" in second and "STATE=UP" in second
    assert "STATE=HUNG" in second
    assert "HUNG" in (root / "alerts.log").read_text()
    assert len((root / "say.log").read_text().splitlines()) == 1
    run()
    assert len((root / "say.log").read_text().splitlines()) == 1


@pytest.mark.parametrize(("tag", "rc", "status"), [
    ("windows", 1, "NOT-LINUX"),
    ("", 1, "COULD-NOT-LOOK"),
    ("", 0, "COULD-NOT-LOOK"),
])
def test_non_boot_answer_never_hung_with_linux_timeout_control(watch, tag, rc, status):
    root, state, _, _, run = watch
    (root.parent / "hosts").write_text(f"good 192.0.2.1 {tag}\nhung 192.0.2.2 linux\n")
    (root.parent / "ssh").write_text(
        "#!/bin/sh\ncase \"$*\" in *192.0.2.2*) exit 255;; esac\n"
        f"printf 'cat: /proc/boot_id unavailable\\n'\nexit {rc}\n"
    )
    # An inherited failure streak must not turn an unjudgeable answer into HUNG.
    (state / "fleet-watch.json").write_text(json.dumps({
        "hosts": {"192.0.2.1": {"fails": 2}}, "alerts": [],
    }))
    for _ in range(2):
        text = run()
        host_line = next(line for line in text.splitlines() if line.startswith("192.0.2.1 "))
        assert f"STATE={status}" in host_line, "NON-LINUX-FALSE-HUNG: non-boot answer judged hung"
        assert "VERIFY=COULD-NOT-LOOK" in host_line
        assert "HUNG:192.0.2.1" not in text
    assert "HUNG:192.0.2.2" in text, "LINUX-TIMEOUT-HUNG-CONTROL-MISSING"
    assert "HUNG:192.0.2.1" not in (root / "alerts.log").read_text()
    assert len((root / "say.log").read_text().splitlines()) == 1


def test_tcp_down_is_unreachable(watch):
    root, _, _, _, run = watch
    (root / "tcp-down").touch()
    assert "STATE=UNREACHABLE" in run()
    assert "HUNG" not in run()


def test_boot_change_and_recovery(watch):
    root, _, _, _, run = watch
    run()
    (root / "boot").write_text("22222222-2222-2222-2222-222222222222\n")
    assert "STATE=REBOOTED" in run()
    assert "STATE=UP" in run()


def test_hub_breach_transition_and_negative(watch):
    root, _, _, sample, run = watch
    assert "HUB-BREACH" not in run()
    sample.write_text("utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n" + "now\t5\t10\t0\t50\t20\n" * 3)
    assert "HUB-BREACH" in run()
    assert "HUB-BREACH" in (root / "alerts.log").read_text()
    run()
    assert sum("HUB-BREACH" in line for line in (root / "say.log").read_text().splitlines()) == 1
    sample.write_text("utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\nnow\t5\t10\t0\t5\t20\n")
    assert "HUB-BREACH" not in run()


def test_pm_idle_exactly_one_wake_and_fresh_control(watch):
    root, _, transcript, _, run = watch
    (root / "DISPATCH-LEDGER.tsv").write_text("now\tseat\trow\tdispatched\tbrief\n")
    assert "PM-IDLE" not in run()
    os.utime(transcript, (time.time() - 2400,) * 2)
    assert "PM-IDLE" in run()
    assert "STALL: no PM action 30 min" in (root / "alerts.log").read_text()
    assert sum("PM-IDLE" in line for line in (root / "say.log").read_text().splitlines()) == 1
    run()
    assert sum("PM-IDLE" in line for line in (root / "say.log").read_text().splitlines()) == 1
    transcript.touch()
    assert "PM-IDLE" not in run()


def test_completed_dispatch_and_empty_inbox_are_not_pending(watch):
    root, _, transcript, _, run = watch
    (root / "DISPATCH-LEDGER.tsv").write_text("old\tseat\trow\tdispatched\tbrief\nnow\tseat\trow\tlanded\tbrief\n")
    os.utime(transcript, (time.time() - 2400,) * 2)
    assert "PM-IDLE" not in run()


def test_missing_and_stale_inputs_are_unknown(watch):
    root, _, transcript, sample, run = watch
    sample.unlink()
    (root / "PLACEMENTS.tsv").unlink()
    transcript.unlink()
    text = run()
    assert "SAMPLES=COULD NOT LOOK" in text and "PLACEMENTS=COULD NOT LOOK" in text
    assert "PM=COULD NOT LOOK" in text and "PM-IDLE" not in text
    sample.write_text("utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\nnow\t5\t10\t0\t5\t20\n")
    os.utime(sample, (time.time() - 90,) * 2)
    assert "SAMPLER-STALE" in run()


def test_shared_probe_lock_does_not_advance_failure_streak(watch):
    _, state, _, _, run = watch
    lock = (state / "probe-192.0.2.2.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert "PROBE-BUSY" in run()
    finally:
        lock.close()
    assert "HUNG" not in run()
    assert "HUNG:192.0.2.2" in run()


def test_seated_host_outside_host_list_is_probed(watch):
    root, _, _, _, run = watch
    (root / "ROLE-OCCUPANCY.tsv").write_text("worker\tother-seat\t123\t192.0.2.3\tnow\n")
    text = run()
    assert "COVERAGE=3/3" in text and "192.0.2.3" in text


def test_digest_overflow_is_explicit(watch):
    root, _, _, _, run = watch
    (root / "PLACEMENTS.tsv").write_text("time\tseat\thost\n" + ("x" * 5000) + "\n")
    assert "DIGEST-OVERSIZE" in run()


def test_no_acting_markers_and_metric_fields(watch):
    _, state, _, _, run = watch
    text = run()
    for label in ("SEATS=", "CAP=", "LOAD1=", "CORES=", "BLOCKED=", "MEM=", "VERIFY=", "LAST_OK="):
        assert label in text
    assert not (state / "LAUNCH-HALT").exists()
    assert not (state / "FLEET-HALT").exists()
    data = json.loads((state / "fleet-watch.json").read_text())
    assert data["hosts"]["192.0.2.1"]["boot"] == "11111111-1111-1111-1111-111111111111"


def test_optional_metrics_failure_is_not_hung(watch):
    root, _, _, _, run = watch
    tools = root.parent / "remote-tools"
    tools.mkdir()
    (tools / "cat").write_text("#!/bin/sh\ncat \"$BD_FLEET_WATCH_ROOT/boot\"\nprintf '100.00 50.00\\n'\n".replace("cat ", "/bin/cat ", 1))
    (tools / "awk").write_text("#!/bin/sh\nprintf '1.0\\n'\n")
    (tools / "nproc").write_text("#!/bin/sh\nexit 127\n")
    for tool in tools.iterdir():
        tool.chmod(0o755)
    ssh = root.parent / "ssh"
    ssh.write_text("#!/bin/sh\nfor command; do :; done\nPATH=\"" + str(tools) + ":$PATH\" /bin/sh -c \"$command\"\n")
    assert "STATE=UP" in run()
    assert "HUNG" not in run()


def test_unknown_inputs_do_not_end_alert_streak(watch):
    root, _, transcript, sample, run = watch
    (root / "DISPATCH-LEDGER.tsv").write_text("now\tseat\trow\tdispatched\tbrief\n")
    samples = "utc\tload1\tprocs\tblocked\tiowait_pct\tmem_pct\n" + "now\t5\t10\t0\t50\t20\n" * 3
    sample.write_text(samples)
    old = time.time() - 2400
    os.utime(transcript, (old, old))
    run()
    sample.unlink()
    transcript.unlink()
    assert "COULD NOT LOOK" in run()
    sample.write_text(samples)
    transcript.write_text("{}\n")
    os.utime(transcript, (old, old))
    run()
    entries = (root / "alerts.log").read_text().splitlines()
    assert sum("PM-IDLE" in line for line in entries) == 1
    assert sum("HUB-BREACH" in line for line in entries) == 1, "HUB-STREAK-ALERT-MISSING-OR-DUPLICATE"


def test_busy_probe_keeps_hung_notification_streak(watch):
    root, state, _, _, run = watch
    run()
    assert "HUNG" in run()
    lock = (state / "probe-192.0.2.2.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert "PROBE-BUSY" in run()
    finally:
        lock.close()
    assert "HUNG" in run()
    assert len((root / "say.log").read_text().splitlines()) == 1


def test_real_vm_stays_up_and_runtime_bound(watch):
    root, _, _, _, run = watch
    address = os.environ.get("BD_FLEET_WATCH_REAL_HOST")
    if not address:
        pytest.skip("explicit remote real VM control")
    (root.parent / "hosts").write_text("real " + address + "\n")
    (root / "HOST-STATE.tsv").write_text("real\t" + address + "\tup\tnow\n")
    (root / "ROLE-OCCUPANCY.tsv").write_text("")
    native_ssh = root.parent / "real-ssh"
    known = os.environ["BD_FLEET_WATCH_REAL_KNOWN_HOSTS"]
    native_ssh.write_text("#!/bin/sh\nexec /usr/bin/ssh -o ControlPath=none -o IdentitiesOnly=no -o StrictHostKeyChecking=yes -o UserKnownHostsFile=" + known + " \"$@\"\n")
    native_ssh.chmod(0o755)
    runtimes = []
    for _ in range(20):
        text = run({"BD_FLEET_WATCH_SSH": str(native_ssh), "BD_FLEET_WATCH_TCP": ""})
        assert "STATE=UP" in text and "HUNG" not in text
        runtimes.append(float(next(line[6:] for line in text.splitlines() if line.startswith("RUN_S="))))
    p95 = sorted(runtimes)[18]
    assert p95 < 240
    out = os.environ.get("BD_FLEET_WATCH_MEASURE_LOG")
    if out:
        Path(out).write_text(json.dumps({"real_host": address, "cycles": 20, "p95_s": p95,
                                       "runtimes_s": runtimes, "false_hung": 0}) + "\n")
