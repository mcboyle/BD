"""Regression test for BH2-25 / BH2-vroute-006: bd-offer-sweep.sh bash process regex.

Defect: pgrep and ps checks in bd-offer-sweep.sh used regex ^(/bin/)?bash, which fails to
match /usr/bin/bash on standard Linux systems where bash is /usr/bin/bash, causing
concurrency limits and in-flight deduplication to fail open under load.
Fix: Update the regex to ^(/usr)?(/bin/)?bash in lines 357 and 363.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE_SCRIPT = os.environ.get("BD_BH2_25_CANDIDATE", "")

pytestmark = pytest.mark.skipif(
    not CANDIDATE_SCRIPT,
    reason="candidate opt-in required (BD_BH2_25_CANDIDATE)",
)


def test_candidate_regex_matches_usr_bin_bash():
    """Candidate script must use regex that matches /usr/bin/bash."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )
    with open(CANDIDATE_SCRIPT, encoding="utf-8") as f:
        content = f.read()

    expected_pattern = r"^(/usr)?(/bin/)?bash"
    assert expected_pattern in content, (
        f"candidate script must use {expected_pattern} for bash matching"
    )

    # Verify both pgrep and ps lines use the expanded regex
    pgrep_match = re.search(r'pgrep -fc "\^(\([^)]+\)\?)+bash', content)
    assert pgrep_match is not None, "pgrep regex not found in candidate script"
    assert "/usr" in pgrep_match.group(0), "pgrep regex does not match /usr prefix"

    ps_match = re.search(r'grep -qE "\^(\([^)]+\)\?)+bash', content)
    assert ps_match is not None, "ps grep regex not found in candidate script"
    assert "/usr" in ps_match.group(0), "ps grep regex does not match /usr prefix"


def test_regex_matching_against_process_lines():
    """Extracted regex from script must match bash, /bin/bash, and /usr/bin/bash."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )
    with open(CANDIDATE_SCRIPT, encoding="utf-8") as f:
        content = f.read()

    match = re.search(r'grep -qE "(\^[^"]*bd-review-prep\\\.sh [^"]*)"', content)
    assert match is not None, "could not find prep regex in script"
    pattern = match.group(1).replace(r"\$", "$").replace("$id", "123")

    test_lines = [
        "bash /home/mboyle/bd-persist/harness/bd-review-prep.sh 123",
        "/bin/bash /home/mboyle/bd-persist/harness/bd-review-prep.sh 123",
        "/usr/bin/bash /home/mboyle/bd-persist/harness/bd-review-prep.sh 123",
    ]

    for line in test_lines:
        res = subprocess.run(
            ["grep", "-qE", pattern],
            input=line,
            text=True,
            capture_output=True,
            check=False,
        )
        assert res.returncode == 0, f"pattern '{pattern}' failed to match line: {line}"


def test_behavioral_detects_usr_bin_bash_inflight(tmp_path: Path):
    """Candidate script must detect in-flight /usr/bin/bash prep processes."""
    assert os.path.isfile(CANDIDATE_SCRIPT), (
        f"candidate script {CANDIDATE_SCRIPT} missing"
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)

    # Mock ps to return /usr/bin/bash running bd-review-prep.sh for row777
    mock_ps = bin_dir / "ps"
    mock_ps.write_text(
        "#!/usr/bin/env bash\n"
        'echo "/usr/bin/bash /home/mboyle/bd-persist/harness/bd-review-prep.sh 777"\n',
        encoding="utf-8",
    )
    mock_ps.chmod(0o755)

    # Mock pgrep to return 1
    mock_pgrep = bin_dir / "pgrep"
    mock_pgrep.write_text("#!/usr/bin/env bash\necho 1\n", encoding="utf-8")
    mock_pgrep.chmod(0o755)

    # Mock tmux
    mock_tmux = bin_dir / "tmux"
    mock_tmux.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    mock_tmux.chmod(0o755)

    # Mock bd-say
    mock_say = bin_dir / "bd-say.sh"
    mock_say.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    mock_say.chmod(0o755)

    persist_dir = tmp_path / "persist"
    persist_dir.mkdir(parents=True)
    log_file = tmp_path / "offer-sweep.log"

    # Queue file with row777
    queue_file = tmp_path / "queue.tsv"
    cut_dir = tmp_path / "row777"
    cut_dir.mkdir(parents=True)
    queue_file.write_text(f"777\t{cut_dir}\tbrief777\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["BD_PERSIST_ROOT"] = str(persist_dir)
    env["BD_OFFER_SWEEP_LOG"] = str(log_file)
    env["BD_OFFER_SWEEP_SAY"] = str(mock_say)
    env["BD_REVIEW_PREP_QUEUE"] = str(queue_file)

    with open(CANDIDATE_SCRIPT, encoding="utf-8") as f:
        script_text = f.read()
    match = re.search(r'grep -qE "([^"]+bd-review-prep\\\.sh[^"]*)"', script_text)
    assert match is not None, "regex for bd-review-prep.sh not found in script"
    extracted_pattern = match.group(1).replace(r"\$", "$")

    cmd = (
        f'say(){{ echo "$@" >> "{log_file}"; }}; '
        f"MAXP=5; id=777; "
        f'if ps -eo args | grep -qE "{extracted_pattern}"; then '
        f'say "PREP-INFLIGHT $id"; fi'
    )
    res = subprocess.run(
        ["bash", "-c", cmd],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"command failed: {res.stderr}"

    assert log_file.is_file(), "log file not created"
    content = log_file.read_text(encoding="utf-8")
    assert "PREP-INFLIGHT 777" in content, (
        f"in-flight /usr/bin/bash process was not detected with pattern '{extracted_pattern}':\n{content}"
    )
