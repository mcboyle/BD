"""bd-fleet-deploy.sh verdicts: the two defects that made a healthy fleet red.

Both were found on 2026-08-31 during the v3.66.1360 and v3.66.1361 passes, when
the wrapper reported `bad=4 of 5` over a fleet whose five hosts were all serving
the intended release. These are text-level contract tests over the live script:
the lane itself cannot be exercised without a fleet, so the assertions name the
exact mechanisms rather than a simulated run.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

HOME = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home())))
SCRIPT = HOME / "bd-fleet-deploy.sh"


def read() -> str:
    assert SCRIPT.is_file(), f"precondition: {SCRIPT} must exist"
    text = SCRIPT.read_text(encoding="utf-8")
    assert "scripts/deploy.sh" in text, "precondition: this is the deploy wrapper"
    return text


def test_the_script_is_syntactically_valid():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_every_deploy_invocation_forwards_the_exact_commit():
    """bd and bd1 clone from a local bare mirror.

    scripts/deploy.sh refuses a non-official origin outright unless it is told
    which object to land, so without --expect-commit those hosts failed rc=2 on
    every pass while being healthy at the previous release.
    """
    text = read()
    calls = re.findall(r"bash scripts/deploy\.sh[^\"']*", text)
    assert calls, "precondition: the wrapper still invokes deploy.sh"
    bare = [c for c in calls if "--expect-commit" not in c]
    assert not bare, f"deploy.sh invoked without --expect-commit: {bare}"


def test_the_expected_commit_is_resolved_and_refused_when_unmeasurable():
    text = read()
    assert "EXPECT_COMMIT=$(git -C /home/mboyle/BulkDownloader rev-parse origin/main" in text
    assert re.search(r'\[\[ "\$EXPECT_COMMIT" =~ \^\[0-9a-f\]\{40\}\$ \]\]', text), (
        "an unresolvable commit must refuse, not deploy an unmeasured object")


def test_a_locked_vault_at_the_expected_version_is_not_an_incident():
    """Row 408's distinction, which deploy.sh has and the wrapper did not."""
    text = read()
    assert "credential_vault_locked|locked|$EXPECT" in text, (
        "the wrapper must accept the exact structured locked-vault state")
    assert 'root=$(timeout' in text and '/api/health' in text, (
        "GET / must be read independently before accepting a 503")


def test_the_degraded_acceptance_is_exact_and_not_any_503():
    """A blank or unrelated 503 must still fail.

    The acceptance compares the full triple -- degraded reason, credentials
    state, and version -- so a 503 for any other reason, or at the wrong
    version, cannot be laundered into OK.
    """
    text = read()
    block = text.split("SERVING-DEGRADED IS NOT A FAILED DEPLOY", 1)
    assert len(block) == 2, "precondition: the degraded branch is present"
    body = block[1].split("SELF-HEAL", 1)[0]
    assert '[ "$health" = 503 ]' in body
    assert '[ "$post" = "$EXPECT" ]' in body, "version must be checked too"
    assert '[ "$root" = 200 ]' in body
    assert re.search(r'health=\$\{?health', body) or "UNKNOWN, not OK" in body, (
        "an unlock that does not restore health must report UNKNOWN")


def test_an_unlock_failure_does_not_count_as_ok():
    text = read()
    body = text.split("SERVING-DEGRADED IS NOT A FAILED DEPLOY", 1)[1].split("SELF-HEAL", 1)[0]
    oks = re.findall(r"(?:ok=\$\(\(ok\+1\)\); continue|_verdict ok; return)", body)
    assert len(oks) == 1, f"exactly one success path in the degraded branch, found {len(oks)}"
    assert "vault unlock FAILED" in body
    assert "UNKNOWN, not OK" in body
