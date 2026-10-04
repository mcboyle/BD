"""O1826 BRIEF-28: Settings nav/changed-sections come from settingsSchema SETTINGS_SECTIONS."""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


def test_o1826_c28_settings_sections_single_source():
    spec = "src/routes/Settings.sections.test.tsx"
    receipt = run_vitest(spec, expected_tests=4)
    assert receipt["tests_passed"] == 4, receipt
