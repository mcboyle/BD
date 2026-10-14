"""APP-FEATURE F015: a Settings switch toggled back to its loaded state is clean."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


def test_appfeat_f015_settings_switch_roundtrip_clean():
    spec = "src/routes/Settings.dirtyRoundtrip.test.tsx"
    receipt = run_vitest(spec, expected_tests=4)
    assert receipt["tests_passed"] == 4, receipt
