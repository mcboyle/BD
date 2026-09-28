import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_30_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def route(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_file() and os.access(candidate, os.X_OK), (
        "candidate must be executable"
    )
    bins = tmp_path / "bin"
    bins.mkdir()
    tmux = bins / "tmux"
    tmux.write_text("#!/bin/bash\nexit 1\n")
    tmux.chmod(0o755)
    backend = tmp_path / "inbox-backend.sh"
    backend.write_text(
        '#!/bin/bash\nprintf "%s\\t%s\\n" "$1" "$2" >> "$CAPTURE"\nif [ "$BACKEND_RC" != 0 ]; then echo INBOX_REFUSED; exit "$BACKEND_RC"; fi\necho INBOX_FILED\n'
    )
    capture = tmp_path / "calls.tsv"

    def send(flag, text="WAKE receipt", rc=0):
        env = {
            "PATH": str(bins) + ":/usr/bin:/bin",
            "HOME": str(tmp_path),
            "BD_SAY_SH": str(backend),
            "BD_SAY_FILE_FIRST": "1",
            "CAPTURE": str(capture),
            "BACKEND_RC": str(rc),
        }
        result = subprocess.run(
            ["bash", str(candidate), flag, text],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return result, capture.read_text() if capture.exists() else ""

    return send


@pytest.mark.parametrize(("flag", "target"), [("-e", "bd-fable-b"), ("-p", "bd-fable")])
def test_inbox_backend_can_accept_without_tmux(route, flag, target):
    result, calls = route(flag)
    assert result.returncode == 0, "BH2_30_INBOX_ROUTE_BLOCKED: " + result.stderr
    assert result.stdout.strip() == "INBOX_FILED"
    assert calls == target + "\tWAKE receipt\n"


def test_routine_control_reaches_backend(route):
    result, calls = route("-r")
    assert result.returncode == 0
    assert calls == "bd-status\tWAKE receipt\n"


@pytest.mark.parametrize("flag", ["-e", "-p"])
def test_backend_refusal_is_preserved(route, flag):
    result, calls = route(flag, rc=4)
    assert calls, "backend must decide whether delivery is available"
    assert result.returncode == 4
    assert result.stdout.strip() == "INBOX_REFUSED"


@pytest.mark.parametrize("flag", ["-e", "-p"])
def test_oversized_text_refuses_before_backend(route, flag):
    result, calls = route(flag, "x" * 31)
    assert result.returncode == 2
    assert "31 chars > 30" in result.stderr
    assert calls == ""
