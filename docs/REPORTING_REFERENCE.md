# Reporting reference

All report generators are read-only and write under `reports/`.

| tool | output |
|------|--------|
| `tools/template_health_report.py` | `reports/template_health.{json,md}` |
| `tools/capture_quality_report.py` | `reports/capture_analytics.md` |
| `tools/release_diff_summary.py` | `reports/release_history.md` |
| `tools/dependency_inventory.py` | `reports/codebase_inventory.md` |
| `tools/test_runtime_report.py` | `reports/test_health.md` |
| `tools/technical_debt_report.py` | `reports/technical_debt.md` |
| `tools/compat_shim_audit.py` | `reports/compat_shim_audit.md` |
| `tools/offline_pack_report.py` | `reports/offline_pack_report.md` |
| `tools/environment_report.py` | `reports/environment.md` |
| `tools/kb_audit.py` | `reports/kb_health.md` |

Dashboard data layer: `/api/data/{template_health,capture_analytics,queue_analytics,release_analytics,kb_analytics}` (`bulk_downloader/app_data_layer.py`).
Report Center page: `/cockpit/reports` (`app_report_center.py`).
