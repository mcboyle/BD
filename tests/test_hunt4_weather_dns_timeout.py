"""A DNS health probe must restore the process socket timeout."""

import socket

from bulk_downloader import site_weather

BD_GATE_SCOPE = "module"


def test_probe_dns_restores_default_timeout_on_success_and_failure(monkeypatch):
    observed = []
    recorded = []
    original = socket.getdefaulttimeout()
    monkeypatch.setattr(site_weather, "_record", lambda *a, **kw: recorded.append((a, kw)))

    def resolve(host):
        observed.append((host, socket.getdefaulttimeout()))
        if host == "bad.example":
            raise OSError("lookup failed")
        return "203.0.113.8"

    monkeypatch.setattr(site_weather.socket, "gethostbyname", resolve)
    try:
        socket.setdefaulttimeout(None)
        good = site_weather.probe_dns("test-site", "good.example", timeout=0.25)
        assert good["ok"] is True
        assert good["ip"] == "203.0.113.8"
        assert socket.getdefaulttimeout() is None
        bad = site_weather.probe_dns("test-site", "bad.example", timeout=0.5)
        assert bad["ok"] is False
        assert socket.getdefaulttimeout() is None
        assert observed == [("good.example", 0.25), ("bad.example", 0.5)]
        assert [entry[0][2] for entry in recorded] == [True, False]

        # lens (B1): the ORIGINAL value is restored, not a hardcoded None --
        # a process that set its own default keeps it.
        socket.setdefaulttimeout(7.0)
        site_weather.probe_dns("test-site", "good.example", timeout=0.25)
        assert socket.getdefaulttimeout() == 7.0
    finally:
        socket.setdefaulttimeout(original)
