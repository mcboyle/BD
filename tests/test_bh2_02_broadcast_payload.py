import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_02_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def sender(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), (
        "candidate must be executable"
    )
    source = candidate.read_text()
    boundary = "# ---- H755: DEFERRED BATCH-RELAY ENQUEUE"
    assert source.count(boundary) == 1, "transport boundary must be unique"
    # Execute the real guards, spooling and EXIT ledger; replace only delivery.
    script = tmp_path / "say-before-transport.sh"
    script.write_text(source.split(boundary)[0] + "\nRC=0; exit 0\n")
    bins = tmp_path / "bin"
    bins.mkdir()
    tmux = bins / "tmux"
    tmux.write_text(
        '#!/bin/bash\ncase "$1" in ls) echo "$FIXTURE_TARGET";; has-session) exit 0;; *) exit 91;; esac\n'
    )
    tmux.chmod(0o755)
    noop = bins / "noop"
    noop.write_text("#!/bin/bash\nexit 0\n")
    noop.chmod(0o755)
    log = tmp_path / "say.log"
    env = {
        "PATH": str(bins) + ":/usr/bin:/bin",
        "HOME": str(tmp_path),
        "BD_SEAT": "fixture-sender",
        "BD_SAY_LOG": str(log),
        "BD_SAY_INBOX_ROOT": str(tmp_path / "inbox"),
        "BD_SAY_RETIRED_ROOT": str(tmp_path / "retired"),
        "BD_SAY_GENCHECK": str(noop),
        "BD_SAY_GENWARN_LOG": str(tmp_path / "gen.log"),
        "BD_SAY_BATCH": "0",
        "BD_SAY_NO_RELAY": "1",
        "BD_SAY_REROUTE": "0",
    }

    def send(target, text, file_first=False):
        run_env = {
            **env,
            "FIXTURE_TARGET": target,
            "BD_SAY_FILE_FIRST": "1" if file_first else "0",
        }
        result = subprocess.run(
            ["bash", str(script), target, text],
            env=run_env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result

    return send, log, tmp_path / "inbox"


@pytest.mark.parametrize("file_first", [False, True])
def test_spooled_fourth_target_is_refused(sender, file_first):
    send, log, inbox = sender
    payload = "WAKE " + "x" * 150
    for target in ("fixture-one", "fixture-two", "fixture-three"):
        result = send(target, payload, file_first)
        assert result.returncode == 0, result.stdout + result.stderr
        files = list((inbox / target).glob("*.md"))
        assert len(files) == 1
        assert files[0].read_text().strip() == "[from fixture-sender] " + payload
    assert len(log.read_text().splitlines()) == 3
    result = send("fixture-four", payload, file_first)
    assert result.returncode == 8, (
        "BH2_02_SPOOLED_BROADCAST_ESCAPED: " + result.stdout + result.stderr
    )
    assert "broadcast limiter" in result.stdout
    assert not (inbox / "fixture-four").exists(), "refusal must precede spooling"


def test_short_unspooled_positive_control(sender):
    send, log, _ = sender
    for target in ("fixture-one", "fixture-two", "fixture-three"):
        assert send(target, "WAKE short").returncode == 0
    assert len(log.read_text().splitlines()) == 3
    result = send("fixture-four", "WAKE short")
    assert result.returncode == 8
    assert "broadcast limiter" in result.stdout


@pytest.mark.parametrize("file_first", [False, True])
def test_distinct_payload_and_repeated_target_stay_allowed(sender, file_first):
    send, _, _ = sender
    payload = "WAKE " + "x" * 150
    for target in ("fixture-one", "fixture-one", "fixture-two", "fixture-three"):
        result = send(target, payload, file_first)
        assert result.returncode == 0, result.stdout + result.stderr
    result = send("fixture-four", payload + " different", file_first)
    assert result.returncode == 0, result.stdout + result.stderr
