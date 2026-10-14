"""dss-spa-secrets F9-F13: the secret/token UI reported what it had not measured.

The behaviour runs in the two tracked vitest specs below: Settings (a stored
secret typed into and emptied again) and Secrets + ApiTokensPanel (unread list,
unwritten clipboard, stale status, a delete key the select no longer shows).
"""
import pytest

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


@pytest.mark.parametrize(
    "spec,count",
    [("src/routes/Settings.secretBlankEdit.test.tsx", 2), ("src/routes/Secrets.dssSpaSecrets.test.tsx", 12)],
)
def test_dss_spa_secrets_vitest(spec, count):
    receipt = run_vitest(spec, expected_tests=count)
    expected = {
        "spec": spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": count,
        "tests_collected": count,
    }
    assert receipt == expected, (
        f"dss-spa-secrets vitest receipt mismatch: expected={expected!r}, observed={receipt!r}"
    )
