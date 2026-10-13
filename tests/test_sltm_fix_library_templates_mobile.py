"""sltm-fix: bughunt spa-library-templates-mobile F1-F7 (finder bd-worker-C2-C).

Runs the three vitest specs that pin the fixes: Library paging / full-library
search / load error / long-title wrap (F1-F3), Template Manager load error (F4)
and the 390 px row-wrap classes (F5-F7). The 390 px overflow itself is measured
in Chromium; jsdom has no layout.
"""

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"


def test_sltm_library_older_items_error_and_wrap():
    receipt = run_vitest("src/routes/Library.olderItems.test.tsx", expected_tests=11)
    assert receipt["tests_passed"] == 11, receipt


def test_sltm_template_manager_load_error():
    receipt = run_vitest(
        "src/routes/TemplateManager.loadError.test.tsx", expected_tests=2
    )
    assert receipt["tests_passed"] == 2, receipt


def test_sltm_mobile_rows_wrap():
    receipt = run_vitest("src/components/ui/mobileRows.sltm.test.tsx", expected_tests=3)
    assert receipt["tests_passed"] == 3, receipt
