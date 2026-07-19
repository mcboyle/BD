"""v3.66.680 (B1/P10): BD_DISABLE_VPN_RUNTIME read at call-time, not import-time.

Deferred from Bucket 1: the disable gate was frozen at module import, so a
var set after import (e.g. a live env/.env write) could not take effect.
"""
import bulk_downloader.vpn_runtime as vr


def test_is_disabled_reads_env_at_call_time(monkeypatch):
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    assert vr._is_disabled() is False
    monkeypatch.setenv("BD_DISABLE_VPN_RUNTIME", "1")
    assert vr._is_disabled() is True
    monkeypatch.setenv("BD_DISABLE_VPN_RUNTIME", "0")
    assert vr._is_disabled() is False


def test_init_honors_disable_set_after_import(monkeypatch):
    monkeypatch.setenv("BD_DISABLE_VPN_RUNTIME", "1")
    monkeypatch.setattr(vr, "_initialized", False, raising=False)
    res = vr.init({}, start_monitors=False)
    assert res.get("skipped") is True
    assert res.get("reason") == "BD_DISABLE_VPN_RUNTIME"
