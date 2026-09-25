import pytest

from flask import Flask

from bulk_downloader import widgets_config
from bulk_downloader.app_widgets_api import widgets_bp


def test_widget_save_failure_keeps_memory_and_disk_in_sync(tmp_path, monkeypatch):
    path = tmp_path / "widgets.json"
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(path))
    widgets_config._reset_for_tests()
    try:
        baseline = [{"id": "done_today", "size": "sm"}]
        widgets_config.set_global(baseline)
        assert widgets_config.get_global() == baseline
        original_bytes = path.read_bytes()

        def reject_replace(*args):
            raise OSError("storage is unavailable")

        monkeypatch.setattr(widgets_config.os, "replace", reject_replace)
        requested = [{"id": "queue_depth", "size": "md"}]
        with pytest.raises(OSError, match="storage is unavailable"):
            widgets_config.set_global(requested)
        assert widgets_config.get_global() == baseline
        assert path.read_bytes() == original_bytes
    finally:
        widgets_config._reset_for_tests()


@pytest.mark.parametrize("operation", ["set_site", "reset_site", "reset_global"])
def test_other_widget_writes_refuse_failed_save(tmp_path, monkeypatch, operation):
    path = tmp_path / "widgets.json"
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(path))
    widgets_config._reset_for_tests()
    try:
        widgets_config.set_global([{"id": "done_today", "size": "sm"}])
        widgets_config.set_for_site("example", [{"id": "queue_depth", "size": "md"}])
        before = widgets_config.get_all()
        original_bytes = path.read_bytes()

        def reject_replace(*args):
            raise OSError("storage is unavailable")

        monkeypatch.setattr(widgets_config.os, "replace", reject_replace)
        with pytest.raises(OSError, match="storage is unavailable"):
            if operation == "set_site":
                widgets_config.set_for_site("example", [{"id": "workers", "size": "sm"}])
            elif operation == "reset_site":
                widgets_config.reset_for_site("example")
            else:
                widgets_config.reset_global()
        assert widgets_config.get_all() == before
        assert path.read_bytes() == original_bytes
    finally:
        widgets_config._reset_for_tests()


@pytest.mark.parametrize("method,scope", [("put", "_global"), ("put", "example"),
                                          ("delete", "_global"), ("delete", "example")])
def test_widget_routes_report_failed_save(tmp_path, monkeypatch, method, scope):
    path = tmp_path / "widgets.json"
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(path))
    widgets_config._reset_for_tests()
    try:
        widgets_config.set_for_site("example", [{"id": "queue_depth", "size": "md"}])
        before = widgets_config.get_all()
        app = Flask(__name__)
        app.register_blueprint(widgets_bp)
        client = app.test_client()

        def reject_replace(*args):
            raise OSError("storage is unavailable")

        monkeypatch.setattr(widgets_config.os, "replace", reject_replace)
        body = {"widgets": [{"id": "workers", "size": "sm"}]}
        response = getattr(client, method)(f"/api/widgets/{scope}", json=body)
        assert response.status_code == 500
        assert response.get_json() == {
            "ok": False, "error": "could not save widget settings: storage is unavailable"}
        assert widgets_config.get_all() == before
    finally:
        widgets_config._reset_for_tests()
